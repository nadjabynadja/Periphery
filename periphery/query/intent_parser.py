"""Component 1 — Intent Parser.

Uses Claude API to decompose natural language queries into structured
analytical intents. Caches parsed intents using semantic similarity
to detect near-duplicate queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import anthropic
import numpy as np

from periphery.query.models import (
    GeographicScope,
    ParsedIntent,
    TemporalScope,
)

logger = logging.getLogger(__name__)

INTENT_PARSER_SYSTEM_PROMPT = """You are the query parser for an OSINT intelligence system called Periphery. The system maintains a living ontology of emergent clusters, entities, relationships, trajectories, and anomalies detected from continuous open-source data ingestion.

Current ontology summary:
{snapshot_summary}

The analyst has submitted this query:
"{query_text}"

Decompose this query into structured analytical intents. Return ONLY valid JSON with no preamble:

{{
    "query_type": str,
    "entities_referenced": [str],
    "entity_types_requested": [str],
    "relationships_requested": [str],
    "geographic_scope": {{
        "regions": [str],
        "coordinates": null,
        "scope_type": str
    }},
    "temporal_scope": {{
        "start": str or null,
        "end": str or null,
        "temporal_focus": str
    }},
    "confidence_threshold": float,
    "analytical_focus": str,
    "implied_subqueries": [str],
    "clusters_likely_relevant": [str]
}}

query_type must be one of: entity_lookup, relationship_query, cluster_exploration, trajectory_query, geographic_query, temporal_query, anomaly_query, situational_awareness, comparative, freeform

analytical_focus must be one of: connections, timeline, actors, geography, emerging_patterns, anomalies

Handle ambiguous queries gracefully. Broad queries like "what's going on in the Middle East" should be situational_awareness with geographic scope, not forced into narrow entity_lookup. Generate implied_subqueries for broad queries — decompose them into multiple sub-operations."""

# Similarity threshold for intent cache hits
CACHE_SIMILARITY_THRESHOLD = 0.92


class IntentCache:
    """Semantic cache for parsed intents using embedding similarity."""

    def __init__(self, max_size: int = 500) -> None:
        self._entries: list[dict[str, Any]] = []
        self._max_size = max_size

    def lookup(self, query_embedding: np.ndarray) -> ParsedIntent | None:
        if not self._entries:
            return None

        best_score = 0.0
        best_intent = None

        for entry in self._entries:
            cached_emb = entry["embedding"]
            score = float(np.dot(query_embedding, cached_emb))
            if score > best_score:
                best_score = score
                best_intent = entry["intent"]

        if best_score >= CACHE_SIMILARITY_THRESHOLD and best_intent is not None:
            logger.debug("intent_cache_hit score=%.3f", best_score)
            return best_intent
        return None

    def store(self, query_embedding: np.ndarray, intent: ParsedIntent) -> None:
        self._entries.append({
            "embedding": query_embedding.copy(),
            "intent": intent,
            "timestamp": time.time(),
        })
        if len(self._entries) > self._max_size:
            self._entries = self._entries[-self._max_size:]


class IntentParser:
    """Parses natural language queries into structured analytical intents."""

    def __init__(
        self,
        anthropic_api_key: str = "",
        model: str = "claude-sonnet-4-20250514",
        cache_max_size: int = 500,
    ) -> None:
        self._client = (
            anthropic.AsyncAnthropic(api_key=anthropic_api_key)
            if anthropic_api_key
            else None
        )
        self._model = model
        self._cache = IntentCache(max_size=cache_max_size)

    def _build_snapshot_summary(self, snapshot: Any | None) -> str:
        if snapshot is None:
            return "No ontology snapshot available yet. The system is still ingesting initial data."

        parts = [
            f"Corpus: {snapshot.corpus_stats.total_documents} documents, "
            f"{snapshot.corpus_stats.total_entities} entities, "
            f"{snapshot.corpus_stats.total_relationships} relationships",
        ]

        if snapshot.clusters:
            parts.append(f"\nClusters ({len(snapshot.clusters)}):")
            for c in snapshot.clusters[:20]:
                entities_str = ", ".join(c.key_entities[:5]) if c.key_entities else "no key entities"
                parts.append(
                    f"  - [{c.cluster_id}] {c.label or 'unlabeled'} "
                    f"(size={c.size}, confidence={c.confidence:.2f}, "
                    f"status={c.status}, entities: {entities_str})"
                )

        if snapshot.trajectories:
            parts.append(f"\nTrajectories ({len(snapshot.trajectories)}):")
            for t in snapshot.trajectories[:10]:
                parts.append(
                    f"  - {t.cluster_id}: {t.pattern} "
                    f"(velocity={t.velocity:.3f}, confidence={t.confidence:.2f})"
                )

        if snapshot.anomalies:
            unresolved = [a for a in snapshot.anomalies if not a.resolved]
            if unresolved:
                parts.append(f"\nUnresolved anomalies ({len(unresolved)}):")
                for a in unresolved[:10]:
                    parts.append(
                        f"  - {a.anomaly_type} (score={a.anomaly_score:.2f}, "
                        f"spaces={a.outlier_spaces})"
                    )

        if snapshot.emerging_structures:
            parts.append(f"\nEmerging structures ({len(snapshot.emerging_structures)}):")
            for e in snapshot.emerging_structures[:5]:
                parts.append(
                    f"  - {e.region_id} in {e.space} "
                    f"(confidence={e.formation_confidence:.2f})"
                )

        return "\n".join(parts)

    async def parse(
        self,
        query_text: str,
        snapshot: Any | None = None,
        query_embedding: np.ndarray | None = None,
        session_context: str = "",
    ) -> tuple[ParsedIntent, int]:
        """Parse a query into structured intent.

        Returns (parsed_intent, elapsed_ms).
        """
        start = time.monotonic()

        # Check cache
        if query_embedding is not None:
            cached = self._cache.lookup(query_embedding)
            if cached is not None:
                elapsed = int((time.monotonic() - start) * 1000)
                return cached, elapsed

        # Build prompt
        snapshot_summary = self._build_snapshot_summary(snapshot)
        prompt = INTENT_PARSER_SYSTEM_PROMPT.format(
            snapshot_summary=snapshot_summary,
            query_text=query_text,
        )

        if session_context:
            prompt += f"\n\nSession context (previous queries):\n{session_context}"

        intent = await self._call_llm(prompt, query_text)

        # Cache the result
        if query_embedding is not None:
            self._cache.store(query_embedding, intent)

        elapsed = int((time.monotonic() - start) * 1000)
        return intent, elapsed

    async def _call_llm(self, system_prompt: str, query_text: str) -> ParsedIntent:
        if self._client is None:
            return self._hardcoded_fallback(query_text)

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": query_text}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                lines = raw.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                raw = "\n".join(lines)

            data = json.loads(raw)
            return self._parse_response(data)

        except Exception as e:
            logger.error("intent_parser_llm_failed: %s", e)
            return self._hardcoded_fallback(query_text)

    def _parse_response(self, data: dict[str, Any]) -> ParsedIntent:
        geo_raw = data.get("geographic_scope", {}) or {}
        temporal_raw = data.get("temporal_scope", {}) or {}

        return ParsedIntent(
            query_type=data.get("query_type", "freeform"),
            entities_referenced=data.get("entities_referenced", []),
            entity_types_requested=data.get("entity_types_requested", []),
            relationships_requested=data.get("relationships_requested", []),
            geographic_scope=GeographicScope(
                regions=geo_raw.get("regions", []),
                coordinates=geo_raw.get("coordinates"),
                scope_type=geo_raw.get("scope_type", "global"),
            ),
            temporal_scope=TemporalScope(
                start=temporal_raw.get("start"),
                end=temporal_raw.get("end"),
                temporal_focus=temporal_raw.get("temporal_focus", "current"),
            ),
            confidence_threshold=float(data.get("confidence_threshold", 0.0)),
            analytical_focus=data.get("analytical_focus", "connections"),
            implied_subqueries=data.get("implied_subqueries", []),
            clusters_likely_relevant=data.get("clusters_likely_relevant", []),
        )

    def _hardcoded_fallback(self, query_text: str) -> ParsedIntent:
        """Rule-based fallback when Claude API is unavailable."""
        q = query_text.lower()

        query_type = "freeform"
        analytical_focus = "connections"
        entities: list[str] = []
        regions: list[str] = []

        # Detect query type heuristically
        if any(w in q for w in ["who is", "what is", "tell me about"]):
            query_type = "entity_lookup"
            analytical_focus = "actors"
        elif any(w in q for w in ["connected", "relationship", "linked", "between"]):
            query_type = "relationship_query"
            analytical_focus = "connections"
        elif any(w in q for w in ["trend", "trajectory", "changing", "evolving"]):
            query_type = "trajectory_query"
            analytical_focus = "emerging_patterns"
        elif any(w in q for w in ["anomal", "unusual", "strange", "unexpected"]):
            query_type = "anomaly_query"
            analytical_focus = "anomalies"
        elif any(w in q for w in ["what's happening", "situation", "overview", "status"]):
            query_type = "situational_awareness"
            analytical_focus = "emerging_patterns"
        elif any(w in q for w in ["where", "geographic", "location", "region"]):
            query_type = "geographic_query"
            analytical_focus = "geography"
        elif any(w in q for w in ["when", "timeline", "history", "since"]):
            query_type = "temporal_query"
            analytical_focus = "timeline"

        # Extract geographic regions
        region_keywords = {
            "middle east": "Middle East", "red sea": "Red Sea",
            "europe": "Europe", "africa": "Africa", "asia": "Asia",
            "iran": "Iran", "china": "China", "russia": "Russia",
            "ukraine": "Ukraine", "taiwan": "Taiwan", "israel": "Israel",
            "gaza": "Gaza", "yemen": "Yemen", "syria": "Syria",
            "pacific": "Pacific", "arctic": "Arctic",
        }
        for keyword, region in region_keywords.items():
            if keyword in q:
                regions.append(region)

        scope_type = "region" if regions else "global"

        return ParsedIntent(
            query_type=query_type,
            entities_referenced=entities,
            entity_types_requested=[],
            relationships_requested=[],
            geographic_scope=GeographicScope(
                regions=regions,
                scope_type=scope_type,
            ),
            temporal_scope=TemporalScope(temporal_focus="current"),
            confidence_threshold=0.0,
            analytical_focus=analytical_focus,
            implied_subqueries=[],
            clusters_likely_relevant=[],
        )

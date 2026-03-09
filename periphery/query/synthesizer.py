"""Component 4 — Result Synthesizer.

Uses Claude API to synthesize raw retrieval results into coherent
analytical narratives with intelligence community language conventions.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic

from periphery.query.models import RetrievalResults, SynthesisOutput

logger = logging.getLogger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """You are the analytical synthesis engine for Periphery, an OSINT intelligence system. The analyst asked:

"{original_query}"

The system retrieved the following structured results from its living ontology:

{structured_results_json}

Synthesize these results into a clear, concise analytical response. Follow these rules:

1. Lead with the most important finding — what directly answers the analyst's question.
2. Present high-confidence findings as definitive statements. Present medium-confidence findings as assessed judgments with caveats. Present low-confidence findings as emerging indicators or tentative observations.
3. Use intelligence community language conventions: "we assess with high confidence," "reporting suggests," "there are indications that."
4. If trajectories are present, describe what's changing and in what direction.
5. If anomalies are present, highlight them as potential leads for further investigation.
6. If there are relational paths between entities the analyst asked about, describe the connection chain clearly.
7. Note any significant gaps — areas where the system has low coverage or unresolved ambiguities.
8. Do not fabricate information. If the results don't contain something, don't invent it.
9. Keep the response under 500 words unless the query is explicitly broad (situational awareness type).

At the end, suggest 2-3 follow-up queries the analyst might want to explore based on what the results reveal.

Return ONLY valid JSON with no preamble:
{{
    "summary": str,
    "analysis": str,
    "confidence_assessment": str,
    "key_findings": [str],
    "gaps_and_limitations": [str],
    "suggested_followups": [str],
    "sources_used": int,
    "highest_confidence_finding": str,
    "lowest_confidence_finding": str
}}"""

# Result count threshold below which synthesis is skipped
SIMPLE_QUERY_THRESHOLD = 3


class ResultSynthesizer:
    """Synthesizes structured retrieval results into analytical narratives."""

    def __init__(
        self,
        anthropic_api_key: str = "",
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = (
            anthropic.AsyncAnthropic(api_key=anthropic_api_key)
            if anthropic_api_key
            else None
        )
        self._model = model

    def _should_skip_synthesis(self, results: RetrievalResults, query_type: str) -> bool:
        """Skip synthesis for simple queries with few results."""
        total_items = (
            len(results.entities) + len(results.clusters) +
            len(results.relationships) + len(results.trajectories) +
            len(results.anomalies)
        )
        if total_items <= SIMPLE_QUERY_THRESHOLD and query_type == "entity_lookup":
            return True
        return False

    def _build_results_summary(self, results: RetrievalResults) -> str:
        """Build a concise JSON summary of results for the LLM."""
        summary: dict[str, Any] = {}

        if results.entities:
            summary["entities"] = [
                {
                    "name": e.name, "type": e.type,
                    "confidence": e.confidence,
                    "cluster_memberships": e.cluster_memberships[:5],
                    "relevance": e.relevance_score,
                }
                for e in results.entities[:20]
            ]

        if results.clusters:
            summary["clusters"] = [
                {
                    "id": c.cluster_id, "label": c.label,
                    "confidence": c.confidence, "size": c.size,
                    "key_entities": [e.get("name", "") for e in c.key_entities[:5]],
                    "relevance": c.relevance_score,
                }
                for c in results.clusters[:15]
            ]

        if results.relationships:
            summary["relationships"] = [
                {
                    "subject": r.subject.get("name", ""),
                    "predicate": r.predicate,
                    "object": r.object.get("name", ""),
                    "confidence": r.confidence,
                    "tier": r.extraction_tier,
                }
                for r in results.relationships[:15]
            ]

        if results.trajectories:
            summary["trajectories"] = [
                {
                    "cluster": t.cluster_label,
                    "pattern": t.pattern,
                    "velocity": t.velocity,
                    "confidence": t.confidence,
                    "description": t.description,
                }
                for t in results.trajectories[:10]
            ]

        if results.anomalies:
            summary["anomalies"] = [
                {
                    "type": a.type, "score": a.score,
                    "description": a.description,
                    "credibility": a.source_credibility,
                }
                for a in results.anomalies[:10]
            ]

        if results.relational_paths:
            summary["relational_paths"] = [
                {
                    "from": p.from_entity, "to": p.to_entity,
                    "path_type": p.path_type,
                    "confidence": p.path_confidence,
                    "hops": len(p.path),
                }
                for p in results.relational_paths[:5]
            ]

        if results.emerging_structures:
            summary["emerging_structures"] = [
                {
                    "region": e.region_id,
                    "confidence": e.formation_confidence,
                    "description": e.description,
                }
                for e in results.emerging_structures[:5]
            ]

        return json.dumps(summary, indent=2, default=str)

    async def synthesize(
        self,
        query_text: str,
        results: RetrievalResults,
        query_type: str = "freeform",
    ) -> tuple[SynthesisOutput, int]:
        """Synthesize results into an analytical narrative.

        Returns (synthesis, elapsed_ms).
        """
        start = time.monotonic()

        # Skip synthesis for simple queries
        if self._should_skip_synthesis(results, query_type):
            output = self._build_simple_output(results)
            elapsed = int((time.monotonic() - start) * 1000)
            return output, elapsed

        if self._client is None:
            output = self._build_fallback_output(results)
            elapsed = int((time.monotonic() - start) * 1000)
            return output, elapsed

        results_json = self._build_results_summary(results)
        prompt = SYNTHESIS_SYSTEM_PROMPT.format(
            original_query=query_text,
            structured_results_json=results_json,
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=prompt,
                messages=[{"role": "user", "content": "Synthesize the results."}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                lines = raw.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                raw = "\n".join(lines)

            data = json.loads(raw)
            output = SynthesisOutput(
                summary=data.get("summary", ""),
                analysis=data.get("analysis", ""),
                confidence_assessment=data.get("confidence_assessment", ""),
                key_findings=data.get("key_findings", []),
                gaps_and_limitations=data.get("gaps_and_limitations", []),
                suggested_followups=data.get("suggested_followups", []),
                sources_used=data.get("sources_used", 0),
                highest_confidence_finding=data.get("highest_confidence_finding", ""),
                lowest_confidence_finding=data.get("lowest_confidence_finding", ""),
            )
        except Exception as e:
            logger.error("synthesis_llm_failed: %s", e)
            output = self._build_fallback_output(results)

        elapsed = int((time.monotonic() - start) * 1000)
        return output, elapsed

    def _build_simple_output(self, results: RetrievalResults) -> SynthesisOutput:
        """Build output for simple queries without LLM synthesis."""
        findings = []
        for e in results.entities[:5]:
            findings.append(f"Entity: {e.name} ({e.type}, confidence: {e.confidence:.2f})")
        for c in results.clusters[:5]:
            findings.append(f"Cluster: {c.label} (size: {c.size}, confidence: {c.confidence:.2f})")

        summary = findings[0] if findings else "No significant results found."
        return SynthesisOutput(
            summary=summary,
            analysis="\n".join(findings) if findings else "Insufficient data for analysis.",
            confidence_assessment="Direct lookup — confidence based on entity resolution scores.",
            key_findings=findings,
            gaps_and_limitations=["Simple query — full synthesis skipped for performance."],
            suggested_followups=[],
            sources_used=len(results.entities) + len(results.clusters),
        )

    def _build_fallback_output(self, results: RetrievalResults) -> SynthesisOutput:
        """Build output without Claude API."""
        findings = []
        highest = ""
        lowest = ""
        max_conf = 0.0
        min_conf = 1.0

        for e in results.entities[:10]:
            finding = f"Entity '{e.name}' ({e.type}) — confidence {e.confidence:.2f}"
            findings.append(finding)
            if e.confidence > max_conf:
                max_conf = e.confidence
                highest = finding
            if e.confidence < min_conf:
                min_conf = e.confidence
                lowest = finding

        for c in results.clusters[:10]:
            finding = f"Cluster '{c.label}' (size {c.size}) — confidence {c.confidence:.2f}"
            findings.append(finding)
            if c.confidence > max_conf:
                max_conf = c.confidence
                highest = finding
            if c.confidence < min_conf:
                min_conf = c.confidence
                lowest = finding

        for t in results.trajectories[:5]:
            findings.append(f"Trajectory: {t.description}")

        for a in results.anomalies[:5]:
            findings.append(f"Anomaly: {a.description}")

        total = (
            len(results.entities) + len(results.clusters) +
            len(results.relationships)
        )

        return SynthesisOutput(
            summary=findings[0] if findings else "No results found matching the query.",
            analysis="\n".join(findings) if findings else "No data available.",
            confidence_assessment=(
                "Structured results returned without LLM synthesis. "
                "Review confidence scores on individual items."
            ),
            key_findings=findings[:5],
            gaps_and_limitations=[
                "Claude API unavailable — raw results returned without narrative synthesis."
            ],
            suggested_followups=[],
            sources_used=total,
            highest_confidence_finding=highest,
            lowest_confidence_finding=lowest,
        )

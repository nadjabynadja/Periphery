"""Stage 7 — LLM Verification Layer.

Post-extraction verification and enrichment using Claude Haiku and Exa.
Runs AFTER all extraction stages (entity extraction, relationship extraction,
temporal tagging, geospatial resolution, source credibility, entity resolution)
but BEFORE final document assembly.

Components:
  1. EntityVerifier   — filters junk, fixes misclassifications, deduplicates
  2. LocationVerifier — verifies geocoded coordinates are correct
  3. RelationshipVerifier — prunes noise, enriches predicates
  4. ExaEnricher      — adds real-time context for key entities via Exa search

All components share a single Anthropic client and BudgetTracker. Every
component degrades gracefully on API failure.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from periphery.enrichment.budget import BudgetTracker
from periphery.enrichment.models import (
    ExtractedEntity,
    ExtractedRelationship,
    GeospatialData,
    PipelineDocument,
)
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# ─── Haiku pricing (as of late 2024) ─────────────────────────────────────────
# claude-haiku-3-5: $0.80/M input, $4.00/M output
_HAIKU_INPUT_COST_PER_TOKEN = 0.80 / 1_000_000
_HAIKU_OUTPUT_COST_PER_TOKEN = 4.00 / 1_000_000


# ─── Prompt templates ────────────────────────────────────────────────────────

_ENTITY_VERIFY_PROMPT = """\
You are an expert OSINT entity classifier. Review the list of extracted entities and \
verify each one.

Document context:
Title: {title}
Text snippet: {snippet}

For each entity, you must decide:
1. is_valid: Is this a real, meaningful named entity? Remove:
   - Days of the week (Monday, Tuesday, etc.)
   - Months standalone (January, February)
   - Single letters or single digits
   - Common words incorrectly tagged (e.g., "The", "New", "American")
   - Generic numbers without context
2. entity_type: Correct the type if wrong. Common errors:
   - "AI" → label as PRODUCT, not GPE
   - "Congress" → ORG, not PERSON
   - "the Kremlin" → can be both ORG and FAC; pick most contextually appropriate
   - Country demonyms like "American", "Russian" → NORP, not GPE
3. canonical_name: The canonical form (e.g., "U.S." → "United States", \
"Putin" → "Vladimir Putin" if context makes clear)
4. confidence: 0.0-1.0 how clearly this is a real, meaningful entity in context
5. merge_with: If this entity is a variant/alias of another entity in the list, \
set this to the canonical_name of the entity it should merge into

Few-shot examples:
Input: [{{"text": "Monday", "entity_type": "DATE"}}, \
{{"text": "US", "entity_type": "GPE"}}, \
{{"text": "U.S.", "entity_type": "GPE"}}, \
{{"text": "AI", "entity_type": "GPE"}}, \
{{"text": "Vladimir Putin", "entity_type": "PERSON"}}]

Output: [
  {{"text": "Monday", "entity_type": "DATE", "canonical_name": "Monday", \
"confidence": 0.1, "is_valid": false, "merge_with": null}},
  {{"text": "US", "entity_type": "GPE", "canonical_name": "United States", \
"confidence": 0.95, "is_valid": true, "merge_with": null}},
  {{"text": "U.S.", "entity_type": "GPE", "canonical_name": "United States", \
"confidence": 0.95, "is_valid": true, "merge_with": "United States"}},
  {{"text": "AI", "entity_type": "PRODUCT", "canonical_name": "AI", \
"confidence": 0.7, "is_valid": true, "merge_with": null}},
  {{"text": "Vladimir Putin", "entity_type": "PERSON", \
"canonical_name": "Vladimir Putin", "confidence": 0.99, "is_valid": true, \
"merge_with": null}}
]

Now verify the following entities. Return ONLY a valid JSON array, no markdown fencing:

{entities_json}"""


_LOCATION_VERIFY_PROMPT = """\
You are a geospatial expert. Verify the geocoded coordinates for each entity.

Document context:
Title: {title}
Text snippet: {snippet}

For each entity with coordinates, determine:
1. should_geocode: Should this type of entity even have coordinates?
   - PERSON → false (people don't have fixed coordinates)
   - Abstract concepts, software names, acronyms → false
   - GPE (countries, cities), LOC, FAC → true
2. coordinates_correct: Are the provided lat/lon correct?
   - Check for obvious errors: e.g. "United States" should NOT be near \
lat=16.2, lon=-61.5 (that's Guadeloupe)
   - For countries: centroid should be in the rough geographic center
3. suggested_lat / suggested_lon: Provide correct coordinates if wrong
4. reason: Brief explanation

Few-shot examples:
Input: [{{"entity_text": "United States", "entity_type": "GPE", \
"lat": 16.2, "lon": -61.5}},
        {{"entity_text": "Vladimir Putin", "entity_type": "PERSON", \
"lat": 55.75, "lon": 37.61}}]

Output: [
  {{"entity_text": "United States", "should_geocode": true, \
"coordinates_correct": false, "suggested_lat": 39.5, "suggested_lon": -98.35, \
"reason": "Coordinates point to Guadeloupe; US centroid is ~39.5N, 98.35W"}},
  {{"entity_text": "Vladimir Putin", "should_geocode": false, \
"coordinates_correct": null, "suggested_lat": null, "suggested_lon": null, \
"reason": "PERSON entities should not be geocoded"}}
]

Now verify the following entities. Return ONLY a valid JSON array, no markdown fencing:

{entities_json}"""


_RELATIONSHIP_VERIFY_PROMPT = """\
You are an expert OSINT relationship analyst. Review the extracted relationships \
and verify each one.

Document context:
Title: {title}
Text snippet: {snippet}

For each relationship, determine:
1. is_meaningful: Is this a real, informative relationship, or just noise from \
two entities appearing near each other?
2. predicate: Provide a specific relationship type. Replace generic "co_occurs_with" \
with specific predicates like:
   leads, founded, directs, employed_by, located_in, subsidiary_of, acquired,
   allied_with, sanctioned_by, met_with, supplied_weapons_to, arrested, indicted,
   appointed, invested_in, funded_by, opposes, supports, attacked, negotiated_with
3. confidence: 0.0-1.0
4. reason: Brief explanation of why you classified it this way

Few-shot examples:
Input: [
  {{"subject": "NATO", "predicate": "co_occurs_with", "object": "Ukraine", \
"extraction_tier": 1}},
  {{"subject": "Sunday", "predicate": "co_occurs_with", "object": "Moscow", \
"extraction_tier": 1}},
  {{"subject": "Elon Musk", "predicate": "acquire", "object": "Twitter", \
"extraction_tier": 2}}
]

Output: [
  {{"subject": "NATO", "object": "Ukraine", "is_meaningful": true, \
"predicate": "allied_with", "confidence": 0.85, \
"reason": "NATO-Ukraine relationship is a well-established geopolitical fact"}},
  {{"subject": "Sunday", "object": "Moscow", "is_meaningful": false, \
"predicate": "co_occurs_with", "confidence": 0.1, \
"reason": "Day of week co-occurrence with city is not a meaningful relationship"}},
  {{"subject": "Elon Musk", "object": "Twitter", "is_meaningful": true, \
"predicate": "acquired", "confidence": 0.97, \
"reason": "Direct acquisition relationship clearly stated"}}
]

Now verify the following relationships. Return ONLY a valid JSON array, \
no markdown fencing:

{relationships_json}"""


# ─── LLM JSON parsing utility ────────────────────────────────────────────────

def _parse_llm_json(content: str) -> list[dict] | None:
    """Parse LLM JSON response, stripping markdown fences if needed."""
    try:
        result = json.loads(content)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    stripped = re.sub(r"```(?:json)?\s*\n?", "", content).strip().rstrip("`").strip()
    try:
        result = json.loads(stripped)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    return None


def _estimate_haiku_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * _HAIKU_INPUT_COST_PER_TOKEN
        + output_tokens * _HAIKU_OUTPUT_COST_PER_TOKEN
    )


# ─── Verification stats ───────────────────────────────────────────────────────

@dataclass
class VerificationStats:
    entities_filtered: int = 0
    entities_reclassified: int = 0
    entities_merged: int = 0
    locations_cleared: int = 0
    locations_corrected: int = 0
    relationships_pruned: int = 0
    relationships_enriched: int = 0
    entities_exa_enriched: int = 0
    haiku_calls: int = 0
    haiku_cost_usd: float = 0.0
    exa_calls: int = 0


# ─── EntityVerifier ──────────────────────────────────────────────────────────

class EntityVerifier:
    """Verifies and cleans extracted entities via Claude Haiku."""

    def __init__(
        self,
        anthropic_client: Any,
        budget_tracker: BudgetTracker,
        model: str = "claude-haiku-3-5-20241022",
        batch_size: int = 50,
    ) -> None:
        self._client = anthropic_client
        self._budget = budget_tracker
        self._model = model
        self._batch_size = batch_size

    async def verify(
        self,
        doc: PipelineDocument,
        stats: VerificationStats,
    ) -> PipelineDocument:
        """Verify and clean entities in-place on the PipelineDocument."""
        if not self._client or not self._budget.budget_available:
            return doc

        entities = doc.extracted_entities
        if not entities:
            return doc

        title = doc.title or ""
        snippet = (doc.full_text or "")[:500]

        # Process in batches
        verified_results: list[dict] = []
        for i in range(0, len(entities), self._batch_size):
            batch = entities[i : i + self._batch_size]
            batch_results = await self._verify_batch(batch, title, snippet, stats)
            verified_results.extend(batch_results)

        if not verified_results:
            return doc

        # Apply results
        doc = self._apply_results(doc, verified_results, stats)
        return doc

    async def _verify_batch(
        self,
        entities: list[ExtractedEntity],
        title: str,
        snippet: str,
        stats: VerificationStats,
    ) -> list[dict]:
        """Call Haiku to verify a batch of entities."""
        if not self._budget.budget_available:
            return []

        entities_json = json.dumps(
            [{"text": e.text, "entity_type": e.entity_type} for e in entities],
            indent=2,
        )

        prompt = _ENTITY_VERIFY_PROMPT.format(
            title=title,
            snippet=snippet,
            entities_json=entities_json,
        )

        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )

            cost = _estimate_haiku_cost(
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            self._budget.record_spend(cost)
            stats.haiku_calls += 1
            stats.haiku_cost_usd += cost

            content = response.content[0].text
            result = _parse_llm_json(content)

            if result is None:
                logger.warning(
                    "entity_verification_parse_failed",
                    content_preview=content[:200],
                )
                return []

            logger.debug(
                "entity_verification_complete",
                batch_size=len(entities),
                results=len(result),
                cost_usd=round(cost, 5),
            )
            return result

        except Exception as exc:
            logger.warning("entity_verification_failed", error=str(exc))
            return []

    def _apply_results(
        self,
        doc: PipelineDocument,
        results: list[dict],
        stats: VerificationStats,
    ) -> PipelineDocument:
        """Apply entity verification results to the document."""
        # Build lookup by text (case-insensitive)
        result_map: dict[str, dict] = {}
        for r in results:
            text = r.get("text", "")
            if text:
                result_map[text.lower()] = r

        new_entities: list[ExtractedEntity] = []
        canonical_remap: dict[str, str] = {}  # old canonical_name → new canonical_name

        for ent in doc.extracted_entities:
            vr = result_map.get(ent.text.lower())
            if vr is None:
                # Not in results — keep as-is
                new_entities.append(ent)
                continue

            if not vr.get("is_valid", True):
                # Filter out invalid entities
                stats.entities_filtered += 1
                logger.debug(
                    "entity_filtered",
                    text=ent.text,
                    entity_type=ent.entity_type,
                )
                # Remove from geospatial/temporal data
                old_key = f"{ent.text}:{ent.entity_type}"
                doc.geospatial_data.pop(old_key, None)
                doc.temporal_contexts.pop(old_key, None)
                continue

            # Check for merge
            merge_with = vr.get("merge_with")
            if merge_with:
                # Update resolved_entity_map to point to canonical entity
                old_key = f"{ent.text}:{ent.entity_type}"
                canonical_remap[old_key] = merge_with
                # Update the entity map entry
                if old_key in doc.resolved_entity_map:
                    doc.resolved_entity_map[old_key] = merge_with
                stats.entities_merged += 1
                logger.debug("entity_merged", text=ent.text, merge_with=merge_with)
                # Skip adding this entity since it merges into another
                continue

            # Update entity fields
            old_type = ent.entity_type
            new_type = vr.get("entity_type", ent.entity_type)
            new_confidence = float(vr.get("confidence", ent.confidence))
            new_canonical = vr.get("canonical_name", ent.text)

            if new_type != old_type:
                stats.entities_reclassified += 1
                logger.debug(
                    "entity_reclassified",
                    text=ent.text,
                    old_type=old_type,
                    new_type=new_type,
                )

            updated = ent.model_copy(
                update={
                    "entity_type": new_type,
                    "confidence": max(0.0, min(1.0, new_confidence)),
                    "text": new_canonical,
                }
            )
            new_entities.append(updated)

            # If the type changed, update keyed data
            if new_type != old_type or new_canonical != ent.text:
                old_key = f"{ent.text}:{old_type}"
                new_key = f"{new_canonical}:{new_type}"
                if old_key in doc.geospatial_data:
                    doc.geospatial_data[new_key] = doc.geospatial_data.pop(old_key)
                if old_key in doc.temporal_contexts:
                    doc.temporal_contexts[new_key] = doc.temporal_contexts.pop(old_key)
                if old_key in doc.resolved_entity_map:
                    doc.resolved_entity_map[new_key] = doc.resolved_entity_map.pop(old_key)

        doc.extracted_entities = new_entities
        return doc


# ─── LocationVerifier ────────────────────────────────────────────────────────

class LocationVerifier:
    """Verifies geocoded coordinates via Claude Haiku."""

    # Class-level geocoding correction cache: entity_text → corrected GeospatialData
    _correction_cache: dict[str, dict] = {}

    def __init__(
        self,
        anthropic_client: Any,
        budget_tracker: BudgetTracker,
        model: str = "claude-haiku-3-5-20241022",
        batch_size: int = 30,
    ) -> None:
        self._client = anthropic_client
        self._budget = budget_tracker
        self._model = model
        self._batch_size = batch_size

    async def verify(
        self,
        doc: PipelineDocument,
        stats: VerificationStats,
    ) -> PipelineDocument:
        """Verify geocoded entities in the document."""
        if not self._client or not self._budget.budget_available:
            return doc

        # Collect entities that have geospatial data
        geo_entities = []
        for ent in doc.extracted_entities:
            key = f"{ent.text}:{ent.entity_type}"
            geo = doc.geospatial_data.get(key)
            if geo and geo.resolved and geo.latitude is not None:
                geo_entities.append((ent, key, geo))

        if not geo_entities:
            return doc

        title = doc.title or ""
        snippet = (doc.full_text or "")[:500]

        # Process in batches
        for i in range(0, len(geo_entities), self._batch_size):
            batch = geo_entities[i : i + self._batch_size]
            await self._verify_batch(doc, batch, title, snippet, stats)

        return doc

    async def _verify_batch(
        self,
        doc: PipelineDocument,
        batch: list[tuple],
        title: str,
        snippet: str,
        stats: VerificationStats,
    ) -> None:
        """Verify a batch of geocoded entities."""
        if not self._budget.budget_available:
            return

        items = []
        cache_hits: dict[str, dict] = {}

        for ent, key, geo in batch:
            # Check correction cache first
            cached = self._correction_cache.get(ent.text)
            if cached is not None:
                cache_hits[key] = cached
                continue
            items.append({
                "entity_text": ent.text,
                "entity_type": ent.entity_type,
                "lat": geo.latitude,
                "lon": geo.longitude,
            })

        # Apply cached corrections
        for key, correction in cache_hits.items():
            self._apply_correction(doc, key, correction, stats)

        if not items:
            return

        entities_json = json.dumps(items, indent=2)
        prompt = _LOCATION_VERIFY_PROMPT.format(
            title=title,
            snippet=snippet,
            entities_json=entities_json,
        )

        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )

            cost = _estimate_haiku_cost(
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            self._budget.record_spend(cost)
            stats.haiku_calls += 1
            stats.haiku_cost_usd += cost

            content = response.content[0].text
            results = _parse_llm_json(content)

            if results is None:
                logger.warning(
                    "location_verification_parse_failed",
                    content_preview=content[:200],
                )
                return

            # Build entity_text → result map
            result_map = {r.get("entity_text", "").lower(): r for r in results}

            for ent, key, geo in batch:
                if ent.text in cache_hits:
                    continue
                vr = result_map.get(ent.text.lower())
                if vr is None:
                    continue

                # Cache the correction
                self._correction_cache[ent.text] = vr
                self._apply_correction(doc, key, vr, stats)

        except Exception as exc:
            logger.warning("location_verification_failed", error=str(exc))

    def _apply_correction(
        self,
        doc: PipelineDocument,
        key: str,
        correction: dict,
        stats: VerificationStats,
    ) -> None:
        """Apply a location verification correction to the document."""
        geo = doc.geospatial_data.get(key)
        if geo is None:
            return

        should_geocode = correction.get("should_geocode", True)
        if not should_geocode:
            # Clear geospatial data for this entity
            doc.geospatial_data[key] = GeospatialData(resolved=False)
            stats.locations_cleared += 1
            logger.debug(
                "location_cleared",
                entity_text=correction.get("entity_text", key),
                reason=correction.get("reason", ""),
            )
            return

        coordinates_correct = correction.get("coordinates_correct", True)
        if coordinates_correct is False:
            suggested_lat = correction.get("suggested_lat")
            suggested_lon = correction.get("suggested_lon")
            if suggested_lat is not None and suggested_lon is not None:
                updated = geo.model_copy(
                    update={
                        "latitude": float(suggested_lat),
                        "longitude": float(suggested_lon),
                        "geocoding_source": "llm_verified",
                    }
                )
                doc.geospatial_data[key] = updated
                stats.locations_corrected += 1
                logger.debug(
                    "location_corrected",
                    entity_text=correction.get("entity_text", key),
                    old_lat=geo.latitude,
                    old_lon=geo.longitude,
                    new_lat=suggested_lat,
                    new_lon=suggested_lon,
                    reason=correction.get("reason", ""),
                )


# ─── RelationshipVerifier ────────────────────────────────────────────────────

class RelationshipVerifier:
    """Verifies and enriches relationships via Claude Haiku."""

    def __init__(
        self,
        anthropic_client: Any,
        budget_tracker: BudgetTracker,
        model: str = "claude-haiku-3-5-20241022",
        batch_size: int = 40,
    ) -> None:
        self._client = anthropic_client
        self._budget = budget_tracker
        self._model = model
        self._batch_size = batch_size

    async def verify(
        self,
        doc: PipelineDocument,
        stats: VerificationStats,
    ) -> PipelineDocument:
        """Verify and enrich relationships in the document."""
        if not self._client or not self._budget.budget_available:
            return doc

        relationships = doc.extracted_relationships
        if not relationships:
            return doc

        title = doc.title or ""
        snippet = (doc.full_text or "")[:500]

        verified_results: list[dict] = []
        for i in range(0, len(relationships), self._batch_size):
            batch = relationships[i : i + self._batch_size]
            results = await self._verify_batch(batch, title, snippet, stats)
            verified_results.extend(results)

        if not verified_results:
            return doc

        doc = self._apply_results(doc, verified_results, stats)
        return doc

    async def _verify_batch(
        self,
        relationships: list[ExtractedRelationship],
        title: str,
        snippet: str,
        stats: VerificationStats,
    ) -> list[dict]:
        """Verify a batch of relationships."""
        if not self._budget.budget_available:
            return []

        rels_json = json.dumps(
            [
                {
                    "subject": r.subject_text,
                    "predicate": r.predicate,
                    "object": r.object_text,
                    "extraction_tier": r.extraction_tier,
                }
                for r in relationships
            ],
            indent=2,
        )

        prompt = _RELATIONSHIP_VERIFY_PROMPT.format(
            title=title,
            snippet=snippet,
            relationships_json=rels_json,
        )

        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )

            cost = _estimate_haiku_cost(
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            self._budget.record_spend(cost)
            stats.haiku_calls += 1
            stats.haiku_cost_usd += cost

            content = response.content[0].text
            results = _parse_llm_json(content)

            if results is None:
                logger.warning(
                    "relationship_verification_parse_failed",
                    content_preview=content[:200],
                )
                return []

            logger.debug(
                "relationship_verification_complete",
                batch_size=len(relationships),
                results=len(results),
                cost_usd=round(cost, 5),
            )
            return results

        except Exception as exc:
            logger.warning("relationship_verification_failed", error=str(exc))
            return []

    def _apply_results(
        self,
        doc: PipelineDocument,
        results: list[dict],
        stats: VerificationStats,
    ) -> PipelineDocument:
        """Apply relationship verification results."""
        # Build lookup by (subject, object) pair (case-insensitive)
        result_map: dict[tuple[str, str], dict] = {}
        for r in results:
            subj = r.get("subject", "").lower()
            obj = r.get("object", "").lower()
            if subj and obj:
                result_map[(subj, obj)] = r

        new_relationships: list[ExtractedRelationship] = []

        for rel in doc.extracted_relationships:
            key = (rel.subject_text.lower(), rel.object_text.lower())
            vr = result_map.get(key)

            if vr is None:
                # Not verified — keep as-is
                new_relationships.append(rel)
                continue

            if not vr.get("is_meaningful", True):
                stats.relationships_pruned += 1
                logger.debug(
                    "relationship_pruned",
                    subject=rel.subject_text,
                    predicate=rel.predicate,
                    object=rel.object_text,
                    reason=vr.get("reason", ""),
                )
                continue

            # Update predicate and confidence
            new_predicate = vr.get("predicate", rel.predicate)
            new_confidence = float(vr.get("confidence", rel.confidence))

            if new_predicate != rel.predicate:
                stats.relationships_enriched += 1
                logger.debug(
                    "relationship_enriched",
                    subject=rel.subject_text,
                    old_predicate=rel.predicate,
                    new_predicate=new_predicate,
                )

            updated = rel.model_copy(
                update={
                    "predicate": new_predicate,
                    "confidence": max(0.0, min(1.0, new_confidence)),
                }
            )
            new_relationships.append(updated)

        doc.extracted_relationships = new_relationships
        return doc


# ─── ExaEnricher ─────────────────────────────────────────────────────────────

@dataclass
class EntityEnrichment:
    """Enrichment metadata for an entity from Exa."""
    entity_text: str
    description: str = ""
    category: str = ""
    key_facts: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)


class ExaEnricher:
    """Enriches key entities with real-time context via Exa search."""

    # Important entity types that qualify for enrichment
    IMPORTANT_TYPES = frozenset({"GPE", "ORG", "PERSON", "FAC", "LOC"})
    HIGH_CONFIDENCE_TYPES = frozenset({"GPE", "ORG"})

    def __init__(
        self,
        api_key: str,
        budget_tracker: BudgetTracker,
        min_source_count: int = 3,
        exa_cache_ttl: float = 600.0,
    ) -> None:
        self._api_key = api_key
        self._budget = budget_tracker
        self._min_source_count = min_source_count
        self._cache_ttl = exa_cache_ttl
        self._enrichment_cache: dict[str, EntityEnrichment] = {}
        self._exa_client: Any = None

    def _get_exa_client(self) -> Any:
        """Lazy-initialize Exa client."""
        if self._exa_client is None:
            try:
                from exa_py import Exa
                self._exa_client = Exa(api_key=self._api_key)
            except ImportError:
                logger.warning("exa_py_not_installed")
                return None
        return self._exa_client

    def _is_important_entity(self, ent: ExtractedEntity) -> bool:
        """Determine if an entity qualifies for Exa enrichment."""
        if not ent.text or len(ent.text) < 3:
            return False
        if ent.entity_type in self.HIGH_CONFIDENCE_TYPES and ent.confidence >= 0.8:
            return True
        return False

    async def enrich(
        self,
        doc: PipelineDocument,
        stats: VerificationStats,
    ) -> dict[str, EntityEnrichment]:
        """Enrich qualifying entities in the document via Exa."""
        if not self._api_key or not self._budget.budget_available:
            return {}

        exa = self._get_exa_client()
        if exa is None:
            return {}

        # Count entity source appearances across the document for source_count proxy
        entity_text_counts: dict[str, int] = {}
        for ent in doc.extracted_entities:
            entity_text_counts[ent.text] = entity_text_counts.get(ent.text, 0) + 1

        qualifying = [
            ent for ent in doc.extracted_entities
            if (
                self._is_important_entity(ent)
                or entity_text_counts.get(ent.text, 0) >= self._min_source_count
            )
        ]

        # Deduplicate by canonical text
        seen_texts: set[str] = set()
        enrichments: dict[str, EntityEnrichment] = {}

        for ent in qualifying:
            if ent.text in seen_texts:
                continue
            seen_texts.add(ent.text)

            if not self._budget.budget_available:
                break

            # Check cache
            cached = self._enrichment_cache.get(ent.text)
            if cached and (time.time() - cached.fetched_at) < self._cache_ttl:
                enrichments[ent.text] = cached
                continue

            enrichment = await self._enrich_entity(exa, ent, doc.title or "", stats)
            if enrichment:
                enrichments[ent.text] = enrichment
                self._enrichment_cache[ent.text] = enrichment

        return enrichments

    async def _enrich_entity(
        self,
        exa: Any,
        ent: ExtractedEntity,
        doc_title: str,
        stats: VerificationStats,
    ) -> EntityEnrichment | None:
        """Fetch enrichment data for a single entity via Exa."""
        query = f'"{ent.text}" {doc_title}'[:200]

        try:
            response = await asyncio.to_thread(
                exa.search_and_contents,
                query,
                num_results=3,
                type="auto",
                use_autoprompt=True,
                text={"max_characters": 500},
            )

            stats.exa_calls += 1
            stats.entities_exa_enriched += 1

            key_facts: list[str] = []
            recent_events: list[str] = []
            sources: list[str] = []

            for result in response.results:
                text = getattr(result, "text", "") or ""
                title = getattr(result, "title", "") or ""
                url = getattr(result, "url", "") or ""

                if url:
                    sources.append(url)
                if title:
                    recent_events.append(title)
                if text and len(text) > 50:
                    key_facts.append(text[:200])

            description = recent_events[0] if recent_events else ""

            enrichment = EntityEnrichment(
                entity_text=ent.text,
                description=description,
                category=ent.entity_type,
                key_facts=key_facts[:3],
                recent_events=recent_events[:3],
                sources=sources[:3],
            )

            logger.debug(
                "entity_exa_enriched",
                entity=ent.text,
                sources_found=len(sources),
            )
            return enrichment

        except Exception as exc:
            logger.warning(
                "exa_enrichment_failed",
                entity=ent.text,
                error=str(exc),
            )
            return None


# ─── LLMVerificationStage ────────────────────────────────────────────────────

class LLMVerificationStage(EnrichmentStage):
    """Stage 7: LLM verification and enrichment layer.

    Runs all four verification components in sequence:
      1. EntityVerifier   — filter junk, fix types, deduplicate
      2. LocationVerifier — verify geocoded coordinates
      3. RelationshipVerifier — prune noise, enrich predicates
      4. ExaEnricher      — real-time entity context via Exa

    All components are optional (disabled if API keys / budget unavailable).
    Failures in any component don't block the pipeline.
    """

    def __init__(
        self,
        anthropic_client: Any = None,
        budget_tracker: BudgetTracker | None = None,
        exa_api_key: str = "",
        model: str = "claude-haiku-3-5-20241022",
        enabled: bool = True,
        exa_enabled: bool = True,
        exa_min_source_count: int = 3,
        batch_size: int = 50,
    ) -> None:
        self._enabled = enabled
        self._budget = budget_tracker or BudgetTracker()
        self._model = model
        self._batch_size = batch_size

        self._entity_verifier = EntityVerifier(
            anthropic_client=anthropic_client,
            budget_tracker=self._budget,
            model=model,
            batch_size=batch_size,
        )
        self._location_verifier = LocationVerifier(
            anthropic_client=anthropic_client,
            budget_tracker=self._budget,
            model=model,
            batch_size=max(1, batch_size // 2),
        )
        self._relationship_verifier = RelationshipVerifier(
            anthropic_client=anthropic_client,
            budget_tracker=self._budget,
            model=model,
            batch_size=batch_size,
        )

        self._exa_enricher: ExaEnricher | None = None
        if exa_enabled and exa_api_key:
            self._exa_enricher = ExaEnricher(
                api_key=exa_api_key,
                budget_tracker=self._budget,
                min_source_count=exa_min_source_count,
            )

    @property
    def name(self) -> str:
        return "llm_verification"

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Run all verification components on the document."""
        if not self._enabled:
            return doc

        stats = VerificationStats()
        start = time.monotonic()

        # 1. Entity verification
        try:
            doc = await self._entity_verifier.verify(doc, stats)
        except Exception as exc:
            logger.warning(
                "entity_verification_error",
                doc_id=doc.id,
                error=str(exc),
            )

        # 2. Location verification
        try:
            doc = await self._location_verifier.verify(doc, stats)
        except Exception as exc:
            logger.warning(
                "location_verification_error",
                doc_id=doc.id,
                error=str(exc),
            )

        # 3. Relationship verification
        try:
            doc = await self._relationship_verifier.verify(doc, stats)
        except Exception as exc:
            logger.warning(
                "relationship_verification_error",
                doc_id=doc.id,
                error=str(exc),
            )

        # 4. Exa enrichment
        if self._exa_enricher is not None:
            try:
                enrichments = await self._exa_enricher.enrich(doc, stats)
                # Store enrichment metadata on the document's ingest_metadata for now
                if enrichments:
                    doc.ingest_metadata["exa_enrichments"] = {
                        k: {
                            "description": v.description,
                            "category": v.category,
                            "key_facts": v.key_facts,
                            "recent_events": v.recent_events,
                            "sources": v.sources,
                        }
                        for k, v in enrichments.items()
                    }
            except Exception as exc:
                logger.warning(
                    "exa_enrichment_error",
                    doc_id=doc.id,
                    error=str(exc),
                )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_verification_complete",
            doc_id=doc.id,
            elapsed_ms=elapsed_ms,
            entities_filtered=stats.entities_filtered,
            entities_reclassified=stats.entities_reclassified,
            entities_merged=stats.entities_merged,
            locations_cleared=stats.locations_cleared,
            locations_corrected=stats.locations_corrected,
            relationships_pruned=stats.relationships_pruned,
            relationships_enriched=stats.relationships_enriched,
            exa_enriched=stats.entities_exa_enriched,
            haiku_calls=stats.haiku_calls,
            haiku_cost_usd=round(stats.haiku_cost_usd, 5),
            exa_calls=stats.exa_calls,
        )

        return doc

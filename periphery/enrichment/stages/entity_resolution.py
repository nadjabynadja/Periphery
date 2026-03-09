"""Stage 6 — Cross-Reference Entity Resolution.

Resolves different surface forms to the same underlying canonical entity.
Maintains a persistent entity index with exact matching, alias matching,
and fuzzy matching (scoped by entity type).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog

from periphery.enrichment.models import CanonicalEntity, PipelineDocument
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# Fuzzy matching threshold (Jaro-Winkler score, 0-1)
FUZZY_MATCH_THRESHOLD = 0.88


class EntityIndex:
    """In-memory entity resolution index.

    Stores canonical entities and supports exact, alias, and fuzzy matching.
    In production this would be backed by SQLite or Redis.
    """

    def __init__(self) -> None:
        self._entities: dict[str, CanonicalEntity] = {}  # canonical_id → entity
        self._name_index: dict[str, str] = {}  # lowercase name → canonical_id
        self._alias_index: dict[str, str] = {}  # lowercase alias → canonical_id
        self._type_index: dict[str, list[str]] = {}  # entity_type → [canonical_ids]

    def lookup_exact(self, text: str) -> CanonicalEntity | None:
        """Exact match lookup."""
        cid = self._name_index.get(text.lower())
        if cid:
            return self._entities.get(cid)
        return None

    def lookup_alias(self, text: str) -> CanonicalEntity | None:
        """Alias match lookup."""
        cid = self._alias_index.get(text.lower())
        if cid:
            return self._entities.get(cid)
        return None

    def lookup_fuzzy(
        self, text: str, entity_type: str
    ) -> tuple[CanonicalEntity | None, float]:
        """Fuzzy match lookup, scoped by entity type.

        Returns (entity, score) or (None, 0.0).
        """
        from rapidfuzz import fuzz

        candidates = self._type_index.get(entity_type, [])
        best_match: CanonicalEntity | None = None
        best_score = 0.0

        text_lower = text.lower()
        for cid in candidates:
            entity = self._entities[cid]
            # Compare against canonical name and all aliases
            names = [entity.canonical_name.lower()] + [
                a.lower() for a in entity.aliases
            ]
            for name in names:
                score = fuzz.WRatio(text_lower, name) / 100.0
                if score > best_score:
                    best_score = score
                    best_match = entity

        if best_score >= FUZZY_MATCH_THRESHOLD:
            return best_match, best_score
        return None, 0.0

    def register(
        self,
        text: str,
        entity_type: str,
        doc_id: str,
        credibility_tier: int = 4,
    ) -> CanonicalEntity:
        """Register a new canonical entity."""
        canonical_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        entity = CanonicalEntity(
            canonical_id=canonical_id,
            canonical_name=text,
            entity_type=entity_type,
            aliases=[text],
            first_seen=now,
            last_seen=now,
            source_documents=[doc_id],
            credibility_floor=credibility_tier,
            merge_confidence=1.0,
        )
        self._entities[canonical_id] = entity
        self._name_index[text.lower()] = canonical_id
        self._alias_index[text.lower()] = canonical_id
        if entity_type not in self._type_index:
            self._type_index[entity_type] = []
        self._type_index[entity_type].append(canonical_id)
        return entity

    def update(
        self,
        canonical_id: str,
        *,
        new_alias: str | None = None,
        doc_id: str | None = None,
        credibility_tier: int | None = None,
    ) -> None:
        """Update an existing canonical entity."""
        entity = self._entities.get(canonical_id)
        if not entity:
            return
        entity.last_seen = datetime.now(timezone.utc)
        if new_alias and new_alias not in entity.aliases:
            entity.aliases.append(new_alias)
            self._alias_index[new_alias.lower()] = canonical_id
        if doc_id and doc_id not in entity.source_documents:
            entity.source_documents.append(doc_id)
        if credibility_tier is not None:
            entity.credibility_floor = min(entity.credibility_floor, credibility_tier)

    def __len__(self) -> int:
        return len(self._entities)

    def get(self, canonical_id: str) -> CanonicalEntity | None:
        return self._entities.get(canonical_id)


class EntityResolutionStage(EnrichmentStage):
    """Stage 6: Resolve entities to canonical entries in the entity index."""

    def __init__(
        self,
        entity_index: EntityIndex | None = None,
        fuzzy_threshold: float = FUZZY_MATCH_THRESHOLD,
    ) -> None:
        self._index = entity_index or EntityIndex()
        self._fuzzy_threshold = fuzzy_threshold

    @property
    def name(self) -> str:
        return "entity_resolution"

    @property
    def entity_index(self) -> EntityIndex:
        return self._index

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Resolve entities against the canonical index."""
        credibility_tier = 4
        if doc.source_credibility:
            credibility_tier = doc.source_credibility.source_credibility_tier

        resolved = 0
        created = 0
        fuzzy = 0

        for entity in doc.extracted_entities:
            entity_key = f"{entity.text}:{entity.entity_type}"

            # 1. Exact match
            canonical = self._index.lookup_exact(entity.text)
            if canonical:
                doc.resolved_entity_map[entity_key] = canonical.canonical_id
                self._index.update(
                    canonical.canonical_id,
                    doc_id=doc.id,
                    credibility_tier=credibility_tier,
                )
                resolved += 1
                continue

            # 2. Alias match
            canonical = self._index.lookup_alias(entity.text)
            if canonical:
                doc.resolved_entity_map[entity_key] = canonical.canonical_id
                self._index.update(
                    canonical.canonical_id,
                    new_alias=entity.text,
                    doc_id=doc.id,
                    credibility_tier=credibility_tier,
                )
                resolved += 1
                continue

            # 3. Fuzzy match (scoped by type)
            canonical, score = self._index.lookup_fuzzy(
                entity.text, entity.entity_type
            )
            if canonical and score >= self._fuzzy_threshold:
                doc.resolved_entity_map[entity_key] = canonical.canonical_id
                self._index.update(
                    canonical.canonical_id,
                    new_alias=entity.text,
                    doc_id=doc.id,
                    credibility_tier=credibility_tier,
                )
                canonical.merge_confidence = min(canonical.merge_confidence, score)
                fuzzy += 1
                continue

            # 4. New entity — register it
            new_entity = self._index.register(
                entity.text, entity.entity_type, doc.id, credibility_tier
            )
            doc.resolved_entity_map[entity_key] = new_entity.canonical_id
            created += 1

        logger.debug(
            "entity_resolution_complete",
            doc_id=doc.id,
            resolved=resolved,
            fuzzy_matched=fuzzy,
            new_entities=created,
            index_size=len(self._index),
        )
        return doc

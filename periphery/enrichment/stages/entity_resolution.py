"""Stage 6 — Cross-Reference Entity Resolution.

Resolves different surface forms to the same underlying canonical entity.
Maintains a persistent entity index backed by SQLite with an in-memory
cache layer for fast synchronous lookups.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

import structlog

from periphery.enrichment.models import CanonicalEntity, PipelineDocument
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# Fuzzy matching threshold (Jaro-Winkler score, 0-1)
FUZZY_MATCH_THRESHOLD = 0.88

# Dirty flush thresholds
_FLUSH_BATCH_SIZE = 100
_FLUSH_INTERVAL_SECONDS = 30.0


class EntityIndex:
    """SQLite-backed entity resolution index with in-memory cache.

    All lookups are synchronous (from memory). Writes go to both the
    in-memory cache and the database. Dirty entities are batched and
    flushed periodically for write efficiency.
    """

    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path
        self._entities: dict[str, CanonicalEntity] = {}  # canonical_id → entity
        self._name_index: dict[str, str] = {}  # lowercase name → canonical_id
        self._alias_index: dict[str, str] = {}  # lowercase alias → canonical_id
        self._type_index: dict[str, list[str]] = {}  # entity_type → [canonical_ids]
        self._loaded = False
        self._dirty: set[str] = set()  # canonical_ids needing flush
        self._last_flush_time = time.monotonic()
        # Extra DB-only fields stored separately (bio, location)
        self._db_extras: dict[str, dict] = {}  # canonical_id → {bio_short, bio_long, ...}

    async def load(self) -> int:
        """Load all canonical entities from the database into memory."""
        from periphery.db import get_connection

        count = 0
        try:
            async with get_connection() as db:
                # Load canonical entities
                cursor = await db.execute(
                    "SELECT canonical_id, canonical_name, entity_type, aliases, "
                    "first_seen, last_seen, source_documents, credibility_floor, "
                    "merge_confidence, bio_short, bio_long, bio_generated_at, "
                    "location_lat, location_lon, location_name "
                    "FROM canonical_entities"
                )
                rows = await cursor.fetchall()

                for row in rows:
                    canonical_id = row[0]
                    canonical_name = row[1]
                    entity_type = row[2]

                    # Parse JSON fields
                    aliases = []
                    try:
                        aliases = json.loads(row[3]) if row[3] else []
                    except (json.JSONDecodeError, TypeError):
                        aliases = []

                    source_documents = []
                    try:
                        source_documents = json.loads(row[6]) if row[6] else []
                    except (json.JSONDecodeError, TypeError):
                        source_documents = []

                    # Parse timestamps
                    first_seen = _parse_timestamp(row[4])
                    last_seen = _parse_timestamp(row[5])

                    entity = CanonicalEntity(
                        canonical_id=canonical_id,
                        canonical_name=canonical_name,
                        entity_type=entity_type,
                        aliases=aliases,
                        first_seen=first_seen,
                        last_seen=last_seen,
                        source_documents=source_documents,
                        credibility_floor=row[7] if row[7] is not None else 4,
                        merge_confidence=row[8] if row[8] is not None else 1.0,
                    )

                    # Store in memory
                    self._entities[canonical_id] = entity
                    self._name_index[canonical_name.lower()] = canonical_id
                    if entity_type not in self._type_index:
                        self._type_index[entity_type] = []
                    self._type_index[entity_type].append(canonical_id)

                    # Store DB-only extras
                    self._db_extras[canonical_id] = {
                        "bio_short": row[9],
                        "bio_long": row[10],
                        "bio_generated_at": row[11],
                        "location_lat": row[12],
                        "location_lon": row[13],
                        "location_name": row[14],
                    }

                    count += 1

                # Load aliases
                cursor = await db.execute(
                    "SELECT alias_text, canonical_id FROM entity_aliases"
                )
                alias_rows = await cursor.fetchall()
                for arow in alias_rows:
                    alias_lower = arow[0].lower()
                    self._alias_index[alias_lower] = arow[1]
                    # Also ensure aliases list is synced
                    entity = self._entities.get(arow[1])
                    if entity and arow[0] not in entity.aliases:
                        entity.aliases.append(arow[0])

        except Exception:
            logger.warning("entity_index_load_failed", exc_info=True)

        self._loaded = True
        logger.info("entity_index_loaded", count=count)
        return count

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

    async def register(
        self,
        text: str,
        entity_type: str,
        doc_id: str,
        credibility_tier: int = 4,
    ) -> CanonicalEntity:
        """Register a new canonical entity (memory + database)."""
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

        # Update in-memory cache
        self._entities[canonical_id] = entity
        self._name_index[text.lower()] = canonical_id
        self._alias_index[text.lower()] = canonical_id
        if entity_type not in self._type_index:
            self._type_index[entity_type] = []
        self._type_index[entity_type].append(canonical_id)

        # Write to database
        try:
            from periphery.db import get_connection

            async with get_connection() as db:
                await db.execute(
                    """INSERT OR IGNORE INTO canonical_entities
                    (canonical_id, canonical_name, entity_type, aliases,
                     first_seen, last_seen, source_documents, document_count,
                     credibility_floor, merge_confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        canonical_id,
                        text,
                        entity_type,
                        json.dumps([text]),
                        now.isoformat(),
                        now.isoformat(),
                        json.dumps([doc_id]),
                        1,
                        credibility_tier,
                        1.0,
                    ),
                )
                await db.execute(
                    """INSERT OR IGNORE INTO entity_aliases
                    (alias_text, canonical_id, alias_type, match_score)
                    VALUES (?, ?, 'exact', 1.0)""",
                    (text, canonical_id),
                )
                await db.commit()
        except Exception:
            logger.debug("entity_register_db_write_failed", canonical_id=canonical_id, exc_info=True)

        return entity

    async def update(
        self,
        canonical_id: str,
        *,
        new_alias: str | None = None,
        doc_id: str | None = None,
        credibility_tier: int | None = None,
    ) -> None:
        """Update an existing canonical entity (memory + mark dirty)."""
        entity = self._entities.get(canonical_id)
        if not entity:
            return
        entity.last_seen = datetime.now(timezone.utc)
        if new_alias and new_alias not in entity.aliases:
            entity.aliases.append(new_alias)
            self._alias_index[new_alias.lower()] = canonical_id
            # Write alias to DB immediately (small insert)
            try:
                from periphery.db import get_connection

                async with get_connection() as db:
                    await db.execute(
                        """INSERT OR IGNORE INTO entity_aliases
                        (alias_text, canonical_id, alias_type, match_score)
                        VALUES (?, ?, 'alias', 1.0)""",
                        (new_alias, canonical_id),
                    )
                    await db.commit()
            except Exception:
                logger.debug("entity_alias_db_write_failed", exc_info=True)
        if doc_id and doc_id not in entity.source_documents:
            entity.source_documents.append(doc_id)
        if credibility_tier is not None:
            entity.credibility_floor = min(entity.credibility_floor, credibility_tier)

        # Mark dirty for batch flush
        self._dirty.add(canonical_id)

    async def update_location(
        self,
        canonical_id: str,
        *,
        lat: float,
        lon: float,
        name: str = "",
    ) -> None:
        """Update location data for a canonical entity."""
        # Update db_extras cache
        if canonical_id not in self._db_extras:
            self._db_extras[canonical_id] = {}
        self._db_extras[canonical_id]["location_lat"] = lat
        self._db_extras[canonical_id]["location_lon"] = lon
        self._db_extras[canonical_id]["location_name"] = name

        try:
            from periphery.db import get_connection

            async with get_connection() as db:
                await db.execute(
                    """UPDATE canonical_entities
                    SET location_lat = ?, location_lon = ?, location_name = ?
                    WHERE canonical_id = ?""",
                    (lat, lon, name, canonical_id),
                )
                await db.commit()
        except Exception:
            logger.debug("entity_location_update_failed", exc_info=True)

    async def flush(self) -> None:
        """Write all dirty entities to the database."""
        if not self._dirty:
            return

        dirty_ids = list(self._dirty)
        self._dirty.clear()
        self._last_flush_time = time.monotonic()

        try:
            from periphery.db import get_connection

            async with get_connection() as db:
                for cid in dirty_ids:
                    entity = self._entities.get(cid)
                    if not entity:
                        continue
                    await db.execute(
                        """UPDATE canonical_entities
                        SET aliases = ?, last_seen = ?, source_documents = ?,
                            document_count = ?, credibility_floor = ?,
                            merge_confidence = ?
                        WHERE canonical_id = ?""",
                        (
                            json.dumps(entity.aliases),
                            entity.last_seen.isoformat(),
                            json.dumps(entity.source_documents),
                            len(entity.source_documents),
                            entity.credibility_floor,
                            entity.merge_confidence,
                            cid,
                        ),
                    )
                await db.commit()

            logger.debug("entity_index_flushed", count=len(dirty_ids))
        except Exception:
            # Put them back as dirty for next flush attempt
            self._dirty.update(dirty_ids)
            logger.warning("entity_index_flush_failed", exc_info=True)

    def _should_auto_flush(self) -> bool:
        """Check if we should auto-flush based on batch size or time."""
        if len(self._dirty) >= _FLUSH_BATCH_SIZE:
            return True
        if time.monotonic() - self._last_flush_time > _FLUSH_INTERVAL_SECONDS:
            return True
        return False

    async def get_profile(self, canonical_id: str) -> dict | None:
        """Get full entity profile for the detail panel / entity page."""
        entity = self._entities.get(canonical_id)
        if not entity:
            return None

        extras = self._db_extras.get(canonical_id, {})

        return {
            "canonical_id": entity.canonical_id,
            "canonical_name": entity.canonical_name,
            "entity_type": entity.entity_type,
            "aliases": entity.aliases,
            "first_seen": entity.first_seen.isoformat() if entity.first_seen else None,
            "last_seen": entity.last_seen.isoformat() if entity.last_seen else None,
            "source_documents": entity.source_documents,
            "document_count": len(entity.source_documents),
            "credibility_floor": entity.credibility_floor,
            "merge_confidence": entity.merge_confidence,
            "bio_short": extras.get("bio_short"),
            "bio_long": extras.get("bio_long"),
            "bio_generated_at": extras.get("bio_generated_at"),
            "location_lat": extras.get("location_lat"),
            "location_lon": extras.get("location_lon"),
            "location_name": extras.get("location_name"),
        }

    async def generate_bio(
        self,
        canonical_id: str,
        anthropic_client,
        doc_contents: list[str],
    ) -> tuple[str, str]:
        """Generate short and long bios using Claude. Returns (bio_short, bio_long)."""
        entity = self._entities.get(canonical_id)
        if not entity:
            return ("", "")

        # Prepare document excerpts (first 500 chars each, up to 10)
        excerpts = []
        for content in doc_contents[:10]:
            excerpts.append(content[:500])

        prompt = f"""You are an intelligence analyst. Generate a profile for the following entity based on the source documents provided.

Entity: {entity.canonical_name}
Type: {entity.entity_type}
Also known as: {', '.join(entity.aliases)}

Source material:
{chr(10).join(f'--- Document ---{chr(10)}{e}' for e in excerpts)}

Return JSON:
{{
  "bio_short": "One sentence summary suitable for a tooltip or sidebar",
  "bio_long": "2-4 paragraph intelligence profile covering: identity, significance, known activities, relationships, and current status. Use intelligence community language."
}}"""

        try:
            response = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = response.content[0].text
            # Extract JSON from response
            import re
            json_match = re.search(r'\{[^{}]*"bio_short"[^{}]*"bio_long"[^{}]*\}', response_text, re.DOTALL)
            if json_match:
                bio_data = json.loads(json_match.group())
            else:
                bio_data = json.loads(response_text)

            bio_short = bio_data.get("bio_short", "")
            bio_long = bio_data.get("bio_long", "")
        except Exception:
            logger.warning("bio_generation_failed", canonical_id=canonical_id, exc_info=True)
            return ("", "")

        # Save to database
        now = datetime.now(timezone.utc)
        try:
            from periphery.db import get_connection

            async with get_connection() as db:
                await db.execute(
                    """UPDATE canonical_entities
                    SET bio_short = ?, bio_long = ?, bio_generated_at = ?
                    WHERE canonical_id = ?""",
                    (bio_short, bio_long, now.isoformat(), canonical_id),
                )
                await db.commit()
        except Exception:
            logger.debug("bio_save_failed", exc_info=True)

        # Update in-memory extras
        if canonical_id not in self._db_extras:
            self._db_extras[canonical_id] = {}
        self._db_extras[canonical_id]["bio_short"] = bio_short
        self._db_extras[canonical_id]["bio_long"] = bio_long
        self._db_extras[canonical_id]["bio_generated_at"] = now.isoformat()

        return (bio_short, bio_long)

    def __len__(self) -> int:
        return len(self._entities)

    def get(self, canonical_id: str) -> CanonicalEntity | None:
        return self._entities.get(canonical_id)


def _parse_timestamp(val) -> datetime:
    """Parse a timestamp string or return current UTC time."""
    if val is None:
        return datetime.now(timezone.utc)
    if isinstance(val, datetime):
        return val
    try:
        # Try ISO format
        return datetime.fromisoformat(str(val)).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


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

    async def initialize(self) -> None:
        """Load the entity index from the database."""
        count = await self._index.load()
        logger.info("entity_resolution_initialized", index_size=count)

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
                await self._index.update(
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
                await self._index.update(
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
                await self._index.update(
                    canonical.canonical_id,
                    new_alias=entity.text,
                    doc_id=doc.id,
                    credibility_tier=credibility_tier,
                )
                canonical.merge_confidence = min(canonical.merge_confidence, score)
                fuzzy += 1
                continue

            # 4. New entity — register it
            new_entity = await self._index.register(
                entity.text, entity.entity_type, doc.id, credibility_tier
            )
            doc.resolved_entity_map[entity_key] = new_entity.canonical_id
            created += 1

        # Propagate geospatial data to canonical entities
        for entity in doc.extracted_entities:
            entity_key = f"{entity.text}:{entity.entity_type}"
            canonical_id = doc.resolved_entity_map.get(entity_key)
            if not canonical_id:
                continue
            geo_data = doc.geospatial_data.get(entity_key)
            if geo_data and geo_data.resolved and geo_data.latitude is not None:
                extras = self._index._db_extras.get(canonical_id, {})
                if not extras.get("location_lat"):
                    await self._index.update_location(
                        canonical_id,
                        lat=geo_data.latitude,
                        lon=geo_data.longitude,
                        name=geo_data.display_name,
                    )

        # Flush dirty entities to database
        await self._index.flush()

        logger.debug(
            "entity_resolution_complete",
            doc_id=doc.id,
            resolved=resolved,
            fuzzy_matched=fuzzy,
            new_entities=created,
            index_size=len(self._index),
        )
        return doc

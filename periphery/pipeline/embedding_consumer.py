"""Embedding consumer — multi-space vector generation for enriched documents.

Transforms enriched documents into five independent embedding spaces:
  1. Semantic space — meaning of the document content (sentence-transformer)
  2. Entity space — structural fingerprint of extracted entities (sentence-transformer)
  3. Relational space — structural fingerprint of entity interactions (sentence-transformer)
  4. Temporal space — numerical encoding of temporal characteristics
  5. Geospatial space — numerical encoding of geographic characteristics

Each space gets its own FAISS index. The Crystallizer can query each space
independently or combine them for multi-dimensional analysis.
"""

from __future__ import annotations

import base64
import json
import math
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

from periphery.config import get_settings
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore, MultiSpaceIndexManager

from .consumer import StageConsumer

logger = structlog.get_logger(__name__)

# Fixed region mapping for geospatial one-hot encoding
REGION_NAMES = [
    "Middle East",
    "East Asia",
    "South Asia",
    "Southeast Asia",
    "Central Asia",
    "Europe",
    "North Africa",
    "Sub-Saharan Africa",
    "North America",
    "Latin America",
    "Oceania",
    "Other",
]

# Country-to-region mapping for one-hot encoding (covers most common references)
_COUNTRY_TO_REGION: dict[str, int] = {}
_REGION_COUNTRIES: dict[int, list[str]] = {
    0: [  # Middle East
        "saudi arabia", "iran", "iraq", "israel", "jordan", "lebanon", "syria",
        "yemen", "oman", "qatar", "bahrain", "kuwait", "united arab emirates",
        "uae", "turkey", "palestine",
    ],
    1: [  # East Asia
        "china", "japan", "south korea", "north korea", "taiwan", "mongolia",
        "hong kong", "macau",
    ],
    2: [  # South Asia
        "india", "pakistan", "bangladesh", "sri lanka", "nepal", "bhutan",
        "maldives", "afghanistan",
    ],
    3: [  # Southeast Asia
        "indonesia", "malaysia", "philippines", "vietnam", "thailand", "myanmar",
        "cambodia", "laos", "singapore", "brunei", "timor-leste",
    ],
    4: [  # Central Asia
        "kazakhstan", "uzbekistan", "turkmenistan", "tajikistan", "kyrgyzstan",
    ],
    5: [  # Europe
        "united kingdom", "uk", "france", "germany", "italy", "spain", "portugal",
        "netherlands", "belgium", "switzerland", "austria", "poland", "ukraine",
        "russia", "sweden", "norway", "denmark", "finland", "ireland", "greece",
        "czech republic", "czechia", "romania", "hungary", "serbia", "croatia",
        "bosnia", "bulgaria", "slovakia", "slovenia", "lithuania", "latvia",
        "estonia", "moldova", "belarus", "albania", "north macedonia", "montenegro",
        "kosovo", "iceland", "luxembourg", "malta", "cyprus",
    ],
    6: [  # North Africa
        "egypt", "libya", "tunisia", "algeria", "morocco", "sudan",
    ],
    7: [  # Sub-Saharan Africa
        "nigeria", "south africa", "kenya", "ethiopia", "ghana", "tanzania",
        "uganda", "mozambique", "angola", "cameroon", "senegal", "somalia",
        "democratic republic of the congo", "congo", "zimbabwe", "zambia",
        "botswana", "namibia", "rwanda", "mali", "niger", "chad",
    ],
    8: [  # North America
        "united states", "usa", "us", "canada", "mexico",
    ],
    9: [  # Latin America
        "brazil", "argentina", "colombia", "chile", "peru", "venezuela",
        "ecuador", "bolivia", "paraguay", "uruguay", "cuba", "panama",
        "costa rica", "guatemala", "honduras", "el salvador", "nicaragua",
        "dominican republic", "haiti", "jamaica", "trinidad and tobago",
    ],
    10: [  # Oceania
        "australia", "new zealand", "fiji", "papua new guinea",
    ],
}

# Build reverse lookup
for _region_idx, _countries in _REGION_COUNTRIES.items():
    for _country in _countries:
        _COUNTRY_TO_REGION[_country] = _region_idx


def _get_region_index(country: str) -> int:
    """Map a country name to its region index (0-11)."""
    return _COUNTRY_TO_REGION.get(country.lower().strip(), len(REGION_NAMES) - 1)


class EmbeddingConsumer(StageConsumer):
    """Processes documents from enriched -> embedding -> embedded.

    Generates embeddings in five independent spaces and stores them in
    both SQLite (durable) and FAISS (query-time) indices.
    """

    input_status = "enriched"
    processing_status = "embedding"
    output_status = "embedded"
    started_at_column = "embedding_started_at"
    completed_at_column = "embedding_completed_at"
    batch_size = 20

    def __init__(
        self,
        db_path: str,
        faiss_store: FAISSStore | None = None,
        multi_space_manager: MultiSpaceIndexManager | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(db_path, **kwargs)
        self._store = faiss_store
        self._multi_space = multi_space_manager
        # Corpus-level temporal normalization parameters
        self._temporal_min_ts: float | None = None
        self._temporal_max_ts: float | None = None

    def set_store(self, store: FAISSStore) -> None:
        """Set the FAISS store (for deferred initialization / backward compat)."""
        self._store = store

    def set_multi_space_manager(self, manager: MultiSpaceIndexManager) -> None:
        """Set the multi-space index manager."""
        self._multi_space = manager

    async def process(
        self, db: aiosqlite.Connection, doc_rows: list[dict[str, Any]]
    ) -> list[str]:
        """Generate multi-space embeddings for claimed documents."""
        if self._multi_space is None and self._store is None:
            logger.warning("no_index_manager_configured")
            return []

        settings = get_settings()
        start_time = time.monotonic()

        success_ids: list[str] = []
        valid_docs: list[dict[str, Any]] = []
        enrichments: list[dict[str, Any]] = []

        # Phase 1: Load enrichment data for all docs in batch
        for doc_row in doc_rows:
            doc_id = doc_row["id"]
            try:
                enrichment = await self._load_enrichment(db, doc_id)
                valid_docs.append(doc_row)
                enrichments.append(enrichment)
            except Exception:
                logger.exception("embedding_prep_failed", doc_id=doc_id)

        if not valid_docs:
            return []

        try:
            # Phase 2: Build text representations for batch encoding
            semantic_texts = []
            entity_texts = []
            relational_texts = []

            for i, doc_row in enumerate(valid_docs):
                content = doc_row.get("content", "") or ""
                enrichment = enrichments[i]
                semantic_texts.append(content)
                entity_texts.append(self._build_entity_text(enrichment))
                relational_texts.append(self._build_relational_text(enrichment))

            # Phase 3: Batch encode text embedding spaces
            semantic_vectors = embedder.embed(semantic_texts)
            entity_vectors = embedder.embed(entity_texts)
            relational_vectors = embedder.embed(relational_texts)

            dim = semantic_vectors.shape[1]
            model_name = settings.embedding_model

            # Phase 4: Compute chunk-level semantic embeddings
            all_chunk_data = []
            for i, doc_row in enumerate(valid_docs):
                content = doc_row.get("content", "") or ""
                chunks = self._chunk_text(
                    content,
                    chunk_size=settings.embedding_chunk_size,
                    overlap=settings.embedding_chunk_overlap,
                )
                all_chunk_data.append(chunks)

            # Phase 5: Compute numerical vectors (temporal + geospatial)
            temporal_vectors = self._compute_temporal_vectors(valid_docs, enrichments)
            geospatial_vectors = self._compute_geospatial_vectors(enrichments, settings)

            # Phase 6: Store everything per-document
            faiss_ids: list[str] = []
            faiss_semantic: list[np.ndarray] = []
            faiss_entity: list[np.ndarray] = []
            faiss_relational: list[np.ndarray] = []
            faiss_temporal: list[np.ndarray] = []
            faiss_geospatial: list[np.ndarray] = []

            for i, doc_row in enumerate(valid_docs):
                doc_id = doc_row["id"]
                enrichment = enrichments[i]
                try:
                    # Compute completeness flags
                    completeness = self._compute_completeness(enrichment)

                    # Encode chunk embeddings for storage
                    chunk_records = self._encode_chunk_records(all_chunk_data[i])

                    # Store in SQLite
                    await self._store_embedding(
                        db,
                        doc_id,
                        semantic_vec=semantic_vectors[i],
                        entity_vec=entity_vectors[i],
                        relational_vec=relational_vectors[i],
                        temporal_vec=temporal_vectors[i],
                        geospatial_vec=geospatial_vectors[i],
                        chunk_records=chunk_records,
                        completeness=completeness,
                        model_name=model_name,
                        dim=dim,
                    )

                    faiss_ids.append(doc_id)
                    faiss_semantic.append(semantic_vectors[i])
                    faiss_entity.append(entity_vectors[i])
                    faiss_relational.append(relational_vectors[i])
                    faiss_temporal.append(temporal_vectors[i])
                    faiss_geospatial.append(geospatial_vectors[i])
                    success_ids.append(doc_id)
                except Exception:
                    logger.exception("embedding_store_failed", doc_id=doc_id)

            # Phase 7: Update FAISS indices
            if faiss_ids:
                self._update_indices(
                    faiss_ids,
                    faiss_semantic,
                    faiss_entity,
                    faiss_relational,
                    faiss_temporal,
                    faiss_geospatial,
                )

            elapsed = time.monotonic() - start_time
            logger.info(
                "batch_embedded",
                count=len(success_ids),
                elapsed_ms=round(elapsed * 1000, 1),
                avg_ms=round((elapsed / len(success_ids)) * 1000, 1) if success_ids else 0,
            )

        except Exception:
            logger.exception("batch_embedding_failed")
            return []

        return success_ids

    # ── Text Representation Builders ──────────────────────────────────────

    def _build_entity_text(self, enrichment: dict[str, Any]) -> str:
        """Build structured text of entities grouped by type for entity space.

        Format: "PERSON: Name1, Name2 | ORG: Org1, Org2 | GPE: Place1"
        """
        entities = enrichment.get("entities") or []
        by_type: dict[str, list[str]] = {}

        for ent in entities:
            if isinstance(ent, dict):
                etype = ent.get("entity_type", "ENTITY")
                text = ent.get("text", "")
                if text:
                    by_type.setdefault(etype, []).append(text)

        if not by_type:
            return "no entities extracted"

        parts = []
        for etype, names in sorted(by_type.items()):
            parts.append(f"{etype}: {', '.join(names)}")
        return " | ".join(parts)

    def _build_relational_text(self, enrichment: dict[str, Any]) -> str:
        """Build structured text of relationships for relational space.

        Format: "Subject predicate Object | Subject predicate Object"
        """
        relationships = enrichment.get("relationships") or []
        parts: list[str] = []

        for rel in relationships:
            if isinstance(rel, dict):
                subj = rel.get("subject_id", "") or rel.get("subject_text", "")
                pred = rel.get("predicate", "")
                obj = rel.get("object_id", "") or rel.get("object_text", "")
                if pred:
                    parts.append(f"{subj} {pred} {obj}")

        return " | ".join(parts) if parts else "no relationships extracted"

    # ── Chunking ──────────────────────────────────────────────────────────

    def _chunk_text(
        self,
        text: str,
        chunk_size: int = 256,
        overlap: int = 64,
    ) -> list[dict[str, Any]]:
        """Split text into overlapping chunks by approximate token count.

        Returns list of {"chunk_text": str, "start_char": int, "end_char": int}.
        Uses whitespace-based approximation: 1 token ~ 4 chars.
        """
        if not text:
            return []

        # Approximate chars per token
        chars_per_token = 4
        chunk_chars = chunk_size * chars_per_token
        overlap_chars = overlap * chars_per_token

        if len(text) <= chunk_chars:
            return [{"chunk_text": text, "start_char": 0, "end_char": len(text)}]

        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_chars, len(text))
            # Try to break at whitespace
            if end < len(text):
                space_pos = text.rfind(" ", start + chunk_chars - overlap_chars, end + 1)
                if space_pos > start:
                    end = space_pos

            chunks.append({
                "chunk_text": text[start:end],
                "start_char": start,
                "end_char": end,
            })

            if end >= len(text):
                break

            start = end - overlap_chars
            if start <= chunks[-1]["start_char"]:
                start = end  # avoid infinite loop on very short overlaps

        return chunks

    def _encode_chunk_records(
        self, chunks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Embed chunks and encode embeddings as base64 for JSON storage."""
        if not chunks:
            return []

        texts = [c["chunk_text"] for c in chunks]
        chunk_vectors = embedder.embed(texts)

        records = []
        for i, chunk in enumerate(chunks):
            vec_bytes = chunk_vectors[i].tobytes()
            records.append({
                "chunk_text": chunk["chunk_text"][:500],  # truncate for storage
                "embedding": base64.b64encode(vec_bytes).decode("ascii"),
                "start_char": chunk["start_char"],
                "end_char": chunk["end_char"],
            })
        return records

    # ── Temporal Vector Construction ──────────────────────────────────────

    def _compute_temporal_vectors(
        self,
        doc_rows: list[dict[str, Any]],
        enrichments: list[dict[str, Any]],
    ) -> np.ndarray:
        """Compute temporal feature vectors for a batch of documents.

        Returns shape (n, 10) array with features:
          [0] publication_timestamp_normalized (0-1)
          [1] narrative_centroid_normalized (0-1)
          [2] temporal_span_days
          [3] pct_entities_current
          [4] pct_entities_historical
          [5] pct_entities_speculative
          [6] pct_relationships_current
          [7] pct_relationships_historical
          [8] days_since_earliest_date_reference
          [9] days_until_latest_date_reference
        """
        n = len(doc_rows)
        vectors = np.full((n, 10), 0.5, dtype=np.float32)  # default: max uncertainty
        now = datetime.now(timezone.utc)

        for i, doc_row in enumerate(doc_rows):
            enrichment = enrichments[i]
            temporal = enrichment.get("temporal_context")

            if temporal is None:
                continue  # keep default 0.5 vector

            try:
                # Publication timestamp
                pub_str = doc_row.get("published")
                if pub_str:
                    pub_dt = self._parse_datetime(pub_str)
                    if pub_dt:
                        vectors[i, 0] = self._normalize_timestamp(pub_dt)

                # Entity temporal status distribution
                entities = enrichment.get("entities") or []
                entity_statuses = self._extract_temporal_statuses(entities, temporal)
                total_ent = len(entity_statuses) if entity_statuses else 1
                vectors[i, 3] = entity_statuses.get("current", 0) / total_ent
                vectors[i, 4] = entity_statuses.get("historical", 0) / total_ent
                vectors[i, 5] = entity_statuses.get("speculative", 0) / total_ent

                # Relationship temporal status distribution
                relationships = enrichment.get("relationships") or []
                rel_statuses = self._extract_relationship_temporal_statuses(relationships)
                total_rel = sum(rel_statuses.values()) if rel_statuses else 1
                vectors[i, 6] = rel_statuses.get("current", 0) / total_rel
                vectors[i, 7] = rel_statuses.get("historical", 0) / total_rel

                # Date references from temporal context
                dates = self._extract_dates_from_temporal(temporal)
                if dates:
                    earliest = min(dates)
                    latest = max(dates)
                    vectors[i, 1] = self._normalize_timestamp(
                        earliest + (latest - earliest) / 2
                    )
                    span = (latest - earliest).days
                    vectors[i, 2] = min(span / 365.0, 1.0)  # normalize to max 1 year
                    vectors[i, 8] = min((now - earliest).days / 365.0, 1.0)
                    days_until = (latest - now).days
                    vectors[i, 9] = max(0.0, min(days_until / 365.0, 1.0))

            except Exception:
                logger.debug("temporal_vector_partial", doc_id=doc_row.get("id"))

        return vectors

    def _extract_temporal_statuses(
        self, entities: list[dict], temporal: Any
    ) -> dict[str, int]:
        """Count entity temporal statuses from temporal context data."""
        counts: dict[str, int] = {"current": 0, "historical": 0, "speculative": 0}
        if isinstance(temporal, dict):
            for key, ctx in temporal.items():
                if isinstance(ctx, dict):
                    status = ctx.get("status", "unresolved")
                    if status in counts:
                        counts[status] += 1
        elif isinstance(temporal, list):
            for ctx in temporal:
                if isinstance(ctx, dict):
                    status = ctx.get("status", "unresolved")
                    if status in counts:
                        counts[status] += 1
        return counts

    def _extract_relationship_temporal_statuses(
        self, relationships: list[dict],
    ) -> dict[str, int]:
        """Count relationship temporal qualifiers."""
        counts: dict[str, int] = {"current": 0, "historical": 0, "speculative": 0}
        for rel in relationships:
            if isinstance(rel, dict):
                qualifier = rel.get("temporal_qualifier", "")
                if qualifier in counts:
                    counts[qualifier] += 1
        return counts

    def _extract_dates_from_temporal(self, temporal: Any) -> list[datetime]:
        """Extract all explicit dates from temporal context data."""
        dates: list[datetime] = []
        items = []
        if isinstance(temporal, dict):
            items = list(temporal.values())
        elif isinstance(temporal, list):
            items = temporal

        for ctx in items:
            if not isinstance(ctx, dict):
                continue
            for key in ("explicit_date", "date_range_start", "date_range_end", "document_date"):
                val = ctx.get(key)
                if val:
                    dt = self._parse_datetime(val)
                    if dt:
                        dates.append(dt)
        return dates

    def _normalize_timestamp(self, dt: datetime) -> float:
        """Normalize a timestamp to 0.0-1.0 range across system lifetime."""
        ts = dt.timestamp()
        # Use corpus-level min/max if available, otherwise use reasonable defaults
        min_ts = self._temporal_min_ts or datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        max_ts = self._temporal_max_ts or datetime.now(timezone.utc).timestamp()
        if max_ts <= min_ts:
            return 0.5
        return max(0.0, min(1.0, (ts - min_ts) / (max_ts - min_ts)))

    @staticmethod
    def _parse_datetime(val: Any) -> datetime | None:
        """Parse a datetime from various formats."""
        if isinstance(val, datetime):
            return val
        if isinstance(val, (int, float)):
            try:
                return datetime.fromtimestamp(val, tz=timezone.utc)
            except (ValueError, OSError):
                return None
        if isinstance(val, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(val, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    continue
        return None

    # ── Geospatial Vector Construction ────────────────────────────────────

    def _compute_geospatial_vectors(
        self,
        enrichments: list[dict[str, Any]],
        settings: Any,
    ) -> np.ndarray:
        """Compute geospatial feature vectors for a batch of documents.

        Returns shape (n, base_dim + region_count) array with features:
          [0] centroid_latitude_normalized (-90..90 -> 0..1)
          [1] centroid_longitude_normalized (-180..180 -> 0..1)
          [2] geographic_spread_km_normalized
          [3] num_countries_referenced_normalized
          [4] num_locations_resolved
          [5] pct_locations_resolved
          [6..6+region_count] region one-hot vector
        """
        region_count = settings.embedding_region_count
        geo_dim = settings.embedding_geospatial_base_dim + region_count
        n = len(enrichments)
        vectors = np.zeros((n, geo_dim), dtype=np.float32)

        for i, enrichment in enumerate(enrichments):
            geo_data = enrichment.get("geospatial_data")
            if not geo_data:
                continue

            try:
                # Handle both document-level summary and per-entity geo data
                doc_geo = None
                entity_geos: list[dict] = []

                if isinstance(geo_data, dict):
                    # Could be document-level summary or per-entity mapping
                    if "geographic_centroid" in geo_data or "locations_found" in geo_data:
                        doc_geo = geo_data
                    else:
                        entity_geos = [
                            v for v in geo_data.values()
                            if isinstance(v, dict) and v.get("resolved")
                        ]
                elif isinstance(geo_data, list):
                    entity_geos = [
                        g for g in geo_data
                        if isinstance(g, dict) and g.get("resolved")
                    ]

                if doc_geo:
                    centroid = doc_geo.get("geographic_centroid")
                    if centroid and isinstance(centroid, dict):
                        lat = centroid.get("lat", 0)
                        lon = centroid.get("lon", 0)
                        vectors[i, 0] = (lat + 90.0) / 180.0
                        vectors[i, 1] = (lon + 180.0) / 360.0

                    spread = doc_geo.get("geographic_spread_km")
                    if spread is not None:
                        vectors[i, 2] = min(spread / 20000.0, 1.0)  # normalize to Earth diameter

                    countries = doc_geo.get("countries_referenced", [])
                    vectors[i, 3] = min(len(countries) / 10.0, 1.0)

                    found = doc_geo.get("locations_found", 0)
                    resolved = doc_geo.get("locations_resolved", 0)
                    vectors[i, 4] = min(resolved / 20.0, 1.0)
                    vectors[i, 5] = resolved / max(found, 1)

                    # Region one-hot from primary region or countries
                    primary_region = doc_geo.get("primary_region")
                    if primary_region and primary_region in REGION_NAMES:
                        idx = REGION_NAMES.index(primary_region)
                        vectors[i, settings.embedding_geospatial_base_dim + idx] = 1.0
                    elif countries:
                        for country in countries:
                            idx = _get_region_index(country)
                            vectors[i, settings.embedding_geospatial_base_dim + idx] = 1.0

                elif entity_geos:
                    # Compute from individual entity geos
                    lats = [g["latitude"] for g in entity_geos if g.get("latitude") is not None]
                    lons = [g["longitude"] for g in entity_geos if g.get("longitude") is not None]

                    if lats and lons:
                        avg_lat = sum(lats) / len(lats)
                        avg_lon = sum(lons) / len(lons)
                        vectors[i, 0] = (avg_lat + 90.0) / 180.0
                        vectors[i, 1] = (avg_lon + 180.0) / 360.0

                        # Geographic spread (simplified: max distance between points)
                        if len(lats) > 1:
                            spread = self._approx_spread_km(lats, lons)
                            vectors[i, 2] = min(spread / 20000.0, 1.0)

                    all_locations = enrichment.get("entities") or []
                    total_locs = sum(
                        1 for e in all_locations
                        if isinstance(e, dict) and e.get("entity_type") in ("GPE", "LOC", "FAC")
                    )
                    vectors[i, 4] = min(len(entity_geos) / 20.0, 1.0)
                    vectors[i, 5] = len(entity_geos) / max(total_locs, 1)

                    # Countries from hierarchy
                    countries_seen: set[str] = set()
                    for g in entity_geos:
                        hierarchy = g.get("hierarchy", {})
                        if isinstance(hierarchy, dict):
                            country = hierarchy.get("country")
                            if country:
                                countries_seen.add(country)
                    vectors[i, 3] = min(len(countries_seen) / 10.0, 1.0)

                    for country in countries_seen:
                        idx = _get_region_index(country)
                        vectors[i, settings.embedding_geospatial_base_dim + idx] = 1.0

            except Exception:
                logger.debug("geospatial_vector_partial", index=i)

        return vectors

    @staticmethod
    def _approx_spread_km(lats: list[float], lons: list[float]) -> float:
        """Approximate geographic spread in km using simplified Haversine."""
        max_dist = 0.0
        for j in range(len(lats)):
            for k in range(j + 1, len(lats)):
                dlat = math.radians(lats[k] - lats[j])
                dlon = math.radians(lons[k] - lons[j])
                a = (
                    math.sin(dlat / 2) ** 2
                    + math.cos(math.radians(lats[j]))
                    * math.cos(math.radians(lats[k]))
                    * math.sin(dlon / 2) ** 2
                )
                c = 2 * math.asin(min(1.0, math.sqrt(a)))
                dist = 6371.0 * c
                max_dist = max(max_dist, dist)
        return max_dist

    # ── Completeness Flags ────────────────────────────────────────────────

    def _compute_completeness(self, enrichment: dict[str, Any]) -> dict[str, bool]:
        """Determine which embedding spaces have meaningful data."""
        entities = enrichment.get("entities") or []
        relationships = enrichment.get("relationships") or []
        temporal = enrichment.get("temporal_context")
        geospatial = enrichment.get("geospatial_data")

        has_entities = len(entities) > 0
        has_relationships = len(relationships) > 0
        has_temporal = temporal is not None and (
            (isinstance(temporal, dict) and len(temporal) > 0)
            or (isinstance(temporal, list) and len(temporal) > 0)
        )
        has_geospatial = geospatial is not None and (
            (isinstance(geospatial, dict) and len(geospatial) > 0)
            or (isinstance(geospatial, list) and len(geospatial) > 0)
        )

        return {
            "semantic": True,  # always computed
            "entity": has_entities,
            "relational": has_relationships,
            "temporal": has_temporal,
            "geospatial": has_geospatial,
        }

    # ── Index Updates ─────────────────────────────────────────────────────

    def _update_indices(
        self,
        doc_ids: list[str],
        semantic: list[np.ndarray],
        entity: list[np.ndarray],
        relational: list[np.ndarray],
        temporal: list[np.ndarray],
        geospatial: list[np.ndarray],
    ) -> None:
        """Add vectors to all FAISS indices."""
        # Legacy single store (backward compat)
        if self._store is not None:
            vectors_array = np.stack(semantic).astype(np.float32)
            self._store.add(doc_ids, vectors_array)

        # Multi-space indices
        if self._multi_space is not None:
            space_vectors = {
                "semantic": np.stack(semantic),
                "entity": np.stack(entity),
                "relational": np.stack(relational),
                "temporal": np.stack(temporal),
                "geospatial": np.stack(geospatial),
            }
            for space, vecs in space_vectors.items():
                self._multi_space.add(space, doc_ids, vecs.astype(np.float32))

            # Save indices after each batch
            self._multi_space.save()

            # Check if rebuild needed
            if self._multi_space.track_batch(len(doc_ids)):
                logger.info("index_rebuild_triggered")

            logger.info(
                "multi_space_indices_updated",
                count=len(doc_ids),
                totals={
                    space: self._multi_space.total(space)
                    for space in self._multi_space.spaces
                },
            )

    # ── Enrichment Loading ────────────────────────────────────────────────

    async def _load_enrichment(
        self, db: aiosqlite.Connection, doc_id: str
    ) -> dict[str, Any]:
        """Load enrichment data for a document."""
        cursor = await db.execute(
            "SELECT entities, relationships, temporal_context, geospatial_data, cross_references "
            "FROM document_enrichments WHERE document_id = ?",
            (doc_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return {}

        result: dict[str, Any] = {}
        col_names = ["entities", "relationships", "temporal_context", "geospatial_data", "cross_references"]
        for i, name in enumerate(col_names):
            val = row[i]
            if val and isinstance(val, str):
                try:
                    result[name] = json.loads(val)
                except json.JSONDecodeError:
                    result[name] = None
            else:
                result[name] = val
        return result

    # ── SQLite Persistence ────────────────────────────────────────────────

    async def _store_embedding(
        self,
        db: aiosqlite.Connection,
        doc_id: str,
        *,
        semantic_vec: np.ndarray,
        entity_vec: np.ndarray,
        relational_vec: np.ndarray,
        temporal_vec: np.ndarray,
        geospatial_vec: np.ndarray,
        chunk_records: list[dict[str, Any]],
        completeness: dict[str, bool],
        model_name: str,
        dim: int,
    ) -> None:
        """Write all embedding vectors to document_embeddings table."""
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            INSERT OR REPLACE INTO document_embeddings
                (document_id, semantic_embedding, semantic_chunks,
                 entity_embedding, relational_embedding,
                 temporal_vector, geospatial_vector,
                 embedding_model, embedding_dimensions,
                 completeness, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                semantic_vec.tobytes(),
                json.dumps(chunk_records) if chunk_records else None,
                entity_vec.tobytes(),
                relational_vec.tobytes(),
                json.dumps(temporal_vec.tolist()),
                json.dumps(geospatial_vec.tolist()),
                model_name,
                dim,
                json.dumps(completeness),
                now,
                now,
            ),
        )
        await db.commit()

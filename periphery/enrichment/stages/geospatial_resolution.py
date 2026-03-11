"""Stage 4 -- Geospatial Resolution.

Resolves GPE, LOC, and FAC entities to coordinates using a tiered geocoding
architecture:
  Tier 1:   Persistent SQLite cache (instant, free)
  Tier 1.5: Embedding index (FAISS cosine similarity, fuzzy name matching)
  Tier 2:   GeoNames local database (fast, free, no rate limits)
  Tier 3:   Nominatim API (accurate, rate-limited)

Also handles:
  - Location entity identification (ORG+location modifiers, coordinates, addresses)
  - Ambiguity resolution (country context, co-occurring locations, population bias)
  - Geographic feature enrichment (type, bounding box, hierarchy, hotspot proximity)
  - Relationship geospatial enrichment (distance, cross-border, chokepoint proximity)
  - Document-level geospatial summary
"""

from __future__ import annotations
import sqlite3
import asyncio
from importlib.resources import path
import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import structlog

from periphery.enrichment.models import (
    BoundingBox,
    DocumentGeospatialSummary,
    ExtractedEntity,
    GeoCandidate,
    GeoHierarchy,
    GeospatialData,
    PipelineDocument,
    RelationshipGeospatial,
)
from periphery.enrichment.pipeline import EnrichmentStage


logger = structlog.get_logger(__name__)

# Entity types that should be directly geocoded
_GEO_ENTITY_TYPES = frozenset({"GPE", "LOC", "FAC"})

# Regex patterns for coordinate mentions
_COORD_DECIMAL = re.compile(
    r"(?P<lat>[-+]?\d{1,2}\.\d{3,})\s*[,\s]\s*(?P<lon>[-+]?\d{1,3}\.\d{3,})"
)
_COORD_DMS = re.compile(
    r"(?P<lat_d>\d{1,2})\s*[°]\s*(?P<lat_m>\d{1,2})\s*[\'′]\s*"
    r"(?P<lat_s>\d{1,2}(?:\.\d+)?)\s*[\"″]?\s*(?P<lat_dir>[NSns])\s*[,\s]\s*"
    r"(?P<lon_d>\d{1,3})\s*[°]\s*(?P<lon_m>\d{1,2})\s*[\'′]\s*"
    r"(?P<lon_s>\d{1,2}(?:\.\d+)?)\s*[\"″]?\s*(?P<lon_dir>[EWew])"
)

# Address pattern (simplified)
_ADDRESS_PATTERN = re.compile(
    r"\d{1,5}\s+[\w\s]{2,40},\s*[\w\s]{2,30},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?"
)

# Known maritime chokepoints for proximity calculations
_CHOKEPOINTS = {
    "Strait of Hormuz": (26.5667, 56.2500),
    "Bab el-Mandeb": (12.5833, 43.3333),
    "Strait of Malacca": (2.5000, 101.0000),
    "Suez Canal": (30.4550, 32.3500),
    "Taiwan Strait": (24.0000, 119.5000),
    "Panama Canal": (9.0800, -79.6800),
    "Bosphorus": (41.1190, 29.0510),
    "Strait of Gibraltar": (35.9654, -5.3478),
    "Dardanelles": (40.2000, 26.4000),
}

# Country name -> continent mapping for hierarchy enrichment
_CONTINENT_MAP: dict[str, str] = {}  # populated from seed data


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute haversine distance in kilometers between two points."""
    R = 6371.0
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _dms_to_decimal(d: float, m: float, s: float, direction: str) -> float:
    """Convert degrees/minutes/seconds to decimal degrees."""
    decimal = d + m / 60.0 + s / 3600.0
    if direction.upper() in ("S", "W"):
        decimal = -decimal
    return decimal


# ---------------------------------------------------------------------------
# Component 1: Persistent SQLite geocoding cache
# ---------------------------------------------------------------------------

class GeocodingCache:
    """Persistent SQLite geocoding cache.

    Stores geocoding results permanently -- location-to-coordinate mappings
    don't change, so every external API call that could have been a cache hit
    is wasted time and rate limit budget.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._memory: dict[tuple[str, str], GeospatialData] = {}
        self._db: sqlite3.Connection | None = None
        if db_path:
            self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite cache database."""
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS geocoding_cache (
                location_text TEXT,
                country_context TEXT DEFAULT '',
                latitude FLOAT,
                longitude FLOAT,
                display_name TEXT DEFAULT '',
                location_type TEXT DEFAULT '',
                bounding_box JSON,
                hierarchy JSON,
                confidence FLOAT DEFAULT 0.0,
                source TEXT DEFAULT '',
                candidates JSON,
                needs_crystallizer_resolution INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (location_text, country_context)
            )
        """)
        self._db.commit()
    def get(self, location: str, country_context: str = "") -> GeospatialData | None:
        """Look up a location in cache (memory first, then SQLite).

        Tries exact (location, country_context) first, then falls back to
        (location, "") if no match found with the given context.
        """
        loc_key = location.lower().strip()
        ctx_key = country_context.lower().strip()
        key = (loc_key, ctx_key)

        if key in self._memory:
            return self._memory[key]

        # Fallback: try with empty context
        fallback_key = (loc_key, "")
        if ctx_key and fallback_key in self._memory:
            return self._memory[fallback_key]

        # If no country_context given, also search for any entry with this location
        if not ctx_key:
            for mem_key, mem_val in self._memory.items():
                if mem_key[0] == loc_key:
                    return mem_val

        if self._db is not None:
            row = self._db.execute(
                "SELECT * FROM geocoding_cache WHERE location_text = ? AND country_context = ?",
                key,
            ).fetchone()
            if row:
                data = self._row_to_geospatial(row)
                self._memory[key] = data
                return data

            # Fallback: try empty context or any match
            if ctx_key:
                row = self._db.execute(
                    "SELECT * FROM geocoding_cache WHERE location_text = ? AND country_context = ''",
                    (loc_key,),
                ).fetchone()
            else:
                row = self._db.execute(
                    "SELECT * FROM geocoding_cache WHERE location_text = ? LIMIT 1",
                    (loc_key,),
                ).fetchone()
            if row:
                data = self._row_to_geospatial(row)
                self._memory[key] = data
                return data

        return None

    def put(self, location: str, data: GeospatialData, country_context: str = "") -> None:
        """Store a geocoding result in cache."""
        key = (location.lower().strip(), country_context.lower().strip())
        self._memory[key] = data

        if self._db is not None:
            bb_json = json.dumps(data.bounding_box.model_dump() if data.bounding_box else None)
            hier_json = json.dumps(data.hierarchy.model_dump())
            cand_json = json.dumps([c.model_dump() for c in data.candidates]) if data.candidates else None
            self._db.execute(
                """INSERT OR REPLACE INTO geocoding_cache
                   (location_text, country_context, latitude, longitude, display_name,
                    location_type, bounding_box, hierarchy, confidence, source,
                    candidates, needs_crystallizer_resolution)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key[0], key[1], data.latitude, data.longitude,
                    data.display_name, data.location_type, bb_json, hier_json,
                    data.confidence, data.geocoding_source, cand_json,
                    int(data.needs_crystallizer_resolution),
                ),
            )
            self._db.commit()

    def _row_to_geospatial(self, row: sqlite3.Row) -> GeospatialData:
        """Convert a database row to a GeospatialData object."""
        bb_data = json.loads(row["bounding_box"]) if row["bounding_box"] else None
        hier_data = json.loads(row["hierarchy"]) if row["hierarchy"] else {}
        cand_data = json.loads(row["candidates"]) if row["candidates"] else []

        bounding_box = BoundingBox(**bb_data) if bb_data else None
        hierarchy = GeoHierarchy(**hier_data) if hier_data else GeoHierarchy()
        candidates = [GeoCandidate(**c) for c in cand_data] if cand_data else []

        return GeospatialData(
            resolved=row["latitude"] is not None,
            latitude=row["latitude"],
            longitude=row["longitude"],
            display_name=row["display_name"] or "",
            location_type=row["location_type"] or "",
            bounding_box=bounding_box,
            hierarchy=hierarchy,
            confidence=row["confidence"] or 0.0,
            geocoding_source=row["source"] or "cache",
            candidates=candidates,
            needs_crystallizer_resolution=bool(row["needs_crystallizer_resolution"]),
        )

    def seed_from_file(self, seed_path: str) -> int:
        """Load seed data from a JSON file into the cache.

        Returns the number of entries loaded.
        """
        path = Path(seed_path)
        if not path.exists():
            logger.warning("seed_file_not_found", path=seed_path)
            return 0

        with open(path) as f:
            seeds = json.load(f)

        count = 0
        for entry in seeds:
            hier_raw = entry.get("hierarchy", {})
            bb_raw = entry.get("bounding_box")

            hierarchy = GeoHierarchy(**hier_raw) if hier_raw else GeoHierarchy()
            bounding_box = BoundingBox(**bb_raw) if bb_raw else None

            data = GeospatialData(
                resolved=True,
                latitude=entry["latitude"],
                longitude=entry["longitude"],
                display_name=entry.get("display_name", ""),
                location_type=entry.get("location_type", "city"),
                bounding_box=bounding_box,
                hierarchy=hierarchy,
                confidence=entry.get("confidence", 1.0),
                geocoding_source="cache",
            )
            self.put(
                entry["location_text"],
                data,
                country_context=entry.get("country_context", ""),
            )
            count += 1

        logger.info("geocoding_cache_seeded", entries=count, source=seed_path)
        return count

    def __len__(self) -> int:
        return len(self._memory)

    def close(self) -> None:
        """Close the SQLite connection if open."""
        if self._db is not None:
            self._db.close()
            self._db = None


# ---------------------------------------------------------------------------
# Component 2: GeoNames local database lookup
# ---------------------------------------------------------------------------

class GeoNamesIndex:
    """Local GeoNames database for fast, free geocoding.

    Reads from an SQLite database populated from GeoNames allCountries.txt.
    If the database doesn't exist, returns None for all queries (degrades
    gracefully so the system works without the GeoNames dump).
    """

    # Feature class priority: P (populated places) and A (admin) rank higher
    _FEATURE_CLASS_PRIORITY = {"P": 0, "A": 1, "S": 2, "T": 3, "H": 4, "L": 5, "R": 6, "U": 7, "V": 8}

    def __init__(self, db_path: str | None = None) -> None:
        self._db: sqlite3.Connection | None = None
        if db_path and Path(db_path).exists():
            try:
                self._db = sqlite3.connect(str(db_path))
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA busy_timeout=5000")  
            except Exception:
                logger.warning("geonames_db_open_failed", path=db_path)
                self._db = None

    @property
    def available(self) -> bool:
        return self._db is not None

    def lookup(
        self,
        name: str,
        country_code: str | None = None,
        feature_class: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Look up a location name in the GeoNames database.

        Returns a list of candidate dicts sorted by priority (feature class
        and population).
        """
        if self._db is None:
            return []

        params: list[Any] = []
        query = "SELECT * FROM geonames WHERE name = ? COLLATE NOCASE"
        params.append(name)

        if country_code:
            query += " AND country_code = ? COLLATE NOCASE"
            params.append(country_code)

        if feature_class:
            query += " AND feature_class = ?"
            params.append(feature_class)

        query += " ORDER BY population DESC LIMIT ?"
        params.append(limit)

        try:
            rows = self._db.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def fuzzy_lookup(self, name: str, limit: int = 10) -> list[dict]:
        """Fuzzy lookup using LIKE queries."""
        if self._db is None:
            return []
        try:
            rows = self._db.execute(
                "SELECT * FROM geonames WHERE name LIKE ? COLLATE NOCASE "
                "ORDER BY population DESC LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


# ---------------------------------------------------------------------------
# Component 3: Embedding-based geo index (Tier 2 fuzzy match)
# ---------------------------------------------------------------------------

class EmbeddingGeoIndex:
    """Embedding-based location lookup using FAISS cosine similarity.

    Provides fuzzy name matching as Tier 2 in the geocoding pipeline —
    handling alternate names, abbreviations, and partial names that
    exact-match tiers miss (e.g. "Hormuz" → "Strait of Hormuz",
    "NYC" → "New York", "Moskva" → "Moscow, Russia").

    The index stores normalized embeddings of rich location descriptions
    (e.g. "Moscow, Russia") and matches short query strings against them.
    Batch-embedded at startup from seed data + cache for efficiency.
    """

    def __init__(self) -> None:
        self._index: Any = None  # faiss.IndexFlatIP, lazy init
        self._entries: list[dict] = []  # parallel to index rows

    @property
    def size(self) -> int:
        return len(self._entries)

    def _ensure_index(self, dim: int) -> None:
        if self._index is not None:
            return
        try:
            import faiss
            self._index = faiss.IndexFlatIP(dim)
        except ImportError:
            logger.warning("faiss_not_available_skipping_geo_embedding_index")

    @staticmethod
    def _rich_text(name: str, geo: GeospatialData) -> str:
        """Build a rich description for embedding (index side)."""
        parts = [name]
        if geo.hierarchy and geo.hierarchy.region:
            parts.append(geo.hierarchy.region)
        if geo.hierarchy and geo.hierarchy.country:
            parts.append(geo.hierarchy.country)
        return ", ".join(parts[:3])

    def add_batch(self, entries: list[tuple[str, GeospatialData]]) -> int:
        """Batch-add resolved locations (one embed call for all)."""
        valid = [
            (name, geo) for name, geo in entries
            if geo.resolved and geo.latitude is not None
        ]
        if not valid:
            return 0

        texts = [self._rich_text(name, geo) for name, geo in valid]
        try:
            from periphery.ingest.embedder import embed
            vectors = embed(texts)
        except Exception as exc:
            logger.warning("geo_embed_batch_failed", error=str(exc))
            return 0

        self._ensure_index(vectors.shape[1])
        if self._index is None:
            return 0

        self._index.add(vectors)
        for name, geo in valid:
            self._entries.append({
                "name": name,
                "lat": geo.latitude,
                "lon": geo.longitude,
                "display_name": geo.display_name or name,
                "location_type": geo.location_type or "",
                "hierarchy": geo.hierarchy.model_dump() if geo.hierarchy else {},
                "confidence": geo.confidence,
            })
        return len(valid)

    def add(self, name: str, geo: GeospatialData) -> None:
        """Add a single resolved location to the index."""
        self.add_batch([(name, geo)])

    def lookup_with_meta(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.75,
    ) -> tuple[list[GeoCandidate], list[dict]]:
        """Return (candidates, index_entries) sorted by embedding similarity.

        Only returns pairs with cosine similarity >= min_score.
        The parallel entries list carries location_type, hierarchy, etc.
        """
        if self._index is None or self._index.ntotal == 0:
            return [], []
        try:
            from periphery.ingest.embedder import embed
            vec = embed([query])
        except Exception:
            return [], []

        k_actual = min(k, self._index.ntotal)
        scores, indices = self._index.search(vec, k_actual)

        candidates: list[GeoCandidate] = []
        entries: list[dict] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or float(score) < min_score:
                continue
            entry = self._entries[idx]
            candidates.append(GeoCandidate(
                latitude=entry["lat"],
                longitude=entry["lon"],
                display_name=entry["display_name"],
                # Scale embedding similarity by stored confidence
                confidence=min(1.0, float(score) * entry.get("confidence", 1.0)),
                population=None,
            ))
            entries.append(entry)
        return candidates, entries

    def lookup(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.75,
    ) -> list[GeoCandidate]:
        """Return up to k candidates matching query by embedding similarity."""
        candidates, _ = self.lookup_with_meta(query, k=k, min_score=min_score)
        return candidates

    def build_from_cache(self, cache: "GeocodingCache") -> int:
        """Populate the index from all resolved entries in a GeocodingCache."""
        seen: set[str] = set()
        entries: list[tuple[str, GeospatialData]] = []

        for (name, _ctx), geo in cache._memory.items():
            if name not in seen and geo.resolved and geo.latitude is not None:
                seen.add(name)
                entries.append((name, geo))

        if cache._db is not None:
            for row in cache._db.execute(
                "SELECT * FROM geocoding_cache WHERE latitude IS NOT NULL"
            ).fetchall():
                name = row["location_text"]
                if name not in seen:
                    seen.add(name)
                    entries.append((name, cache._row_to_geospatial(row)))

        count = self.add_batch(entries)
        logger.info("embedding_geo_index_built", count=count)
        return count


# ---------------------------------------------------------------------------
# Component 4: Nominatim API client (Tier 3)
# ---------------------------------------------------------------------------

class NominatimClient:
    """Async Nominatim API client with strict rate limiting.

    Respects Nominatim's 1 request/second limit. Uses the same style
    of rate limiting as the RSS politeness layer.
    """

    def __init__(self, rate_limit_delay: float = 1.0, user_agent: str = "periphery-enrichment/0.1") -> None:
        self._rate_limit_delay = rate_limit_delay
        self._user_agent = user_agent
        self._last_request_time: float = 0.0
        self._geocoder = None
        self._queue: asyncio.Queue[tuple[str, str, asyncio.Future]] = asyncio.Queue()
        self._processing = False

    def _get_geocoder(self):
        if self._geocoder is None:
            from geopy.geocoders import Nominatim
            self._geocoder = Nominatim(user_agent=self._user_agent)
        return self._geocoder

    async def geocode(self, location: str, country_context: str = "") -> list[dict]:
        """Geocode a location string using Nominatim.

        Returns a list of candidate results.
        """
        geocoder = self._get_geocoder()

        # Respect rate limits
        elapsed = time.time() - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)

        try:
            query = location
            if country_context and country_context.lower() != location.lower():
                query = f"{location}, {country_context}"

            results = geocoder.geocode(
                query, exactly_one=False, limit=5, timeout=10,
                addressdetails=True,
            )
            self._last_request_time = time.time()

            if not results and query != location:
                # Retry without context
                results = geocoder.geocode(
                    location, exactly_one=False, limit=5, timeout=10,
                    addressdetails=True,
                )
                self._last_request_time = time.time()

            if not results:
                return []

            candidates = []
            for result in results:
                raw = result.raw or {}
                address = raw.get("address", {})
                candidates.append({
                    "latitude": result.latitude,
                    "longitude": result.longitude,
                    "display_name": result.address,
                    "type": raw.get("type", ""),
                    "class": raw.get("class", ""),
                    "importance": float(raw.get("importance", 0.0)),
                    "boundingbox": raw.get("boundingbox"),
                    "address": address,
                    "country": address.get("country", ""),
                    "country_code": address.get("country_code", ""),
                    "state": address.get("state", ""),
                    "city": address.get("city", address.get("town", address.get("village", ""))),
                })
            return candidates

        except Exception as exc:
            logger.warning("nominatim_geocode_failed", location=location, error=str(exc))
            return []


# ---------------------------------------------------------------------------
# Component 5: Location entity identification
# ---------------------------------------------------------------------------

def _identify_geocoding_targets(doc: PipelineDocument) -> list[dict]:
    """Identify all entities that need geocoding from the document.

    Returns a list of dicts with keys: text, entity_type, entity_key, source.
    Handles:
    - Direct location entities (GPE, LOC, FAC)
    - ORGs with location modifiers
    - Coordinate mentions (already parsed)
    - Addresses
    """
    targets: list[dict] = []
    seen_keys: set[str] = set()

    # Direct location entities
    for ent in doc.extracted_entities:
        if ent.entity_type in _GEO_ENTITY_TYPES:
            key = f"{ent.text}:{ent.entity_type}"
            if key not in seen_keys:
                seen_keys.add(key)
                targets.append({
                    "text": ent.text,
                    "entity_type": ent.entity_type,
                    "entity_key": key,
                    "source": "direct",
                    "parent_entity": None,
                })

    # ORGs with location context ("the Moscow office of Gazprom")
    spacy_doc = doc.spacy_doc
    if spacy_doc is not None:
        for ent in doc.extracted_entities:
            if ent.entity_type in ("ORG", "PERSON", "EVENT"):
                location_modifier = _extract_location_modifier(
                    ent, spacy_doc, doc.full_text
                )
                if location_modifier:
                    key = f"{location_modifier}:GPE"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        targets.append({
                            "text": location_modifier,
                            "entity_type": "GPE",
                            "entity_key": key,
                            "source": "modifier",
                            "parent_entity": f"{ent.text}:{ent.entity_type}",
                        })

    # Coordinate mentions -- parse and validate directly
    text = doc.full_text
    for m in _COORD_DECIMAL.finditer(text):
        lat, lon = float(m.group("lat")), float(m.group("lon"))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            coord_text = m.group(0)
            key = f"{coord_text}:COORD"
            if key not in seen_keys:
                seen_keys.add(key)
                targets.append({
                    "text": coord_text,
                    "entity_type": "COORD",
                    "entity_key": key,
                    "source": "coordinate",
                    "latitude": lat,
                    "longitude": lon,
                })

    for m in _COORD_DMS.finditer(text):
        lat = _dms_to_decimal(
            float(m.group("lat_d")), float(m.group("lat_m")),
            float(m.group("lat_s")), m.group("lat_dir")
        )
        lon = _dms_to_decimal(
            float(m.group("lon_d")), float(m.group("lon_m")),
            float(m.group("lon_s")), m.group("lon_dir")
        )
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            coord_text = m.group(0)
            key = f"{coord_text}:COORD"
            if key not in seen_keys:
                seen_keys.add(key)
                targets.append({
                    "text": coord_text,
                    "entity_type": "COORD",
                    "entity_key": key,
                    "source": "coordinate",
                    "latitude": lat,
                    "longitude": lon,
                })

    # Address patterns
    for m in _ADDRESS_PATTERN.finditer(text):
        addr_text = m.group(0)
        key = f"{addr_text}:ADDRESS"
        if key not in seen_keys:
            seen_keys.add(key)
            targets.append({
                "text": addr_text,
                "entity_type": "ADDRESS",
                "entity_key": key,
                "source": "address",
                "parent_entity": None,
            })

    return targets


def _extract_location_modifier(
    entity: ExtractedEntity, spacy_doc: Any, text: str
) -> str | None:
    """Extract location modifiers from non-location entities using context.

    Looks for patterns like "in <location>", "of <location>", "from <location>"
    in the entity's context window.
    """
    context = entity.context_window
    if not context:
        return None

    # Simple heuristic: look for prepositional location patterns
    prep_pattern = re.compile(
        r"\b(?:in|of|from|near|at|based in)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    )
    for m in prep_pattern.finditer(context):
        candidate = m.group(1)
        # Verify it's not the entity itself
        if candidate.lower() != entity.text.lower() and len(candidate) > 2:
            return candidate

    return None


# ---------------------------------------------------------------------------
# Component 6: Ambiguity resolution
# ---------------------------------------------------------------------------

class AmbiguityResolver:
    """Resolves ambiguous locations using document context."""

    def resolve(
        self,
        location: str,
        candidates: list[GeoCandidate],
        doc_context: dict,
    ) -> tuple[GeoCandidate | None, list[GeoCandidate], bool]:
        """Resolve ambiguity among candidates.

        Returns (best_candidate, all_candidates, needs_crystallizer).
        """
        if not candidates:
            return None, [], True
        if len(candidates) == 1:
            return candidates[0], candidates, False

        # Score each candidate
        scored = []
        for cand in candidates:
            score = cand.confidence

            # Country context boost
            country_ctx = doc_context.get("country_context", [])
            if country_ctx:
                for ctx_country in country_ctx:
                    if ctx_country.lower() in cand.display_name.lower():
                        score += 0.3
                        break

            # Geographic centroid proximity boost
            centroid = doc_context.get("centroid")
            if centroid:
                dist = _haversine_km(
                    cand.latitude, cand.longitude,
                    centroid["lat"], centroid["lon"]
                )
                if dist < 500:
                    score += 0.25
                elif dist < 2000:
                    score += 0.1

            # Population bias
            if cand.population and cand.population > 1_000_000:
                score += 0.15
            elif cand.population and cand.population > 100_000:
                score += 0.05

            scored.append((score, cand))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score = scored[0][0]
        second_score = scored[1][0] if len(scored) > 1 else 0

        # If top two are close, flag for crystallizer
        needs_crystallizer = (best_score - second_score) < 0.15

        # Update candidate confidences
        resolved_candidates = []
        for score, cand in scored:
            resolved_candidates.append(
                GeoCandidate(
                    latitude=cand.latitude,
                    longitude=cand.longitude,
                    display_name=cand.display_name,
                    confidence=min(1.0, score),
                    population=cand.population,
                )
            )

        return resolved_candidates[0], resolved_candidates, needs_crystallizer


# ---------------------------------------------------------------------------
# Component 7: Geographic feature enrichment
# ---------------------------------------------------------------------------

def _classify_location_type(raw_type: str, raw_class: str, display_name: str) -> str:
    """Classify a location into a type category from Nominatim/GeoNames metadata."""
    display_lower = display_name.lower()

    # Check for specific facility/feature types
    if any(kw in display_lower for kw in ("strait", "channel", "passage")):
        return "maritime_chokepoint"
    if any(kw in display_lower for kw in ("canal",)):
        return "maritime_chokepoint"
    if any(kw in display_lower for kw in ("sea", "ocean", "gulf", "bay")):
        return "water_body"
    if any(kw in display_lower for kw in ("air base", "naval base", "military", "army", "fort ")):
        return "military_base"
    if any(kw in display_lower for kw in ("port of",)):
        return "port"
    if any(kw in display_lower for kw in ("wall street", "city of london", "financial")):
        return "financial_center"
    if any(kw in display_lower for kw in ("border crossing", "checkpoint")):
        return "border_crossing"

    # From Nominatim class/type
    type_lower = raw_type.lower() if raw_type else ""
    class_lower = raw_class.lower() if raw_class else ""

    if type_lower in ("city", "town", "village", "hamlet"):
        return "city"
    if type_lower in ("country", "nation"):
        return "country"
    if type_lower in ("state", "province", "region", "county", "administrative"):
        return "region"
    if class_lower == "aeroway":
        return "facility"
    if class_lower == "building":
        return "facility"

    # Default based on Nominatim class
    if class_lower == "place":
        return "city"
    if class_lower == "boundary":
        return "region"

    return "unknown"


def _build_hierarchy_from_address(address: dict) -> GeoHierarchy:
    """Build a GeoHierarchy from Nominatim address details."""
    return GeoHierarchy(
        city=address.get("city", address.get("town", address.get("village"))),
        region=address.get("state", address.get("county")),
        country=address.get("country"),
        continent=_get_continent(address.get("country", "")),
    )


def _get_continent(country: str) -> str | None:
    """Map a country name to its continent."""
    if not country:
        return None
    # Use the global continent map populated from seed data
    return _CONTINENT_MAP.get(country.lower())


def _nearest_chokepoint(lat: float, lon: float) -> tuple[str, float] | None:
    """Find the nearest maritime chokepoint and its distance."""
    nearest = None
    min_dist = float("inf")
    for name, (cp_lat, cp_lon) in _CHOKEPOINTS.items():
        dist = _haversine_km(lat, lon, cp_lat, cp_lon)
        if dist < min_dist:
            min_dist = dist
            nearest = name
    if nearest and min_dist < 1000:  # Only report if within 1000km
        return nearest, round(min_dist, 1)
    return None


def _build_bounding_box(raw_bb: list | None) -> BoundingBox | None:
    """Build a BoundingBox from Nominatim's boundingbox array [s, n, w, e]."""
    if not raw_bb or len(raw_bb) < 4:
        return None
    try:
        return BoundingBox(
            south=float(raw_bb[0]),
            north=float(raw_bb[1]),
            west=float(raw_bb[2]),
            east=float(raw_bb[3]),
        )
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main Stage
# ---------------------------------------------------------------------------

class GeospatialResolutionStage(EnrichmentStage):
    """Stage 4: Resolve location entities to coordinates.

    Uses a tiered geocoding approach:
      Tier 1:   Persistent cache (SQLite, exact match)
      Tier 1.5: Embedding index (FAISS cosine similarity, fuzzy name match)
      Tier 2:   GeoNames local database (exact + LIKE match)
      Tier 3:   Nominatim API (rate-limited fallback)

    Also enriches with:
      - Location type classification
      - Bounding boxes for area entities
      - Hierarchical containment
      - Chokepoint proximity
      - Relationship spatial metadata
      - Document-level geospatial summary
    """

    def __init__(
        self,
        cache: GeocodingCache | None = None,
        geonames: GeoNamesIndex | None = None,
        nominatim: NominatimClient | None = None,
        embedding_index: EmbeddingGeoIndex | None = None,
        geocoder: str = "nominatim",
        rate_limit_delay: float = 1.0,
        cache_db_path: str | None = None,
        geonames_db_path: str | None = None,
        seed_file_path: str | None = None,
    ) -> None:
        self._cache = cache or GeocodingCache(db_path=cache_db_path)
        self._geonames = geonames or GeoNamesIndex(db_path=geonames_db_path)
        self._nominatim = nominatim or NominatimClient(rate_limit_delay=rate_limit_delay)
        self._embedding_index = embedding_index or EmbeddingGeoIndex()
        self._geocoder_name = geocoder
        self._resolver = AmbiguityResolver()
        self._seeded = False
        self._seed_file_path = seed_file_path

        # Populate continent map and seed embedding index from cache
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        """Seed the cache from the seed file if not already done."""
        if self._seeded:
            return
        self._seeded = True

        seed_path = self._seed_file_path
        if not seed_path:
            # Try default location
            default_paths = [
                Path(__file__).parent.parent.parent.parent / "data" / "geospatial_seeds.json",
                Path("data/geospatial_seeds.json"),
            ]
            for p in default_paths:
                if p.exists():
                    seed_path = str(p)
                    break

        if seed_path and Path(seed_path).exists():
            count = self._cache.seed_from_file(seed_path)
            # Build continent map from seed data
            with open(seed_path) as f:
                seeds = json.load(f)
            for entry in seeds:
                hier = entry.get("hierarchy", {})
                country = hier.get("country", "")
                continent = hier.get("continent", "")
                if country and continent:
                    _CONTINENT_MAP[country.lower()] = continent

        # Build embedding index from all resolved cache entries
        if self._embedding_index.size == 0:
            self._embedding_index.build_from_cache(self._cache)

    @property
    def name(self) -> str:
        return "geospatial_resolution"

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Resolve location entities to coordinates."""
        # Identify all geocoding targets
        targets = _identify_geocoding_targets(doc)

        if not targets:
            doc.document_geospatial = DocumentGeospatialSummary()
            return doc

        # Build document context for disambiguation
        doc_context = self._build_document_context(doc, targets)

        # Geocode each target
        for target in targets:
            entity_key = target["entity_key"]

            # Already-parsed coordinates bypass geocoding
            if target["source"] == "coordinate":
                lat, lon = target["latitude"], target["longitude"]
                doc.geospatial_data[entity_key] = GeospatialData(
                    resolved=True,
                    latitude=lat,
                    longitude=lon,
                    display_name=target["text"],
                    location_type="coordinate",
                    confidence=1.0,
                    geocoding_source="parsed",
                )
                continue

            # Tiered geocoding
            geo_data = await self._tiered_geocode(
                target["text"],
                target["entity_type"],
                doc_context,
            )
            doc.geospatial_data[entity_key] = geo_data

        # Build document-level geospatial summary
        doc.document_geospatial = self._build_document_summary(doc)

        # Enrich relationships with spatial metadata
        self._enrich_relationships(doc)

        logger.debug(
            "geospatial_resolution_complete",
            doc_id=doc.id,
            targets=len(targets),
            resolved=sum(1 for g in doc.geospatial_data.values() if g.resolved),
            total=len(doc.geospatial_data),
            cache_size=len(self._cache),
        )
        return doc

    def _build_document_context(self, doc: PipelineDocument, targets: list[dict]) -> dict:
        """Build disambiguation context from the document.

        Extracts country mentions, computes geographic centroid of
        high-confidence locations, and identifies document topic signals.
        """
        context: dict = {
            "country_context": [],
            "centroid": None,
            "all_location_texts": [t["text"] for t in targets],
        }

        # Extract country mentions from entities
        country_mentions = []
        for ent in doc.extracted_entities:
            if ent.entity_type == "GPE":
                # Check if this is a known country from our cache
                cached = self._cache.get(ent.text)
                if cached and cached.location_type == "country":
                    country_mentions.append(ent.text)
                elif len(ent.text.split()) <= 2:
                    context["country_context"].append(ent.text)

        context["country_context"].extend(country_mentions)

        # Compute centroid of already-resolved locations from cache
        resolved_points = []
        for target in targets:
            cached = self._cache.get(target["text"])
            if cached and cached.resolved and cached.latitude is not None:
                resolved_points.append((cached.latitude, cached.longitude))

        if resolved_points:
            avg_lat = sum(p[0] for p in resolved_points) / len(resolved_points)
            avg_lon = sum(p[1] for p in resolved_points) / len(resolved_points)
            context["centroid"] = {"lat": avg_lat, "lon": avg_lon}

        return context

    async def _tiered_geocode(
        self,
        location: str,
        entity_type: str,
        doc_context: dict,
    ) -> GeospatialData:
        """Geocode using the tiered approach.

        Tier 1:   Persistent SQLite cache (exact match)
        Tier 1.5: Embedding index (cosine similarity, fuzzy name matching)
        Tier 2:   GeoNames local database
        Tier 3:   Nominatim API (rate-limited fallback)
        """

        # Determine country context
        country_ctx_list = doc_context.get("country_context", [])
        country_ctx = ""
        for ctx in country_ctx_list:
            if ctx.lower() != location.lower():
                country_ctx = ctx
                break

        # Tier 1: Cache lookup (exact match)
        cached = self._cache.get(location, country_ctx)
        if cached:
            return cached

        # Also try without country context
        if country_ctx:
            cached_no_ctx = self._cache.get(location)
            if cached_no_ctx:
                return cached_no_ctx

        # Tier 1.5: Embedding-based fuzzy name match
        emb_candidates, emb_entries = self._embedding_index.lookup_with_meta(location, k=5)
        if emb_candidates:
            best, all_candidates, needs_crystallizer = self._resolver.resolve(
                location, emb_candidates, doc_context
            )
            if best and best.confidence >= 0.75:
                # Recover hierarchy from the matched index entry
                best_entry = emb_entries[0] if emb_entries else {}
                hier_raw = best_entry.get("hierarchy", {})
                hierarchy = GeoHierarchy(**hier_raw) if hier_raw else GeoHierarchy()
                geo_data = GeospatialData(
                    resolved=True,
                    latitude=best.latitude,
                    longitude=best.longitude,
                    display_name=best.display_name,
                    location_type=best_entry.get("location_type", "city") or "city",
                    hierarchy=hierarchy,
                    confidence=best.confidence,
                    geocoding_source="embedding",
                    candidates=all_candidates if needs_crystallizer else [],
                    needs_crystallizer_resolution=needs_crystallizer,
                )
                self._cache.put(location, geo_data, country_ctx)
                logger.debug(
                    "geocode_embedding_hit",
                    location=location,
                    matched=best.display_name,
                    score=round(best.confidence, 3),
                )
                return geo_data

        # Tier 2: GeoNames local database
        if self._geonames.available:
            geo_data = self._geocode_geonames(location, entity_type, doc_context)
            if geo_data and geo_data.resolved:
                self._cache.put(location, geo_data, country_ctx)
                self._embedding_index.add(location, geo_data)
                return geo_data

        # Tier 3: Nominatim API (rate-limited)
        geo_data = await self._geocode_nominatim(location, entity_type, doc_context)
        self._cache.put(location, geo_data, country_ctx)
        if geo_data.resolved:
            self._embedding_index.add(location, geo_data)
        return geo_data

    def _geocode_geonames(
        self,
        location: str,
        entity_type: str,
        doc_context: dict,
    ) -> GeospatialData | None:
        """Geocode using the local GeoNames database."""
        # Determine preferred feature class from entity type
        feature_class = None
        if entity_type in ("GPE", "LOC"):
            feature_class = None  # search all, prioritize P and A
        elif entity_type == "FAC":
            feature_class = "S"

        results = self._geonames.lookup(location, feature_class=feature_class)
        if not results:
            results = self._geonames.fuzzy_lookup(location, limit=5)

        if not results:
            return None

        # Build candidates
        candidates = []
        for r in results:
            candidates.append(
                GeoCandidate(
                    latitude=r.get("latitude", 0.0),
                    longitude=r.get("longitude", 0.0),
                    display_name=f"{r.get('name', '')}, {r.get('country_code', '')}",
                    confidence=0.8,
                    population=r.get("population", 0),
                )
            )

        # Resolve ambiguity
        best, all_candidates, needs_crystallizer = self._resolver.resolve(
            location, candidates, doc_context
        )

        if not best:
            return None

        return GeospatialData(
            resolved=True,
            latitude=best.latitude,
            longitude=best.longitude,
            display_name=best.display_name,
            location_type="city",  # GeoNames default
            hierarchy=GeoHierarchy(),
            confidence=best.confidence,
            geocoding_source="geonames",
            candidates=all_candidates if needs_crystallizer else [],
            needs_crystallizer_resolution=needs_crystallizer,
        )

    async def _geocode_nominatim(
        self,
        location: str,
        entity_type: str,
        doc_context: dict,
    ) -> GeospatialData:
        """Geocode using the Nominatim API."""
        country_ctx = ""
        for ctx in doc_context.get("country_context", []):
            if ctx.lower() != location.lower():
                country_ctx = ctx
                break

        results = await self._nominatim.geocode(location, country_ctx)

        if not results:
            return GeospatialData(
                resolved=False,
                display_name=location,
                confidence=0.0,
                geocoding_source="nominatim",
                needs_crystallizer_resolution=True,
            )

        # Build candidates
        candidates = []
        for i, r in enumerate(results):
            confidence = max(0.3, r.get("importance", 0.5))
            candidates.append(
                GeoCandidate(
                    latitude=r["latitude"],
                    longitude=r["longitude"],
                    display_name=r["display_name"],
                    confidence=confidence,
                    population=None,
                )
            )

        # Resolve ambiguity
        best, all_candidates, needs_crystallizer = self._resolver.resolve(
            location, candidates, doc_context
        )

        if not best:
            return GeospatialData(
                resolved=False,
                display_name=location,
                confidence=0.0,
                geocoding_source="nominatim",
                candidates=all_candidates,
                needs_crystallizer_resolution=True,
            )

        # Get enrichment data from the best Nominatim result
        best_raw = results[0]
        raw_type = best_raw.get("type", "")
        raw_class = best_raw.get("class", "")
        location_type = _classify_location_type(raw_type, raw_class, best.display_name)
        bounding_box = _build_bounding_box(best_raw.get("boundingbox"))
        address = best_raw.get("address", {})
        hierarchy = _build_hierarchy_from_address(address)

        # For countries and regions, ensure bounding box is set
        if location_type in ("country", "region") and not bounding_box:
            bounding_box = _build_bounding_box(best_raw.get("boundingbox"))

        return GeospatialData(
            resolved=True,
            latitude=best.latitude,
            longitude=best.longitude,
            display_name=best.display_name,
            location_type=location_type,
            bounding_box=bounding_box,
            hierarchy=hierarchy,
            confidence=best.confidence,
            geocoding_source="nominatim",
            candidates=all_candidates if needs_crystallizer else [],
            needs_crystallizer_resolution=needs_crystallizer,
        )

    # -------------------------------------------------------------------
    # Document-level summary
    # -------------------------------------------------------------------

    def _build_document_summary(self, doc: PipelineDocument) -> DocumentGeospatialSummary:
        """Build a document-level geospatial summary."""
        geo_data = doc.geospatial_data
        locations_found = len(geo_data)
        locations_resolved = sum(1 for g in geo_data.values() if g.resolved)

        # Compute centroid and spread
        resolved_points = [
            (g.latitude, g.longitude)
            for g in geo_data.values()
            if g.resolved and g.latitude is not None and g.longitude is not None
        ]

        centroid = None
        spread_km = None
        if resolved_points:
            avg_lat = sum(p[0] for p in resolved_points) / len(resolved_points)
            avg_lon = sum(p[1] for p in resolved_points) / len(resolved_points)
            centroid = {"lat": round(avg_lat, 4), "lon": round(avg_lon, 4)}

            if len(resolved_points) > 1:
                max_dist = 0.0
                for i, p1 in enumerate(resolved_points):
                    for p2 in resolved_points[i + 1:]:
                        dist = _haversine_km(p1[0], p1[1], p2[0], p2[1])
                        max_dist = max(max_dist, dist)
                spread_km = round(max_dist, 1)

        # Determine primary region and countries
        countries: list[str] = []
        region_counter: Counter[str] = Counter()
        for g in geo_data.values():
            if g.hierarchy.country:
                countries.append(g.hierarchy.country)
            if g.hierarchy.region:
                region_counter[g.hierarchy.region] += 1
            elif g.hierarchy.country:
                region_counter[g.hierarchy.country] += 1

        primary_region = region_counter.most_common(1)[0][0] if region_counter else None
        unique_countries = list(dict.fromkeys(countries))  # preserve order, deduplicate

        return DocumentGeospatialSummary(
            locations_found=locations_found,
            locations_resolved=locations_resolved,
            geographic_centroid=centroid,
            geographic_spread_km=spread_km,
            primary_region=primary_region,
            countries_referenced=unique_countries,
        )

    # -------------------------------------------------------------------
    # Relationship geospatial enrichment
    # -------------------------------------------------------------------

    def _enrich_relationships(self, doc: PipelineDocument) -> None:
        """Add spatial metadata to relationships between geocoded entities."""
        for rel in doc.extracted_relationships:
            subj_key = f"{rel.subject_text}:{rel.subject_type}"
            obj_key = f"{rel.object_text}:{rel.object_type}"

            subj_geo = doc.geospatial_data.get(subj_key)
            obj_geo = doc.geospatial_data.get(obj_key)

            if not (subj_geo and obj_geo and subj_geo.resolved and obj_geo.resolved):
                continue

            if subj_geo.latitude is None or obj_geo.latitude is None:
                continue

            # Distance
            distance = _haversine_km(
                subj_geo.latitude, subj_geo.longitude,
                obj_geo.latitude, obj_geo.longitude,
            )

            # Cross-border flag
            subj_country = subj_geo.hierarchy.country
            obj_country = obj_geo.hierarchy.country
            cross_border = bool(
                subj_country and obj_country
                and subj_country.lower() != obj_country.lower()
            )

            # Chokepoint proximity: check if either endpoint is near a chokepoint
            chokepoint = None
            for point_geo in (subj_geo, obj_geo):
                if point_geo.latitude is not None:
                    cp_result = _nearest_chokepoint(point_geo.latitude, point_geo.longitude)
                    if cp_result:
                        chokepoint = f"{cp_result[0]} ({cp_result[1]}km)"
                        break

            rel_key = f"{rel.subject_text}-{rel.predicate}-{rel.object_text}"
            doc.relationship_geospatial[rel_key] = RelationshipGeospatial(
                distance_km=round(distance, 1),
                cross_border=cross_border,
                subject_country=subj_country,
                object_country=obj_country,
                chokepoint_proximity=chokepoint,
            )

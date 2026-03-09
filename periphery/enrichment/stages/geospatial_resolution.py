"""Stage 4 — Geospatial Resolution.

Resolves GPE and LOC entities to coordinates using geocoding services.
Caches results aggressively and uses document context for disambiguation.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import structlog

from periphery.enrichment.models import (
    GeoCandidate,
    GeospatialData,
    PipelineDocument,
)
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# Entity types that should be geocoded
_GEO_ENTITY_TYPES = frozenset({"GPE", "LOC", "FAC"})


class GeocodingCache:
    """In-memory geocoding cache to avoid repeat lookups."""

    def __init__(self) -> None:
        self._cache: dict[str, GeospatialData] = {}

    def get(self, location: str) -> GeospatialData | None:
        return self._cache.get(location.lower())

    def put(self, location: str, data: GeospatialData) -> None:
        self._cache[location.lower()] = data

    def __len__(self) -> int:
        return len(self._cache)


class GeospatialResolutionStage(EnrichmentStage):
    """Stage 4: Resolve location entities to coordinates."""

    def __init__(
        self,
        cache: GeocodingCache | None = None,
        geocoder: str = "nominatim",
        rate_limit_delay: float = 1.0,
    ) -> None:
        self._cache = cache or GeocodingCache()
        self._geocoder_name = geocoder
        self._rate_limit_delay = rate_limit_delay
        self._geocoder = None
        self._last_request_time: float = 0

    @property
    def name(self) -> str:
        return "geospatial_resolution"

    def _get_geocoder(self):
        """Lazy-load geocoder."""
        if self._geocoder is None:
            from geopy.geocoders import Nominatim

            self._geocoder = Nominatim(user_agent="periphery-enrichment/0.1")
        return self._geocoder

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Resolve location entities to coordinates."""
        location_entities = [
            e for e in doc.extracted_entities if e.entity_type in _GEO_ENTITY_TYPES
        ]

        if not location_entities:
            return doc

        # Extract context clues (co-occurring country/state mentions)
        context_locations = self._extract_context(doc)

        for entity in location_entities:
            entity_key = f"{entity.text}:{entity.entity_type}"

            # Check cache first
            cached = self._cache.get(entity.text)
            if cached:
                doc.geospatial_data[entity_key] = cached
                continue

            # Geocode
            geo_data = await self._geocode(entity.text, context_locations)
            doc.geospatial_data[entity_key] = geo_data
            self._cache.put(entity.text, geo_data)

        logger.debug(
            "geospatial_resolution_complete",
            doc_id=doc.id,
            resolved=sum(
                1 for g in doc.geospatial_data.values()
                if g.latitude is not None
            ),
            total=len(doc.geospatial_data),
            cache_size=len(self._cache),
        )
        return doc

    def _extract_context(self, doc: PipelineDocument) -> list[str]:
        """Extract country/state context clues from the document."""
        return [
            e.text
            for e in doc.extracted_entities
            if e.entity_type == "GPE" and len(e.text.split()) <= 2
        ]

    async def _geocode(
        self, location: str, context: list[str]
    ) -> GeospatialData:
        """Geocode a location string, using context for disambiguation."""
        geocoder = self._get_geocoder()

        # Respect rate limits
        import time

        elapsed = time.time() - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)

        try:
            # Try with context first for disambiguation
            query = location
            if context:
                # Add the most likely country/state for disambiguation
                for ctx in context:
                    if ctx.lower() != location.lower():
                        query = f"{location}, {ctx}"
                        break

            results = geocoder.geocode(
                query, exactly_one=False, limit=5, timeout=10
            )
            self._last_request_time = time.time()

            if not results:
                # Retry without context
                if query != location:
                    results = geocoder.geocode(
                        location, exactly_one=False, limit=5, timeout=10
                    )
                    self._last_request_time = time.time()

            if not results:
                return GeospatialData(
                    resolution_confidence=0.0,
                    geo_source=self._geocoder_name,
                )

            # Build candidates
            candidates = []
            for i, result in enumerate(results):
                confidence = max(0.3, 1.0 - (i * 0.15))
                candidates.append(
                    GeoCandidate(
                        latitude=result.latitude,
                        longitude=result.longitude,
                        display_name=result.address,
                        confidence=confidence,
                    )
                )

            best = candidates[0]
            return GeospatialData(
                latitude=best.latitude,
                longitude=best.longitude,
                resolution_confidence=best.confidence if len(candidates) == 1 else best.confidence * 0.8,
                geo_candidates=candidates if len(candidates) > 1 else [],
                geo_source=self._geocoder_name,
            )

        except Exception as exc:
            logger.warning(
                "geocoding_failed",
                location=location,
                error=str(exc),
            )
            return GeospatialData(
                resolution_confidence=0.0,
                geo_source=self._geocoder_name,
            )

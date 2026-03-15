"""OpenStreetMap — infrastructure and geographic feature monitoring via Overpass API.

Queries the Overpass API to monitor changes in infrastructure, military
installations, border crossings, airports, ports, and other features
of intelligence interest within specified bounding boxes.

Overpass API: https://overpass-api.de/api/interpreter
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone

import aiohttp

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id


# Pre-built Overpass queries for intelligence-relevant feature types
_FEATURE_QUERIES: dict[str, str] = {
    "military": '[out:json][timeout:60];(node["military"]{bbox};way["military"]{bbox};);out center meta;',
    "aeroway": '[out:json][timeout:60];(node["aeroway"~"aerodrome|helipad"]{bbox};way["aeroway"~"aerodrome|helipad"]{bbox};);out center meta;',
    "port": '[out:json][timeout:60];(node["harbour"="yes"]{bbox};way["harbour"="yes"]{bbox};node["industrial"="port"]{bbox};way["industrial"="port"]{bbox};);out center meta;',
    "border_crossing": '[out:json][timeout:60];(node["border_type"="checkpoint"]{bbox};node["barrier"="border_control"]{bbox};);out center meta;',
    "power_plant": '[out:json][timeout:60];(node["power"="plant"]{bbox};way["power"="plant"]{bbox};);out center meta;',
    "embassy": '[out:json][timeout:60];(node["amenity"="embassy"]{bbox};way["amenity"="embassy"]{bbox};);out center meta;',
}


class OpenStreetMapSource(DataSource):
    """Queries OpenStreetMap Overpass API for infrastructure features."""

    name = "openstreetmap"
    category = "infrastructure"
    default_poll_interval = 3600  # OSM data changes slowly

    OVERPASS_URL = "https://overpass-api.de/api/interpreter"

    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        feature_types: list[str] | None = None,
        custom_queries: list[str] | None = None,
        overpass_url: str | None = None,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._bbox = bbox  # (south, west, north, east)
        self._feature_types = feature_types or list(_FEATURE_QUERIES.keys())
        self._custom_queries = custom_queries or []
        if overpass_url:
            self.OVERPASS_URL = overpass_url
        self._known_features: dict[str, str] = {}  # feature_id → content hash

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        if not self._bbox:
            return []

        bbox_str = f"({self._bbox[0]},{self._bbox[1]},{self._bbox[2]},{self._bbox[3]})"
        docs: list[IngestedDocument] = []
        now = datetime.now(timezone.utc)

        # Run pre-built feature queries
        for feature_type in self._feature_types:
            template = _FEATURE_QUERIES.get(feature_type)
            if not template:
                continue
            query = template.replace("{bbox}", bbox_str)
            type_docs = await self._run_query(session, query, feature_type, now)
            docs.extend(type_docs)

        # Run custom Overpass queries
        for i, query in enumerate(self._custom_queries):
            query = query.replace("{bbox}", bbox_str)
            type_docs = await self._run_query(session, query, f"custom_{i}", now)
            docs.extend(type_docs)

        return docs

    async def _run_query(
        self,
        session: aiohttp.ClientSession,
        query: str,
        feature_type: str,
        now: datetime,
    ) -> list[IngestedDocument]:
        try:
            async with session.post(
                self.OVERPASS_URL,
                data={"data": query},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return []

        elements = data.get("elements", [])
        docs = []

        for elem in elements:
            doc = self._make_feature_doc(elem, feature_type, now)
            if doc:
                docs.append(doc)

        return docs

    def _make_feature_doc(
        self,
        elem: dict,
        feature_type: str,
        now: datetime,
    ) -> IngestedDocument | None:
        osm_type = elem.get("type", "node")
        osm_id = elem.get("id")
        if osm_id is None:
            return None

        tags = elem.get("tags", {})
        lat = elem.get("lat") or (elem.get("center", {}).get("lat"))
        lon = elem.get("lon") or (elem.get("center", {}).get("lon"))
        if lat is None or lon is None:
            return None

        name = tags.get("name", tags.get("name:en", f"Unnamed {feature_type}"))
        feature_key = f"{osm_type}/{osm_id}"

        # Build a content hash to detect changes
        tag_str = str(sorted(tags.items()))
        content_hash = hashlib.md5(tag_str.encode()).hexdigest()[:12]

        # Skip if we've already seen this exact feature state
        if self._known_features.get(feature_key) == content_hash:
            return None
        self._known_features[feature_key] = content_hash

        # Parse timestamp if available
        timestamp_str = elem.get("timestamp")
        published = now
        if timestamp_str:
            try:
                published = datetime.fromisoformat(
                    timestamp_str.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        # Build human-readable description
        content_parts = [
            f"OSM {feature_type}: {name}",
            f"at ({lat:.5f}, {lon:.5f})",
        ]

        # Include interesting tags
        for key in [
            "operator", "description", "military", "aeroway",
            "harbour", "power", "amenity", "capacity",
            "country", "addr:country",
        ]:
            val = tags.get(key)
            if val:
                content_parts.append(f"{key}={val}")

        if tags.get("disused") == "yes" or tags.get("abandoned") == "yes":
            content_parts.append("STATUS: disused/abandoned")

        content = " | ".join(content_parts)

        return IngestedDocument(
            id=make_document_id("osm", f"{feature_key}:{content_hash}"),
            source_feed="openstreetmap",
            source_category="infrastructure",
            source_credibility_tier=3,
            title=f"{feature_type.replace('_', ' ').title()}: {name}",
            url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            published=published,
            content=content,
            content_quality="full",
            metadata={
                "source_type": "osm_feature",
                "osm_type": osm_type,
                "osm_id": osm_id,
                "feature_type": feature_type,
                "latitude": lat,
                "longitude": lon,
                "name": name,
                "tags": tags,
                "last_edited_by": elem.get("user"),
                "version": elem.get("version"),
                "changeset": elem.get("changeset"),
            },
        )

"""Maritime vessel tracking via Position-API (AIS/MarineTraffic).

Uses the transparency-everywhere/position-api service which provides:
  GET /ais/mt/:mmsi/location/latest
  GET /legacy/getVesselsInArea/:area
  GET /legacy/getVesselsNearMe/:lat/:lng/:distance
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import aiohttp

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id


class MaritimeSource(DataSource):
    """Polls maritime vessel positions via Position-API."""

    name = "maritime"
    category = "maritime"
    default_poll_interval = 60

    def __init__(
        self,
        *,
        position_api_url: str = "http://localhost:3000",
        mmsi_watchlist: list[str] | None = None,
        watch_areas: list[str] | None = None,
        watch_points: list[dict] | None = None,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._api_url = position_api_url.rstrip("/")
        self._mmsi_watchlist: list[str] = mmsi_watchlist or []
        self._watch_areas: list[str] = watch_areas or []
        self._watch_points: list[dict] = watch_points or []
        self._last_positions: dict[str, dict] = {}

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        docs: list[IngestedDocument] = []
        now = datetime.now(timezone.utc)
        fetch_time = int(time.time())

        # Track individual vessels by MMSI
        for mmsi in self._mmsi_watchlist:
            doc = await self._fetch_vessel(session, mmsi, now, fetch_time)
            if doc:
                docs.append(doc)

        # Track vessels in areas (e.g., "WMED", "EMED")
        for area in self._watch_areas:
            area_docs = await self._fetch_area(session, area, now, fetch_time)
            docs.extend(area_docs)

        # Track vessels near geographic points
        for point in self._watch_points:
            lat = point.get("lat")
            lng = point.get("lng")
            distance = point.get("distance_nm", 50)
            if lat is not None and lng is not None:
                point_docs = await self._fetch_nearby(
                    session, lat, lng, distance, now, fetch_time
                )
                docs.extend(point_docs)

        return docs

    async def _fetch_vessel(
        self,
        session: aiohttp.ClientSession,
        mmsi: str,
        now: datetime,
        fetch_time: int,
    ) -> IngestedDocument | None:
        try:
            async with session.get(
                f"{self._api_url}/ais/mt/{mmsi}/location/latest",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        return self._make_vessel_doc(data, mmsi, now, fetch_time)

    async def _fetch_area(
        self,
        session: aiohttp.ClientSession,
        area: str,
        now: datetime,
        fetch_time: int,
    ) -> list[IngestedDocument]:
        try:
            async with session.get(
                f"{self._api_url}/legacy/getVesselsInArea/{area}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return []

        vessels = data if isinstance(data, list) else data.get("vessels", [])
        docs = []
        for vessel in vessels:
            mmsi = str(vessel.get("mmsi", ""))
            if mmsi:
                doc = self._make_vessel_doc(vessel, mmsi, now, fetch_time)
                if doc:
                    docs.append(doc)
        return docs

    async def _fetch_nearby(
        self,
        session: aiohttp.ClientSession,
        lat: float,
        lng: float,
        distance: float,
        now: datetime,
        fetch_time: int,
    ) -> list[IngestedDocument]:
        try:
            async with session.get(
                f"{self._api_url}/legacy/getVesselsNearMe/{lat}/{lng}/{distance}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return []

        vessels = data if isinstance(data, list) else data.get("vessels", [])
        docs = []
        for vessel in vessels:
            mmsi = str(vessel.get("mmsi", ""))
            if mmsi:
                doc = self._make_vessel_doc(vessel, mmsi, now, fetch_time)
                if doc:
                    docs.append(doc)
        return docs

    def _make_vessel_doc(
        self,
        data: dict,
        mmsi: str,
        now: datetime,
        fetch_time: int,
    ) -> IngestedDocument | None:
        if not data:
            return None

        lat = data.get("latitude") or data.get("lat")
        lon = data.get("longitude") or data.get("lon") or data.get("lng")
        if lat is None or lon is None:
            return None

        # Skip unchanged positions
        prev = self._last_positions.get(mmsi)
        if prev and prev.get("lat") == lat and prev.get("lon") == lon:
            return None
        self._last_positions[mmsi] = {"lat": lat, "lon": lon, "time": fetch_time}

        vessel_name = data.get("name") or data.get("shipName") or f"MMSI:{mmsi}"
        vessel_type = data.get("type") or data.get("shipType") or "unknown"
        speed = data.get("speed") or data.get("sog")
        course = data.get("course") or data.get("cog")
        destination = data.get("destination", "")
        flag = data.get("flag") or data.get("country", "")
        status = data.get("status") or data.get("navStatus", "")

        content_parts = [
            f"Vessel {vessel_name} (MMSI: {mmsi}) type={vessel_type}",
            f"at position ({lat:.4f}, {lon:.4f})",
        ]
        if flag:
            content_parts.append(f"flag: {flag}")
        if speed is not None:
            content_parts.append(f"speed {speed} kts")
        if course is not None:
            content_parts.append(f"course {course}°")
        if destination:
            content_parts.append(f"destination: {destination}")
        if status:
            content_parts.append(f"status: {status}")

        content = " | ".join(content_parts)

        return IngestedDocument(
            id=make_document_id("maritime", f"{mmsi}:{fetch_time}"),
            source_feed="maritime-ais",
            source_category="maritime",
            source_credibility_tier=2,
            title=f"Vessel {vessel_name} position update",
            url=f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{mmsi}",
            published=now,
            content=content,
            content_quality="full",
            processing_status="skip",  # Raw structured data — no LLM enrichment
            metadata={
                "source_type": "vessel_position",
                "mmsi": mmsi,
                "vessel_name": vessel_name,
                "vessel_type": vessel_type,
                "latitude": lat,
                "longitude": lon,
                "speed_kts": speed,
                "course_deg": course,
                "destination": destination,
                "flag": flag,
                "nav_status": status,
                "raw_response": data,
            },
        )

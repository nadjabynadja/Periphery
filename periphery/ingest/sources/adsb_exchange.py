"""ADS-B Exchange — aircraft position tracking via Position-API.

Uses the transparency-everywhere/position-api service which provides:
  GET /adsb/adsbe/:icao/location/latest
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import aiohttp

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id


class ADSBExchangeSource(DataSource):
    """Polls ADS-B Exchange via Position-API for tracked aircraft."""

    name = "adsb-exchange"
    category = "aviation"
    default_poll_interval = 30

    def __init__(
        self,
        *,
        position_api_url: str = "http://localhost:3000",
        icao_watchlist: list[str] | None = None,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._api_url = position_api_url.rstrip("/")
        self._icao_watchlist: list[str] = icao_watchlist or []
        self._last_positions: dict[str, dict] = {}

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        if not self._icao_watchlist:
            return []

        docs: list[IngestedDocument] = []
        now = datetime.now(timezone.utc)
        fetch_time = int(time.time())

        for icao in self._icao_watchlist:
            try:
                async with session.get(
                    f"{self._api_url}/adsb/adsbe/{icao}/location/latest",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue

            if not data:
                continue

            # Skip if position hasn't changed
            prev = self._last_positions.get(icao)
            lat = data.get("latitude") or data.get("lat")
            lon = data.get("longitude") or data.get("lon") or data.get("lng")
            if lat is None or lon is None:
                continue

            if prev and prev.get("lat") == lat and prev.get("lon") == lon:
                continue

            self._last_positions[icao] = {"lat": lat, "lon": lon, "time": fetch_time}

            altitude = data.get("altitude") or data.get("alt")
            speed = data.get("speed") or data.get("groundSpeed")
            heading = data.get("heading") or data.get("track")
            callsign = data.get("callsign", "").strip() or icao.upper()
            registration = data.get("registration", "")

            content_parts = [
                f"Aircraft {callsign} (ICAO: {icao.upper()}) tracked via ADS-B Exchange",
                f"at position ({lat:.4f}, {lon:.4f})",
            ]
            if altitude is not None:
                content_parts.append(f"altitude {altitude} ft")
            if speed is not None:
                content_parts.append(f"speed {speed} kts")
            if heading is not None:
                content_parts.append(f"heading {heading}°")
            if registration:
                content_parts.append(f"registration {registration}")

            content = " | ".join(content_parts)

            doc = IngestedDocument(
                id=make_document_id("adsb-exchange", f"{icao}:{fetch_time}"),
                source_feed="adsb-exchange",
                source_category="aviation",
                source_credibility_tier=2,
                title=f"ADS-B aircraft {callsign} position",
                url=f"https://globe.adsbexchange.com/?icao={icao}",
                published=now,
                content=content,
                content_quality="full",
                metadata={
                    "source_type": "aircraft_position",
                    "icao24": icao,
                    "callsign": callsign,
                    "registration": registration,
                    "latitude": lat,
                    "longitude": lon,
                    "altitude_ft": altitude,
                    "speed_kts": speed,
                    "heading_deg": heading,
                    "raw_response": data,
                },
            )
            docs.append(doc)

        return docs

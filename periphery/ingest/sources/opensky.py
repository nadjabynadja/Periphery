"""OpenSky Network — real-time aircraft state vectors.

Uses the free REST API at https://opensky-network.org/api/states/all
No authentication required for anonymous access (rate-limited to
~10 req/min, 5-second state resolution).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiohttp

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id


class OpenSkySource(DataSource):
    """Polls OpenSky Network for aircraft state vectors."""

    name = "opensky"
    category = "aviation"
    default_poll_interval = 15  # seconds — API updates every ~5s

    BASE_URL = "https://opensky-network.org/api"

    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        username: str | None = None,
        password: str | None = None,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._bbox = bbox  # (lamin, lomin, lamax, lomax)
        self._auth = aiohttp.BasicAuth(username, password) if username else None
        self._last_time: int = 0

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        params: dict[str, str] = {}
        if self._bbox:
            lamin, lomin, lamax, lomax = self._bbox
            params.update({
                "lamin": str(lamin),
                "lomin": str(lomin),
                "lamax": str(lamax),
                "lomax": str(lomax),
            })

        async with session.get(
            f"{self.BASE_URL}/states/all",
            params=params or None,
            auth=self._auth,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        api_time = data.get("time", 0)
        states = data.get("states") or []

        # Skip if we already processed this timestamp
        if api_time <= self._last_time:
            return []
        self._last_time = api_time

        docs: list[IngestedDocument] = []
        timestamp = datetime.fromtimestamp(api_time, tz=timezone.utc)

        for sv in states:
            icao24 = sv[0]
            callsign = (sv[1] or "").strip()
            origin_country = sv[2]
            longitude = sv[5]
            latitude = sv[6]
            baro_altitude = sv[7]
            on_ground = sv[8]
            velocity = sv[9]  # m/s
            true_track = sv[10]  # degrees clockwise from north
            vertical_rate = sv[11]
            geo_altitude = sv[13]
            squawk = sv[14]

            if latitude is None or longitude is None:
                continue

            display_callsign = callsign or icao24.upper()
            altitude_ft = round(baro_altitude * 3.281) if baro_altitude else None
            speed_kts = round(velocity * 1.944) if velocity else None

            content_parts = [
                f"Aircraft {display_callsign} (ICAO: {icao24.upper()}) observed",
                f"from {origin_country}",
                f"at position ({latitude:.4f}, {longitude:.4f})",
            ]
            if altitude_ft is not None:
                content_parts.append(
                    f"altitude {altitude_ft} ft {'(ground)' if on_ground else ''}"
                )
            if speed_kts is not None:
                content_parts.append(f"speed {speed_kts} kts")
            if true_track is not None:
                content_parts.append(f"heading {true_track:.0f}°")
            if vertical_rate is not None and vertical_rate != 0:
                direction = "climbing" if vertical_rate > 0 else "descending"
                content_parts.append(
                    f"{direction} at {abs(vertical_rate):.1f} m/s"
                )

            content = " | ".join(content_parts)

            doc = IngestedDocument(
                id=make_document_id("opensky", f"{icao24}:{api_time}"),
                source_feed="opensky-network",
                source_category="aviation",
                source_credibility_tier=2,
                title=f"Aircraft {display_callsign} position update",
                url=f"https://opensky-network.org/aircraft-profile?icao24={icao24}",
                published=timestamp,
                content=content,
                content_quality="full",
                metadata={
                    "source_type": "aircraft_position",
                    "icao24": icao24,
                    "callsign": callsign,
                    "origin_country": origin_country,
                    "latitude": latitude,
                    "longitude": longitude,
                    "baro_altitude_m": baro_altitude,
                    "geo_altitude_m": geo_altitude,
                    "on_ground": on_ground,
                    "velocity_ms": velocity,
                    "true_track_deg": true_track,
                    "vertical_rate_ms": vertical_rate,
                    "squawk": squawk,
                    "api_time": api_time,
                },
            )
            docs.append(doc)

        return docs

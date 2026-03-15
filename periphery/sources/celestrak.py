"""CelesTrak — satellite TLE (Two-Line Element) orbital data.

Fetches current orbital elements from CelesTrak's public GP data API:
  https://celestrak.org/NORAD/elements/gp.php?GROUP=...&FORMAT=json

Satellite groups include: active, stations, visual, weather, resource,
sarsat, geo, intelsat, ses, iridium, starlink, oneweb, etc.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

import aiohttp

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id


# Earth radius in km for orbital calculations
_EARTH_RADIUS_KM = 6371.0


def _period_minutes(mean_motion: float) -> float:
    """Orbital period in minutes from mean motion (revs/day)."""
    if mean_motion <= 0:
        return 0.0
    return 1440.0 / mean_motion


def _apogee_perigee_km(mean_motion: float, eccentricity: float) -> tuple[float, float]:
    """Approximate apogee and perigee altitude in km from TLE elements."""
    if mean_motion <= 0:
        return (0.0, 0.0)
    # Semi-major axis from mean motion (Kepler's third law)
    mu = 398600.4418  # km^3/s^2
    n_rad_s = mean_motion * 2 * math.pi / 86400.0
    a = (mu / (n_rad_s ** 2)) ** (1.0 / 3.0)
    apogee = a * (1 + eccentricity) - _EARTH_RADIUS_KM
    perigee = a * (1 - eccentricity) - _EARTH_RADIUS_KM
    return (round(apogee, 1), round(perigee, 1))


class CelesTrakSource(DataSource):
    """Polls CelesTrak for satellite orbital data."""

    name = "celestrak"
    category = "space"
    default_poll_interval = 3600  # TLE data updates infrequently

    BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"

    def __init__(
        self,
        *,
        groups: list[str] | None = None,
        norad_ids: list[int] | None = None,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._groups = groups or ["stations", "active"]
        self._norad_ids = norad_ids or []
        self._known_epochs: dict[int, str] = {}  # NORAD ID → last epoch

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        docs: list[IngestedDocument] = []

        # Fetch by group
        for group in self._groups:
            group_docs = await self._fetch_group(session, group)
            docs.extend(group_docs)

        # Fetch individual satellites by NORAD ID
        for norad_id in self._norad_ids:
            doc = await self._fetch_single(session, norad_id)
            if doc:
                docs.append(doc)

        return docs

    async def _fetch_group(
        self, session: aiohttp.ClientSession, group: str
    ) -> list[IngestedDocument]:
        try:
            async with session.get(
                self.BASE_URL,
                params={"GROUP": group, "FORMAT": "json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return []

        if not isinstance(data, list):
            return []

        docs = []
        for sat in data:
            doc = self._make_sat_doc(sat, group)
            if doc:
                docs.append(doc)
        return docs

    async def _fetch_single(
        self, session: aiohttp.ClientSession, norad_id: int
    ) -> IngestedDocument | None:
        try:
            async with session.get(
                self.BASE_URL,
                params={"CATNR": str(norad_id), "FORMAT": "json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        if not isinstance(data, list) or not data:
            return None
        return self._make_sat_doc(data[0], "tracked")

    def _make_sat_doc(
        self, sat: dict, group: str
    ) -> IngestedDocument | None:
        norad_id = sat.get("NORAD_CAT_ID")
        if norad_id is None:
            return None

        epoch = sat.get("EPOCH", "")
        # Skip if epoch hasn't changed (no new data)
        prev_epoch = self._known_epochs.get(norad_id)
        if prev_epoch == epoch:
            return None
        self._known_epochs[norad_id] = epoch

        name = sat.get("OBJECT_NAME", f"NORAD:{norad_id}").strip()
        intl_designator = sat.get("OBJECT_ID", "")
        inclination = sat.get("INCLINATION", 0)
        eccentricity = sat.get("ECCENTRICITY", 0)
        mean_motion = sat.get("MEAN_MOTION", 0)
        raan = sat.get("RA_OF_ASC_NODE", 0)
        arg_pericenter = sat.get("ARG_OF_PERICENTER", 0)
        mean_anomaly = sat.get("MEAN_ANOMALY", 0)
        rev_at_epoch = sat.get("REV_AT_EPOCH", 0)
        object_type = sat.get("OBJECT_TYPE", "")
        rcs_size = sat.get("RCS_SIZE", "")
        decay_date = sat.get("DECAY_DATE")

        period_min = _period_minutes(mean_motion)
        apogee_km, perigee_km = _apogee_perigee_km(mean_motion, eccentricity)

        content_parts = [
            f"Satellite {name} (NORAD: {norad_id})",
            f"group={group}",
            f"inclination {inclination:.1f}°",
            f"period {period_min:.1f} min",
            f"apogee {apogee_km:.0f} km / perigee {perigee_km:.0f} km",
            f"eccentricity {eccentricity:.6f}",
        ]
        if object_type:
            content_parts.append(f"type: {object_type}")
        if rcs_size:
            content_parts.append(f"RCS: {rcs_size}")
        if decay_date:
            content_parts.append(f"DECAYED: {decay_date}")

        content = " | ".join(content_parts)

        # Parse epoch to datetime
        published = None
        if epoch:
            try:
                published = datetime.fromisoformat(epoch.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                published = datetime.now(timezone.utc)

        return IngestedDocument(
            id=make_document_id("celestrak", f"{norad_id}:{epoch}"),
            source_feed="celestrak",
            source_category="space",
            source_credibility_tier=1,
            title=f"Satellite {name} orbital update",
            url=f"https://celestrak.org/satcat/table-satcat.php?CATNR={norad_id}",
            published=published,
            content=content,
            content_quality="full",
            metadata={
                "source_type": "satellite_tle",
                "norad_id": norad_id,
                "object_name": name,
                "intl_designator": intl_designator,
                "object_type": object_type,
                "group": group,
                "epoch": epoch,
                "inclination_deg": inclination,
                "eccentricity": eccentricity,
                "mean_motion_rev_day": mean_motion,
                "raan_deg": raan,
                "arg_pericenter_deg": arg_pericenter,
                "mean_anomaly_deg": mean_anomaly,
                "period_min": period_min,
                "apogee_km": apogee_km,
                "perigee_km": perigee_km,
                "rev_at_epoch": rev_at_epoch,
                "rcs_size": rcs_size,
                "decay_date": decay_date,
            },
        )

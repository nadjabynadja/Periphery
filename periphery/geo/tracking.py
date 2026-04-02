"""N2YO satellite tracking + Position API (ADS-B / AIS) proxy helpers."""

import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ── N2YO Configuration ───────────────────────────────────────────

N2YO_BASE = "https://api.n2yo.com/rest/v1/satellite/"
N2YO_API_KEY = os.getenv("N2YO_API_KEY", "")

# Position API (ADS-B + AIS) — runs as sibling Docker service
POSITION_API_URL = os.getenv("POSITION_API_URL", "http://localhost:5050")


# ── Rate Limiter (100 requests/hour per endpoint type) ────────────

class _RateLimiter:
    """Simple sliding-window rate limiter: max `limit` calls per `window` seconds."""

    def __init__(self, limit: int = 100, window: int = 3600):
        self.limit = limit
        self.window = window
        self._buckets: dict[str, list[float]] = {}

    def check(self, bucket: str) -> bool:
        now = time.time()
        timestamps = self._buckets.setdefault(bucket, [])
        # Prune expired entries
        cutoff = now - self.window
        self._buckets[bucket] = [t for t in timestamps if t > cutoff]
        return len(self._buckets[bucket]) < self.limit

    def record(self, bucket: str) -> None:
        self._buckets.setdefault(bucket, []).append(time.time())

    def remaining(self, bucket: str) -> int:
        now = time.time()
        cutoff = now - self.window
        timestamps = self._buckets.get(bucket, [])
        active = [t for t in timestamps if t > cutoff]
        return max(0, self.limit - len(active))


_rate = _RateLimiter(limit=100, window=3600)


# ── N2YO API Client ──────────────────────────────────────────────

async def _n2yo_get(endpoint: str, bucket: str) -> dict[str, Any]:
    """Make a rate-limited GET to N2YO API."""
    if not N2YO_API_KEY:
        raise ValueError("N2YO_API_KEY not configured")

    if not _rate.check(bucket):
        remaining = _rate.remaining(bucket)
        raise RuntimeError(
            f"N2YO rate limit exceeded for '{bucket}' — {remaining} remaining this hour"
        )

    url = f"{N2YO_BASE}{endpoint}&apiKey={N2YO_API_KEY}"
    _rate.record(bucket)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def get_satellites_above(
    lat: float,
    lng: float,
    radius: int = 70,
    category: int = 0,
) -> dict[str, Any]:
    """Get satellites above a location.

    Args:
        lat/lng: observer position
        radius: search radius in degrees (0-90, default 70)
        category: satellite category (0=all, 18=amateur, 52=ISS, etc.)
    """
    endpoint = f"above/{lat}/{lng}/0/{radius}/{category}/?"
    return await _n2yo_get(endpoint, "above")


async def get_satellite_positions(
    norad_id: int,
    lat: float,
    lng: float,
    seconds: int = 60,
) -> dict[str, Any]:
    """Get predicted positions for a satellite.

    Args:
        norad_id: NORAD catalog ID
        lat/lng: observer position
        seconds: prediction window (default 60s)
    """
    endpoint = f"positions/{norad_id}/{lat}/{lng}/0/{seconds}/?"
    return await _n2yo_get(endpoint, "positions")


async def get_tle(norad_id: int) -> dict[str, Any]:
    """Get TLE (Two-Line Element) data for a satellite."""
    endpoint = f"tle/{norad_id}/?"
    return await _n2yo_get(endpoint, "tle")


# ── Position API Proxies (ADS-B + AIS) ───────────────────────────

async def proxy_vessels_nearby(
    lat: float, lng: float, distance: float
) -> dict[str, Any]:
    """Proxy to position-api: get vessels near a location."""
    url = f"{POSITION_API_URL}/legacy/getVesselsNearMe"
    params = {"lat": lat, "lng": lng, "distance": distance}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def proxy_aircraft_location(icao: str) -> dict[str, Any]:
    """Proxy to position-api: get latest aircraft location by ICAO hex."""
    url = f"{POSITION_API_URL}/adsb/adsbe/{icao}/location/latest"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def proxy_vessel_location(mmsi: str) -> dict[str, Any]:
    """Proxy to position-api: get latest vessel location by MMSI."""
    url = f"{POSITION_API_URL}/ais/mt/{mmsi}/location/latest"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

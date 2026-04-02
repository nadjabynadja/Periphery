"""SkyFi satellite imagery integration.

API docs: https://docs.skyfi.com/
Handles archive search and image ordering.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SKYFI_API_KEY = os.environ.get("SKYFI_API_KEY", "")
SKYFI_BASE = "https://api.skyfi.com/v1"


async def search_skyfi_archive(
    aoi: list[list[float]],  # [[lng, lat], ...]
    start_date: str,
    end_date: str,
    max_cloud_cover: int = 20,
) -> list[dict]:
    """Search SkyFi archive for available satellite imagery over an AOI."""
    if not SKYFI_API_KEY:
        logger.warning("SKYFI_API_KEY not set — returning empty results")
        return []

    # Build GeoJSON polygon
    # Close the ring if not already closed
    ring = list(aoi)
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])

    payload = {
        "aoi": {
            "type": "Polygon",
            "coordinates": [ring],
        },
        "startDate": start_date,
        "endDate": end_date,
        "maxCloudCover": max_cloud_cover,
        "limit": 20,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{SKYFI_BASE}/archive/search",
                headers={
                    "Authorization": f"Bearer {SKYFI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if resp.status_code != 200:
                logger.error(f"SkyFi search failed: {resp.status_code} {resp.text[:500]}")
                return []

            data = resp.json()
            results = []

            for item in data.get("results", data.get("items", [])):
                results.append({
                    "id": item.get("id", ""),
                    "provider": item.get("provider", "Unknown"),
                    "satellite": item.get("satellite", item.get("source", "Unknown")),
                    "captureDate": item.get("captureDate", item.get("acquisitionDate", "")),
                    "resolution": item.get("resolution", item.get("gsd", 0)),
                    "cloudCover": item.get("cloudCover", 0),
                    "cost": item.get("price", item.get("cost", 0)),
                    "thumbnailUrl": item.get("thumbnailUrl", item.get("preview", "")),
                    "areaKm2": item.get("areaKm2", item.get("area", 0)),
                })

            return results

    except Exception as e:
        logger.error(f"SkyFi archive search failed: {e}")
        return []


async def order_skyfi_image(archive_id: str, budget: float) -> dict:
    """Order satellite imagery from SkyFi."""
    if not SKYFI_API_KEY:
        raise ValueError("SKYFI_API_KEY not configured")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{SKYFI_BASE}/orders",
                headers={
                    "Authorization": f"Bearer {SKYFI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "archiveId": archive_id,
                    "maxBudget": budget,
                },
            )

            if resp.status_code not in (200, 201):
                raise ValueError(f"SkyFi order failed: {resp.status_code} {resp.text[:500]}")

            data = resp.json()
            return {
                "orderId": data.get("orderId", data.get("id", "")),
                "status": data.get("status", "submitted"),
                "estimatedCost": data.get("estimatedCost", 0),
            }

    except httpx.HTTPError as e:
        raise ValueError(f"SkyFi API error: {e}")

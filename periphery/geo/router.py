"""Geospatial API router — property records, CCTV, satellite imagery, deep search."""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/geo", tags=["geo"])

# ── Property Records ──────────────────────────────────────────────

class PropertyRecordResponse(BaseModel):
    address: str
    owners: list[str]
    voters: list[dict]
    donors: list[dict]
    businesses: list[dict]
    assessedValue: Optional[float] = None
    parcelId: Optional[str] = None


@router.get("/property-records")
async def get_property_records(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    address: Optional[str] = Query(None, max_length=500),
):
    """Look up property records, voters, donors, and businesses at a location."""
    from periphery.geo.records import lookup_property_records

    try:
        result = await lookup_property_records(lat, lng, address)
        return result
    except Exception as e:
        logger.error(f"Property records lookup failed: {e}")
        return PropertyRecordResponse(
            address=address or f"{lat}, {lng}",
            owners=[],
            voters=[],
            donors=[],
            businesses=[],
        )


# ── Deep Search ───────────────────────────────────────────────────

class DeepSearchRequest(BaseModel):
    person: str
    address: str


class DeepSearchResponse(BaseModel):
    results: list[str]
    loading: bool = False


@router.post("/deep-search")
async def deep_search(req: DeepSearchRequest):
    """Programmatic algorithmic search on a person using public records + web."""
    from periphery.geo.deep_search import run_deep_search

    try:
        results = await run_deep_search(req.person, req.address)
        return DeepSearchResponse(results=results)
    except Exception as e:
        logger.error(f"Deep search failed: {e}")
        return DeepSearchResponse(results=[f"Search error: {str(e)}"])


# ── CCTV Nearby ───────────────────────────────────────────────────

class CCTVFeedItem(BaseModel):
    id: str
    name: str
    url: str
    location: dict  # {lat, lng}
    source: str
    status: str = "unknown"


class CCTVNearbyResponse(BaseModel):
    feeds: list[CCTVFeedItem]


@router.get("/cctv/nearby")
async def cctv_nearby(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius: int = Query(2000, ge=100, le=10000, description="Search radius in meters (100-10000)"),
):
    """Find public CCTV cameras near a location."""
    from periphery.geo.cctv import find_nearby_cameras

    try:
        feeds = await find_nearby_cameras(lat, lng, radius)
        return CCTVNearbyResponse(feeds=feeds)
    except Exception as e:
        logger.error(f"CCTV search failed: {e}")
        return CCTVNearbyResponse(feeds=[])


# ── Satellite Imagery (SkyFi) ─────────────────────────────────────

class SatelliteSearchRequest(BaseModel):
    aoi: list[list[float]]  # [[lng, lat], ...] GeoJSON ring, max 64 vertices
    start_date: str
    end_date: str
    max_cloud_cover: int = 20

    def model_post_init(self, __context: object) -> None:
        if len(self.aoi) < 3:
            raise ValueError("AOI polygon requires at least 3 vertices")
        if len(self.aoi) > 64:
            raise ValueError("AOI polygon limited to 64 vertices")


class SatelliteOrderRequest(BaseModel):
    archive_id: str
    budget: float = 50.0


@router.post("/satellite/search")
async def satellite_search(req: SatelliteSearchRequest):
    """Search SkyFi archive for available satellite imagery."""
    from periphery.geo.satellite import search_skyfi_archive

    try:
        results = await search_skyfi_archive(
            req.aoi, req.start_date, req.end_date, req.max_cloud_cover
        )
        return {"results": results}
    except Exception as e:
        logger.error(f"Satellite search failed: {e}")
        return {"results": [], "error": str(e)}


@router.post("/satellite/order")
async def satellite_order(req: SatelliteOrderRequest):
    """Order satellite imagery from SkyFi."""
    from periphery.geo.satellite import order_skyfi_image

    try:
        result = await order_skyfi_image(req.archive_id, req.budget)
        return result
    except Exception as e:
        logger.error(f"Satellite order failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

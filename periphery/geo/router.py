"""Geospatial API router — property records, CCTV, satellite imagery, deep search."""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, HTTPException, UploadFile
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


# ── CCTV Frame Detection ─────────────────────────────────────────

class CCTVDetectionResult(BaseModel):
    objects: list[dict]
    license_plates: list[dict]
    face_matches: list[dict]
    hate_symbols: list[dict]


@router.post("/cctv/detect")
async def cctv_detect(image: UploadFile = File(...)):
    """Run all detection models on a CCTV frame.

    Accepts a multipart form upload with an image file (JPEG/PNG).
    Runs object detection, license plate detection, face matching
    against watchlists, and hate symbol detection.
    """
    from periphery.geo.detection import (
        detect_faces_against_watchlist,
        detect_hate_symbols,
        detect_license_plates,
        detect_objects,
    )

    try:
        frame_bytes = await image.read()
    except Exception as e:
        logger.error(f"Failed to read uploaded image: {e}")
        raise HTTPException(status_code=400, detail="Invalid image upload")

    if not frame_bytes:
        raise HTTPException(status_code=400, detail="Empty image file")

    watchlist_dir = os.environ.get("WATCHLIST_DIR", "/app/data/watchlists")

    objects = detect_objects(frame_bytes)
    plates = detect_license_plates(frame_bytes)
    faces = detect_faces_against_watchlist(frame_bytes, watchlist_dir)
    symbols = detect_hate_symbols(frame_bytes)

    return CCTVDetectionResult(
        objects=objects,
        license_plates=plates,
        face_matches=faces,
        hate_symbols=symbols,
    )


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


# ── Tracking (ADS-B / AIS / Satellite) ────────────────────────────

@router.get("/tracking/vessels-nearby")
async def tracking_vessels_nearby(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    distance: float = Query(10.0, ge=0.1, le=500, description="Search distance in nautical miles"),
):
    """Find vessels near a location via AIS (position-api proxy)."""
    from periphery.geo.tracking import proxy_vessels_nearby

    try:
        result = await proxy_vessels_nearby(lat, lng, distance)
        return result
    except Exception as e:
        logger.error(f"Vessel tracking failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/tracking/aircraft")
async def tracking_aircraft(
    icao: str = Query(..., min_length=4, max_length=8, description="ICAO hex code"),
):
    """Get latest aircraft position by ICAO hex code (ADS-B proxy)."""
    from periphery.geo.tracking import proxy_aircraft_location

    try:
        result = await proxy_aircraft_location(icao)
        return result
    except Exception as e:
        logger.error(f"Aircraft tracking failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/tracking/vessel")
async def tracking_vessel(
    mmsi: str = Query(..., min_length=5, max_length=15, description="MMSI number"),
):
    """Get latest vessel position by MMSI (AIS proxy)."""
    from periphery.geo.tracking import proxy_vessel_location

    try:
        result = await proxy_vessel_location(mmsi)
        return result
    except Exception as e:
        logger.error(f"Vessel MMSI tracking failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/tracking/satellites-above")
async def tracking_satellites_above(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius: int = Query(70, ge=0, le=90, description="Search radius in degrees"),
    category: int = Query(0, ge=0, description="Satellite category (0=all)"),
):
    """Get satellites currently above a location via N2YO."""
    from periphery.geo.tracking import get_satellites_above

    try:
        result = await get_satellites_above(lat, lng, radius, category)
        return result
    except Exception as e:
        logger.error(f"Satellite tracking failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/tracking/satellite-positions")
async def tracking_satellite_positions(
    norad_id: int = Query(..., description="NORAD catalog ID"),
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    seconds: int = Query(60, ge=1, le=300, description="Prediction window in seconds"),
):
    """Get predicted satellite positions via N2YO."""
    from periphery.geo.tracking import get_satellite_positions

    try:
        result = await get_satellite_positions(norad_id, lat, lng, seconds)
        return result
    except Exception as e:
        logger.error(f"Satellite position prediction failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


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

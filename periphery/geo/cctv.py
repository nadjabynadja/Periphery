"""CCTV camera discovery — find public cameras near a location.

Sources:
  - Insecam.org index (public unsecured cameras)
  - DOT traffic cameras (state-specific)
  - OpenStreetMap surveillance=* tags
"""

import logging
import math
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def query_osm_cameras(lat: float, lng: float, radius_m: int) -> list[dict]:
    """Query OpenStreetMap Overpass API for surveillance cameras."""
    radius_str = str(radius_m)
    query = f"""
    [out:json][timeout:10];
    (
      node["man_made"="surveillance"](around:{radius_str},{lat},{lng});
      node["surveillance"](around:{radius_str},{lat},{lng});
    );
    out body;
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            cameras = []
            for elem in data.get("elements", []):
                tags = elem.get("tags", {})
                cam = {
                    "id": f"osm-{elem['id']}",
                    "name": tags.get("name", tags.get("description", f"Camera {elem['id']}")),
                    "url": tags.get("contact:webcam", tags.get("url", "")),
                    "location": {"lat": elem["lat"], "lng": elem["lon"]},
                    "source": "OpenStreetMap",
                    "status": "unknown",
                }
                cameras.append(cam)
            return cameras
    except Exception as e:
        logger.debug(f"OSM camera query failed: {e}")
        return []


async def query_dot_cameras(lat: float, lng: float, radius_m: int) -> list[dict]:
    """Query DOT traffic camera feeds (NC specific for now)."""
    # NC DOT traffic cameras API
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://tims.ncdot.gov/TIMS/api/traffic/cameras",
                params={"format": "json"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            cameras = []
            for cam in data if isinstance(data, list) else data.get("cameras", []):
                cam_lat = float(cam.get("latitude", 0))
                cam_lng = float(cam.get("longitude", 0))
                dist = haversine_km(lat, lng, cam_lat, cam_lng) * 1000  # to meters
                if dist <= radius_m:
                    cameras.append({
                        "id": f"ncdot-{cam.get('id', '')}",
                        "name": cam.get("location", cam.get("name", "NC DOT Camera")),
                        "url": cam.get("imageUrl", cam.get("videoUrl", "")),
                        "location": {"lat": cam_lat, "lng": cam_lng},
                        "source": "NC DOT",
                        "status": "live" if cam.get("imageUrl") else "unknown",
                    })
            return cameras
    except Exception as e:
        logger.debug(f"DOT camera query failed: {e}")
        return []


async def find_nearby_cameras(lat: float, lng: float, radius_m: int = 2000) -> list[dict]:
    """Find all public cameras near a location."""
    cameras = []

    # Query multiple sources
    osm_cams = await query_osm_cameras(lat, lng, radius_m)
    cameras.extend(osm_cams)

    dot_cams = await query_dot_cameras(lat, lng, radius_m)
    cameras.extend(dot_cams)

    # Sort by distance
    cameras.sort(
        key=lambda c: haversine_km(
            lat, lng,
            c["location"]["lat"], c["location"]["lng"]
        )
    )

    return cameras[:50]  # Cap at 50

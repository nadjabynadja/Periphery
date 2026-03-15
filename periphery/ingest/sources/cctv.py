"""Public CCTV camera feed aggregation.

Monitors public traffic and weather camera feeds from government DOT
APIs and other open sources. Captures camera metadata and availability
status rather than video frames — frame analysis would require a
separate CV pipeline.

Supported sources:
- US DOT 511 camera feeds (JSON APIs)
- Public camera listing endpoints
- Custom camera registries
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

import aiohttp

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id


class CCTVSource(DataSource):
    """Monitors public CCTV camera feeds for availability and metadata."""

    name = "cctv"
    category = "surveillance"
    default_poll_interval = 300  # 5 minutes

    def __init__(
        self,
        *,
        camera_feeds: list[dict] | None = None,
        dot_endpoints: list[str] | None = None,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            camera_feeds: Static list of cameras, each a dict with
                keys: id, name, url, lat, lon, type, region.
            dot_endpoints: URLs to DOT/511 JSON camera listing APIs.
        """
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._camera_feeds: list[dict] = camera_feeds or []
        self._dot_endpoints: list[str] = dot_endpoints or []
        self._last_status: dict[str, str] = {}  # cam_id → status hash

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        docs: list[IngestedDocument] = []
        now = datetime.now(timezone.utc)

        # Poll DOT/511 endpoints for camera listings
        for endpoint_url in self._dot_endpoints:
            endpoint_docs = await self._fetch_dot_endpoint(
                session, endpoint_url, now
            )
            docs.extend(endpoint_docs)

        # Check static camera list for availability changes
        for cam in self._camera_feeds:
            doc = await self._check_camera(session, cam, now)
            if doc:
                docs.append(doc)

        return docs

    async def _fetch_dot_endpoint(
        self,
        session: aiohttp.ClientSession,
        endpoint_url: str,
        now: datetime,
    ) -> list[IngestedDocument]:
        try:
            async with session.get(
                endpoint_url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return []

        cameras = []
        if isinstance(data, list):
            cameras = data
        elif isinstance(data, dict):
            # Common DOT API patterns
            cameras = (
                data.get("cameras", [])
                or data.get("features", [])
                or data.get("data", [])
            )

        docs = []
        for cam_data in cameras:
            doc = self._make_camera_doc(cam_data, endpoint_url, now)
            if doc:
                docs.append(doc)
        return docs

    async def _check_camera(
        self,
        session: aiohttp.ClientSession,
        cam: dict,
        now: datetime,
    ) -> IngestedDocument | None:
        url = cam.get("url", "")
        cam_id = cam.get("id", url)
        if not url:
            return None

        # Just check if the stream/image endpoint is reachable
        status = "unknown"
        try:
            async with session.head(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=True,
            ) as resp:
                status = "online" if resp.status < 400 else f"error_{resp.status}"
        except aiohttp.ClientError:
            status = "offline"
        except Exception:
            status = "error"

        # Skip if status hasn't changed
        status_hash = hashlib.md5(status.encode()).hexdigest()[:8]
        if self._last_status.get(cam_id) == status_hash:
            return None
        self._last_status[cam_id] = status_hash

        name = cam.get("name", f"Camera {cam_id}")
        lat = cam.get("lat")
        lon = cam.get("lon")
        cam_type = cam.get("type", "unknown")
        region = cam.get("region", "")

        content_parts = [
            f"CCTV camera {name} status: {status}",
            f"type={cam_type}",
        ]
        if lat is not None and lon is not None:
            content_parts.append(f"at ({lat:.5f}, {lon:.5f})")
        if region:
            content_parts.append(f"region: {region}")

        content = " | ".join(content_parts)

        return IngestedDocument(
            id=make_document_id("cctv", f"{cam_id}:{status_hash}:{int(now.timestamp())}"),
            source_feed="cctv-public",
            source_category="surveillance",
            source_credibility_tier=3,
            title=f"Camera {name}: {status}",
            url=url,
            published=now,
            content=content,
            content_quality="metadata_only",
            metadata={
                "source_type": "cctv_camera",
                "camera_id": cam_id,
                "camera_name": name,
                "camera_type": cam_type,
                "status": status,
                "latitude": lat,
                "longitude": lon,
                "region": region,
                "stream_url": url,
            },
        )

    def _make_camera_doc(
        self,
        cam_data: dict,
        endpoint_url: str,
        now: datetime,
    ) -> IngestedDocument | None:
        """Convert a DOT API camera record to an IngestedDocument."""
        # Handle GeoJSON features
        if "geometry" in cam_data and "properties" in cam_data:
            props = cam_data["properties"]
            geom = cam_data["geometry"]
            coords = geom.get("coordinates", [])
            cam_data = {
                **props,
                "lon": coords[0] if len(coords) > 0 else None,
                "lat": coords[1] if len(coords) > 1 else None,
            }

        cam_id = str(
            cam_data.get("id")
            or cam_data.get("cameraId")
            or cam_data.get("camera_id")
            or ""
        )
        if not cam_id:
            return None

        name = (
            cam_data.get("name")
            or cam_data.get("cameraName")
            or cam_data.get("description")
            or f"DOT Camera {cam_id}"
        )
        lat = cam_data.get("latitude") or cam_data.get("lat")
        lon = cam_data.get("longitude") or cam_data.get("lon") or cam_data.get("lng")
        image_url = cam_data.get("imageUrl") or cam_data.get("url") or cam_data.get("image")
        direction = cam_data.get("direction", "")
        road = cam_data.get("roadway") or cam_data.get("road") or cam_data.get("route", "")

        # Build status hash to track changes
        status_str = f"{lat}:{lon}:{image_url}:{cam_data.get('status', '')}"
        status_hash = hashlib.md5(status_str.encode()).hexdigest()[:8]

        if self._last_status.get(cam_id) == status_hash:
            return None
        self._last_status[cam_id] = status_hash

        content_parts = [f"DOT camera {name}"]
        if lat is not None and lon is not None:
            content_parts.append(f"at ({lat:.5f}, {lon:.5f})")
        if road:
            content_parts.append(f"road: {road}")
        if direction:
            content_parts.append(f"direction: {direction}")

        content = " | ".join(content_parts)

        return IngestedDocument(
            id=make_document_id("cctv-dot", f"{cam_id}:{status_hash}"),
            source_feed="cctv-dot",
            source_category="surveillance",
            source_credibility_tier=3,
            title=f"DOT Camera: {name}",
            url=image_url or endpoint_url,
            published=now,
            content=content,
            content_quality="metadata_only",
            metadata={
                "source_type": "cctv_camera",
                "camera_id": cam_id,
                "camera_name": name,
                "camera_type": "dot_traffic",
                "latitude": lat,
                "longitude": lon,
                "image_url": image_url,
                "direction": direction,
                "road": road,
                "endpoint": endpoint_url,
            },
        )

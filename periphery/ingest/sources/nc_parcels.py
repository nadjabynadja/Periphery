"""NC Property Records (Parcels) data source.

Queries the NC OneMap Statewide Parcels ArcGIS FeatureServer REST API
to fetch parcel/property records for all 100 NC counties.

Data source:
  - NC OneMap Statewide Parcels FeatureServer
  - ~5.9M parcels, paginated via resultOffset/resultRecordCount
  - Public data, no authentication required

PUBLIC DATA NOTICE
------------------
NC property records are public data maintained by county tax offices
and aggregated by NC OneMap. Contains owner names, addresses, and
property values (PII).
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

FEATURESERVER_BASE_URL = (
    "https://services.nconemap.gov/secure/rest/services/"
    "NC1Map_Parcels/FeatureServer/0"
)
PARCELS_DATASET_URL = (
    "https://www.nconemap.gov/datasets/NC-GICC::statewide-parcels"
)


def _safe_str(val: Any) -> str:
    """Return stripped string or empty string for None/null values."""
    if val is None:
        return ""
    return str(val).strip()


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Return float or default for None/null values."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _build_parcel_content(attrs: dict[str, Any]) -> str:
    """Build structured text content for a parcel document."""
    siteadd = _safe_str(attrs.get("siteadd"))
    scity = _safe_str(attrs.get("scity"))
    ownname = _safe_str(attrs.get("ownname"))
    owntype = _safe_str(attrs.get("owntype"))
    parno = _safe_str(attrs.get("parno"))
    cntyname = _safe_str(attrs.get("cntyname"))
    parval = _safe_float(attrs.get("parval"))
    landval = _safe_float(attrs.get("landval"))
    improvval = _safe_float(attrs.get("improvval"))
    gisacres = _safe_float(attrs.get("gisacres"))
    struct = _safe_str(attrs.get("struct"))
    parusedesc = _safe_str(attrs.get("parusedesc"))
    parusecode = _safe_str(attrs.get("parusecode"))
    saledatetx = _safe_str(attrs.get("saledatetx"))
    legdecfull = _safe_str(attrs.get("legdecfull"))
    mailadd = _safe_str(attrs.get("mailadd"))
    mcity = _safe_str(attrs.get("mcity"))
    mstate = _safe_str(attrs.get("mstate"))
    mzip = _safe_str(attrs.get("mzip"))

    return (
        f"Property: {siteadd}, {scity}\n"
        f"Owner: {ownname} | Type: {owntype}\n"
        f"Parcel: {parno} | County: {cntyname}\n"
        f"Value: ${parval:,.0f} (Land: ${landval:,.0f}, Improved: ${improvval:,.0f})\n"
        f"Acres: {gisacres:.2f} | Has Structure: {struct}\n"
        f"Use: {parusedesc} ({parusecode})\n"
        f"Last Sale: {saledatetx}\n"
        f"Legal: {legdecfull}\n"
        f"Mailing: {mailadd}, {mcity}, {mstate} {mzip}"
    )


def _build_parcel_title(attrs: dict[str, Any]) -> str:
    """Build title for a parcel document."""
    ownname = _safe_str(attrs.get("ownname")) or "Unknown Owner"
    siteadd = _safe_str(attrs.get("siteadd")) or "No Address"
    scity = _safe_str(attrs.get("scity")) or "Unknown City"
    parval = _safe_float(attrs.get("parval"))
    return f"{ownname} — {siteadd}, {scity} — ${parval:,.0f}"


class NCParcelsSource(DataSource):
    """Queries NC OneMap Statewide Parcels FeatureServer.

    Paginates through the ArcGIS REST API, converting each parcel
    feature into an IngestedDocument. Emits documents in configurable
    batches via self._emit().
    """

    name = "nc_parcels"
    category = "property_records"
    default_poll_interval = 604800  # weekly

    def __init__(
        self,
        *,
        page_size: int = 2000,
        batch_size: int = 10000,
        query_delay: float = 2.0,
        county: str | None = None,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._page_size = page_size
        self._batch_size = batch_size
        self._query_delay = query_delay
        self._county = county

    def _build_where_clause(self) -> str:
        """Build the WHERE clause for the FeatureServer query."""
        if self._county:
            # ArcGIS SQL uses single quotes for string literals
            safe_county = self._county.replace("'", "''")
            return f"UPPER(cntyname)='{safe_county.upper()}'"
        return "1=1"

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Page through FeatureServer and emit parcel documents in batches."""
        logger.info("nc_parcels_fetch_start", county=self._county)

        batch: list[IngestedDocument] = []
        total_emitted = 0
        offset = 0
        where = self._build_where_clause()

        while True:
            params = {
                "where": where,
                "outFields": "*",
                "outSR": "4326",
                "resultOffset": str(offset),
                "resultRecordCount": str(self._page_size),
                "f": "json",
            }

            url = f"{FEATURESERVER_BASE_URL}/query"
            try:
                async with session.get(url, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            except Exception as exc:
                logger.error(
                    "nc_parcels_api_error",
                    offset=offset,
                    error=str(exc),
                )
                break

            features = data.get("features", [])
            if not features:
                break

            for feature in features:
                attrs = feature.get("attributes", {})
                geometry = feature.get("geometry")

                objectid = attrs.get("objectid")
                if objectid is None:
                    objectid = attrs.get("OBJECTID")
                if objectid is None:
                    continue

                # Extract coordinates from geometry (WGS84 via outSR=4326)
                latitude = None
                longitude = None
                if geometry:
                    longitude = geometry.get("x")
                    latitude = geometry.get("y")

                # Build metadata with all raw fields
                metadata: dict[str, Any] = {}
                for key, val in attrs.items():
                    metadata[key] = val
                metadata["source_type"] = "nc_parcels"
                if latitude is not None:
                    metadata["latitude"] = latitude
                if longitude is not None:
                    metadata["longitude"] = longitude

                doc = IngestedDocument(
                    id=make_document_id("nc_parcels", str(objectid)),
                    source_feed="NC Property Records",
                    source_category="property_records",
                    source_credibility_tier=1,
                    title=_build_parcel_title(attrs),
                    url=PARCELS_DATASET_URL,
                    content=_build_parcel_content(attrs),
                    content_quality="full",
                    data_classification="PII",
                    metadata=metadata,
                )
                batch.append(doc)

                if len(batch) >= self._batch_size:
                    await self._emit(batch)
                    total_emitted += len(batch)
                    logger.info(
                        "nc_parcels_progress",
                        emitted=total_emitted,
                        offset=offset,
                    )
                    batch = []

            offset += len(features)

            # Check if there are more results
            exceeded = data.get("exceededTransferLimit", False)
            if not exceeded:
                break

            # Rate limit between API pages
            await asyncio.sleep(self._query_delay)

        # Emit remaining batch
        if batch:
            await self._emit(batch)
            total_emitted += len(batch)

        logger.info("nc_parcels_fetch_complete", total_docs=total_emitted)
        return []  # already emitted via _emit

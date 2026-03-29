"""NC Register of Deeds data source (stub — Wake County first).

Designed to ingest public property transfer records from county Register
of Deeds portals. Initial implementation targets Wake County
(rodpub.wakegov.com).

**STATUS: STUB** — The Wake County ROD portal uses a vendor system
(likely Kofile/CSC) that requires further analysis to reverse-engineer.
This module defines the full source structure and document model, with a
placeholder fetch() that logs a not-yet-implemented message.

Data source: https://rodpub.wakegov.com/
Public property records: grantor, grantee, date, document type, book/page.

PII DATA NOTICE
---------------
Register of Deeds records contain personal information (names, addresses,
property descriptions). Data classification is set to PII.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

# County portal URLs (extensible)
COUNTY_PORTALS: dict[str, str] = {
    "WAKE": "https://rodpub.wakegov.com/",
}

# Rate limiting
DEFAULT_REQUEST_DELAY = 3.0  # seconds between requests


def build_rod_document(
    *,
    county: str,
    doc_type: str,
    record_date: str,
    book: str,
    page: str,
    grantor: str,
    grantee: str,
    legal_description: str = "",
    consideration: str = "",
    document_id: str = "",
) -> IngestedDocument:
    """Build an IngestedDocument from a Register of Deeds record.

    This is a standalone function so it can be used by the stub and
    eventually by the real scraper, as well as in tests.
    """
    # Build unique key
    if book and page:
        unique_key = f"{county}:{book}:{page}"
    elif document_id:
        unique_key = f"{county}:{document_id}"
    else:
        unique_key = f"{county}:{doc_type}:{grantor}:{grantee}:{record_date}"

    content = _build_rod_content(
        doc_type=doc_type,
        record_date=record_date,
        book=book,
        page=page,
        grantor=grantor,
        grantee=grantee,
        legal_description=legal_description,
        consideration=consideration,
        county=county,
    )

    consideration_display = f"${consideration}" if consideration else ""

    metadata: dict[str, Any] = {
        "source_type": "nc_rod",
        "county": county,
        "doc_type": doc_type,
        "record_date": record_date,
        "book": book,
        "page": page,
        "grantor": grantor,
        "grantee": grantee,
        "legal_description": legal_description,
        "consideration": consideration,
    }
    if document_id:
        metadata["document_id"] = document_id

    return IngestedDocument(
        id=make_document_id("nc_rod", unique_key),
        source_feed=f"NC Register of Deeds — {county.title()}",
        source_category="property_records",
        source_credibility_tier=1,
        title=f"{doc_type}: {grantor} → {grantee} ({record_date})",
        url=COUNTY_PORTALS.get(county.upper(), ""),
        content=content,
        content_quality="full",
        data_classification="PII",
        metadata=metadata,
    )


def _build_rod_content(
    *,
    doc_type: str,
    record_date: str,
    book: str,
    page: str,
    grantor: str,
    grantee: str,
    legal_description: str,
    consideration: str,
    county: str,
) -> str:
    """Build structured text content for a ROD record."""
    lines = [
        f"Document Type: {doc_type}",
        f"Recorded: {record_date} | Book: {book} Page: {page}",
        f"Grantor: {grantor}",
        f"Grantee: {grantee}",
    ]
    if legal_description:
        lines.append(f"Property: {legal_description}")
    if consideration:
        lines.append(f"Consideration: ${consideration}")
    lines.append(f"County: {county.title()}")
    return "\n".join(lines)


class NCRegisterOfDeedsSource(DataSource):
    """NC Register of Deeds data source (stub).

    Designed to ingest property transfer records from county ROD portals.
    Currently implements Wake County only, as a stub pending portal
    analysis.

    When activated, fetch() logs a not-yet-implemented message for each
    configured county. The document model and builder functions are fully
    implemented and tested — only the web scraping logic remains to be
    added.

    **Experimental/stub** — do not enable in production until the portal
    scraping is implemented.
    """

    name = "nc_rod"
    category = "property_records"
    default_poll_interval = 604800  # weekly

    def __init__(
        self,
        *,
        counties: list[str] | None = None,
        request_delay: float = DEFAULT_REQUEST_DELAY,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._counties = [c.upper() for c in (counties or ["WAKE"])]
        self._request_delay = request_delay

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Stub fetch — logs not-yet-implemented for each county.

        Once the Wake County portal API is reverse-engineered, this
        method will query by date range and parse results.
        """
        for county in self._counties:
            portal_url = COUNTY_PORTALS.get(county, "unknown")
            logger.warning(
                "nc_rod_not_implemented",
                county=county,
                portal=portal_url,
                message=f"ROD scraping not yet implemented for {county}",
            )

        return []

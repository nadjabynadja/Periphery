"""IRS Exempt Organizations (NC) data source.

Downloads and parses the IRS Business Master File (BMF) CSV for North
Carolina exempt organizations.

Data source: https://www.irs.gov/pub/irs-soi/eo_nc.csv
Updated quarterly by the IRS. Free, no authentication required.

PUBLIC DATA NOTICE
------------------
IRS Exempt Organization data is public record, published by the IRS
Statistics of Income division, and freely available for bulk download.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

# IRS BMF CSV for NC exempt organizations
IRS_EO_NC_URL = "https://www.irs.gov/pub/irs-soi/eo_nc.csv"

# Batch size for emitting documents
BATCH_SIZE = 5000

# NTEE major category descriptions (first letter of NTEE code)
NTEE_MAJOR_CATEGORIES: dict[str, str] = {
    "A": "Arts, Culture & Humanities",
    "B": "Education",
    "C": "Environment",
    "D": "Animal-Related",
    "E": "Health Care",
    "F": "Mental Health & Crisis Intervention",
    "G": "Diseases, Disorders & Medical Disciplines",
    "H": "Medical Research",
    "I": "Crime & Legal-Related",
    "J": "Employment",
    "K": "Food, Agriculture & Nutrition",
    "L": "Housing & Shelter",
    "M": "Public Safety, Disaster Preparedness & Relief",
    "N": "Recreation & Sports",
    "O": "Youth Development",
    "P": "Human Services",
    "Q": "International, Foreign Affairs & National Security",
    "R": "Civil Rights, Social Action & Advocacy",
    "S": "Community Improvement & Capacity Building",
    "T": "Philanthropy, Voluntarism & Grantmaking Foundations",
    "U": "Science & Technology",
    "V": "Social Science",
    "W": "Public & Societal Benefit",
    "X": "Religion-Related",
    "Y": "Mutual & Membership Benefit",
    "Z": "Unknown/Unclassified",
}

# Foundation code descriptions
FOUNDATION_CODES: dict[str, str] = {
    "00": "All organizations except 501(c)(3)",
    "02": "Private operating foundation exempt from paying excise taxes on investment income",
    "03": "Private operating foundation (other)",
    "04": "Private non-operating foundation",
    "09": "Suspense",
    "10": "Church 170(b)(1)(A)(i)",
    "11": "School 170(b)(1)(A)(ii)",
    "12": "Hospital or medical research organization 170(b)(1)(A)(iii)",
    "13": "Organization which operates for benefit of college or university",
    "14": "Governmental unit",
    "15": "Organization which receives a substantial part of its support from a governmental unit or the general public",
    "16": "Organization that normally receives no more than one-third of its support from gross investment income",
    "17": "Organizations tested for public safety",
    "18": "Organization organized and operated to test for public safety",
    "21": "509(a)(3) Type I",
    "22": "509(a)(3) Type II",
    "23": "509(a)(3) Type III functionally integrated",
    "24": "509(a)(3) Type III non-functionally integrated",
}

# Organization status codes
STATUS_CODES: dict[str, str] = {
    "01": "Unconditional Exemption",
    "02": "Conditional Exemption",
    "12": "Trust described in section 4947(a)(2) of the IR Code",
    "25": "Organization terminated (merger/dissolution/etc.)",
}


def _ntee_description(code: str) -> str:
    """Return a human-readable description for an NTEE code."""
    if not code:
        return ""
    major = code[0].upper()
    desc = NTEE_MAJOR_CATEGORIES.get(major, "")
    return f"{desc} ({code})" if desc else code


def _safe_int(value: str) -> int:
    """Parse an integer from a string, returning 0 on failure."""
    try:
        return int(value.strip()) if value and value.strip() else 0
    except (ValueError, TypeError):
        return 0


def _format_currency(value: str) -> str:
    """Format a numeric string as currency."""
    amt = _safe_int(value)
    if amt == 0 and (not value or not value.strip()):
        return "N/A"
    return f"${amt:,}"


def _build_org_content(row: dict[str, str]) -> str:
    """Build structured text content for an exempt organization document."""
    name = row.get("NAME", "").strip()
    ein = row.get("EIN", "").strip()
    ico = row.get("ICO", "").strip()
    street = row.get("STREET", "").strip()
    city = row.get("CITY", "").strip()
    zip_code = row.get("ZIP", "").strip()
    subsection = row.get("SUBSECTION", "").strip()
    foundation = row.get("FOUNDATION", "").strip()
    ruling = row.get("RULING", "").strip()
    status = row.get("STATUS", "").strip()
    asset_amt = row.get("ASSET_AMT", "").strip()
    income_amt = row.get("INCOME_AMT", "").strip()
    revenue_amt = row.get("REVENUE_AMT", "").strip()
    ntee_cd = row.get("NTEE_CD", "").strip()
    activity = row.get("ACTIVITY", "").strip()
    tax_period = row.get("TAX_PERIOD", "").strip()

    foundation_desc = FOUNDATION_CODES.get(foundation, "")
    status_desc = STATUS_CODES.get(status, "")

    lines = [
        f"Organization: {name}",
        f"EIN: {ein} | Care Of: {ico}" if ico else f"EIN: {ein}",
        f"Address: {street}, {city}, NC {zip_code}",
        f"Subsection: 501(c)({subsection}) | Foundation: {foundation}" + (f" ({foundation_desc})" if foundation_desc else ""),
        f"Ruling Date: {ruling} | Status: {status}" + (f" ({status_desc})" if status_desc else ""),
        f"Assets: {_format_currency(asset_amt)} | Income: {_format_currency(income_amt)} | Revenue: {_format_currency(revenue_amt)}",
        f"NTEE Code: {ntee_cd}" + (f" — {_ntee_description(ntee_cd)}" if ntee_cd else "") + f" | Activity: {activity}",
        f"Filing Period: {tax_period}",
    ]
    return "\n".join(lines)


class IRSExemptOrgsSource(DataSource):
    """Polls IRS Exempt Organizations BMF data for North Carolina.

    Downloads eo_nc.csv from the IRS Statistics of Income site, parses
    the CSV, and emits IngestedDocument batches. The IRS pre-filters
    this file to NC organizations.

    Data is updated quarterly by the IRS. Each record represents a
    tax-exempt organization registered with the IRS.
    """

    name = "irs_exempt_orgs"
    category = "business_nonprofit"
    default_poll_interval = 7776000  # ~quarterly (90 days)

    def __init__(
        self,
        *,
        csv_url: str = IRS_EO_NC_URL,
        batch_size: int = BATCH_SIZE,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._csv_url = csv_url
        self._batch_size = batch_size

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Download and parse IRS exempt organizations CSV for NC."""
        logger.info("irs_exempt_orgs_fetch_start", url=self._csv_url)

        timeout = aiohttp.ClientTimeout(total=120, connect=30)
        async with session.get(self._csv_url, timeout=timeout) as resp:
            resp.raise_for_status()
            csv_text = await resp.text(encoding="utf-8")

        docs = self._parse_csv(csv_text)
        total = len(docs)

        # Emit in batches
        for i in range(0, total, self._batch_size):
            batch = docs[i : i + self._batch_size]
            await self._emit(batch)
            logger.info(
                "irs_exempt_orgs_batch",
                emitted=min(i + self._batch_size, total),
                total=total,
            )

        logger.info("irs_exempt_orgs_fetch_complete", total_docs=total)
        return []  # already emitted via _emit

    def _parse_csv(self, csv_text: str) -> list[IngestedDocument]:
        """Parse CSV text into IngestedDocument list."""
        docs: list[IngestedDocument] = []
        seen_eins: set[str] = set()

        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            ein = (row.get("EIN") or "").strip()
            if not ein:
                continue

            # Deduplicate by EIN
            if ein in seen_eins:
                continue
            seen_eins.add(ein)

            doc = self._build_document(row)
            if doc is not None:
                docs.append(doc)

        return docs

    def _build_document(self, row: dict[str, str]) -> IngestedDocument | None:
        """Build an IngestedDocument from a CSV row."""
        ein = (row.get("EIN") or "").strip()
        name = (row.get("NAME") or "").strip()
        city = (row.get("CITY") or "").strip()
        ntee_cd = (row.get("NTEE_CD") or "").strip()

        if not ein or not name:
            return None

        content = _build_org_content(row)
        encoded_name = quote_plus(name)

        # Build metadata with all CSV fields
        metadata: dict[str, Any] = {}
        for key in row:
            val = row[key]
            metadata[key.lower()] = val.strip() if isinstance(val, str) else str(val)
        metadata["source_type"] = "irs_exempt_orgs"
        metadata["ntee_description"] = _ntee_description(ntee_cd)

        return IngestedDocument(
            id=make_document_id("irs_exempt", ein),
            source_feed="IRS Exempt Organizations",
            source_category="business_nonprofit",
            source_credibility_tier=1,
            title=f"{name} — EIN {ein} — {city}, NC",
            url=f"https://apps.irs.gov/app/eos/detailsPage?ein={ein}&name={encoded_name}&city={quote_plus(city)}&state=NC",
            content=content,
            content_quality="full",
            data_classification="PUBLIC",
            metadata=metadata,
        )

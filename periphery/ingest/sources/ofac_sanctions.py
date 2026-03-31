"""OFAC Sanctions List data source.

Downloads and parses the U.S. Treasury OFAC Specially Designated Nationals
(SDN) list and the Consolidated Non-SDN list, producing IngestedDocument
objects for each sanctioned entity.

The SDN list CSV is pipe-delimited (not comma-separated), while the
consolidated list may use a different format — this module handles both.

LICENSE / ATTRIBUTION
---------------------
OFAC sanctions data is published by the U.S. Department of the Treasury,
Office of Foreign Assets Control. This data is a work of the United States
Government and is in the public domain (17 U.S.C. §105).

No license restrictions apply, but attribution is good practice:

    "Includes sanctions data from the U.S. Department of the Treasury,
     Office of Foreign Assets Control (OFAC).
     https://ofac.treasury.gov/"

The SDN list and Consolidated list are updated frequently. Source data:
    https://www.treasury.gov/ofac/downloads/sdn.csv
    https://www.treasury.gov/ofac/downloads/consolidated/cons_prim.csv
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
from collections import defaultdict
from datetime import datetime
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

# OFAC download URLs
_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
_ADD_URL = "https://www.treasury.gov/ofac/downloads/add.csv"
_ALT_URL = "https://www.treasury.gov/ofac/downloads/alt.csv"
_CONS_URL = "https://www.treasury.gov/ofac/downloads/consolidated/cons_prim.csv"

# SDN CSV field order (pipe-delimited, no header row)
_SDN_FIELDS = [
    "ent_num",
    "SDN_Name",
    "SDN_Type",
    "Program",
    "Title",
    "Call_Sign",
    "Vess_Type",
    "Tonnage",
    "GRT",
    "Vess_Flag",
    "Vess_Owner",
    "Remarks",
]

# ADD CSV field order (pipe-delimited, no header row)
_ADD_FIELDS = [
    "ent_num",
    "add_num",
    "address",
    "city_state_zip",
    "country",
    "add_remarks",
]

# ALT CSV field order (pipe-delimited, no header row)
_ALT_FIELDS = [
    "ent_num",
    "alt_num",
    "alt_type",
    "alt_name",
    "alt_remarks",
]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _clean(val: str) -> str:
    """Strip and remove OFAC placeholder values."""
    v = val.strip().strip('"')
    if v in ("-0-", "-", "N/A", ""):
        return ""
    return v


def _parse_pipe_csv(
    text: str, field_names: list[str]
) -> list[dict[str, str]]:
    """Parse a pipe-delimited CSV (no header) into list of dicts."""
    rows: list[dict[str, str]] = []
    reader = csv.reader(io.StringIO(text), delimiter="|", quotechar='"')
    for raw_row in reader:
        if not raw_row or all(not c.strip() for c in raw_row):
            continue
        row: dict[str, str] = {}
        for i, field in enumerate(field_names):
            row[field] = _clean(raw_row[i]) if i < len(raw_row) else ""
        rows.append(row)
    return rows


def _detect_delimiter(text: str) -> str:
    """Sniff delimiter from first line — OFAC sometimes ships CSV or pipe."""
    first_line = text.split("\n", 1)[0]
    if first_line.count("|") > first_line.count(","):
        return "|"
    return ","


def _parse_cons_csv(text: str) -> list[dict[str, str]]:
    """Parse consolidated list CSV — may have header row."""
    lines = text.strip().split("\n")
    if not lines:
        return []

    delimiter = _detect_delimiter(lines[0])
    has_header = any(
        word in lines[0].upper()
        for word in ("ENT_NUM", "NAME", "TYPE", "PROGRAM")
    )

    reader = csv.DictReader(
        io.StringIO(text),
        delimiter=delimiter,
    ) if has_header else csv.DictReader(
        io.StringIO(text),
        fieldnames=_SDN_FIELDS,
        delimiter=delimiter,
    )

    rows: list[dict[str, str]] = []
    for row in reader:
        # DictReader may put overflow fields into a list under the restkey —
        # coerce everything to str before cleaning.
        cleaned: dict[str, str] = {}
        for k, v in row.items():
            if isinstance(v, list):
                v = " ".join(str(x) for x in v)
            cleaned[k] = _clean(str(v) if v is not None else "")
        rows.append(cleaned)
    return rows


def _build_sdn_content(
    sdn: dict[str, str],
    addresses: list[dict[str, str]],
    alt_names: list[dict[str, str]],
) -> str:
    """Build structured text content for an SDN document."""
    parts: list[str] = []

    name = sdn.get("SDN_Name", "")
    if name:
        parts.append(f"Name: {name}")

    sdn_type = sdn.get("SDN_Type", "")
    if sdn_type:
        parts.append(f"Type: {sdn_type}")

    program = sdn.get("Program", "")
    if program:
        parts.append(f"Sanction programs: {program}")

    title = sdn.get("Title", "")
    if title:
        parts.append(f"Title: {title}")

    # Vessel fields
    for field, label in [
        ("Call_Sign", "Call sign"),
        ("Vess_Type", "Vessel type"),
        ("Tonnage", "Tonnage"),
        ("GRT", "GRT"),
        ("Vess_Flag", "Vessel flag"),
        ("Vess_Owner", "Vessel owner"),
    ]:
        val = sdn.get(field, "")
        if val:
            parts.append(f"{label}: {val}")

    remarks = sdn.get("Remarks", "")
    if remarks:
        parts.append(f"Remarks: {remarks}")

    # Addresses
    if addresses:
        parts.append("\nAddresses:")
        for addr in addresses:
            addr_parts = [
                addr.get("address", ""),
                addr.get("city_state_zip", ""),
                addr.get("country", ""),
            ]
            addr_str = ", ".join(p for p in addr_parts if p)
            if addr_str:
                parts.append(f"  - {addr_str}")
            remarks = addr.get("add_remarks", "")
            if remarks:
                parts.append(f"    ({remarks})")

    # Alternative names
    if alt_names:
        parts.append("\nAlternative names:")
        for alt in alt_names:
            alt_type = alt.get("alt_type", "")
            alt_name = alt.get("alt_name", "")
            if alt_name:
                line = f"  - {alt_name}"
                if alt_type:
                    line += f" ({alt_type})"
                remarks = alt.get("alt_remarks", "")
                if remarks:
                    line += f" — {remarks}"
                parts.append(line)

    return "\n".join(parts)


class OFACSanctionsSource(DataSource):
    """Polls OFAC SDN and Consolidated sanctions lists.

    Downloads all supporting CSV files (addresses, alt names) and joins
    them onto each SDN entry. Optionally includes the Consolidated Non-SDN
    list.
    """

    name = "ofac_sanctions"
    category = "sanctions_financial"
    default_poll_interval = 86400  # daily

    def __init__(
        self,
        *,
        poll_interval: int | None = None,
        enabled: bool = True,
        include_consolidated: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._include_consolidated = include_consolidated
        self._seen_hashes: set[str] = set()

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Download all OFAC CSVs and return new/changed documents."""
        logger.info("ofac_fetch_start")

        # Download all files in parallel
        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        tasks = [
            self._fetch_text(session, _SDN_URL, timeout),
            self._fetch_text(session, _ADD_URL, timeout),
            self._fetch_text(session, _ALT_URL, timeout),
        ]
        if self._include_consolidated:
            tasks.append(self._fetch_text(session, _CONS_URL, timeout))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        sdn_text = results[0] if not isinstance(results[0], Exception) else ""
        add_text = results[1] if not isinstance(results[1], Exception) else ""
        alt_text = results[2] if not isinstance(results[2], Exception) else ""
        cons_text = (
            results[3]
            if self._include_consolidated
            and len(results) > 3
            and not isinstance(results[3], Exception)
            else ""
        )

        if isinstance(results[0], Exception):
            logger.error("ofac_sdn_download_failed", error=str(results[0]))
            raise results[0]  # type: ignore[misc]
        if isinstance(results[1], Exception):
            logger.warning("ofac_add_download_failed", error=str(results[1]))
        if isinstance(results[2], Exception):
            logger.warning("ofac_alt_download_failed", error=str(results[2]))

        docs = await asyncio.get_running_loop().run_in_executor(
            None,
            self._parse_all,
            sdn_text,
            add_text,
            alt_text,
            cons_text,
        )

        logger.info("ofac_fetch_complete", total_docs=len(docs))
        return docs

    async def _fetch_text(
        self,
        session: aiohttp.ClientSession,
        url: str,
        timeout: aiohttp.ClientTimeout,
    ) -> str:
        async with session.get(url, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.text(encoding="latin-1")

    def _parse_all(
        self,
        sdn_text: str,
        add_text: str,
        alt_text: str,
        cons_text: str,
    ) -> list[IngestedDocument]:
        """Parse all OFAC CSVs synchronously."""
        # Build lookup tables indexed by ent_num
        addresses: dict[str, list[dict[str, str]]] = defaultdict(list)
        alt_names: dict[str, list[dict[str, str]]] = defaultdict(list)

        if add_text:
            for row in _parse_pipe_csv(add_text, _ADD_FIELDS):
                ent_num = row.get("ent_num", "")
                if ent_num:
                    addresses[ent_num].append(row)

        if alt_text:
            for row in _parse_pipe_csv(alt_text, _ALT_FIELDS):
                ent_num = row.get("ent_num", "")
                if ent_num:
                    alt_names[ent_num].append(row)

        docs: list[IngestedDocument] = []

        # Parse SDN list
        if sdn_text:
            sdn_rows = _parse_pipe_csv(sdn_text, _SDN_FIELDS)
            logger.info("ofac_sdn_rows", count=len(sdn_rows))
            for sdn in sdn_rows:
                doc = self._make_sdn_document(
                    sdn,
                    addresses.get(sdn.get("ent_num", ""), []),
                    alt_names.get(sdn.get("ent_num", ""), []),
                    source_feed="OFAC SDN List",
                    source_type="ofac_sdn",
                )
                if doc is not None:
                    docs.append(doc)

        # Parse consolidated Non-SDN list
        if cons_text:
            cons_rows = _parse_cons_csv(cons_text)
            logger.info("ofac_cons_rows", count=len(cons_rows))
            for cons in cons_rows:
                doc = self._make_sdn_document(
                    cons,
                    addresses.get(cons.get("ent_num", ""), []),
                    alt_names.get(cons.get("ent_num", ""), []),
                    source_feed="OFAC Consolidated",
                    source_type="ofac_consolidated",
                )
                if doc is not None:
                    docs.append(doc)

        return docs

    def _make_sdn_document(
        self,
        sdn: dict[str, str],
        addresses: list[dict[str, str]],
        alt_names: list[dict[str, str]],
        source_feed: str,
        source_type: str,
    ) -> IngestedDocument | None:
        """Build an IngestedDocument from a single SDN/consolidated row."""
        ent_num = sdn.get("ent_num", "").strip()
        name = sdn.get("SDN_Name", "").strip()

        if not ent_num or not name:
            return None

        content = _build_sdn_content(sdn, addresses, alt_names)
        content_hash = _content_hash(content)

        # Deduplication — skip unchanged entries
        if content_hash in self._seen_hashes:
            return None
        self._seen_hashes.add(content_hash)

        doc_id = make_document_id("ofac", f"OFAC-{ent_num}-{source_type}")

        # Parse sanction programs (semicolon or space separated)
        program_str = sdn.get("Program", "")
        sanction_programs = [
            p.strip() for p in program_str.replace(";", " ").split() if p.strip()
        ]

        metadata: dict[str, Any] = {
            **{k: v for k, v in sdn.items() if v},
            "source_type": source_type,
            "sanctioned": True,
            "sanction_programs": sanction_programs,
            "content_hash": content_hash,
            # Public domain data — attribution for good practice
            "attribution": "U.S. Department of the Treasury, Office of Foreign Assets Control (OFAC)",
            "license": "Public Domain (17 U.S.C. §105)",
            "source_url": "https://ofac.treasury.gov/",
        }

        if addresses:
            metadata["addresses"] = addresses
        if alt_names:
            metadata["alt_names"] = alt_names

        # Build URL for reference
        url = (
            f"https://sanctionssearch.ofac.treas.gov/"
            f"?id={ent_num}"
        )

        return IngestedDocument(
            id=doc_id,
            source_feed=source_feed,
            source_category="sanctions_financial",
            source_credibility_tier=1,  # U.S. government source
            title=name,
            url=url,
            published=None,
            content=content,
            metadata=metadata,
        )

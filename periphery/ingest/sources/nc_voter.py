"""NC Voter Registration and Voter History data source.

Downloads and parses North Carolina voter registration and voter history
data from the NC State Board of Elections (NCSBE).

Data files:
  - ncvoter_Statewide.zip  (~2GB compressed, ~7M records)
  - ncvhis_Statewide.zip   (~1GB compressed, voter history)

Both are tab-delimited text files inside ZIP archives.

PUBLIC DATA NOTICE
------------------
NC voter registration data is public record under NCGS §163-82.10.
This data is published by the NC State Board of Elections and freely
available for download.
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

NCVOTER_ZIP_URL = "https://s3.amazonaws.com/dl.ncsbe.gov/data/ncvoter_Statewide.zip"
NCVHIS_ZIP_URL = "https://s3.amazonaws.com/dl.ncsbe.gov/data/ncvhis_Statewide.zip"

# Column headers for the tab-delimited files
NCVOTER_COLUMNS = [
    "county_id", "county_desc", "voter_reg_num", "ncid", "last_name",
    "first_name", "middle_name", "name_suffix_lbl", "status_cd",
    "voter_status_desc", "reason_cd", "voter_status_reason_desc",
    "res_street_address", "res_city_desc", "state_cd", "zip_code",
    "mail_addr1", "mail_addr2", "mail_addr3", "mail_addr4", "mail_city",
    "mail_state", "mail_zipcode", "full_phone_number", "confidential_ind",
    "registr_dt", "race_code", "ethnic_code", "party_cd", "gender_code",
    "birth_year", "age_at_year_end", "birth_state", "drivers_lic", "ssn",
    "no_dl_ssn_chkbx", "hava_id_req", "precinct_abbrv", "precinct_desc",
    "municipality_abbrv", "municipality_desc", "ward_abbrv", "ward_desc",
    "cong_dist_abbrv", "super_court_abbrv", "judic_dist_abbrv",
    "nc_senate_abbrv", "nc_house_abbrv", "county_commiss_abbrv",
    "county_commiss_desc", "township_abbrv", "township_desc",
    "school_dist_abbrv", "school_dist_desc", "fire_dist_abbrv",
    "fire_dist_desc", "water_dist_abbrv", "water_dist_desc",
    "sewer_dist_abbrv", "sewer_dist_desc", "sanit_dist_abbrv",
    "sanit_dist_desc", "rescue_dist_abbrv", "rescue_dist_desc",
    "munic_dist_abbrv", "munic_dist_desc", "dist_1_abbrv", "dist_1_desc",
    "vtd_abbrv", "vtd_desc",
]

NCVHIS_COLUMNS = [
    "county_id", "county_desc", "voter_reg_num", "election_lbl",
    "election_desc", "voting_method", "voted_party_cd", "voted_party_desc",
    "pct_label", "pct_description", "ncid", "voted_county_id",
    "voted_county_desc", "vtd_label", "vtd_description",
]

# Sensitive fields to strip from metadata (PII that shouldn't be stored)
_SENSITIVE_FIELDS = {"drivers_lic", "ssn"}


def _build_voter_content(row: dict[str, str], history_count: int) -> str:
    """Build structured text content for a voter document."""
    name_parts = [
        row.get("first_name", "").strip(),
        row.get("middle_name", "").strip(),
        row.get("last_name", "").strip(),
        row.get("name_suffix_lbl", "").strip(),
    ]
    name = " ".join(p for p in name_parts if p)

    return (
        f"Voter: {name}\n"
        f"Party: {row.get('party_cd', '').strip()} | Status: {row.get('voter_status_desc', '').strip()}\n"
        f"County: {row.get('county_desc', '').strip()} | Precinct: {row.get('precinct_desc', '').strip()}\n"
        f"Address: {row.get('res_street_address', '').strip()}, "
        f"{row.get('res_city_desc', '').strip()}, "
        f"{row.get('state_cd', '').strip()} {row.get('zip_code', '').strip()}\n"
        f"Districts: CD-{row.get('cong_dist_abbrv', '').strip()}, "
        f"SD-{row.get('nc_senate_abbrv', '').strip()}, "
        f"HD-{row.get('nc_house_abbrv', '').strip()}\n"
        f"Registered: {row.get('registr_dt', '').strip()} | "
        f"Birth Year: {row.get('birth_year', '').strip()}\n"
        f"Race: {row.get('race_code', '').strip()} | "
        f"Ethnicity: {row.get('ethnic_code', '').strip()} | "
        f"Gender: {row.get('gender_code', '').strip()}\n"
        f"Elections Voted: {history_count}"
    )


def _parse_row(line: str, columns: list[str]) -> dict[str, str] | None:
    """Parse a tab-delimited line into a dict using the column list."""
    fields = line.rstrip("\n\r").split("\t")
    if len(fields) < len(columns):
        return None
    return {col: fields[i].strip() if i < len(fields) else "" for i, col in enumerate(columns)}


def _build_history_record(row: dict[str, str]) -> dict[str, str]:
    """Extract the relevant fields from a voter history row."""
    return {
        "election_lbl": row.get("election_lbl", "").strip(),
        "election_desc": row.get("election_desc", "").strip(),
        "voting_method": row.get("voting_method", "").strip(),
        "voted_party_cd": row.get("voted_party_cd", "").strip(),
        "voted_county_desc": row.get("voted_county_desc", "").strip(),
    }


class NCVoterSource(DataSource):
    """Polls NC State Board of Elections voter registration data.

    Downloads ncvoter_Statewide.zip and ncvhis_Statewide.zip, parses
    tab-delimited records, and emits IngestedDocument batches via
    self._emit() to avoid loading 7M+ records into memory.
    """

    name = "nc_voter"
    category = "voter_registration"
    default_poll_interval = 604800  # weekly

    def __init__(
        self,
        *,
        data_dir: str | Path = "/app/data/voter",
        batch_size: int = 10000,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._data_dir = Path(data_dir)
        self._batch_size = batch_size

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Download and parse NC voter data, emitting documents in batches."""
        logger.info("nc_voter_fetch_start")
        self._data_dir.mkdir(parents=True, exist_ok=True)

        voter_zip = self._data_dir / "ncvoter_Statewide.zip"
        history_zip = self._data_dir / "ncvhis_Statewide.zip"

        # Download ZIPs (skip if fresh)
        await self._maybe_download(session, NCVOTER_ZIP_URL, voter_zip)
        await self._maybe_download(session, NCVHIS_ZIP_URL, history_zip)

        # Build voting history map in a thread (CPU-bound)
        history_map = await asyncio.get_event_loop().run_in_executor(
            None, self._build_history_map, history_zip
        )

        logger.info("nc_voter_history_loaded", ncids=len(history_map))

        # Stream voter registration and emit batches
        count = await asyncio.get_event_loop().run_in_executor(
            None, self._process_voters_sync, voter_zip, history_map
        )

        logger.info("nc_voter_fetch_complete", total_docs=count)
        return []  # already emitted via _emit

    def _process_voters_sync(
        self, voter_zip: Path, history_map: dict[str, list[dict[str, str]]]
    ) -> int:
        """Process voter registration ZIP synchronously (runs in thread)."""
        batch: list[IngestedDocument] = []
        total = 0

        with zipfile.ZipFile(voter_zip, "r") as zf:
            # Find the voter data file inside the ZIP
            voter_file = self._find_file_in_zip(zf, "ncvoter")
            if voter_file is None:
                logger.error("nc_voter_file_not_found", zip_contents=zf.namelist())
                return 0

            with zf.open(voter_file) as raw:
                reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                # Skip header line
                header_line = reader.readline()
                if not header_line:
                    return 0

                for line in reader:
                    row = _parse_row(line, NCVOTER_COLUMNS)
                    if row is None:
                        continue

                    ncid = row.get("ncid", "").strip()
                    if not ncid:
                        continue

                    voting_history = history_map.get(ncid, [])
                    doc = self._build_document(row, ncid, voting_history)
                    batch.append(doc)

                    if len(batch) >= self._batch_size:
                        self._emit_sync(batch)
                        total += len(batch)
                        if total % 100_000 == 0:
                            logger.info("nc_voter_progress", processed=total)
                        batch = []

        if batch:
            self._emit_sync(batch)
            total += len(batch)

        return total

    def _emit_sync(self, docs: list[IngestedDocument]) -> None:
        """Emit documents from a sync context by scheduling on the event loop."""
        if self._on_documents is None:
            return
        # If _on_documents is a regular function (e.g. in tests), call directly
        import inspect
        if not inspect.iscoroutinefunction(self._on_documents):
            self._on_documents(docs)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # We're in a thread executor — schedule coroutine on the main loop
            future = asyncio.run_coroutine_threadsafe(
                self._emit(docs), loop
            )
            future.result(timeout=60)
        else:
            asyncio.run(self._emit(docs))

    def _build_history_map(
        self, history_zip: Path
    ) -> dict[str, list[dict[str, str]]]:
        """Parse ncvhis ZIP and build ncid -> list[history_record] map."""
        history: dict[str, list[dict[str, str]]] = {}

        if not history_zip.exists():
            logger.warning("nc_voter_history_zip_missing", path=str(history_zip))
            return history

        with zipfile.ZipFile(history_zip, "r") as zf:
            history_file = self._find_file_in_zip(zf, "ncvhis")
            if history_file is None:
                logger.error("nc_voter_history_file_not_found", zip_contents=zf.namelist())
                return history

            with zf.open(history_file) as raw:
                reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                # Skip header
                reader.readline()

                for line in reader:
                    row = _parse_row(line, NCVHIS_COLUMNS)
                    if row is None:
                        continue
                    ncid = row.get("ncid", "").strip()
                    if not ncid:
                        continue
                    record = _build_history_record(row)
                    if ncid not in history:
                        history[ncid] = []
                    history[ncid].append(record)

        return history

    def _find_file_in_zip(self, zf: zipfile.ZipFile, prefix: str) -> str | None:
        """Find the first file in a ZIP matching the given prefix."""
        for name in zf.namelist():
            basename = os.path.basename(name).lower()
            if basename.startswith(prefix) and basename.endswith(".txt"):
                return name
        # Fallback: any file with the prefix
        for name in zf.namelist():
            if prefix in name.lower():
                return name
        return None

    def _build_document(
        self,
        row: dict[str, str],
        ncid: str,
        voting_history: list[dict[str, str]],
    ) -> IngestedDocument:
        """Build an IngestedDocument from a voter registration row."""
        first_name = row.get("first_name", "").strip()
        last_name = row.get("last_name", "").strip()
        party_cd = row.get("party_cd", "").strip()
        county_desc = row.get("county_desc", "").strip()

        content = _build_voter_content(row, len(voting_history))

        # Build metadata with all fields (excluding sensitive PII)
        metadata: dict[str, Any] = {}
        for col, val in row.items():
            if col not in _SENSITIVE_FIELDS:
                metadata[col] = val.strip() if val else ""
        metadata["source_type"] = "nc_voter"
        metadata["voting_history"] = voting_history

        return IngestedDocument(
            id=make_document_id("nc_voter", ncid),
            source_feed="NC Voter Registration",
            source_category="voter_registration",
            source_credibility_tier=1,
            title=f"{first_name} {last_name} — {party_cd} — {county_desc}",
            url=f"https://vt.ncsbe.gov/RegLkup/VoterDetail/?NCID={ncid}",
            content=content,
            content_quality="full",
            metadata=metadata,
        )

    async def _maybe_download(
        self, session: aiohttp.ClientSession, url: str, dest: Path
    ) -> None:
        """Download a file if it doesn't exist or is older than poll_interval."""
        should_download = True
        if dest.exists() and dest.stat().st_size > 1_000_000:
            age_seconds = time.time() - dest.stat().st_mtime
            max_age = self.poll_interval or self.default_poll_interval
            if age_seconds < max_age:
                logger.info(
                    "nc_voter_zip_cache_hit",
                    path=str(dest),
                    age_hours=round(age_seconds / 3600, 1),
                )
                should_download = False

        if should_download:
            logger.info("nc_voter_download_start", url=url, dest=str(dest))
            timeout = aiohttp.ClientTimeout(total=7200, connect=30)
            async with session.get(url, timeout=timeout) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(dest, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total and downloaded % (100 * 1024 * 1024) < 1024 * 1024:
                            pct = downloaded * 100 // total
                            logger.info(
                                "nc_voter_download_progress",
                                url=url,
                                pct=pct,
                                mb=downloaded // (1024 * 1024),
                            )
            logger.info("nc_voter_download_done", path=str(dest), size_mb=dest.stat().st_size // (1024 * 1024))

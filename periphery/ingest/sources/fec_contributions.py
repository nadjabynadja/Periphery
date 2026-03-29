"""FEC Individual Contributions data source.

Downloads and parses FEC bulk individual contribution files.
Data is pipe-delimited (|) with no header row.

Data files:
  - indiv24.zip (~4GB compressed, 2023-2024 cycle)
  - indiv22.zip (2021-2022 cycle)
  - indiv20.zip (2019-2020 cycle)

PUBLIC DATA NOTICE
------------------
FEC individual contribution data is public record under the Federal
Election Campaign Act. This data is published by the Federal Election
Commission and freely available for bulk download.
"""

from __future__ import annotations

import asyncio
import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

# Bulk download URL pattern: indiv{YY}.zip
FEC_BULK_URL_TEMPLATE = "https://www.fec.gov/files/bulk-downloads/{cycle}/indiv{short_cycle}.zip"

# FEC individual contributions have NO header row.
# Field order per FEC bulk data specification.
FEC_FIELDS = [
    "CMTE_ID",
    "AMNDT_IND",
    "RPT_TP",
    "TRANSACTION_PGI",
    "IMAGE_NUM",
    "TRANSACTION_TP",
    "ENTITY_TP",
    "NAME",
    "CITY",
    "STATE",
    "ZIP_CODE",
    "EMPLOYER",
    "OCCUPATION",
    "TRANSACTION_DT",
    "TRANSACTION_AMT",
    "OTHER_ID",
    "TRAN_ID",
    "FILE_NUM",
    "MEMO_CD",
    "MEMO_TEXT",
    "SUB_ID",
]


def _cycle_to_short(cycle: str) -> str:
    """Convert '2024' → '24', '2022' → '22', etc."""
    return cycle[-2:]


def _build_fec_url(cycle: str) -> str:
    """Build the FEC bulk download URL for a given election cycle."""
    short = _cycle_to_short(cycle)
    return FEC_BULK_URL_TEMPLATE.format(cycle=cycle, short_cycle=short)


def _parse_fec_line(line: str) -> dict[str, str] | None:
    """Parse a pipe-delimited FEC line into a dict. Returns None on bad data."""
    fields = line.rstrip("\n\r").split("|")
    if len(fields) < len(FEC_FIELDS):
        return None
    return {FEC_FIELDS[i]: fields[i].strip() if i < len(fields) else "" for i in range(len(FEC_FIELDS))}


def _build_contribution_content(row: dict[str, str]) -> str:
    """Build structured text content for a contribution document."""
    return (
        f"Contributor: {row.get('NAME', '')}\n"
        f"Location: {row.get('CITY', '')}, {row.get('STATE', '')} {row.get('ZIP_CODE', '')}\n"
        f"Employer: {row.get('EMPLOYER', '')} | Occupation: {row.get('OCCUPATION', '')}\n"
        f"Committee: {row.get('CMTE_ID', '')} | Amount: ${row.get('TRANSACTION_AMT', '')}\n"
        f"Date: {row.get('TRANSACTION_DT', '')} | Transaction Type: {row.get('TRANSACTION_TP', '')}\n"
        f"Entity Type: {row.get('ENTITY_TP', '')} | Report Type: {row.get('RPT_TP', '')}"
    )


class FECContributionsSource(DataSource):
    """Polls FEC individual contribution bulk data files.

    Downloads indiv{YY}.zip for configured election cycles, streams
    pipe-delimited records, filters to the configured state, and emits
    IngestedDocument batches via self._emit() to avoid loading multi-GB
    files into memory.
    """

    name = "fec_contributions"
    category = "campaign_finance"
    default_poll_interval = 604800  # weekly

    def __init__(
        self,
        *,
        data_dir: str | Path = "/app/data/fec",
        cycles: list[str] | None = None,
        batch_size: int = 10000,
        state_filter: str = "NC",
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._data_dir = Path(data_dir)
        self._cycles = cycles or ["2024"]
        self._batch_size = batch_size
        self._state_filter = state_filter.upper()

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Download and parse FEC contribution data, emitting documents in batches."""
        logger.info("fec_contributions_fetch_start", cycles=self._cycles, state=self._state_filter)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        total_count = 0
        for cycle in self._cycles:
            url = _build_fec_url(cycle)
            zip_path = self._data_dir / f"indiv{_cycle_to_short(cycle)}.zip"

            # Download if needed
            await self._maybe_download(session, url, zip_path)

            # Process in thread (CPU-bound)
            count = await asyncio.get_running_loop().run_in_executor(
                None, self._process_cycle_sync, zip_path, cycle
            )
            total_count += count

        logger.info("fec_contributions_fetch_complete", total_docs=total_count)
        return []  # already emitted via _emit

    def _process_cycle_sync(self, zip_path: Path, cycle: str) -> int:
        """Process a single cycle ZIP synchronously (runs in thread)."""
        batch: list[IngestedDocument] = []
        total = 0

        if not zip_path.exists():
            logger.error("fec_zip_not_found", path=str(zip_path))
            return 0

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Find itcont.txt inside the ZIP
            data_file = self._find_data_file(zf)
            if data_file is None:
                logger.error("fec_data_file_not_found", zip_contents=zf.namelist())
                return 0

            with zf.open(data_file) as raw:
                reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                # FEC bulk files have NO header row — start parsing immediately

                for line in reader:
                    row = _parse_fec_line(line)
                    if row is None:
                        continue

                    # Filter to configured state
                    state = row.get("STATE", "").strip().upper()
                    if state != self._state_filter:
                        continue

                    sub_id = row.get("SUB_ID", "").strip()
                    if not sub_id:
                        continue

                    doc = self._build_document(row, sub_id, cycle)
                    batch.append(doc)

                    if len(batch) >= self._batch_size:
                        self._emit_sync(batch)
                        total += len(batch)
                        if total % 100_000 == 0:
                            logger.info("fec_contributions_progress", processed=total, cycle=cycle)
                        batch = []

        if batch:
            self._emit_sync(batch)
            total += len(batch)

        logger.info("fec_cycle_complete", cycle=cycle, records=total)
        return total

    def _find_data_file(self, zf: zipfile.ZipFile) -> str | None:
        """Find the contributions data file inside the ZIP."""
        for name in zf.namelist():
            basename = os.path.basename(name).lower()
            if basename.startswith("itcont") and basename.endswith(".txt"):
                return name
        # Fallback: any .txt file
        for name in zf.namelist():
            if name.lower().endswith(".txt"):
                return name
        return None

    def _build_document(
        self,
        row: dict[str, str],
        sub_id: str,
        cycle: str,
    ) -> IngestedDocument:
        """Build an IngestedDocument from a FEC contribution row."""
        name = row.get("NAME", "").strip()
        cmte_id = row.get("CMTE_ID", "").strip()
        amount = row.get("TRANSACTION_AMT", "").strip()
        date = row.get("TRANSACTION_DT", "").strip()

        content = _build_contribution_content(row)
        encoded_name = quote_plus(name)

        # Build metadata with all fields
        metadata: dict[str, Any] = {}
        for field in FEC_FIELDS:
            metadata[field] = row.get(field, "").strip()
        metadata["source_type"] = "fec_contributions"
        metadata["cycle"] = cycle

        return IngestedDocument(
            id=make_document_id("fec_contributions", sub_id),
            source_feed="FEC Individual Contributions",
            source_category="campaign_finance",
            source_credibility_tier=1,
            title=f"{name} — ${amount} to {cmte_id} ({date})",
            url=f"https://www.fec.gov/data/receipts/individual-contributions/?contributor_name={encoded_name}&contributor_state={self._state_filter}",
            content=content,
            content_quality="full",
            data_classification="PII",
            metadata=metadata,
        )

    def _emit_sync(self, docs: list[IngestedDocument]) -> None:
        """Emit documents from a sync context by scheduling on the event loop."""
        if self._on_documents is None:
            return
        import inspect
        if not inspect.iscoroutinefunction(self._on_documents):
            self._on_documents(docs)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._emit(docs), loop
            )
            future.result(timeout=60)
        else:
            asyncio.run(self._emit(docs))

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
                    "fec_zip_cache_hit",
                    path=str(dest),
                    age_hours=round(age_seconds / 3600, 1),
                )
                should_download = False

        if should_download:
            logger.info("fec_download_start", url=url, dest=str(dest))
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
                                "fec_download_progress",
                                url=url,
                                pct=pct,
                                mb=downloaded // (1024 * 1024),
                            )
            logger.info("fec_download_done", path=str(dest), size_mb=dest.stat().st_size // (1024 * 1024))

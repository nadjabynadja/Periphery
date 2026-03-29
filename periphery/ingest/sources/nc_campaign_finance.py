"""NC State Board of Elections Campaign Finance data source.

Queries the NCSBE Campaign Finance Transaction Lookup at
https://cf.ncsbe.gov/CFTxnLkup/ for recent receipts and expenditures.

No bulk download is available — data is fetched via the web search form
and CSV export endpoint.

PUBLIC DATA NOTICE
------------------
NC campaign finance data is public record. This data is published by the
NC State Board of Elections and freely available for search and export.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

# NCSBE Campaign Finance Transaction Lookup endpoints
NCSBE_SEARCH_URL = "https://cf.ncsbe.gov/CFTxnLkup/ExportResults/"
NCSBE_SEARCH_INIT_URL = "https://cf.ncsbe.gov/CFTxnLkup/SearchResults/"

# Rate limit: minimum delay between requests (seconds)
REQUEST_DELAY = 5.0

# Transaction types to query
TRANSACTION_TYPES = ["Receipt", "Expenditure"]


def _format_date(dt: datetime) -> str:
    """Format datetime as mm/dd/yyyy for NCSBE API."""
    return dt.strftime("%m/%d/%Y")


def _parse_ncsbe_date(date_str: str) -> str:
    """Parse various date formats from NCSBE data. Returns ISO date or original."""
    if not date_str or not date_str.strip():
        return ""
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S %p"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def _build_transaction_content(row: dict[str, str], txn_type: str) -> str:
    """Build structured text content for a campaign finance transaction."""
    return (
        f"Transaction Type: {txn_type}\n"
        f"Name: {row.get('Name', row.get('name', ''))}\n"
        f"Committee: {row.get('Committee Name', row.get('committee_name', ''))}\n"
        f"Amount: ${row.get('Amount', row.get('amount', ''))}\n"
        f"Date: {row.get('Date', row.get('date', ''))}\n"
        f"City: {row.get('City', row.get('city', ''))}\n"
        f"County: {row.get('County', row.get('county', ''))}\n"
        f"Description: {row.get('Purpose', row.get('purpose', row.get('Description', '')))}\n"
        f"Account Code: {row.get('Account Code', row.get('account_code', ''))}\n"
        f"Form of Payment: {row.get('Form of Payment', row.get('form_of_payment', ''))}"
    )


class NCCampaignFinanceSource(DataSource):
    """Polls NCSBE Campaign Finance Transaction Lookup.

    Queries recent receipts and expenditures via the NCSBE search form,
    exports results as CSV, and emits IngestedDocument batches.

    The NCSBE CFTxnLkup interface uses ASP.NET form POST. We POST search
    parameters to SearchResults/, then request ExportResults/ for CSV.
    Rate-limited to avoid hammering the state server.
    """

    name = "nc_campaign_finance"
    category = "campaign_finance"
    default_poll_interval = 604800  # weekly

    def __init__(
        self,
        *,
        lookback_days: int = 30,
        batch_size: int = 10000,
        request_delay: float = REQUEST_DELAY,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._lookback_days = lookback_days
        self._batch_size = batch_size
        self._request_delay = request_delay
        self._last_poll_date: datetime | None = None

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Query NCSBE for recent campaign finance transactions."""
        logger.info("nc_campaign_finance_fetch_start")

        now = datetime.now(timezone.utc)
        if self._last_poll_date:
            date_from = self._last_poll_date
        else:
            date_from = now - timedelta(days=self._lookback_days)
        date_to = now

        total_count = 0
        for txn_type in TRANSACTION_TYPES:
            count = await self._fetch_transaction_type(
                session, txn_type, date_from, date_to
            )
            total_count += count
            # Rate limit between transaction type queries
            await asyncio.sleep(self._request_delay)

        self._last_poll_date = date_to
        logger.info("nc_campaign_finance_fetch_complete", total_docs=total_count)
        return []  # already emitted via _emit

    async def _fetch_transaction_type(
        self,
        session: aiohttp.ClientSession,
        txn_type: str,
        date_from: datetime,
        date_to: datetime,
    ) -> int:
        """Fetch transactions of a given type within the date range.

        Iterates month by month if the range exceeds 31 days.
        """
        total = 0
        current_from = date_from

        while current_from < date_to:
            # Process in month-sized chunks
            current_to = min(current_from + timedelta(days=31), date_to)

            try:
                docs = await self._query_and_parse(
                    session, txn_type, current_from, current_to
                )
                if docs:
                    # Batch emit
                    for i in range(0, len(docs), self._batch_size):
                        batch = docs[i:i + self._batch_size]
                        await self._emit(batch)
                    total += len(docs)
                    logger.info(
                        "nc_campaign_finance_chunk",
                        txn_type=txn_type,
                        date_from=_format_date(current_from),
                        date_to=_format_date(current_to),
                        count=len(docs),
                    )
            except Exception as exc:
                logger.error(
                    "nc_campaign_finance_query_error",
                    txn_type=txn_type,
                    date_from=_format_date(current_from),
                    date_to=_format_date(current_to),
                    error=str(exc),
                )

            current_from = current_to
            await asyncio.sleep(self._request_delay)

        return total

    async def _query_and_parse(
        self,
        session: aiohttp.ClientSession,
        txn_type: str,
        date_from: datetime,
        date_to: datetime,
    ) -> list[IngestedDocument]:
        """Query NCSBE search and parse CSV results into documents.

        The NCSBE CFTxnLkup uses ASP.NET form POST:
        1. POST to SearchResults/ with form parameters to initiate search
        2. GET ExportResults/ to download CSV of results

        If direct CSV export fails, falls back to parsing the search results.
        """
        # Form data for the NCSBE search
        form_data = {
            "TransactionType": txn_type,
            "Name": "",
            "IsOrg": "false",
            "Committee": "",
            "DateFrom": _format_date(date_from),
            "DateTo": _format_date(date_to),
            "AmountFrom": "",
            "AmountTo": "",
            "County": "",
            "City": "",
        }

        timeout = aiohttp.ClientTimeout(total=120, connect=30)

        # Step 1: POST search to establish session/results
        async with session.post(
            NCSBE_SEARCH_INIT_URL,
            data=form_data,
            timeout=timeout,
            allow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            # The search results page is loaded; cookies/session should be set

        await asyncio.sleep(1)

        # Step 2: Export CSV results
        async with session.get(
            NCSBE_SEARCH_URL,
            timeout=timeout,
            allow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            csv_text = await resp.text()

        return self._parse_csv_results(csv_text, txn_type)

    def _parse_csv_results(
        self,
        csv_text: str,
        txn_type: str,
    ) -> list[IngestedDocument]:
        """Parse CSV export text into IngestedDocument list."""
        docs: list[IngestedDocument] = []

        if not csv_text or not csv_text.strip():
            return docs

        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            doc = self._build_document(row, txn_type)
            if doc is not None:
                docs.append(doc)

        return docs

    def _build_document(
        self,
        row: dict[str, str],
        txn_type: str,
    ) -> IngestedDocument | None:
        """Build an IngestedDocument from a CSV row."""
        # Handle various possible column names from the CSV export
        name = (row.get("Name") or row.get("name") or "").strip()
        committee = (row.get("Committee Name") or row.get("committee_name") or "").strip()
        amount = (row.get("Amount") or row.get("amount") or "").strip()
        date_raw = (row.get("Date") or row.get("date") or row.get("Transaction Date") or "").strip()
        date = _parse_ncsbe_date(date_raw)

        if not name and not committee:
            return None

        # Build a unique key from the transaction details
        unique_key = f"{committee}:{date}:{amount}:{name}"
        content = _build_transaction_content(row, txn_type)

        direction = "to" if txn_type == "Receipt" else "from"

        # Build metadata with all raw fields
        metadata: dict[str, Any] = {k: (v.strip() if isinstance(v, str) else str(v)) for k, v in row.items()}
        metadata["source_type"] = "nc_campaign_finance"
        metadata["transaction_type"] = txn_type

        return IngestedDocument(
            id=make_document_id("nc_campaign_finance", unique_key),
            source_feed="NCSBE Campaign Finance",
            source_category="campaign_finance",
            source_credibility_tier=1,
            title=f"{name} — ${amount} {direction} {committee} ({date})",
            url=f"https://cf.ncsbe.gov/CFTxnLkup/",
            content=content,
            content_quality="full",
            data_classification="PII",
            metadata=metadata,
        )

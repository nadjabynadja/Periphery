"""Tests for NC Campaign Finance data source."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from periphery.ingest.sources.base import make_document_id
from periphery.ingest.sources.nc_campaign_finance import (
    NCCampaignFinanceSource,
    _build_transaction_content,
    _format_date,
    _parse_ncsbe_date,
)
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_CSV = """Name,Committee Name,Amount,Date,City,County,Purpose,Account Code,Form of Payment
"DOE, JOHN",NC DEMS,500.00,10/01/2023,RALEIGH,WAKE,,General,Check
"SMITH, JANE",NC GOP,250.00,10/15/2023,DURHAM,DURHAM,,Primary,Credit Card
"""

SAMPLE_CSV_EMPTY = ""


class MockResponse:
    """Mock aiohttp response."""

    def __init__(self, text="", status=200):
        self._text = text
        self.status = status

    async def text(self):
        return self._text

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"HTTP {self.status}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Date formatting tests
# ---------------------------------------------------------------------------

class TestDateUtils:
    def test_format_date(self):
        dt = datetime(2023, 10, 1, tzinfo=timezone.utc)
        assert _format_date(dt) == "10/01/2023"

    def test_parse_ncsbe_date_mmddyyyy(self):
        assert _parse_ncsbe_date("10/01/2023") == "2023-10-01"

    def test_parse_ncsbe_date_iso(self):
        assert _parse_ncsbe_date("2023-10-01") == "2023-10-01"

    def test_parse_ncsbe_date_empty(self):
        assert _parse_ncsbe_date("") == ""
        assert _parse_ncsbe_date("  ") == ""

    def test_parse_ncsbe_date_unknown_format(self):
        # Returns original if can't parse
        assert _parse_ncsbe_date("not-a-date") == "not-a-date"


# ---------------------------------------------------------------------------
# Content building tests
# ---------------------------------------------------------------------------

class TestContentBuilding:
    def test_build_transaction_content(self):
        row = {
            "Name": "DOE, JOHN",
            "Committee Name": "NC DEMS",
            "Amount": "500.00",
            "Date": "10/01/2023",
            "City": "RALEIGH",
            "County": "WAKE",
            "Purpose": "General",
            "Account Code": "",
            "Form of Payment": "Check",
        }
        content = _build_transaction_content(row, "Receipt")
        assert "Transaction Type: Receipt" in content
        assert "DOE, JOHN" in content
        assert "NC DEMS" in content
        assert "$500.00" in content

    def test_build_transaction_content_expenditure(self):
        row = {
            "Name": "VENDOR INC",
            "Committee Name": "NC DEMS",
            "Amount": "1000.00",
            "Date": "10/05/2023",
            "City": "CHARLOTTE",
            "County": "MECKLENBURG",
            "Purpose": "Printing",
            "Account Code": "OD",
            "Form of Payment": "Wire",
        }
        content = _build_transaction_content(row, "Expenditure")
        assert "Transaction Type: Expenditure" in content
        assert "VENDOR INC" in content


# ---------------------------------------------------------------------------
# CSV parsing tests
# ---------------------------------------------------------------------------

class TestCSVParsing:
    def test_parse_csv_results(self):
        source = NCCampaignFinanceSource()
        docs = source._parse_csv_results(SAMPLE_CSV, "Receipt")
        assert len(docs) == 2
        assert docs[0].source_feed == "NCSBE Campaign Finance"
        assert docs[0].source_category == "campaign_finance"
        assert docs[0].source_credibility_tier == 1
        assert docs[0].data_classification == "PII"
        assert "DOE, JOHN" in docs[0].title
        assert "$500.00" in docs[0].title
        assert "to" in docs[0].title  # Receipt direction

    def test_parse_csv_expenditure_direction(self):
        source = NCCampaignFinanceSource()
        docs = source._parse_csv_results(SAMPLE_CSV, "Expenditure")
        assert len(docs) == 2
        assert "from" in docs[0].title  # Expenditure direction

    def test_parse_empty_csv(self):
        source = NCCampaignFinanceSource()
        docs = source._parse_csv_results("", "Receipt")
        assert docs == []

    def test_parse_csv_metadata(self):
        source = NCCampaignFinanceSource()
        docs = source._parse_csv_results(SAMPLE_CSV, "Receipt")
        doc = docs[0]
        assert doc.metadata["source_type"] == "nc_campaign_finance"
        assert doc.metadata["transaction_type"] == "Receipt"
        assert doc.metadata["Name"] == "DOE, JOHN"

    def test_document_id_deterministic(self):
        source = NCCampaignFinanceSource()
        docs1 = source._parse_csv_results(SAMPLE_CSV, "Receipt")
        docs2 = source._parse_csv_results(SAMPLE_CSV, "Receipt")
        assert docs1[0].id == docs2[0].id


# ---------------------------------------------------------------------------
# Document building tests
# ---------------------------------------------------------------------------

class TestDocumentBuilding:
    def test_build_document_receipt(self):
        source = NCCampaignFinanceSource()
        row = {
            "Name": "DOE, JOHN",
            "Committee Name": "NC DEMS",
            "Amount": "500.00",
            "Date": "10/01/2023",
            "City": "RALEIGH",
            "County": "WAKE",
            "Purpose": "",
            "Account Code": "",
            "Form of Payment": "",
        }
        doc = source._build_document(row, "Receipt")
        assert doc is not None
        assert "to" in doc.title
        assert doc.content_quality == "full"
        assert doc.url == "https://cf.ncsbe.gov/CFTxnLkup/"

    def test_build_document_expenditure(self):
        source = NCCampaignFinanceSource()
        row = {
            "Name": "VENDOR INC",
            "Committee Name": "NC GOP",
            "Amount": "1000.00",
            "Date": "10/05/2023",
            "City": "",
            "County": "",
            "Purpose": "Printing",
            "Account Code": "",
            "Form of Payment": "",
        }
        doc = source._build_document(row, "Expenditure")
        assert doc is not None
        assert "from" in doc.title

    def test_build_document_empty_name_and_committee_returns_none(self):
        source = NCCampaignFinanceSource()
        row = {
            "Name": "",
            "Committee Name": "",
            "Amount": "100",
            "Date": "10/01/2023",
        }
        doc = source._build_document(row, "Receipt")
        assert doc is None


# ---------------------------------------------------------------------------
# Fetch integration tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetch:
    @pytest.mark.asyncio
    async def test_fetch_queries_both_types(self):
        source = NCCampaignFinanceSource(
            lookback_days=7,
            request_delay=0,  # no delay in tests
        )

        emitted: list[IngestedDocument] = []
        source._on_documents = AsyncMock(side_effect=lambda docs: emitted.extend(docs))

        session = MagicMock()
        # POST returns search page, GET returns CSV
        session.post = MagicMock(return_value=MockResponse(""))
        session.get = MagicMock(return_value=MockResponse(SAMPLE_CSV))

        docs = await source.fetch(session)
        assert docs == []  # emitted via _emit
        # Should have called post twice (Receipt + Expenditure)
        assert session.post.call_count == 2
        # Should have called get twice (export for each type)
        assert session.get.call_count == 2
        # 2 records per CSV × 2 types = 4 total
        assert len(emitted) == 4

    @pytest.mark.asyncio
    async def test_fetch_handles_empty_response(self):
        source = NCCampaignFinanceSource(
            lookback_days=7,
            request_delay=0,
        )

        emitted: list[IngestedDocument] = []
        source._on_documents = AsyncMock(side_effect=lambda docs: emitted.extend(docs))

        session = MagicMock()
        session.post = MagicMock(return_value=MockResponse(""))
        session.get = MagicMock(return_value=MockResponse(""))

        docs = await source.fetch(session)
        assert docs == []
        assert len(emitted) == 0

    @pytest.mark.asyncio
    async def test_fetch_handles_error(self):
        source = NCCampaignFinanceSource(
            lookback_days=7,
            request_delay=0,
        )

        source._on_documents = AsyncMock()

        session = MagicMock()
        session.post = MagicMock(return_value=MockResponse("", status=500))

        # Should not raise — errors are caught internally
        docs = await source.fetch(session)
        assert docs == []


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults(self):
        source = NCCampaignFinanceSource()
        assert source.name == "nc_campaign_finance"
        assert source.category == "campaign_finance"
        assert source._lookback_days == 30
        assert source._batch_size == 10000
        assert source.default_poll_interval == 604800

    def test_custom_config(self):
        source = NCCampaignFinanceSource(
            lookback_days=14,
            batch_size=5000,
            request_delay=10.0,
            poll_interval=86400,
            enabled=False,
        )
        assert source._lookback_days == 14
        assert source._batch_size == 5000
        assert source._request_delay == 10.0
        assert source.poll_interval == 86400
        assert source.enabled is False

"""Tests for IRS Exempt Organizations (NC) data source."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from periphery.ingest.sources.irs_exempt_orgs import (
    IRSExemptOrgsSource,
    _build_org_content,
    _ntee_description,
    _safe_int,
    _format_currency,
    NTEE_MAJOR_CATEGORIES,
)
from periphery.ingest.sources.base import make_document_id
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Sample CSV data
# ---------------------------------------------------------------------------

SAMPLE_CSV = """EIN,NAME,ICO,STREET,CITY,STATE,ZIP,GROUP,SUBSECTION,AFFILIATION,CLASSIFICATION,RULING,DEDUCTIBILITY,FOUNDATION,ACTIVITY,ORGANIZATION,STATUS,TAX_PERIOD,ASSET_CD,INCOME_CD,FILING_REQ_CD,PF_FILING_REQ_CD,ACCT_PD,ASSET_AMT,INCOME_AMT,REVENUE_AMT,NTEE_CD,SORT_NAME
560532106,HABITAT FOR HUMANITY OF WAKE COUNTY,% JOHN DOE,PO BOX 12345,RALEIGH,NC,27605,0,3,3,1,198704,1,15,0,1,01,202306,7,7,1,0,6,5234567,3456789,3456789,L21,HABITAT FOR HUMANITY
561234567,TRIANGLE COMMUNITY FOUNDATION,,PO BOX 99999,DURHAM,NC,27702,0,3,3,1,199501,1,16,0,1,01,202312,8,8,1,0,12,98765432,12345678,12345678,T31,TRIANGLE COMMUNITY FOUNDATION
560532106,HABITAT FOR HUMANITY OF WAKE COUNTY,% JOHN DOE,PO BOX 12345,RALEIGH,NC,27605,0,3,3,1,198704,1,15,0,1,01,202306,7,7,1,0,6,5234567,3456789,3456789,L21,HABITAT FOR HUMANITY
"""

SAMPLE_CSV_MINIMAL = """EIN,NAME,ICO,STREET,CITY,STATE,ZIP,GROUP,SUBSECTION,AFFILIATION,CLASSIFICATION,RULING,DEDUCTIBILITY,FOUNDATION,ACTIVITY,ORGANIZATION,STATUS,TAX_PERIOD,ASSET_CD,INCOME_CD,FILING_REQ_CD,PF_FILING_REQ_CD,ACCT_PD,ASSET_AMT,INCOME_AMT,REVENUE_AMT,NTEE_CD,SORT_NAME
560000001,TEST ORG,,123 MAIN ST,RALEIGH,NC,27601,0,3,3,1,200001,1,10,0,1,01,202312,0,0,0,0,12,0,0,0,,TEST ORG
"""


# ---------------------------------------------------------------------------
# Helper: mock aiohttp response for CSV download
# ---------------------------------------------------------------------------

class MockCSVResponse:
    def __init__(self, text: str, status: int = 200):
        self._text = text
        self.status = status

    async def text(self, encoding: str = "utf-8") -> str:
        return self._text

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"HTTP {self.status}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# NTEE descriptions
# ---------------------------------------------------------------------------

class TestNTEEDescription:
    def test_known_code(self):
        desc = _ntee_description("L21")
        assert "Housing" in desc
        assert "L21" in desc

    def test_unknown_code(self):
        desc = _ntee_description("Z99")
        assert "Unknown" in desc or "Z99" in desc

    def test_empty_code(self):
        assert _ntee_description("") == ""

    def test_all_major_categories(self):
        for letter in NTEE_MAJOR_CATEGORIES:
            desc = _ntee_description(f"{letter}01")
            assert desc  # should return something non-empty


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

class TestUtils:
    def test_safe_int_valid(self):
        assert _safe_int("12345") == 12345

    def test_safe_int_empty(self):
        assert _safe_int("") == 0

    def test_safe_int_invalid(self):
        assert _safe_int("abc") == 0

    def test_format_currency_valid(self):
        assert _format_currency("5234567") == "$5,234,567"

    def test_format_currency_zero(self):
        assert _format_currency("0") == "$0"

    def test_format_currency_empty(self):
        assert _format_currency("") == "N/A"


# ---------------------------------------------------------------------------
# CSV parsing and document building
# ---------------------------------------------------------------------------

class TestCSVParsing:
    def test_parse_csv_basic(self):
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV)
        # 3 rows but one is duplicate EIN → 2 unique docs
        assert len(docs) == 2

    def test_parse_csv_dedup_by_ein(self):
        """Duplicate EINs should be deduplicated."""
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV)
        eins = [d.metadata["ein"] for d in docs]
        assert len(eins) == len(set(eins))

    def test_parse_csv_empty(self):
        source = IRSExemptOrgsSource(enabled=False)
        header_only = "EIN,NAME,ICO,STREET,CITY,STATE,ZIP,GROUP,SUBSECTION,AFFILIATION,CLASSIFICATION,RULING,DEDUCTIBILITY,FOUNDATION,ACTIVITY,ORGANIZATION,STATUS,TAX_PERIOD,ASSET_CD,INCOME_CD,FILING_REQ_CD,PF_FILING_REQ_CD,ACCT_PD,ASSET_AMT,INCOME_AMT,REVENUE_AMT,NTEE_CD,SORT_NAME\n"
        docs = source._parse_csv(header_only)
        assert docs == []


# ---------------------------------------------------------------------------
# Document building
# ---------------------------------------------------------------------------

class TestDocumentBuilding:
    def test_document_fields(self):
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV)
        doc = docs[0]

        assert isinstance(doc, IngestedDocument)
        assert doc.source_feed == "IRS Exempt Organizations"
        assert doc.source_category == "business_nonprofit"
        assert doc.source_credibility_tier == 1
        assert doc.data_classification == "PUBLIC"
        assert "HABITAT FOR HUMANITY" in doc.title
        assert "560532106" in doc.title
        assert "RALEIGH" in doc.title

    def test_document_id_deterministic(self):
        source = IRSExemptOrgsSource(enabled=False)
        docs1 = source._parse_csv(SAMPLE_CSV)
        docs2 = source._parse_csv(SAMPLE_CSV)
        assert docs1[0].id == docs2[0].id

    def test_document_id_uses_ein(self):
        """Document ID should be based on make_document_id("irs_exempt", EIN)."""
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV)
        expected_id = make_document_id("irs_exempt", "560532106")
        assert docs[0].id == expected_id

    def test_document_content_format(self):
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV)
        content = docs[0].content

        assert "Organization: HABITAT FOR HUMANITY" in content
        assert "EIN: 560532106" in content
        assert "501(c)(3)" in content
        assert "RALEIGH" in content

    def test_document_metadata_includes_all_fields(self):
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV)
        meta = docs[0].metadata

        assert meta["source_type"] == "irs_exempt_orgs"
        assert meta["ein"] == "560532106"
        assert meta["name"] == "HABITAT FOR HUMANITY OF WAKE COUNTY"
        assert meta["city"] == "RALEIGH"
        assert "ntee_description" in meta

    def test_document_url_contains_ein(self):
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV)
        assert "ein=560532106" in docs[0].url

    def test_minimal_record(self):
        """A record with minimal fields should still produce a valid document."""
        source = IRSExemptOrgsSource(enabled=False)
        docs = source._parse_csv(SAMPLE_CSV_MINIMAL)
        assert len(docs) == 1
        assert "TEST ORG" in docs[0].title


# ---------------------------------------------------------------------------
# Content building
# ---------------------------------------------------------------------------

class TestContentBuilding:
    def test_build_org_content(self):
        row = {
            "NAME": "TEST NONPROFIT",
            "EIN": "123456789",
            "ICO": "Jane Smith",
            "STREET": "100 Main St",
            "CITY": "DURHAM",
            "ZIP": "27701",
            "SUBSECTION": "3",
            "FOUNDATION": "15",
            "RULING": "200501",
            "STATUS": "01",
            "ASSET_AMT": "1000000",
            "INCOME_AMT": "500000",
            "REVENUE_AMT": "500000",
            "NTEE_CD": "B20",
            "ACTIVITY": "0",
            "TAX_PERIOD": "202312",
        }
        content = _build_org_content(row)
        assert "TEST NONPROFIT" in content
        assert "123456789" in content
        assert "Jane Smith" in content
        assert "501(c)(3)" in content
        assert "$1,000,000" in content


# ---------------------------------------------------------------------------
# Fetch (async)
# ---------------------------------------------------------------------------

class TestFetch:
    @pytest.mark.asyncio
    async def test_fetch_downloads_and_parses(self):
        source = IRSExemptOrgsSource(enabled=True)
        source._on_documents = AsyncMock()

        session = MagicMock()
        session.get = MagicMock(return_value=MockCSVResponse(SAMPLE_CSV))

        docs = await source.fetch(session)
        # Returns empty (emits via _emit)
        assert docs == []
        # But _on_documents should have been called
        assert source._on_documents.called
        # Check the emitted docs
        call_args = source._on_documents.call_args_list
        emitted_docs = []
        for call in call_args:
            emitted_docs.extend(call[0][0])
        assert len(emitted_docs) == 2  # 2 unique EINs

    @pytest.mark.asyncio
    async def test_fetch_handles_empty_csv(self):
        source = IRSExemptOrgsSource(enabled=True)
        source._on_documents = AsyncMock()

        header_only = "EIN,NAME,ICO,STREET,CITY,STATE,ZIP,GROUP,SUBSECTION,AFFILIATION,CLASSIFICATION,RULING,DEDUCTIBILITY,FOUNDATION,ACTIVITY,ORGANIZATION,STATUS,TAX_PERIOD,ASSET_CD,INCOME_CD,FILING_REQ_CD,PF_FILING_REQ_CD,ACCT_PD,ASSET_AMT,INCOME_AMT,REVENUE_AMT,NTEE_CD,SORT_NAME\n"
        session = MagicMock()
        session.get = MagicMock(return_value=MockCSVResponse(header_only))

        docs = await source.fetch(session)
        assert docs == []


# ---------------------------------------------------------------------------
# Source properties
# ---------------------------------------------------------------------------

class TestSourceProperties:
    def test_source_name(self):
        source = IRSExemptOrgsSource(enabled=False)
        assert source.name == "irs_exempt_orgs"

    def test_source_category(self):
        source = IRSExemptOrgsSource(enabled=False)
        assert source.category == "business_nonprofit"

    def test_default_poll_interval(self):
        source = IRSExemptOrgsSource(enabled=False)
        assert source.default_poll_interval == 7776000

    def test_health(self):
        source = IRSExemptOrgsSource(enabled=False)
        health = source.health()
        assert health["name"] == "irs_exempt_orgs"
        assert health["enabled"] is False

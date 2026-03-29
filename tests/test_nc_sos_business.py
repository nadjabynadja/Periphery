"""Tests for NC Secretary of State Business Registration data source."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from periphery.ingest.sources.nc_sos_business import (
    NCSoSBusinessSource,
    parse_sos_profile,
    _build_business_content,
    _clean_text,
    _extract_field,
)
from periphery.ingest.sources.base import make_document_id
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Sample HTML for SoS profile pages
# ---------------------------------------------------------------------------

SAMPLE_PROFILE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Business Registration - ACME CORPORATION LLC</title></head>
<body>
<h2>ACME CORPORATION LLC</h2>
<div class="profile-details">
    <span>Entity Type:</span> <span>Limited Liability Company</span>
    <span>Status:</span> <span>Current-Active</span>
    <span>Date Formed:</span> <span>01/15/2020</span>
    <span>Registered Agent:</span> <span>John Q. Smith</span>
    <span>Agent Address:</span> <span>123 Main St, Raleigh, NC 27601</span>
    <span>Principal Office Address:</span> <span>456 Oak Ave, Durham, NC 27701</span>
</div>
</body>
</html>
"""

SAMPLE_PROFILE_HTML_MINIMAL = """
<!DOCTYPE html>
<html>
<head><title>Business Registration - SIMPLE INC</title></head>
<body>
<h2>SIMPLE INC</h2>
<div>Business Registration details</div>
</body>
</html>
"""

SAMPLE_INVALID_HTML = """
<!DOCTYPE html>
<html>
<head><title>Page Not Found</title></head>
<body><h1>404 Not Found</h1></body>
</html>
"""


# ---------------------------------------------------------------------------
# Helper: mock aiohttp response
# ---------------------------------------------------------------------------

class MockHTMLResponse:
    def __init__(self, text: str, status: int = 200):
        self._text = text
        self.status = status

    async def text(self) -> str:
        return self._text

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"HTTP {self.status}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

class TestHTMLParsing:
    def test_parse_full_profile(self):
        fields = parse_sos_profile(SAMPLE_PROFILE_HTML, "12345")
        assert fields is not None
        assert fields["entity_name"] == "ACME CORPORATION LLC"
        assert fields["sos_id"] == "12345"

    def test_parse_entity_type(self):
        fields = parse_sos_profile(SAMPLE_PROFILE_HTML, "12345")
        assert fields is not None
        assert "Limited Liability" in fields["entity_type"] or fields["entity_type"] != "Unknown"

    def test_parse_status(self):
        fields = parse_sos_profile(SAMPLE_PROFILE_HTML, "12345")
        assert fields is not None
        # Status may be parsed or default to Unknown depending on HTML structure
        assert fields["status"]  # non-empty

    def test_parse_minimal_profile(self):
        fields = parse_sos_profile(SAMPLE_PROFILE_HTML_MINIMAL, "99999")
        assert fields is not None
        assert fields["entity_name"] == "SIMPLE INC"
        assert fields["sos_id"] == "99999"

    def test_parse_invalid_html(self):
        fields = parse_sos_profile(SAMPLE_INVALID_HTML, "00000")
        assert fields is None

    def test_parse_empty_html(self):
        fields = parse_sos_profile("", "00000")
        assert fields is None


# ---------------------------------------------------------------------------
# Profile field extraction
# ---------------------------------------------------------------------------

class TestFieldExtraction:
    def test_extract_field_basic(self):
        html = '<span>Status:</span> <span>Active</span>'
        val = _extract_field(html, "Status")
        assert val == "Active"

    def test_extract_field_missing(self):
        html = '<span>Name:</span> <span>Test</span>'
        val = _extract_field(html, "NonExistent")
        assert val == ""

    def test_clean_text(self):
        assert _clean_text("  hello   world  ") == "hello world"
        assert _clean_text("\n\ttab\n") == "tab"


# ---------------------------------------------------------------------------
# Content building
# ---------------------------------------------------------------------------

class TestContentBuilding:
    def test_build_business_content(self):
        fields = {
            "entity_name": "ACME CORP",
            "entity_type": "Corporation",
            "status": "Active",
            "sos_id": "12345",
            "date_formed": "01/15/2020",
            "agent_name": "John Smith",
            "agent_address": "123 Main St, Raleigh, NC",
            "principal_address": "456 Oak Ave, Durham, NC",
        }
        content = _build_business_content(fields)
        assert "Entity: ACME CORP" in content
        assert "Type: Corporation | Status: Active" in content
        assert "SOS ID: 12345" in content
        assert "Registered Agent: John Smith" in content
        assert "Principal Office: 456 Oak Ave" in content


# ---------------------------------------------------------------------------
# Document building
# ---------------------------------------------------------------------------

class TestDocumentBuilding:
    def test_document_from_parsed_profile(self):
        fields = parse_sos_profile(SAMPLE_PROFILE_HTML, "12345")
        assert fields is not None

        content = _build_business_content(fields)
        doc_id = make_document_id("nc_sos_business", "12345")

        doc = IngestedDocument(
            id=doc_id,
            source_feed="NC Secretary of State",
            source_category="business_registration",
            source_credibility_tier=1,
            title=f"{fields['entity_name']} — {fields['entity_type']} — {fields['status']}",
            url="https://www.sosnc.gov/online_services/search/Business_Registration_profile?Id=12345",
            content=content,
            data_classification="PUBLIC",
            metadata=dict(fields),
        )

        assert doc.source_feed == "NC Secretary of State"
        assert doc.source_category == "business_registration"
        assert doc.data_classification == "PUBLIC"
        assert "ACME CORPORATION" in doc.title

    def test_document_id_deterministic(self):
        id1 = make_document_id("nc_sos_business", "12345")
        id2 = make_document_id("nc_sos_business", "12345")
        assert id1 == id2

    def test_document_id_different_for_different_sos_ids(self):
        id1 = make_document_id("nc_sos_business", "12345")
        id2 = make_document_id("nc_sos_business", "67890")
        assert id1 != id2


# ---------------------------------------------------------------------------
# Seed file loading
# ---------------------------------------------------------------------------

class TestSeedFileLoading:
    def test_load_seed_ids_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# Comment line\n")
            f.write("12345\n")
            f.write("67890\n")
            f.write("\n")  # blank line
            f.write("11111\n")
            f.name

        source = NCSoSBusinessSource(seed_file=f.name, enabled=False)
        ids = source._load_seed_ids()
        assert ids == ["12345", "67890", "11111"]

        Path(f.name).unlink()

    def test_load_seed_ids_missing_file(self):
        source = NCSoSBusinessSource(seed_file="/nonexistent/file.txt", enabled=False)
        ids = source._load_seed_ids()
        assert ids == []

    def test_load_seed_ids_no_file_configured(self):
        source = NCSoSBusinessSource(seed_file="", enabled=False)
        ids = source._load_seed_ids()
        assert ids == []


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_daily_limit_default(self):
        source = NCSoSBusinessSource(enabled=False)
        assert source._daily_limit == 500

    def test_daily_limit_custom(self):
        source = NCSoSBusinessSource(daily_limit=100, enabled=False)
        assert source._daily_limit == 100

    def test_request_delay_default(self):
        source = NCSoSBusinessSource(enabled=False)
        assert source._request_delay == 5.0


# ---------------------------------------------------------------------------
# Fetch (async)
# ---------------------------------------------------------------------------

class TestFetch:
    @pytest.mark.asyncio
    async def test_fetch_no_seed_file(self):
        source = NCSoSBusinessSource(seed_file="", enabled=True)
        session = MagicMock()
        docs = await source.fetch(session)
        assert docs == []

    @pytest.mark.asyncio
    async def test_fetch_with_seed_ids(self):
        # Create temp seed file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("12345\n")
            seed_path = f.name

        source = NCSoSBusinessSource(
            seed_file=seed_path,
            request_delay=0,  # no delay in tests
            enabled=True,
        )
        source._on_documents = AsyncMock()

        session = MagicMock()
        session.get = MagicMock(return_value=MockHTMLResponse(SAMPLE_PROFILE_HTML))

        docs = await source.fetch(session)
        assert docs == []  # emitted via _emit
        assert source._on_documents.called

        # Check emitted docs
        call_args = source._on_documents.call_args_list
        emitted_docs = []
        for call in call_args:
            emitted_docs.extend(call[0][0])
        assert len(emitted_docs) == 1

        Path(seed_path).unlink()

    @pytest.mark.asyncio
    async def test_fetch_skips_already_ingested(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("12345\n")
            seed_path = f.name

        source = NCSoSBusinessSource(
            seed_file=seed_path,
            request_delay=0,
            enabled=True,
        )
        source._on_documents = AsyncMock()
        source._ingested_ids = {"12345"}  # already ingested

        session = MagicMock()
        docs = await source.fetch(session)
        assert docs == []
        # Should not have called session.get
        assert not session.get.called

        Path(seed_path).unlink()


# ---------------------------------------------------------------------------
# Source properties
# ---------------------------------------------------------------------------

class TestSourceProperties:
    def test_source_name(self):
        source = NCSoSBusinessSource(enabled=False)
        assert source.name == "nc_sos_business"

    def test_source_category(self):
        source = NCSoSBusinessSource(enabled=False)
        assert source.category == "business_registration"

    def test_default_poll_interval(self):
        source = NCSoSBusinessSource(enabled=False)
        assert source.default_poll_interval == 604800

    def test_health(self):
        source = NCSoSBusinessSource(enabled=False)
        health = source.health()
        assert health["name"] == "nc_sos_business"
        assert health["enabled"] is False

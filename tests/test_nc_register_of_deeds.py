"""Tests for NC Register of Deeds data source (stub)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from periphery.ingest.sources.nc_register_of_deeds import (
    NCRegisterOfDeedsSource,
    build_rod_document,
    _build_rod_content,
    COUNTY_PORTALS,
)
from periphery.ingest.sources.base import make_document_id
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Document model / content building
# ---------------------------------------------------------------------------

class TestContentBuilding:
    def test_build_rod_content_full(self):
        content = _build_rod_content(
            doc_type="Deed of Trust",
            record_date="2024-01-15",
            book="12345",
            page="678",
            grantor="SMITH, JOHN A",
            grantee="BANK OF AMERICA",
            legal_description="LOT 5 BLOCK 2 SUNRISE ESTATES",
            consideration="350000",
            county="Wake",
        )
        assert "Document Type: Deed of Trust" in content
        assert "Book: 12345 Page: 678" in content
        assert "Grantor: SMITH, JOHN A" in content
        assert "Grantee: BANK OF AMERICA" in content
        assert "Property: LOT 5 BLOCK 2" in content
        assert "Consideration: $350000" in content
        assert "County: Wake" in content

    def test_build_rod_content_minimal(self):
        content = _build_rod_content(
            doc_type="Warranty Deed",
            record_date="2024-03-01",
            book="100",
            page="50",
            grantor="DOE, JANE",
            grantee="ROE, RICHARD",
            legal_description="",
            consideration="",
            county="Wake",
        )
        assert "Warranty Deed" in content
        assert "DOE, JANE" in content
        assert "Property:" not in content  # no legal description
        assert "Consideration:" not in content  # no amount


# ---------------------------------------------------------------------------
# Document building via build_rod_document
# ---------------------------------------------------------------------------

class TestBuildRodDocument:
    def test_build_document_full(self):
        doc = build_rod_document(
            county="WAKE",
            doc_type="Deed of Trust",
            record_date="2024-01-15",
            book="12345",
            page="678",
            grantor="SMITH, JOHN A",
            grantee="BANK OF AMERICA",
            legal_description="LOT 5 BLOCK 2",
            consideration="350000",
        )
        assert isinstance(doc, IngestedDocument)
        assert doc.source_feed == "NC Register of Deeds — Wake"
        assert doc.source_category == "property_records"
        assert doc.source_credibility_tier == 1
        assert doc.data_classification == "PII"
        assert "Deed of Trust" in doc.title
        assert "SMITH" in doc.title
        assert "BANK OF AMERICA" in doc.title
        assert doc.metadata["county"] == "WAKE"
        assert doc.metadata["source_type"] == "nc_rod"

    def test_document_id_uses_book_page(self):
        doc = build_rod_document(
            county="WAKE",
            doc_type="Deed",
            record_date="2024-01-01",
            book="100",
            page="200",
            grantor="A",
            grantee="B",
        )
        expected_id = make_document_id("nc_rod", "WAKE:100:200")
        assert doc.id == expected_id

    def test_document_id_uses_document_id_fallback(self):
        doc = build_rod_document(
            county="WAKE",
            doc_type="Deed",
            record_date="2024-01-01",
            book="",
            page="",
            grantor="A",
            grantee="B",
            document_id="DOC-12345",
        )
        expected_id = make_document_id("nc_rod", "WAKE:DOC-12345")
        assert doc.id == expected_id

    def test_document_id_deterministic(self):
        doc1 = build_rod_document(
            county="WAKE", doc_type="Deed", record_date="2024-01-01",
            book="100", page="200", grantor="A", grantee="B",
        )
        doc2 = build_rod_document(
            county="WAKE", doc_type="Deed", record_date="2024-01-01",
            book="100", page="200", grantor="A", grantee="B",
        )
        assert doc1.id == doc2.id

    def test_document_url_from_county_portal(self):
        doc = build_rod_document(
            county="WAKE", doc_type="Deed", record_date="2024-01-01",
            book="100", page="200", grantor="A", grantee="B",
        )
        assert doc.url == COUNTY_PORTALS["WAKE"]

    def test_document_metadata_complete(self):
        doc = build_rod_document(
            county="WAKE",
            doc_type="Deed of Trust",
            record_date="2024-01-15",
            book="12345",
            page="678",
            grantor="SMITH, JOHN",
            grantee="BANK",
            legal_description="LOT 5",
            consideration="350000",
            document_id="DOC-999",
        )
        meta = doc.metadata
        assert meta["source_type"] == "nc_rod"
        assert meta["county"] == "WAKE"
        assert meta["doc_type"] == "Deed of Trust"
        assert meta["book"] == "12345"
        assert meta["page"] == "678"
        assert meta["grantor"] == "SMITH, JOHN"
        assert meta["grantee"] == "BANK"
        assert meta["consideration"] == "350000"
        assert meta["document_id"] == "DOC-999"


# ---------------------------------------------------------------------------
# Source (stub fetch)
# ---------------------------------------------------------------------------

class TestSourceStub:
    @pytest.mark.asyncio
    async def test_fetch_returns_empty(self):
        """Stub fetch should return empty list and log warning."""
        source = NCRegisterOfDeedsSource(enabled=True)
        session = MagicMock()
        docs = await source.fetch(session)
        assert docs == []

    @pytest.mark.asyncio
    async def test_fetch_multiple_counties(self):
        source = NCRegisterOfDeedsSource(counties=["WAKE", "DURHAM"], enabled=True)
        session = MagicMock()
        docs = await source.fetch(session)
        assert docs == []
        assert "DURHAM" in source._counties


# ---------------------------------------------------------------------------
# Source properties
# ---------------------------------------------------------------------------

class TestSourceProperties:
    def test_source_name(self):
        source = NCRegisterOfDeedsSource(enabled=False)
        assert source.name == "nc_rod"

    def test_source_category(self):
        source = NCRegisterOfDeedsSource(enabled=False)
        assert source.category == "property_records"

    def test_default_poll_interval(self):
        source = NCRegisterOfDeedsSource(enabled=False)
        assert source.default_poll_interval == 604800

    def test_default_counties(self):
        source = NCRegisterOfDeedsSource(enabled=False)
        assert source._counties == ["WAKE"]

    def test_custom_counties(self):
        source = NCRegisterOfDeedsSource(counties=["WAKE", "DURHAM", "ORANGE"], enabled=False)
        assert source._counties == ["WAKE", "DURHAM", "ORANGE"]

    def test_request_delay_default(self):
        source = NCRegisterOfDeedsSource(enabled=False)
        assert source._request_delay == 3.0

    def test_health(self):
        source = NCRegisterOfDeedsSource(enabled=False)
        health = source.health()
        assert health["name"] == "nc_rod"
        assert health["enabled"] is False

    def test_county_portals(self):
        assert "WAKE" in COUNTY_PORTALS
        assert "wakegov" in COUNTY_PORTALS["WAKE"]

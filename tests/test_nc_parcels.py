"""Tests for NC Property Records (Parcels) data source."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from periphery.ingest.sources.base import make_document_id
from periphery.ingest.sources.nc_parcels import (
    FEATURESERVER_BASE_URL,
    PARCELS_DATASET_URL,
    NCParcelsSource,
    _build_parcel_content,
    _build_parcel_title,
    _safe_float,
    _safe_str,
)
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_feature(
    objectid: int = 1,
    ownname: str = "SMITH, JOHN",
    siteadd: str = "123 MAIN ST",
    scity: str = "RALEIGH",
    parno: str = "0001234",
    cntyname: str = "WAKE",
    parval: float = 250000.0,
    landval: float = 80000.0,
    improvval: float = 170000.0,
    gisacres: float = 0.45,
    struct: str = "Y",
    parusecode: str = "R1",
    parusedesc: str = "RESIDENTIAL",
    saledatetx: str = "01/15/2023",
    legdecfull: str = "LOT 5 BLK A OAKWOOD",
    owntype: str = "Individual",
    mailadd: str = "123 MAIN ST",
    mcity: str = "RALEIGH",
    mstate: str = "NC",
    mzip: str = "27601",
    x: float = -78.6382,
    y: float = 35.7796,
    geometry: dict | None = None,
) -> dict:
    """Build a mock FeatureServer feature."""
    attrs = {
        "objectid": objectid,
        "ownname": ownname,
        "siteadd": siteadd,
        "scity": scity,
        "parno": parno,
        "cntyname": cntyname,
        "parval": parval,
        "landval": landval,
        "improvval": improvval,
        "gisacres": gisacres,
        "struct": struct,
        "parusecode": parusecode,
        "parusedesc": parusedesc,
        "saledatetx": saledatetx,
        "legdecfull": legdecfull,
        "owntype": owntype,
        "mailadd": mailadd,
        "mcity": mcity,
        "mstate": mstate,
        "mzip": mzip,
        "altparno": "",
        "munit": "",
        "sunit": "",
        "multistruc": "N",
        "saledate": None,
        "parusecd2": "",
        "parusedsc2": "",
        "parvaltype": "Market",
        "recareano": 0.45,
        "recareatx": "0.45 AC",
        "revisedate": None,
        "sourceref": "",
        "sourcedate": None,
    }

    if geometry is None:
        geometry = {"x": x, "y": y}

    return {"attributes": attrs, "geometry": geometry}


def _make_api_response(
    features: list[dict],
    exceeded_transfer_limit: bool = False,
) -> dict:
    """Build a mock FeatureServer JSON response."""
    resp = {"features": features}
    if exceeded_transfer_limit:
        resp["exceededTransferLimit"] = True
    return resp


class MockResponse:
    """Mock aiohttp response for FeatureServer queries."""

    def __init__(self, json_data: dict, status: int = 200):
        self._json_data = json_data
        self.status = status

    async def json(self, content_type=None):
        return self._json_data

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=self.status,
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_safe_str_with_value(self):
        assert _safe_str("hello") == "hello"

    def test_safe_str_with_none(self):
        assert _safe_str(None) == ""

    def test_safe_str_strips(self):
        assert _safe_str("  test  ") == "test"

    def test_safe_float_with_value(self):
        assert _safe_float(123.45) == 123.45

    def test_safe_float_with_none(self):
        assert _safe_float(None) == 0.0

    def test_safe_float_with_bad_string(self):
        assert _safe_float("not_a_number", 99.0) == 99.0


class TestBuildParcelContent:
    def test_content_format(self):
        feature = _make_feature()
        attrs = feature["attributes"]
        content = _build_parcel_content(attrs)

        assert "Property: 123 MAIN ST, RALEIGH" in content
        assert "Owner: SMITH, JOHN | Type: Individual" in content
        assert "Parcel: 0001234 | County: WAKE" in content
        assert "Value: $250,000 (Land: $80,000, Improved: $170,000)" in content
        assert "Acres: 0.45 | Has Structure: Y" in content
        assert "Use: RESIDENTIAL (R1)" in content
        assert "Last Sale: 01/15/2023" in content
        assert "Legal: LOT 5 BLK A OAKWOOD" in content
        assert "Mailing: 123 MAIN ST, RALEIGH, NC 27601" in content

    def test_content_with_null_values(self):
        attrs = {"objectid": 1}  # all other fields missing
        content = _build_parcel_content(attrs)
        assert "Property: , " in content
        assert "Value: $0" in content


class TestBuildParcelTitle:
    def test_title_format(self):
        attrs = _make_feature()["attributes"]
        title = _build_parcel_title(attrs)
        assert title == "SMITH, JOHN — 123 MAIN ST, RALEIGH — $250,000"

    def test_title_with_missing_fields(self):
        attrs = {"objectid": 1}
        title = _build_parcel_title(attrs)
        assert "Unknown Owner" in title
        assert "No Address" in title


# ---------------------------------------------------------------------------
# NCParcelsSource tests
# ---------------------------------------------------------------------------

class TestNCParcelsSource:
    def test_source_attributes(self):
        source = NCParcelsSource()
        assert source.name == "nc_parcels"
        assert source.category == "property_records"
        assert source.default_poll_interval == 604800

    def test_where_clause_no_county(self):
        source = NCParcelsSource()
        assert source._build_where_clause() == "1=1"

    def test_where_clause_with_county(self):
        source = NCParcelsSource(county="Wake")
        assert source._build_where_clause() == "UPPER(cntyname)='WAKE'"

    def test_where_clause_sql_injection_safe(self):
        source = NCParcelsSource(county="O'Brien")
        where = source._build_where_clause()
        assert "O''BRIEN" in where

    @pytest.mark.asyncio
    async def test_fetch_single_page(self):
        """Test fetching a single page of results (no exceededTransferLimit)."""
        features = [_make_feature(objectid=i + 1) for i in range(5)]
        response_data = _make_api_response(features, exceeded_transfer_limit=False)

        source = NCParcelsSource(page_size=2000, batch_size=100, query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response_data))

        result = await source.fetch(session)

        assert result == []  # already emitted
        assert len(emitted_docs) == 5

        # Verify document structure
        doc = emitted_docs[0]
        assert doc.source_feed == "NC Property Records"
        assert doc.source_category == "property_records"
        assert doc.source_credibility_tier == 1
        assert doc.content_quality == "full"
        assert doc.data_classification == "PII"
        assert doc.metadata["source_type"] == "nc_parcels"

    @pytest.mark.asyncio
    async def test_fetch_pagination(self):
        """Test multi-page pagination with exceededTransferLimit."""
        page1_features = [_make_feature(objectid=i + 1) for i in range(3)]
        page2_features = [_make_feature(objectid=i + 4) for i in range(2)]

        page1 = _make_api_response(page1_features, exceeded_transfer_limit=True)
        page2 = _make_api_response(page2_features, exceeded_transfer_limit=False)

        source = NCParcelsSource(page_size=3, batch_size=100, query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockResponse(page1)
            return MockResponse(page2)

        session.get = mock_get

        await source.fetch(session)

        assert len(emitted_docs) == 5
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_batch_emission(self):
        """Test that documents are emitted in batches of batch_size."""
        features = [_make_feature(objectid=i + 1) for i in range(15)]
        response = _make_api_response(features, exceeded_transfer_limit=False)

        source = NCParcelsSource(page_size=2000, batch_size=5, query_delay=0)

        emit_calls: list[int] = []

        async def capture_emit(docs):
            emit_calls.append(len(docs))

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        # 15 docs with batch_size=5 → 3 emit calls of 5 each
        assert emit_calls == [5, 5, 5]

    @pytest.mark.asyncio
    async def test_fetch_coordinate_extraction(self):
        """Test that WGS84 coordinates are extracted from geometry."""
        feature = _make_feature(objectid=1, x=-78.6382, y=35.7796)
        response = _make_api_response([feature])

        source = NCParcelsSource(query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        doc = emitted_docs[0]
        assert doc.metadata["latitude"] == 35.7796
        assert doc.metadata["longitude"] == -78.6382

    @pytest.mark.asyncio
    async def test_fetch_null_geometry(self):
        """Test graceful handling of features with null geometry."""
        feature = _make_feature(objectid=1)
        feature["geometry"] = None

        response = _make_api_response([feature])

        source = NCParcelsSource(query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        doc = emitted_docs[0]
        assert "latitude" not in doc.metadata
        assert "longitude" not in doc.metadata

    @pytest.mark.asyncio
    async def test_fetch_skips_null_objectid(self):
        """Test that features without objectid are skipped."""
        feature = _make_feature(objectid=1)
        feature["attributes"]["objectid"] = None

        response = _make_api_response([feature])

        source = NCParcelsSource(query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        assert len(emitted_docs) == 0

    @pytest.mark.asyncio
    async def test_fetch_county_filter(self):
        """Test that county filter is applied in where clause."""
        features = [_make_feature(objectid=1, cntyname="WAKE")]
        response = _make_api_response(features)

        source = NCParcelsSource(county="WAKE", query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        # Verify the where clause was used
        call_args = session.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params")
        assert "UPPER(cntyname)='WAKE'" in params["where"]

    @pytest.mark.asyncio
    async def test_fetch_requests_outsr_4326(self):
        """Test that outSR=4326 is included in API request params."""
        response = _make_api_response([])

        source = NCParcelsSource(query_delay=0)

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        call_args = session.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params")
        assert params["outSR"] == "4326"
        assert params["outFields"] == "*"
        assert params["f"] == "json"

    @pytest.mark.asyncio
    async def test_document_id_deterministic(self):
        """Test that document IDs are deterministic based on objectid."""
        id1 = make_document_id("nc_parcels", "12345")
        id2 = make_document_id("nc_parcels", "12345")
        assert id1 == id2

        id3 = make_document_id("nc_parcels", "99999")
        assert id1 != id3

    @pytest.mark.asyncio
    async def test_fetch_all_metadata_fields(self):
        """Test that all attribute fields are preserved in metadata."""
        feature = _make_feature(objectid=42)
        response = _make_api_response([feature])

        source = NCParcelsSource(query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        meta = emitted_docs[0].metadata
        assert meta["ownname"] == "SMITH, JOHN"
        assert meta["parno"] == "0001234"
        assert meta["cntyname"] == "WAKE"
        assert meta["parval"] == 250000.0
        assert meta["source_type"] == "nc_parcels"

    @pytest.mark.asyncio
    async def test_fetch_api_error_stops_gracefully(self):
        """Test that API errors stop pagination gracefully."""
        source = NCParcelsSource(query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse({}, status=500))

        await source.fetch(session)

        assert len(emitted_docs) == 0

    @pytest.mark.asyncio
    async def test_fetch_empty_response(self):
        """Test handling of empty feature list."""
        response = _make_api_response([])

        source = NCParcelsSource(query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        assert len(emitted_docs) == 0

    def test_constructor_defaults(self):
        source = NCParcelsSource()
        assert source._page_size == 2000
        assert source._batch_size == 10000
        assert source._query_delay == 2.0
        assert source._county is None
        assert source.enabled is True

    def test_constructor_custom_params(self):
        source = NCParcelsSource(
            page_size=500,
            batch_size=5000,
            query_delay=1.0,
            county="MECKLENBURG",
            enabled=False,
        )
        assert source._page_size == 500
        assert source._batch_size == 5000
        assert source._query_delay == 1.0
        assert source._county == "MECKLENBURG"
        assert source.enabled is False

    @pytest.mark.asyncio
    async def test_url_points_to_dataset(self):
        """Test that document URL points to the NC OneMap dataset page."""
        feature = _make_feature(objectid=1)
        response = _make_api_response([feature])

        source = NCParcelsSource(query_delay=0)

        emitted_docs: list[IngestedDocument] = []

        async def capture_emit(docs):
            emitted_docs.extend(docs)

        source._on_documents = capture_emit

        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(response))

        await source.fetch(session)

        assert emitted_docs[0].url == PARCELS_DATASET_URL

"""Tests for the geospatial API router."""

import pytest
from unittest.mock import AsyncMock, patch

from periphery.geo.router import router, SatelliteSearchRequest


class TestPropertyRecordsEndpoint:
    """Tests for GET /api/geo/property-records."""

    @pytest.mark.asyncio
    async def test_property_records_returns_structure(self):
        """Verify the response structure when DBs are missing."""
        from periphery.geo.router import get_property_records

        with patch("periphery.geo.records.reverse_geocode", new_callable=AsyncMock, return_value=None):
            result = await get_property_records(lat=35.7796, lng=-78.6382, address="123 Main St, Raleigh, NC")
        # Endpoint returns dict on success, PropertyRecordResponse on error
        if isinstance(result, dict):
            assert result["address"] == "123 Main St, Raleigh, NC"
            assert isinstance(result["owners"], list)
            assert isinstance(result["voters"], list)
        else:
            assert result.address == "123 Main St, Raleigh, NC"

    @pytest.mark.asyncio
    async def test_property_records_graceful_on_error(self):
        """Verify graceful degradation when lookup raises."""
        from periphery.geo.router import get_property_records

        with patch("periphery.geo.records.lookup_property_records", new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await get_property_records(lat=35.0, lng=-78.0, address="test")
        # Should return empty PropertyRecordResponse, not raise
        assert result.address == "test"
        assert result.owners == []


class TestCCTVEndpoint:
    """Tests for GET /api/geo/cctv/nearby."""

    @pytest.mark.asyncio
    async def test_cctv_returns_empty_on_error(self):
        from periphery.geo.router import cctv_nearby

        with patch("periphery.geo.cctv.find_nearby_cameras", new_callable=AsyncMock, side_effect=Exception("network")):
            result = await cctv_nearby(lat=35.0, lng=-78.0, radius=2000)
        assert result.feeds == []


class TestSatelliteSearchValidation:
    """Tests for satellite search request validation."""

    def test_aoi_requires_min_3_vertices(self):
        with pytest.raises(ValueError, match="at least 3 vertices"):
            SatelliteSearchRequest(
                aoi=[[0, 0], [1, 1]],
                start_date="2026-01-01",
                end_date="2026-01-31",
            )

    def test_aoi_rejects_over_64_vertices(self):
        big_aoi = [[float(i), float(i)] for i in range(65)]
        with pytest.raises(ValueError, match="64 vertices"):
            SatelliteSearchRequest(
                aoi=big_aoi,
                start_date="2026-01-01",
                end_date="2026-01-31",
            )

    def test_aoi_accepts_valid_polygon(self):
        req = SatelliteSearchRequest(
            aoi=[[0, 0], [1, 0], [1, 1], [0, 1]],
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
        assert len(req.aoi) == 4


class TestRecordsHelpers:
    """Tests for records.py helper functions."""

    def test_parse_address_parts(self):
        from periphery.geo.records import _parse_address_parts

        street, city, zip_code = _parse_address_parts("123 Main St, Raleigh, NC 27601")
        assert street == "123 Main St"
        assert city == "Raleigh"
        assert zip_code == "27601"

    def test_parse_address_no_commas(self):
        from periphery.geo.records import _parse_address_parts

        street, city, zip_code = _parse_address_parts("123 Main St")
        assert street == "123 Main St"
        assert city == ""
        assert zip_code == ""


class TestCCTVHelpers:
    """Tests for CCTV helper functions."""

    def test_haversine_same_point(self):
        from periphery.geo.cctv import haversine_km

        assert haversine_km(35.0, -78.0, 35.0, -78.0) == 0.0

    def test_haversine_known_distance(self):
        from periphery.geo.cctv import haversine_km

        # Raleigh to Durham ≈ 35 km
        dist = haversine_km(35.7796, -78.6382, 35.9940, -78.8986)
        assert 25 < dist < 40

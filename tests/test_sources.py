"""Tests for external data source integrations."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from periphery.sources.base import DataSource, make_document_id
from periphery.sources.opensky import OpenSkySource
from periphery.sources.adsb_exchange import ADSBExchangeSource
from periphery.sources.maritime import MaritimeSource
from periphery.sources.celestrak import CelesTrakSource, _period_minutes, _apogee_perigee_km
from periphery.sources.openstreetmap import OpenStreetMapSource
from periphery.sources.cctv import CCTVSource
from periphery.sources.daemon import SourcesDaemon
from periphery.sources.factory import build_sources, _parse_csv, _parse_float_tuple
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Helper: mock aiohttp response
# ---------------------------------------------------------------------------

class MockResponse:
    def __init__(self, json_data, status=200):
        self._json_data = json_data
        self.status = status

    async def json(self):
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


class MockHeadResponse:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# base.py
# ---------------------------------------------------------------------------

class TestBase:
    def test_make_document_id_deterministic(self):
        id1 = make_document_id("opensky", "abc123:1000")
        id2 = make_document_id("opensky", "abc123:1000")
        assert id1 == id2
        assert len(id1) == 24

    def test_make_document_id_different_inputs(self):
        id1 = make_document_id("opensky", "abc123:1000")
        id2 = make_document_id("opensky", "abc123:1001")
        assert id1 != id2

    def test_datasource_health(self):
        source = OpenSkySource(enabled=False)
        health = source.health()
        assert health["name"] == "opensky"
        assert health["enabled"] is False
        assert health["running"] is False
        assert health["total_fetched"] == 0


# ---------------------------------------------------------------------------
# OpenSky
# ---------------------------------------------------------------------------

class TestOpenSky:
    @pytest.fixture
    def opensky_response(self):
        return {
            "time": 1700000000,
            "states": [
                [
                    "abc123", "UAL123  ", "United States", 1700000000, 1700000000,
                    -73.7789, 40.6413, 10000, False, 250.0,
                    90.0, 5.0, None, 10050, "1200", False, 0,
                ],
                [
                    "def456", "BAW456  ", "United Kingdom", 1700000000, 1700000000,
                    -0.4614, 51.4700, None, True, 0.0,
                    0.0, 0.0, None, None, None, False, 0,
                ],
                [
                    "ghi789", None, "France", 1700000000, 1700000000,
                    None, None, 5000, False, 200.0,
                    180.0, -3.0, None, 5100, None, False, 0,
                ],
            ],
        }

    @pytest.mark.asyncio
    async def test_fetch_returns_documents(self, opensky_response):
        source = OpenSkySource()
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(opensky_response))

        docs = await source.fetch(session)

        # 2 valid (ghi789 has no lat/lon), 1 skipped
        assert len(docs) == 2
        assert all(isinstance(d, IngestedDocument) for d in docs)
        assert docs[0].source_category == "aviation"
        assert docs[0].metadata["icao24"] == "abc123"
        assert "UAL123" in docs[0].title
        assert docs[0].metadata["latitude"] == 40.6413

    @pytest.mark.asyncio
    async def test_fetch_skips_unchanged_timestamp(self, opensky_response):
        source = OpenSkySource()
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(opensky_response))

        # First call
        docs1 = await source.fetch(session)
        assert len(docs1) == 2

        # Same timestamp → should return empty
        docs2 = await source.fetch(session)
        assert len(docs2) == 0

    @pytest.mark.asyncio
    async def test_fetch_with_bbox(self):
        source = OpenSkySource(bbox=(40.0, -74.0, 41.0, -73.0))
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse({"time": 100, "states": []}))

        await source.fetch(session)
        call_kwargs = session.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["lamin"] == "40.0"
        assert params["lomax"] == "-73.0"


# ---------------------------------------------------------------------------
# ADS-B Exchange
# ---------------------------------------------------------------------------

class TestADSBExchange:
    @pytest.mark.asyncio
    async def test_fetch_tracked_aircraft(self):
        source = ADSBExchangeSource(
            icao_watchlist=["abc123", "def456"],
            position_api_url="http://test:3000",
        )
        session = MagicMock()

        def mock_get(url, **kwargs):
            if "abc123" in url:
                return MockResponse({
                    "latitude": 40.6, "longitude": -73.7,
                    "altitude": 35000, "speed": 450, "callsign": "UAL123",
                })
            return MockResponse({}, status=404)

        session.get = mock_get
        docs = await source.fetch(session)
        assert len(docs) == 1
        assert docs[0].metadata["icao24"] == "abc123"

    @pytest.mark.asyncio
    async def test_skips_unchanged_position(self):
        source = ADSBExchangeSource(
            icao_watchlist=["abc123"],
            position_api_url="http://test:3000",
        )
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse({
            "latitude": 40.6, "longitude": -73.7,
        }))

        docs1 = await source.fetch(session)
        assert len(docs1) == 1

        docs2 = await source.fetch(session)
        assert len(docs2) == 0

    @pytest.mark.asyncio
    async def test_empty_watchlist(self):
        source = ADSBExchangeSource(icao_watchlist=[])
        session = MagicMock()
        docs = await source.fetch(session)
        assert docs == []


# ---------------------------------------------------------------------------
# Maritime
# ---------------------------------------------------------------------------

class TestMaritime:
    @pytest.mark.asyncio
    async def test_fetch_vessel_by_mmsi(self):
        source = MaritimeSource(
            mmsi_watchlist=["123456789"],
            position_api_url="http://test:3000",
        )
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse({
            "latitude": 51.5, "longitude": -0.1,
            "name": "TEST VESSEL", "type": "Cargo",
            "speed": 12.5, "course": 270,
            "destination": "Rotterdam", "flag": "NL",
        }))

        docs = await source.fetch(session)
        assert len(docs) == 1
        assert docs[0].metadata["mmsi"] == "123456789"
        assert docs[0].metadata["vessel_name"] == "TEST VESSEL"
        assert docs[0].source_category == "maritime"

    @pytest.mark.asyncio
    async def test_fetch_area(self):
        source = MaritimeSource(
            watch_areas=["WMED"],
            position_api_url="http://test:3000",
        )
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse([
            {"mmsi": "111", "latitude": 36.0, "longitude": 14.0, "name": "V1"},
            {"mmsi": "222", "latitude": 37.0, "longitude": 15.0, "name": "V2"},
        ]))

        docs = await source.fetch(session)
        assert len(docs) == 2

    @pytest.mark.asyncio
    async def test_fetch_nearby(self):
        source = MaritimeSource(
            watch_points=[{"lat": 51.5, "lng": -0.1, "distance_nm": 25}],
            position_api_url="http://test:3000",
        )
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse([
            {"mmsi": "333", "latitude": 51.6, "longitude": -0.05, "name": "NEARBY"},
        ]))

        docs = await source.fetch(session)
        assert len(docs) == 1


# ---------------------------------------------------------------------------
# CelesTrak
# ---------------------------------------------------------------------------

class TestCelesTrak:
    def test_period_minutes(self):
        # ISS: ~15.5 revs/day → ~92 min period
        period = _period_minutes(15.5)
        assert 92 < period < 93

    def test_apogee_perigee(self):
        apogee, perigee = _apogee_perigee_km(15.5, 0.001)
        assert perigee > 300  # ISS-like orbit
        assert apogee > perigee

    @pytest.fixture
    def celestrak_response(self):
        return [
            {
                "NORAD_CAT_ID": 25544,
                "OBJECT_NAME": "ISS (ZARYA)",
                "OBJECT_ID": "1998-067A",
                "EPOCH": "2024-01-01T12:00:00",
                "MEAN_MOTION": 15.5,
                "ECCENTRICITY": 0.0001,
                "INCLINATION": 51.6,
                "RA_OF_ASC_NODE": 120.0,
                "ARG_OF_PERICENTER": 90.0,
                "MEAN_ANOMALY": 45.0,
                "REV_AT_EPOCH": 45000,
                "OBJECT_TYPE": "PAYLOAD",
                "RCS_SIZE": "LARGE",
            },
        ]

    @pytest.mark.asyncio
    async def test_fetch_group(self, celestrak_response):
        source = CelesTrakSource(groups=["stations"])
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(celestrak_response))

        docs = await source.fetch(session)
        assert len(docs) == 1
        assert docs[0].metadata["norad_id"] == 25544
        assert "ISS" in docs[0].title
        assert docs[0].source_category == "space"

    @pytest.mark.asyncio
    async def test_skips_same_epoch(self, celestrak_response):
        source = CelesTrakSource(groups=["stations"])
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse(celestrak_response))

        docs1 = await source.fetch(session)
        assert len(docs1) == 1

        docs2 = await source.fetch(session)
        assert len(docs2) == 0


# ---------------------------------------------------------------------------
# OpenStreetMap
# ---------------------------------------------------------------------------

class TestOpenStreetMap:
    @pytest.fixture
    def overpass_response(self):
        return {
            "elements": [
                {
                    "type": "node",
                    "id": 123456,
                    "lat": 48.8566,
                    "lon": 2.3522,
                    "tags": {
                        "name": "Test Military Base",
                        "military": "barracks",
                    },
                },
                {
                    "type": "way",
                    "id": 789012,
                    "center": {"lat": 51.47, "lon": -0.45},
                    "tags": {
                        "name": "Heathrow Airport",
                        "aeroway": "aerodrome",
                        "operator": "BAA",
                    },
                },
            ],
        }

    @pytest.mark.asyncio
    async def test_fetch_features(self, overpass_response):
        source = OpenStreetMapSource(
            bbox=(48.0, 2.0, 52.0, 3.0),
            feature_types=["military"],
        )
        session = MagicMock()
        session.post = MagicMock(return_value=MockResponse(overpass_response))

        docs = await source.fetch(session)
        assert len(docs) == 2
        assert docs[0].source_category == "infrastructure"
        assert docs[0].metadata["osm_id"] == 123456

    @pytest.mark.asyncio
    async def test_no_bbox_returns_empty(self):
        source = OpenStreetMapSource()
        session = MagicMock()
        docs = await source.fetch(session)
        assert docs == []

    @pytest.mark.asyncio
    async def test_skips_unchanged_features(self, overpass_response):
        source = OpenStreetMapSource(
            bbox=(48.0, 2.0, 52.0, 3.0),
            feature_types=["military"],
        )
        session = MagicMock()
        session.post = MagicMock(return_value=MockResponse(overpass_response))

        docs1 = await source.fetch(session)
        assert len(docs1) == 2

        docs2 = await source.fetch(session)
        assert len(docs2) == 0


# ---------------------------------------------------------------------------
# CCTV
# ---------------------------------------------------------------------------

class TestCCTV:
    @pytest.mark.asyncio
    async def test_fetch_dot_endpoint(self):
        source = CCTVSource(
            dot_endpoints=["http://dot.example.com/api/cameras"],
        )
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse({
            "cameras": [
                {
                    "id": "cam1",
                    "name": "I-95 Camera",
                    "latitude": 40.7,
                    "longitude": -74.0,
                    "imageUrl": "http://dot.example.com/cam1.jpg",
                    "roadway": "I-95",
                },
            ],
        }))

        docs = await source.fetch(session)
        assert len(docs) == 1
        assert docs[0].metadata["camera_id"] == "cam1"
        assert docs[0].source_category == "surveillance"

    @pytest.mark.asyncio
    async def test_check_static_camera(self):
        source = CCTVSource(
            camera_feeds=[{
                "id": "test-cam",
                "name": "Test Camera",
                "url": "http://example.com/stream",
                "lat": 40.0,
                "lon": -74.0,
                "type": "traffic",
            }],
        )
        session = MagicMock()
        session.head = MagicMock(return_value=MockHeadResponse(200))

        docs = await source.fetch(session)
        assert len(docs) == 1
        assert docs[0].metadata["status"] == "online"

    @pytest.mark.asyncio
    async def test_geojson_camera_format(self):
        source = CCTVSource(
            dot_endpoints=["http://dot.example.com/geojson"],
        )
        session = MagicMock()
        session.get = MagicMock(return_value=MockResponse({
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-74.0, 40.7]},
                    "properties": {
                        "id": "geo-cam-1",
                        "name": "GeoJSON Camera",
                        "direction": "NB",
                    },
                },
            ],
        }))

        docs = await source.fetch(session)
        assert len(docs) == 1
        assert docs[0].metadata["latitude"] == 40.7
        assert docs[0].metadata["longitude"] == -74.0


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class TestSourcesDaemon:
    @pytest.mark.asyncio
    async def test_daemon_lifecycle(self):
        source = MagicMock(spec=DataSource)
        source.name = "test"
        source.enabled = True
        source.start = AsyncMock()
        source.stop = AsyncMock()
        source.health.return_value = {"name": "test", "enabled": True}

        daemon = SourcesDaemon([source])
        await daemon.start()

        assert source.start.called
        health = daemon.health()
        assert health["enabled_sources"] == 1

        await daemon.stop()
        assert source.stop.called


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_parse_csv(self):
        assert _parse_csv("a, b, c") == ["a", "b", "c"]
        assert _parse_csv("") == []
        assert _parse_csv("one") == ["one"]

    def test_parse_float_tuple(self):
        result = _parse_float_tuple("40.0,-74.0,41.0,-73.0")
        assert result == (40.0, -74.0, 41.0, -73.0)
        assert _parse_float_tuple("") is None
        assert _parse_float_tuple("not,numbers") is None

    def test_build_sources_all_disabled(self):
        """With defaults, all sources are disabled."""
        from periphery.config import Settings
        settings = Settings(
            anthropic_api_key="test",
            exa_api_key="test",
        )
        sources = build_sources(settings)
        assert len(sources) == 6
        assert all(not s.enabled for s in sources)

    def test_build_sources_opensky_enabled(self):
        from periphery.config import Settings
        settings = Settings(
            anthropic_api_key="test",
            exa_api_key="test",
            opensky_enabled=True,
            opensky_bbox="40.0,-74.0,41.0,-73.0",
        )
        sources = build_sources(settings)
        opensky = next(s for s in sources if s.name == "opensky")
        assert opensky.enabled is True
        assert opensky._bbox == (40.0, -74.0, 41.0, -73.0)

    def test_build_sources_maritime_with_watchlist(self):
        from periphery.config import Settings
        settings = Settings(
            anthropic_api_key="test",
            exa_api_key="test",
            maritime_enabled=True,
            maritime_mmsi_watchlist="123456789,987654321",
            maritime_watch_areas="WMED,EMED",
        )
        sources = build_sources(settings)
        maritime = next(s for s in sources if s.name == "maritime")
        assert maritime.enabled is True
        assert maritime._mmsi_watchlist == ["123456789", "987654321"]
        assert maritime._watch_areas == ["WMED", "EMED"]

    def test_build_sources_celestrak_with_norad_ids(self):
        from periphery.config import Settings
        settings = Settings(
            anthropic_api_key="test",
            exa_api_key="test",
            celestrak_enabled=True,
            celestrak_norad_ids="25544,48274",
        )
        sources = build_sources(settings)
        celestrak = next(s for s in sources if s.name == "celestrak")
        assert celestrak.enabled is True
        assert celestrak._norad_ids == [25544, 48274]

"""Tests for GDELTDocSource integration."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from periphery.ingest.sources.gdelt_doc import (
    GDELTDocSource,
    GDELT_QUERIES,
    _parse_seendate,
)
from periphery.ingest.sources.base import make_document_id


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockResponse:
    def __init__(self, json_data, status=200):
        self._json_data = json_data
        self.status = status

    async def json(self, content_type=None):
        return self._json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_parse_seendate_valid():
    dt = _parse_seendate("20260328T143000Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 28
    assert dt.hour == 14
    assert dt.minute == 30
    assert dt.tzinfo == timezone.utc


def test_parse_seendate_no_z():
    dt = _parse_seendate("20260328T143000")
    assert dt is not None
    assert dt.hour == 14


def test_parse_seendate_empty():
    assert _parse_seendate("") is None
    assert _parse_seendate(None) is None


def test_parse_seendate_invalid():
    assert _parse_seendate("not-a-date") is None


def test_query_count():
    """We should have 28 query sets across 8 categories."""
    assert len(GDELT_QUERIES) == 28
    categories = {q["category"] for q in GDELT_QUERIES}
    assert len(categories) == 8


def test_source_defaults():
    src = GDELTDocSource()
    assert src.name == "gdelt_doc"
    assert src.category == "global_news"
    assert src.default_poll_interval == 900
    assert src._max_articles == 75
    assert src._query_delay == 5.0


def test_source_custom_params():
    src = GDELTDocSource(
        poll_interval=600,
        max_articles_per_query=50,
        query_delay=1.0,
    )
    assert src.poll_interval == 600
    assert src._max_articles == 50
    assert src._query_delay == 1.0


@pytest.mark.asyncio
async def test_fetch_single_query():
    """Test that a single query produces deduplicated IngestedDocuments."""
    mock_data = {
        "articles": [
            {
                "url": "https://example.com/article1",
                "title": "Test Article 1",
                "seendate": "20260328T120000Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "United States",
                "socialimage": "https://example.com/img.jpg",
            },
            {
                "url": "https://example.com/article2",
                "title": "Test Article 2",
                "seendate": "20260328T121500Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "United States",
                "socialimage": "",
            },
            {
                "url": "https://example.com/article1",  # duplicate URL
                "title": "Test Article 1 Duplicate",
                "seendate": "20260328T120000Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "United States",
                "socialimage": "",
            },
        ]
    }

    # Use only one query to simplify test
    src = GDELTDocSource(
        queries=[{"category": "test", "query": "test query"}],
        query_delay=0,
    )

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(return_value=MockResponse(mock_data))

    docs = await src.fetch(session)

    # Should deduplicate: 2 unique articles, not 3
    assert len(docs) == 2
    assert docs[0].title == "Test Article 1"
    assert docs[1].title == "Test Article 2"

    # Check fields
    doc = docs[0]
    assert doc.source_feed == "GDELT (test)"
    assert doc.source_category == "global_news"
    assert doc.source_credibility_tier == 2
    assert doc.content_quality == "metadata_only"
    assert doc.metadata["source_type"] == "gdelt_doc"
    assert doc.metadata["gdelt_domain"] == "example.com"
    assert doc.metadata["gdelt_language"] == "English"
    assert doc.metadata["gdelt_source_country"] == "United States"
    assert doc.metadata["gdelt_query_category"] == "test"

    # ID should be deterministic
    expected_id = make_document_id("gdelt_doc", "https://example.com/article1")
    assert doc.id == expected_id


@pytest.mark.asyncio
async def test_fetch_empty_response():
    """Empty API response should return no documents."""
    src = GDELTDocSource(
        queries=[{"category": "test", "query": "obscure query"}],
        query_delay=0,
    )

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(return_value=MockResponse({"articles": []}))

    docs = await src.fetch(session)
    assert docs == []


@pytest.mark.asyncio
async def test_fetch_api_error_continues():
    """API errors on one query should not stop other queries."""
    good_data = {
        "articles": [
            {
                "url": "https://example.com/good",
                "title": "Good Article",
                "seendate": "20260328T120000Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "US",
                "socialimage": "",
            }
        ]
    }

    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResponse({}, status=500)
        return MockResponse(good_data)

    src = GDELTDocSource(
        queries=[
            {"category": "bad", "query": "will fail"},
            {"category": "good", "query": "will succeed"},
        ],
        query_delay=0,
    )

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = mock_get

    docs = await src.fetch(session)
    assert len(docs) == 1
    assert docs[0].title == "Good Article"


@pytest.mark.asyncio
async def test_cross_cycle_dedup():
    """Articles from previous cycle should not be re-emitted."""
    data = {
        "articles": [
            {
                "url": "https://example.com/persistent",
                "title": "Persistent Article",
                "seendate": "20260328T120000Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "US",
                "socialimage": "",
            }
        ]
    }

    src = GDELTDocSource(
        queries=[{"category": "test", "query": "test"}],
        query_delay=0,
    )

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(return_value=MockResponse(data))

    # First cycle: should return 1
    docs1 = await src.fetch(session)
    assert len(docs1) == 1

    # Second cycle with same data: cross-cycle dedup filters them out
    # (seen_urls from cycle 1 blocks same URLs in cycle 2)
    docs2 = await src.fetch(session)
    assert len(docs2) == 0  # correctly deduped across cycles

    # Third cycle: seen_urls was replaced with cycle 2's empty set,
    # so cycle 3 should see them as new again
    docs3 = await src.fetch(session)
    assert len(docs3) == 1  # back again since cycle 2 had no URLs


@pytest.mark.asyncio
async def test_skip_articles_without_url_or_title():
    """Articles missing URL or title should be skipped."""
    data = {
        "articles": [
            {"url": "", "title": "No URL", "seendate": "20260328T120000Z"},
            {"url": "https://example.com/no-title", "title": "", "seendate": "20260328T120000Z"},
            {
                "url": "https://example.com/valid",
                "title": "Valid",
                "seendate": "20260328T120000Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "US",
                "socialimage": "",
            },
        ]
    }

    src = GDELTDocSource(
        queries=[{"category": "test", "query": "test"}],
        query_delay=0,
    )

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(return_value=MockResponse(data))

    docs = await src.fetch(session)
    assert len(docs) == 1
    assert docs[0].title == "Valid"

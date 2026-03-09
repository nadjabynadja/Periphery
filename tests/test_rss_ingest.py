"""Tests for the RSS ingest daemon components."""

import asyncio
import tempfile
from pathlib import Path

import pytest
import yaml

from periphery.rss_ingest.content import clean_html
from periphery.rss_ingest.dedup import Deduplicator, DeduplicationStore
from periphery.rss_ingest.feed_manager import FeedManager
from periphery.rss_ingest.models import FeedConfig, IngestedDocument
from periphery.rss_ingest.queue import InProcessQueue


# ── DeduplicationStore tests ───────────────────────────────────────────


class TestDeduplicationStore:
    def test_mark_and_check(self):
        store = DeduplicationStore(max_size=100)
        assert not store.is_seen("abc")
        store.mark_seen("abc")
        assert store.is_seen("abc")

    def test_eviction(self):
        store = DeduplicationStore(max_size=3)
        store.mark_seen("a")
        store.mark_seen("b")
        store.mark_seen("c")
        assert len(store) == 3
        store.mark_seen("d")
        assert len(store) == 3
        # "a" should have been evicted
        assert not store.is_seen("a")
        assert store.is_seen("d")

    def test_lru_refresh(self):
        store = DeduplicationStore(max_size=3)
        store.mark_seen("a")
        store.mark_seen("b")
        store.mark_seen("c")
        # access "a" to refresh it
        store.is_seen("a")
        store.mark_seen("d")
        # "b" should be evicted, not "a"
        assert not store.is_seen("b")
        assert store.is_seen("a")


# ── Deduplicator tests ────────────────────────────────────────────────


class TestDeduplicator:
    def test_id_dedup(self):
        d = Deduplicator()
        assert not d.is_duplicate("id1", "some content")
        d.record("id1", "some content")
        assert d.is_duplicate("id1", "different content")

    def test_content_dedup(self):
        d = Deduplicator()
        d.record("id1", "The quick brown fox jumps over the lazy dog")
        # same content, different ID
        assert d.is_duplicate("id2", "The quick brown fox jumps over the lazy dog")

    def test_content_normalization(self):
        d = Deduplicator()
        d.record("id1", "Hello   World")
        # extra whitespace should normalize to the same hash
        assert d.is_duplicate("id2", "hello world")

    def test_different_content_not_duplicate(self):
        d = Deduplicator()
        d.record("id1", "article about cats")
        assert not d.is_duplicate("id2", "article about dogs")


# ── FeedManager tests ─────────────────────────────────────────────────


class TestFeedManager:
    def _write_config(self, path: Path, feeds: list[dict]) -> None:
        path.write_text(yaml.dump({"feeds": feeds}))

    def test_load_config(self, tmp_path):
        cfg = tmp_path / "feeds.yaml"
        self._write_config(cfg, [
            {"url": "https://example.com/feed.xml", "name": "Test", "category": "test"},
        ])
        fm = FeedManager(cfg)
        assert len(fm.feeds) == 1
        assert fm.feeds[0].url == "https://example.com/feed.xml"
        assert fm.feeds[0].poll_interval == 300  # default

    def test_reload_adds_and_removes(self, tmp_path):
        cfg = tmp_path / "feeds.yaml"
        self._write_config(cfg, [
            {"url": "https://a.com/feed", "name": "A", "category": "test"},
            {"url": "https://b.com/feed", "name": "B", "category": "test"},
        ])
        fm = FeedManager(cfg)
        assert len(fm.feeds) == 2

        # remove B, add C
        self._write_config(cfg, [
            {"url": "https://a.com/feed", "name": "A", "category": "test"},
            {"url": "https://c.com/feed", "name": "C", "category": "test"},
        ])
        fm.reload()
        urls = {f.url for f in fm.feeds}
        assert "https://b.com/feed" not in urls
        assert "https://c.com/feed" in urls

    def test_state_preserved_on_reload(self, tmp_path):
        cfg = tmp_path / "feeds.yaml"
        self._write_config(cfg, [
            {"url": "https://a.com/feed", "name": "A", "category": "test"},
        ])
        fm = FeedManager(cfg)
        state = fm.get_state("https://a.com/feed")
        state.etag = '"abc123"'
        state.consecutive_failures = 3

        fm.reload()
        state2 = fm.get_state("https://a.com/feed")
        assert state2.etag == '"abc123"'
        assert state2.consecutive_failures == 3

    def test_categories(self, tmp_path):
        cfg = tmp_path / "feeds.yaml"
        self._write_config(cfg, [
            {"url": "https://a.com/feed", "name": "A", "category": "news"},
            {"url": "https://b.com/feed", "name": "B", "category": "CVE"},
            {"url": "https://c.com/feed", "name": "C", "category": "news"},
        ])
        fm = FeedManager(cfg)
        assert fm.categories == ["CVE", "news"]
        assert len(fm.feeds_by_category("news")) == 2

    def test_add_and_remove_runtime(self, tmp_path):
        cfg = tmp_path / "feeds.yaml"
        self._write_config(cfg, [])
        fm = FeedManager(cfg)
        assert len(fm.feeds) == 0

        fm.add_feed(FeedConfig(url="https://x.com/feed", name="X", category="test"))
        assert len(fm.feeds) == 1

        assert fm.remove_feed("https://x.com/feed")
        assert len(fm.feeds) == 0
        assert not fm.remove_feed("https://nonexistent.com")

    def test_default_config_loads(self):
        """The bundled feeds.yaml should parse without error."""
        fm = FeedManager()
        assert len(fm.feeds) >= 25


# ── Content cleaning tests ─────────────────────────────────────────────


class TestContentCleaning:
    def test_clean_simple_html(self):
        html = "<p>Hello <b>world</b>!</p>"
        text = clean_html(html)
        assert "Hello" in text
        assert "world" in text
        assert "<p>" not in text

    def test_clean_empty(self):
        assert clean_html("") == ""


# ── InProcessQueue tests ──────────────────────────────────────────────


class TestInProcessQueue:
    @pytest.mark.asyncio
    async def test_put_get(self):
        q = InProcessQueue(maxsize=10)
        doc = IngestedDocument(
            id="test1",
            source_feed="https://example.com/feed",
            source_category="test",
            title="Test Article",
            url="https://example.com/article",
            content="This is a test article body.",
        )
        await q.put(doc)
        assert q.depth() == 1
        got = await q.get()
        assert got.id == "test1"
        assert q.depth() == 0

    @pytest.mark.asyncio
    async def test_depth(self):
        q = InProcessQueue(maxsize=10)
        for i in range(5):
            doc = IngestedDocument(
                id=f"doc{i}",
                source_feed="https://example.com/feed",
                source_category="test",
                title=f"Article {i}",
                url=f"https://example.com/{i}",
                content=f"Content {i}",
            )
            await q.put(doc)
        assert q.depth() == 5


# ── Model tests ───────────────────────────────────────────────────────


class TestModels:
    def test_ingested_document_defaults(self):
        doc = IngestedDocument(
            id="abc",
            source_feed="https://example.com",
            source_category="test",
            title="Test",
            url="https://example.com/1",
            content="Body text",
        )
        assert doc.ingested is not None
        assert doc.metadata == {}
        assert doc.raw_html == ""

    def test_feed_config_defaults(self):
        fc = FeedConfig(url="https://example.com/feed", name="Test", category="news")
        assert fc.poll_interval == 300
        assert fc.priority == 3

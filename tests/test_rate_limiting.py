"""Tests for the RSS rate limiting and politeness layer."""

import asyncio
import time
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from periphery.rss_ingest.models import (
    BackoffState,
    DaemonStatus,
    DomainStatus,
    FeedConfig,
    HealthStatus,
    IngestedDocument,
)
from periphery.rss_ingest.rate_limiter import (
    DomainLimiter,
    DomainRateLimitConfig,
    RateLimiterChain,
    RateLimitConfig,
    SlidingWindowCounter,
    TokenBucket,
    extract_domain,
)
from periphery.rss_ingest.priority_scheduler import PollRequest, PriorityScheduler
from periphery.rss_ingest.feed_manager import FeedManager


# ── extract_domain tests ──────────────────────────────────────────────


class TestExtractDomain:
    def test_basic_url(self):
        assert extract_domain("https://example.com/feed.xml") == "example.com"

    def test_with_port(self):
        assert extract_domain("https://example.com:8080/feed") == "example.com:8080"

    def test_subdomain(self):
        assert extract_domain("https://feeds.reuters.com/reuters/topNews") == "feeds.reuters.com"

    def test_case_insensitive(self):
        assert extract_domain("https://EXAMPLE.COM/feed") == "example.com"


# ── TokenBucket tests ────────────────────────────────────────────────


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_initial_tokens(self):
        bucket = TokenBucket(rate=1.0, capacity=5)
        assert bucket.tokens == pytest.approx(5.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_acquire_consumes_token(self):
        bucket = TokenBucket(rate=1.0, capacity=5)
        await bucket.acquire()
        assert bucket.tokens == pytest.approx(4.0, abs=0.2)

    @pytest.mark.asyncio
    async def test_acquire_all_tokens(self):
        bucket = TokenBucket(rate=10.0, capacity=3)
        for _ in range(3):
            await bucket.acquire()
        # tokens should be near zero
        assert bucket.tokens < 1.0

    @pytest.mark.asyncio
    async def test_refill_over_time(self):
        bucket = TokenBucket(rate=100.0, capacity=5)
        # drain all tokens
        for _ in range(5):
            await bucket.acquire()
        # wait for refill
        await asyncio.sleep(0.05)
        assert bucket.tokens >= 1.0

    @pytest.mark.asyncio
    async def test_capacity_cap(self):
        bucket = TokenBucket(rate=1000.0, capacity=3)
        await asyncio.sleep(0.05)
        # should not exceed capacity
        assert bucket.tokens <= 3.0


# ── SlidingWindowCounter tests ───────────────────────────────────────


class TestSlidingWindowCounter:
    @pytest.mark.asyncio
    async def test_basic_acquire(self):
        counter = SlidingWindowCounter(max_per_minute=10)
        await counter.acquire()
        assert counter.current_count == 1

    @pytest.mark.asyncio
    async def test_multiple_acquires(self):
        counter = SlidingWindowCounter(max_per_minute=100)
        for _ in range(5):
            await counter.acquire()
        assert counter.current_count == 5


# ── DomainLimiter tests ─────────────────────────────────────────────


class TestDomainLimiter:
    def test_metrics_tracking(self):
        cfg = DomainRateLimitConfig(requests_per_second=1.0, burst_size=5)
        limiter = DomainLimiter(cfg)
        limiter.record_request()
        limiter.record_request()
        assert limiter.requests_last_minute() == 2

    def test_429_tracking(self):
        cfg = DomainRateLimitConfig()
        limiter = DomainLimiter(cfg)
        limiter.total_429s += 1
        assert limiter.total_429s == 1


# ── RateLimiterChain tests ──────────────────────────────────────────


class TestRateLimiterChain:
    @pytest.mark.asyncio
    async def test_acquire_context_manager(self):
        config = RateLimitConfig(
            defaults=DomainRateLimitConfig(
                requests_per_second=100.0,
                burst_size=10,
                max_concurrent=5,
            ),
            max_concurrent_requests=50,
            max_requests_per_minute=1000,
        )
        chain = RateLimiterChain(config)
        async with chain.acquire("https://example.com/feed.xml"):
            # should be inside the rate limiter
            assert chain.current_concurrent == 1
        # should be released
        assert chain.current_concurrent == 0

    @pytest.mark.asyncio
    async def test_domain_override(self):
        override = DomainRateLimitConfig(
            requests_per_second=0.05,
            burst_size=1,
            max_concurrent=1,
        )
        config = RateLimitConfig(
            defaults=DomainRateLimitConfig(
                requests_per_second=100.0,
                burst_size=10,
            ),
            overrides={"nvd.nist.gov": override},
            max_concurrent_requests=50,
            max_requests_per_minute=1000,
        )
        chain = RateLimiterChain(config)
        limiter = await chain._get_domain_limiter("nvd.nist.gov")
        assert limiter.config.requests_per_second == 0.05
        assert limiter.config.burst_size == 1

    def test_domain_stats(self):
        config = RateLimitConfig(
            defaults=DomainRateLimitConfig(requests_per_second=100.0, burst_size=10),
            max_concurrent_requests=50,
            max_requests_per_minute=1000,
        )
        chain = RateLimiterChain(config)
        stats = chain.domain_stats()
        assert isinstance(stats, dict)

    def test_record_429(self):
        config = RateLimitConfig(
            defaults=DomainRateLimitConfig(requests_per_second=1.0, burst_size=5),
            max_concurrent_requests=50,
            max_requests_per_minute=1000,
        )
        chain = RateLimiterChain(config)
        # pre-create domain limiter
        from periphery.rss_ingest.rate_limiter import DomainLimiter
        chain._domain_limiters["example.com"] = DomainLimiter(
            DomainRateLimitConfig(requests_per_second=1.0, burst_size=5)
        )
        chain.record_429("https://example.com/feed.xml")
        assert chain._domain_limiters["example.com"].total_429s == 1

    def test_set_crawl_delay(self):
        config = RateLimitConfig(
            defaults=DomainRateLimitConfig(requests_per_second=1.0, burst_size=5),
            max_concurrent_requests=50,
            max_requests_per_minute=1000,
        )
        chain = RateLimiterChain(config)
        # crawl delay of 20 seconds → rate of 0.05, slower than 1.0
        chain.set_crawl_delay("slow-site.com", 20.0)
        limiter = chain._domain_limiters["slow-site.com"]
        assert limiter.bucket.rate == 0.05

    @pytest.mark.asyncio
    async def test_concurrent_requests_tracked(self):
        config = RateLimitConfig(
            defaults=DomainRateLimitConfig(
                requests_per_second=100.0,
                burst_size=10,
                max_concurrent=5,
            ),
            max_concurrent_requests=50,
            max_requests_per_minute=1000,
        )
        chain = RateLimiterChain(config)

        async with chain.acquire("https://a.com/1"):
            async with chain.acquire("https://b.com/1"):
                assert chain.current_concurrent == 2
        assert chain.current_concurrent == 0


# ── PriorityScheduler tests ─────────────────────────────────────────


class TestPriorityScheduler:
    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        scheduler = PriorityScheduler()
        low = FeedConfig(url="https://low.com/feed", name="Low", category="test", priority=4)
        high = FeedConfig(url="https://high.com/feed", name="High", category="test", priority=1)
        normal = FeedConfig(url="https://normal.com/feed", name="Normal", category="test", priority=3)

        scheduler.submit(low)
        scheduler.submit(normal)
        scheduler.submit(high)

        first = await scheduler.next()
        second = await scheduler.next()
        third = await scheduler.next()

        assert first.name == "High"
        assert second.name == "Normal"
        assert third.name == "Low"

    @pytest.mark.asyncio
    async def test_fifo_same_priority(self):
        scheduler = PriorityScheduler()
        a = FeedConfig(url="https://a.com/feed", name="A", category="test", priority=2)
        b = FeedConfig(url="https://b.com/feed", name="B", category="test", priority=2)

        scheduler.submit(a)
        scheduler.submit(b)

        first = await scheduler.next()
        second = await scheduler.next()

        assert first.name == "A"
        assert second.name == "B"

    def test_pending_count(self):
        scheduler = PriorityScheduler()
        assert scheduler.pending() == 0
        feed = FeedConfig(url="https://x.com/feed", name="X", category="test")
        scheduler.submit(feed)
        assert scheduler.pending() == 1

    def test_empty(self):
        scheduler = PriorityScheduler()
        assert scheduler.empty()


# ── BackoffState model tests ────────────────────────────────────────


class TestBackoffState:
    def test_defaults(self):
        bs = BackoffState(feed_url="https://example.com/feed")
        assert bs.status == "active"
        assert bs.consecutive_failures == 0
        assert bs.total_429s_lifetime == 0
        assert bs.total_5xx_lifetime == 0

    def test_status_transitions(self):
        bs = BackoffState(feed_url="https://example.com/feed")
        bs.status = "degraded"
        assert bs.status == "degraded"
        bs.status = "dormant"
        assert bs.status == "dormant"
        bs.status = "active"
        assert bs.status == "active"


# ── FeedManager rate limit config tests ─────────────────────────────


class TestFeedManagerRateLimits:
    def _write_config(self, path: Path, content: dict) -> None:
        path.write_text(yaml.dump(content))

    def test_default_rate_limits(self, tmp_path):
        cfg = tmp_path / "feeds.yaml"
        self._write_config(cfg, {
            "feeds": [
                {"url": "https://example.com/feed", "name": "Test", "category": "test"},
            ],
        })
        fm = FeedManager(cfg)
        rl = fm.rate_limit_config
        assert rl.defaults.requests_per_second == 0.1
        assert rl.defaults.burst_size == 3
        assert rl.max_concurrent_requests == 20
        assert rl.max_requests_per_minute == 300

    def test_custom_rate_limits(self, tmp_path):
        cfg = tmp_path / "feeds.yaml"
        self._write_config(cfg, {
            "feeds": [
                {"url": "https://example.com/feed", "name": "Test", "category": "test"},
            ],
            "rate_limits": {
                "defaults": {
                    "requests_per_second": 0.5,
                    "burst_size": 10,
                    "max_concurrent_per_domain": 4,
                },
                "overrides": {
                    "special.com": {
                        "requests_per_second": 0.01,
                        "burst_size": 1,
                    },
                },
                "global_limits": {
                    "max_concurrent_requests": 50,
                    "max_requests_per_minute": 600,
                },
            },
        })
        fm = FeedManager(cfg)
        rl = fm.rate_limit_config
        assert rl.defaults.requests_per_second == 0.5
        assert rl.defaults.burst_size == 10
        assert rl.defaults.max_concurrent == 4
        assert rl.max_concurrent_requests == 50
        assert rl.max_requests_per_minute == 600
        assert "special.com" in rl.overrides
        assert rl.overrides["special.com"].requests_per_second == 0.01

    def test_bundled_config_has_rate_limits(self):
        fm = FeedManager()
        rl = fm.rate_limit_config
        assert rl.defaults.requests_per_second == 0.1
        assert "nvd.nist.gov" in rl.overrides


# ── IngestedDocument with full_content_blocked ──────────────────────


class TestIngestedDocumentBlocked:
    def test_full_content_blocked_default(self):
        doc = IngestedDocument(
            id="abc",
            source_feed="https://example.com",
            source_category="test",
            title="Test",
            url="https://example.com/1",
            content="Body text",
        )
        assert doc.full_content_blocked is False

    def test_full_content_blocked_set(self):
        doc = IngestedDocument(
            id="abc",
            source_feed="https://example.com",
            source_category="test",
            title="Test",
            url="https://example.com/1",
            content="Summary only",
            full_content_blocked=True,
        )
        assert doc.full_content_blocked is True


# ── HealthStatus model tests ────────────────────────────────────────


class TestHealthStatus:
    def test_healthy_default(self):
        h = HealthStatus(active_feeds=10)
        assert h.healthy is True
        assert h.degraded_feeds == 0
        assert h.dormant_feeds == 0

    def test_unhealthy_with_critical(self):
        h = HealthStatus(
            active_feeds=8,
            degraded_feeds=2,
            critical_feeds_non_active=["OFAC Feed"],
            healthy=False,
        )
        assert h.healthy is False
        assert len(h.critical_feeds_non_active) == 1


# ── DomainStatus model tests ────────────────────────────────────────


class TestDomainStatus:
    def test_defaults(self):
        ds = DomainStatus(domain="example.com")
        assert ds.total_429s == 0
        assert ds.total_5xx == 0
        assert ds.active_backoff is False

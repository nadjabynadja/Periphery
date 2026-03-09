"""Per-domain token bucket rate limiter and global concurrency governor.

Every outbound HTTP request passes through this chain:
    Per-Domain Rate Limiter (token bucket)
        → Global Concurrency Governor (semaphore + sliding window)
            → HTTP Request

The rate limiter is keyed by domain, not individual feed URL — a site
publishing 15 feeds still has one rate limit for its single server.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class DomainRateLimitConfig:
    """Rate limit parameters for a single domain."""

    requests_per_second: float = 0.1  # 1 request per 10 seconds
    burst_size: int = 3
    max_concurrent: int = 2


@dataclass
class RateLimitConfig:
    """Top-level rate limiting configuration."""

    defaults: DomainRateLimitConfig = field(default_factory=DomainRateLimitConfig)
    overrides: dict[str, DomainRateLimitConfig] = field(default_factory=dict)
    # global limits
    max_concurrent_requests: int = 20
    max_requests_per_minute: int = 300


def extract_domain(url: str) -> str:
    """Extract the domain (netloc) from a URL for rate-limit keying."""
    parsed = urlparse(url)
    return parsed.netloc.lower()


class TokenBucket:
    """Async-compatible token bucket rate limiter.

    Tokens refill at ``rate`` tokens per second, up to ``capacity``.
    Calling ``acquire()`` waits until a token is available.
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # compute wait time until next token
                wait = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(wait)

    @property
    def tokens(self) -> float:
        """Current approximate token count (for monitoring)."""
        self._refill()
        return self._tokens


class DomainLimiter:
    """Per-domain rate limiter: token bucket + per-domain concurrency semaphore."""

    def __init__(self, config: DomainRateLimitConfig) -> None:
        self.config = config
        self.bucket = TokenBucket(config.requests_per_second, config.burst_size)
        self.semaphore = asyncio.Semaphore(config.max_concurrent)
        # metrics
        self._request_timestamps: list[float] = []
        self.total_429s: int = 0
        self.total_5xx: int = 0
        self.active_backoff: bool = False

    def requests_last_minute(self) -> int:
        """Count requests made in the last 60 seconds."""
        cutoff = time.monotonic() - 60.0
        self._request_timestamps = [
            t for t in self._request_timestamps if t > cutoff
        ]
        return len(self._request_timestamps)

    def record_request(self) -> None:
        """Record a request timestamp for metrics."""
        self._request_timestamps.append(time.monotonic())


class SlidingWindowCounter:
    """Tracks global requests per minute with a sliding window."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until we're under the per-minute cap."""
        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - 60.0
                self._timestamps = [t for t in self._timestamps if t > cutoff]
                if len(self._timestamps) < self.max_per_minute:
                    self._timestamps.append(now)
                    return
                # wait until the oldest request expires
                wait = self._timestamps[0] - cutoff + 0.1
            await asyncio.sleep(wait)

    @property
    def current_count(self) -> int:
        cutoff = time.monotonic() - 60.0
        return sum(1 for t in self._timestamps if t > cutoff)


class RateLimiterChain:
    """Orchestrates per-domain rate limiting and global concurrency control.

    Usage::

        limiter = RateLimiterChain(config)
        async with limiter.acquire("https://example.com/feed.xml"):
            async with session.get(url) as resp:
                ...
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._domain_limiters: dict[str, DomainLimiter] = {}
        self._global_semaphore = asyncio.Semaphore(config.max_concurrent_requests)
        self._global_rpm = SlidingWindowCounter(config.max_requests_per_minute)
        self._lock = asyncio.Lock()

    def _get_domain_config(self, domain: str) -> DomainRateLimitConfig:
        """Get rate limit config for a domain, checking overrides."""
        if domain in self._config.overrides:
            return self._config.overrides[domain]
        return self._config.defaults

    async def _get_domain_limiter(self, domain: str) -> DomainLimiter:
        """Get or create the DomainLimiter for a domain."""
        if domain not in self._domain_limiters:
            async with self._lock:
                if domain not in self._domain_limiters:
                    cfg = self._get_domain_config(domain)
                    self._domain_limiters[domain] = DomainLimiter(cfg)
        return self._domain_limiters[domain]

    def set_crawl_delay(self, domain: str, delay_seconds: float) -> None:
        """Set a floor on request rate from robots.txt Crawl-delay.

        If the Crawl-delay is slower than our configured rate, adopt it.
        """
        if domain not in self._domain_limiters:
            cfg = self._get_domain_config(domain)
            self._domain_limiters[domain] = DomainLimiter(cfg)

        limiter = self._domain_limiters[domain]
        crawl_rate = 1.0 / delay_seconds if delay_seconds > 0 else float("inf")
        if crawl_rate < limiter.bucket.rate:
            logger.info(
                "crawl_delay_override",
                domain=domain,
                crawl_delay=delay_seconds,
                old_rate=limiter.bucket.rate,
                new_rate=crawl_rate,
            )
            limiter.bucket.rate = crawl_rate

    def acquire(self, url: str) -> _AcquireContext:
        """Return an async context manager that enforces the full limiter chain."""
        domain = extract_domain(url)
        return _AcquireContext(self, domain)

    # ── monitoring ──────────────────────────────────────────────────────

    @property
    def current_concurrent(self) -> int:
        """Approximate number of currently active requests."""
        sem = self._global_semaphore
        return self._config.max_concurrent_requests - sem._value

    @property
    def global_rpm(self) -> int:
        return self._global_rpm.current_count

    def domain_stats(self) -> dict[str, dict]:
        """Per-domain telemetry for the status endpoint."""
        stats: dict[str, dict] = {}
        for domain, limiter in self._domain_limiters.items():
            stats[domain] = {
                "bucket_tokens": round(limiter.bucket.tokens, 2),
                "requests_last_minute": limiter.requests_last_minute(),
                "total_429s": limiter.total_429s,
                "total_5xx": limiter.total_5xx,
                "active_backoff": limiter.active_backoff,
            }
        return stats

    def record_429(self, url: str) -> None:
        """Record a 429 response for a domain."""
        domain = extract_domain(url)
        if domain in self._domain_limiters:
            self._domain_limiters[domain].total_429s += 1

    def record_5xx(self, url: str) -> None:
        """Record a 5xx response for a domain."""
        domain = extract_domain(url)
        if domain in self._domain_limiters:
            self._domain_limiters[domain].total_5xx += 1


class _AcquireContext:
    """Async context manager for the full rate-limiter chain."""

    def __init__(self, chain: RateLimiterChain, domain: str) -> None:
        self._chain = chain
        self._domain = domain

    async def __aenter__(self) -> None:
        # 1. Per-domain token bucket
        limiter = await self._chain._get_domain_limiter(self._domain)
        await limiter.bucket.acquire()
        # 2. Per-domain concurrency
        await limiter.semaphore.acquire()
        # 3. Global requests-per-minute cap
        await self._chain._global_rpm.acquire()
        # 4. Global concurrency semaphore
        await self._chain._global_semaphore.acquire()
        # record the request for metrics
        limiter.record_request()

    async def __aexit__(self, *exc_info) -> None:
        limiter = await self._chain._get_domain_limiter(self._domain)
        limiter.semaphore.release()
        self._chain._global_semaphore.release()

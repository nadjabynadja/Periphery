"""Robots.txt compliance layer.

Checks robots.txt before full-page article fetches (not RSS feed polls,
which are explicitly published for automated consumption).

- Fetches and caches robots.txt per domain for 24 hours.
- Uses ``urllib.robotparser`` from the standard library.
- Respects ``Crawl-delay`` directives and feeds them into the rate limiter.
"""

from __future__ import annotations

import time
from urllib.robotparser import RobotFileParser

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

_CACHE_TTL = 86400  # 24 hours
_USER_AGENT = "Periphery"
_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=15)


class _CachedRobots:
    """A parsed robots.txt with an expiry timestamp."""

    __slots__ = ("parser", "fetched_at", "crawl_delay")

    def __init__(self, parser: RobotFileParser, crawl_delay: float | None) -> None:
        self.parser = parser
        self.fetched_at = time.monotonic()
        self.crawl_delay = crawl_delay

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.fetched_at) > _CACHE_TTL


class RobotsChecker:
    """Async robots.txt checker with 24-hour caching.

    Usage::

        checker = RobotsChecker(session)
        if await checker.is_allowed("https://example.com/article/123"):
            # fetch the article
            ...
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._cache: dict[str, _CachedRobots] = {}

    async def is_allowed(self, url: str) -> bool:
        """Check whether *url* is allowed by robots.txt for our user-agent.

        Returns True (allow) if the robots.txt can't be fetched or parsed.
        """
        from urllib.parse import urlparse

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        cached = self._cache.get(origin)
        if cached is None or cached.expired:
            cached = await self._fetch_robots(origin)
            self._cache[origin] = cached

        return cached.parser.can_fetch(_USER_AGENT, url)

    async def get_crawl_delay(self, url: str) -> float | None:
        """Return the Crawl-delay for the domain of *url*, if any."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        cached = self._cache.get(origin)
        if cached is None or cached.expired:
            cached = await self._fetch_robots(origin)
            self._cache[origin] = cached

        return cached.crawl_delay

    async def _fetch_robots(self, origin: str) -> _CachedRobots:
        """Fetch and parse robots.txt for *origin*."""
        robots_url = f"{origin}/robots.txt"
        parser = RobotFileParser()
        crawl_delay: float | None = None

        try:
            async with self._session.get(
                robots_url,
                timeout=_FETCH_TIMEOUT,
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    parser.parse(text.splitlines())

                    # extract Crawl-delay
                    delay = parser.crawl_delay(_USER_AGENT)
                    if delay is not None:
                        crawl_delay = float(delay)
                    else:
                        # check wildcard
                        delay = parser.crawl_delay("*")
                        if delay is not None:
                            crawl_delay = float(delay)

                    logger.debug(
                        "robots_fetched",
                        origin=origin,
                        crawl_delay=crawl_delay,
                    )
                else:
                    # no robots.txt or error → allow everything
                    parser.allow_all = True
                    logger.debug(
                        "robots_not_found",
                        origin=origin,
                        status=resp.status,
                    )
        except Exception as exc:
            # network error → allow everything (be permissive on failure)
            parser.allow_all = True
            logger.warning(
                "robots_fetch_error",
                origin=origin,
                error=str(exc),
            )

        return _CachedRobots(parser, crawl_delay)

    def cache_size(self) -> int:
        """Number of cached robots.txt entries."""
        return len(self._cache)

"""Content extraction and cleaning.

Strategy:
  1. If the feed entry has full HTML content, clean it in-process.
  2. If only a summary/link is provided, fetch the full article and
     extract with trafilatura (excellent at stripping nav/boilerplate).
  3. Fall back to BeautifulSoup plain-text extraction if trafilatura
     returns nothing.

Full-page fetches go through the rate limiter chain and robots.txt checker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp
import structlog
import trafilatura
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from .rate_limiter import RateLimiterChain
    from .robots_checker import RobotsChecker

logger = structlog.get_logger(__name__)

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=30)

_POLITE_HEADERS = {
    "User-Agent": "Periphery/0.1 (OSINT Research; https://github.com/periphery-project)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


@dataclass
class ContentResult:
    """Result of content extraction from an article fetch."""

    text: str = ""
    raw_html: str = ""
    content_quality: str = "full"  # full | summary_only | metadata_only
    blocked_by_robots: bool = False
    fetch_failed: bool = False


def clean_html(html: str) -> str:
    """Extract plain text from HTML using trafilatura, falling back to BS4."""
    if not html:
        return ""
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )
    if text:
        return text.strip()
    # fallback to BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


async def fetch_full_article(
    url: str,
    session: aiohttp.ClientSession,
    *,
    rate_limiter: RateLimiterChain | None = None,
    robots_checker: RobotsChecker | None = None,
) -> ContentResult:
    """Fetch a URL and extract article text.

    Returns a ContentResult with extracted text, raw HTML, content quality tag,
    and status flags.
    """
    # check robots.txt before article fetches
    if robots_checker is not None:
        allowed = await robots_checker.is_allowed(url)
        if not allowed:
            logger.info("robots_blocked", url=url)
            return ContentResult(
                blocked_by_robots=True,
                content_quality="metadata_only",
            )

    try:
        if rate_limiter is not None:
            async with rate_limiter.acquire(url):
                async with session.get(
                    url,
                    timeout=_FETCH_TIMEOUT,
                    headers=_POLITE_HEADERS,
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        logger.warning("article_fetch_failed", url=url, status=resp.status)
                        return ContentResult(
                            fetch_failed=True,
                            content_quality="metadata_only",
                        )
                    raw_html = await resp.text()
        else:
            async with session.get(
                url,
                timeout=_FETCH_TIMEOUT,
                headers=_POLITE_HEADERS,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    logger.warning("article_fetch_failed", url=url, status=resp.status)
                    return ContentResult(
                        fetch_failed=True,
                        content_quality="metadata_only",
                    )
                raw_html = await resp.text()
    except Exception as exc:
        logger.warning("article_fetch_error", url=url, error=str(exc))
        return ContentResult(
            fetch_failed=True,
            content_quality="metadata_only",
        )

    clean = clean_html(raw_html)
    if clean:
        return ContentResult(
            text=clean,
            raw_html=raw_html,
            content_quality="full",
        )

    # trafilatura and bs4 both failed to extract meaningful content
    return ContentResult(
        raw_html=raw_html,
        content_quality="summary_only",
    )

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


def clean_html(html: str) -> str:
    """Extract plain text from HTML using trafilatura, falling back to BS4."""
    if not html:
        return ""
    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    if text:
        return text.strip()
    # fallback
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


async def fetch_full_article(
    url: str,
    session: aiohttp.ClientSession,
    *,
    rate_limiter: RateLimiterChain | None = None,
    robots_checker: RobotsChecker | None = None,
) -> tuple[str, str, bool]:
    """Fetch a URL and extract article text.

    Returns (clean_text, raw_html, blocked_by_robots).

    If robots.txt disallows the URL, returns empty content with
    blocked_by_robots=True.
    """
    # check robots.txt before article fetches
    if robots_checker is not None:
        allowed = await robots_checker.is_allowed(url)
        if not allowed:
            logger.info("robots_blocked", url=url)
            return "", "", True

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
                        return "", "", False
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
                    return "", "", False
                raw_html = await resp.text()
    except Exception as exc:
        logger.warning("article_fetch_error", url=url, error=str(exc))
        return "", "", False

    clean = clean_html(raw_html)
    return clean, raw_html, False

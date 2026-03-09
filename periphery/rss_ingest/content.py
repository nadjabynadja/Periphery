"""Content extraction and cleaning.

Strategy:
  1. If the feed entry has full HTML content, clean it in-process.
  2. If only a summary/link is provided, fetch the full article and
     extract with trafilatura (excellent at stripping nav/boilerplate).
  3. Fall back to BeautifulSoup plain-text extraction if trafilatura
     returns nothing.
"""

from __future__ import annotations

import aiohttp
import structlog
import trafilatura
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=30)


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
) -> tuple[str, str]:
    """Fetch a URL and extract article text.

    Returns (clean_text, raw_html).
    """
    try:
        async with session.get(
            url,
            timeout=_FETCH_TIMEOUT,
            headers={"User-Agent": "Periphery/0.1 RSS Ingest"},
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                logger.warning("article_fetch_failed", url=url, status=resp.status)
                return "", ""
            raw_html = await resp.text()
    except Exception as exc:
        logger.warning("article_fetch_error", url=url, error=str(exc))
        return "", ""

    clean = clean_html(raw_html)
    return clean, raw_html

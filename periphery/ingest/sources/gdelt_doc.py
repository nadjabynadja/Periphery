"""GDELT DOC 2.0 API data source.

Polls the GDELT DOC 2.0 article search API every 15 minutes with
topic-specific queries mapped to Periphery's 8 DIC collection areas.
Returns article metadata (title, URL, source domain, language, country)
as IngestedDocument objects for enrichment.

GDELT processes global news in 65 languages, covers wire services
(Reuters, AP, AFP), and is completely free with no API key required.

LICENSE / ATTRIBUTION
---------------------
GDELT data is provided by the GDELT Project (https://www.gdeltproject.org/).
The GDELT DOC API is free for non-commercial and commercial use.

    "Powered by GDELT Project (https://www.gdeltproject.org/)"

DOC 2.0 API documentation:
    https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

# GDELT DOC 2.0 base URL
_DOC_API_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"

# 28 query sets across 8 DIC topic areas
GDELT_QUERIES: list[dict[str, str]] = [
    # --- DOMESTIC POLICY AND SECURITY ---
    {
        "category": "domestic_security",
        "query": '"DHS shutdown" OR "federal shutdown" sourcecountry:US',
    },
    {
        "category": "domestic_security",
        "query": '"executive order" OR "White House" sourcecountry:US',
    },
    {
        "category": "domestic_security",
        "query": '"FBI" OR "DOJ" OR "homeland security" sourcecountry:US',
    },
    {
        "category": "domestic_security",
        "query": '"immigration" OR "border" sourcecountry:US',
    },
    # --- LEGISLATIVE AFFAIRS ---
    {
        "category": "legislative",
        "query": '"Congress" OR "Senate" OR "House" sourcecountry:US',
    },
    {
        "category": "legislative",
        "query": '"legislation" OR "bill passed" OR "committee hearing" sourcecountry:US',
    },
    # --- INDO-PACIFIC ---
    {
        "category": "indo_pacific",
        "query": '"China" OR "Taiwan" OR "South China Sea"',
    },
    {
        "category": "indo_pacific",
        "query": '"North Korea" OR "DPRK" OR "Kim Jong"',
    },
    {
        "category": "indo_pacific",
        "query": '"India" OR "Pakistan" OR "Modi"',
    },
    {
        "category": "indo_pacific",
        "query": '"ASEAN" OR "Philippines" OR "Indonesia" OR "Vietnam"',
    },
    # --- MIDDLE EAST ---
    {
        "category": "middle_east",
        "query": '"Iran" OR "Hormuz" OR "IRGC"',
    },
    {
        "category": "middle_east",
        "query": '"Israel" OR "Gaza" OR "IDF" OR "Hezbollah"',
    },
    {
        "category": "middle_east",
        "query": '"Saudi Arabia" OR "UAE" OR "Gulf"',
    },
    {
        "category": "middle_east",
        "query": '"Syria" OR "Iraq" OR "Yemen" OR "Houthi"',
    },
    # --- AFRICA AND GLOBAL SOUTH ---
    {
        "category": "africa",
        "query": '"Sudan" OR "RSF" OR "Darfur"',
    },
    {
        "category": "africa",
        "query": '"Ethiopia" OR "Eritrea" OR "Somalia"',
    },
    {
        "category": "africa",
        "query": '"Nigeria" OR "Sahel" OR "Mali" OR "Burkina Faso"',
    },
    {
        "category": "africa",
        "query": '"South Africa" OR "DRC" OR "Congo"',
    },
    # --- WESTERN HEMISPHERE ---
    {
        "category": "western_hemisphere",
        "query": '"Mexico" OR "cartel" OR "AMLO" OR "Sheinbaum"',
    },
    {
        "category": "western_hemisphere",
        "query": '"Venezuela" OR "Maduro" OR "Colombia"',
    },
    {
        "category": "western_hemisphere",
        "query": '"Brazil" OR "Lula" OR "Argentina" OR "Milei"',
    },
    {
        "category": "western_hemisphere",
        "query": '"Canada" OR "Trudeau" OR "Carney"',
    },
    # --- EASTERN AND WESTERN EUROPE ---
    {
        "category": "europe",
        "query": '"Ukraine" OR "Zelensky" OR "Kursk"',
    },
    {
        "category": "europe",
        "query": '"Russia" OR "Putin" OR "Kremlin"',
    },
    {
        "category": "europe",
        "query": '"NATO" OR "European Union" OR "EU"',
    },
    {
        "category": "europe",
        "query": '"Germany" OR "France" OR "UK" OR "Starmer"',
    },
    # --- MULTILATERAL AND DIPLOMATIC ---
    {
        "category": "multilateral",
        "query": '"United Nations" OR "UN Security Council"',
    },
    {
        "category": "multilateral",
        "query": '"IMF" OR "World Bank" OR "WTO" OR "WHO" OR "IAEA" OR "ICC"',
    },
]


def _parse_seendate(raw: str) -> datetime | None:
    """Parse GDELT seendate format: ``YYYYMMDDTHHMMSSz``."""
    if not raw:
        return None
    try:
        # Remove trailing 'Z' or 'z' if present
        cleaned = raw.rstrip("Zz")
        return datetime.strptime(cleaned, "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc,
        )
    except (ValueError, TypeError):
        return None


class GDELTDocSource(DataSource):
    """GDELT DOC 2.0 API article ingestion source.

    Polls 28 topic-specific queries every 15 minutes, deduplicates
    by URL within each cycle, and produces IngestedDocument objects
    for the enrichment pipeline.
    """

    name = "gdelt_doc"
    category = "global_news"
    default_poll_interval = 900  # 15 minutes

    def __init__(
        self,
        *,
        poll_interval: int | None = None,
        enabled: bool = True,
        max_articles_per_query: int = 75,
        query_delay: float = 2.0,
        queries: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._max_articles = max_articles_per_query
        self._query_delay = query_delay
        self._queries = queries or GDELT_QUERIES
        # Cross-cycle URL dedup (keeps last cycle's URLs to avoid re-ingesting)
        self._seen_urls: set[str] = set()

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Poll all query sets and return deduplicated articles."""
        all_docs: list[IngestedDocument] = []
        cycle_urls: set[str] = set()

        for i, qset in enumerate(self._queries):
            try:
                docs = await self._fetch_query(
                    session, qset["query"], qset["category"],
                )
                for doc in docs:
                    if doc.url in cycle_urls or doc.url in self._seen_urls:
                        continue
                    cycle_urls.add(doc.url)
                    all_docs.append(doc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "gdelt_query_error",
                    category=qset["category"],
                    query=qset["query"][:60],
                    error=str(exc),
                )

            # Polite delay between queries (skip after last)
            if i < len(self._queries) - 1:
                await asyncio.sleep(self._query_delay)

        # Rotate seen URLs: keep only this cycle's
        self._seen_urls = cycle_urls

        logger.info(
            "gdelt_cycle_complete",
            queries=len(self._queries),
            articles=len(all_docs),
            deduped_urls=len(cycle_urls),
        )
        return all_docs

    async def _fetch_query(
        self,
        session: aiohttp.ClientSession,
        query: str,
        category: str,
    ) -> list[IngestedDocument]:
        """Execute a single GDELT DOC API query and parse results."""
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "timespan": "15min",
            "maxrecords": str(self._max_articles),
            "sort": "DateDesc",
        }

        url = f"{_DOC_API_BASE}?query={quote(query)}&mode=artlist&format=json&timespan=15min&maxrecords={self._max_articles}&sort=DateDesc"

        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning(
                    "gdelt_api_error",
                    status=resp.status,
                    category=category,
                )
                return []

            data = await resp.json(content_type=None)

        articles = data.get("articles", [])
        if not articles:
            return []

        docs: list[IngestedDocument] = []
        for article in articles:
            article_url = (article.get("url") or "").strip()
            if not article_url:
                continue

            title = (article.get("title") or "").strip()
            if not title:
                continue

            doc_id = make_document_id("gdelt_doc", article_url)
            published = _parse_seendate(article.get("seendate", ""))
            domain = article.get("domain", "")
            language = article.get("language", "")
            source_country = article.get("sourcecountry", "")
            social_image = article.get("socialimage", "")

            docs.append(
                IngestedDocument(
                    id=doc_id,
                    source_feed=f"GDELT ({category})",
                    source_category="global_news",
                    source_credibility_tier=2,
                    title=title,
                    url=article_url,
                    published=published,
                    content=title,  # MVP: title only, no full-text fetch
                    content_quality="metadata_only",
                    metadata={
                        "source_type": "gdelt_doc",
                        "gdelt_domain": domain,
                        "gdelt_language": language,
                        "gdelt_source_country": source_country,
                        "gdelt_query_category": category,
                        "gdelt_seendate": article.get("seendate", ""),
                        "gdelt_social_image": social_image,
                    },
                )
            )

        logger.debug(
            "gdelt_query_fetched",
            category=category,
            articles=len(docs),
            query=query[:60],
        )
        return docs

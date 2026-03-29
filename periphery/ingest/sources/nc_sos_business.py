"""NC Secretary of State Business Registration data source.

Fetches business entity profiles from the NC Secretary of State's
online services portal using a seed-and-expand approach.

Data source: https://www.sosnc.gov/online_services/search/Business_Registration_profile?Id={SOS_ID}
Rate limited: 1 request per 5 seconds, max 500 per day.

PUBLIC DATA NOTICE
------------------
NC Secretary of State business registration data is public record.
This source only fetches profiles for known SOS IDs (from a seed file)
and does NOT crawl or spider the website.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import aiohttp
import structlog

from periphery.rss_ingest.models import IngestedDocument

from .base import DataSource, make_document_id

logger = structlog.get_logger(__name__)

# NC SoS Business Registration profile URL template
NCSSOS_PROFILE_URL = "https://www.sosnc.gov/online_services/search/Business_Registration_profile?Id={sos_id}"

# Rate limiting
DEFAULT_REQUEST_DELAY = 5.0  # seconds between requests
DEFAULT_DAILY_LIMIT = 500


def _clean_text(text: str) -> str:
    """Clean extracted HTML text: collapse whitespace and strip."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_field(html: str, label: str) -> str:
    """Extract a field value from the SoS profile HTML by label text.

    The NC SoS profile pages use various HTML structures. This tries
    several patterns to extract the value following a label.
    """
    # Pattern 1: <span/strong/label>Label:</span/strong/label> <span>Value</span>
    patterns = [
        rf'{label}\s*:?\s*</(?:span|strong|label|th|td)>\s*<(?:span|td)[^>]*>\s*([^<]+)',
        rf'{label}\s*:?\s*</(?:div|p)>\s*<(?:div|p|span)[^>]*>\s*([^<]+)',
        rf'{label}\s*:?\s*([^<\n]+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            val = _clean_text(m.group(1))
            if val and val not in (":", ""):
                return val
    return ""


def parse_sos_profile(html: str, sos_id: str) -> dict[str, str] | None:
    """Parse a NC SoS Business Registration profile page.

    Returns a dict of extracted fields, or None if the page doesn't
    contain valid business data.
    """
    if not html or "Business Registration" not in html and "Entity" not in html:
        return None

    # Try to extract entity name from page title or heading
    entity_name = ""
    # <title>Business Registration - ENTITY NAME</title>
    m = re.search(r"<title>[^<]*?-\s*([^<]+)</title>", html, re.IGNORECASE)
    if m:
        entity_name = _clean_text(m.group(1))
    if not entity_name:
        # <h2>ENTITY NAME</h2> or similar heading
        m = re.search(r"<h[1-3][^>]*>\s*([^<]+)\s*</h[1-3]>", html, re.IGNORECASE)
        if m:
            entity_name = _clean_text(m.group(1))

    entity_type = _extract_field(html, "(?:Entity |Business |Filing )Type")
    status = _extract_field(html, "Status")
    date_formed = _extract_field(html, "(?:Date Formed|Date of Incorporation|Date Filed)")
    agent_name = _extract_field(html, "(?:Registered Agent|Agent)")
    agent_address = _extract_field(html, "(?:Agent Address|Registered Office)")
    principal_address = _extract_field(html, "(?:Principal (?:Office )?Address|Principal Office)")

    if not entity_name:
        return None

    return {
        "entity_name": entity_name,
        "entity_type": entity_type or "Unknown",
        "status": status or "Unknown",
        "date_formed": date_formed,
        "agent_name": agent_name,
        "agent_address": agent_address,
        "principal_address": principal_address,
        "sos_id": sos_id,
    }


def _build_business_content(fields: dict[str, str]) -> str:
    """Build structured text content for a business entity document."""
    lines = [
        f"Entity: {fields['entity_name']}",
        f"Type: {fields['entity_type']} | Status: {fields['status']}",
        f"SOS ID: {fields['sos_id']} | Date Formed: {fields['date_formed']}",
        f"Registered Agent: {fields['agent_name']}",
        f"Agent Address: {fields['agent_address']}",
        f"Principal Office: {fields['principal_address']}",
    ]
    return "\n".join(lines)


class NCSoSBusinessSource(DataSource):
    """Polls NC Secretary of State Business Registration profiles.

    Uses a seed-and-expand approach: fetches profiles only for known SOS
    IDs provided via a seed file. Does NOT crawl or spider the SoS website.

    Rate limited to 1 request per 5 seconds with a daily cap of 500
    requests per fetch() invocation.
    """

    name = "nc_sos_business"
    category = "business_registration"
    default_poll_interval = 604800  # weekly

    def __init__(
        self,
        *,
        seed_file: str = "",
        request_delay: float = DEFAULT_REQUEST_DELAY,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        poll_interval: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(poll_interval=poll_interval, enabled=enabled)
        self._seed_file = seed_file
        self._request_delay = request_delay
        self._daily_limit = daily_limit
        self._ingested_ids: set[str] = set()

    def _load_seed_ids(self) -> list[str]:
        """Load SOS IDs from the seed file. Returns empty list if not configured."""
        if not self._seed_file:
            logger.info("nc_sos_business_no_seed_file")
            return []

        path = Path(self._seed_file)
        if not path.exists():
            logger.warning("nc_sos_business_seed_file_missing", path=str(path))
            return []

        ids = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ids.append(line)

        logger.info("nc_sos_business_seed_loaded", count=len(ids))
        return ids

    async def fetch(self, session: aiohttp.ClientSession) -> list[IngestedDocument]:
        """Fetch NC SoS Business Registration profiles for seed SOS IDs."""
        logger.info("nc_sos_business_fetch_start")

        seed_ids = self._load_seed_ids()
        if not seed_ids:
            logger.info("nc_sos_business_no_ids_to_fetch")
            return []

        # Filter out already-ingested IDs
        new_ids = [sid for sid in seed_ids if sid not in self._ingested_ids]
        if not new_ids:
            logger.info("nc_sos_business_all_ingested")
            return []

        # Cap to daily limit
        batch_ids = new_ids[: self._daily_limit]
        logger.info(
            "nc_sos_business_fetching",
            total_seed=len(seed_ids),
            new=len(new_ids),
            batch=len(batch_ids),
        )

        docs: list[IngestedDocument] = []
        fetched = 0

        for sos_id in batch_ids:
            try:
                doc = await self._fetch_profile(session, sos_id)
                if doc is not None:
                    docs.append(doc)
                    self._ingested_ids.add(sos_id)
                fetched += 1

                # Rate limit
                await asyncio.sleep(self._request_delay)

            except Exception as exc:
                logger.error(
                    "nc_sos_business_profile_error",
                    sos_id=sos_id,
                    error=str(exc),
                )
                # Still respect rate limit on errors
                await asyncio.sleep(self._request_delay)

        # Emit in batch
        if docs:
            await self._emit(docs)

        logger.info(
            "nc_sos_business_fetch_complete",
            fetched=fetched,
            docs=len(docs),
        )
        return []  # already emitted via _emit

    async def _fetch_profile(
        self, session: aiohttp.ClientSession, sos_id: str
    ) -> IngestedDocument | None:
        """Fetch and parse a single SoS business profile."""
        url = NCSSOS_PROFILE_URL.format(sos_id=sos_id)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)

        async with session.get(url, timeout=timeout) as resp:
            resp.raise_for_status()
            html = await resp.text()

        fields = parse_sos_profile(html, sos_id)
        if fields is None:
            logger.debug("nc_sos_business_parse_failed", sos_id=sos_id)
            return None

        content = _build_business_content(fields)

        metadata: dict[str, Any] = dict(fields)
        metadata["source_type"] = "nc_sos_business"

        return IngestedDocument(
            id=make_document_id("nc_sos_business", sos_id),
            source_feed="NC Secretary of State",
            source_category="business_registration",
            source_credibility_tier=1,
            title=f"{fields['entity_name']} — {fields['entity_type']} — {fields['status']}",
            url=url,
            content=content,
            content_quality="full",
            data_classification="PUBLIC",
            metadata=metadata,
        )

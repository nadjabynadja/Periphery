"""Async polling engine.

Polls RSS/Atom feeds on their configured intervals, respects ETags and
Last-Modified headers, applies exponential backoff on failure, and
pushes cleaned entries through deduplication into the output queue.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone

import aiohttp
import feedparser
import structlog

from .content import clean_html, fetch_full_article
from .dedup import Deduplicator
from .feed_manager import FeedManager
from .models import FeedConfig, IngestedDocument
from .queue import OutputQueue

logger = structlog.get_logger(__name__)

_MAX_BACKOFF = 3600  # 1 hour ceiling
_BASE_BACKOFF = 30  # 30 seconds initial backoff
_USER_AGENT = "Periphery/0.1 RSS Ingest"


def _entry_id(entry: dict, feed_url: str) -> str:
    """Derive a stable unique ID for a feed entry."""
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    composite = f"{feed_url}:{raw}"
    return hashlib.sha256(composite.encode()).hexdigest()[:24]


def _parse_published(entry: dict) -> datetime | None:
    """Extract published datetime from a feedparser entry."""
    for field in ("published_parsed", "updated_parsed"):
        tp = entry.get(field)
        if tp:
            try:
                return datetime(*tp[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _entry_html(entry: dict) -> str:
    """Best-effort HTML content from a feed entry."""
    # content field is a list of dicts in feedparser
    content_list = entry.get("content", [])
    if content_list:
        return content_list[0].get("value", "")
    return entry.get("summary", "")


def _entry_metadata(entry: dict) -> dict:
    """Pull structured metadata from a feed entry."""
    meta: dict = {}
    if entry.get("author"):
        meta["author"] = entry["author"]
    if entry.get("tags"):
        meta["tags"] = [t.get("term", "") for t in entry["tags"] if t.get("term")]
    if entry.get("authors"):
        meta["authors"] = [a.get("name", "") for a in entry["authors"] if a.get("name")]
    return meta


class PollingEngine:
    """Async engine that polls feeds and pushes documents to the queue."""

    def __init__(
        self,
        feed_manager: FeedManager,
        deduplicator: Deduplicator,
        output_queue: OutputQueue,
        *,
        fetch_full_articles: bool = True,
    ) -> None:
        self._fm = feed_manager
        self._dedup = deduplicator
        self._queue = output_queue
        self._fetch_full = fetch_full_articles
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}
        self._session: aiohttp.ClientSession | None = None
        # metrics
        self._recent_ingests: list[float] = []  # timestamps

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start polling all configured feeds."""
        self._running = True
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT},
        )
        self._sync_tasks()
        logger.info("polling_engine_started", feed_count=len(self._fm.feeds))

    async def stop(self) -> None:
        """Gracefully stop all polling tasks."""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("polling_engine_stopped")

    def _sync_tasks(self) -> None:
        """Ensure one polling task per configured feed."""
        current_urls = {f.url for f in self._fm.feeds}
        # cancel tasks for removed feeds
        for url in list(self._tasks):
            if url not in current_urls:
                self._tasks[url].cancel()
                del self._tasks[url]
        # start tasks for new feeds
        for feed in self._fm.feeds:
            if feed.url not in self._tasks or self._tasks[feed.url].done():
                self._tasks[feed.url] = asyncio.create_task(
                    self._poll_loop(feed),
                    name=f"poll:{feed.name}",
                )

    async def refresh(self) -> None:
        """Re-sync tasks after a config reload."""
        self._sync_tasks()

    # ── per-feed poll loop ─────────────────────────────────────────────

    async def _poll_loop(self, feed: FeedConfig) -> None:
        """Long-running loop for a single feed."""
        state = self._fm.get_state(feed.url)
        while self._running:
            try:
                await self._poll_once(feed)
                state.consecutive_failures = 0
            except asyncio.CancelledError:
                return
            except Exception as exc:
                state.consecutive_failures += 1
                state.error_count += 1
                state.last_error = str(exc)
                backoff = min(
                    _BASE_BACKOFF * (2 ** (state.consecutive_failures - 1)),
                    _MAX_BACKOFF,
                )
                logger.error(
                    "poll_error",
                    feed=feed.name,
                    url=feed.url,
                    error=str(exc),
                    consecutive_failures=state.consecutive_failures,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                continue

            # sleep until next poll, re-read config in case interval changed
            cfg = self._fm.get_config(feed.url)
            interval = cfg.poll_interval if cfg else feed.poll_interval
            await asyncio.sleep(interval)

    async def _poll_once(self, feed: FeedConfig) -> None:
        """Fetch and process a single feed."""
        assert self._session is not None
        state = self._fm.get_state(feed.url)

        headers: dict[str, str] = {}
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        async with self._session.get(
            feed.url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
            allow_redirects=True,
        ) as resp:
            if resp.status == 304:
                logger.debug("feed_not_modified", feed=feed.name)
                state.last_poll = datetime.now(timezone.utc)
                return
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} from {feed.url}")

            # capture conditional headers for next request
            state.etag = resp.headers.get("ETag") or state.etag
            state.last_modified = resp.headers.get("Last-Modified") or state.last_modified

            raw_body = await resp.text()

        state.last_poll = datetime.now(timezone.utc)
        parsed = feedparser.parse(raw_body)

        new_count = 0
        for entry in parsed.entries:
            eid = _entry_id(entry, feed.url)
            html = _entry_html(entry)
            text = clean_html(html)

            # if content is too short, try fetching the full article
            link = entry.get("link", "")
            raw_html = html
            if len(text) < 200 and link and self._fetch_full:
                full_text, full_html = await fetch_full_article(link, self._session)
                if full_text:
                    text = full_text
                    raw_html = full_html

            if not text:
                continue

            if self._dedup.is_duplicate(eid, text):
                continue

            self._dedup.record(eid, text)

            doc = IngestedDocument(
                id=eid,
                source_feed=feed.url,
                source_category=feed.category,
                title=entry.get("title", ""),
                url=link,
                published=_parse_published(entry),
                content=text,
                raw_html=raw_html,
                metadata=_entry_metadata(entry),
            )

            await self._queue.put(doc)
            new_count += 1
            state.entries_ingested += 1
            self._recent_ingests.append(time.time())

        state.last_success = datetime.now(timezone.utc)
        if new_count:
            logger.info(
                "feed_polled",
                feed=feed.name,
                new_entries=new_count,
                total_entries=len(parsed.entries),
            )
        else:
            logger.debug("feed_polled_no_new", feed=feed.name)

    # ── metrics ────────────────────────────────────────────────────────

    def entries_since(self, seconds: float) -> int:
        """Count entries ingested in the last *seconds*."""
        cutoff = time.time() - seconds
        # prune old entries lazily
        self._recent_ingests = [t for t in self._recent_ingests if t > cutoff]
        return len(self._recent_ingests)

"""Async polling engine.

Polls RSS/Atom feeds on their configured intervals, respects ETags and
Last-Modified headers, applies adaptive backoff on failure, and pushes
cleaned entries through deduplication into the output queue.

Every outbound request passes through:
    Priority Scheduler → Per-Domain Rate Limiter → Global Governor → HTTP
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import feedparser
import structlog

from .content import ContentResult, clean_html, fetch_full_article
from .dedup import Deduplicator
from .feed_manager import FeedManager
from .models import BackoffState, FeedConfig, FeedState, IngestedDocument
from .priority_scheduler import PriorityScheduler
from .queue import OutputQueue
from .rate_limiter import RateLimiterChain, extract_domain

if TYPE_CHECKING:
    from .document_store import DocumentStore
    from .robots_checker import RobotsChecker

logger = structlog.get_logger(__name__)

_MAX_BACKOFF = 3600  # 1 hour ceiling
_BASE_BACKOFF_5XX = 30  # 30 seconds for server errors
_BASE_BACKOFF_429 = 60  # 60 seconds for rate limit errors
_DEGRADED_INTERVAL = 3600  # 1 hour in degraded state
_DORMANT_INTERVAL = 21600  # 6 hours in dormant state
_DEGRADED_THRESHOLD = 5  # consecutive failures before degraded
_DORMANT_THRESHOLD = 10  # consecutive failures before dormant
_304_LENGTHEN_THRESHOLD = 5  # consecutive 304s before lengthening interval

_USER_AGENT = "Periphery/0.1 (OSINT Research; https://github.com/periphery-project)"

_FEED_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

_BACKOFF_STATE_FILE = Path("/tmp/periphery_backoff_state.json")


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
    """Async engine that polls feeds through the rate limiter chain."""

    def __init__(
        self,
        feed_manager: FeedManager,
        deduplicator: Deduplicator,
        output_queue: OutputQueue,
        rate_limiter: RateLimiterChain,
        *,
        robots_checker: RobotsChecker | None = None,
        document_store: DocumentStore | None = None,
        fetch_full_articles: bool = True,
    ) -> None:
        self._fm = feed_manager
        self._dedup = deduplicator
        self._queue = output_queue
        self._rate_limiter = rate_limiter
        self._robots_checker = robots_checker
        self._document_store = document_store
        self._fetch_full = fetch_full_articles
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}
        self._session: aiohttp.ClientSession | None = None
        self._scheduler = PriorityScheduler()
        # per-feed backoff state
        self._backoff: dict[str, BackoffState] = {}
        # metrics
        self._recent_ingests: list[float] = []  # timestamps
        # first-poll-cycle summary counters
        self._first_cycle_complete = False
        self._cycle_feeds_polled = 0
        self._cycle_entries_found = 0
        self._cycle_articles_fetched = 0
        self._cycle_docs_persisted = 0
        self._cycle_failures = 0

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start polling all configured feeds."""
        self._running = True
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT},
        )
        self._load_backoff_state()
        # apply crawl-delay from robots.txt to rate limiter on first start
        if self._robots_checker:
            await self._apply_crawl_delays()
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
        self._save_backoff_state()
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

    # ── crawl delay ────────────────────────────────────────────────────

    async def _apply_crawl_delays(self) -> None:
        """Fetch robots.txt crawl-delay for all feed domains and apply to rate limiter."""
        if not self._robots_checker:
            return
        seen_domains: set[str] = set()
        for feed in self._fm.feeds:
            domain = extract_domain(feed.url)
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            try:
                delay = await self._robots_checker.get_crawl_delay(feed.url)
                if delay is not None:
                    self._rate_limiter.set_crawl_delay(domain, delay)
            except Exception:
                pass  # non-critical, move on

    # ── backoff state ──────────────────────────────────────────────────

    def _get_backoff(self, feed: FeedConfig) -> BackoffState:
        if feed.url not in self._backoff:
            self._backoff[feed.url] = BackoffState(
                feed_url=feed.url,
                domain=extract_domain(feed.url),
            )
        return self._backoff[feed.url]

    def get_all_backoff_states(self) -> list[BackoffState]:
        return list(self._backoff.values())

    def _save_backoff_state(self) -> None:
        """Persist backoff state to disk for crash recovery."""
        try:
            data = [bs.model_dump(mode="json") for bs in self._backoff.values()]
            _BACKOFF_STATE_FILE.write_text(json.dumps(data, default=str))
        except Exception as exc:
            logger.warning("backoff_state_save_failed", error=str(exc))

    def _load_backoff_state(self) -> None:
        """Load backoff state from disk if available."""
        if not _BACKOFF_STATE_FILE.exists():
            return
        try:
            data = json.loads(_BACKOFF_STATE_FILE.read_text())
            for entry in data:
                # convert datetime strings back
                for dt_field in ("next_allowed_poll",):
                    if entry.get(dt_field):
                        entry[dt_field] = datetime.fromisoformat(entry[dt_field])
                bs = BackoffState(**entry)
                self._backoff[bs.feed_url] = bs
            logger.info("backoff_state_loaded", count=len(self._backoff))
        except Exception as exc:
            logger.warning("backoff_state_load_failed", error=str(exc))

    # ── per-feed poll loop ─────────────────────────────────────────────

    async def _poll_loop(self, feed: FeedConfig) -> None:
        """Long-running loop for a single feed."""
        state = self._fm.get_state(feed.url)
        backoff = self._get_backoff(feed)

        while self._running:
            # respect backoff: if next_allowed_poll is in the future, wait
            now = datetime.now(timezone.utc)
            if backoff.next_allowed_poll and backoff.next_allowed_poll > now:
                wait = (backoff.next_allowed_poll - now).total_seconds()
                logger.debug(
                    "backoff_waiting",
                    feed=feed.name,
                    status=backoff.status,
                    wait_seconds=round(wait, 1),
                )
                await asyncio.sleep(wait)
                continue

            try:
                await self._poll_once(feed)
                # success — reset backoff
                state.consecutive_failures = 0
                backoff.consecutive_failures = 0
                backoff.current_backoff_seconds = 0.0
                backoff.last_failure_type = None
                if backoff.status != "active":
                    logger.info(
                        "feed_recovered",
                        feed=feed.name,
                        previous_status=backoff.status,
                    )
                    backoff.status = "active"

            except asyncio.CancelledError:
                return

            except _RateLimitError as exc:
                self._handle_429(feed, state, backoff, exc)
                continue

            except _ServerError as exc:
                self._handle_5xx(feed, state, backoff, exc)
                continue

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                self._handle_connection_error(feed, state, backoff, exc)
                continue

            except Exception as exc:
                self._handle_generic_error(feed, state, backoff, exc)
                continue

            # sleep until next poll
            interval = self._effective_interval(feed, backoff)
            await asyncio.sleep(interval)

    def _effective_interval(self, feed: FeedConfig, backoff: BackoffState) -> float:
        """Compute the effective poll interval considering feed status."""
        cfg = self._fm.get_config(feed.url)
        base = cfg.poll_interval if cfg else feed.poll_interval

        if backoff.status == "degraded":
            return max(base, _DEGRADED_INTERVAL)
        if backoff.status == "dormant":
            return max(base, _DORMANT_INTERVAL)

        # if consistently getting 304, lengthen interval
        if backoff.consecutive_304s >= _304_LENGTHEN_THRESHOLD:
            return min(base * 2, 3600)  # at most 1 hour

        return base

    # ── error handlers ─────────────────────────────────────────────────

    def _handle_429(
        self,
        feed: FeedConfig,
        state: FeedState,
        backoff: BackoffState,
        exc: _RateLimitError,
    ) -> None:
        state.consecutive_failures += 1
        state.error_count += 1
        state.last_error = str(exc)
        backoff.consecutive_failures += 1
        backoff.last_failure_type = "429"
        backoff.total_429s_lifetime += 1
        self._rate_limiter.record_429(feed.url)

        if exc.retry_after is not None:
            wait = exc.retry_after
        else:
            wait = min(
                _BASE_BACKOFF_429 * (2 ** (backoff.consecutive_failures - 1)),
                _MAX_BACKOFF,
            )

        backoff.current_backoff_seconds = wait
        backoff.next_allowed_poll = datetime.now(timezone.utc) + timedelta(seconds=wait)

        logger.warning(
            "rate_limited_429",
            feed=feed.name,
            retry_after=exc.retry_after,
            backoff_seconds=wait,
            consecutive_failures=backoff.consecutive_failures,
        )

    def _handle_5xx(
        self,
        feed: FeedConfig,
        state: FeedState,
        backoff: BackoffState,
        exc: _ServerError,
    ) -> None:
        state.consecutive_failures += 1
        state.error_count += 1
        state.last_error = str(exc)
        backoff.consecutive_failures += 1
        backoff.last_failure_type = "5xx"
        backoff.total_5xx_lifetime += 1
        self._rate_limiter.record_5xx(feed.url)

        wait = min(
            _BASE_BACKOFF_5XX * (2 ** (backoff.consecutive_failures - 1)),
            _MAX_BACKOFF,
        )
        backoff.current_backoff_seconds = wait
        backoff.next_allowed_poll = datetime.now(timezone.utc) + timedelta(seconds=wait)

        # degrade after threshold
        if backoff.consecutive_failures >= _DEGRADED_THRESHOLD and backoff.status == "active":
            backoff.status = "degraded"
            logger.warning("feed_degraded", feed=feed.name, failures=backoff.consecutive_failures)

        logger.error(
            "server_error",
            feed=feed.name,
            status=exc.status_code,
            backoff_seconds=wait,
            status_state=backoff.status,
        )

    def _handle_connection_error(
        self,
        feed: FeedConfig,
        state: FeedState,
        backoff: BackoffState,
        exc: Exception,
    ) -> None:
        state.consecutive_failures += 1
        state.error_count += 1
        state.last_error = str(exc)
        backoff.consecutive_failures += 1
        backoff.last_failure_type = "connection"

        wait = min(
            _BASE_BACKOFF_5XX * (2 ** (backoff.consecutive_failures - 1)),
            _MAX_BACKOFF,
        )
        backoff.current_backoff_seconds = wait
        backoff.next_allowed_poll = datetime.now(timezone.utc) + timedelta(seconds=wait)

        # degrade at 5, dormant at 10
        if backoff.consecutive_failures >= _DORMANT_THRESHOLD and backoff.status != "dormant":
            backoff.status = "dormant"
            logger.error(
                "feed_dormant",
                feed=feed.name,
                failures=backoff.consecutive_failures,
            )
        elif backoff.consecutive_failures >= _DEGRADED_THRESHOLD and backoff.status == "active":
            backoff.status = "degraded"
            logger.warning("feed_degraded", feed=feed.name, failures=backoff.consecutive_failures)

        logger.error(
            "connection_error",
            feed=feed.name,
            error=str(exc),
            backoff_seconds=wait,
            status=backoff.status,
        )

    def _handle_generic_error(
        self,
        feed: FeedConfig,
        state: FeedState,
        backoff: BackoffState,
        exc: Exception,
    ) -> None:
        state.consecutive_failures += 1
        state.error_count += 1
        state.last_error = str(exc)
        backoff.consecutive_failures += 1
        backoff.last_failure_type = "error"

        wait = min(
            _BASE_BACKOFF_5XX * (2 ** (backoff.consecutive_failures - 1)),
            _MAX_BACKOFF,
        )
        backoff.current_backoff_seconds = wait
        backoff.next_allowed_poll = datetime.now(timezone.utc) + timedelta(seconds=wait)

        logger.error(
            "poll_error",
            feed=feed.name,
            url=feed.url,
            error=str(exc),
            consecutive_failures=backoff.consecutive_failures,
            backoff=wait,
        )

    # ── single poll ────────────────────────────────────────────────────

    async def _poll_once(self, feed: FeedConfig) -> None:
        """Fetch and process a single feed through the rate limiter chain."""
        assert self._session is not None
        state = self._fm.get_state(feed.url)
        backoff = self._get_backoff(feed)

        headers = dict(_FEED_HEADERS)
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        # go through the rate limiter chain
        async with self._rate_limiter.acquire(feed.url):
            async with self._session.get(
                feed.url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as resp:
                if resp.status == 304:
                    logger.debug("feed_not_modified", feed=feed.name)
                    state.last_poll = datetime.now(timezone.utc)
                    backoff.consecutive_304s += 1
                    return

                if resp.status == 429:
                    retry_after = self._parse_retry_after(resp)
                    raise _RateLimitError(feed.url, retry_after)

                if 500 <= resp.status < 600:
                    raise _ServerError(feed.url, resp.status)

                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status} from {feed.url}")

                # capture conditional headers for next request
                state.etag = resp.headers.get("ETag") or state.etag
                state.last_modified = resp.headers.get("Last-Modified") or state.last_modified

                raw_body = await resp.text()

        # reset 304 counter on success
        backoff.consecutive_304s = 0
        state.last_poll = datetime.now(timezone.utc)
        parsed = feedparser.parse(raw_body)

        new_count = 0
        self._cycle_feeds_polled += 1
        self._cycle_entries_found += len(parsed.entries)

        for entry in parsed.entries:
            eid = _entry_id(entry, feed.url)
            link = entry.get("link", "")
            summary_html = _entry_html(entry)
            summary_text = clean_html(summary_html)

            # check dedup against SQLite (survives restarts) + in-memory
            if await self._dedup.is_known(eid, link):
                logger.debug("dedup_skip", entry_id=eid, feed=feed.name)
                continue

            # determine content quality and fetch full article if needed
            content_quality = "full"
            text = summary_text
            raw_html = summary_html
            full_content_blocked = False

            if len(summary_text) < 200 and link and self._fetch_full:
                result: ContentResult = await fetch_full_article(
                    link,
                    self._session,
                    rate_limiter=self._rate_limiter,
                    robots_checker=self._robots_checker,
                )

                if result.blocked_by_robots:
                    full_content_blocked = True
                    content_quality = "metadata_only"
                elif result.fetch_failed:
                    # fetch failed (timeout, 403, paywall) — keep summary
                    content_quality = "metadata_only" if not summary_text else "summary_only"
                    self._cycle_failures += 1
                elif result.text:
                    text = result.text
                    raw_html = result.raw_html
                    content_quality = "full"
                    self._cycle_articles_fetched += 1
                else:
                    # trafilatura + bs4 both failed on fetched HTML
                    content_quality = "summary_only" if summary_text else "metadata_only"
            elif text:
                # we have inline content from the feed
                content_quality = "full" if len(text) >= 200 else "summary_only"
            else:
                # no text at all
                content_quality = "metadata_only"

            # even metadata_only documents are worth persisting
            if not text and content_quality == "metadata_only":
                text = ""  # explicit empty — still persist

            # full dedup check including content hash
            if text and await self._dedup.is_duplicate_persistent(eid, link, text):
                logger.debug("dedup_content_skip", entry_id=eid, feed=feed.name)
                continue

            self._dedup.record(eid, text if text else eid)

            doc = IngestedDocument(
                id=eid,
                source_feed=feed.url,
                source_category=feed.category,
                source_credibility_tier=feed.priority,
                title=entry.get("title", ""),
                url=link,
                published=_parse_published(entry),
                content=text,
                raw_html=raw_html,
                summary=summary_text,
                content_quality=content_quality,
                full_content_blocked=full_content_blocked,
                metadata=_entry_metadata(entry),
            )

            # persist to SQLite immediately — durability over throughput
            if self._document_store is not None:
                try:
                    inserted = await self._document_store.insert(doc)
                    if inserted:
                        await self._document_store.enqueue_for_enrichment(doc.id)
                        self._cycle_docs_persisted += 1
                    else:
                        # already in DB (race or restart)
                        continue
                except Exception as exc:
                    logger.error(
                        "document_persist_failed",
                        doc_id=doc.id,
                        error=str(exc),
                    )
                    self._cycle_failures += 1

            await self._queue.put(doc)
            new_count += 1
            state.entries_ingested += 1
            self._recent_ingests.append(time.time())

        state.last_success = datetime.now(timezone.utc)

        # log first cycle summary after first successful poll
        if not self._first_cycle_complete:
            self._first_cycle_complete = True
            logger.info(
                "first_poll_cycle_summary",
                feeds_polled=self._cycle_feeds_polled,
                entries_found=self._cycle_entries_found,
                articles_fetched=self._cycle_articles_fetched,
                docs_persisted=self._cycle_docs_persisted,
                failures=self._cycle_failures,
            )

        if new_count:
            logger.info(
                "feed_polled",
                feed=feed.name,
                new_entries=new_count,
                total_entries=len(parsed.entries),
            )
        else:
            logger.debug("feed_polled_no_new", feed=feed.name)

    @staticmethod
    def _parse_retry_after(resp: aiohttp.ClientResponse) -> float | None:
        """Parse the Retry-After header (seconds or HTTP-date)."""
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return None
        # try as integer seconds
        try:
            return float(raw)
        except ValueError:
            pass
        # try as HTTP-date
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(raw)
            delta = (dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delta)
        except Exception:
            return None

    # ── metrics ────────────────────────────────────────────────────────

    def entries_since(self, seconds: float) -> int:
        """Count entries ingested in the last *seconds*."""
        cutoff = time.time() - seconds
        self._recent_ingests = [t for t in self._recent_ingests if t > cutoff]
        return len(self._recent_ingests)

    def feeds_by_status(self) -> dict[str, int]:
        """Count feeds in each backoff status."""
        counts = {"active": 0, "degraded": 0, "dormant": 0}
        for bs in self._backoff.values():
            counts[bs.status] = counts.get(bs.status, 0) + 1
        return counts

    def get_alerts(self) -> list[str]:
        """Generate alerts for feeds in bad state too long."""
        alerts: list[str] = []
        now = datetime.now(timezone.utc)

        for bs in self._backoff.values():
            cfg = self._fm.get_config(bs.feed_url)
            feed_name = cfg.name if cfg else bs.feed_url

            if bs.status == "degraded" and bs.next_allowed_poll:
                # alert if degraded > 24 hours (check via lifetime counters)
                total_backoff = bs.total_429s_lifetime + bs.total_5xx_lifetime
                if bs.consecutive_failures >= 10:
                    alerts.append(
                        f"DEGRADED >24h: {feed_name} ({bs.consecutive_failures} consecutive failures)"
                    )

            if bs.status == "dormant":
                alerts.append(
                    f"DORMANT: {feed_name} ({bs.consecutive_failures} consecutive failures)"
                )

            # alert if critical feed is non-active
            if cfg and cfg.priority == 1 and bs.status != "active":
                alerts.append(
                    f"CRITICAL feed non-active: {feed_name} (status={bs.status})"
                )

        return alerts


class _RateLimitError(Exception):
    """Raised when a feed returns HTTP 429."""

    def __init__(self, url: str, retry_after: float | None) -> None:
        self.url = url
        self.retry_after = retry_after
        super().__init__(f"429 Too Many Requests from {url}")


class _ServerError(Exception):
    """Raised when a feed returns HTTP 5xx."""

    def __init__(self, url: str, status_code: int) -> None:
        self.url = url
        self.status_code = status_code
        super().__init__(f"HTTP {status_code} from {url}")

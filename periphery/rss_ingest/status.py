"""FastAPI status endpoint for the RSS ingest daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from .models import DaemonStatus, FeedState

if TYPE_CHECKING:
    from .daemon import RSSIngestDaemon

router = APIRouter(prefix="/rss", tags=["rss-ingest"])

_daemon_ref: RSSIngestDaemon | None = None


def register_daemon(daemon: RSSIngestDaemon) -> None:
    """Bind the running daemon so the endpoint can read its state."""
    global _daemon_ref  # noqa: PLW0603
    _daemon_ref = daemon


@router.get("/status", response_model=DaemonStatus)
async def rss_status() -> DaemonStatus:
    if _daemon_ref is None:
        return DaemonStatus()

    d = _daemon_ref
    states: list[FeedState] = d.feed_manager.all_states()

    return DaemonStatus(
        active_feeds=len(d.feed_manager.feeds),
        feeds=states,
        entries_last_hour=d.poller.entries_since(3600),
        entries_last_day=d.poller.entries_since(86400),
        total_entries_ingested=sum(s.entries_ingested for s in states),
        queue_depth=d.output_queue.depth(),
        uptime_seconds=d.uptime,
    )


@router.post("/reload")
async def reload_feeds() -> dict:
    """Hot-reload feed configuration from disk."""
    if _daemon_ref is None:
        return {"ok": False, "error": "daemon not running"}
    _daemon_ref.feed_manager.reload()
    await _daemon_ref.poller.refresh()
    return {"ok": True, "feeds": len(_daemon_ref.feed_manager.feeds)}

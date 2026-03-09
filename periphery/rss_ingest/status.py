"""FastAPI status and health endpoints for the RSS ingest daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from .models import BackoffState, DaemonStatus, DomainStatus, FeedState, HealthStatus

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

    # rate limiting telemetry
    domain_stats: dict[str, DomainStatus] = {}
    if d.rate_limiter:
        for domain, stats in d.rate_limiter.domain_stats().items():
            domain_stats[domain] = DomainStatus(
                domain=domain,
                bucket_tokens=stats["bucket_tokens"],
                requests_last_minute=stats["requests_last_minute"],
                total_429s=stats["total_429s"],
                total_5xx=stats["total_5xx"],
                active_backoff=stats["active_backoff"],
            )

    # backoff states
    backoff_states = d.poller.get_all_backoff_states()
    status_counts = d.poller.feeds_by_status()
    alerts = d.poller.get_alerts()

    return DaemonStatus(
        active_feeds=len(d.feed_manager.feeds),
        feeds=states,
        entries_last_hour=d.poller.entries_since(3600),
        entries_last_day=d.poller.entries_since(86400),
        total_entries_ingested=sum(s.entries_ingested for s in states),
        queue_depth=d.output_queue.depth(),
        uptime_seconds=d.uptime,
        current_concurrent_requests=d.rate_limiter.current_concurrent if d.rate_limiter else 0,
        global_requests_per_minute=d.rate_limiter.global_rpm if d.rate_limiter else 0,
        degraded_feed_count=status_counts.get("degraded", 0),
        dormant_feed_count=status_counts.get("dormant", 0),
        domain_stats=domain_stats,
        backoff_states=backoff_states,
        alerts=alerts,
    )


@router.get("/health", response_model=HealthStatus)
async def rss_health() -> HealthStatus:
    """Aggregate health check for the RSS daemon."""
    if _daemon_ref is None:
        return HealthStatus(healthy=False)

    d = _daemon_ref
    status_counts = d.poller.feeds_by_status()

    # find critical feeds (priority 1) in non-active state
    critical_non_active: list[str] = []
    for bs in d.poller.get_all_backoff_states():
        if bs.status != "active":
            cfg = d.feed_manager.get_config(bs.feed_url)
            if cfg and cfg.priority == 1:
                critical_non_active.append(cfg.name)

    healthy = len(critical_non_active) == 0

    return HealthStatus(
        active_feeds=status_counts.get("active", 0),
        degraded_feeds=status_counts.get("degraded", 0),
        dormant_feeds=status_counts.get("dormant", 0),
        critical_feeds_non_active=critical_non_active,
        healthy=healthy,
    )


@router.post("/reload")
async def reload_feeds() -> dict:
    """Hot-reload feed configuration from disk."""
    if _daemon_ref is None:
        return {"ok": False, "error": "daemon not running"}
    _daemon_ref.feed_manager.reload()
    await _daemon_ref.poller.refresh()
    return {"ok": True, "feeds": len(_daemon_ref.feed_manager.feeds)}

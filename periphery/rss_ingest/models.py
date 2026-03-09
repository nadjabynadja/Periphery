"""Pydantic models for RSS ingest pipeline."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeedConfig(BaseModel):
    """Configuration for a single RSS/Atom feed."""

    url: str
    name: str
    category: str
    poll_interval: int = 300  # seconds, default 5 minutes
    priority: int = 3  # 1 = highest, 5 = lowest


class FeedState(BaseModel):
    """Runtime state tracked per feed."""

    url: str
    etag: str | None = None
    last_modified: str | None = None
    last_poll: datetime | None = None
    last_success: datetime | None = None
    consecutive_failures: int = 0
    entries_ingested: int = 0
    error_count: int = 0
    last_error: str | None = None


class BackoffState(BaseModel):
    """Adaptive backoff state tracked per feed."""

    feed_url: str
    domain: str = ""
    consecutive_failures: int = 0
    last_failure_type: str | None = None
    current_backoff_seconds: float = 0.0
    next_allowed_poll: datetime | None = None
    status: str = "active"  # active | degraded | dormant
    total_429s_lifetime: int = 0
    total_5xx_lifetime: int = 0
    consecutive_304s: int = 0


class IngestedDocument(BaseModel):
    """Standardized document produced by the RSS ingest pipeline."""

    id: str
    source_feed: str
    source_category: str
    source_credibility_tier: int = 3
    title: str
    url: str
    published: datetime | None = None
    ingested: datetime = Field(default_factory=_utcnow)
    content: str
    raw_html: str = ""
    summary: str = ""
    content_quality: str = "full"  # full | summary_only | metadata_only
    full_content_blocked: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    enrichment_status: str = "pending"  # pending | in_progress | complete | failed
    embedding_status: str = "pending"  # pending | complete | failed


class DomainStatus(BaseModel):
    """Rate limiting status for a single domain."""

    domain: str
    bucket_tokens: float = 0.0
    requests_last_minute: int = 0
    total_429s: int = 0
    total_5xx: int = 0
    active_backoff: bool = False


class HealthStatus(BaseModel):
    """Aggregate health status for the /health endpoint."""

    active_feeds: int = 0
    degraded_feeds: int = 0
    dormant_feeds: int = 0
    critical_feeds_non_active: list[str] = Field(default_factory=list)
    healthy: bool = True


class DaemonStatus(BaseModel):
    """Status snapshot for the monitoring endpoint."""

    active_feeds: int = 0
    feeds: list[FeedState] = Field(default_factory=list)
    entries_last_hour: int = 0
    entries_last_day: int = 0
    total_entries_ingested: int = 0
    queue_depth: int = 0
    uptime_seconds: float = 0.0
    # rate limiting telemetry
    current_concurrent_requests: int = 0
    global_requests_per_minute: int = 0
    degraded_feed_count: int = 0
    dormant_feed_count: int = 0
    domain_stats: dict[str, DomainStatus] = Field(default_factory=dict)
    backoff_states: list[BackoffState] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)

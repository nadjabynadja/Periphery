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


class IngestedDocument(BaseModel):
    """Standardized document produced by the RSS ingest pipeline."""

    id: str
    source_feed: str
    source_category: str
    title: str
    url: str
    published: datetime | None = None
    ingested: datetime = Field(default_factory=_utcnow)
    content: str
    raw_html: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DaemonStatus(BaseModel):
    """Status snapshot for the monitoring endpoint."""

    active_feeds: int = 0
    feeds: list[FeedState] = Field(default_factory=list)
    entries_last_hour: int = 0
    entries_last_day: int = 0
    total_entries_ingested: int = 0
    queue_depth: int = 0
    uptime_seconds: float = 0.0

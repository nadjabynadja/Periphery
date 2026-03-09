"""Feed Manager — loads, validates, and hot-reloads feed configurations."""

from __future__ import annotations

import os
from pathlib import Path

import structlog
import yaml

from .models import FeedConfig, FeedState

logger = structlog.get_logger(__name__)

_DEFAULT_CONFIG = Path(__file__).parent / "feeds.yaml"


class FeedManager:
    """Manages feed configurations and per-feed runtime state.

    Supports runtime reload: call ``reload()`` to pick up config changes
    without restarting the daemon.  New feeds are added, removed feeds are
    dropped, and existing feeds keep their runtime state (ETags, backoff
    counters, etc.).
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = Path(
            config_path
            or os.environ.get("PERIPHERY_FEEDS_CONFIG", "")
            or _DEFAULT_CONFIG
        )
        self._feeds: dict[str, FeedConfig] = {}
        self._states: dict[str, FeedState] = {}
        self.reload()

    # ── public API ──────────────────────────────────────────────────────

    def reload(self) -> None:
        """(Re)load feed configs from the YAML file."""
        raw = self._config_path.read_text()
        data = yaml.safe_load(raw)
        entries = data.get("feeds", [])

        new_feeds: dict[str, FeedConfig] = {}
        for entry in entries:
            fc = FeedConfig(**entry)
            new_feeds[fc.url] = fc

        # reconcile states: keep existing, add new, drop removed
        removed = set(self._feeds) - set(new_feeds)
        added = set(new_feeds) - set(self._feeds)

        for url in removed:
            self._states.pop(url, None)
        for url in added:
            self._states[url] = FeedState(url=url)

        self._feeds = new_feeds
        logger.info(
            "feeds_loaded",
            total=len(self._feeds),
            added=len(added),
            removed=len(removed),
            config_path=str(self._config_path),
        )

    @property
    def feeds(self) -> list[FeedConfig]:
        return list(self._feeds.values())

    def get_config(self, url: str) -> FeedConfig | None:
        return self._feeds.get(url)

    def get_state(self, url: str) -> FeedState:
        if url not in self._states:
            self._states[url] = FeedState(url=url)
        return self._states[url]

    def all_states(self) -> list[FeedState]:
        return list(self._states.values())

    def feeds_by_category(self, category: str) -> list[FeedConfig]:
        return [f for f in self._feeds.values() if f.category == category]

    @property
    def categories(self) -> list[str]:
        return sorted({f.category for f in self._feeds.values()})

    # ── runtime mutations (add/remove without touching YAML) ───────────

    def add_feed(self, config: FeedConfig) -> None:
        self._feeds[config.url] = config
        if config.url not in self._states:
            self._states[config.url] = FeedState(url=config.url)
        logger.info("feed_added", url=config.url, category=config.category)

    def remove_feed(self, url: str) -> bool:
        if url in self._feeds:
            del self._feeds[url]
            self._states.pop(url, None)
            logger.info("feed_removed", url=url)
            return True
        return False

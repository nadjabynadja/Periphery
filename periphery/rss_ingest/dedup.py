"""Deduplication layer — in-memory rolling hash sets with SQLite backing.

Deduplicates on two axes:
  1. Feed-provided entry IDs / GUIDs.
  2. Content hashes (SHA-256 of cleaned text body) to catch the same
     wire story syndicated across many feeds.

The in-memory layer uses plain Python sets with a bounded size and LRU-style
eviction via an ``OrderedDict``.  The SQLite-backed check queries the document
store so deduplication survives daemon restarts.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .document_store import DocumentStore

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_SIZE = 500_000  # entries


class DeduplicationStore:
    """Bounded dedup store backed by an OrderedDict (LRU eviction)."""

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._max_size = max_size
        self._seen: OrderedDict[str, None] = OrderedDict()

    # ── public API ──────────────────────────────────────────────────────

    def is_seen(self, key: str) -> bool:
        """Return True if *key* has already been recorded."""
        if key in self._seen:
            # refresh position
            self._seen.move_to_end(key)
            return True
        return False

    def mark_seen(self, key: str) -> None:
        """Record *key* and evict oldest entries if over capacity."""
        if key in self._seen:
            self._seen.move_to_end(key)
            return
        self._seen[key] = None
        while len(self._seen) > self._max_size:
            evicted, _ = self._seen.popitem(last=False)
            logger.debug("dedup_evicted", key=evicted)

    def __len__(self) -> int:
        return len(self._seen)


class Deduplicator:
    """Two-layer deduplicator: entry ID + content hash, with optional SQLite backing."""

    def __init__(
        self,
        max_size: int = _DEFAULT_MAX_SIZE,
        document_store: DocumentStore | None = None,
    ) -> None:
        self._id_store = DeduplicationStore(max_size)
        self._content_store = DeduplicationStore(max_size)
        self._document_store = document_store

    @staticmethod
    def content_hash(text: str) -> str:
        """SHA-256 hex digest of normalized text."""
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    def is_duplicate(self, entry_id: str, content: str) -> bool:
        """Check if an entry is a duplicate by ID or content hash (in-memory only)."""
        if self._id_store.is_seen(entry_id):
            return True
        chash = self.content_hash(content)
        if self._content_store.is_seen(chash):
            return True
        return False

    async def is_duplicate_persistent(self, entry_id: str, url: str, content: str) -> bool:
        """Check dedup against both in-memory store and SQLite.

        This is the primary dedup check — it survives daemon restarts because
        it queries the durable document store.
        """
        # fast in-memory check first
        if self._id_store.is_seen(entry_id):
            return True
        chash = self.content_hash(content)
        if self._content_store.is_seen(chash):
            return True
        # check SQLite if available
        if self._document_store is not None:
            if await self._document_store.is_duplicate(entry_id, url):
                # populate in-memory cache so future checks are fast
                self._id_store.mark_seen(entry_id)
                return True
        return False

    async def is_known(self, entry_id: str, url: str) -> bool:
        """Quick check if an entry ID or URL is already known (in-memory + SQLite).

        Used before fetching article content — avoids wasting HTTP requests
        on documents we already have.
        """
        if self._id_store.is_seen(entry_id):
            return True
        if self._document_store is not None:
            if await self._document_store.is_duplicate(entry_id, url):
                self._id_store.mark_seen(entry_id)
                return True
        return False

    def record(self, entry_id: str, content: str) -> None:
        """Mark an entry as seen."""
        self._id_store.mark_seen(entry_id)
        chash = self.content_hash(content)
        self._content_store.mark_seen(chash)

    @property
    def size(self) -> int:
        return len(self._id_store) + len(self._content_store)

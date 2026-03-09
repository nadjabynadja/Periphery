"""Deduplication layer — in-memory rolling hash sets.

Deduplicates on two axes:
  1. Feed-provided entry IDs / GUIDs.
  2. Content hashes (SHA-256 of cleaned text body) to catch the same
     wire story syndicated across many feeds.

The implementation uses plain Python sets with a bounded size and LRU-style
eviction via an ``OrderedDict``.  This is simple, correct, and sufficient for
single-process operation.  For multi-process / distributed deployments,
swap to a Redis set behind the same interface.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import structlog

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
    """Two-layer deduplicator: entry ID + content hash."""

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._id_store = DeduplicationStore(max_size)
        self._content_store = DeduplicationStore(max_size)

    @staticmethod
    def content_hash(text: str) -> str:
        """SHA-256 hex digest of normalized text."""
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    def is_duplicate(self, entry_id: str, content: str) -> bool:
        """Check if an entry is a duplicate by ID or content hash."""
        if self._id_store.is_seen(entry_id):
            return True
        chash = self.content_hash(content)
        if self._content_store.is_seen(chash):
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

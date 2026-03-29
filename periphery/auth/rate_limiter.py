"""In-memory rate limiting for API keys and failed auth tracking.

No external dependencies (Redis, etc.) — uses simple dicts with
time-based expiration. Suitable for single-process deployments.
"""

from __future__ import annotations

import logging
import time
import threading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-key rate limiter (requests per minute)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Sliding-window rate limiter keyed by arbitrary string IDs."""

    def __init__(self) -> None:
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.monotonic()
        window = 60.0  # 1-minute sliding window

        with self._lock:
            timestamps = self._buckets.get(key, [])
            # Prune expired entries
            timestamps = [t for t in timestamps if now - t < window]

            if len(timestamps) >= limit:
                self._buckets[key] = timestamps
                return False

            timestamps.append(now)
            self._buckets[key] = timestamps
            return True

    def remaining(self, key: str, limit: int) -> int:
        """Return how many requests remain in the current window."""
        now = time.monotonic()
        window = 60.0

        with self._lock:
            timestamps = self._buckets.get(key, [])
            active = [t for t in timestamps if now - t < window]
            return max(0, limit - len(active))

    def cleanup(self) -> None:
        """Remove stale entries. Call periodically if needed."""
        now = time.monotonic()
        with self._lock:
            stale_keys = []
            for key, timestamps in self._buckets.items():
                active = [t for t in timestamps if now - t < 60.0]
                if not active:
                    stale_keys.append(key)
                else:
                    self._buckets[key] = active
            for key in stale_keys:
                del self._buckets[key]


# ---------------------------------------------------------------------------
# Failed auth tracker (per-IP brute-force protection)
# ---------------------------------------------------------------------------

class FailedAuthTracker:
    """Track failed authentication attempts per IP address.

    After `max_failures` failures within `window_seconds`, the IP is
    blocked until the window expires.
    """

    def __init__(
        self,
        max_failures: int = 10,
        window_seconds: float = 900.0,  # 15 minutes
    ) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def record_failure(self, ip: str) -> None:
        """Record a failed auth attempt from the given IP."""
        now = time.monotonic()
        with self._lock:
            timestamps = self._failures.get(ip, [])
            timestamps = [t for t in timestamps if now - t < self._window]
            timestamps.append(now)
            self._failures[ip] = timestamps
        logger.warning("auth_failure_recorded ip=%s count=%d", ip, len(timestamps))

    def is_blocked(self, ip: str) -> bool:
        """Return True if the IP has exceeded the failure threshold."""
        now = time.monotonic()
        with self._lock:
            timestamps = self._failures.get(ip, [])
            active = [t for t in timestamps if now - t < self._window]
            self._failures[ip] = active
            return len(active) >= self._max_failures

    def clear(self, ip: str) -> None:
        """Clear failure history for an IP (e.g., after successful auth)."""
        with self._lock:
            self._failures.pop(ip, None)

    def cleanup(self) -> None:
        """Remove stale entries."""
        now = time.monotonic()
        with self._lock:
            stale_keys = []
            for ip, timestamps in self._failures.items():
                active = [t for t in timestamps if now - t < self._window]
                if not active:
                    stale_keys.append(ip)
                else:
                    self._failures[ip] = active
            for ip in stale_keys:
                del self._failures[ip]


# ---------------------------------------------------------------------------
# Global singletons
# ---------------------------------------------------------------------------

rate_limiter = RateLimiter()
failed_auth_tracker = FailedAuthTracker()

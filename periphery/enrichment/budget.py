"""LLM budget tracker for Tier 3 relationship extraction.

Tracks spend per hour and per day, and enforces configurable caps.
When the budget is exhausted, callers should fall back to cheaper tiers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class BudgetTracker:
    """Tracks LLM API spend and enforces caps."""

    hourly_cap_usd: float = 5.0
    daily_cap_usd: float = 50.0
    _hourly_spend: float = 0.0
    _daily_spend: float = 0.0
    _hourly_reset: float = field(default_factory=time.time)
    _daily_reset: float = field(default_factory=time.time)

    def _maybe_reset(self) -> None:
        now = time.time()
        if now - self._hourly_reset >= 3600:
            self._hourly_spend = 0.0
            self._hourly_reset = now
        if now - self._daily_reset >= 86400:
            self._daily_spend = 0.0
            self._daily_reset = now

    @property
    def budget_available(self) -> bool:
        """Return True if there's budget remaining."""
        self._maybe_reset()
        return (
            self._hourly_spend < self.hourly_cap_usd
            and self._daily_spend < self.daily_cap_usd
        )

    def record_spend(self, amount_usd: float) -> None:
        """Record a spend event."""
        self._maybe_reset()
        self._hourly_spend += amount_usd
        self._daily_spend += amount_usd
        logger.debug(
            "budget_spend_recorded",
            amount=amount_usd,
            hourly_spend=self._hourly_spend,
            daily_spend=self._daily_spend,
            hourly_remaining=max(0, self.hourly_cap_usd - self._hourly_spend),
            daily_remaining=max(0, self.daily_cap_usd - self._daily_spend),
        )

    @property
    def hourly_remaining(self) -> float:
        self._maybe_reset()
        return max(0.0, self.hourly_cap_usd - self._hourly_spend)

    @property
    def daily_remaining(self) -> float:
        self._maybe_reset()
        return max(0.0, self.daily_cap_usd - self._daily_spend)

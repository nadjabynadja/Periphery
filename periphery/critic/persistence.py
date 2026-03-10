"""Critic persistence — SQLite storage for critic run metadata.

Uses the shared DatabasePool for all connections. Schema is defined
centrally in periphery/db.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from periphery.db import get_pool
import structlog

logger = structlog.get_logger(__name__)


class CriticStore:
    """Async SQLite persistence for Critic run metadata."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        """Verify pool is available. Schema is managed by db.py."""
        get_pool()  # raises if not initialized
        logger.info("critic_store_initialized", db_path=self._db_path)

    async def save_run(
        self,
        run_id: str,
        model_version: int,
        snapshot_id: str,
        structures_scored: int,
        mean_confidence: float,
        median_confidence: float,
        low_confidence_count: int,
        high_confidence_count: int,
        scoring_time_ms: int,
    ) -> None:
        """Record a critic scoring run."""
        pool = get_pool()
        async with pool.acquire() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO critic_runs
                    (run_id, timestamp, model_version, snapshot_id,
                     structures_scored, mean_confidence, median_confidence,
                     low_confidence_count, high_confidence_count, scoring_time_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    model_version,
                    snapshot_id,
                    structures_scored,
                    mean_confidence,
                    median_confidence,
                    low_confidence_count,
                    high_confidence_count,
                    scoring_time_ms,
                ),
            )
            await db.commit()

    async def get_recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get the most recent critic runs."""
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT * FROM critic_runs ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def get_score_trend(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get mean confidence trend over recent runs."""
        pool = get_pool()
        async with pool.acquire() as db:
            cursor = await db.execute(
                """
                SELECT timestamp, mean_confidence, median_confidence,
                       structures_scored, low_confidence_count, high_confidence_count
                FROM critic_runs
                ORDER BY timestamp DESC LIMIT ?
                """,
                (limit,),
            )
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def get_latest_run(self) -> dict[str, Any] | None:
        """Get the most recent critic run."""
        runs = await self.get_recent_runs(limit=1)
        return runs[0] if runs else None

"""Critic persistence — SQLite storage for critic run metadata.

Adds the critic_runs table for monitoring the Critic's performance
and retraining history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger(__name__)

CRITIC_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS critic_runs (
    run_id TEXT PRIMARY KEY,
    timestamp TIMESTAMP,
    model_version INTEGER,
    snapshot_id TEXT,
    structures_scored INTEGER,
    mean_confidence FLOAT,
    median_confidence FLOAT,
    low_confidence_count INTEGER,
    high_confidence_count INTEGER,
    scoring_time_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_critic_run_time ON critic_runs(timestamp);
"""


class CriticStore:
    """Async SQLite persistence for Critic run metadata."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        """Create critic tables if they don't exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(CRITIC_SCHEMA_SQL)
            await db.commit()
        self._initialized = True
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
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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

            # Prune old runs — keep last 500
            await db.execute(
                """
                DELETE FROM critic_runs
                WHERE run_id NOT IN (
                    SELECT run_id FROM critic_runs
                    ORDER BY timestamp DESC LIMIT 500
                )
                """
            )
            await db.commit()

    async def get_recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get the most recent critic runs."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            cursor = await db.execute(
                "SELECT * FROM critic_runs ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def get_score_trend(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get mean confidence trend over recent runs."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
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

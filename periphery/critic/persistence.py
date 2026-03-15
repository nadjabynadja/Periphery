"""Critic persistence — SQLite storage for critic run metadata and scores.

Stores run-level stats, per-structure scores, and confidence history.
Schema is defined in periphery/db.py (canonical source).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from periphery.db import get_connection
import structlog

logger = structlog.get_logger(__name__)


class CriticStore:
    """Async SQLite persistence for Critic run metadata and scores."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        """Mark as initialized. Schema is created by db.ensure_database()."""
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
        async with get_connection(self._db_path) as db:
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

    async def save_scores(
        self, run_id: str, scored_structures: list[dict[str, Any]]
    ) -> None:
        """Batch insert per-structure scores for a run, prune to last 5 runs."""
        async with get_connection(self._db_path) as db:
            for s in scored_structures:
                await db.execute(
                    """
                    INSERT INTO critic_scores
                        (run_id, structure_id, structure_type, confidence,
                         confidence_raw, confidence_calibrated, signal_scores, explanation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        s.get("id", ""),
                        s.get("type", ""),
                        s.get("confidence"),
                        s.get("confidence_raw"),
                        s.get("confidence_calibrated"),
                        json.dumps(s.get("signal_scores", {})),
                        json.dumps(s.get("explanation", {})),
                    ),
                )

            # Prune scores from runs older than the last 5
            await db.execute(
                """
                DELETE FROM critic_scores
                WHERE run_id NOT IN (
                    SELECT run_id FROM critic_runs
                    ORDER BY timestamp DESC LIMIT 5
                )
                """
            )
            await db.commit()

    async def get_latest_scores(self) -> list[dict[str, Any]]:
        """Return scores from the most recent run."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT cs.* FROM critic_scores cs
                JOIN critic_runs cr ON cs.run_id = cr.run_id
                ORDER BY cr.timestamp DESC
                LIMIT 500
                """
            )
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            if not rows:
                return []
            # All rows from the latest run
            results = [dict(zip(columns, row)) for row in rows]
            latest_run = results[0]["run_id"]
            return [r for r in results if r["run_id"] == latest_run]

    async def get_scores_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return scores for a specific run."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM critic_scores WHERE run_id = ?",
                (run_id,),
            )
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def save_confidence_history(
        self, history_dict: dict[str, list[float]]
    ) -> None:
        """Upsert last-20 entries per structure into confidence history."""
        async with get_connection(self._db_path) as db:
            for structure_id, values in history_dict.items():
                # Keep last 20
                recent = values[-20:]
                for idx, conf in enumerate(recent):
                    await db.execute(
                        """
                        INSERT OR REPLACE INTO critic_confidence_history
                            (structure_id, snapshot_index, confidence)
                        VALUES (?, ?, ?)
                        """,
                        (structure_id, idx, conf),
                    )
            await db.commit()

    async def load_confidence_history(self) -> dict[str, list[float]]:
        """Load confidence history from DB."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT structure_id, snapshot_index, confidence
                FROM critic_confidence_history
                ORDER BY structure_id, snapshot_index
                """
            )
            rows = await cursor.fetchall()

        history: dict[str, list[float]] = {}
        for row in rows:
            sid = row[0]
            conf = row[2]
            if sid not in history:
                history[sid] = []
            history[sid].append(conf)
        return history

    async def get_recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get the most recent critic runs."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM critic_runs ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            columns = [d[0] for d in cursor.description]
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def get_score_trend(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get mean confidence trend over recent runs."""
        async with get_connection(self._db_path) as db:
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

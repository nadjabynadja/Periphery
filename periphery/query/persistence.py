"""Component 7 — Query History & Analyst Context.

Persists query history, session state, and analyst annotations in SQLite.
Feeds query patterns back into system development and entity resolution.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from periphery.db import get_connection

logger = logging.getLogger(__name__)

QUERY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS query_history (
    query_id TEXT PRIMARY KEY,
    query_text TEXT,
    parsed_intent JSON,
    execution_plan JSON,
    result_summary JSON,
    execution_stats JSON,
    analyst_feedback JSON,
    session_id TEXT,
    timestamp TIMESTAMP,
    response_time_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_query_time ON query_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_query_session ON query_history(session_id);

CREATE TABLE IF NOT EXISTS query_sessions (
    session_id TEXT PRIMARY KEY,
    state JSON,
    created_at TIMESTAMP,
    last_active TIMESTAMP
);

CREATE TABLE IF NOT EXISTS query_bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id TEXT REFERENCES query_history(query_id),
    session_id TEXT,
    label TEXT,
    created_at TIMESTAMP,
    active BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_bookmark_session ON query_bookmarks(session_id);

CREATE TABLE IF NOT EXISTS analyst_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annotation_type TEXT,
    target_type TEXT,
    target_id TEXT,
    annotation_data JSON,
    session_id TEXT,
    created_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_annotation_target ON analyst_annotations(target_type, target_id);
"""


class QueryStore:
    """Async SQLite persistence for query history and session state."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(QUERY_SCHEMA_SQL)
            await db.commit()
        self._initialized = True
        logger.info("query_store_initialized db=%s", self._db_path)

    async def save_query(
        self,
        query_id: str,
        query_text: str,
        parsed_intent: dict[str, Any],
        execution_plan: dict[str, Any],
        result_summary: dict[str, Any],
        execution_stats: dict[str, Any],
        session_id: str | None = None,
        response_time_ms: int = 0,
    ) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT OR REPLACE INTO query_history
                    (query_id, query_text, parsed_intent, execution_plan,
                     result_summary, execution_stats, session_id,
                     timestamp, response_time_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query_id,
                    query_text,
                    json.dumps(parsed_intent),
                    json.dumps(execution_plan),
                    json.dumps(result_summary),
                    json.dumps(execution_stats),
                    session_id,
                    datetime.now(timezone.utc).isoformat(),
                    response_time_ms,
                ),
            )
            await db.commit()

    async def get_recent_queries(
        self, limit: int = 20, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            if session_id:
                cursor = await db.execute(
                    "SELECT query_id, query_text, parsed_intent, result_summary, "
                    "timestamp, response_time_ms FROM query_history "
                    "WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (session_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT query_id, query_text, parsed_intent, result_summary, "
                    "timestamp, response_time_ms FROM query_history "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [
                {
                    "query_id": r[0],
                    "query_text": r[1],
                    "parsed_intent": json.loads(r[2]) if r[2] else {},
                    "result_summary": json.loads(r[3]) if r[3] else {},
                    "timestamp": r[4],
                    "response_time_ms": r[5],
                }
                for r in rows
            ]

    async def save_session(self, session_id: str, state: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT OR REPLACE INTO query_sessions
                    (session_id, state, created_at, last_active)
                VALUES (?, ?, COALESCE(
                    (SELECT created_at FROM query_sessions WHERE session_id = ?),
                    ?
                ), ?)
                """,
                (session_id, json.dumps(state), session_id, now, now),
            )
            await db.commit()

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            cursor = await db.execute(
                "SELECT state FROM query_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
        return None

    async def save_bookmark(
        self, query_id: str, session_id: str, label: str = ""
    ) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO query_bookmarks (query_id, session_id, label, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (query_id, session_id, label, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def get_bookmarks(self, session_id: str) -> list[dict[str, Any]]:
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            cursor = await db.execute(
                """
                SELECT b.query_id, b.label, b.created_at, h.query_text
                FROM query_bookmarks b
                LEFT JOIN query_history h ON b.query_id = h.query_id
                WHERE b.session_id = ? AND b.active = TRUE
                ORDER BY b.created_at DESC
                """,
                (session_id,),
            )
            return [
                {
                    "query_id": r[0],
                    "label": r[1],
                    "created_at": r[2],
                    "query_text": r[3],
                }
                for r in await cursor.fetchall()
            ]

    async def save_annotation(
        self,
        annotation_type: str,
        target_type: str,
        target_id: str,
        annotation_data: dict[str, Any],
        session_id: str = "",
    ) -> None:
        """Save an analyst annotation (entity merge, relationship confirmation, etc.)."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                INSERT INTO analyst_annotations
                    (annotation_type, target_type, target_id, annotation_data,
                     session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation_type,
                    target_type,
                    target_id,
                    json.dumps(annotation_data),
                    session_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()

    async def save_feedback(
        self, query_id: str, feedback: dict[str, Any]
    ) -> None:
        """Save analyst feedback on a query result."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "UPDATE query_history SET analyst_feedback = ? WHERE query_id = ?",
                (json.dumps(feedback), query_id),
            )
            await db.commit()

    async def get_query_stats(self) -> dict[str, Any]:
        """Return aggregate query statistics."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            cursor = await db.execute(
                "SELECT COUNT(*), AVG(response_time_ms), "
                "MIN(response_time_ms), MAX(response_time_ms) "
                "FROM query_history"
            )
            row = await cursor.fetchone()
            total, avg_ms, min_ms, max_ms = row if row else (0, 0, 0, 0)

            cursor = await db.execute(
                "SELECT COUNT(*) FROM query_history WHERE analyst_feedback IS NOT NULL"
            )
            feedback_count = (await cursor.fetchone())[0]

            return {
                "total_queries": total or 0,
                "avg_response_ms": round(avg_ms or 0, 1),
                "min_response_ms": min_ms or 0,
                "max_response_ms": max_ms or 0,
                "queries_with_feedback": feedback_count or 0,
            }

"""Centralized database layer — connection pool, schema, and lifecycle.

Single source of truth for all SQLite schema definitions and the
connection pool that every component shares. Components never open
their own connections; they acquire one from the pool.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — the single canonical definition of every table and index.
# Component-level files (document_store.py, crystallizer/persistence.py, etc.)
# no longer carry their own CREATE TABLE statements.
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- ===== Documents =====
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source_feed TEXT NOT NULL,
    source_category TEXT,
    source_credibility_tier INTEGER,
    title TEXT,
    url TEXT,
    published TIMESTAMP,
    ingested TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    content TEXT,
    raw_html TEXT,
    summary TEXT,
    content_quality TEXT DEFAULT 'full',
    metadata JSON,
    processing_status TEXT DEFAULT 'pending',
    processing_error TEXT,
    enrichment_started_at TIMESTAMP,
    enrichment_completed_at TIMESTAMP,
    embedding_started_at TIMESTAMP,
    embedding_completed_at TIMESTAMP,
    crystallization_started_at TIMESTAMP,
    crystallization_completed_at TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3
);

CREATE INDEX IF NOT EXISTS idx_ingested ON documents(ingested);
CREATE INDEX IF NOT EXISTS idx_source_feed ON documents(source_feed);
CREATE INDEX IF NOT EXISTS idx_processing_status ON documents(processing_status);
CREATE INDEX IF NOT EXISTS idx_url ON documents(url);
CREATE INDEX IF NOT EXISTS idx_content_quality ON documents(content_quality);
CREATE INDEX IF NOT EXISTS idx_processing_retry ON documents(processing_status, retry_count);

CREATE TABLE IF NOT EXISTS document_enrichments (
    document_id TEXT PRIMARY KEY REFERENCES documents(id),
    entities JSON,
    relationships JSON,
    temporal_context JSON,
    geospatial_data JSON,
    cross_references JSON,
    enrichment_metadata JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_enrichment_doc ON document_enrichments(document_id);

CREATE TABLE IF NOT EXISTS document_embeddings (
    document_id TEXT PRIMARY KEY REFERENCES documents(id),
    semantic_embedding BLOB,
    semantic_chunks JSON,
    entity_embedding BLOB,
    relational_embedding BLOB,
    temporal_vector JSON,
    geospatial_vector JSON,
    embedding_model TEXT,
    embedding_dimensions INTEGER,
    completeness JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_embedding_doc ON document_embeddings(document_id);

-- ===== Crystallizer =====
CREATE TABLE IF NOT EXISTS crystallizer_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    generated_at TIMESTAMP,
    snapshot_data JSON,
    corpus_size INTEGER,
    num_clusters INTEGER,
    num_anomalies INTEGER,
    processing_time_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snapshot_time ON crystallizer_snapshots(generated_at);

CREATE TABLE IF NOT EXISTS clusters (
    cluster_id TEXT PRIMARY KEY,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP,
    status TEXT,
    current_size INTEGER,
    cross_space_coherence FLOAT,
    label TEXT,
    key_entities JSON,
    metadata JSON
);
CREATE INDEX IF NOT EXISTS idx_cluster_status ON clusters(status);

CREATE TABLE IF NOT EXISTS cluster_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT REFERENCES clusters(cluster_id),
    timestamp TIMESTAMP,
    size INTEGER,
    centroid JSON,
    coherence FLOAT
);
CREATE INDEX IF NOT EXISTS idx_cluster_snap_time ON cluster_snapshots(cluster_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_cluster_snap_cluster ON cluster_snapshots(cluster_id);

CREATE TABLE IF NOT EXISTS trajectories (
    trajectory_id TEXT PRIMARY KEY,
    cluster_id TEXT REFERENCES clusters(cluster_id),
    space TEXT,
    pattern TEXT,
    velocity FLOAT,
    confidence FLOAT,
    first_detected TIMESTAMP,
    last_updated TIMESTAMP,
    snapshots JSON
);
CREATE INDEX IF NOT EXISTS idx_trajectory_cluster ON trajectories(cluster_id);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id TEXT PRIMARY KEY,
    document_id TEXT,
    anomaly_type TEXT,
    anomaly_score FLOAT,
    outlier_spaces JSON,
    source_credibility INTEGER,
    first_detected TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_into_cluster TEXT
);
CREATE INDEX IF NOT EXISTS idx_anomaly_unresolved ON anomalies(resolved, anomaly_score DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_document ON anomalies(document_id);

CREATE TABLE IF NOT EXISTS relational_gradients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_cluster TEXT REFERENCES clusters(cluster_id),
    target_cluster TEXT REFERENCES clusters(cluster_id),
    gradient_score FLOAT,
    components JSON,
    first_detected TIMESTAMP,
    trend TEXT
);
CREATE INDEX IF NOT EXISTS idx_gradient_score ON relational_gradients(gradient_score DESC);
CREATE INDEX IF NOT EXISTS idx_gradient_clusters ON relational_gradients(source_cluster, target_cluster);

-- ===== Critic =====
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

-- ===== Query =====
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
CREATE INDEX IF NOT EXISTS idx_session_active ON query_sessions(last_active);

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

# ---------------------------------------------------------------------------
# Retention policy defaults — configurable via DatabasePool settings.
# ---------------------------------------------------------------------------

DEFAULT_RETENTION = {
    "crystallizer_snapshots": 100,
    "critic_runs": 500,
    "query_history_days": 90,
    "query_sessions_days": 30,
    "cluster_snapshots_per_cluster": 200,
}


# ---------------------------------------------------------------------------
# Connection Pool
# ---------------------------------------------------------------------------

class DatabasePool:
    """Async connection pool for aiosqlite.

    Maintains a bounded set of reusable connections with pre-configured
    pragmas. All components acquire connections through this pool instead
    of opening their own.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        pool_size: int = 5,
        busy_timeout_ms: int = 30_000,
        retention: dict[str, int] | None = None,
    ) -> None:
        self._db_path = str(Path(db_path).resolve())
        self._pool_size = pool_size
        self._busy_timeout_ms = busy_timeout_ms
        self._retention = {**DEFAULT_RETENTION, **(retention or {})}

        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=pool_size)
        self._all_connections: list[aiosqlite.Connection] = []
        self._initialized = False
        self._closed = False

        # Telemetry
        self._acquire_count = 0
        self._release_count = 0
        self._active_count = 0
        self._peak_active = 0
        self._total_wait_ns = 0
        self._lock = asyncio.Lock()

    @property
    def db_path(self) -> str:
        return self._db_path

    async def initialize(self) -> None:
        """Create the database file, run schema migrations, and fill the pool."""
        if self._initialized:
            return

        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Create schema with a dedicated bootstrap connection
        bootstrap = await self._create_connection()
        try:
            await bootstrap.executescript(SCHEMA_SQL)
            await bootstrap.commit()
        finally:
            await bootstrap.close()

        # Fill the pool
        for _ in range(self._pool_size):
            conn = await self._create_connection()
            self._all_connections.append(conn)
            await self._pool.put(conn)

        self._initialized = True
        logger.info(
            "database_pool_initialized db=%s pool_size=%d",
            self._db_path, self._pool_size,
        )

    async def _create_connection(self) -> aiosqlite.Connection:
        """Open a new connection with standard pragmas."""
        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA synchronous=NORMAL")
        db.row_factory = aiosqlite.Row
        return db

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool.

        Usage::

            async with pool.acquire() as db:
                await db.execute("SELECT ...")
        """
        if self._closed:
            raise RuntimeError("DatabasePool is closed")
        if not self._initialized:
            raise RuntimeError("DatabasePool not initialized — call initialize() first")

        start = time.monotonic_ns()
        conn = await self._pool.get()
        wait_ns = time.monotonic_ns() - start

        async with self._lock:
            self._acquire_count += 1
            self._active_count += 1
            self._total_wait_ns += wait_ns
            if self._active_count > self._peak_active:
                self._peak_active = self._active_count

        try:
            yield conn
        except Exception:
            # On error, rollback any uncommitted transaction before returning
            try:
                await conn.rollback()
            except Exception:
                pass
            raise
        finally:
            async with self._lock:
                self._release_count += 1
                self._active_count -= 1
            await self._pool.put(conn)

    async def close(self) -> None:
        """Drain the pool and close all connections."""
        if self._closed:
            return
        self._closed = True

        for conn in self._all_connections:
            try:
                await conn.close()
            except Exception:
                pass
        self._all_connections.clear()

        # Drain the queue
        while not self._pool.empty():
            try:
                self._pool.get_nowait()
            except asyncio.QueueEmpty:
                break

        logger.info("database_pool_closed db=%s", self._db_path)

    async def run_retention(self) -> dict[str, int]:
        """Apply retention policies. Returns counts of deleted rows per table."""
        deleted: dict[str, int] = {}
        async with self.acquire() as db:
            # Crystallizer snapshots — keep N most recent.
            # NULL out critic_runs references first to avoid FK violation.
            limit = self._retention["crystallizer_snapshots"]
            await db.execute(
                """
                UPDATE critic_runs SET snapshot_id = NULL
                WHERE snapshot_id IS NOT NULL
                AND snapshot_id NOT IN (
                    SELECT snapshot_id FROM crystallizer_snapshots
                    ORDER BY generated_at DESC LIMIT ?
                )
                """,
                (limit,),
            )
            cursor = await db.execute(
                """
                DELETE FROM crystallizer_snapshots
                WHERE snapshot_id NOT IN (
                    SELECT snapshot_id FROM crystallizer_snapshots
                    ORDER BY generated_at DESC LIMIT ?
                )
                """,
                (limit,),
            )
            deleted["crystallizer_snapshots"] = cursor.rowcount

            # Critic runs — keep N most recent
            limit = self._retention["critic_runs"]
            cursor = await db.execute(
                """
                DELETE FROM critic_runs
                WHERE run_id NOT IN (
                    SELECT run_id FROM critic_runs
                    ORDER BY timestamp DESC LIMIT ?
                )
                """,
                (limit,),
            )
            deleted["critic_runs"] = cursor.rowcount

            # Query history — keep last N days
            days = self._retention["query_history_days"]
            cursor = await db.execute(
                "DELETE FROM query_history WHERE timestamp < datetime('now', ?)",
                (f"-{days} days",),
            )
            deleted["query_history"] = cursor.rowcount

            # Query sessions — keep last N days
            days = self._retention["query_sessions_days"]
            cursor = await db.execute(
                "DELETE FROM query_sessions WHERE last_active < datetime('now', ?)",
                (f"-{days} days",),
            )
            deleted["query_sessions"] = cursor.rowcount

            # Cluster snapshots — keep N per cluster
            per_cluster = self._retention["cluster_snapshots_per_cluster"]
            cursor = await db.execute(
                """
                DELETE FROM cluster_snapshots
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY cluster_id ORDER BY timestamp DESC
                        ) AS rn
                        FROM cluster_snapshots
                    ) WHERE rn <= ?
                )
                """,
                (per_cluster,),
            )
            deleted["cluster_snapshots"] = cursor.rowcount

            await db.commit()

        if any(v > 0 for v in deleted.values()):
            logger.info("retention_applied deleted=%s", deleted)
        return deleted

    def health(self) -> dict[str, Any]:
        """Return pool health metrics."""
        avg_wait_ms = 0.0
        if self._acquire_count > 0:
            avg_wait_ms = (self._total_wait_ns / self._acquire_count) / 1_000_000

        return {
            "db_path": self._db_path,
            "pool_size": self._pool_size,
            "available": self._pool.qsize(),
            "active": self._active_count,
            "peak_active": self._peak_active,
            "total_acquires": self._acquire_count,
            "avg_wait_ms": round(avg_wait_ms, 3),
            "initialized": self._initialized,
            "closed": self._closed,
        }


# ---------------------------------------------------------------------------
# Global pool singleton
# ---------------------------------------------------------------------------

_pool: DatabasePool | None = None


async def init_pool(
    db_path: str | Path,
    *,
    pool_size: int = 5,
    busy_timeout_ms: int = 30_000,
    retention: dict[str, int] | None = None,
) -> DatabasePool:
    """Initialize the global database pool. Safe to call multiple times."""
    global _pool
    if _pool is not None and _pool._initialized and not _pool._closed:
        return _pool
    _pool = DatabasePool(
        db_path,
        pool_size=pool_size,
        busy_timeout_ms=busy_timeout_ms,
        retention=retention,
    )
    await _pool.initialize()
    return _pool


def get_pool() -> DatabasePool:
    """Get the global pool. Raises if not initialized."""
    if _pool is None or not _pool._initialized:
        raise RuntimeError(
            "Database pool not initialized. Call init_pool() at startup."
        )
    return _pool


async def close_pool() -> None:
    """Close the global pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Legacy compatibility — components that haven't migrated yet can still
# use get_connection(). These thin wrappers route through the pool when
# available, or fall back to direct connections.
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_connection(db_path: str | Path | None = None):
    """Acquire a connection — uses pool if available, else opens a direct one.

    Prefer pool.acquire() for new code. This wrapper exists so existing
    callers keep working during the migration.
    """
    if _pool is not None and _pool._initialized and not _pool._closed:
        # Route through pool (ignoring db_path — pool is single-db)
        async with _pool.acquire() as db:
            yield db
    else:
        # Fallback: direct connection (pre-pool startup or tests)
        db = await aiosqlite.connect(str(db_path or "./data/periphery_documents.db"))
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()


async def get_persistent_connection(db_path: str | Path) -> aiosqlite.Connection:
    """For connections that stay open for the lifetime of a component.

    Kept for backward compat with DocumentStore. New code should use
    the pool instead.
    """
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=30000")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA synchronous=NORMAL")
    db.row_factory = aiosqlite.Row
    return db


async def ensure_database(db_path: str | Path) -> None:
    """Create the database and all tables.

    Delegates to init_pool() which handles schema creation. Kept as a
    convenience entry point for main.py and tests.
    """
    await init_pool(db_path)

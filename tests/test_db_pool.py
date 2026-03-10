"""Tests for the centralized database pool and schema management."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from periphery.db import (
    DatabasePool,
    close_pool,
    get_connection,
    get_pool,
    init_pool,
)


@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_pool_initialize_creates_schema(tmp_db_path):
    """Pool initialization should create all tables."""
    pool = DatabasePool(tmp_db_path, pool_size=2)
    await pool.initialize()
    try:
        async with pool.acquire() as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        expected_tables = {
            "documents",
            "document_enrichments",
            "document_embeddings",
            "crystallizer_snapshots",
            "clusters",
            "cluster_snapshots",
            "trajectories",
            "anomalies",
            "relational_gradients",
            "critic_runs",
            "query_history",
            "query_sessions",
            "query_bookmarks",
            "analyst_annotations",
        }
        assert expected_tables.issubset(tables), f"Missing: {expected_tables - tables}"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pool_acquire_and_release(tmp_db_path):
    """Connections should be reusable through the pool."""
    pool = DatabasePool(tmp_db_path, pool_size=2)
    await pool.initialize()
    try:
        # Acquire and release
        async with pool.acquire() as db:
            await db.execute("SELECT 1")

        health = pool.health()
        assert health["pool_size"] == 2
        assert health["available"] == 2
        assert health["active"] == 0
        assert health["total_acquires"] == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pool_concurrent_access(tmp_db_path):
    """Multiple concurrent acquires should work within pool_size."""
    pool = DatabasePool(tmp_db_path, pool_size=3)
    await pool.initialize()
    try:
        results = []

        async def worker(i):
            async with pool.acquire() as db:
                cursor = await db.execute("SELECT ?", (i,))
                row = await cursor.fetchone()
                results.append(row[0])

        await asyncio.gather(worker(1), worker(2), worker(3))
        assert sorted(results) == [1, 2, 3]
        assert pool.health()["total_acquires"] == 3
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pool_health_metrics(tmp_db_path):
    """Health endpoint should report accurate metrics."""
    pool = DatabasePool(tmp_db_path, pool_size=2)
    await pool.initialize()
    try:
        for _ in range(5):
            async with pool.acquire() as db:
                await db.execute("SELECT 1")

        health = pool.health()
        assert health["total_acquires"] == 5
        assert health["initialized"] is True
        assert health["closed"] is False
        assert health["peak_active"] >= 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pool_rollback_on_error(tmp_db_path):
    """Errors should trigger rollback before returning connection to pool."""
    pool = DatabasePool(tmp_db_path, pool_size=1)
    await pool.initialize()
    try:
        # Insert a row in a transaction that will be rolled back
        try:
            async with pool.acquire() as db:
                await db.execute(
                    "INSERT INTO documents (id, source_feed, content) VALUES (?, ?, ?)",
                    ("test1", "feed1", "content1"),
                )
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # The insert should have been rolled back
        async with pool.acquire() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            row = await cursor.fetchone()
            assert row[0] == 0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pool_close_prevents_acquire(tmp_db_path):
    """Acquiring after close should raise RuntimeError."""
    pool = DatabasePool(tmp_db_path, pool_size=1)
    await pool.initialize()
    await pool.close()

    with pytest.raises(RuntimeError, match="closed"):
        async with pool.acquire() as db:
            pass


@pytest.mark.asyncio
async def test_retention_policies(tmp_db_path):
    """Retention should clean up old records."""
    pool = DatabasePool(
        tmp_db_path,
        pool_size=1,
        retention={"crystallizer_snapshots": 2, "critic_runs": 2},
    )
    await pool.initialize()
    try:
        async with pool.acquire() as db:
            # Insert 5 snapshots
            for i in range(5):
                await db.execute(
                    "INSERT INTO crystallizer_snapshots (snapshot_id, generated_at, corpus_size) "
                    "VALUES (?, datetime('now', ?), 100)",
                    (f"snap_{i}", f"-{5 - i} hours"),
                )
            await db.commit()

        deleted = await pool.run_retention()
        assert deleted["crystallizer_snapshots"] == 3  # kept 2, deleted 3

        async with pool.acquire() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM crystallizer_snapshots")
            row = await cursor.fetchone()
            assert row[0] == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_global_pool_lifecycle(tmp_db_path):
    """init_pool / get_pool / close_pool should manage the global singleton."""
    pool = await init_pool(tmp_db_path, pool_size=2)
    try:
        assert get_pool() is pool
        async with get_pool().acquire() as db:
            await db.execute("SELECT 1")
    finally:
        await close_pool()

    with pytest.raises(RuntimeError):
        get_pool()


@pytest.mark.asyncio
async def test_legacy_get_connection_uses_pool(tmp_db_path):
    """get_connection() should route through the pool when available."""
    await init_pool(tmp_db_path, pool_size=2)
    try:
        async with get_connection() as db:
            cursor = await db.execute("SELECT 1")
            row = await cursor.fetchone()
            assert row[0] == 1

        # Verify pool was used (acquire count > 0 from schema init)
        assert get_pool().health()["total_acquires"] >= 1
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_legacy_get_connection_fallback(tmp_db_path):
    """get_connection() should fall back to direct connection without pool."""
    # No pool initialized — should use direct connection
    async with get_connection(tmp_db_path) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS test_t (id TEXT)")
        cursor = await db.execute("SELECT 1")
        row = await cursor.fetchone()
        assert row[0] == 1


@pytest.mark.asyncio
async def test_documents_crud_through_pool(tmp_db_path):
    """Basic document insert and query through pool."""
    pool = DatabasePool(tmp_db_path, pool_size=2)
    await pool.initialize()
    try:
        async with pool.acquire() as db:
            await db.execute(
                "INSERT INTO documents (id, source_feed, content, processing_status) "
                "VALUES (?, ?, ?, ?)",
                ("doc1", "feed1", "Test content", "pending"),
            )
            await db.commit()

        async with pool.acquire() as db:
            cursor = await db.execute("SELECT content FROM documents WHERE id = ?", ("doc1",))
            row = await cursor.fetchone()
            assert row[0] == "Test content"

            cursor = await db.execute(
                "SELECT processing_status FROM documents WHERE id = ?", ("doc1",)
            )
            row = await cursor.fetchone()
            assert row[0] == "pending"
    finally:
        await pool.close()

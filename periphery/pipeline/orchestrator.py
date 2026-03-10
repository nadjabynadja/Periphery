"""Pipeline orchestrator — manages all stage consumers as concurrent async tasks.

Single entry point: ``python -m periphery.pipeline``
Uses the shared DatabasePool for all connections.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from periphery.db import get_pool
import structlog

from .consumer import StageConsumer

logger = structlog.get_logger(__name__)


class PipelineOrchestrator:
    """Starts all stage consumers and monitors their health.

    If any consumer task crashes, the orchestrator restarts it after a brief
    delay rather than bringing down the whole pipeline.
    """

    def __init__(
        self,
        consumers: list[StageConsumer],
        db_path: str,
        *,
        restart_delay: float = 5.0,
    ) -> None:
        self._consumers = consumers
        self._db_path = db_path
        self._restart_delay = restart_delay
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._started_at: datetime | None = None

    @property
    def consumers(self) -> list[StageConsumer]:
        return self._consumers

    async def run(self) -> None:
        """Run all consumers concurrently with automatic restart on failure."""
        self._running = True
        self._started_at = datetime.now(timezone.utc)

        # Run stale claim recovery on startup
        pool = get_pool()
        async with pool.acquire() as db:
            for consumer in self._consumers:
                try:
                    recovered = await consumer.recover_stale_claims(db)
                    if recovered:
                        logger.info(
                            "stale_recovery_complete",
                            consumer=consumer.name,
                            recovered=recovered,
                        )
                except Exception:
                    logger.exception(
                        "stale_recovery_failed", consumer=consumer.name
                    )

        # Start all consumer tasks
        for consumer in self._consumers:
            self._tasks[consumer.name] = asyncio.create_task(
                self._run_consumer(consumer),
                name=f"pipeline-{consumer.name}",
            )

        # Wait for all tasks (they run indefinitely)
        try:
            await asyncio.gather(*self._tasks.values())
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop all consumers."""
        self._running = False
        for consumer in self._consumers:
            await consumer.stop()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("pipeline_orchestrator_stopped")

    async def _run_consumer(self, consumer: StageConsumer) -> None:
        """Run a consumer with automatic restart on crash."""
        while self._running:
            try:
                await consumer.run()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "consumer_crashed",
                    consumer=consumer.name,
                    restart_delay=self._restart_delay,
                )
                if self._running:
                    await asyncio.sleep(self._restart_delay)
                    logger.info("consumer_restarting", consumer=consumer.name)

    async def get_pipeline_stats(self) -> dict[str, Any]:
        """Return full pipeline state for the stats endpoint."""
        pool = get_pool()
        async with pool.acquire() as db:
            # Pipeline status counts
            cursor = await db.execute(
                "SELECT processing_status, COUNT(*) FROM documents GROUP BY processing_status"
            )
            status_counts = {r[0]: r[1] for r in await cursor.fetchall()}

            # Throughput: documents processed in last hour per stage
            throughput: dict[str, Any] = {}

            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents WHERE ingested > datetime('now', '-1 hour')"
            )
            row = await cursor.fetchone()
            throughput["documents_ingested_last_hour"] = row[0] if row else 0

            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE enrichment_completed_at > datetime('now', '-1 hour')"
            )
            row = await cursor.fetchone()
            throughput["documents_enriched_last_hour"] = row[0] if row else 0

            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE embedding_completed_at > datetime('now', '-1 hour')"
            )
            row = await cursor.fetchone()
            throughput["documents_embedded_last_hour"] = row[0] if row else 0

            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE crystallization_completed_at > datetime('now', '-1 hour')"
            )
            row = await cursor.fetchone()
            throughput["documents_crystallized_last_hour"] = row[0] if row else 0

            # Average enrichment time
            cursor = await db.execute(
                """
                SELECT AVG(
                    (julianday(enrichment_completed_at) - julianday(enrichment_started_at)) * 86400000
                )
                FROM documents
                WHERE enrichment_completed_at IS NOT NULL
                  AND enrichment_started_at IS NOT NULL
                  AND enrichment_completed_at > datetime('now', '-1 hour')
                """
            )
            row = await cursor.fetchone()
            throughput["avg_enrichment_time_ms"] = round(row[0], 1) if row and row[0] else 0

            # Average embedding time
            cursor = await db.execute(
                """
                SELECT AVG(
                    (julianday(embedding_completed_at) - julianday(embedding_started_at)) * 86400000
                )
                FROM documents
                WHERE embedding_completed_at IS NOT NULL
                  AND embedding_started_at IS NOT NULL
                  AND embedding_completed_at > datetime('now', '-1 hour')
                """
            )
            row = await cursor.fetchone()
            throughput["avg_embedding_time_ms"] = round(row[0], 1) if row and row[0] else 0

            # Pipeline lag: time between newest ingested and newest crystallized
            cursor = await db.execute(
                """
                SELECT
                    (julianday((SELECT MAX(ingested) FROM documents)) -
                     julianday((SELECT MAX(crystallization_completed_at)
                                FROM documents
                                WHERE crystallization_completed_at IS NOT NULL))
                    ) * 86400
                """
            )
            row = await cursor.fetchone()
            throughput["pipeline_lag_seconds"] = round(row[0], 1) if row and row[0] else 0

            # Failures
            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents WHERE processing_status = 'failed'"
            )
            row = await cursor.fetchone()
            total_failed = row[0] if row else 0

            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM documents
                WHERE processing_status = 'failed'
                  AND ingested > datetime('now', '-1 hour')
                """
            )
            row = await cursor.fetchone()
            failed_last_hour = row[0] if row else 0

            # Top failure reasons
            cursor = await db.execute(
                """
                SELECT processing_error, COUNT(*) as cnt
                FROM documents
                WHERE processing_status = 'failed'
                  AND processing_error IS NOT NULL
                GROUP BY processing_error
                ORDER BY cnt DESC
                LIMIT 5
                """
            )
            top_failures = [
                {"reason": r[0], "count": r[1]}
                for r in await cursor.fetchall()
            ]

            # Consumer health
            consumer_health = {}
            for consumer in self._consumers:
                consumer_health[consumer.name.lower().replace("consumer", "")] = consumer.health()

        return {
            "pipeline_status": {
                "pending": status_counts.get("pending", 0),
                "enriching": status_counts.get("enriching", 0),
                "enriched": status_counts.get("enriched", 0),
                "embedding": status_counts.get("embedding", 0),
                "embedded": status_counts.get("embedded", 0),
                "crystallized": status_counts.get("crystallized", 0),
                "failed": status_counts.get("failed", 0),
            },
            "throughput": throughput,
            "failures": {
                "total_failed": total_failed,
                "failed_last_hour": failed_last_hour,
                "top_failure_reasons": top_failures,
            },
            "consumers": consumer_health,
        }

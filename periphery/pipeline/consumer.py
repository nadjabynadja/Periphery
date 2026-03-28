"""Stage consumer base class — the core abstraction for pipeline stages.

Every stage consumer does the same fundamental thing: poll for documents in
its input state, claim them, process them, and advance their state. The only
thing that varies is the actual processing logic.
"""

from __future__ import annotations

import abc
import asyncio
import aiosqlite
import time
from datetime import datetime, timezone
from typing import Any

from periphery.db import get_connection
import structlog

logger = structlog.get_logger(__name__)


class StageConsumer(abc.ABC):
    """Base class for all processing stage consumers.

    Each stage:
    - Polls for documents in ``input_status``
    - Claims them by setting status to ``processing_status``
    - Processes them (subclass implements this)
    - Advances them to ``output_status`` on success
    - Marks them ``failed`` on error (with retry logic)
    """

    input_status: str
    processing_status: str
    output_status: str
    batch_size: int = 10
    poll_interval: float = 10.0
    started_at_column: str = ""
    completed_at_column: str = ""

    def __init__(
        self,
        db_path: str,
        *,
        batch_size: int | None = None,
        poll_interval: float | None = None,
        stale_claim_timeout: float = 600.0,
        max_retries: int = 3,
    ) -> None:
        self._db_path = db_path
        if batch_size is not None:
            self.batch_size = batch_size
        if poll_interval is not None:
            self.poll_interval = poll_interval
        self._stale_claim_timeout = stale_claim_timeout
        self._max_retries = max_retries

        # Notification queue — fast path for inter-stage handoff
        self._notify_queue: asyncio.Queue[str] = asyncio.Queue()

        # Callback to notify the next stage consumer
        self._on_advance: Any = None

        # Health tracking
        self._running = False
        self._last_heartbeat: datetime | None = None
        self._docs_processed_times: list[float] = []
        self._docs_processed_last_hour = 0
        self._error_count_last_hour = 0

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    async def process(self, db: aiosqlite.Connection, doc_rows: list[dict[str, Any]]) -> list[str]:
        """Process claimed documents. Return list of successfully processed doc IDs.

        Subclasses implement the actual processing logic here. The base class
        handles claiming, advancing, and failure tracking.
        """
        ...

    def notify(self, document_id: str) -> None:
        """Push a document ID notification for fast-path processing."""
        try:
            self._notify_queue.put_nowait(document_id)
        except asyncio.QueueFull:
            pass

    async def recover_stale_claims(self, db: aiosqlite.Connection) -> int:
        """Find documents stuck in processing_status and reset them.

        These are documents claimed by a previous process that crashed before
        completing. Reset them to input_status for reprocessing.
        """
        if not self.started_at_column:
            return 0

        cursor = await db.execute(
            f"""
            UPDATE documents
            SET processing_status = ?,
                {self.started_at_column} = NULL
            WHERE processing_status = ?
              AND {self.started_at_column} < datetime('now', ?)
            """,
            (
                self.input_status,
                self.processing_status,
                f"-{int(self._stale_claim_timeout)} seconds",
            ),
        )
        await db.commit()
        count = cursor.rowcount
        if count > 0:
            logger.info(
                "stale_claims_recovered",
                consumer=self.name,
                count=count,
                timeout_seconds=self._stale_claim_timeout,
            )
        return count

    async def run(self) -> None:
        """Main consumer loop with dual-path input: notifications + sweep."""
        self._running = True
        logger.info("consumer_started", consumer=self.name)

        while self._running:
            try:
                processed = await self._run_cycle()
                self._last_heartbeat = datetime.now(timezone.utc)

                if processed == 0:
                    # No work found — check notification queue or sleep
                    await self._wait_for_work()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("consumer_cycle_error", consumer=self.name)
                await asyncio.sleep(1.0)

        logger.info("consumer_stopped", consumer=self.name)

    async def stop(self) -> None:
        """Signal the consumer to stop."""
        self._running = False

    async def _wait_for_work(self) -> None:
        """Wait for notifications or poll interval, whichever comes first."""
        try:
            await asyncio.wait_for(
                self._notify_queue.get(),
                timeout=self.poll_interval,
            )
        except asyncio.TimeoutError:
            pass

    async def _run_cycle(self) -> int:
        """Run one polling + processing cycle. Returns count of docs processed.

        Split into three short DB transactions (claim → process → advance)
        to avoid holding a write lock during the long processing phase
        (LLM calls, embedding, etc.), which would block the API server.
        """
        # Phase 1: Claim batch (short DB transaction)
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row
            claimed = await self._claim_batch(db)
            if not claimed:
                return 0

        # Phase 2: Process (no DB connection held — frees write lock for API)
        start = time.monotonic()
        try:
            async with get_connection(self._db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                db.row_factory = aiosqlite.Row
                success_ids = await self.process(db, claimed)
        except Exception as exc:
            # Whole-batch failure — mark all as retry/failed
            async with get_connection(self._db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                db.row_factory = aiosqlite.Row
                for doc in claimed:
                    await self._handle_failure(db, doc["id"], doc.get("retry_count", 0), str(exc))
            return 0

        elapsed = time.monotonic() - start

        # Phase 3: Advance/fail (short DB transaction)
        if success_ids is None:
            success_ids = []
        success_set = set(str(sid) for sid in success_ids)

        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row
            for doc in claimed:
                doc_id = str(doc["id"])
                if doc_id in success_set:
                    await self._advance(db, doc_id)
                    self._docs_processed_times.append(elapsed / max(len(success_set), 1))
                    self._docs_processed_last_hour += 1
                else:
                    await self._handle_failure(
                        db, doc_id, doc.get("retry_count", 0),
                        "not in success list from process()"
                    )

            return len(success_ids)

    async def _claim_batch(self, db: aiosqlite.Connection) -> list[dict[str, Any]]:
        """Claim a batch of documents by atomically updating their status."""
        now = datetime.now(timezone.utc).isoformat()

        # Select candidates ordered by priority (lower = higher), credibility tier, then ingested (oldest first)
        cursor = await db.execute(
            """
            SELECT id, source_feed, source_category, source_credibility_tier,
                   title, url, content, summary, metadata, retry_count,
                   published, ingested, priority
            FROM documents
            WHERE processing_status = ?
            ORDER BY COALESCE(priority, 3) ASC, COALESCE(source_credibility_tier, 4) ASC, ingested ASC
            LIMIT ?
            """,
            (self.input_status, self.batch_size),
        )
        rows = await cursor.fetchall()
        if not rows:
            return []

        docs = []
        for row in rows:
            doc = dict(row)
            docs.append(doc)

        # Claim them in a transaction
        doc_ids = [d["id"] for d in docs]
        placeholders = ",".join("?" for _ in doc_ids)

        set_clause = f"processing_status = ?"
        params: list[Any] = [self.processing_status]
        if self.started_at_column:
            set_clause += f", {self.started_at_column} = ?"
            params.append(now)

        params.extend(doc_ids)
        params.append(self.input_status)

        await db.execute(
            f"""
            UPDATE documents
            SET {set_clause}
            WHERE id IN ({placeholders})
              AND processing_status = ?
            """,
            params,
        )
        await db.commit()

        logger.debug(
            "batch_claimed",
            consumer=self.name,
            count=len(docs),
            doc_ids=doc_ids[:3],
        )
        return docs

    async def _advance(self, db: aiosqlite.Connection, doc_id: str) -> None:
        """Advance a document to the output status."""
        now = datetime.now(timezone.utc).isoformat()

        set_clause = "processing_status = ?"
        params: list[Any] = [self.output_status]
        if self.completed_at_column:
            set_clause += f", {self.completed_at_column} = ?"
            params.append(now)

        params.append(doc_id)
        await db.execute(
            f"UPDATE documents SET {set_clause} WHERE id = ?",
            params,
        )
        await db.commit()

        # Notify the next stage consumer for fast-path pickup
        if self._on_advance is not None:
            try:
                self._on_advance(doc_id)
            except Exception:
                pass  # notification is optimization, not required

    async def _handle_failure(
        self, db: aiosqlite.Connection, doc_id: str, retry_count: int, error: str
    ) -> None:
        """Handle processing failure with retry logic."""
        retry_count = retry_count or 0
        new_retry = retry_count + 1
        self._error_count_last_hour += 1

        if new_retry >= self._max_retries:
            await db.execute(
                """
                UPDATE documents
                SET processing_status = 'failed',
                    processing_error = ?,
                    retry_count = ?
                WHERE id = ?
                """,
                (error, new_retry, doc_id),
            )
            logger.warning(
                "document_failed_permanently",
                consumer=self.name,
                doc_id=doc_id,
                error=error,
                retries=new_retry,
            )
        else:
            # Reset to input status for retry
            await db.execute(
                """
                UPDATE documents
                SET processing_status = ?,
                    retry_count = ?,
                    processing_error = ?
                WHERE id = ?
                """,
                (self.input_status, new_retry, error, doc_id),
            )
            logger.info(
                "document_retry_scheduled",
                consumer=self.name,
                doc_id=doc_id,
                retry=new_retry,
                max_retries=self._max_retries,
            )
        await db.commit()

    def health(self) -> dict[str, Any]:
        """Return health info for this consumer."""
        avg_time_ms = 0.0
        if self._docs_processed_times:
            recent = self._docs_processed_times[-100:]
            avg_time_ms = (sum(recent) / len(recent)) * 1000

        return {
            "status": "running" if self._running else "stopped",
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            "docs_processed_last_hour": self._docs_processed_last_hour,
            "avg_processing_time_ms": round(avg_time_ms, 1),
            "error_count_last_hour": self._error_count_last_hour,
        }

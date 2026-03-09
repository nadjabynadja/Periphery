"""Crystallization consumer — triggers ontology updates for embedded documents.

Claims embedded documents in batches and feeds them into the Crystallizer
process for cluster detection, trajectory analysis, and relational gradient
extraction over the updated embedding space.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from periphery.db import get_connection
import structlog

from .consumer import StageConsumer

if TYPE_CHECKING:
    from periphery.crystallizer.worker import CrystallizerWorker

logger = structlog.get_logger(__name__)


class CrystallizationConsumer(StageConsumer):
    """Processes documents from embedded -> crystallizing -> crystallized.

    Doesn't process individual documents — triggers a Crystallizer update
    when enough new embedded documents are available.
    """

    input_status = "embedded"
    processing_status = "crystallizing"
    output_status = "crystallized"
    started_at_column = "crystallization_started_at"
    completed_at_column = "crystallization_completed_at"
    batch_size = 50

    def __init__(
        self,
        db_path: str,
        crystallizer_worker: CrystallizerWorker | None = None,
        *,
        min_batch_threshold: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__(db_path, **kwargs)
        self._worker = crystallizer_worker
        self._min_batch_threshold = min_batch_threshold

    def set_worker(self, worker: CrystallizerWorker) -> None:
        """Set the crystallizer worker (for deferred initialization)."""
        self._worker = worker

    async def _run_cycle(self) -> int:
        """Override to check minimum batch threshold before claiming."""
        async with get_connection(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")

            # Check queue depth before claiming
            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents WHERE processing_status = ?",
                (self.input_status,),
            )
            row = await cursor.fetchone()
            queue_depth = row[0] if row else 0

            if queue_depth < self._min_batch_threshold:
                return 0

        # Proceed with normal cycle
        return await super()._run_cycle()

    async def process(
        self, db: aiosqlite.Connection, doc_rows: list[dict[str, Any]]
    ) -> list[str]:
        """Trigger crystallization for the batch of embedded documents.

        Crystallization is a global operation over the entire embedding space,
        not a per-document transformation. If the crystallizer encounters an
        error, the documents themselves are not at fault — they have already
        been successfully embedded. We still advance them so they don't get
        permanently stuck as 'failed'.
        """
        if self._worker is None:
            logger.warning("crystallizer_worker_not_configured")
            return []

        doc_ids = [d["id"] for d in doc_rows]

        try:
            stats = await self._worker.crystallize()
            logger.info(
                "crystallization_triggered",
                batch_size=len(doc_ids),
                stats=stats,
            )
        except Exception:
            logger.exception("crystallization_failed", batch_size=len(doc_ids))
            # Crystallization failure is not a per-document failure — the docs
            # were already successfully embedded. Advance them so they don't
            # accumulate as permanently failed.

        return doc_ids

"""Entry point: python -m periphery.pipeline

Starts the full processing pipeline orchestrator.
"""

from __future__ import annotations

import asyncio
import logging

import structlog

from periphery.config import get_settings
from periphery.enrichment.pipeline import build_enrichment_pipeline

from .crystallization_consumer import CrystallizationConsumer
from .embedding_consumer import EmbeddingConsumer
from .enrichment_consumer import EnrichmentConsumer
from .orchestrator import PipelineOrchestrator

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
logger = structlog.get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    db_path = settings.pipeline_db_path

    enrichment_pipeline = build_enrichment_pipeline(settings)
    enrichment = EnrichmentConsumer(
        db_path,
        pipeline=enrichment_pipeline,
        batch_size=settings.pipeline_enrichment_batch_size,
        poll_interval=settings.pipeline_enrichment_poll_interval,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )
    embedding = EmbeddingConsumer(
        db_path,
        batch_size=settings.pipeline_embedding_batch_size,
        poll_interval=settings.pipeline_embedding_poll_interval,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )
    crystallization = CrystallizationConsumer(
        db_path,
        batch_size=settings.pipeline_crystallization_batch_size,
        poll_interval=settings.pipeline_crystallization_poll_interval,
        min_batch_threshold=settings.pipeline_crystallization_min_batch,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )

    orchestrator = PipelineOrchestrator(
        consumers=[enrichment, embedding, crystallization],
        db_path=db_path,
        restart_delay=settings.pipeline_consumer_restart_delay,
    )

    logger.info(
        "pipeline_starting",
        db_path=db_path,
        enrichment_batch=settings.pipeline_enrichment_batch_size,
        embedding_batch=settings.pipeline_embedding_batch_size,
        crystallization_batch=settings.pipeline_crystallization_batch_size,
    )

    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())

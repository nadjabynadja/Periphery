"""Entry point: python -m periphery.pipeline

Standalone enrichment pipeline process.  Initializes its own FAISS store,
multi-space indices, enrichment pipeline, and crystallizer worker, then runs
the pipeline orchestrator until interrupted.

Reads from collection databases (rss.db, gdelt.db, sanctions.db) and writes
enriched data to analytical.db.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path


import structlog

from periphery.config import get_settings
from periphery.critic.network import CoherenceCritic
from periphery.critic.persistence import CriticStore
from periphery.critic.runner import CriticRunner
from periphery.critic.trainer import CriticTrainer
from periphery.crystallizer.worker import CrystallizerWorker
from periphery.enrichment.pipeline import build_enrichment_pipeline
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore, MultiSpaceIndexManager

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

    # The pipeline writes to analytical.db
    db_path = settings.db_analytical_path

    # Collection DB paths for reading pending documents
    collection_db_paths = {
        "rss": settings.db_rss_path,
        "gdelt": settings.db_gdelt_path,
        "sanctions": settings.db_sanctions_path,
    }

    # Ensure required data directories exist
    for dir_path in [
        Path(settings.faiss_index_path).parent,
        Path(settings.embedding_index_dir),
        Path(settings.critic_checkpoint_dir),
        Path(settings.critic_training_dir),
        Path(db_path).parent,
    ]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Ensure collection databases exist
    from periphery.db import ensure_collection_database
    for coll_path in collection_db_paths.values():
        await ensure_collection_database(coll_path)

    # Initialize analytical database schema
    from periphery.db import ensure_database
    await ensure_database(db_path)

    # Initialize embedding model and vector store
    logger.info("pipeline_init_embedding", model=settings.embedding_model)
    dim = embedder.get_dimension()
    store = FAISSStore(dim=dim, index_path=settings.faiss_index_path)
    logger.info("pipeline_faiss_ready", dim=dim, vectors=store.total)

    # Initialize multi-space embedding indices
    geo_dim = settings.embedding_geospatial_base_dim + settings.embedding_region_count
    multi_space_manager = MultiSpaceIndexManager(
        index_dir=settings.embedding_index_dir,
        rebuild_interval=settings.embedding_rebuild_interval,
    )
    multi_space_manager.initialize({
        "semantic": dim,
        "entity": dim,
        "relational": dim,
        "temporal": settings.embedding_temporal_dim,
        "geospatial": geo_dim,
    })
    logger.info("pipeline_multi_space_ready", stats=multi_space_manager.stats())

    # Initialize document list for crystallizer
    from periphery.ingest.router import set_store, get_documents
    set_store(store)
    documents = get_documents()

    # Initialize Continuous Critic
    ensemble_weights = {
        "critic_neural": settings.critic_ensemble_weight_neural,
        "source_diversity": settings.critic_ensemble_weight_source_diversity,
        "temporal_consistency": settings.critic_ensemble_weight_temporal,
        "cross_space_agreement": settings.critic_ensemble_weight_cross_space,
        "stability": settings.critic_ensemble_weight_stability,
    }

    critic_model = CoherenceCritic()
    critic_trainer = CriticTrainer(
        model=critic_model,
        device=settings.device,
        checkpoint_dir=settings.critic_checkpoint_dir,
        training_dir=settings.critic_training_dir,
        max_checkpoints=settings.critic_max_checkpoints,
    )

    checkpoint_result = critic_trainer.load_checkpoint()
    if checkpoint_result.get("status") == "loaded":
        logger.info("pipeline_critic_checkpoint_loaded", version=checkpoint_result.get("version"))

    critic_store = CriticStore(settings.crystallizer_db_path)
    await critic_store.initialize()

    critic_runner = CriticRunner(
        model=critic_model,
        trainer=critic_trainer,
        store=critic_store,
        device=settings.device,
        retraining_interval_runs=settings.critic_retraining_interval_runs,
        retraining_interval_hours=settings.critic_retraining_interval_hours,
        fine_tune_epochs=settings.critic_fine_tune_epochs,
        perturbation_variants=settings.critic_perturbation_variants,
        ensemble_weights=ensemble_weights,
        drift_mean_threshold=settings.critic_drift_mean_threshold,
        drift_low_confidence_ratio=settings.critic_drift_low_confidence_ratio,
        drift_window_size=settings.critic_drift_window_size,
    )

    # Restore calibrator from checkpoint
    cal_params = checkpoint_result.get("calibration_params")
    if cal_params:
        critic_runner._calibrator.load_params(cal_params)

    # Load persisted state (confidence history, latest scores)
    await critic_runner.load_state()
    logger.info("pipeline_critic_initialized")

    # Initialize crystallizer worker
    worker = CrystallizerWorker(
        store=store,
        documents=documents,
        interval=settings.crystallizer_interval,
        multi_space_manager=multi_space_manager,
        db_path=settings.crystallizer_db_path,
        full_recluster_interval_docs=settings.crystallizer_full_recluster_interval_docs,
        full_recluster_interval_seconds=settings.crystallizer_full_recluster_interval_seconds,
        incremental_update_interval_seconds=settings.crystallizer_incremental_interval_seconds,
        min_cluster_size=settings.crystallizer_min_cluster_size,
        min_samples=settings.crystallizer_min_samples,
        cluster_selection_epsilon=settings.crystallizer_cluster_selection_epsilon,
        trajectory_min_snapshots=settings.crystallizer_trajectory_min_snapshots,
        auto_label_with_llm=settings.crystallizer_auto_label_with_llm,
        anthropic_api_key=settings.anthropic_api_key,
    )

    async def critic_callback(snapshot) -> None:
        """Score and train the critic after each clustering pass."""
        if snapshot is None:
            return
        try:
            await critic_runner.score_snapshot(snapshot)
            await critic_runner.maybe_retrain(snapshot)
        except Exception:
            logger.exception("pipeline_critic_scoring_failed")

    worker.on_crystallize = critic_callback

    # Build enrichment pipeline with persistent entity index
    from periphery.enrichment.stages.entity_resolution import EntityIndex
    entity_index = EntityIndex(db_path=db_path)
    await entity_index.load()

    enrichment_pipeline = build_enrichment_pipeline(settings, entity_index=entity_index)

    # Build consumers
    enrichment = EnrichmentConsumer(
        db_path,
        pipeline=enrichment_pipeline,
        collection_db_paths=collection_db_paths,
        batch_size=settings.pipeline_enrichment_batch_size,
        poll_interval=settings.pipeline_enrichment_poll_interval,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )
    embedding = EmbeddingConsumer(
        db_path,
        faiss_store=store,
        multi_space_manager=multi_space_manager,
        batch_size=settings.pipeline_embedding_batch_size,
        poll_interval=settings.pipeline_embedding_poll_interval,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )
    crystallization = CrystallizationConsumer(
        db_path,
        crystallizer_worker=worker,
        batch_size=settings.pipeline_crystallization_batch_size,
        poll_interval=settings.pipeline_crystallization_poll_interval,
        min_batch_threshold=settings.pipeline_crystallization_min_batch,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )

    # Wire inter-stage notifications
    enrichment._on_advance = embedding.notify
    embedding._on_advance = crystallization.notify

    orchestrator = PipelineOrchestrator(
        consumers=[enrichment, embedding, crystallization],
        db_path=db_path,
        restart_delay=settings.pipeline_consumer_restart_delay,
    )

    # Start crystallizer worker
    await worker.start()

    # Handle shutdown signals
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        logger.info("pipeline_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info(
        "pipeline_starting",
        db_path=db_path,
        collection_dbs=collection_db_paths,
        enrichment_batch=settings.pipeline_enrichment_batch_size,
        embedding_batch=settings.pipeline_embedding_batch_size,
        crystallization_batch=settings.pipeline_crystallization_batch_size,
    )

    # Run orchestrator in background, wait for stop signal
    orchestrator_task = asyncio.create_task(
        orchestrator.run(), name="pipeline-orchestrator"
    )

    await stop_event.wait()

    # Graceful shutdown
    logger.info("pipeline_shutting_down")
    await orchestrator.stop()
    orchestrator_task.cancel()
    try:
        await orchestrator_task
    except asyncio.CancelledError:
        pass

    await worker.stop()
    store.save()
    multi_space_manager.save()
    logger.info("pipeline_stopped")


if __name__ == "__main__":
    asyncio.run(main())

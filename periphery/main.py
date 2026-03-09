import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from periphery.config import get_settings
from periphery.critic.network import CoherenceCritic, CoherenceNet
from periphery.critic.persistence import CriticStore
from periphery.critic.runner import CriticRunner
from periphery.critic.scoring import score_all_clusters
from periphery.critic.trainer import AdversarialTrainer, CriticTrainer
from periphery.crystallizer.worker import CrystallizerWorker
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore, MultiSpaceIndexManager
from periphery.pipeline.crystallization_consumer import CrystallizationConsumer
from periphery.pipeline.embedding_consumer import EmbeddingConsumer
from periphery.enrichment.pipeline import build_enrichment_pipeline
from periphery.pipeline.enrichment_consumer import EnrichmentConsumer
from periphery.pipeline.orchestrator import PipelineOrchestrator
from periphery.query.analytical_engine import AnalyticalQueryEngine

logger = logging.getLogger(__name__)

# App-level singletons
store: FAISSStore | None = None
multi_space_manager: MultiSpaceIndexManager | None = None
worker: CrystallizerWorker | None = None
coherence_net: CoherenceNet | None = None
trainer: AdversarialTrainer | None = None
critic_model: CoherenceCritic | None = None
critic_trainer: CriticTrainer | None = None
critic_runner: CriticRunner | None = None
critic_store: CriticStore | None = None
pipeline_orchestrator: PipelineOrchestrator | None = None
analytical_engine: AnalyticalQueryEngine | None = None
_pipeline_task: asyncio.Task | None = None


async def critic_callback(vectors: np.ndarray, labels: np.ndarray) -> dict[int, float]:
    """Called by crystallizer after each clustering pass to score + train the critic."""
    if coherence_net is None or trainer is None:
        return {}

    # Legacy pair-based training
    train_results = trainer.train_multiple(vectors, labels, epochs=5)
    logger.info("Critic training: %s", train_results[-1] if train_results else "no results")

    # Score all clusters (legacy)
    scores = score_all_clusters(coherence_net, vectors, labels)

    # Run new Critic scoring on current snapshot if available
    if critic_runner is not None and worker is not None and worker.current_snapshot is not None:
        try:
            await critic_runner.score_snapshot(worker.current_snapshot)
            await critic_runner.maybe_retrain(worker.current_snapshot)
        except Exception:
            logger.exception("critic_runner_scoring_failed")

    # Keep the analytical query engine's snapshot in sync
    if analytical_engine is not None and worker is not None:
        analytical_engine.snapshot = worker.current_snapshot

    return scores


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize all layers on startup, cleanup on shutdown."""
    global store, multi_space_manager, worker, coherence_net, trainer
    global critic_model, critic_trainer, critic_runner, critic_store
    global pipeline_orchestrator, analytical_engine, _pipeline_task

    settings = get_settings()

    # Layer 1: Initialize embedding model and vector store
    logger.info("Initializing embedding model: %s", settings.embedding_model)
    dim = embedder.get_dimension()
    store = FAISSStore(dim=dim, index_path=settings.faiss_index_path)
    logger.info("FAISS store ready (dim=%d, vectors=%d)", dim, store.total)

    # Layer 1b: Initialize multi-space embedding indices
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
    logger.info("Multi-space indices ready: %s", multi_space_manager.stats())

    # Wire up ingest router
    from periphery.ingest.router import set_store, get_documents
    set_store(store)
    documents = get_documents()

    # Layer 3: Initialize critic network (legacy pair-based)
    coherence_net = CoherenceNet(dim=dim)
    trainer = AdversarialTrainer(coherence_net, device=settings.device)

    # Layer 3b: Initialize new Continuous Critic
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

    # Try to load existing checkpoint
    checkpoint_result = critic_trainer.load_checkpoint()
    if checkpoint_result.get("status") == "loaded":
        logger.info("Loaded Critic checkpoint v%s", checkpoint_result.get("version"))

    # Initialize critic persistence
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
    )
    logger.info("Continuous Critic initialized")

    # Layer 2: Start crystallizer worker (full analytical engine)
    db_path = settings.pipeline_db_path
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
    worker.on_crystallize = critic_callback

    from periphery.crystallizer.router import set_worker
    set_worker(worker)

    # Wire up critic router
    from periphery.critic.router import set_critic_state
    set_critic_state({
        "model": coherence_net,
        "legacy_trainer": trainer,
        "trainer": critic_trainer,
        "runner": critic_runner,
        "critic_store": critic_store,
        "worker": worker,
        "store": store,
    })

    # Layer 4: Initialize query engine (legacy)
    from periphery.query.engine import QueryEngine
    from periphery.query.router import set_engine
    engine = QueryEngine(
        store=store,
        documents=documents,
        graph=worker.graph,
        db_path=db_path,
    )
    set_engine(engine)

    # Layer 4b: Initialize analytical query engine (new NLP-powered pipeline)
    from periphery.query.api import set_analytical_engine, set_crystallizer_worker
    analytical_engine = AnalyticalQueryEngine(
        faiss_store=store,
        multi_space=multi_space_manager,
        entity_index=None,  # wired below after enrichment pipeline is built
        anthropic_api_key=settings.anthropic_api_key,
        db_path=db_path,
        llm_model=settings.enrichment_llm_model,
    )
    # Seed the snapshot from the crystallizer
    analytical_engine.snapshot = worker.current_snapshot
    await analytical_engine.initialize()
    set_analytical_engine(analytical_engine)
    set_crystallizer_worker(worker)
    logger.info("Analytical query engine initialized")

    # Layer 5: Initialize processing pipeline
    enrichment_pipeline = build_enrichment_pipeline(settings)
    enrichment_consumer = EnrichmentConsumer(
        db_path,
        pipeline=enrichment_pipeline,
        batch_size=settings.pipeline_enrichment_batch_size,
        poll_interval=settings.pipeline_enrichment_poll_interval,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )
    embedding_consumer = EmbeddingConsumer(
        db_path,
        faiss_store=store,
        multi_space_manager=multi_space_manager,
        batch_size=settings.pipeline_embedding_batch_size,
        poll_interval=settings.pipeline_embedding_poll_interval,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )
    crystallization_consumer = CrystallizationConsumer(
        db_path,
        crystallizer_worker=worker,
        batch_size=settings.pipeline_crystallization_batch_size,
        poll_interval=settings.pipeline_crystallization_poll_interval,
        min_batch_threshold=settings.pipeline_crystallization_min_batch,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )

    # Wire entity index from enrichment pipeline to analytical engine
    for stage in enrichment_pipeline.stages:
        if hasattr(stage, 'entity_index'):
            analytical_engine._preprocessor._entity_index = stage.entity_index
            analytical_engine._retriever._entity_index = stage.entity_index
            logger.info("Wired entity index to analytical engine (%d entities)", len(stage.entity_index))
            break

    # Wire inter-stage notifications
    enrichment_consumer._on_advance = embedding_consumer.notify
    embedding_consumer._on_advance = crystallization_consumer.notify

    pipeline_orchestrator = PipelineOrchestrator(
        consumers=[enrichment_consumer, embedding_consumer, crystallization_consumer],
        db_path=db_path,
        restart_delay=settings.pipeline_consumer_restart_delay,
    )

    from periphery.pipeline.router import set_orchestrator, set_multi_space_manager
    set_orchestrator(pipeline_orchestrator)
    set_multi_space_manager(multi_space_manager)

    # Start background crystallizer
    await worker.start()

    # Start pipeline orchestrator as background task
    _pipeline_task = asyncio.create_task(
        pipeline_orchestrator.run(), name="pipeline-orchestrator"
    )

    logger.info("Periphery system initialized — all layers active, pipeline running")

    yield

    # Shutdown
    if pipeline_orchestrator:
        await pipeline_orchestrator.stop()
    if _pipeline_task:
        _pipeline_task.cancel()
        try:
            await _pipeline_task
        except asyncio.CancelledError:
            pass
    await worker.stop()
    store.save()
    if multi_space_manager:
        multi_space_manager.save()
    logger.info("Periphery system shut down")


app = FastAPI(
    title="Periphery",
    description="Data infrastructure where schema is emergent observation, not predefined imposition",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
from periphery.ingest.router import router as ingest_router
from periphery.crystallizer.router import router as crystallizer_router
from periphery.critic.router import router as critic_router
from periphery.query.router import router as query_router
from periphery.query.api import router as query_api_router
from periphery.pipeline.router import router as pipeline_router

app.include_router(ingest_router)
app.include_router(crystallizer_router)
app.include_router(critic_router)
app.include_router(query_router)
app.include_router(query_api_router)
app.include_router(pipeline_router)


@app.get("/")
async def root():
    return {
        "name": "Periphery",
        "version": "0.1.0",
        "principle": "Schema is observation, not imposition",
        "layers": {
            "ingest": "/ingest",
            "crystallizer": "/crystallizer",
            "critic": "/critic",
            "query": "/query",
            "api": "/api",
            "pipeline": "/pipeline",
        },
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "vectors": store.total if store else 0,
        "clusters": len(worker.clusters) if worker else 0,
        "last_crystallization": worker.last_run.isoformat() if worker and worker.last_run else None,
        "pipeline": pipeline_orchestrator is not None,
    }


# Serve frontend static files if built
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/app", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

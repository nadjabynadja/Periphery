import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from periphery.config import get_settings
from periphery.critic.network import CoherenceCritic
from periphery.critic.persistence import CriticStore
from periphery.critic.runner import CriticRunner
from periphery.critic.trainer import CriticTrainer
from periphery.crystallizer.worker import CrystallizerWorker
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore, MultiSpaceIndexManager
from periphery.query.analytical_engine import AnalyticalQueryEngine

logger = logging.getLogger(__name__)

# App-level singletons
store: FAISSStore | None = None
multi_space_manager: MultiSpaceIndexManager | None = None
worker: CrystallizerWorker | None = None
critic_model: CoherenceCritic | None = None
critic_trainer: CriticTrainer | None = None
critic_runner: CriticRunner | None = None
critic_store: CriticStore | None = None
analytical_engine: AnalyticalQueryEngine | None = None


async def critic_callback(vectors: np.ndarray, labels: np.ndarray) -> dict[int, float]:
    """Called by crystallizer after each clustering pass to score + train the critic."""
    if worker is None or worker.current_snapshot is None:
        return {}

    scores: dict[int, float] = {}
    if critic_runner is not None:
        try:
            await critic_runner.score_snapshot(worker.current_snapshot)
            await critic_runner.maybe_retrain(worker.current_snapshot)
            scores = {
                int(s["id"]): s["confidence"]
                for s in critic_runner.last_scoring_results
                if s.get("type") == "cluster"
                and s.get("id", "").isdigit()
            }
        except Exception:
            logger.exception("critic_runner_scoring_failed")

    # Keep the analytical query engine's snapshot in sync
    if analytical_engine is not None:
        analytical_engine.snapshot = worker.current_snapshot

    # Broadcast new snapshot to WebSocket clients
    if worker.current_snapshot is not None:
        from periphery.ws.router import broadcast_snapshot, set_current_snapshot
        set_current_snapshot(worker.current_snapshot)
        await broadcast_snapshot(worker.current_snapshot)

    return scores


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize query-serving layers on startup.

    The RSS ingest daemon and enrichment pipeline run as separate processes.
    This server handles API endpoints, query engines, and WebSocket updates.
    """
    global store, multi_space_manager, worker
    global critic_model, critic_trainer, critic_runner, critic_store
    global analytical_engine

    settings = get_settings()

    # Ensure required data directories exist
    for dir_path in [
        Path(settings.faiss_index_path).parent,
        Path(settings.embedding_index_dir),
        Path(settings.critic_checkpoint_dir),
        Path(settings.critic_training_dir),
        Path(settings.pipeline_db_path).parent,
    ]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Initialize database schema before any component starts
    from periphery.db import ensure_database
    await ensure_database(settings.pipeline_db_path)

    # Initialize full-text search indexes
    from periphery.search.setup import initialize_fts, rebuild_search_indexes
    await initialize_fts(settings.pipeline_db_path)
    await rebuild_search_indexes(settings.pipeline_db_path, force=True)
    logger.info("Full-text search indexes initialized")

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

    # Layer 3: Initialize Continuous Critic
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
    logger.info("Continuous Critic initialized")

    # Layer 2: Start crystallizer worker (for query serving)
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
        "runner": critic_runner,
        "critic_store": critic_store,
        "worker": worker,
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

    # Load persistent entity index for the query engine
    from periphery.enrichment.stages.entity_resolution import EntityIndex
    entity_index = EntityIndex(db_path=db_path)
    await entity_index.load()
    logger.info("Entity index loaded for query engine: %d entities", len(entity_index))

    # Layer 4b: Initialize analytical query engine (new NLP-powered pipeline)
    from periphery.query.api import set_analytical_engine, set_crystallizer_worker, set_entity_index
    set_entity_index(entity_index)
    analytical_engine = AnalyticalQueryEngine(
        faiss_store=store,
        multi_space=multi_space_manager,
        entity_index=entity_index,
        anthropic_api_key=settings.anthropic_api_key,
        exa_api_key=settings.exa_api_key,
        db_path=db_path,
        llm_model=settings.enrichment_llm_model,
    )
    # Seed the snapshot from the crystallizer
    analytical_engine.snapshot = worker.current_snapshot
    from periphery.ws.router import set_current_snapshot as _ws_set_snapshot
    _ws_set_snapshot(worker.current_snapshot)
    await analytical_engine.initialize()
    set_analytical_engine(analytical_engine)
    set_crystallizer_worker(worker)
    logger.info("Analytical query engine initialized")

    # Wire pipeline router with read-only status access (no orchestrator running here)
    from periphery.pipeline.router import set_multi_space_manager
    set_multi_space_manager(multi_space_manager)

    # Start background crystallizer
    await worker.start()

    logger.info("Periphery API server initialized — query layers active")

    yield

    # Shutdown
    await worker.stop()

    store.save()
    if multi_space_manager:
        multi_space_manager.save()
    logger.info("Periphery API server shut down")


app = FastAPI(
    title="Periphery",
    description="Data infrastructure where schema is emergent observation, not predefined imposition",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — configurable via CORS_ORIGINS env var (comma-separated)
_settings = get_settings()
_cors_origins = [o.strip() for o in _settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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
from periphery.ws.router import router as ws_router
from periphery.commands.router import router as commands_router
from periphery.search.router import router as search_router
from periphery.auth.router import router as auth_router

# Set search router db_path
from periphery.search.router import set_db_path as _set_search_db_path
_set_search_db_path(_settings.pipeline_db_path)

app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(crystallizer_router)
app.include_router(critic_router)
app.include_router(query_router)
app.include_router(query_api_router)
app.include_router(pipeline_router)
app.include_router(ws_router)
app.include_router(commands_router)
app.include_router(search_router)


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
    }


# Serve frontend static files if built
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/app", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

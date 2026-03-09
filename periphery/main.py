import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from periphery.config import get_settings
from periphery.critic.network import CoherenceNet
from periphery.critic.scoring import score_all_clusters
from periphery.critic.trainer import AdversarialTrainer
from periphery.crystallizer.worker import CrystallizerWorker
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore
from periphery.pipeline.crystallization_consumer import CrystallizationConsumer
from periphery.pipeline.embedding_consumer import EmbeddingConsumer
from periphery.pipeline.enrichment_consumer import EnrichmentConsumer
from periphery.pipeline.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)

# App-level singletons
store: FAISSStore | None = None
worker: CrystallizerWorker | None = None
coherence_net: CoherenceNet | None = None
trainer: AdversarialTrainer | None = None
pipeline_orchestrator: PipelineOrchestrator | None = None
_pipeline_task: asyncio.Task | None = None


async def critic_callback(vectors: np.ndarray, labels: np.ndarray) -> dict[int, float]:
    """Called by crystallizer after each clustering pass to score + train the critic."""
    if coherence_net is None or trainer is None:
        return {}

    # Train on current structure
    train_results = trainer.train_multiple(vectors, labels, epochs=5)
    logger.info("Critic training: %s", train_results[-1] if train_results else "no results")

    # Score all clusters
    scores = score_all_clusters(coherence_net, vectors, labels)
    return scores


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize all layers on startup, cleanup on shutdown."""
    global store, worker, coherence_net, trainer, pipeline_orchestrator, _pipeline_task

    settings = get_settings()

    # Layer 1: Initialize embedding model and vector store
    logger.info("Initializing embedding model: %s", settings.embedding_model)
    dim = embedder.get_dimension()
    store = FAISSStore(dim=dim, index_path=settings.faiss_index_path)
    logger.info("FAISS store ready (dim=%d, vectors=%d)", dim, store.total)

    # Wire up ingest router
    from periphery.ingest.router import set_store, get_documents
    set_store(store)
    documents = get_documents()

    # Layer 3: Initialize critic network
    coherence_net = CoherenceNet(dim=dim)
    trainer = AdversarialTrainer(coherence_net, device=settings.device)

    # Layer 2: Start crystallizer worker
    worker = CrystallizerWorker(
        store=store,
        documents=documents,
        interval=settings.crystallizer_interval,
    )
    worker.on_crystallize = critic_callback

    from periphery.crystallizer.router import set_worker
    set_worker(worker)

    # Wire up critic router
    from periphery.critic.router import set_critic_state
    set_critic_state({
        "model": coherence_net,
        "trainer": trainer,
        "worker": worker,
        "store": store,
    })

    # Layer 4: Initialize query engine
    from periphery.query.engine import QueryEngine
    from periphery.query.router import set_engine
    engine = QueryEngine(
        store=store,
        documents=documents,
        graph=worker.graph,
    )
    set_engine(engine)

    # Layer 5: Initialize processing pipeline
    db_path = settings.pipeline_db_path
    enrichment_consumer = EnrichmentConsumer(
        db_path,
        batch_size=settings.pipeline_enrichment_batch_size,
        poll_interval=settings.pipeline_enrichment_poll_interval,
        max_retries=settings.pipeline_max_retries,
        stale_claim_timeout=settings.pipeline_stale_claim_timeout_seconds,
    )
    embedding_consumer = EmbeddingConsumer(
        db_path,
        faiss_store=store,
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

    # Wire inter-stage notifications
    enrichment_consumer._on_advance = embedding_consumer.notify
    embedding_consumer._on_advance = crystallization_consumer.notify

    pipeline_orchestrator = PipelineOrchestrator(
        consumers=[enrichment_consumer, embedding_consumer, crystallization_consumer],
        db_path=db_path,
        restart_delay=settings.pipeline_consumer_restart_delay,
    )

    from periphery.pipeline.router import set_orchestrator
    set_orchestrator(pipeline_orchestrator)

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
from periphery.pipeline.router import router as pipeline_router

app.include_router(ingest_router)
app.include_router(crystallizer_router)
app.include_router(critic_router)
app.include_router(query_router)
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

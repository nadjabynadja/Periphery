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

logger = logging.getLogger(__name__)

# App-level singletons
store: FAISSStore | None = None
worker: CrystallizerWorker | None = None
coherence_net: CoherenceNet | None = None
trainer: AdversarialTrainer | None = None


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
    global store, worker, coherence_net, trainer

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

    # Start background crystallizer
    await worker.start()
    logger.info("Periphery system initialized — all layers active")

    yield

    # Shutdown
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

app.include_router(ingest_router)
app.include_router(crystallizer_router)
app.include_router(critic_router)
app.include_router(query_router)


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

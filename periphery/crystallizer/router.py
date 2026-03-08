from fastapi import APIRouter

from periphery.models import Cluster, OntologySnapshot

router = APIRouter(prefix="/crystallizer", tags=["crystallizer"])

# Set by main.py on startup
_worker = None


def set_worker(worker) -> None:
    global _worker
    _worker = worker


def get_worker():
    assert _worker is not None, "Crystallizer not initialized"
    return _worker


@router.get("/clusters", response_model=list[Cluster])
async def get_clusters():
    """Get current cluster assignments."""
    worker = get_worker()
    return worker.clusters


@router.get("/graph", response_model=OntologySnapshot)
async def get_graph():
    """Get the full emergent ontology graph."""
    worker = get_worker()
    return worker.graph.to_snapshot()


@router.get("/graph/{node_id}", response_model=OntologySnapshot)
async def get_subgraph(node_id: str, depth: int = 2):
    """Get a subgraph around a specific node."""
    worker = get_worker()
    return worker.graph.get_subgraph(node_id, depth=depth)


@router.post("/crystallize")
async def trigger_crystallize():
    """Trigger an immediate crystallization pass."""
    worker = get_worker()
    stats = await worker.crystallize()
    return {"status": "complete", **stats}


@router.get("/stats")
async def get_stats():
    """Get crystallizer statistics."""
    worker = get_worker()
    return {
        "last_run": worker.last_run.isoformat() if worker.last_run else None,
        "cluster_count": len(worker.clusters),
        "stats": worker.stats,
    }


@router.get("/bridges")
async def get_bridges():
    """Get documents that bridge separate clusters."""
    worker = get_worker()
    return {"bridge_documents": worker.graph.find_bridges()}

"""Crystallizer API routes — expose ontology snapshots and telemetry."""

from __future__ import annotations

from typing import Any

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
    """Get current cluster assignments (legacy format)."""
    worker = get_worker()
    return worker.clusters


@router.get("/graph", response_model=OntologySnapshot)
async def get_graph():
    """Get the full emergent ontology graph (legacy format)."""
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
    """Get crystallizer statistics and telemetry."""
    worker = get_worker()
    result: dict[str, Any] = {
        "last_run": worker.last_run.isoformat() if worker.last_run else None,
        "cluster_count": len(worker.clusters),
        "stats": worker.stats,
    }

    snapshot = worker.current_snapshot
    if snapshot:
        result["snapshot"] = {
            "snapshot_id": snapshot.snapshot_id,
            "generated_at": snapshot.generated_at.isoformat(),
            "corpus_stats": snapshot.corpus_stats.model_dump(),
            "num_clusters": len(snapshot.clusters),
            "num_anomalies": len(snapshot.anomalies),
            "num_trajectories": len(snapshot.trajectories),
            "num_gradients": len(snapshot.relational_gradients),
            "num_emerging": len(snapshot.emerging_structures),
            "num_convergence_alerts": len(snapshot.convergence_alerts),
        }

        status_counts: dict[str, int] = {}
        for c in snapshot.clusters:
            status_counts[c.status] = status_counts.get(c.status, 0) + 1
        result["clusters_by_status"] = status_counts

        pattern_counts: dict[str, int] = {}
        for t in snapshot.trajectories:
            pattern_counts[t.pattern] = pattern_counts.get(t.pattern, 0) + 1
        result["trajectories_by_pattern"] = pattern_counts

        anomaly_counts: dict[str, int] = {}
        for a in snapshot.anomalies:
            anomaly_counts[a.anomaly_type] = anomaly_counts.get(a.anomaly_type, 0) + 1
        result["anomalies_by_type"] = anomaly_counts

    return result


@router.get("/snapshot")
async def get_snapshot():
    """Get the full living ontology snapshot."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return {"status": "no_snapshot", "message": "No crystallization has been performed yet"}
    return snapshot.model_dump()


@router.get("/snapshot/clusters")
async def get_detected_clusters():
    """Get all detected clusters from the current snapshot."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return []
    return [c.model_dump() for c in snapshot.clusters]


@router.get("/snapshot/clusters/{cluster_id}")
async def get_detected_cluster(cluster_id: str):
    """Get a specific detected cluster."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return {"error": "No snapshot available"}
    for c in snapshot.clusters:
        if c.cluster_id == cluster_id:
            return c.model_dump()
    return {"error": "Cluster not found"}


@router.get("/snapshot/anomalies")
async def get_anomalies():
    """Get all unresolved anomalies from the current snapshot."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return []
    return [a.model_dump() for a in snapshot.anomalies]


@router.get("/snapshot/trajectories")
async def get_trajectories():
    """Get all trajectories from the current snapshot."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return []
    return [t.model_dump() for t in snapshot.trajectories]


@router.get("/snapshot/gradients")
async def get_gradients():
    """Get top relational gradients from the current snapshot."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return []
    sorted_gradients = sorted(
        snapshot.relational_gradients,
        key=lambda g: g.gradient_score,
        reverse=True,
    )
    return [g.model_dump() for g in sorted_gradients[:50]]


@router.get("/snapshot/convergences")
async def get_convergences():
    """Get convergence alerts from the current snapshot."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return []
    return [a.model_dump() for a in snapshot.convergence_alerts]


@router.get("/snapshot/emerging")
async def get_emerging():
    """Get emerging structures from the current snapshot."""
    worker = get_worker()
    snapshot = worker.current_snapshot
    if snapshot is None:
        return []
    return [e.model_dump() for e in snapshot.emerging_structures]


@router.get("/bridges")
async def get_bridges():
    """Get documents that bridge separate clusters."""
    worker = get_worker()
    return {"bridge_documents": worker.graph.find_bridges()}

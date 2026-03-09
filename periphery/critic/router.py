"""Critic API endpoints.

Exposes scoring results, monitoring stats, training triggers,
and confidence explanations.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from periphery.models import CriticScore

router = APIRouter(prefix="/critic", tags=["critic"])

# Set by main.py on startup
_critic_state: dict[str, Any] | None = None


def set_critic_state(state: dict[str, Any]) -> None:
    global _critic_state
    _critic_state = state


def get_critic_state() -> dict[str, Any]:
    assert _critic_state is not None, "Critic not initialized"
    return _critic_state


@router.get("/scores", response_model=list[CriticScore])
async def get_scores():
    """Get coherence scores for all clusters."""
    state = get_critic_state()
    clusters = state["worker"].clusters
    return [
        CriticScore(
            cluster_id=c.id,
            coherence_score=c.coherence_score or 0.0,
            document_count=len(c.document_ids),
        )
        for c in clusters
    ]


@router.get("/monitoring")
async def get_monitoring():
    """Get Critic monitoring stats: model version, score distribution, alerts."""
    state = get_critic_state()
    runner = state.get("runner")
    if runner is None:
        return {"status": "not_initialized"}
    return runner.get_monitoring_stats()


@router.get("/explanations")
async def get_explanations(limit: int = 20):
    """Get confidence explanations for recently scored structures."""
    state = get_critic_state()
    runner = state.get("runner")
    if runner is None:
        return {"explanations": []}

    results = runner.last_scoring_results
    explanations = []
    for s in results[:limit]:
        explanations.append({
            "id": s.get("id", ""),
            "type": s.get("type", ""),
            "confidence": s.get("confidence", 0.0),
            "confidence_raw": s.get("confidence_raw", 0.0),
            "confidence_calibrated": s.get("confidence_calibrated", 0.0),
            "signal_scores": s.get("signal_scores", {}),
            "explanation": s.get("explanation", {}),
        })
    return {"explanations": explanations}


@router.get("/score-trend")
async def get_score_trend():
    """Get confidence score trend over time."""
    state = get_critic_state()
    critic_store = state.get("critic_store")
    if critic_store is None:
        return {"trend": []}

    trend = await critic_store.get_score_trend()
    return {"trend": trend}


@router.post("/retrain")
async def trigger_retrain():
    """Manually trigger Critic retraining on current snapshot."""
    state = get_critic_state()
    runner = state.get("runner")
    worker = state.get("worker")

    if runner is None or worker is None:
        return {"status": "not_initialized"}

    snapshot = worker.current_snapshot
    if snapshot is None:
        return {"status": "no_snapshot"}

    result = await runner.maybe_retrain(snapshot)
    if result is None:
        # Force retrain even if not scheduled
        from periphery.critic.perturbations import PerturbationEngine

        engine = PerturbationEngine()
        samples = engine.generate_dataset(
            clusters=snapshot.clusters,
            gradients=snapshot.relational_gradients,
            trajectories=snapshot.trajectories,
        )
        if not samples:
            return {"status": "no_data"}

        trainer = state.get("trainer")
        if trainer is None:
            return {"status": "no_trainer"}

        result = trainer.retrain_with_rollback(samples)

    return {"training_result": result}


@router.post("/score-snapshot")
async def score_current_snapshot():
    """Score the current Crystallizer snapshot."""
    state = get_critic_state()
    runner = state.get("runner")
    worker = state.get("worker")

    if runner is None or worker is None:
        return {"status": "not_initialized"}

    snapshot = worker.current_snapshot
    if snapshot is None:
        return {"status": "no_snapshot"}

    result = await runner.score_snapshot(snapshot)
    return result


@router.post("/evaluate")
async def evaluate_document(document_id: str):
    """Evaluate coherence of a specific document within its cluster (legacy)."""
    from periphery.critic.scoring import score_document

    state = get_critic_state()
    store = state["store"]
    worker = state["worker"]
    model = state["model"]

    doc_ids = store.get_all_ids()
    if document_id not in doc_ids:
        return {"error": "Document not found"}

    if worker.labels is None:
        return {"error": "No clustering results available"}

    idx = doc_ids.index(document_id)
    label = int(worker.labels[idx])

    if label == -1:
        return {"document_id": document_id, "cluster_id": -1, "coherence_score": 0.0, "status": "noise"}

    vectors = store.get_all_vectors()
    doc_vec = vectors[idx]
    cluster_mask = worker.labels == label
    cluster_vecs = vectors[cluster_mask]

    score = score_document(model, doc_vec, cluster_vecs)
    return {"document_id": document_id, "cluster_id": label, "coherence_score": score}


@router.get("/outliers")
async def get_outliers(limit: int = 10):
    """Get structures with lowest coherence scores."""
    state = get_critic_state()
    runner = state.get("runner")

    if runner and runner.last_scoring_results:
        results = sorted(runner.last_scoring_results, key=lambda s: s.get("confidence", 0.0))
        return {
            "outliers": [
                {
                    "id": s.get("id", ""),
                    "type": s.get("type", ""),
                    "confidence": s.get("confidence", 0.0),
                }
                for s in results[:limit]
            ]
        }

    # Legacy fallback
    from periphery.critic.scoring import score_document

    store = state["store"]
    worker = state["worker"]
    model = state["model"]

    if worker.labels is None:
        return {"outliers": []}

    doc_ids = store.get_all_ids()
    vectors = store.get_all_vectors()
    scores = []

    for i, doc_id in enumerate(doc_ids):
        label = int(worker.labels[i])
        if label == -1:
            scores.append((doc_id, label, 0.0))
            continue
        cluster_mask = worker.labels == label
        cluster_vecs = vectors[cluster_mask]
        s = score_document(model, vectors[i], cluster_vecs)
        scores.append((doc_id, label, s))

    scores.sort(key=lambda x: x[2])
    return {
        "outliers": [
            {"document_id": did, "cluster_id": cid, "coherence_score": s}
            for did, cid, s in scores[:limit]
        ]
    }


@router.post("/train")
async def trigger_training(epochs: int = 10):
    """Trigger adversarial training of the critic network (legacy)."""
    state = get_critic_state()
    store = state["store"]
    worker = state["worker"]
    legacy_trainer = state.get("legacy_trainer")

    if legacy_trainer is None:
        return {"status": "not_available", "reason": "use /critic/retrain instead"}

    if worker.labels is None:
        return {"status": "skipped", "reason": "no_clustering_results"}

    vectors = store.get_all_vectors()
    results = legacy_trainer.train_multiple(vectors, worker.labels, epochs=epochs)
    return {"training_results": results}

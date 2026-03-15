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
    """Get coherence scores for all scored structures."""
    state = get_critic_state()
    runner = state.get("runner")
    if runner is None:
        return []

    results = runner.last_scoring_results
    if not results:
        # Fall back to persisted scores
        critic_store = state.get("critic_store")
        if critic_store:
            results = await critic_store.get_latest_scores()
            return [
                CriticScore(
                    structure_id=s.get("structure_id", ""),
                    structure_type=s.get("structure_type", ""),
                    confidence=s.get("confidence", 0.0),
                    confidence_raw=s.get("confidence_raw", 0.0),
                    confidence_calibrated=s.get("confidence_calibrated", 0.0),
                    signal_scores=s.get("signal_scores", {}),
                )
                for s in results
            ]
        return []

    return [
        CriticScore(
            structure_id=s.get("id", ""),
            structure_type=s.get("type", ""),
            confidence=s.get("confidence", 0.0),
            confidence_raw=s.get("confidence_raw", 0.0),
            confidence_calibrated=s.get("confidence_calibrated", 0.0),
            signal_scores=s.get("signal_scores", {}),
        )
        for s in results
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

    result = await runner.force_retrain(snapshot)
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


@router.get("/outliers")
async def get_outliers(limit: int = 10):
    """Get structures with lowest coherence scores."""
    state = get_critic_state()
    runner = state.get("runner")

    if not runner or not runner.last_scoring_results:
        return {"outliers": []}

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

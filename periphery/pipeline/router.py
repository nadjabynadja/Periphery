"""Pipeline stats API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .orchestrator import PipelineOrchestrator

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

_orchestrator: PipelineOrchestrator | None = None


def set_orchestrator(orchestrator: PipelineOrchestrator) -> None:
    """Bind the running orchestrator so the endpoint can read its state."""
    global _orchestrator  # noqa: PLW0603
    _orchestrator = orchestrator


@router.get("/stats")
async def pipeline_stats() -> dict[str, Any]:
    """Return full pipeline state: status counts, throughput, failures, consumer health."""
    if _orchestrator is None:
        return {
            "pipeline_status": {},
            "throughput": {},
            "failures": {"total_failed": 0, "failed_last_hour": 0, "top_failure_reasons": []},
            "consumers": {},
        }
    return await _orchestrator.get_pipeline_stats()

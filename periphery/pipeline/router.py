"""Pipeline stats API endpoints."""

from __future__ import annotations

from typing import Any

from periphery.db import get_connection
from fastapi import APIRouter

from periphery.ingest.store import MultiSpaceIndexManager

from .orchestrator import PipelineOrchestrator

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

_orchestrator: PipelineOrchestrator | None = None
_multi_space_manager: MultiSpaceIndexManager | None = None
_sources_daemon: Any = None


def set_sources_daemon(daemon: Any) -> None:
    """Bind the sources daemon for the health endpoint."""
    global _sources_daemon  # noqa: PLW0603
    _sources_daemon = daemon


def set_orchestrator(orchestrator: PipelineOrchestrator) -> None:
    """Bind the running orchestrator so the endpoint can read its state."""
    global _orchestrator  # noqa: PLW0603
    _orchestrator = orchestrator


def set_multi_space_manager(manager: MultiSpaceIndexManager) -> None:
    """Bind the multi-space index manager for stats."""
    global _multi_space_manager  # noqa: PLW0603
    _multi_space_manager = manager


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


@router.get("/embedding-stats")
async def embedding_stats() -> dict[str, Any]:
    """Return multi-space embedding index statistics and completeness distribution."""
    result: dict[str, Any] = {}

    # Index stats from multi-space manager
    if _multi_space_manager is not None:
        result["indices"] = _multi_space_manager.stats()
    else:
        result["indices"] = {}

    # Completeness distribution from SQLite
    if _orchestrator is not None:
        db_path = _orchestrator._db_path
        async with get_connection(db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")

            # Total embedded
            cursor = await db.execute(
                "SELECT COUNT(*) FROM document_embeddings"
            )
            row = await cursor.fetchone()
            result["total_embedded"] = row[0] if row else 0

            # Completeness distribution
            cursor = await db.execute(
                "SELECT completeness FROM document_embeddings WHERE completeness IS NOT NULL"
            )
            rows = await cursor.fetchall()
            full_count = 0
            partial_count = 0
            space_counts: dict[str, int] = {
                "semantic": 0, "entity": 0, "relational": 0,
                "temporal": 0, "geospatial": 0,
            }
            for row in rows:
                try:
                    import json
                    comp = json.loads(row[0])
                    all_complete = all(comp.values())
                    if all_complete:
                        full_count += 1
                    else:
                        partial_count += 1
                    for space, present in comp.items():
                        if present and space in space_counts:
                            space_counts[space] += 1
                except (json.JSONDecodeError, TypeError, AttributeError):
                    partial_count += 1

            result["completeness"] = {
                "full_enrichment": full_count,
                "partial_enrichment": partial_count,
                "per_space": space_counts,
            }

            # Embedding model info
            cursor = await db.execute(
                "SELECT DISTINCT embedding_model, embedding_dimensions "
                "FROM document_embeddings LIMIT 5"
            )
            models = [
                {"model": r[0], "dimensions": r[1]}
                for r in await cursor.fetchall()
            ]
            result["model_info"] = models

    return result


@router.get("/sources")
async def sources_health() -> dict[str, Any]:
    """Return health and metrics for all external data sources."""
    if _sources_daemon is None:
        return {"enabled": False, "sources": {}}
    return _sources_daemon.health()

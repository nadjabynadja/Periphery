"""WebSocket endpoint for live snapshot updates.

Clients connect to /ws/snapshot and receive a notification whenever the
Crystallizer produces a new LivingOntologySnapshot.  A heartbeat ping is
sent every 30 seconds to keep the connection alive.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from periphery.crystallizer.models import LivingOntologySnapshot

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Connection registry ──────────────────────────────────────────────────

_connections: set[WebSocket] = set()


# ── Broadcast helper (called from main.py after each crystallization) ───

def _build_snapshot_message(snapshot: LivingOntologySnapshot) -> dict:
    """Build a lightweight JSON-serialisable summary of a snapshot."""
    return {
        "type": "snapshot_update",
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at.isoformat(),
        "corpus_stats": snapshot.corpus_stats.model_dump(mode="json"),
        "cluster_count": len(snapshot.clusters),
        "anomaly_count": len(snapshot.anomalies),
        "trajectory_count": len(snapshot.trajectories),
        "emerging_structure_count": len(snapshot.emerging_structures),
        "convergence_alert_count": len(snapshot.convergence_alerts),
        "relational_gradient_count": len(snapshot.relational_gradients),
        "processing_time_ms": snapshot.processing_time_ms,
        "cluster_summaries": [
            {
                "cluster_id": c.cluster_id,
                "label": c.label,
                "size": c.size,
                "status": c.status,
                "confidence": c.confidence,
                "primary_space": c.primary_space,
            }
            for c in snapshot.clusters
        ],
    }


async def broadcast_snapshot(snapshot: LivingOntologySnapshot) -> None:
    """Send a lightweight snapshot notification to every connected client."""
    if not _connections:
        return

    message = _build_snapshot_message(snapshot)

    stale: list[WebSocket] = []
    for ws in _connections:
        try:
            await ws.send_json(message)
        except Exception:
            stale.append(ws)

    for ws in stale:
        _connections.discard(ws)


# ── WebSocket endpoint ──────────────────────────────────────────────────

@router.websocket("/ws/snapshot")
async def snapshot_ws(websocket: WebSocket):
    """Accept a WebSocket connection and stream snapshot updates.

    - On connect: sends the current snapshot (if available).
    - On each crystallization: receives a push via ``broadcast_snapshot``.
    - Every 30 s of inactivity: sends a heartbeat ping to keep alive.
    - Handles client ``ping`` messages with a ``pong`` reply.
    """
    await websocket.accept()
    _connections.add(websocket)
    logger.info("ws_snapshot_client_connected (total=%d)", len(_connections))

    # Send current snapshot immediately if one exists
    if _current_snapshot is not None:
        try:
            msg = _build_snapshot_message(_current_snapshot)
            await websocket.send_json(msg)
        except Exception:
            pass

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0,
                )
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # No message from client in 30 s — send heartbeat
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_snapshot_error")
    finally:
        _connections.discard(websocket)
        logger.info("ws_snapshot_client_disconnected (total=%d)", len(_connections))


# ── State accessor ──────────────────────────────────────────────────────

_current_snapshot: LivingOntologySnapshot | None = None


def set_current_snapshot(snapshot: LivingOntologySnapshot | None) -> None:
    """Called from main.py to keep a reference to the latest snapshot."""
    global _current_snapshot
    _current_snapshot = snapshot

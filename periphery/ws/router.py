"""WebSocket endpoint for live snapshot and query updates.

Clients connect to /ws/snapshot and receive a notification whenever the
Crystallizer produces a new LivingOntologySnapshot or a document is ingested.
Clients connect to /ws/query/{query_id} for per-query progress updates.

A heartbeat ping is sent every 30 seconds to keep connections alive.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from periphery.crystallizer.models import LivingOntologySnapshot

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Connection Manager ───────────────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections and broadcasts updates."""

    def __init__(self) -> None:
        self._snapshot_subscribers: set[WebSocket] = set()
        self._query_subscribers: dict[str, set[WebSocket]] = {}  # query_id -> connections

    async def connect_snapshot(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._snapshot_subscribers.add(websocket)
        logger.info("ws_snapshot_connected (total=%d)", len(self._snapshot_subscribers))

    async def connect_query(self, websocket: WebSocket, query_id: str) -> None:
        await websocket.accept()
        if query_id not in self._query_subscribers:
            self._query_subscribers[query_id] = set()
        self._query_subscribers[query_id].add(websocket)
        logger.info("ws_query_connected query_id=%s", query_id)

    def disconnect_snapshot(self, websocket: WebSocket) -> None:
        self._snapshot_subscribers.discard(websocket)
        logger.info("ws_snapshot_disconnected (total=%d)", len(self._snapshot_subscribers))

    def disconnect_query(self, websocket: WebSocket, query_id: str) -> None:
        if query_id in self._query_subscribers:
            self._query_subscribers[query_id].discard(websocket)
            if not self._query_subscribers[query_id]:
                del self._query_subscribers[query_id]

    async def broadcast_snapshot_update(self, snapshot_data: dict[str, Any]) -> None:
        """Broadcast a snapshot update to all snapshot subscribers."""
        if not self._snapshot_subscribers:
            return

        message = json.dumps({
            "type": "snapshot_update",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": snapshot_data,
        })
        dead: set[WebSocket] = set()
        for ws in self._snapshot_subscribers:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        self._snapshot_subscribers -= dead

    async def broadcast_query_update(self, query_id: str, update_data: dict[str, Any]) -> None:
        """Broadcast an update to subscribers of a specific query."""
        subscribers = self._query_subscribers.get(query_id, set())
        if not subscribers:
            return

        message = json.dumps({
            "type": "query_update",
            "query_id": query_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": update_data,
        })
        dead: set[WebSocket] = set()
        for ws in subscribers:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        subscribers -= dead

    @property
    def snapshot_subscriber_count(self) -> int:
        return len(self._snapshot_subscribers)


# Singleton instance
ws_manager = ConnectionManager()


# ── Snapshot broadcast helpers (called from main.py) ─────────────────────

def _build_snapshot_message(snapshot: LivingOntologySnapshot) -> dict[str, Any]:
    """Build a lightweight JSON-serialisable summary of a snapshot."""
    return {
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
        "new_clusters": [
            {
                "cluster_id": c.cluster_id,
                "label": c.label,
                "size": c.size,
                "status": c.status,
                "confidence": c.confidence,
            }
            for c in snapshot.clusters
            if c.status == "forming"
        ],
        "updated_clusters": [
            c.cluster_id
            for c in snapshot.clusters
            if c.status in ("growing", "shrinking")
        ],
        "new_anomalies": [
            {
                "anomaly_id": a.anomaly_id,
                "anomaly_type": a.anomaly_type,
                "anomaly_score": a.anomaly_score,
                "description": a.description,
            }
            for a in snapshot.anomalies[:10]
        ],
        "convergence_alerts": [
            a.model_dump(mode="json") if hasattr(a, "model_dump") else str(a)
            for a in snapshot.convergence_alerts
        ],
    }


async def broadcast_snapshot(snapshot: LivingOntologySnapshot) -> None:
    """Send a lightweight snapshot notification to every connected client."""
    message = _build_snapshot_message(snapshot)
    await ws_manager.broadcast_snapshot_update(message)


async def broadcast_document_ingested(doc: dict[str, Any]) -> None:
    """Broadcast a new_document event when a document is ingested."""
    await ws_manager.broadcast_snapshot_update({
        "type": "new_document",
        "doc_id": doc.get("id", ""),
        "title": doc.get("title", ""),
        "source_name": doc.get("source", {}).get("source_name", "") if isinstance(doc.get("source"), dict) else "",
        "source_category": doc.get("source", {}).get("source_category", "") if isinstance(doc.get("source"), dict) else "",
        "content_quality": doc.get("content_quality", "unknown"),
        "ingested": doc.get("ingested", datetime.now(timezone.utc).isoformat()),
    })


# ── WebSocket endpoints ──────────────────────────────────────────────────

@router.websocket("/ws/snapshot")
async def snapshot_ws(websocket: WebSocket) -> None:
    """Accept a WebSocket connection and stream snapshot updates.

    - On connect: sends the current snapshot (if available).
    - On each crystallization: receives a push via ``broadcast_snapshot``.
    - Every 30 s of inactivity: sends a heartbeat ping to keep alive.
    - Handles client ``ping`` messages with a ``pong`` reply.
    """
    await ws_manager.connect_snapshot(websocket)

    # Send current snapshot immediately if one exists
    if _current_snapshot is not None:
        try:
            msg = _build_snapshot_message(_current_snapshot)
            await websocket.send_json({"type": "snapshot_update", "data": msg})
        except Exception:
            pass

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0,
                )
                # Handle client messages (ping, filter, etc.)
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
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
        ws_manager.disconnect_snapshot(websocket)


@router.websocket("/ws/query/{query_id}")
async def query_ws(websocket: WebSocket, query_id: str) -> None:
    """Accept a WebSocket connection for per-query progress updates."""
    await ws_manager.connect_query(websocket, query_id)

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0,
                )
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    if data == "ping":
                        await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_query_error query_id=%s", query_id)
    finally:
        ws_manager.disconnect_query(websocket, query_id)


# ── State accessor ────────────────────────────────────────────────────────

_current_snapshot: LivingOntologySnapshot | None = None


def set_current_snapshot(snapshot: LivingOntologySnapshot | None) -> None:
    """Called from main.py to keep a reference to the latest snapshot."""
    global _current_snapshot
    _current_snapshot = snapshot

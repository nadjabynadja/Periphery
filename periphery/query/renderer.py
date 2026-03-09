"""Component 5 — Confidence Renderer.

Maps confidence scores to the frontend's legibility gradient. Every result
element gets rendering metadata that encodes confidence as visual properties:
opacity, blur, animation, border style, and color.
"""

from __future__ import annotations

from typing import Any

from periphery.query.models import RenderingMetadata, RetrievalResults

# ── Legibility Gradient Definition ───────────────────────────────────────

LEGIBILITY_GRADIENT: dict[str, dict[str, Any]] = {
    "solid": {
        "min_confidence": 0.8,
        "opacity": 1.0,
        "blur": 0,
        "animation": "none",
        "border": "solid",
        "label_visibility": "full",
        "description": "High confidence — well-established structure",
        "confidence_color": "#00D4FF",
        "glow_intensity": 0.8,
    },
    "defined": {
        "min_confidence": 0.6,
        "opacity": 0.85,
        "blur": 0,
        "animation": "none",
        "border": "solid",
        "label_visibility": "full",
        "description": "Moderate confidence — supported by multiple sources",
        "confidence_color": "#00B8D4",
        "glow_intensity": 0.6,
    },
    "emerging": {
        "min_confidence": 0.4,
        "opacity": 0.6,
        "blur": 1,
        "animation": "slow_pulse",
        "border": "dashed",
        "label_visibility": "on_hover",
        "description": "Emerging — pattern detected but not fully resolved",
        "confidence_color": "#FFB833",
        "glow_intensity": 0.4,
    },
    "haze": {
        "min_confidence": 0.2,
        "opacity": 0.35,
        "blur": 3,
        "animation": "pulse",
        "border": "none",
        "label_visibility": "on_hover",
        "description": "Low confidence — tentative signal, may be noise",
        "confidence_color": "#996B1F",
        "glow_intensity": 0.2,
    },
    "whisper": {
        "min_confidence": 0.0,
        "opacity": 0.15,
        "blur": 5,
        "animation": "slow_drift",
        "border": "none",
        "label_visibility": "on_click_only",
        "description": "Minimal confidence — barely detectable signal",
        "confidence_color": "#3A4A5C",
        "glow_intensity": 0.1,
    },
}


def confidence_to_tier(confidence: float) -> str:
    """Map a confidence score to a legibility tier name."""
    if confidence >= 0.8:
        return "solid"
    elif confidence >= 0.6:
        return "defined"
    elif confidence >= 0.4:
        return "emerging"
    elif confidence >= 0.2:
        return "haze"
    else:
        return "whisper"


def confidence_to_rendering(confidence: float) -> RenderingMetadata:
    """Map a confidence score to full rendering metadata."""
    tier = confidence_to_tier(confidence)
    spec = LEGIBILITY_GRADIENT[tier]
    return RenderingMetadata(
        legibility_tier=tier,
        opacity=spec["opacity"],
        blur=spec["blur"],
        animation=spec["animation"],
        border=spec["border"],
        label_visibility=spec["label_visibility"],
        confidence_color=spec["confidence_color"],
        glow_intensity=spec["glow_intensity"],
    )


class ConfidenceRenderer:
    """Attaches rendering metadata to all result elements."""

    def render(self, results: RetrievalResults) -> dict[str, Any]:
        """Attach rendering metadata to all result elements.

        Returns a dict with the same structure as RetrievalResults but
        each element wrapped with its rendering metadata.
        """
        output: dict[str, Any] = {"query_id": results.query_id}

        # Entities
        output["entities"] = [
            {
                "entity": e.model_dump(),
                "rendering": confidence_to_rendering(e.confidence).model_dump(),
            }
            for e in results.entities
        ]

        # Clusters
        output["clusters"] = [
            {
                "cluster": c.model_dump(),
                "rendering": confidence_to_rendering(c.confidence).model_dump(),
            }
            for c in results.clusters
        ]

        # Relationships
        output["relationships"] = [
            {
                "relationship": r.model_dump(),
                "rendering": confidence_to_rendering(r.confidence).model_dump(),
            }
            for r in results.relationships
        ]

        # Trajectories
        output["trajectories"] = [
            {
                "trajectory": t.model_dump(),
                "rendering": confidence_to_rendering(t.confidence).model_dump(),
            }
            for t in results.trajectories
        ]

        # Anomalies — use score as confidence proxy
        output["anomalies"] = [
            {
                "anomaly": a.model_dump(),
                "rendering": confidence_to_rendering(
                    min(1.0, a.score)
                ).model_dump(),
            }
            for a in results.anomalies
        ]

        # Relational paths
        output["relational_paths"] = [
            {
                "path": p.model_dump(),
                "rendering": confidence_to_rendering(p.path_confidence).model_dump(),
            }
            for p in results.relational_paths
        ]

        # Emerging structures
        output["emerging_structures"] = [
            {
                "structure": e.model_dump(),
                "rendering": confidence_to_rendering(
                    e.formation_confidence
                ).model_dump(),
            }
            for e in results.emerging_structures
        ]

        return output

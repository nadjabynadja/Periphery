"""Confidence explanations for scored structures.

Every scored structure gets a human-readable explanation of why the
score is what it is. Analysts don't trust black-box numbers — they
need to know what's driving the score.
"""

from __future__ import annotations

from typing import Any


def generate_explanation(
    scored_structure: dict[str, Any],
    snapshot_history: list[float] | None = None,
) -> dict[str, Any]:
    """Generate a human-readable explanation for a scored structure.

    Args:
        scored_structure: dict with 'confidence', 'confidence_calibrated',
            'signal_scores', 'type', 'features', 'context'
        snapshot_history: list of confidence scores from previous snapshots

    Returns:
        Explanation dict with primary_factors, risk_factors, trend info.
    """
    signals = scored_structure.get("signal_scores", {})
    ctx = scored_structure.get("context", {})
    structure_type = scored_structure.get("type", "unknown")

    primary_factors = _extract_primary_factors(signals, ctx, structure_type)
    risk_factors = _extract_risk_factors(signals, ctx, structure_type)
    trend, trend_detail = _compute_trend(
        scored_structure.get("confidence", 0.0), snapshot_history
    )

    return {
        "confidence": scored_structure.get("confidence", 0.0),
        "confidence_calibrated": scored_structure.get("confidence_calibrated", 0.0),
        "explanation": {
            "primary_factors": primary_factors,
            "risk_factors": risk_factors,
            "trend": trend,
            "trend_detail": trend_detail,
        },
    }


def _extract_primary_factors(
    signals: dict[str, float],
    ctx: dict[str, Any],
    structure_type: str,
) -> list[dict[str, Any]]:
    """Extract the top contributing factors to the score."""
    factors = []

    # Source diversity
    source_score = signals.get("source_diversity", 0.0)
    num_sources = ctx.get("num_sources", 0)
    if source_score > 0.3:
        tiers = ctx.get("source_tiers", "multiple")
        factors.append({
            "factor": "source_diversity",
            "score": round(source_score, 2),
            "detail": f"Supported by {num_sources} independent sources across {tiers} credibility tiers",
        })

    # Cross-space agreement
    cross_score = signals.get("cross_space_agreement", 0.0)
    if cross_score > 0.3:
        spaces = ctx.get("agreed_spaces", [])
        space_str = ", ".join(spaces) if spaces else "multiple spaces"
        factors.append({
            "factor": "cross_space_coherence",
            "score": round(cross_score, 2),
            "detail": f"Structure appears in {space_str}",
        })

    # Temporal consistency
    temporal_score = signals.get("temporal_consistency", 0.0)
    if temporal_score > 0.3:
        factors.append({
            "factor": "temporal_consistency",
            "score": round(temporal_score, 2),
            "detail": _temporal_detail(temporal_score, ctx),
        })

    # Stability
    stability_score = signals.get("stability", 0.0)
    if stability_score > 0.3:
        age = ctx.get("age_snapshots", 0)
        factors.append({
            "factor": "stability",
            "score": round(stability_score, 2),
            "detail": f"Structure has persisted across {age} snapshots",
        })

    # Neural critic
    neural_score = signals.get("critic_neural", 0.0)
    factors.append({
        "factor": "neural_critic",
        "score": round(neural_score, 2),
        "detail": _neural_detail(neural_score, structure_type),
    })

    # Sort by score descending, keep top 3
    factors.sort(key=lambda f: f["score"], reverse=True)
    return factors[:3]


def _extract_risk_factors(
    signals: dict[str, float],
    ctx: dict[str, Any],
    structure_type: str,
) -> list[dict[str, Any]]:
    """Extract risk factors that are dragging the score down."""
    risks = []

    if signals.get("temporal_consistency", 1.0) < 0.5:
        conflicts = ctx.get("temporal_conflicts", 0)
        risks.append({
            "factor": "low_temporal_consistency",
            "detail": f"Temporal conflict between sources ({conflicts} conflicts detected)",
        })

    if signals.get("source_diversity", 1.0) < 0.3:
        num_sources = ctx.get("num_sources", 0)
        tier1 = ctx.get("tier1_sources", 0)
        risks.append({
            "factor": "low_source_diversity",
            "detail": f"Only {num_sources} source(s), {tier1} tier-1",
        })

    if signals.get("cross_space_agreement", 1.0) < 0.3:
        risks.append({
            "factor": "single_space",
            "detail": "Structure only appears in one embedding space",
        })

    if signals.get("stability", 1.0) < 0.2:
        risks.append({
            "factor": "new_structure",
            "detail": "Structure is newly detected and has not been validated across runs",
        })

    if signals.get("critic_neural", 1.0) < 0.4:
        risks.append({
            "factor": "low_neural_score",
            "detail": "Neural Critic rates structural coherence as low",
        })

    return risks


def _compute_trend(
    current: float,
    history: list[float] | None,
) -> tuple[str, str]:
    """Compute confidence trend from snapshot history."""
    if not history or len(history) < 2:
        return "new", "Newly scored structure — no trend data available"

    recent = history[-3:] if len(history) >= 3 else history
    avg_recent = sum(recent) / len(recent)

    if current > avg_recent + 0.05:
        direction = "improving"
    elif current < avg_recent - 0.05:
        direction = "declining"
    else:
        direction = "stable"

    oldest = recent[0]
    span = len(recent)
    detail = (
        f"Confidence has moved from {oldest:.2f} to {current:.2f} "
        f"over the last {span} snapshots"
    )
    return direction, detail


def _temporal_detail(score: float, ctx: dict[str, Any]) -> str:
    if score > 0.8:
        return "Strong temporal agreement across all sources"
    elif score > 0.5:
        current = ctx.get("temporal_current", 0)
        historical = ctx.get("temporal_historical", 0)
        return (
            f"Mixed temporal signals — {current} sources say current, "
            f"{historical} say historical"
        )
    else:
        return "Conflicting temporal signals across sources"


def _neural_detail(score: float, structure_type: str) -> str:
    if score > 0.8:
        return f"Neural Critic rates this {structure_type} as highly coherent"
    elif score > 0.5:
        return f"Neural Critic rates this {structure_type} as plausible"
    elif score > 0.3:
        return f"Neural Critic rates this {structure_type} as uncertain"
    else:
        return f"Neural Critic rates this {structure_type} as likely noise"

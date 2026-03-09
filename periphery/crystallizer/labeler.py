"""Auto-labeling — generate human-readable labels for clusters.

Uses entity centrality and relationship predicates to create template-based
labels, with optional LLM-based descriptive labels via Claude API.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def generate_label(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    size: int,
) -> str:
    """Generate a human-readable label from a cluster's key entities and relationships.

    Template: "[Primary Entity] - [Primary Predicate] - [Secondary Entity] (N documents)"
    """
    if not entities and not relationships:
        return f"Unnamed cluster ({size} documents)"

    # Top entities by frequency
    entity_counter: Counter[str] = Counter()
    for ent in entities:
        name = ent.get("text") or ent.get("canonical_name") or ""
        if name:
            entity_counter[name] += 1

    top_entities = [name for name, _ in entity_counter.most_common(5)]

    # Top predicates
    pred_counter: Counter[str] = Counter()
    for rel in relationships:
        pred = rel.get("predicate", "")
        if pred:
            pred_counter[pred] += 1

    top_predicates = [pred for pred, _ in pred_counter.most_common(3)]

    if len(top_entities) >= 2 and top_predicates:
        label = f"{top_entities[0]} - {top_predicates[0]} - {top_entities[1]}"
    elif top_entities:
        label = ", ".join(top_entities[:3])
    else:
        label = ", ".join(top_predicates[:3]) if top_predicates else "Unnamed cluster"

    return f"{label} ({size} documents)"


async def generate_label_llm(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
) -> str | None:
    """Generate a descriptive label using Claude API.

    Returns None if the call fails or is skipped.
    """
    if not api_key:
        return None

    entity_names = [
        ent.get("text") or ent.get("canonical_name", "")
        for ent in entities[:10]
    ]
    rel_descriptions = [
        f"{r.get('subject_text', '')} {r.get('predicate', '')} {r.get('object_text', '')}"
        for r in relationships[:5]
    ]

    prompt = (
        "Given these entities and relationships from an intelligence cluster, "
        "generate a 5-10 word descriptive label.\n\n"
        f"Key entities: {', '.join(entity_names)}\n"
        f"Key relationships: {'; '.join(rel_descriptions)}\n\n"
        "Respond with ONLY the label, nothing else."
    )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
        label = response.content[0].text.strip()
        return label if label else None
    except Exception:
        logger.debug("llm_label_failed")
        return None


def extract_key_entities(
    doc_ids: list[str],
    doc_entities: dict[str, list[dict[str, Any]]],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Extract the most central entities in a cluster by document frequency."""
    entity_freq: Counter[str] = Counter()
    entity_records: dict[str, dict[str, Any]] = {}

    for did in doc_ids:
        for ent in doc_entities.get(did, []):
            name = ent.get("canonical_id") or ent.get("text", "")
            if name:
                entity_freq[name] += 1
                if name not in entity_records:
                    entity_records[name] = ent

    return [entity_records[name] for name, _ in entity_freq.most_common(top_n) if name in entity_records]


def extract_key_relationships(
    doc_ids: list[str],
    doc_relationships: dict[str, list[dict[str, Any]]],
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Extract the most representative relationships in a cluster."""
    rel_freq: Counter[str] = Counter()
    rel_records: dict[str, dict[str, Any]] = {}

    for did in doc_ids:
        for rel in doc_relationships.get(did, []):
            key = f"{rel.get('subject_id', '')}_{rel.get('predicate', '')}_{rel.get('object_id', '')}"
            if key and key != "__":
                rel_freq[key] += 1
                if key not in rel_records:
                    rel_records[key] = rel

    return [rel_records[key] for key, _ in rel_freq.most_common(top_n) if key in rel_records]

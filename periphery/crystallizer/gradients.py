"""Relational gradient analysis — detect emergent relationships between clusters.

Computes composite relationship scores between cluster pairs based on:
  - Entity co-membership across clusters
  - Temporal alignment
  - Geographic proximity
  - Relational bridge entities
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

from periphery.crystallizer.models import RelationalGradient, GradientComponents

logger = structlog.get_logger(__name__)


class GradientAnalyzer:
    """Computes relational gradients between cluster pairs.

    The gradients represent emergent relationships that weren't explicitly
    extracted by the enrichment pipeline — they arise from patterns across
    many documents that no single document could reveal.
    """

    def __init__(self) -> None:
        self._previous_gradients: dict[tuple[str, str], float] = {}

    def compute_gradients(
        self,
        clusters: dict[str, set[str]],
        doc_entities: dict[str, list[dict[str, Any]]],
        doc_relationships: dict[str, list[dict[str, Any]]],
        cluster_temporal_centers: dict[str, float | None],
        cluster_geo_centroids: dict[str, tuple[float, float] | None],
    ) -> list[RelationalGradient]:
        """Compute relational gradients for all cluster pairs.

        Args:
            clusters: {cluster_id: set(doc_ids)}
            doc_entities: {doc_id: [entity_dicts]}
            doc_relationships: {doc_id: [relationship_dicts]}
            cluster_temporal_centers: {cluster_id: timestamp_float or None}
            cluster_geo_centroids: {cluster_id: (lat, lon) or None}

        Returns:
            List of RelationalGradient objects for significant gradients.
        """
        cluster_ids = sorted(clusters.keys())
        gradients: list[RelationalGradient] = []

        # Precompute entity sets per cluster
        cluster_entities: dict[str, set[str]] = {}
        for cid, doc_ids in clusters.items():
            entities: set[str] = set()
            for did in doc_ids:
                for ent in doc_entities.get(did, []):
                    eid = ent.get("canonical_id") or ent.get("text", "")
                    if eid:
                        entities.add(eid)
            cluster_entities[cid] = entities

        # Precompute subject/object entity sets per cluster for bridge detection
        cluster_subjects: dict[str, set[str]] = {}
        cluster_objects: dict[str, set[str]] = {}
        for cid, doc_ids in clusters.items():
            subjects: set[str] = set()
            objects: set[str] = set()
            for did in doc_ids:
                for rel in doc_relationships.get(did, []):
                    subj = rel.get("subject_id") or rel.get("subject_text", "")
                    obj = rel.get("object_id") or rel.get("object_text", "")
                    if subj:
                        subjects.add(subj)
                    if obj:
                        objects.add(obj)
            cluster_subjects[cid] = subjects
            cluster_objects[cid] = objects

        for i, cid_a in enumerate(cluster_ids):
            for cid_b in cluster_ids[i + 1:]:
                components = self._compute_components(
                    cid_a, cid_b,
                    clusters, cluster_entities,
                    cluster_subjects, cluster_objects,
                    cluster_temporal_centers, cluster_geo_centroids,
                )

                score = self._composite_score(components)
                if score < 0.05:
                    continue  # Skip negligible gradients

                # Determine trend
                pair_key = (cid_a, cid_b)
                prev_score = self._previous_gradients.get(pair_key)
                if prev_score is not None:
                    if score > prev_score * 1.1:
                        trend = "strengthening"
                    elif score < prev_score * 0.9:
                        trend = "weakening"
                    else:
                        trend = "stable"
                else:
                    trend = "stable"
                self._previous_gradients[pair_key] = score

                gradients.append(RelationalGradient(
                    source_cluster=cid_a,
                    target_cluster=cid_b,
                    gradient_score=score,
                    components=components,
                    gradient_trend=trend,
                ))

        logger.info(
            "gradients_computed",
            total_pairs=len(cluster_ids) * (len(cluster_ids) - 1) // 2,
            significant_gradients=len(gradients),
        )

        return gradients

    def _compute_components(
        self,
        cid_a: str,
        cid_b: str,
        clusters: dict[str, set[str]],
        cluster_entities: dict[str, set[str]],
        cluster_subjects: dict[str, set[str]],
        cluster_objects: dict[str, set[str]],
        temporal_centers: dict[str, float | None],
        geo_centroids: dict[str, tuple[float, float] | None],
    ) -> GradientComponents:
        """Compute individual gradient components for a cluster pair."""
        entities_a = cluster_entities.get(cid_a, set())
        entities_b = cluster_entities.get(cid_b, set())

        # Entity co-membership
        shared_entities = entities_a & entities_b
        union_entities = entities_a | entities_b
        entity_co_membership = (
            len(shared_entities) / len(union_entities)
            if union_entities else 0.0
        )

        # Temporal alignment
        tc_a = temporal_centers.get(cid_a)
        tc_b = temporal_centers.get(cid_b)
        if tc_a is not None and tc_b is not None:
            # Normalize time difference — closer = higher alignment
            time_diff_days = abs(tc_a - tc_b) / 86400.0
            temporal_alignment = max(0.0, 1.0 - time_diff_days / 365.0)
        else:
            temporal_alignment = 0.0

        # Geographic proximity
        geo_a = geo_centroids.get(cid_a)
        geo_b = geo_centroids.get(cid_b)
        if geo_a is not None and geo_b is not None:
            # Simple Euclidean distance on normalized coords
            dist = np.sqrt(
                (geo_a[0] - geo_b[0]) ** 2 + (geo_a[1] - geo_b[1]) ** 2
            )
            # Normalize: 0 distance = 1.0 proximity, max ~1.4 (antipodal) = 0
            geographic_proximity = max(0.0, 1.0 - dist / 1.4)
        else:
            geographic_proximity = 0.0

        # Relational bridge entities
        # Entities that are subjects in one cluster's docs and objects in the other's
        subj_a = cluster_subjects.get(cid_a, set())
        obj_a = cluster_objects.get(cid_a, set())
        subj_b = cluster_subjects.get(cid_b, set())
        obj_b = cluster_objects.get(cid_b, set())

        bridges_a_to_b = subj_a & obj_b
        bridges_b_to_a = subj_b & obj_a
        all_bridges = bridges_a_to_b | bridges_b_to_a

        return GradientComponents(
            entity_co_membership=entity_co_membership,
            temporal_alignment=temporal_alignment,
            geographic_proximity=geographic_proximity,
            relational_bridges=len(all_bridges),
            bridge_entities=sorted(all_bridges)[:20],  # cap for storage
        )

    def _composite_score(self, components: GradientComponents) -> float:
        """Compute weighted composite gradient score."""
        return (
            0.35 * components.entity_co_membership
            + 0.20 * components.temporal_alignment
            + 0.15 * components.geographic_proximity
            + 0.30 * min(1.0, components.relational_bridges / 5.0)
        )

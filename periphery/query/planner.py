"""Component 2 — Query Planner.

Decomposes parsed intents into concrete operations against the embedding
spaces and ontology snapshot. Produces an execution plan with dependency
ordering for parallel execution.
"""

from __future__ import annotations

import logging
import time
import uuid

from periphery.query.models import ParsedIntent, PlanOperation, QueryPlan

logger = logging.getLogger(__name__)


class QueryPlanner:
    """Transforms structured intents into executable query plans."""

    def plan(self, intent: ParsedIntent, query_id: str) -> tuple[QueryPlan, int]:
        """Build an execution plan from a parsed intent.

        Returns (plan, elapsed_ms).
        """
        start = time.monotonic()

        plan_id = str(uuid.uuid4())[:12]
        operations: list[PlanOperation] = []
        op_counter = 0

        def _op_id() -> str:
            nonlocal op_counter
            op_counter += 1
            return f"op_{op_counter:03d}"

        # 1. If specific clusters are identified, retrieve them first (fast)
        cluster_op_ids: list[str] = []
        if intent.clusters_likely_relevant:
            oid = _op_id()
            cluster_op_ids.append(oid)
            operations.append(PlanOperation(
                operation_id=oid,
                type="cluster_retrieval",
                parameters={"cluster_ids": intent.clusters_likely_relevant},
                priority=0,
            ))

        # 2. Entity search for referenced entities
        entity_op_ids: list[str] = []
        if intent.entities_referenced:
            oid = _op_id()
            entity_op_ids.append(oid)
            operations.append(PlanOperation(
                operation_id=oid,
                type="entity_search",
                parameters={
                    "entity_names": intent.entities_referenced,
                    "entity_types": intent.entity_types_requested,
                },
                priority=0,
            ))

        # 3. Semantic search (supplementary discovery)
        semantic_op_id = _op_id()
        operations.append(PlanOperation(
            operation_id=semantic_op_id,
            type="semantic_search",
            parameters={
                "top_k": 50,
                "confidence_threshold": intent.confidence_threshold,
            },
            priority=1,
        ))

        # 4. Relational path search if asking about connections between entities
        if (
            intent.query_type == "relationship_query"
            and len(intent.entities_referenced) >= 2
        ):
            oid = _op_id()
            operations.append(PlanOperation(
                operation_id=oid,
                type="relational_path",
                parameters={
                    "entity_a": intent.entities_referenced[0],
                    "entity_b": intent.entities_referenced[1],
                    "max_hops": 4,
                    "relationship_types": intent.relationships_requested,
                },
                depends_on=entity_op_ids,
                priority=2,
            ))

        # 5. Geographic filter
        if (
            intent.geographic_scope.scope_type != "global"
            or intent.geographic_scope.regions
            or intent.geographic_scope.coordinates
        ):
            oid = _op_id()
            operations.append(PlanOperation(
                operation_id=oid,
                type="geographic_filter",
                parameters={
                    "regions": intent.geographic_scope.regions,
                    "coordinates": intent.geographic_scope.coordinates,
                    "scope_type": intent.geographic_scope.scope_type,
                },
                priority=1,
            ))

        # 6. Temporal filter
        if intent.temporal_scope.start or intent.temporal_scope.end:
            oid = _op_id()
            operations.append(PlanOperation(
                operation_id=oid,
                type="temporal_filter",
                parameters={
                    "start": intent.temporal_scope.start,
                    "end": intent.temporal_scope.end,
                    "temporal_focus": intent.temporal_scope.temporal_focus,
                },
                priority=1,
            ))

        # 7. Trajectory retrieval for trend/evolution queries
        if intent.query_type in ("trajectory_query", "situational_awareness") or (
            intent.temporal_scope.temporal_focus in ("trend", "predictive")
        ):
            oid = _op_id()
            operations.append(PlanOperation(
                operation_id=oid,
                type="trajectory_retrieval",
                parameters={
                    "cluster_ids": intent.clusters_likely_relevant,
                    "entity_names": intent.entities_referenced,
                    "include_extrapolation": intent.temporal_scope.temporal_focus == "predictive",
                },
                priority=2,
            ))

        # 8. Anomaly retrieval for anomaly queries or broad awareness
        if intent.query_type in ("anomaly_query", "situational_awareness"):
            oid = _op_id()
            operations.append(PlanOperation(
                operation_id=oid,
                type="anomaly_retrieval",
                parameters={
                    "regions": intent.geographic_scope.regions,
                    "entity_names": intent.entities_referenced,
                },
                priority=2,
            ))

        # 9. Handle implied subqueries as additional semantic searches
        for i, subquery in enumerate(intent.implied_subqueries[:3]):
            oid = _op_id()
            operations.append(PlanOperation(
                operation_id=oid,
                type="semantic_search",
                parameters={
                    "subquery_text": subquery,
                    "top_k": 20,
                    "confidence_threshold": intent.confidence_threshold,
                },
                priority=3,
            ))

        # Determine merge strategy based on query type
        if intent.query_type in ("entity_lookup", "relationship_query"):
            merge_strategy = "ranked_fusion"
        elif intent.query_type == "situational_awareness":
            merge_strategy = "union"
        else:
            merge_strategy = "ranked_fusion"

        plan = QueryPlan(
            plan_id=plan_id,
            query_id=query_id,
            operations=operations,
            merge_strategy=merge_strategy,
        )

        elapsed = int((time.monotonic() - start) * 1000)
        return plan, elapsed

"""Tests for the Query Interface — Layer 4.

Tests all components of the analytical query pipeline: models, intent parser,
query planner, retriever, synthesizer, renderer, preprocessor, and persistence.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# ── Models Tests ─────────────────────────────────────────────────────────


class TestModels:
    def test_parsed_intent_defaults(self):
        from periphery.query.models import ParsedIntent

        intent = ParsedIntent()
        assert intent.query_type == "freeform"
        assert intent.entities_referenced == []
        assert intent.confidence_threshold == 0.0
        assert intent.geographic_scope.scope_type == "global"
        assert intent.temporal_scope.temporal_focus == "current"

    def test_query_plan_structure(self):
        from periphery.query.models import PlanOperation, QueryPlan

        plan = QueryPlan(
            plan_id="test-plan",
            query_id="test-query",
            operations=[
                PlanOperation(
                    operation_id="op_001",
                    type="semantic_search",
                    parameters={"top_k": 50},
                    priority=0,
                ),
                PlanOperation(
                    operation_id="op_002",
                    type="entity_search",
                    parameters={"entity_names": ["Iran"]},
                    depends_on=["op_001"],
                    priority=1,
                ),
            ],
            merge_strategy="ranked_fusion",
        )
        assert len(plan.operations) == 2
        assert plan.operations[1].depends_on == ["op_001"]

    def test_rendering_metadata_defaults(self):
        from periphery.query.models import RenderingMetadata

        meta = RenderingMetadata()
        assert meta.legibility_tier == "whisper"
        assert meta.opacity == 0.15
        assert meta.blur == 5

    def test_analytical_query_request(self):
        from periphery.query.models import AnalyticalQueryRequest

        req = AnalyticalQueryRequest(query="What's happening in the Red Sea?")
        assert req.confidence_floor == 0.0
        assert req.max_results == 50
        assert req.include_rendering is True

    def test_session_state(self):
        from periphery.query.models import SessionState

        session = SessionState(session_id="test-session")
        assert session.session_id == "test-session"
        assert session.previous_queries == []
        assert session.confidence_preference == 0.0

    def test_retrieval_results_empty(self):
        from periphery.query.models import RetrievalResults

        results = RetrievalResults(query_id="q1")
        assert results.entities == []
        assert results.clusters == []
        assert results.anomalies == []

    def test_execution_stats(self):
        from periphery.query.models import ExecutionStats

        stats = ExecutionStats(
            total_time_ms=1500,
            intent_parsing_ms=800,
            retrieval_ms=500,
            synthesis_ms=200,
        )
        assert stats.total_time_ms == 1500
        assert stats.cached is False


# ── Intent Parser Tests ──────────────────────────────────────────────────


class TestIntentParser:
    def test_hardcoded_fallback_entity_lookup(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        intent = parser._hardcoded_fallback("Who is the CEO of Saudi Aramco?")
        assert intent.query_type == "entity_lookup"
        assert intent.analytical_focus == "actors"

    def test_hardcoded_fallback_relationship(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        intent = parser._hardcoded_fallback("How are Company X and Person Y connected?")
        assert intent.query_type == "relationship_query"
        assert intent.analytical_focus == "connections"

    def test_hardcoded_fallback_anomaly(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        intent = parser._hardcoded_fallback("What unusual activity has been detected?")
        assert intent.query_type == "anomaly_query"
        assert intent.analytical_focus == "anomalies"

    def test_hardcoded_fallback_situational_awareness(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        intent = parser._hardcoded_fallback("What's happening in the Middle East?")
        assert intent.query_type == "situational_awareness"
        assert "Middle East" in intent.geographic_scope.regions

    def test_hardcoded_fallback_trajectory(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        intent = parser._hardcoded_fallback("How is the trend changing in maritime activity?")
        assert intent.query_type == "trajectory_query"

    def test_hardcoded_fallback_geographic(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        intent = parser._hardcoded_fallback("Show me activity near the Red Sea and Iran")
        assert "Red Sea" in intent.geographic_scope.regions
        assert "Iran" in intent.geographic_scope.regions
        assert intent.geographic_scope.scope_type == "region"

    def test_intent_cache_store_and_lookup(self):
        from periphery.query.intent_parser import IntentCache
        from periphery.query.models import ParsedIntent

        cache = IntentCache(max_size=10)
        emb = np.random.randn(384).astype(np.float32)
        emb /= np.linalg.norm(emb)
        intent = ParsedIntent(query_type="entity_lookup")

        cache.store(emb, intent)

        # Exact same embedding should hit
        result = cache.lookup(emb)
        assert result is not None
        assert result.query_type == "entity_lookup"

    def test_intent_cache_miss(self):
        from periphery.query.intent_parser import IntentCache
        from periphery.query.models import ParsedIntent

        cache = IntentCache(max_size=10)
        emb1 = np.random.randn(384).astype(np.float32)
        emb1 /= np.linalg.norm(emb1)
        intent = ParsedIntent(query_type="entity_lookup")
        cache.store(emb1, intent)

        # Very different embedding should miss
        emb2 = -emb1
        emb2 /= np.linalg.norm(emb2)
        result = cache.lookup(emb2)
        assert result is None

    def test_snapshot_summary_with_no_snapshot(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        summary = parser._build_snapshot_summary(None)
        assert "No ontology snapshot" in summary

    def test_snapshot_summary_with_snapshot(self):
        from periphery.crystallizer.models import (
            CorpusStats,
            DetectedCluster,
            LivingOntologySnapshot,
        )
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        snapshot = LivingOntologySnapshot(
            snapshot_id="test-snap",
            corpus_stats=CorpusStats(total_documents=100, total_entities=50, total_relationships=30),
            clusters=[
                DetectedCluster(
                    cluster_id="c1",
                    primary_space="semantic",
                    label="Iranian Maritime",
                    size=10,
                    confidence=0.8,
                    key_entities=["Iran", "IRGC"],
                ),
            ],
        )
        summary = parser._build_snapshot_summary(snapshot)
        assert "100 documents" in summary
        assert "Iranian Maritime" in summary

    @pytest.mark.asyncio
    async def test_parse_without_api_key(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        intent, elapsed = await parser.parse("What entities are linked to Iran?")
        assert intent.query_type in (
            "entity_lookup", "relationship_query", "freeform",
        )
        assert elapsed >= 0

    def test_parse_response_from_dict(self):
        from periphery.query.intent_parser import IntentParser

        parser = IntentParser()
        data = {
            "query_type": "geographic_query",
            "entities_referenced": ["Iran", "IRGC"],
            "entity_types_requested": ["ORG", "GPE"],
            "relationships_requested": ["funds", "operates"],
            "geographic_scope": {
                "regions": ["Middle East"],
                "coordinates": None,
                "scope_type": "region",
            },
            "temporal_scope": {
                "start": "2024-01-01",
                "end": None,
                "temporal_focus": "current",
            },
            "confidence_threshold": 0.3,
            "analytical_focus": "geography",
            "implied_subqueries": ["What shipping routes pass through?"],
            "clusters_likely_relevant": ["c1", "c2"],
        }
        intent = parser._parse_response(data)
        assert intent.query_type == "geographic_query"
        assert intent.entities_referenced == ["Iran", "IRGC"]
        assert intent.geographic_scope.regions == ["Middle East"]
        assert intent.confidence_threshold == 0.3
        assert len(intent.implied_subqueries) == 1


# ── Query Planner Tests ──────────────────────────────────────────────────


class TestQueryPlanner:
    def test_basic_plan(self):
        from periphery.query.models import ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(query_type="freeform")
        plan, elapsed = planner.plan(intent, "q1")

        assert plan.query_id == "q1"
        # Should always have at least a semantic search
        op_types = [op.type for op in plan.operations]
        assert "semantic_search" in op_types

    def test_entity_plan(self):
        from periphery.query.models import ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(
            query_type="entity_lookup",
            entities_referenced=["Saudi Aramco"],
        )
        plan, _ = planner.plan(intent, "q2")
        op_types = [op.type for op in plan.operations]
        assert "entity_search" in op_types
        assert "semantic_search" in op_types

    def test_relationship_plan(self):
        from periphery.query.models import ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(
            query_type="relationship_query",
            entities_referenced=["Company X", "Person Y"],
        )
        plan, _ = planner.plan(intent, "q3")
        op_types = [op.type for op in plan.operations]
        assert "relational_path" in op_types
        assert "entity_search" in op_types

    def test_geographic_plan(self):
        from periphery.query.models import GeographicScope, ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(
            query_type="geographic_query",
            geographic_scope=GeographicScope(
                regions=["Middle East"],
                scope_type="region",
            ),
        )
        plan, _ = planner.plan(intent, "q4")
        op_types = [op.type for op in plan.operations]
        assert "geographic_filter" in op_types

    def test_anomaly_plan(self):
        from periphery.query.models import ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(query_type="anomaly_query")
        plan, _ = planner.plan(intent, "q5")
        op_types = [op.type for op in plan.operations]
        assert "anomaly_retrieval" in op_types

    def test_situational_awareness_plan(self):
        from periphery.query.models import ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(query_type="situational_awareness")
        plan, _ = planner.plan(intent, "q6")
        op_types = [op.type for op in plan.operations]
        assert "trajectory_retrieval" in op_types
        assert "anomaly_retrieval" in op_types
        assert plan.merge_strategy == "union"

    def test_plan_with_implied_subqueries(self):
        from periphery.query.models import ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(
            query_type="freeform",
            implied_subqueries=["sub1", "sub2"],
        )
        plan, _ = planner.plan(intent, "q7")
        semantic_ops = [op for op in plan.operations if op.type == "semantic_search"]
        # Main + 2 subqueries
        assert len(semantic_ops) >= 3

    def test_plan_dependency_ordering(self):
        from periphery.query.models import ParsedIntent
        from periphery.query.planner import QueryPlanner

        planner = QueryPlanner()
        intent = ParsedIntent(
            query_type="relationship_query",
            entities_referenced=["A", "B"],
        )
        plan, _ = planner.plan(intent, "q8")
        relational_ops = [op for op in plan.operations if op.type == "relational_path"]
        assert len(relational_ops) == 1
        # Relational path should depend on entity search
        assert len(relational_ops[0].depends_on) > 0


# ── Renderer Tests ───────────────────────────────────────────────────────


class TestRenderer:
    def test_confidence_to_tier(self):
        from periphery.query.renderer import confidence_to_tier

        assert confidence_to_tier(0.9) == "solid"
        assert confidence_to_tier(0.7) == "defined"
        assert confidence_to_tier(0.5) == "emerging"
        assert confidence_to_tier(0.3) == "haze"
        assert confidence_to_tier(0.1) == "whisper"

    def test_confidence_to_rendering(self):
        from periphery.query.renderer import confidence_to_rendering

        meta = confidence_to_rendering(0.85)
        assert meta.legibility_tier == "solid"
        assert meta.opacity == 1.0
        assert meta.blur == 0
        assert meta.confidence_color == "#00D4FF"

    def test_confidence_to_rendering_low(self):
        from periphery.query.renderer import confidence_to_rendering

        meta = confidence_to_rendering(0.1)
        assert meta.legibility_tier == "whisper"
        assert meta.opacity == 0.15
        assert meta.blur == 5

    def test_renderer_render_empty(self):
        from periphery.query.models import RetrievalResults
        from periphery.query.renderer import ConfidenceRenderer

        renderer = ConfidenceRenderer()
        results = RetrievalResults(query_id="q1")
        output = renderer.render(results)
        assert output["query_id"] == "q1"
        assert output["entities"] == []
        assert output["clusters"] == []

    def test_renderer_render_with_entities(self):
        from periphery.query.models import EntityResult, RetrievalResults
        from periphery.query.renderer import ConfidenceRenderer

        renderer = ConfidenceRenderer()
        results = RetrievalResults(
            query_id="q1",
            entities=[
                EntityResult(
                    canonical_id="e1",
                    name="Iran",
                    type="GPE",
                    confidence=0.9,
                    relevance_score=0.95,
                ),
                EntityResult(
                    canonical_id="e2",
                    name="Unknown Corp",
                    type="ORG",
                    confidence=0.15,
                    relevance_score=0.3,
                ),
            ],
        )
        output = renderer.render(results)
        assert len(output["entities"]) == 2
        assert output["entities"][0]["rendering"]["legibility_tier"] == "solid"
        assert output["entities"][1]["rendering"]["legibility_tier"] == "whisper"

    def test_legibility_gradient_coverage(self):
        from periphery.query.renderer import LEGIBILITY_GRADIENT

        assert len(LEGIBILITY_GRADIENT) == 5
        assert "solid" in LEGIBILITY_GRADIENT
        assert "whisper" in LEGIBILITY_GRADIENT
        for tier, spec in LEGIBILITY_GRADIENT.items():
            assert "opacity" in spec
            assert "blur" in spec
            assert "confidence_color" in spec


# ── Preprocessor Tests ───────────────────────────────────────────────────


class TestPreprocessor:
    def test_abbreviation_expansion(self):
        from periphery.query.preprocessor import QueryPreprocessor

        pp = QueryPreprocessor()
        text, _ = pp.preprocess("What is the IRGC doing in the Red Sea?")
        assert "Islamic Revolutionary Guard Corps" in text
        assert "IRGC" in text  # Original abbreviation kept

    def test_multiple_abbreviations(self):
        from periphery.query.preprocessor import QueryPreprocessor

        pp = QueryPreprocessor()
        text, _ = pp.preprocess("OFAC sanctions on DPRK entities")
        assert "Office of Foreign Assets Control" in text
        assert "Democratic People's Republic of Korea" in text

    def test_no_abbreviation_in_regular_words(self):
        from periphery.query.preprocessor import QueryPreprocessor

        pp = QueryPreprocessor()
        text, _ = pp.preprocess("Tell me about shipping routes")
        assert text == "Tell me about shipping routes"

    def test_session_context_empty(self):
        from periphery.query.preprocessor import QueryPreprocessor

        pp = QueryPreprocessor()
        _, context = pp.preprocess("test query")
        assert context == ""

    def test_session_context_with_previous_queries(self):
        from periphery.query.models import SessionState
        from periphery.query.preprocessor import QueryPreprocessor

        pp = QueryPreprocessor()
        session = SessionState(
            session_id="s1",
            previous_queries=[
                {"query": "What about Iran?", "summary": "Found 5 entities"},
            ],
            bookmarked_entities=["IRGC", "Saudi Aramco"],
        )
        _, context = pp.preprocess("Tell me more", session)
        assert "What about Iran?" in context
        assert "IRGC" in context

    def test_abbreviation_table_has_key_entries(self):
        from periphery.query.preprocessor import ABBREVIATIONS

        assert "IRGC" in ABBREVIATIONS
        assert "OFAC" in ABBREVIATIONS
        assert "NATO" in ABBREVIATIONS
        assert "JCPOA" in ABBREVIATIONS
        assert len(ABBREVIATIONS) > 40


# ── Persistence Tests ────────────────────────────────────────────────────


class TestPersistence:
    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_query.db")

    @pytest.mark.asyncio
    async def test_initialize(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()
        assert store._initialized is True

    @pytest.mark.asyncio
    async def test_save_and_retrieve_query(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()

        await store.save_query(
            query_id="q1",
            query_text="What about Iran?",
            parsed_intent={"query_type": "entity_lookup"},
            execution_plan={"plan_id": "p1"},
            result_summary={"entities": 3},
            execution_stats={"total_time_ms": 500},
            session_id="s1",
            response_time_ms=500,
        )

        queries = await store.get_recent_queries(limit=10)
        assert len(queries) == 1
        assert queries[0]["query_text"] == "What about Iran?"
        assert queries[0]["response_time_ms"] == 500

    @pytest.mark.asyncio
    async def test_session_save_and_load(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()

        await store.save_session("s1", {"previous_queries": [{"query": "test"}]})
        loaded = await store.load_session("s1")
        assert loaded is not None
        assert loaded["previous_queries"][0]["query"] == "test"

    @pytest.mark.asyncio
    async def test_session_load_nonexistent(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()

        loaded = await store.load_session("nonexistent")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_bookmark_save_and_retrieve(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()

        # Save a query first
        await store.save_query(
            query_id="q1",
            query_text="Test query",
            parsed_intent={},
            execution_plan={},
            result_summary={},
            execution_stats={},
            session_id="s1",
        )

        await store.save_bookmark("q1", "s1", "Important query")
        bookmarks = await store.get_bookmarks("s1")
        assert len(bookmarks) == 1
        assert bookmarks[0]["label"] == "Important query"

    @pytest.mark.asyncio
    async def test_feedback(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()

        await store.save_query(
            query_id="q1",
            query_text="Test",
            parsed_intent={},
            execution_plan={},
            result_summary={},
            execution_stats={},
        )
        await store.save_feedback("q1", {"rating": "thumbs_up", "notes": "Helpful"})

        stats = await store.get_query_stats()
        assert stats["queries_with_feedback"] == 1

    @pytest.mark.asyncio
    async def test_annotation(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()

        await store.save_annotation(
            annotation_type="entity_merge",
            target_type="entity",
            target_id="e1",
            annotation_data={"merge_with": "e2"},
            session_id="s1",
        )
        # No error means success

    @pytest.mark.asyncio
    async def test_query_stats(self, db_path):
        from periphery.query.persistence import QueryStore

        store = QueryStore(db_path)
        await store.initialize()

        stats = await store.get_query_stats()
        assert stats["total_queries"] == 0
        assert stats["avg_response_ms"] == 0


# ── Retriever Tests ──────────────────────────────────────────────────────


class TestRetriever:
    def _make_snapshot(self):
        from periphery.crystallizer.models import (
            Anomaly,
            CorpusStats,
            DetectedCluster,
            GradientComponents,
            LivingOntologySnapshot,
            RelationalGradient,
            Trajectory,
        )

        return LivingOntologySnapshot(
            snapshot_id="snap1",
            corpus_stats=CorpusStats(total_documents=50),
            clusters=[
                DetectedCluster(
                    cluster_id="c1",
                    primary_space="semantic",
                    label="Iranian Maritime Activity",
                    size=10,
                    confidence=0.8,
                    key_entities=["Iran", "IRGC", "Red Sea"],
                    key_relationships=[{"subject": "IRGC", "predicate": "operates_in", "object": "Red Sea"}],
                    member_document_ids=["d1", "d2", "d3"],
                ),
                DetectedCluster(
                    cluster_id="c2",
                    primary_space="semantic",
                    label="Saudi Energy",
                    size=8,
                    confidence=0.7,
                    key_entities=["Saudi Aramco", "OPEC"],
                    member_document_ids=["d4", "d5"],
                ),
            ],
            trajectories=[
                Trajectory(
                    trajectory_id="t1",
                    cluster_id="c1",
                    space="semantic",
                    velocity=0.05,
                    confidence=0.6,
                    pattern="convergence",
                ),
            ],
            anomalies=[
                Anomaly(
                    anomaly_id="a1",
                    document_id="d10",
                    anomaly_type="novel_entity",
                    anomaly_score=0.9,
                    outlier_spaces=["semantic"],
                    source_credibility=2,
                ),
            ],
            relational_gradients=[
                RelationalGradient(
                    source_cluster="c1",
                    target_cluster="c2",
                    gradient_score=0.6,
                    components=GradientComponents(
                        entity_co_membership=0.3,
                        bridge_entities=["OPEC"],
                    ),
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_cluster_retrieval(self):
        from periphery.query.models import PlanOperation
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)
        snapshot = self._make_snapshot()

        result = await retriever._op_cluster_retrieval(
            PlanOperation(
                operation_id="op1",
                type="cluster_retrieval",
                parameters={"cluster_ids": ["c1"]},
            ),
            "test query",
            snapshot,
            {},
        )

        assert len(result["clusters"]) == 1
        assert result["clusters"][0].cluster_id == "c1"
        assert result["clusters"][0].label == "Iranian Maritime Activity"

    @pytest.mark.asyncio
    async def test_anomaly_retrieval(self):
        from periphery.query.models import PlanOperation
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)
        snapshot = self._make_snapshot()

        result = await retriever._op_anomaly_retrieval(
            PlanOperation(
                operation_id="op1",
                type="anomaly_retrieval",
                parameters={},
            ),
            "unusual activity",
            snapshot,
            {},
        )

        assert len(result["anomalies"]) == 1
        assert result["anomalies"][0].score == 0.9

    @pytest.mark.asyncio
    async def test_trajectory_retrieval(self):
        from periphery.query.models import PlanOperation
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)
        snapshot = self._make_snapshot()

        result = await retriever._op_trajectory_retrieval(
            PlanOperation(
                operation_id="op1",
                type="trajectory_retrieval",
                parameters={"cluster_ids": ["c1"]},
            ),
            "trajectory query",
            snapshot,
            {},
        )

        assert len(result["trajectories"]) == 1
        assert result["trajectories"][0].pattern == "convergence"

    @pytest.mark.asyncio
    async def test_relational_path_same_cluster(self):
        from periphery.query.models import PlanOperation
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)
        snapshot = self._make_snapshot()

        result = await retriever._op_relational_path(
            PlanOperation(
                operation_id="op1",
                type="relational_path",
                parameters={"entity_a": "Iran", "entity_b": "IRGC"},
            ),
            "connection query",
            snapshot,
            {},
        )

        assert len(result["relational_paths"]) > 0
        assert result["relational_paths"][0].path_type == "direct"

    @pytest.mark.asyncio
    async def test_relational_path_cross_cluster(self):
        from periphery.query.models import PlanOperation
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)
        snapshot = self._make_snapshot()

        result = await retriever._op_relational_path(
            PlanOperation(
                operation_id="op1",
                type="relational_path",
                parameters={"entity_a": "Iran", "entity_b": "Saudi Aramco"},
            ),
            "connection query",
            snapshot,
            {},
        )

        # Should find cluster-mediated path via gradient
        assert len(result["relational_paths"]) > 0
        assert result["relational_paths"][0].path_type == "cluster_mediated"

    @pytest.mark.asyncio
    async def test_geographic_filter(self):
        from periphery.query.models import PlanOperation
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)
        snapshot = self._make_snapshot()

        result = await retriever._op_geographic_filter(
            PlanOperation(
                operation_id="op1",
                type="geographic_filter",
                parameters={"regions": ["Red Sea"]},
            ),
            "geographic query",
            snapshot,
            {},
        )

        # Should match cluster c1 which has "Red Sea" in key entities
        assert len(result["clusters"]) >= 1

    def test_merge_results_deduplication(self):
        from periphery.query.models import ClusterResult
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)

        completed = {
            "op1": {
                "clusters": [
                    ClusterResult(cluster_id="c1", label="Test", confidence=0.8, relevance_score=0.9),
                ],
            },
            "op2": {
                "clusters": [
                    ClusterResult(cluster_id="c1", label="Test", confidence=0.8, relevance_score=0.5),
                ],
            },
        }

        results = retriever._merge_results("q1", completed, "ranked_fusion", 0.0, 50)
        # Should be deduplicated — keep highest relevance
        assert len(results.clusters) == 1
        assert results.clusters[0].relevance_score == 0.9

    def test_merge_results_confidence_floor(self):
        from periphery.query.models import ClusterResult
        from periphery.query.retriever import MultiSpaceRetriever

        store_mock = MagicMock()
        retriever = MultiSpaceRetriever(store_mock)

        completed = {
            "op1": {
                "clusters": [
                    ClusterResult(cluster_id="c1", confidence=0.9, relevance_score=0.9),
                    ClusterResult(cluster_id="c2", confidence=0.2, relevance_score=0.5),
                ],
            },
        }

        results = retriever._merge_results("q1", completed, "ranked_fusion", 0.5, 50)
        assert len(results.clusters) == 1
        assert results.clusters[0].cluster_id == "c1"


# ── Synthesizer Tests ────────────────────────────────────────────────────


class TestSynthesizer:
    def test_fallback_output(self):
        from periphery.query.models import ClusterResult, EntityResult, RetrievalResults
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()
        results = RetrievalResults(
            query_id="q1",
            entities=[
                EntityResult(
                    canonical_id="e1",
                    name="Iran",
                    type="GPE",
                    confidence=0.9,
                    relevance_score=0.95,
                ),
            ],
            clusters=[
                ClusterResult(
                    cluster_id="c1",
                    label="Iranian Maritime",
                    confidence=0.8,
                    size=10,
                    relevance_score=0.8,
                ),
            ],
        )
        output = synth._build_fallback_output(results)
        assert output.summary != ""
        assert output.sources_used == 2
        assert len(output.key_findings) > 0

    def test_simple_output(self):
        from periphery.query.models import EntityResult, RetrievalResults
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()
        results = RetrievalResults(
            query_id="q1",
            entities=[
                EntityResult(
                    canonical_id="e1",
                    name="Iran",
                    type="GPE",
                    confidence=0.9,
                ),
            ],
        )
        output = synth._build_simple_output(results)
        assert "Iran" in output.summary

    def test_should_skip_synthesis(self):
        from periphery.query.models import EntityResult, RetrievalResults
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()
        results = RetrievalResults(
            query_id="q1",
            entities=[EntityResult(canonical_id="e1", name="Iran", type="GPE")],
        )
        assert synth._should_skip_synthesis(results, "entity_lookup") is True
        assert synth._should_skip_synthesis(results, "situational_awareness") is False

    def test_build_results_summary(self):
        from periphery.query.models import EntityResult, RetrievalResults
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()
        results = RetrievalResults(
            query_id="q1",
            entities=[
                EntityResult(
                    canonical_id="e1",
                    name="Iran",
                    type="GPE",
                    confidence=0.9,
                    relevance_score=0.95,
                ),
            ],
        )
        summary_json = synth._build_results_summary(results)
        summary = json.loads(summary_json)
        assert "entities" in summary
        assert summary["entities"][0]["name"] == "Iran"

    @pytest.mark.asyncio
    async def test_synthesize_without_api_key(self):
        from periphery.query.models import EntityResult, RetrievalResults
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()
        results = RetrievalResults(
            query_id="q1",
            entities=[
                EntityResult(
                    canonical_id="e1",
                    name="Iran",
                    type="GPE",
                    confidence=0.9,
                ),
            ],
        )
        output, elapsed = await synth.synthesize("What about Iran?", results)
        assert output.summary != ""
        assert elapsed >= 0


# ── Integration-style Tests ──────────────────────────────────────────────


class TestAnalyticalEngine:
    @pytest.mark.asyncio
    async def test_engine_initialization(self):
        from periphery.query.analytical_engine import AnalyticalQueryEngine

        store_mock = MagicMock()
        store_mock.search.return_value = []
        store_mock.total = 0

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            engine = AnalyticalQueryEngine(
                faiss_store=store_mock,
                db_path=db_path,
            )
            await engine.initialize()
            assert engine.query_store is not None
            assert engine.snapshot is None
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_engine_query_basic(self):
        from periphery.query.analytical_engine import AnalyticalQueryEngine
        from periphery.query.models import AnalyticalQueryRequest

        store_mock = MagicMock()
        store_mock.search.return_value = []
        store_mock.total = 0

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            engine = AnalyticalQueryEngine(
                faiss_store=store_mock,
                db_path=db_path,
            )
            await engine.initialize()

            # Mock the embedder to avoid loading the model
            with patch("periphery.query.analytical_engine.embedder") as mock_embedder:
                mock_embedder.embed.return_value = [np.zeros(384, dtype=np.float32)]

                with patch("periphery.query.retriever.embedder") as mock_ret_embedder:
                    mock_ret_embedder.embed.return_value = [np.zeros(384, dtype=np.float32)]

                    request = AnalyticalQueryRequest(
                        query="What's happening in the Middle East?"
                    )
                    response = await engine.query(request)

                    assert response.query_id is not None
                    assert response.parsed_intent.query_type in (
                        "situational_awareness", "geographic_query", "freeform",
                    )
                    assert response.execution_stats.total_time_ms >= 0
        finally:
            os.unlink(db_path)

    def test_subscribe_unsubscribe(self):
        from periphery.query.analytical_engine import AnalyticalQueryEngine

        store_mock = MagicMock()
        engine = AnalyticalQueryEngine(faiss_store=store_mock)

        engine.subscribe("q1", {"query_type": "entity_lookup"})
        assert "q1" in engine.subscriptions

        engine.unsubscribe("q1")
        assert "q1" not in engine.subscriptions

    def test_snapshot_property(self):
        from periphery.crystallizer.models import LivingOntologySnapshot
        from periphery.query.analytical_engine import AnalyticalQueryEngine

        store_mock = MagicMock()
        engine = AnalyticalQueryEngine(faiss_store=store_mock)

        assert engine.snapshot is None

        snapshot = LivingOntologySnapshot(snapshot_id="s1")
        engine.snapshot = snapshot
        assert engine.snapshot.snapshot_id == "s1"


# ── Exa Client Tests ────────────────────────────────────────────────────


class TestExaClient:
    def test_exa_models_defaults(self):
        from periphery.query.exa_client import ExaSearchResult, ExaSource

        source = ExaSource(title="Test", url="http://example.com", text="content")
        assert source.score == 0.0
        assert source.published_date is None
        assert source.author is None

        result = ExaSearchResult()
        assert result.sources == []
        assert result.query_used == ""
        assert result.search_time_ms == 0
        assert result.enabled is True

    def test_exa_search_result_with_sources(self):
        from periphery.query.exa_client import ExaSearchResult, ExaSource

        sources = [
            ExaSource(
                title="Article 1",
                url="http://example.com/1",
                text="Some content",
                score=0.95,
                published_date="2024-01-15",
            ),
            ExaSource(
                title="Article 2",
                url="http://example.com/2",
                text="More content",
                score=0.8,
            ),
        ]
        result = ExaSearchResult(
            sources=sources,
            query_used="test query",
            search_time_ms=150,
        )
        assert len(result.sources) == 2
        assert result.sources[0].score == 0.95
        assert result.query_used == "test query"

    @pytest.mark.asyncio
    async def test_exa_client_disabled(self):
        from periphery.query.exa_client import ExaSearchClient

        with patch("periphery.query.exa_client.ExaSearchClient.__init__", return_value=None) as mock_init:
            client = ExaSearchClient.__new__(ExaSearchClient)
            client._enabled = False
            client._client = None
            client._max_results = 10
            client._cache_ttl = 300.0
            client._cache = {}

            result = await client.search("test query")
            assert result.enabled is False
            assert result.sources == []

    @pytest.mark.asyncio
    async def test_exa_client_search_success(self):
        from periphery.query.exa_client import ExaSearchClient

        # Create a mock Exa response
        mock_result = MagicMock()
        mock_result.title = "Test Article"
        mock_result.url = "http://example.com/test"
        mock_result.published_date = "2024-06-01"
        mock_result.text = "Article content here"
        mock_result.score = 0.92
        mock_result.author = "John Doe"

        mock_response = MagicMock()
        mock_response.results = [mock_result]

        with patch("periphery.query.exa_client.ExaSearchClient.__init__", return_value=None):
            client = ExaSearchClient.__new__(ExaSearchClient)
            client._enabled = True
            client._max_results = 10
            client._cache_ttl = 300.0
            client._cache = {}
            client._client = MagicMock()
            client._client.search_and_contents = MagicMock(return_value=mock_response)

            result = await client.search("Iran sanctions")
            assert len(result.sources) == 1
            assert result.sources[0].title == "Test Article"
            assert result.sources[0].score == 0.92
            assert result.query_used == "Iran sanctions"
            assert result.search_time_ms >= 0

    @pytest.mark.asyncio
    async def test_exa_client_search_with_intent_context(self):
        from periphery.query.exa_client import ExaSearchClient

        mock_response = MagicMock()
        mock_response.results = []

        with patch("periphery.query.exa_client.ExaSearchClient.__init__", return_value=None):
            client = ExaSearchClient.__new__(ExaSearchClient)
            client._enabled = True
            client._max_results = 10
            client._cache_ttl = 300.0
            client._cache = {}
            client._client = MagicMock()
            client._client.search_and_contents = MagicMock(return_value=mock_response)

            intent_context = {
                "query_type": "entity_lookup",
                "entity_names": ["IRGC", "Hezbollah"],
                "temporal_focus": "last 7 days",
            }
            result = await client.search("Iran proxy networks", intent_context)
            # Entity names should be appended to query
            assert "IRGC" in result.query_used or "Hezbollah" in result.query_used

    @pytest.mark.asyncio
    async def test_exa_client_caching(self):
        from periphery.query.exa_client import ExaSearchClient

        mock_response = MagicMock()
        mock_result = MagicMock()
        mock_result.title = "Cached Article"
        mock_result.url = "http://example.com/cached"
        mock_result.published_date = None
        mock_result.text = "Content"
        mock_result.score = 0.5
        mock_result.author = None
        mock_response.results = [mock_result]

        with patch("periphery.query.exa_client.ExaSearchClient.__init__", return_value=None):
            client = ExaSearchClient.__new__(ExaSearchClient)
            client._enabled = True
            client._max_results = 10
            client._cache_ttl = 300.0
            client._cache = {}
            client._client = MagicMock()
            client._client.search_and_contents = MagicMock(return_value=mock_response)

            # First call — hits API
            result1 = await client.search("test query")
            assert len(result1.sources) == 1

            # Second call — should use cache
            result2 = await client.search("test query")
            assert len(result2.sources) == 1

            # API should only be called once
            assert client._client.search_and_contents.call_count == 1

    @pytest.mark.asyncio
    async def test_exa_client_api_error_returns_empty(self):
        from periphery.query.exa_client import ExaSearchClient

        with patch("periphery.query.exa_client.ExaSearchClient.__init__", return_value=None):
            client = ExaSearchClient.__new__(ExaSearchClient)
            client._enabled = True
            client._max_results = 10
            client._cache_ttl = 300.0
            client._cache = {}
            client._client = MagicMock()
            client._client.search_and_contents = MagicMock(side_effect=Exception("API error"))

            result = await client.search("test query")
            assert result.sources == []
            assert result.query_used == "test query"


class TestExaIntegration:
    """Test Exa integration points in synthesizer and engine."""

    def test_synthesizer_exa_context_building(self):
        from periphery.query.exa_client import ExaSearchResult, ExaSource
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()
        exa_result = ExaSearchResult(
            sources=[
                ExaSource(
                    title="Breaking News",
                    url="http://example.com/news",
                    published_date="2024-06-01",
                    text="Important development reported.",
                ),
            ],
            query_used="test query",
            search_time_ms=100,
        )

        context = synth._build_exa_context(exa_result)
        assert "External Intelligence" in context
        assert "Breaking News" in context
        assert "Important development reported." in context
        assert "2024-06-01" in context

    @pytest.mark.asyncio
    async def test_synthesizer_with_exa_results(self):
        from periphery.query.exa_client import ExaSearchResult, ExaSource
        from periphery.query.models import EntityResult, RetrievalResults
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()  # No API key — uses fallback
        results = RetrievalResults(
            query_id="q1",
            entities=[
                EntityResult(canonical_id="e1", name="Iran", type="GPE", confidence=0.9),
            ],
        )
        exa_result = ExaSearchResult(
            sources=[
                ExaSource(
                    title="Iran News",
                    url="http://example.com/iran",
                    text="Recent developments.",
                ),
            ],
            query_used="Iran",
            search_time_ms=50,
        )

        # With fallback (no API key), exa_results are not used in output
        # but the method should still accept them without error
        output, elapsed = await synth.synthesize(
            "What about Iran?", results, exa_results=exa_result,
        )
        assert output.summary != ""
        assert elapsed >= 0

    @pytest.mark.asyncio
    async def test_synthesizer_with_none_exa_results(self):
        from periphery.query.models import EntityResult, RetrievalResults
        from periphery.query.synthesizer import ResultSynthesizer

        synth = ResultSynthesizer()
        results = RetrievalResults(
            query_id="q1",
            entities=[
                EntityResult(canonical_id="e1", name="Iran", type="GPE", confidence=0.9),
            ],
        )
        # Passing None explicitly — pipeline without Exa
        output, elapsed = await synth.synthesize(
            "What about Iran?", results, exa_results=None,
        )
        assert output.summary != ""

    def test_execution_stats_exa_field(self):
        from periphery.query.models import ExecutionStats

        stats = ExecutionStats()
        assert stats.exa_search_ms == 0

        stats = ExecutionStats(exa_search_ms=150)
        assert stats.exa_search_ms == 150

    def test_analytical_query_response_exa_fields(self):
        from periphery.query.models import (
            AnalyticalQueryResponse,
            ParsedIntent,
            SynthesisOutput,
        )

        response = AnalyticalQueryResponse(
            query_id="q1",
            parsed_intent=ParsedIntent(),
            synthesis=SynthesisOutput(),
        )
        assert response.external_sources == []
        assert response.exa_query_used == ""

        response = AnalyticalQueryResponse(
            query_id="q1",
            parsed_intent=ParsedIntent(),
            synthesis=SynthesisOutput(),
            external_sources=[
                {"title": "Test", "url": "http://example.com", "published_date": None}
            ],
            exa_query_used="test query",
        )
        assert len(response.external_sources) == 1
        assert response.exa_query_used == "test query"

    @pytest.mark.asyncio
    async def test_engine_search_exa_without_client(self):
        from periphery.query.analytical_engine import AnalyticalQueryEngine
        from periphery.query.models import ParsedIntent

        store_mock = MagicMock()
        engine = AnalyticalQueryEngine(faiss_store=store_mock)
        assert engine._exa_client is None

        intent = ParsedIntent(query_type="entity_lookup")
        result = await engine._search_exa("test query", intent)
        assert result is None

    @pytest.mark.asyncio
    async def test_engine_query_without_exa_key(self):
        """Full pipeline works normally when no Exa key is configured."""
        from periphery.query.analytical_engine import AnalyticalQueryEngine
        from periphery.query.models import AnalyticalQueryRequest

        store_mock = MagicMock()
        store_mock.search.return_value = []
        store_mock.total = 0

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            engine = AnalyticalQueryEngine(
                faiss_store=store_mock,
                db_path=db_path,
            )
            await engine.initialize()

            with patch("periphery.query.analytical_engine.embedder") as mock_embedder:
                mock_embedder.embed.return_value = [np.zeros(384, dtype=np.float32)]

                with patch("periphery.query.retriever.embedder") as mock_ret_embedder:
                    mock_ret_embedder.embed.return_value = [np.zeros(384, dtype=np.float32)]

                    request = AnalyticalQueryRequest(query="Test query")
                    response = await engine.query(request)

                    assert response.query_id is not None
                    assert response.external_sources == []
                    assert response.exa_query_used == ""
                    assert response.execution_stats.exa_search_ms == 0
        finally:
            os.unlink(db_path)

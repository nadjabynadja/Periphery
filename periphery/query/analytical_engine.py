"""Analytical Query Engine — orchestrates all query interface components.

Wires together the preprocessor, intent parser, query planner,
multi-space retriever, result synthesizer, and confidence renderer
into a single coherent query pipeline.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import numpy as np

from periphery.crystallizer.models import LivingOntologySnapshot
from periphery.ingest import embedder
from periphery.ingest.store import FAISSStore, MultiSpaceIndexManager
from periphery.query.intent_parser import IntentParser
from periphery.query.models import (
    AnalyticalQueryRequest,
    AnalyticalQueryResponse,
    ExecutionStats,
    SessionState,
)
from periphery.query.persistence import QueryStore
from periphery.query.planner import QueryPlanner
from periphery.query.preprocessor import QueryPreprocessor
from periphery.query.renderer import ConfidenceRenderer
from periphery.query.retriever import MultiSpaceRetriever
from periphery.query.synthesizer import ResultSynthesizer

logger = logging.getLogger(__name__)


class AnalyticalQueryEngine:
    """Full query pipeline: preprocess → parse → plan → retrieve → synthesize → render."""

    def __init__(
        self,
        faiss_store: FAISSStore,
        multi_space: MultiSpaceIndexManager | None = None,
        entity_index: Any | None = None,
        anthropic_api_key: str = "",
        db_path: str = "",
        llm_model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._preprocessor = QueryPreprocessor(entity_index=entity_index)
        self._intent_parser = IntentParser(
            anthropic_api_key=anthropic_api_key,
            model=llm_model,
        )
        self._planner = QueryPlanner()
        self._retriever = MultiSpaceRetriever(
            faiss_store=faiss_store,
            multi_space=multi_space,
            entity_index=entity_index,
            db_path=db_path,
        )
        self._synthesizer = ResultSynthesizer(
            anthropic_api_key=anthropic_api_key,
            model=llm_model,
        )
        self._renderer = ConfidenceRenderer()
        self._query_store: QueryStore | None = None
        if db_path:
            self._query_store = QueryStore(db_path)

        # In-memory session store (upgrade to Redis for multi-instance)
        self._sessions: dict[str, SessionState] = {}

        # Snapshot reference — updated by the crystallizer
        self._snapshot: LivingOntologySnapshot | None = None

        # Active query subscriptions for streaming
        self._subscriptions: dict[str, dict[str, Any]] = {}

    @property
    def query_store(self) -> QueryStore | None:
        return self._query_store

    @property
    def snapshot(self) -> LivingOntologySnapshot | None:
        return self._snapshot

    @snapshot.setter
    def snapshot(self, value: LivingOntologySnapshot | None) -> None:
        self._snapshot = value

    @property
    def subscriptions(self) -> dict[str, dict[str, Any]]:
        return self._subscriptions

    async def initialize(self) -> None:
        if self._query_store:
            await self._query_store.initialize()

    async def query(self, request: AnalyticalQueryRequest) -> AnalyticalQueryResponse:
        """Execute the full analytical query pipeline."""
        total_start = time.monotonic()
        query_id = str(uuid.uuid4())[:16]

        # 1. Load/create session
        session = await self._get_or_create_session(request.session_id)

        # 2. Preprocess
        processed_text, session_context = self._preprocessor.preprocess(
            request.query, session
        )

        # 3. Embed query
        try:
            query_embedding = embedder.embed([processed_text])[0]
        except Exception:
            query_embedding = None

        # 4. Parse intent
        intent, intent_ms = await self._intent_parser.parse(
            processed_text,
            snapshot=self._snapshot,
            query_embedding=query_embedding,
            session_context=session_context,
        )

        # 5. Plan
        plan, planning_ms = self._planner.plan(intent, query_id)

        # 6. Retrieve
        results, retrieval_ms = await self._retriever.execute(
            plan,
            processed_text,
            self._snapshot,
            confidence_floor=request.confidence_floor,
            max_results=request.max_results,
        )

        # 7. Synthesize
        synthesis, synthesis_ms = await self._synthesizer.synthesize(
            request.query,
            results,
            query_type=intent.query_type,
        )

        # 8. Render
        rendered_results: dict[str, Any] = {}
        if request.include_rendering:
            rendered_results = self._renderer.render(results)
        else:
            rendered_results = results.model_dump()

        # 9. Build stats
        total_ms = int((time.monotonic() - total_start) * 1000)
        stats = ExecutionStats(
            total_time_ms=total_ms,
            intent_parsing_ms=intent_ms,
            planning_ms=planning_ms,
            retrieval_ms=retrieval_ms,
            synthesis_ms=synthesis_ms,
            operations_executed=len(plan.operations),
            documents_searched=self._count_documents_searched(results),
            cached=intent_ms < 5,
        )

        # 10. Update session
        if session:
            session.previous_queries.append({
                "query": request.query,
                "query_id": query_id,
                "summary": synthesis.summary,
            })
            if len(session.previous_queries) > 10:
                session.previous_queries = session.previous_queries[-10:]
            session.last_active = time.time()  # type: ignore[assignment]

        # 11. Persist query history
        if self._query_store:
            try:
                await self._query_store.save_query(
                    query_id=query_id,
                    query_text=request.query,
                    parsed_intent=intent.model_dump(),
                    execution_plan=plan.model_dump(),
                    result_summary={
                        "entities": len(results.entities),
                        "clusters": len(results.clusters),
                        "relationships": len(results.relationships),
                        "trajectories": len(results.trajectories),
                        "anomalies": len(results.anomalies),
                        "summary": synthesis.summary,
                    },
                    execution_stats=stats.model_dump(),
                    session_id=request.session_id,
                    response_time_ms=total_ms,
                )
            except Exception:
                logger.debug("query_history_save_failed", exc_info=True)

        return AnalyticalQueryResponse(
            query_id=query_id,
            parsed_intent=intent,
            synthesis=synthesis,
            results=rendered_results,
            execution_stats=stats,
        )

    def _count_documents_searched(self, results: Any) -> int:
        doc_ids: set[str] = set()
        for e in results.entities:
            doc_ids.update(e.source_documents)
        return len(doc_ids) if doc_ids else 0

    async def _get_or_create_session(
        self, session_id: str | None
    ) -> SessionState | None:
        if session_id is None:
            return None

        if session_id in self._sessions:
            return self._sessions[session_id]

        # Try loading from persistence
        if self._query_store:
            try:
                stored = await self._query_store.load_session(session_id)
                if stored:
                    session = SessionState(session_id=session_id, **stored)
                    self._sessions[session_id] = session
                    return session
            except Exception:
                pass

        session = SessionState(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def subscribe(self, query_id: str, intent_data: dict[str, Any]) -> None:
        """Register a query for streaming updates."""
        self._subscriptions[query_id] = {
            "intent": intent_data,
            "created_at": time.time(),
        }

    def unsubscribe(self, query_id: str) -> None:
        self._subscriptions.pop(query_id, None)

    def check_updates(self, query_id: str) -> list[dict[str, Any]]:
        """Check if any ontology changes are relevant to a subscribed query.

        Called after each crystallizer update to push deltas through WebSocket.
        """
        sub = self._subscriptions.get(query_id)
        if sub is None or self._snapshot is None:
            return []

        # Placeholder — in production this would diff the current snapshot
        # against the last snapshot sent for this query
        return []

"""Enrichment pipeline — async stage-chaining architecture.

Documents flow through a sequence of independent, composable stages.
Each stage reads from an input queue and writes to an output queue.
If a stage fails on a document, the failure is tagged and the document
passes forward with whatever enrichment succeeded.
"""

from __future__ import annotations

import abc
import asyncio
import time
from typing import Callable

import structlog

from periphery.rss_ingest.models import IngestedDocument

from .models import (
    EnrichedDocument,
    EnrichedDocumentContent,
    EnrichedDocumentSource,
    EnrichedEntity,
    EnrichedRelationship,
    EnrichmentMetadata,
    PipelineDocument,
)

logger = structlog.get_logger(__name__)


class EnrichmentStage(abc.ABC):
    """Abstract base class for a pipeline enrichment stage."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable name of this stage."""
        ...

    @abc.abstractmethod
    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Process a document and return it with enrichments added."""
        ...


class EnrichmentPipeline:
    """Async enrichment pipeline that chains stages together.

    Each stage is independent and fault-tolerant — a failure in one stage
    tags the document with the failure but doesn't block subsequent stages.
    """

    def __init__(
        self,
        stages: list[EnrichmentStage] | None = None,
        *,
        concurrency: int = 4,
    ) -> None:
        self._stages: list[EnrichmentStage] = stages or []
        self._concurrency = concurrency
        self._input_queue: asyncio.Queue[IngestedDocument] = asyncio.Queue()
        self._output_queue: asyncio.Queue[EnrichedDocument] = asyncio.Queue()
        self._running = False
        self._workers: list[asyncio.Task] = []

    def add_stage(self, stage: EnrichmentStage) -> None:
        """Append a stage to the pipeline."""
        self._stages.append(stage)

    @property
    def stage_names(self) -> list[str]:
        return [s.name for s in self._stages]

    async def submit(self, doc: IngestedDocument) -> None:
        """Submit a raw document for enrichment."""
        await self._input_queue.put(doc)

    async def get_result(self) -> EnrichedDocument:
        """Get the next enriched document from the output."""
        return await self._output_queue.get()

    def result_ready(self) -> bool:
        """Check if an enriched document is available."""
        return not self._output_queue.empty()

    @property
    def input_depth(self) -> int:
        return self._input_queue.qsize()

    @property
    def output_depth(self) -> int:
        return self._output_queue.qsize()

    async def start(self) -> None:
        """Start pipeline workers."""
        if self._running:
            return
        self._running = True
        for i in range(self._concurrency):
            task = asyncio.create_task(
                self._worker(i), name=f"enrichment-worker-{i}"
            )
            self._workers.append(task)
        logger.info(
            "enrichment_pipeline_started",
            stages=[s.name for s in self._stages],
            concurrency=self._concurrency,
        )

    async def stop(self) -> None:
        """Stop pipeline workers gracefully."""
        self._running = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("enrichment_pipeline_stopped")

    async def _worker(self, worker_id: int) -> None:
        """Worker loop: pull from input, run stages, push to output."""
        while self._running:
            try:
                raw_doc = await asyncio.wait_for(
                    self._input_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            start = time.monotonic()
            try:
                pipeline_doc = self._ingest_to_pipeline(raw_doc)
                pipeline_doc = await self._run_stages(pipeline_doc)
                enriched = self._pipeline_to_enriched(pipeline_doc, start)
                await self._output_queue.put(enriched)
                logger.info(
                    "document_enriched",
                    doc_id=enriched.id,
                    stages_completed=enriched.metadata.enrichment_stages_completed,
                    failures=enriched.metadata.enrichment_failures,
                    processing_time_ms=enriched.metadata.processing_time_ms,
                    worker=worker_id,
                )
            except Exception:
                logger.exception(
                    "enrichment_worker_error",
                    doc_id=raw_doc.id,
                    worker=worker_id,
                )

    async def _run_stages(self, doc: PipelineDocument) -> PipelineDocument:
        """Run all stages on a document, capturing failures per-stage."""
        for stage in self._stages:
            try:
                doc = await stage.process(doc)
                doc.enrichment_stages_completed.append(stage.name)
            except Exception as exc:
                doc.enrichment_failures.append(f"{stage.name}: {exc!r}")
                logger.warning(
                    "stage_failed",
                    stage=stage.name,
                    doc_id=doc.id,
                    error=str(exc),
                )
        return doc

    async def process_document(self, raw_doc: IngestedDocument) -> EnrichedDocument:
        """Process a single document synchronously through all stages.

        Useful for testing and one-off processing without the worker loop.
        """
        start = time.monotonic()
        pipeline_doc = self._ingest_to_pipeline(raw_doc)
        pipeline_doc = await self._run_stages(pipeline_doc)
        return self._pipeline_to_enriched(pipeline_doc, start)

    def _ingest_to_pipeline(self, doc: IngestedDocument) -> PipelineDocument:
        """Convert an IngestedDocument to a PipelineDocument."""
        return PipelineDocument(
            id=doc.id,
            source_feed=doc.source_feed,
            source_category=doc.source_category,
            title=doc.title,
            url=doc.url,
            full_text=doc.content,
            published=doc.published,
            ingested=doc.ingested,
            priority=doc.metadata.get("priority", 3),
        )

    def _pipeline_to_enriched(
        self, doc: PipelineDocument, start_time: float
    ) -> EnrichedDocument:
        """Convert a PipelineDocument to the final EnrichedDocument."""
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        credibility_tier = 4
        if doc.source_credibility:
            credibility_tier = doc.source_credibility.source_credibility_tier

        # Build enriched entities
        entities: list[EnrichedEntity] = []
        for ent in doc.extracted_entities:
            entity_key = f"{ent.text}:{ent.entity_type}"
            canonical_id = doc.resolved_entity_map.get(entity_key, "")
            enriched_ent = EnrichedEntity(
                canonical_id=canonical_id,
                text=ent.text,
                entity_type=ent.entity_type,
                confidence=ent.confidence,
                temporal_context=doc.temporal_contexts.get(entity_key),
                geospatial=doc.geospatial_data.get(entity_key),
                credibility_tier=credibility_tier,
            )
            entities.append(enriched_ent)

        # Build enriched relationships
        relationships: list[EnrichedRelationship] = []
        for rel in doc.extracted_relationships:
            subj_key = f"{rel.subject_text}:{rel.subject_type}"
            obj_key = f"{rel.object_text}:{rel.object_type}"
            enriched_rel = EnrichedRelationship(
                subject_id=doc.resolved_entity_map.get(subj_key, rel.subject_text),
                predicate=rel.predicate,
                object_id=doc.resolved_entity_map.get(obj_key, rel.object_text),
                confidence=rel.confidence,
                extraction_tier=rel.extraction_tier,
                temporal_context=doc.temporal_contexts.get(
                    f"{rel.subject_text}-{rel.predicate}-{rel.object_text}"
                ),
                evidence=rel.evidence,
                credibility_tier=credibility_tier,
            )
            relationships.append(enriched_rel)

        return EnrichedDocument(
            id=doc.id,
            source=EnrichedDocumentSource(
                feed_url=doc.source_feed,
                source_name=doc.source_name or doc.source_feed,
                source_category=doc.source_category,
                credibility_tier=credibility_tier,
            ),
            content=EnrichedDocumentContent(
                title=doc.title,
                full_text=doc.full_text,
                url=doc.url,
                published=doc.published,
                ingested=doc.ingested,
            ),
            entities=entities,
            relationships=relationships,
            metadata=EnrichmentMetadata(
                enrichment_stages_completed=doc.enrichment_stages_completed,
                enrichment_failures=doc.enrichment_failures,
                processing_time_ms=elapsed_ms,
            ),
        )

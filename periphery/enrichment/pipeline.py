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
from typing import TYPE_CHECKING, Callable

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

if TYPE_CHECKING:
    from periphery.config import get_settings, Settings

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
            source_name=doc.metadata.get("source_name", ""),
            source_category=doc.source_category,
            title=doc.title,
            url=doc.url,
            full_text=doc.content,
            published=doc.published,
            ingested=doc.ingested,
            priority=doc.metadata.get("priority", 3),
            ingest_metadata=doc.metadata,
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
        relationship_counts: dict[str, int] = {}
        for rel in doc.extracted_relationships:
            subj_key = f"{rel.subject_text}:{rel.subject_type}"
            obj_key = f"{rel.object_text}:{rel.object_type}"
            enriched_rel = EnrichedRelationship(
                subject_id=doc.resolved_entity_map.get(subj_key, rel.subject_text),
                predicate=rel.predicate,
                object_id=doc.resolved_entity_map.get(obj_key, rel.object_text),
                confidence=rel.confidence,
                extraction_tier=rel.extraction_tier,
                extraction_method=rel.extraction_method,
                temporal_context=doc.temporal_contexts.get(
                    f"{rel.subject_text}-{rel.predicate}-{rel.object_text}"
                ),
                temporal_qualifier=rel.temporal_qualifier,
                evidence=rel.evidence,
                implicit=rel.implicit,
                co_occurrence_weight=rel.co_occurrence_weight,
                geospatial=doc.relationship_geospatial.get(
                    f"{rel.subject_text}-{rel.predicate}-{rel.object_text}"
                ),
                credibility_tier=credibility_tier,
            )
            relationships.append(enriched_rel)

            # Track tier counts
            tier_key = f"tier_{rel.extraction_tier}"
            relationship_counts[tier_key] = relationship_counts.get(tier_key, 0) + 1

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
            document_geospatial=doc.document_geospatial,
            metadata=EnrichmentMetadata(
                enrichment_stages_completed=doc.enrichment_stages_completed,
                enrichment_failures=doc.enrichment_failures,
                processing_time_ms=elapsed_ms,
                llm_enrichment_status=doc.llm_enrichment_status,
                relationship_counts=relationship_counts,
            ),
        )


def build_enrichment_pipeline(settings: Settings, entity_index=None) -> EnrichmentPipeline:
    """Build a fully configured EnrichmentPipeline from application settings.

    Assembles all six stages in the correct order:
      1. Entity Extraction (SpaCy NER + OSINT regex patterns)
      2. Source Credibility Tagging (must run before relationship extraction)
      3. Relationship Extraction (co-occurrence, dependency, LLM tiers)
      4. Temporal Tagging
      5. Geospatial Resolution
      6. Entity Resolution (must run after extraction stages)
    """
    from .budget import BudgetTracker
    from .stages.entity_extraction import EntityExtractionStage
    from .stages.entity_resolution import EntityResolutionStage
    from .stages.geospatial_resolution import GeospatialResolutionStage
    from .stages.relationship_extraction import RelationshipExtractionStage
    from .stages.source_credibility import SourceCredibilityStage
    from .stages.temporal_tagging import TemporalTaggingStage

    budget_tracker = BudgetTracker(
        hourly_cap_usd=settings.enrichment_llm_hourly_cap_usd,
        daily_cap_usd=settings.enrichment_llm_daily_cap_usd,
    )

    # Build anthropic client only if API key is configured
    anthropic_client = None
    if settings.anthropic_api_key:
        import anthropic

        anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    stages: list[EnrichmentStage] = [
        EntityExtractionStage(spacy_model=settings.enrichment_spacy_model),
        SourceCredibilityStage(),
        RelationshipExtractionStage(
            budget_tracker=budget_tracker,
            anthropic_client=anthropic_client,
            llm_model=settings.enrichment_llm_model,
            tier2_min_priority=settings.enrichment_tier2_min_priority,
            tier3_min_priority=settings.enrichment_tier3_min_priority,
            llm_timeout_seconds=settings.enrichment_llm_timeout_seconds,
            llm_max_tokens_per_request=settings.enrichment_llm_max_tokens_per_request,
        ),
        TemporalTaggingStage(),
        GeospatialResolutionStage(
            cache_db_path=settings.enrichment_geocode_cache_db,
            geonames_db_path=settings.enrichment_geonames_db,
            seed_file_path=settings.enrichment_geospatial_seed_file,
            photon_base_url=settings.enrichment_photon_base_url,
            llm_model_path=settings.enrichment_llm_model_path,
            llm_enabled=settings.enrichment_llm_disambiguator_enabled,
        ),
        EntityResolutionStage(
            entity_index=entity_index,
            fuzzy_threshold=settings.enrichment_fuzzy_match_threshold,
        ),
    ]

    pipeline = EnrichmentPipeline(
        stages=stages,
        concurrency=settings.enrichment_concurrency,
    )

    logger.info(
        "enrichment_pipeline_built",
        stages=[s.name for s in stages],
        concurrency=settings.enrichment_concurrency,
        llm_enabled=anthropic_client is not None,
    )

    return pipeline

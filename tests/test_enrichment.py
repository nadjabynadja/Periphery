"""Tests for the enrichment pipeline.

Tests cover the pipeline scaffold, all enrichment models, entity extraction
regex patterns, source credibility tagging, entity resolution index,
budget tracking, and full pipeline integration (without SpaCy dependency).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from periphery.enrichment.budget import BudgetTracker
from periphery.enrichment.models import (
    CanonicalEntity,
    EnrichedDocument,
    EnrichedEntity,
    EnrichedRelationship,
    EnrichmentMetadata,
    ExtractedEntity,
    ExtractedRelationship,
    GeoCandidate,
    GeospatialData,
    PipelineDocument,
    SourceCredibility,
    TemporalContext,
)
from periphery.enrichment.pipeline import EnrichmentPipeline, EnrichmentStage
from periphery.enrichment.stages.entity_extraction import (
    _PATTERNS,
    _get_sentence_context,
)
from periphery.enrichment.stages.entity_resolution import EntityIndex
from periphery.enrichment.stages.source_credibility import SourceCredibilityStage
from periphery.rss_ingest.models import IngestedDocument


# ── Helper: minimal pipeline document ────────────────────────────────────


def _make_pipeline_doc(**kwargs) -> PipelineDocument:
    defaults = dict(
        id="test-doc-1",
        source_feed="https://example.com/feed",
        source_name="Test Source",
        source_category="news",
        title="Test Article",
        url="https://example.com/article/1",
        full_text="This is a test article about Washington D.C. and Reuters.",
        published=datetime(2025, 1, 15, tzinfo=timezone.utc),
        ingested=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return PipelineDocument(**defaults)


def _make_ingested_doc(**kwargs) -> IngestedDocument:
    defaults = dict(
        id="test-doc-1",
        source_feed="https://example.com/feed",
        source_category="news",
        title="Test Article",
        url="https://example.com/article/1",
        content="Company X acquired Firm Y for $5 billion in Washington D.C.",
    )
    defaults.update(kwargs)
    return IngestedDocument(**defaults)


# ── Pipeline Scaffold Tests ──────────────────────────────────────────────


class DummyStage(EnrichmentStage):
    """A test stage that appends a marker to the document title."""

    def __init__(self, stage_name: str = "dummy", should_fail: bool = False):
        self._name = stage_name
        self._should_fail = should_fail

    @property
    def name(self) -> str:
        return self._name

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        if self._should_fail:
            raise ValueError(f"Stage {self._name} failed intentionally")
        doc.title = f"{doc.title} [{self._name}]"
        return doc


class TestEnrichmentPipeline:
    @pytest.mark.asyncio
    async def test_process_single_document(self):
        pipeline = EnrichmentPipeline(
            stages=[DummyStage("stage_a"), DummyStage("stage_b")]
        )
        raw = _make_ingested_doc()
        result = await pipeline.process_document(raw)

        assert isinstance(result, EnrichedDocument)
        assert result.id == "test-doc-1"
        assert "stage_a" in result.metadata.enrichment_stages_completed
        assert "stage_b" in result.metadata.enrichment_stages_completed
        assert result.metadata.processing_time_ms >= 0

    @pytest.mark.asyncio
    async def test_stage_failure_doesnt_block(self):
        pipeline = EnrichmentPipeline(
            stages=[
                DummyStage("good_stage"),
                DummyStage("bad_stage", should_fail=True),
                DummyStage("after_bad"),
            ]
        )
        raw = _make_ingested_doc()
        result = await pipeline.process_document(raw)

        assert "good_stage" in result.metadata.enrichment_stages_completed
        assert "after_bad" in result.metadata.enrichment_stages_completed
        assert any("bad_stage" in f for f in result.metadata.enrichment_failures)

    @pytest.mark.asyncio
    async def test_empty_pipeline(self):
        pipeline = EnrichmentPipeline(stages=[])
        raw = _make_ingested_doc()
        result = await pipeline.process_document(raw)
        assert result.id == "test-doc-1"
        assert result.metadata.enrichment_stages_completed == []

    @pytest.mark.asyncio
    async def test_worker_loop(self):
        pipeline = EnrichmentPipeline(
            stages=[DummyStage("worker_test")], concurrency=1
        )
        await pipeline.start()
        raw = _make_ingested_doc()
        await pipeline.submit(raw)

        # Wait for processing
        result = await asyncio.wait_for(pipeline.get_result(), timeout=5.0)
        assert result.id == "test-doc-1"
        assert "worker_test" in result.metadata.enrichment_stages_completed
        await pipeline.stop()

    def test_add_stage(self):
        pipeline = EnrichmentPipeline()
        pipeline.add_stage(DummyStage("x"))
        assert pipeline.stage_names == ["x"]

    @pytest.mark.asyncio
    async def test_output_schema(self):
        pipeline = EnrichmentPipeline(stages=[])
        raw = _make_ingested_doc()
        result = await pipeline.process_document(raw)

        # Verify the output schema matches spec
        assert hasattr(result, "id")
        assert hasattr(result, "source")
        assert hasattr(result, "content")
        assert hasattr(result, "entities")
        assert hasattr(result, "relationships")
        assert hasattr(result, "metadata")
        assert hasattr(result.source, "feed_url")
        assert hasattr(result.source, "source_name")
        assert hasattr(result.source, "credibility_tier")
        assert hasattr(result.content, "title")
        assert hasattr(result.content, "full_text")


# ── Entity Extraction Regex Pattern Tests ────────────────────────────────


class TestEntityExtractionPatterns:
    def test_btc_address(self):
        text = "Send to 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa for payment"
        matches = list(_PATTERNS["CRYPTO_WALLET_BTC"].finditer(text))
        assert len(matches) == 1
        assert matches[0].group() == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"

    def test_btc_bech32(self):
        text = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq is a bech32 address"
        matches = list(_PATTERNS["CRYPTO_WALLET_BTC"].finditer(text))
        assert len(matches) == 1

    def test_eth_address(self):
        text = "Wallet 0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe holds funds"
        matches = list(_PATTERNS["CRYPTO_WALLET_ETH"].finditer(text))
        assert len(matches) == 1

    def test_ip_address(self):
        text = "Traffic from 192.168.1.1 and 10.0.0.255 was blocked"
        matches = list(_PATTERNS["IP_ADDRESS"].finditer(text))
        assert len(matches) == 2

    def test_ip_address_rejects_invalid(self):
        text = "The value 999.999.999.999 is not valid"
        matches = list(_PATTERNS["IP_ADDRESS"].finditer(text))
        assert len(matches) == 0

    def test_domain(self):
        text = "The domain evil-site.com hosted malware"
        matches = list(_PATTERNS["DOMAIN"].finditer(text))
        assert len(matches) >= 1
        assert any("evil-site.com" in m.group() for m in matches)

    def test_vessel_imo(self):
        text = "The vessel IMO 9321483 was flagged"
        matches = list(_PATTERNS["VESSEL_IMO"].finditer(text))
        assert len(matches) == 1

    def test_aircraft_tail(self):
        text = "Aircraft N12345 was tracked near the border"
        matches = list(_PATTERNS["AIRCRAFT_TAIL"].finditer(text))
        assert len(matches) >= 1

    def test_military_unit(self):
        text = "The 82nd Airborne deployed to the region"
        matches = list(_PATTERNS["MILITARY_UNIT"].finditer(text))
        assert len(matches) == 1

    def test_ofac_sdn(self):
        text = "Listed as SDN 12345 under OFAC sanctions"
        matches = list(_PATTERNS["OFAC_SDN"].finditer(text))
        assert len(matches) == 1

    def test_social_media_handle(self):
        text = "Follow @OSINT_research for updates"
        matches = list(_PATTERNS["SOCIAL_MEDIA_HANDLE"].finditer(text))
        assert len(matches) == 1
        assert matches[0].group() == "@OSINT_research"

    def test_case_reference(self):
        text = "See Case No. 2024-CV-12345 for details"
        matches = list(_PATTERNS["CASE_REFERENCE"].finditer(text))
        assert len(matches) == 1

    def test_sentence_context(self):
        text = "First sentence. The entity appeared here. Third sentence."
        ctx = _get_sentence_context(text, 22, 28)
        assert "entity" in ctx
        assert "First" not in ctx


# ── Source Credibility Tests ─────────────────────────────────────────────


class TestSourceCredibility:
    @pytest.mark.asyncio
    async def test_tier_from_category(self):
        stage = SourceCredibilityStage()
        doc = _make_pipeline_doc(source_category="sanctions")
        result = await stage.process(doc)
        assert result.source_credibility is not None
        assert result.source_credibility.source_credibility_tier == 1

    @pytest.mark.asyncio
    async def test_tier_from_source_override(self):
        stage = SourceCredibilityStage()
        doc = _make_pipeline_doc(
            source_name="Reuters Top News", source_category="news"
        )
        result = await stage.process(doc)
        assert result.source_credibility is not None
        # Reuters overridden to tier 1 despite news category being tier 2
        assert result.source_credibility.source_credibility_tier == 1

    @pytest.mark.asyncio
    async def test_unknown_category_defaults_to_tier4(self):
        stage = SourceCredibilityStage()
        doc = _make_pipeline_doc(source_category="unknown_type")
        result = await stage.process(doc)
        assert result.source_credibility is not None
        assert result.source_credibility.source_credibility_tier == 4

    @pytest.mark.asyncio
    async def test_credibility_propagates_to_output(self):
        pipeline = EnrichmentPipeline(stages=[SourceCredibilityStage()])
        raw = _make_ingested_doc(source_category="government")
        result = await pipeline.process_document(raw)
        assert result.source.credibility_tier == 1


# ── Entity Resolution Index Tests ────────────────────────────────────────


class TestEntityIndex:
    def test_register_and_exact_lookup(self):
        idx = EntityIndex()
        ent = idx.register("Lockheed Martin", "ORG", "doc1")
        assert ent.canonical_name == "Lockheed Martin"

        found = idx.lookup_exact("Lockheed Martin")
        assert found is not None
        assert found.canonical_id == ent.canonical_id

    def test_case_insensitive_exact(self):
        idx = EntityIndex()
        idx.register("Lockheed Martin", "ORG", "doc1")
        found = idx.lookup_exact("lockheed martin")
        assert found is not None

    def test_alias_lookup(self):
        idx = EntityIndex()
        ent = idx.register("Mohammed bin Salman", "PERSON", "doc1")
        idx.update(ent.canonical_id, new_alias="MBS")

        found = idx.lookup_alias("MBS")
        assert found is not None
        assert found.canonical_id == ent.canonical_id

    def test_fuzzy_lookup_same_type(self):
        idx = EntityIndex()
        idx.register("Lockheed Martin Corporation", "ORG", "doc1")

        found, score = idx.lookup_fuzzy("Lockheed Martin Corporation Ltd", "ORG")
        assert found is not None
        assert score >= 0.88

    def test_fuzzy_no_cross_type(self):
        idx = EntityIndex()
        idx.register("Lockheed Martin", "ORG", "doc1")

        # Searching as PERSON should not match an ORG
        found, score = idx.lookup_fuzzy("Lockheed Martin", "PERSON")
        assert found is None

    def test_update_tracks_documents(self):
        idx = EntityIndex()
        ent = idx.register("CIA", "ORG", "doc1")
        idx.update(ent.canonical_id, doc_id="doc2")
        updated = idx.get(ent.canonical_id)
        assert updated is not None
        assert "doc2" in updated.source_documents

    def test_credibility_floor(self):
        idx = EntityIndex()
        ent = idx.register("OFAC", "ORG", "doc1", credibility_tier=3)
        assert ent.credibility_floor == 3

        idx.update(ent.canonical_id, credibility_tier=1)
        updated = idx.get(ent.canonical_id)
        assert updated is not None
        assert updated.credibility_floor == 1  # takes the minimum

    def test_index_length(self):
        idx = EntityIndex()
        assert len(idx) == 0
        idx.register("A", "ORG", "d1")
        idx.register("B", "ORG", "d1")
        assert len(idx) == 2


# ── Budget Tracker Tests ─────────────────────────────────────────────────


class TestBudgetTracker:
    def test_initial_budget_available(self):
        bt = BudgetTracker(hourly_cap_usd=1.0, daily_cap_usd=10.0)
        assert bt.budget_available

    def test_hourly_cap(self):
        bt = BudgetTracker(hourly_cap_usd=1.0, daily_cap_usd=10.0)
        bt.record_spend(0.5)
        assert bt.budget_available
        bt.record_spend(0.6)
        assert not bt.budget_available

    def test_daily_cap(self):
        bt = BudgetTracker(hourly_cap_usd=100.0, daily_cap_usd=1.0)
        bt.record_spend(0.5)
        assert bt.budget_available
        bt.record_spend(0.6)
        assert not bt.budget_available

    def test_remaining(self):
        bt = BudgetTracker(hourly_cap_usd=5.0, daily_cap_usd=50.0)
        bt.record_spend(2.0)
        assert bt.hourly_remaining == 3.0
        assert bt.daily_remaining == 48.0


# ── Enrichment Stage: Entity Resolution ──────────────────────────────────


class TestEntityResolutionStage:
    @pytest.mark.asyncio
    async def test_registers_new_entities(self):
        from periphery.enrichment.stages.entity_resolution import (
            EntityResolutionStage,
        )

        stage = EntityResolutionStage()
        doc = _make_pipeline_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="Lockheed Martin",
                entity_type="ORG",
                start_char=0,
                end_char=15,
                confidence=0.95,
                extraction_method="spacy",
                context_window="Lockheed Martin won the contract.",
            ),
        ]
        result = await stage.process(doc)
        assert len(result.resolved_entity_map) == 1
        assert len(stage.entity_index) == 1

    @pytest.mark.asyncio
    async def test_resolves_existing_entities(self):
        from periphery.enrichment.stages.entity_resolution import (
            EntityResolutionStage,
        )

        idx = EntityIndex()
        ent = idx.register("Lockheed Martin", "ORG", "old-doc")
        stage = EntityResolutionStage(entity_index=idx)

        doc = _make_pipeline_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="Lockheed Martin",
                entity_type="ORG",
                start_char=0,
                end_char=15,
                confidence=0.95,
                extraction_method="spacy",
                context_window="Lockheed Martin won the contract.",
            ),
        ]
        result = await stage.process(doc)
        resolved_id = result.resolved_entity_map["Lockheed Martin:ORG"]
        assert resolved_id == ent.canonical_id


# ── Pydantic Model Tests ────────────────────────────────────────────────


class TestModels:
    def test_extracted_entity(self):
        e = ExtractedEntity(
            text="Washington",
            entity_type="GPE",
            start_char=0,
            end_char=10,
            confidence=0.9,
            extraction_method="spacy",
            context_window="Washington D.C. hosted the summit.",
        )
        assert e.entity_type == "GPE"

    def test_temporal_context_defaults(self):
        tc = TemporalContext(status="unresolved")
        assert tc.explicit_date is None
        assert tc.tense_confidence == 0.0

    def test_geospatial_data(self):
        g = GeospatialData(
            latitude=38.9072,
            longitude=-77.0369,
            resolution_confidence=0.95,
            geo_source="nominatim",
        )
        assert g.latitude == 38.9072

    def test_enriched_document_serialization(self):
        doc = EnrichedDocument(
            id="test",
            source={"feed_url": "x", "source_name": "y", "source_category": "z"},
            content={"title": "t", "full_text": "f", "url": "u"},
        )
        data = doc.model_dump()
        assert data["id"] == "test"
        assert "entities" in data
        assert "relationships" in data

    def test_pipeline_document(self):
        pd = _make_pipeline_doc()
        assert pd.enrichment_stages_completed == []
        assert pd.enrichment_failures == []

    def test_canonical_entity(self):
        ce = CanonicalEntity(
            canonical_id="abc",
            canonical_name="Test Corp",
            entity_type="ORG",
        )
        assert ce.aliases == []
        assert ce.credibility_floor == 4
        assert ce.merge_confidence == 1.0


# ── Geospatial Cache Tests ──────────────────────────────────────────────


class TestGeocodingCache:
    def test_put_and_get(self):
        from periphery.enrichment.stages.geospatial_resolution import GeocodingCache

        cache = GeocodingCache()
        data = GeospatialData(
            latitude=38.9,
            longitude=-77.0,
            resolution_confidence=0.9,
            geo_source="test",
        )
        cache.put("Washington D.C.", data)
        assert cache.get("Washington D.C.") is not None
        assert cache.get("washington d.c.") is not None  # case insensitive
        assert cache.get("New York") is None

    def test_cache_length(self):
        from periphery.enrichment.stages.geospatial_resolution import GeocodingCache

        cache = GeocodingCache()
        assert len(cache) == 0
        cache.put("A", GeospatialData(geo_source="test"))
        assert len(cache) == 1


# ── Integration: Full Pipeline Without SpaCy ─────────────────────────────


class MockEntityExtractionStage(EnrichmentStage):
    """Mock entity extraction for integration tests (no SpaCy needed)."""

    @property
    def name(self) -> str:
        return "entity_extraction"

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        doc.extracted_entities = [
            ExtractedEntity(
                text="Company X",
                entity_type="ORG",
                start_char=0,
                end_char=9,
                confidence=0.95,
                extraction_method="spacy",
                context_window="Company X acquired Firm Y.",
            ),
            ExtractedEntity(
                text="Firm Y",
                entity_type="ORG",
                start_char=19,
                end_char=25,
                confidence=0.90,
                extraction_method="spacy",
                context_window="Company X acquired Firm Y.",
            ),
            ExtractedEntity(
                text="Washington D.C.",
                entity_type="GPE",
                start_char=45,
                end_char=60,
                confidence=0.92,
                extraction_method="spacy",
                context_window="headquartered in Washington D.C.",
            ),
        ]
        return doc


class MockRelationshipStage(EnrichmentStage):
    """Mock relationship extraction for integration tests."""

    @property
    def name(self) -> str:
        return "relationship_extraction"

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        doc.extracted_relationships = [
            ExtractedRelationship(
                subject_text="Company X",
                subject_type="ORG",
                predicate="acquired",
                object_text="Firm Y",
                object_type="ORG",
                confidence=0.85,
                extraction_tier=2,
                extraction_method="dependency_parse",
                evidence="Company X acquired Firm Y.",
            ),
        ]
        return doc


class TestFullPipelineIntegration:
    @pytest.mark.asyncio
    async def test_end_to_end_enrichment(self):
        from periphery.enrichment.stages.entity_resolution import (
            EntityResolutionStage,
        )

        pipeline = EnrichmentPipeline(
            stages=[
                MockEntityExtractionStage(),
                MockRelationshipStage(),
                SourceCredibilityStage(),
                EntityResolutionStage(),
            ]
        )

        raw = _make_ingested_doc(
            content="Company X acquired Firm Y for $5B in Washington D.C.",
            source_category="news",
        )
        result = await pipeline.process_document(raw)

        # All stages completed
        assert len(result.metadata.enrichment_stages_completed) == 4
        assert len(result.metadata.enrichment_failures) == 0

        # Entities enriched
        assert len(result.entities) == 3
        assert all(e.canonical_id for e in result.entities)
        assert all(e.credibility_tier == 2 for e in result.entities)  # news = tier 2

        # Relationships enriched
        assert len(result.relationships) == 1
        assert result.relationships[0].predicate == "acquired"
        assert result.relationships[0].credibility_tier == 2

        # Source credibility propagated
        assert result.source.credibility_tier == 2

        # Content preserved
        assert result.content.title == "Test Article"
        assert "Company X" in result.content.full_text

    @pytest.mark.asyncio
    async def test_partial_failure_still_produces_output(self):
        pipeline = EnrichmentPipeline(
            stages=[
                MockEntityExtractionStage(),
                DummyStage("failing_stage", should_fail=True),
                SourceCredibilityStage(),
            ]
        )
        raw = _make_ingested_doc()
        result = await pipeline.process_document(raw)

        assert "entity_extraction" in result.metadata.enrichment_stages_completed
        assert "source_credibility" in result.metadata.enrichment_stages_completed
        assert any("failing_stage" in f for f in result.metadata.enrichment_failures)
        assert len(result.entities) == 3  # entities still extracted


# ── Relationship Extraction Tests ────────────────────────────────────────


from periphery.enrichment.stages.relationship_extraction import (
    RelationshipExtractionStage,
    assign_extraction_tiers,
    _deduplicate_relationships,
    WEIGHT_SAME_SENTENCE,
    WEIGHT_SAME_PARAGRAPH,
    WEIGHT_SAME_DOCUMENT,
)


def _make_entities_for_rel_test():
    """Create entities positioned in a known text layout."""
    return [
        ExtractedEntity(
            text="Company X",
            entity_type="ORG",
            start_char=0,
            end_char=9,
            confidence=0.95,
            extraction_method="spacy",
            context_window="Company X acquired Firm Y for $5 billion.",
        ),
        ExtractedEntity(
            text="Firm Y",
            entity_type="ORG",
            start_char=19,
            end_char=25,
            confidence=0.90,
            extraction_method="spacy",
            context_window="Company X acquired Firm Y for $5 billion.",
        ),
        ExtractedEntity(
            text="Washington D.C.",
            entity_type="GPE",
            start_char=60,
            end_char=75,
            confidence=0.92,
            extraction_method="spacy",
            context_window="The deal was signed in Washington D.C.",
        ),
    ]


class TestTierAssignment:
    def test_low_priority_gets_tier1_only(self):
        doc = _make_pipeline_doc(priority=5)
        tiers = assign_extraction_tiers(doc, budget_available=True)
        assert tiers == [1]

    def test_medium_priority_gets_tier1_and_tier2(self):
        doc = _make_pipeline_doc(priority=3)
        tiers = assign_extraction_tiers(doc, budget_available=True)
        assert 1 in tiers
        assert 2 in tiers
        assert 3 not in tiers

    def test_high_priority_gets_all_tiers(self):
        doc = _make_pipeline_doc(priority=1)
        tiers = assign_extraction_tiers(doc, budget_available=True)
        assert tiers == [1, 2, 3]

    def test_high_priority_no_budget_skips_tier3(self):
        doc = _make_pipeline_doc(priority=1)
        tiers = assign_extraction_tiers(doc, budget_available=False)
        assert 1 in tiers
        assert 2 in tiers
        assert 3 not in tiers

    def test_crystallizer_flag_forces_all_tiers(self):
        doc = _make_pipeline_doc(priority=5, crystallizer_priority_flag=True)
        tiers = assign_extraction_tiers(doc, budget_available=True)
        assert tiers == [1, 2, 3]

    def test_crystallizer_flag_no_budget(self):
        doc = _make_pipeline_doc(priority=5, crystallizer_priority_flag=True)
        tiers = assign_extraction_tiers(doc, budget_available=False)
        assert tiers == [1, 2]


class TestTier1Cooccurrence:
    @pytest.mark.asyncio
    async def test_cooccurrence_basic(self):
        """Two entities in the same document produce co-occurrence edges."""
        stage = RelationshipExtractionStage()
        doc = _make_pipeline_doc(
            full_text="Company X acquired Firm Y for $5 billion.",
            priority=5,  # only Tier 1
        )
        doc.extracted_entities = _make_entities_for_rel_test()[:2]

        result = await stage.process(doc)
        assert len(result.extracted_relationships) >= 1
        rel = result.extracted_relationships[0]
        assert rel.predicate == "co_occurs_with"
        assert rel.extraction_tier == 1
        assert rel.extraction_method == "co_occurrence"
        assert rel.co_occurrence_weight is not None

    @pytest.mark.asyncio
    async def test_same_sentence_weight(self):
        """Entities in the same sentence get weight 1.0."""
        stage = RelationshipExtractionStage()
        doc = _make_pipeline_doc(
            full_text="Company X acquired Firm Y for $5 billion.",
            priority=5,
        )
        # Both entities are in the same sentence (offsets within 0-41)
        doc.extracted_entities = [
            ExtractedEntity(
                text="Company X", entity_type="ORG",
                start_char=0, end_char=9,
                confidence=0.95, extraction_method="spacy",
                context_window="Company X acquired Firm Y.",
            ),
            ExtractedEntity(
                text="Firm Y", entity_type="ORG",
                start_char=19, end_char=25,
                confidence=0.90, extraction_method="spacy",
                context_window="Company X acquired Firm Y.",
            ),
        ]

        result = await stage.process(doc)
        rels = [r for r in result.extracted_relationships if r.co_occurrence_weight == WEIGHT_SAME_SENTENCE]
        assert len(rels) >= 1

    @pytest.mark.asyncio
    async def test_document_level_weight(self):
        """Entities in different paragraphs get weight 0.2."""
        stage = RelationshipExtractionStage()
        text = "Company X is a major corporation.\n\nFirm Y operates in Europe."
        doc = _make_pipeline_doc(full_text=text, priority=5)
        doc.extracted_entities = [
            ExtractedEntity(
                text="Company X", entity_type="ORG",
                start_char=0, end_char=9,
                confidence=0.95, extraction_method="spacy",
                context_window="Company X is a major corporation.",
            ),
            ExtractedEntity(
                text="Firm Y", entity_type="ORG",
                start_char=34, end_char=40,
                confidence=0.90, extraction_method="spacy",
                context_window="Firm Y operates in Europe.",
            ),
        ]

        result = await stage.process(doc)
        # Should have at least one relationship
        assert len(result.extracted_relationships) >= 1

    @pytest.mark.asyncio
    async def test_no_entities_skips(self):
        """No entities means no relationships."""
        stage = RelationshipExtractionStage()
        doc = _make_pipeline_doc()
        doc.extracted_entities = []

        result = await stage.process(doc)
        assert result.extracted_relationships == []

    @pytest.mark.asyncio
    async def test_single_entity_no_cooccurrence(self):
        """A single entity can't co-occur with anything."""
        stage = RelationshipExtractionStage()
        doc = _make_pipeline_doc(priority=5)
        doc.extracted_entities = [_make_entities_for_rel_test()[0]]

        result = await stage.process(doc)
        assert result.extracted_relationships == []


class TestRelationshipDeduplication:
    def test_dedup_same_relationship(self):
        """Duplicate relationships are merged, keeping highest confidence and tier."""
        rels = [
            ExtractedRelationship(
                subject_text="Company X", subject_type="ORG",
                predicate="acquired", object_text="Firm Y", object_type="ORG",
                confidence=0.7, extraction_tier=2,
                extraction_method="dependency_parse",
                evidence="Company X acquired Firm Y.",
            ),
            ExtractedRelationship(
                subject_text="Company X", subject_type="ORG",
                predicate="acquired", object_text="Firm Y", object_type="ORG",
                confidence=0.9, extraction_tier=3,
                extraction_method="llm",
                evidence="Company X acquired Firm Y for $5B.",
                temporal_qualifier="historical",
                implicit=False,
            ),
        ]

        deduped = _deduplicate_relationships(rels)
        assert len(deduped) == 1
        assert deduped[0].confidence == 0.9
        assert deduped[0].extraction_tier == 3
        assert deduped[0].temporal_qualifier == "historical"

    def test_dedup_different_relationships(self):
        """Different relationships are not merged."""
        rels = [
            ExtractedRelationship(
                subject_text="Company X", subject_type="ORG",
                predicate="acquired", object_text="Firm Y", object_type="ORG",
                confidence=0.7, extraction_tier=2,
                extraction_method="dependency_parse",
            ),
            ExtractedRelationship(
                subject_text="Company X", subject_type="ORG",
                predicate="funded", object_text="Firm Y", object_type="ORG",
                confidence=0.8, extraction_tier=3,
                extraction_method="llm",
            ),
        ]
        deduped = _deduplicate_relationships(rels)
        assert len(deduped) == 2

    def test_dedup_empty_list(self):
        assert _deduplicate_relationships([]) == []

    def test_dedup_case_insensitive(self):
        """Dedup normalizes case."""
        rels = [
            ExtractedRelationship(
                subject_text="company x", subject_type="ORG",
                predicate="acquired", object_text="firm y", object_type="ORG",
                confidence=0.7, extraction_tier=1,
                extraction_method="co_occurrence",
            ),
            ExtractedRelationship(
                subject_text="Company X", subject_type="ORG",
                predicate="Acquired", object_text="Firm Y", object_type="ORG",
                confidence=0.9, extraction_tier=2,
                extraction_method="dependency_parse",
            ),
        ]
        deduped = _deduplicate_relationships(rels)
        assert len(deduped) == 1
        assert deduped[0].confidence == 0.9


class TestTier3LLMJsonParsing:
    def test_parse_clean_json(self):
        stage = RelationshipExtractionStage()
        result = stage._parse_llm_json('[{"subject": {"name": "X"}}]')
        assert result is not None
        assert len(result) == 1

    def test_parse_markdown_fenced_json(self):
        stage = RelationshipExtractionStage()
        content = '```json\n[{"subject": {"name": "X"}}]\n```'
        result = stage._parse_llm_json(content)
        assert result is not None
        assert len(result) == 1

    def test_parse_invalid_json(self):
        stage = RelationshipExtractionStage()
        result = stage._parse_llm_json("not valid json at all")
        assert result is None

    def test_parse_json_object_not_array(self):
        stage = RelationshipExtractionStage()
        result = stage._parse_llm_json('{"key": "value"}')
        assert result is None


class TestTier3LLMExtraction:
    @pytest.mark.asyncio
    async def test_llm_extraction_success(self):
        """Tier 3 LLM extraction with mocked Anthropic client."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps([
            {
                "subject": {"name": "Company X", "type": "ORG"},
                "predicate": "acquired",
                "object": {"name": "Firm Y", "type": "ORG"},
                "confidence": 0.95,
                "temporal_qualifier": "historical",
                "evidence": "Company X acquired Firm Y.",
                "implicit": False,
            }
        ]))]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        budget = BudgetTracker(hourly_cap_usd=10.0, daily_cap_usd=100.0)
        stage = RelationshipExtractionStage(
            budget_tracker=budget,
            anthropic_client=mock_client,
            tier3_min_priority=5,  # allow all priorities
        )

        doc = _make_pipeline_doc(priority=1)
        doc.extracted_entities = _make_entities_for_rel_test()

        result = await stage.process(doc)

        # Should have Tier 1 (co-occurrence) + Tier 3 (LLM) results
        tier3_rels = [r for r in result.extracted_relationships if r.extraction_tier == 3]
        assert len(tier3_rels) >= 1
        assert tier3_rels[0].predicate == "acquired"
        assert tier3_rels[0].extraction_method == "llm"
        assert tier3_rels[0].temporal_qualifier == "historical"
        assert result.llm_enrichment_status == "complete"

    @pytest.mark.asyncio
    async def test_llm_budget_exhausted(self):
        """When budget is exhausted, Tier 3 is skipped."""
        budget = BudgetTracker(hourly_cap_usd=0.01, daily_cap_usd=0.01)
        budget.record_spend(0.02)  # exhaust the budget

        mock_client = MagicMock()
        stage = RelationshipExtractionStage(
            budget_tracker=budget,
            anthropic_client=mock_client,
            tier3_min_priority=5,
        )

        doc = _make_pipeline_doc(priority=1, crystallizer_priority_flag=True)
        doc.extracted_entities = _make_entities_for_rel_test()

        result = await stage.process(doc)

        # LLM should be skipped due to budget
        assert result.llm_enrichment_status == "budget_exhausted"
        tier3_rels = [r for r in result.extracted_relationships if r.extraction_tier == 3]
        assert len(tier3_rels) == 0

    @pytest.mark.asyncio
    async def test_llm_no_client(self):
        """Without an Anthropic client, Tier 3 is skipped."""
        stage = RelationshipExtractionStage(
            anthropic_client=None,
            tier3_min_priority=5,
        )

        doc = _make_pipeline_doc(priority=1)
        doc.extracted_entities = _make_entities_for_rel_test()

        result = await stage.process(doc)
        assert result.llm_enrichment_status == "skipped"


class TestRelationshipExtractionOutput:
    @pytest.mark.asyncio
    async def test_output_schema_fields(self):
        """All output schema fields are present."""
        stage = RelationshipExtractionStage()
        doc = _make_pipeline_doc(priority=5)
        doc.extracted_entities = _make_entities_for_rel_test()[:2]

        result = await stage.process(doc)
        assert len(result.extracted_relationships) >= 1
        rel = result.extracted_relationships[0]

        # Verify all spec fields exist
        assert hasattr(rel, "subject_text")
        assert hasattr(rel, "subject_type")
        assert hasattr(rel, "subject_canonical_id")
        assert hasattr(rel, "predicate")
        assert hasattr(rel, "object_text")
        assert hasattr(rel, "object_type")
        assert hasattr(rel, "object_canonical_id")
        assert hasattr(rel, "confidence")
        assert hasattr(rel, "extraction_tier")
        assert hasattr(rel, "extraction_method")
        assert hasattr(rel, "temporal_qualifier")
        assert hasattr(rel, "evidence")
        assert hasattr(rel, "implicit")
        assert hasattr(rel, "co_occurrence_weight")

    @pytest.mark.asyncio
    async def test_metadata_tracks_relationship_counts(self):
        """Enrichment metadata includes relationship tier counts."""
        pipeline = EnrichmentPipeline(
            stages=[MockEntityExtractionStage(), MockRelationshipStage()]
        )
        raw = _make_ingested_doc()
        result = await pipeline.process_document(raw)

        assert "tier_2" in result.metadata.relationship_counts
        assert result.metadata.relationship_counts["tier_2"] == 1

    @pytest.mark.asyncio
    async def test_llm_enrichment_status_in_metadata(self):
        """llm_enrichment_status is tracked in metadata."""
        pipeline = EnrichmentPipeline(stages=[])
        raw = _make_ingested_doc()
        result = await pipeline.process_document(raw)

        assert hasattr(result.metadata, "llm_enrichment_status")
        assert result.metadata.llm_enrichment_status == "skipped"


class TestPipelineDocumentNewFields:
    def test_crystallizer_priority_flag_default(self):
        doc = _make_pipeline_doc()
        assert doc.crystallizer_priority_flag is False

    def test_llm_enrichment_status_default(self):
        doc = _make_pipeline_doc()
        assert doc.llm_enrichment_status == "skipped"

    def test_spacy_doc_default_none(self):
        doc = _make_pipeline_doc()
        assert doc.spacy_doc is None

    def test_crystallizer_flag_settable(self):
        doc = _make_pipeline_doc(crystallizer_priority_flag=True)
        assert doc.crystallizer_priority_flag is True


class TestExtractedRelationshipModel:
    def test_all_fields(self):
        rel = ExtractedRelationship(
            subject_text="Company X",
            subject_type="ORG",
            subject_canonical_id="canon-1",
            predicate="acquired",
            object_text="Firm Y",
            object_type="ORG",
            object_canonical_id="canon-2",
            confidence=0.9,
            extraction_tier=3,
            extraction_method="llm",
            temporal_qualifier="historical",
            evidence="Company X acquired Firm Y.",
            implicit=False,
            co_occurrence_weight=None,
        )
        assert rel.subject_canonical_id == "canon-1"
        assert rel.extraction_method == "llm"
        assert rel.implicit is False
        assert rel.co_occurrence_weight is None

    def test_defaults(self):
        rel = ExtractedRelationship(
            subject_text="A", subject_type="ORG",
            predicate="co_occurs_with",
            object_text="B", object_type="ORG",
            confidence=1.0, extraction_tier=1,
        )
        assert rel.subject_canonical_id is None
        assert rel.object_canonical_id is None
        assert rel.extraction_method == ""
        assert rel.temporal_qualifier == ""
        assert rel.implicit is False
        assert rel.co_occurrence_weight is None

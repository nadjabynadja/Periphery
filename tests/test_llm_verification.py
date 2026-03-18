"""Tests for the LLM verification stage.

Tests cover:
  - EntityVerifier: filters junk, fixes types, deduplicates via merge_with
  - LocationVerifier: clears un-geocodable entities, corrects bad coordinates
  - RelationshipVerifier: prunes noise, enriches predicates
  - Budget limiting: stage skips when budget exhausted
  - Graceful failure: all components handle API errors without crashing
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from periphery.enrichment.budget import BudgetTracker
from periphery.enrichment.models import (
    ExtractedEntity,
    ExtractedRelationship,
    GeospatialData,
    PipelineDocument,
)
from periphery.enrichment.stages.llm_verification import (
    EntityVerifier,
    ExaEnricher,
    LLMVerificationStage,
    LocationVerifier,
    RelationshipVerifier,
    VerificationStats,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_doc(**kwargs) -> PipelineDocument:
    """Create a minimal PipelineDocument for testing."""
    defaults = dict(
        id="test-doc-001",
        source_feed="https://example.com/feed",
        source_name="Test Source",
        source_category="news",
        title="NATO and Ukraine Sign Defense Agreement",
        url="https://example.com/article/1",
        full_text=(
            "NATO and Ukraine signed a landmark defense agreement on Monday. "
            "Vladimir Putin condemned the move. The US and EU voiced support. "
            "AI tools were used to analyze the implications."
        ),
        priority=2,
    )
    defaults.update(kwargs)
    return PipelineDocument(**defaults)


def _make_entities() -> list[ExtractedEntity]:
    return [
        ExtractedEntity(
            text="Monday",
            entity_type="DATE",
            start_char=0,
            end_char=6,
            confidence=0.9,
            extraction_method="spacy",
            context_window="...on Monday...",
        ),
        ExtractedEntity(
            text="NATO",
            entity_type="ORG",
            start_char=10,
            end_char=14,
            confidence=0.95,
            extraction_method="spacy",
            context_window="NATO and Ukraine",
        ),
        ExtractedEntity(
            text="Ukraine",
            entity_type="GPE",
            start_char=19,
            end_char=26,
            confidence=0.95,
            extraction_method="spacy",
            context_window="NATO and Ukraine",
        ),
        ExtractedEntity(
            text="US",
            entity_type="GPE",
            start_char=80,
            end_char=82,
            confidence=0.9,
            extraction_method="spacy",
            context_window="The US and EU",
        ),
        ExtractedEntity(
            text="U.S.",
            entity_type="GPE",
            start_char=84,
            end_char=88,
            confidence=0.9,
            extraction_method="spacy",
            context_window="U.S. officials",
        ),
        ExtractedEntity(
            text="AI",
            entity_type="GPE",  # wrong type
            start_char=100,
            end_char=102,
            confidence=0.7,
            extraction_method="spacy",
            context_window="AI tools were used",
        ),
    ]


def _make_relationships() -> list[ExtractedRelationship]:
    return [
        ExtractedRelationship(
            subject_text="NATO",
            subject_type="ORG",
            predicate="co_occurs_with",
            object_text="Ukraine",
            object_type="GPE",
            confidence=1.0,
            extraction_tier=1,
            extraction_method="co_occurrence",
        ),
        ExtractedRelationship(
            subject_text="Monday",
            subject_type="DATE",
            predicate="co_occurs_with",
            object_text="NATO",
            object_type="ORG",
            confidence=0.5,
            extraction_tier=1,
            extraction_method="co_occurrence",
        ),
        ExtractedRelationship(
            subject_text="Vladimir Putin",
            subject_type="PERSON",
            predicate="condemn",
            object_text="NATO",
            object_type="ORG",
            confidence=0.9,
            extraction_tier=2,
            extraction_method="dependency_parse",
        ),
    ]


def _make_anthropic_response(content: str) -> MagicMock:
    """Create a mock Anthropic API response."""
    response = MagicMock()
    response.content = [MagicMock(text=content)]
    response.usage = MagicMock(input_tokens=500, output_tokens=300)
    return response


def _make_budget(exhausted: bool = False) -> BudgetTracker:
    tracker = BudgetTracker(hourly_cap_usd=5.0, daily_cap_usd=50.0)
    if exhausted:
        tracker._hourly_spend = 10.0  # over cap
    return tracker


# ─── EntityVerifier tests ────────────────────────────────────────────────────


class TestEntityVerifier:
    def test_filters_junk_entities(self):
        """Entities marked is_valid=false should be removed."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"text": "Monday", "entity_type": "DATE", "canonical_name": "Monday",
             "confidence": 0.1, "is_valid": False, "merge_with": None},
            {"text": "NATO", "entity_type": "ORG", "canonical_name": "NATO",
             "confidence": 0.95, "is_valid": True, "merge_with": None},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="Monday", entity_type="DATE", start_char=0, end_char=6,
                confidence=0.9, extraction_method="spacy", context_window="",
            ),
            ExtractedEntity(
                text="NATO", entity_type="ORG", start_char=10, end_char=14,
                confidence=0.95, extraction_method="spacy", context_window="",
            ),
        ]

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        assert len(result.extracted_entities) == 1
        assert result.extracted_entities[0].text == "NATO"
        assert stats.entities_filtered == 1

    def test_fixes_misclassified_entity_type(self):
        """Entity type should be corrected per LLM output."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"text": "AI", "entity_type": "PRODUCT", "canonical_name": "AI",
             "confidence": 0.75, "is_valid": True, "merge_with": None},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="AI", entity_type="GPE", start_char=0, end_char=2,
                confidence=0.7, extraction_method="spacy", context_window="",
            ),
        ]

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        assert len(result.extracted_entities) == 1
        assert result.extracted_entities[0].entity_type == "PRODUCT"
        assert stats.entities_reclassified == 1

    def test_merges_duplicate_entities(self):
        """Entities with merge_with set should be removed from entity list."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"text": "US", "entity_type": "GPE", "canonical_name": "United States",
             "confidence": 0.95, "is_valid": True, "merge_with": None},
            {"text": "U.S.", "entity_type": "GPE", "canonical_name": "United States",
             "confidence": 0.95, "is_valid": True, "merge_with": "United States"},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="US", entity_type="GPE", start_char=0, end_char=2,
                confidence=0.9, extraction_method="spacy", context_window="",
            ),
            ExtractedEntity(
                text="U.S.", entity_type="GPE", start_char=5, end_char=9,
                confidence=0.9, extraction_method="spacy", context_window="",
            ),
        ]

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        # U.S. should be merged into United States (removed from entity list)
        assert len(result.extracted_entities) == 1
        assert result.extracted_entities[0].text == "United States"
        assert stats.entities_merged == 1

    def test_updates_confidence_scores(self):
        """Confidence scores from LLM should be applied."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"text": "NATO", "entity_type": "ORG", "canonical_name": "NATO",
             "confidence": 0.99, "is_valid": True, "merge_with": None},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="NATO", entity_type="ORG", start_char=0, end_char=4,
                confidence=0.85, extraction_method="spacy", context_window="",
            ),
        ]

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        assert result.extracted_entities[0].confidence == pytest.approx(0.99)

    def test_skips_when_no_entities(self):
        """Should return doc unchanged if no entities."""
        mock_client = MagicMock()
        doc = _make_doc()
        doc.extracted_entities = []

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        mock_client.messages.create.assert_not_called()
        assert result.extracted_entities == []


# ─── LocationVerifier tests ──────────────────────────────────────────────────


class TestLocationVerifier:
    def test_clears_person_geocoding(self):
        """PERSON entities should have their geocoding cleared."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"entity_text": "Vladimir Putin", "should_geocode": False,
             "coordinates_correct": None, "suggested_lat": None,
             "suggested_lon": None, "reason": "PERSON entities should not be geocoded"},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        # Clear correction cache between tests
        LocationVerifier._correction_cache = {}

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="Vladimir Putin", entity_type="PERSON", start_char=0, end_char=14,
                confidence=0.99, extraction_method="spacy", context_window="",
            ),
        ]
        doc.geospatial_data["Vladimir Putin:PERSON"] = GeospatialData(
            resolved=True, latitude=55.75, longitude=37.61,
            display_name="Moscow, Russia",
        )

        import asyncio
        budget = _make_budget()
        verifier = LocationVerifier(mock_client, budget, batch_size=30)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        geo = result.geospatial_data.get("Vladimir Putin:PERSON")
        assert geo is not None
        assert not geo.resolved
        assert stats.locations_cleared == 1

    def test_corrects_wrong_coordinates(self):
        """Wrong coordinates should be replaced with suggested values."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"entity_text": "United States", "should_geocode": True,
             "coordinates_correct": False, "suggested_lat": 39.5,
             "suggested_lon": -98.35,
             "reason": "Coordinates point to Guadeloupe; US centroid is ~39.5N, 98.35W"},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        LocationVerifier._correction_cache = {}

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="United States", entity_type="GPE", start_char=0, end_char=13,
                confidence=0.99, extraction_method="spacy", context_window="",
            ),
        ]
        doc.geospatial_data["United States:GPE"] = GeospatialData(
            resolved=True, latitude=16.2, longitude=-61.5,
            display_name="Guadeloupe",
        )

        import asyncio
        budget = _make_budget()
        verifier = LocationVerifier(mock_client, budget, batch_size=30)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        geo = result.geospatial_data.get("United States:GPE")
        assert geo is not None
        assert geo.latitude == pytest.approx(39.5)
        assert geo.longitude == pytest.approx(-98.35)
        assert geo.geocoding_source == "llm_verified"
        assert stats.locations_corrected == 1

    def test_skips_correct_coordinates(self):
        """Correct coordinates should not be modified."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"entity_text": "Ukraine", "should_geocode": True,
             "coordinates_correct": True, "suggested_lat": None,
             "suggested_lon": None, "reason": "Coordinates are correct"},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        LocationVerifier._correction_cache = {}

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="Ukraine", entity_type="GPE", start_char=0, end_char=7,
                confidence=0.95, extraction_method="spacy", context_window="",
            ),
        ]
        doc.geospatial_data["Ukraine:GPE"] = GeospatialData(
            resolved=True, latitude=48.38, longitude=31.17,
            display_name="Ukraine",
        )

        import asyncio
        budget = _make_budget()
        verifier = LocationVerifier(mock_client, budget, batch_size=30)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        geo = result.geospatial_data.get("Ukraine:GPE")
        assert geo is not None
        assert geo.latitude == pytest.approx(48.38)
        assert stats.locations_corrected == 0
        assert stats.locations_cleared == 0

    def test_skips_when_no_geocoded_entities(self):
        """Should skip API call if no entities have geocoding."""
        mock_client = MagicMock()
        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="NATO", entity_type="ORG", start_char=0, end_char=4,
                confidence=0.95, extraction_method="spacy", context_window="",
            ),
        ]
        # No geospatial data

        import asyncio
        budget = _make_budget()
        verifier = LocationVerifier(mock_client, budget, batch_size=30)
        stats = VerificationStats()
        asyncio.run(verifier.verify(doc, stats))

        mock_client.messages.create.assert_not_called()

    def test_geocoding_correction_cache(self):
        """Second call for same entity should use cache, not make API call."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"entity_text": "Russia", "should_geocode": True,
             "coordinates_correct": False, "suggested_lat": 61.5,
             "suggested_lon": 105.3, "reason": "Correct centroid"},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        LocationVerifier._correction_cache = {}

        import asyncio
        budget = _make_budget()
        verifier = LocationVerifier(mock_client, budget, batch_size=30)

        def make_russia_doc():
            doc = _make_doc()
            doc.extracted_entities = [
                ExtractedEntity(
                    text="Russia", entity_type="GPE", start_char=0, end_char=6,
                    confidence=0.95, extraction_method="spacy", context_window="",
                ),
            ]
            doc.geospatial_data["Russia:GPE"] = GeospatialData(
                resolved=True, latitude=10.0, longitude=10.0,
            )
            return doc

        # First call: should hit API
        stats1 = VerificationStats()
        asyncio.run(verifier.verify(make_russia_doc(), stats1))
        assert mock_client.messages.create.call_count == 1

        # Second call: should use cache
        stats2 = VerificationStats()
        asyncio.run(verifier.verify(make_russia_doc(), stats2))
        assert mock_client.messages.create.call_count == 1  # still 1, not 2


# ─── RelationshipVerifier tests ──────────────────────────────────────────────


class TestRelationshipVerifier:
    def test_prunes_meaningless_relationships(self):
        """Relationships marked is_meaningful=false should be removed."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"subject": "Monday", "object": "NATO", "is_meaningful": False,
             "predicate": "co_occurs_with", "confidence": 0.1,
             "reason": "Day of week co-occurrence is noise"},
            {"subject": "NATO", "object": "Ukraine", "is_meaningful": True,
             "predicate": "allied_with", "confidence": 0.85,
             "reason": "Geopolitical alliance"},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_relationships = _make_relationships()[:2]  # Monday-NATO and NATO-Ukraine

        import asyncio
        budget = _make_budget()
        verifier = RelationshipVerifier(mock_client, budget, batch_size=40)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        assert len(result.extracted_relationships) == 1
        assert result.extracted_relationships[0].subject_text == "NATO"
        assert stats.relationships_pruned == 1

    def test_enriches_predicates(self):
        """co_occurs_with should be replaced with specific predicates."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"subject": "NATO", "object": "Ukraine", "is_meaningful": True,
             "predicate": "allied_with", "confidence": 0.85,
             "reason": "Alliance relationship"},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_relationships = [
            ExtractedRelationship(
                subject_text="NATO", subject_type="ORG",
                predicate="co_occurs_with",
                object_text="Ukraine", object_type="GPE",
                confidence=1.0, extraction_tier=1,
                extraction_method="co_occurrence",
            ),
        ]

        import asyncio
        budget = _make_budget()
        verifier = RelationshipVerifier(mock_client, budget, batch_size=40)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        assert len(result.extracted_relationships) == 1
        assert result.extracted_relationships[0].predicate == "allied_with"
        assert result.extracted_relationships[0].confidence == pytest.approx(0.85)
        assert stats.relationships_enriched == 1

    def test_keeps_unverified_relationships(self):
        """Relationships not in LLM response should be kept as-is."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response(json.dumps([
            {"subject": "NATO", "object": "Ukraine", "is_meaningful": True,
             "predicate": "allied_with", "confidence": 0.85, "reason": ""},
        ]))
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_relationships = [
            ExtractedRelationship(
                subject_text="NATO", subject_type="ORG",
                predicate="co_occurs_with",
                object_text="Ukraine", object_type="GPE",
                confidence=1.0, extraction_tier=1, extraction_method="co_occurrence",
            ),
            ExtractedRelationship(
                subject_text="Vladimir Putin", subject_type="PERSON",
                predicate="condemn",
                object_text="NATO", object_type="ORG",
                confidence=0.9, extraction_tier=2, extraction_method="dependency_parse",
            ),
        ]

        import asyncio
        budget = _make_budget()
        verifier = RelationshipVerifier(mock_client, budget, batch_size=40)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        # Vladimir Putin → NATO should be kept (not in LLM response)
        assert len(result.extracted_relationships) == 2

    def test_skips_when_no_relationships(self):
        """Should skip API call when no relationships exist."""
        mock_client = MagicMock()
        doc = _make_doc()
        doc.extracted_relationships = []

        import asyncio
        budget = _make_budget()
        verifier = RelationshipVerifier(mock_client, budget, batch_size=40)
        stats = VerificationStats()
        asyncio.run(verifier.verify(doc, stats))

        mock_client.messages.create.assert_not_called()


# ─── Budget limiting tests ────────────────────────────────────────────────────


class TestBudgetLimiting:
    def test_entity_verifier_skips_when_budget_exhausted(self):
        """EntityVerifier should not call API when budget is exhausted."""
        mock_client = MagicMock()
        doc = _make_doc()
        doc.extracted_entities = _make_entities()

        import asyncio
        budget = _make_budget(exhausted=True)
        assert not budget.budget_available

        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        mock_client.messages.create.assert_not_called()
        # Entities should be unchanged
        assert len(result.extracted_entities) == len(_make_entities())

    def test_location_verifier_skips_when_budget_exhausted(self):
        """LocationVerifier should not call API when budget is exhausted."""
        mock_client = MagicMock()
        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="Ukraine", entity_type="GPE", start_char=0, end_char=7,
                confidence=0.95, extraction_method="spacy", context_window="",
            ),
        ]
        doc.geospatial_data["Ukraine:GPE"] = GeospatialData(
            resolved=True, latitude=48.38, longitude=31.17,
        )

        import asyncio
        budget = _make_budget(exhausted=True)
        verifier = LocationVerifier(mock_client, budget, batch_size=30)
        stats = VerificationStats()
        asyncio.run(verifier.verify(doc, stats))

        mock_client.messages.create.assert_not_called()

    def test_relationship_verifier_skips_when_budget_exhausted(self):
        """RelationshipVerifier should not call API when budget is exhausted."""
        mock_client = MagicMock()
        doc = _make_doc()
        doc.extracted_relationships = _make_relationships()

        import asyncio
        budget = _make_budget(exhausted=True)
        verifier = RelationshipVerifier(mock_client, budget, batch_size=40)
        stats = VerificationStats()
        asyncio.run(verifier.verify(doc, stats))

        mock_client.messages.create.assert_not_called()

    def test_llm_verification_stage_skips_when_disabled(self):
        """LLMVerificationStage should skip when enabled=False."""
        mock_client = MagicMock()
        doc = _make_doc()
        doc.extracted_entities = _make_entities()
        doc.extracted_relationships = _make_relationships()

        import asyncio
        stage = LLMVerificationStage(
            anthropic_client=mock_client,
            enabled=False,
        )
        result = asyncio.run(stage.process(doc))

        mock_client.messages.create.assert_not_called()
        # Document should pass through unchanged
        assert len(result.extracted_entities) == len(_make_entities())

    def test_budget_records_spend_per_api_call(self):
        """Each Haiku API call should record spend on the budget tracker."""
        mock_response = _make_anthropic_response(json.dumps([
            {"text": "NATO", "entity_type": "ORG", "canonical_name": "NATO",
             "confidence": 0.95, "is_valid": True, "merge_with": None},
        ]))
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="NATO", entity_type="ORG", start_char=0, end_char=4,
                confidence=0.95, extraction_method="spacy", context_window="",
            ),
        ]

        import asyncio
        budget = _make_budget()
        initial_spend = budget._hourly_spend
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        asyncio.run(verifier.verify(doc, stats))

        assert budget._hourly_spend > initial_spend
        assert stats.haiku_calls == 1
        assert stats.haiku_cost_usd > 0


# ─── Graceful failure tests ──────────────────────────────────────────────────


class TestGracefulFailure:
    def test_entity_verifier_handles_api_exception(self):
        """EntityVerifier should not raise on API exception."""
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(
            side_effect=Exception("API unavailable")
        )

        doc = _make_doc()
        doc.extracted_entities = _make_entities()
        original_count = len(doc.extracted_entities)

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        # Should return doc with original entities unchanged
        assert len(result.extracted_entities) == original_count

    def test_entity_verifier_handles_invalid_json(self):
        """EntityVerifier should handle unparseable JSON from API."""
        mock_client = MagicMock()
        mock_response = _make_anthropic_response("This is not valid JSON at all!")
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_entities = _make_entities()
        original_count = len(doc.extracted_entities)

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        # Should return doc unchanged
        assert len(result.extracted_entities) == original_count

    def test_location_verifier_handles_api_exception(self):
        """LocationVerifier should not raise on API exception."""
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(
            side_effect=Exception("Connection timeout")
        )

        LocationVerifier._correction_cache = {}

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="Ukraine", entity_type="GPE", start_char=0, end_char=7,
                confidence=0.95, extraction_method="spacy", context_window="",
            ),
        ]
        doc.geospatial_data["Ukraine:GPE"] = GeospatialData(
            resolved=True, latitude=48.38, longitude=31.17,
        )

        import asyncio
        budget = _make_budget()
        verifier = LocationVerifier(mock_client, budget, batch_size=30)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        # Geospatial data should be unchanged
        geo = result.geospatial_data.get("Ukraine:GPE")
        assert geo is not None
        assert geo.latitude == pytest.approx(48.38)

    def test_relationship_verifier_handles_api_exception(self):
        """RelationshipVerifier should not raise on API exception."""
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(
            side_effect=Exception("Rate limit exceeded")
        )

        doc = _make_doc()
        doc.extracted_relationships = _make_relationships()
        original_count = len(doc.extracted_relationships)

        import asyncio
        budget = _make_budget()
        verifier = RelationshipVerifier(mock_client, budget, batch_size=40)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        # Should return doc with original relationships unchanged
        assert len(result.extracted_relationships) == original_count

    def test_full_stage_survives_all_components_failing(self):
        """LLMVerificationStage should survive if all components fail."""
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(
            side_effect=Exception("API completely down")
        )

        doc = _make_doc()
        doc.extracted_entities = _make_entities()
        doc.extracted_relationships = _make_relationships()
        original_entity_count = len(doc.extracted_entities)
        original_rel_count = len(doc.extracted_relationships)

        import asyncio
        budget = _make_budget()
        stage = LLMVerificationStage(
            anthropic_client=mock_client,
            budget_tracker=budget,
            exa_api_key="",  # disable Exa
            enabled=True,
        )
        result = asyncio.run(stage.process(doc))

        # Document should pass through with original data intact
        assert len(result.extracted_entities) == original_entity_count
        assert len(result.extracted_relationships) == original_rel_count

    def test_entity_verifier_handles_markdown_fenced_json(self):
        """EntityVerifier should parse JSON wrapped in markdown code fences."""
        mock_client = MagicMock()
        fenced_response = '```json\n[{"text": "NATO", "entity_type": "ORG", "canonical_name": "NATO", "confidence": 0.95, "is_valid": true, "merge_with": null}]\n```'
        mock_response = _make_anthropic_response(fenced_response)
        mock_client.messages.create = MagicMock(return_value=mock_response)

        doc = _make_doc()
        doc.extracted_entities = [
            ExtractedEntity(
                text="NATO", entity_type="ORG", start_char=0, end_char=4,
                confidence=0.85, extraction_method="spacy", context_window="",
            ),
        ]

        import asyncio
        budget = _make_budget()
        verifier = EntityVerifier(mock_client, budget, batch_size=50)
        stats = VerificationStats()
        result = asyncio.run(verifier.verify(doc, stats))

        assert len(result.extracted_entities) == 1
        assert result.extracted_entities[0].confidence == pytest.approx(0.95)


# ─── Integration test ────────────────────────────────────────────────────────


class TestLLMVerificationStageIntegration:
    def test_stage_name(self):
        """Stage name should be 'llm_verification'."""
        stage = LLMVerificationStage()
        assert stage.name == "llm_verification"

    def test_stage_processes_full_document(self):
        """Full pipeline stage should process all components on a real document."""
        # Mock entity verification response
        entity_response = json.dumps([
            {"text": "Monday", "entity_type": "DATE", "canonical_name": "Monday",
             "confidence": 0.1, "is_valid": False, "merge_with": None},
            {"text": "NATO", "entity_type": "ORG", "canonical_name": "NATO",
             "confidence": 0.99, "is_valid": True, "merge_with": None},
            {"text": "Ukraine", "entity_type": "GPE", "canonical_name": "Ukraine",
             "confidence": 0.97, "is_valid": True, "merge_with": None},
            {"text": "AI", "entity_type": "PRODUCT", "canonical_name": "AI",
             "confidence": 0.75, "is_valid": True, "merge_with": None},
        ])

        # Mock relationship verification response (covers all 3 relationships)
        rel_response = json.dumps([
            {"subject": "NATO", "object": "Ukraine", "is_meaningful": True,
             "predicate": "allied_with", "confidence": 0.87, "reason": "Alliance"},
            {"subject": "Monday", "object": "NATO", "is_meaningful": False,
             "predicate": "co_occurs_with", "confidence": 0.1,
             "reason": "Day of week co-occurrence is noise"},
            {"subject": "Vladimir Putin", "object": "NATO", "is_meaningful": True,
             "predicate": "opposes", "confidence": 0.92, "reason": "Putin condemned NATO"},
        ])

        call_count = 0
        def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            content = kwargs.get("messages", [{}])[0].get("content", "")
            # First call = entity verification, second = relationship verification
            if "entity_type" in content and "is_valid" in content:
                return _make_anthropic_response(entity_response)
            return _make_anthropic_response(rel_response)

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(side_effect=mock_create)

        LocationVerifier._correction_cache = {}

        doc = _make_doc()
        doc.extracted_entities = _make_entities()
        doc.extracted_relationships = _make_relationships()

        import asyncio
        budget = _make_budget()
        stage = LLMVerificationStage(
            anthropic_client=mock_client,
            budget_tracker=budget,
            exa_api_key="",  # disable Exa
            model="claude-haiku-3-5-20241022",
            enabled=True,
            batch_size=50,
        )
        result = asyncio.run(stage.process(doc))

        # Monday should be filtered
        entity_texts = [e.text for e in result.extracted_entities]
        assert "Monday" not in entity_texts
        assert "NATO" in entity_texts

        # At least one relationship should be pruned
        assert len(result.extracted_relationships) < len(_make_relationships())

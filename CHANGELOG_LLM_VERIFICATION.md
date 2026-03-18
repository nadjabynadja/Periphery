# LLM Verification Layer — Changelog

**Date:** 2026-03-18  
**Author:** James Beard (Principal Engineer)  
**Branch:** main

---

## Overview

Adds a post-extraction LLM verification and enrichment layer (`llm_verification`) as Stage 7 of the enrichment pipeline. This stage runs after all extraction stages and before final document assembly, using Claude Haiku and Exa search to improve data quality.

## New Files

### `periphery/enrichment/stages/llm_verification.py`

The core verification stage. Contains four components:

#### 1. `EntityVerifier`
- Batches all extracted entities into a single Claude Haiku call (configurable batch size, default 50)
- Filters junk: days of the week, single letters, common words misidentified as entities
- Fixes misclassifications: "AI" → PRODUCT, "Congress" → ORG not PERSON, etc.
- Deduplicates via `merge_with`: "US", "U.S.", "United States" → single canonical entity
- Scores confidence 0.0–1.0 per entity in context
- Updates `resolved_entity_map` for merged entities
- Propagates entity key changes to `geospatial_data` and `temporal_contexts`

#### 2. `LocationVerifier`
- Batches geocoded entities into Claude Haiku calls
- Detects clearly wrong coordinates (e.g. "United States" at lat=16.2, lon=-61.5)
- Clears geocoding for entity types that shouldn't be geocoded (PERSON, abstract concepts)
- Replaces incorrect coordinates with LLM-suggested values
- Class-level geocoding correction cache (no redundant API calls for repeated entities)
- Marks corrected geocodes with `geocoding_source="llm_verified"`

#### 3. `RelationshipVerifier`
- Batches all relationships into Claude Haiku calls
- Prunes noise: co-occurrence of day-of-week with city, unrelated entity pairs
- Enriches predicates: `co_occurs_with` → `allied_with`, `supplies_weapons_to`, `acquired`, etc.
- Updates confidence scores per relationship
- Keeps relationships not in LLM response unchanged (safe fallback)

#### 4. `ExaEnricher`
- Enriches "important" entities with real-time Exa search context
- Qualifies by: `entity_type in {GPE, ORG}` with `confidence >= 0.8`, OR high frequency in document
- Stores enrichment metadata (description, key facts, recent events, sources) in `doc.ingest_metadata["exa_enrichments"]`
- Per-entity Exa cache (configurable TTL, default 10 minutes)
- Lazy-initializes Exa client (no import error if `exa_py` not installed)

#### `LLMVerificationStage`
- Orchestrates all four components in sequence
- Shared `BudgetTracker` and Anthropic client across all components
- Logs comprehensive verification stats: entities filtered/reclassified/merged, locations cleared/corrected, relationships pruned/enriched, Haiku call count and cost, Exa call count
- All components degrade gracefully on API failure — never blocks the pipeline

### `tests/test_llm_verification.py`

27 tests covering:
- `TestEntityVerifier` (5 tests): junk filtering, type correction, merge dedup, confidence updates, empty-entities skip
- `TestLocationVerifier` (5 tests): PERSON geocoding clear, coordinate correction, correct coord passthrough, empty skip, cache behavior
- `TestRelationshipVerifier` (4 tests): noise pruning, predicate enrichment, unverified passthrough, empty skip
- `TestBudgetLimiting` (5 tests): all three verifiers + full stage skip on exhausted budget, spend recording
- `TestGracefulFailure` (6 tests): API exception handling for all components, invalid JSON, markdown-fenced JSON, full stage resilience
- `TestLLMVerificationStageIntegration` (2 tests): stage name, end-to-end document processing

All 27 new tests pass. Full suite: **442 passed, 3 skipped**.

## Modified Files

### `periphery/config.py`

Added 5 new settings under `# LLM verification stage settings`:

```python
verification_enabled: bool = True
verification_model: str = "claude-haiku-3-5-20241022"
verification_exa_enabled: bool = True
verification_exa_min_source_count: int = 3
verification_batch_size: int = 50
```

### `periphery/enrichment/pipeline.py`

- Updated `build_enrichment_pipeline()` docstring: "six stages" → "seven stages"
- Added `LLMVerificationStage` import
- Appended `LLMVerificationStage(...)` as the final stage in the pipeline chain, initialized with the shared `anthropic_client`, `budget_tracker`, and all verification settings from config
- Updated `logger.info("enrichment_pipeline_built", ...)` to include `verification_enabled` flag

## Architecture Notes

- **Shared budget**: The `LLMVerificationStage` shares the same `BudgetTracker` instance as `RelationshipExtractionStage`. Haiku is much cheaper than Sonnet, so verification should rarely compete with Tier 3 extraction for budget headroom.
- **Model choice**: Claude Haiku (`claude-haiku-3-5-20241022`) at $0.80/M input + $4.00/M output. A typical document with 50 entities + 50 relationships costs roughly $0.003–0.008 in Haiku calls.
- **Exa**: Only fires for high-value entities (GPE/ORG with confidence ≥ 0.8). Disabled cleanly if `exa_api_key` is not set or `verification_exa_enabled=False`.
- **Prompts**: Each prompt includes document title + first 500 chars of content for grounding, few-shot examples demonstrating correct behavior, and a strict "return only valid JSON array" instruction.
- **Caching**: `LocationVerifier` uses a class-level correction cache for geocoding fixes (survives across document processing). `ExaEnricher` uses a per-instance TTL cache.

## Test Results

```
tests/test_llm_verification.py: 27 passed
Full suite: 442 passed, 3 skipped, 3 warnings in 15.00s
```

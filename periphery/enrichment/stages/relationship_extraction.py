"""Stage 2 — Relationship Extraction (Tiered).

Three tiers of relationship extraction, applied based on source credibility:
  Tier 1: Co-occurrence (all documents) — entity co-occurrence by proximity
  Tier 2: Dependency-based (credibility tier 1-3) — SpaCy dep parse for SVO triples
  Tier 3: LLM-based (credibility tier 1-2) — Claude API for structured extraction

Reuses the SpaCy Doc object from entity extraction — never re-parses.
Degrades gracefully when the LLM budget is exhausted.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from itertools import combinations
from typing import Any

import structlog

from periphery.enrichment.budget import BudgetTracker
from periphery.enrichment.models import ExtractedRelationship, PipelineDocument
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# Co-occurrence weights by proximity
WEIGHT_SAME_SENTENCE = 1.0
WEIGHT_SAME_PARAGRAPH = 0.5
WEIGHT_SAME_DOCUMENT = 0.2

# LLM extraction prompt — full spec version
_LLM_EXTRACTION_PROMPT = """\
You are an OSINT relationship extraction system. Extract all entity-relationship-entity \
triples from the following text.

For each triple, provide:
- subject: {{"name": str, "type": str}} — the source entity
- predicate: str — the relationship verb. Use specific, normalized predicates: owns, \
funds, directs, operates, acquired, transferred_to, sanctioned_by, located_in, \
subsidiary_of, allied_with, met_with, supplied, transported, arrested, indicted, \
appointed, resigned_from, invested_in, contracted_with
- object: {{"name": str, "type": str}} — the target entity
- confidence: float 0.0-1.0 — your confidence in this extraction
- temporal_qualifier: "current" | "historical" | "speculative"
- evidence: str — the exact sentence or clause that supports this triple
- implicit: bool — true if this relationship is implied rather than explicitly stated

Extract both explicit relationships (directly stated) and implicit relationships \
(strongly implied by context). For implicit relationships, set confidence lower \
and implicit to true.

Return ONLY a valid JSON array. No preamble, no markdown fencing, no explanation.

TEXT:
{text}"""


def assign_extraction_tiers(
    doc: PipelineDocument,
    budget_available: bool,
    tier2_max_credibility: int = 3,
    tier3_max_credibility: int = 2,
) -> list[int]:
    """Decide which tiers a document gets based on credibility and budget.

    Every document gets Tier 1 (co-occurrence).
    Credibility tier 1-3 sources also get Tier 2 (dependency parsing).
    Credibility tier 1-2 sources also get Tier 3 (LLM) if budget allows.
    Crystallizer-flagged documents get all tiers regardless of source.
    """
    tiers = [1]

    if doc.priority <= tier2_max_credibility:
        tiers.append(2)

    if doc.priority <= tier3_max_credibility and budget_available:
        tiers.append(3)

    # Crystallizer-flagged documents get all tiers regardless of source
    if doc.crystallizer_priority_flag:
        tiers = [1, 2, 3] if budget_available else [1, 2]

    return tiers


def _deduplicate_relationships(
    relationships: list[ExtractedRelationship],
) -> list[ExtractedRelationship]:
    """Deduplicate relationships across tiers.

    Normalizes subject, predicate, and object, then merges duplicates.
    When merging, keeps the highest confidence score and richest metadata.
    Keeps extraction_tier reflecting the highest tier that found it.
    """
    if not relationships:
        return relationships

    # Build a key for each relationship
    grouped: dict[str, list[ExtractedRelationship]] = defaultdict(list)
    for rel in relationships:
        key = (
            f"{rel.subject_text.lower().strip()}|"
            f"{rel.predicate.lower().strip()}|"
            f"{rel.object_text.lower().strip()}"
        )
        grouped[key].append(rel)

    deduped: list[ExtractedRelationship] = []
    for _key, rels in grouped.items():
        if len(rels) == 1:
            deduped.append(rels[0])
            continue

        # Sort by extraction_tier descending (prefer higher tiers), then confidence
        rels.sort(key=lambda r: (r.extraction_tier, r.confidence), reverse=True)
        best = rels[0]

        # Merge: keep highest confidence and highest tier
        max_confidence = max(r.confidence for r in rels)
        max_tier = max(r.extraction_tier for r in rels)

        # Find the richest version (highest tier first)
        merged = best.model_copy(
            update={
                "confidence": max_confidence,
                "extraction_tier": max_tier,
                # Keep temporal qualifier from the richest source
                "temporal_qualifier": best.temporal_qualifier
                or next(
                    (r.temporal_qualifier for r in rels if r.temporal_qualifier), ""
                ),
                # Keep evidence from the richest source
                "evidence": best.evidence
                or next((r.evidence for r in rels if r.evidence), ""),
            }
        )
        deduped.append(merged)

    return deduped


class RelationshipExtractionStage(EnrichmentStage):
    """Stage 2: Extract relationships at configurable depth tiers.

    Reuses the SpaCy Doc object from entity extraction (Stage 1).
    Never re-parses the document.
    """

    def __init__(
        self,
        budget_tracker: BudgetTracker | None = None,
        anthropic_client: Any = None,
        llm_model: str = "claude-sonnet-4-20250514",
        tier2_min_priority: int = 3,
        tier3_min_priority: int = 2,
        llm_timeout_seconds: float = 30.0,
        llm_max_tokens_per_request: int = 4000,
    ) -> None:
        self._budget = budget_tracker or BudgetTracker()
        self._anthropic = anthropic_client
        self._llm_model = llm_model
        self._tier2_max_credibility = tier2_min_priority
        self._tier3_max_credibility = tier3_min_priority
        self._llm_timeout = llm_timeout_seconds
        self._llm_max_tokens = llm_max_tokens_per_request

    @property
    def name(self) -> str:
        return "relationship_extraction"

    @property
    def budget_tracker(self) -> BudgetTracker:
        return self._budget

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Extract relationships using tiered approach."""
        if not doc.extracted_entities:
            return doc

        # Use source credibility tier if available (from SourceCredibilityStage),
        # otherwise fall back to doc.priority
        if doc.source_credibility:
            doc.priority = doc.source_credibility.source_credibility_tier

        # Check if document *would* qualify for Tier 3 (ignoring budget)
        tiers_if_budget = assign_extraction_tiers(
            doc,
            budget_available=True,
            tier2_max_credibility=self._tier2_max_credibility,
            tier3_max_credibility=self._tier3_max_credibility,
        )
        wants_tier3 = 3 in tiers_if_budget

        tiers = assign_extraction_tiers(
            doc,
            budget_available=self._budget.budget_available,
            tier2_max_credibility=self._tier2_max_credibility,
            tier3_max_credibility=self._tier3_max_credibility,
        )

        relationships: list[ExtractedRelationship] = []

        # Tier 1: Co-occurrence (always runs)
        if 1 in tiers:
            relationships.extend(self._tier1_cooccurrence(doc))

        # Tier 2: Dependency-based
        if 2 in tiers:
            relationships.extend(self._tier2_dependency(doc))

        # Tier 3: LLM-based
        if 3 in tiers:
            if self._anthropic is not None and self._budget.budget_available:
                doc.llm_enrichment_status = "pending"
                llm_rels = await self._tier3_llm(doc)
                relationships.extend(llm_rels)
                doc.llm_enrichment_status = "complete" if llm_rels else "skipped"
            elif not self._budget.budget_available:
                doc.llm_enrichment_status = "budget_exhausted"
                logger.info(
                    "llm_budget_exhausted",
                    doc_id=doc.id,
                    hourly_remaining=self._budget.hourly_remaining,
                    daily_remaining=self._budget.daily_remaining,
                )
            else:
                doc.llm_enrichment_status = "skipped"
        elif wants_tier3 and not self._budget.budget_available:
            # Document qualified for Tier 3 but budget was exhausted
            doc.llm_enrichment_status = "budget_exhausted"
            logger.info(
                "llm_budget_exhausted",
                doc_id=doc.id,
                hourly_remaining=self._budget.hourly_remaining,
                daily_remaining=self._budget.daily_remaining,
            )

        # Deduplicate across tiers
        relationships = _deduplicate_relationships(relationships)

        doc.extracted_relationships = relationships

        tier_counts = defaultdict(int)
        for r in relationships:
            tier_counts[r.extraction_tier] += 1

        logger.debug(
            "relationships_extracted",
            doc_id=doc.id,
            total=len(relationships),
            tier1=tier_counts.get(1, 0),
            tier2=tier_counts.get(2, 0),
            tier3=tier_counts.get(3, 0),
            tiers_assigned=tiers,
            llm_status=doc.llm_enrichment_status,
        )
        return doc

    # ── Tier 1: Co-occurrence ─────────────────────────────────────────────

    def _tier1_cooccurrence(
        self, doc: PipelineDocument
    ) -> list[ExtractedRelationship]:
        """Build co-occurrence edges from entity proximity.

        Uses SpaCy's sentence segmentation (doc.sents) when the SpaCy Doc is
        available. Falls back to regex splitting otherwise.
        Paragraphs are detected by splitting on double newlines.
        """
        text = doc.full_text
        entities = doc.extracted_entities
        if len(entities) < 2:
            return []

        relationships: list[ExtractedRelationship] = []

        # Build sentence and paragraph boundaries
        spacy_doc = doc.spacy_doc
        if spacy_doc is not None:
            sentence_spans = [
                (sent.start_char, sent.end_char, sent.text)
                for sent in spacy_doc.sents
            ]
        else:
            sentence_spans = self._regex_sentence_spans(text)

        paragraph_spans = self._paragraph_spans(text)

        # Map each entity to its sentence and paragraph indices
        ent_sentence_map: dict[int, int] = {}
        ent_paragraph_map: dict[int, int] = {}

        for i, ent in enumerate(entities):
            ent_mid = (ent.start_char + ent.end_char) // 2
            for s_idx, (s_start, s_end, _s_text) in enumerate(sentence_spans):
                if s_start <= ent_mid < s_end:
                    ent_sentence_map[i] = s_idx
                    break
            for p_idx, (p_start, p_end) in enumerate(paragraph_spans):
                if p_start <= ent_mid < p_end:
                    ent_paragraph_map[i] = p_idx
                    break

        # Group entities by sentence and paragraph
        sent_groups: dict[int, list[int]] = defaultdict(list)
        para_groups: dict[int, list[int]] = defaultdict(list)
        for i in range(len(entities)):
            if i in ent_sentence_map:
                sent_groups[ent_sentence_map[i]].append(i)
            if i in ent_paragraph_map:
                para_groups[ent_paragraph_map[i]].append(i)

        seen_pairs: set[tuple[int, int]] = set()

        # Same-sentence co-occurrences (weight 1.0)
        for s_idx, ent_indices in sent_groups.items():
            sent_text = (
                sentence_spans[s_idx][2] if s_idx < len(sentence_spans) else ""
            )
            for i, j in combinations(ent_indices, 2):
                pair = (min(i, j), max(i, j))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                e1, e2 = entities[i], entities[j]
                relationships.append(
                    ExtractedRelationship(
                        subject_text=e1.text,
                        subject_type=e1.entity_type,
                        predicate="co_occurs_with",
                        object_text=e2.text,
                        object_type=e2.entity_type,
                        confidence=WEIGHT_SAME_SENTENCE,
                        extraction_tier=1,
                        extraction_method="co_occurrence",
                        evidence=sent_text,
                        co_occurrence_weight=WEIGHT_SAME_SENTENCE,
                    )
                )

        # Same-paragraph but not same-sentence (weight 0.5)
        for p_idx, ent_indices in para_groups.items():
            for i, j in combinations(ent_indices, 2):
                pair = (min(i, j), max(i, j))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                e1, e2 = entities[i], entities[j]
                relationships.append(
                    ExtractedRelationship(
                        subject_text=e1.text,
                        subject_type=e1.entity_type,
                        predicate="co_occurs_with",
                        object_text=e2.text,
                        object_type=e2.entity_type,
                        confidence=WEIGHT_SAME_PARAGRAPH,
                        extraction_tier=1,
                        extraction_method="co_occurrence",
                        co_occurrence_weight=WEIGHT_SAME_PARAGRAPH,
                    )
                )

        # Document-level co-occurrences (weight 0.2)
        for i, j in combinations(range(len(entities)), 2):
            pair = (min(i, j), max(i, j))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            e1, e2 = entities[i], entities[j]
            relationships.append(
                ExtractedRelationship(
                    subject_text=e1.text,
                    subject_type=e1.entity_type,
                    predicate="co_occurs_with",
                    object_text=e2.text,
                    object_type=e2.entity_type,
                    confidence=WEIGHT_SAME_DOCUMENT,
                    extraction_tier=1,
                    extraction_method="co_occurrence",
                    co_occurrence_weight=WEIGHT_SAME_DOCUMENT,
                )
            )

        return relationships

    def _regex_sentence_spans(
        self, text: str
    ) -> list[tuple[int, int, str]]:
        """Fallback sentence splitting when SpaCy Doc is not available."""
        spans = []
        for m in re.finditer(r"[^.!?\n]+[.!?]?\s*", text):
            s = m.group().strip()
            if s:
                spans.append((m.start(), m.end(), s))
        if not spans and text.strip():
            spans.append((0, len(text), text.strip()))
        return spans

    def _paragraph_spans(self, text: str) -> list[tuple[int, int]]:
        """Split text into paragraph spans by double newlines."""
        spans = []
        pos = 0
        for m in re.finditer(r"\n\n+", text):
            if m.start() > pos:
                spans.append((pos, m.start()))
            pos = m.end()
        if pos < len(text):
            spans.append((pos, len(text)))
        if not spans and text:
            spans.append((0, len(text)))
        return spans

    # ── Tier 2: Dependency-based extraction ────────────────────────────────

    def _tier2_dependency(
        self, doc: PipelineDocument
    ) -> list[ExtractedRelationship]:
        """Extract SVO triples from SpaCy dependency parse.

        Reuses the SpaCy Doc from entity extraction. Handles:
        - Active voice: "Company X acquired Firm Y"
        - Passive voice: "Firm Y was acquired by Company X" (reverses direction)
        - Prepositional: "CEO of Company X"
        - Copular: "John Smith is the director of ACME Corp"
        """
        spacy_doc = doc.spacy_doc
        if spacy_doc is None:
            logger.warning(
                "tier2_no_spacy_doc",
                doc_id=doc.id,
                msg="SpaCy Doc not available — skipping dependency extraction",
            )
            return []

        # Build lookup sets for entity matching
        entity_texts = {e.text.lower() for e in doc.extracted_entities}
        entity_type_map = {
            e.text.lower(): e.entity_type for e in doc.extracted_entities
        }

        relationships: list[ExtractedRelationship] = []

        for sent in spacy_doc.sents:
            # Collect entities present in this sentence
            sent_entity_tokens = self._find_entities_in_span(
                sent, entity_texts
            )
            if len(sent_entity_tokens) < 2:
                continue

            # Walk every verb in the sentence
            for token in sent:
                if token.pos_ == "VERB":
                    rels = self._extract_verb_relations(
                        token, entity_texts, entity_type_map, sent
                    )
                    relationships.extend(rels)

                # Copular constructions: "X is the director of Y"
                elif token.pos_ == "AUX" and token.dep_ == "ROOT":
                    rels = self._extract_copular_relations(
                        token, entity_texts, entity_type_map, sent
                    )
                    relationships.extend(rels)

        return relationships

    def _extract_verb_relations(
        self,
        verb_token: Any,
        entity_texts: set[str],
        entity_type_map: dict[str, str],
        sent: Any,
    ) -> list[ExtractedRelationship]:
        """Extract relations from a verb token's dependency children."""
        relationships = []
        predicate = verb_token.lemma_.lower()
        is_passive = any(
            child.dep_ == "nsubjpass" for child in verb_token.children
        )

        subjects = []
        objects = []

        for child in verb_token.children:
            span_text = _get_subtree_text(child)

            if child.dep_ == "nsubj":
                if span_text.lower() in entity_texts:
                    subjects.append(span_text)

            elif child.dep_ == "nsubjpass":
                # In passive voice, the grammatical subject is the logical object
                if span_text.lower() in entity_texts:
                    objects.append(span_text)

            elif child.dep_ in ("dobj", "attr", "oprd"):
                if span_text.lower() in entity_texts:
                    objects.append(span_text)

            elif child.dep_ == "prep":
                # "acquired by Company X" or "CEO of Company X"
                prep_text = child.text.lower()
                for pobj in child.children:
                    if pobj.dep_ == "pobj":
                        pobj_text = _get_subtree_text(pobj)
                        if pobj_text.lower() in entity_texts:
                            if prep_text == "by" and is_passive:
                                # "was acquired BY Company X" — Company X is the real subject
                                subjects.append(pobj_text)
                            else:
                                objects.append(pobj_text)

            elif child.dep_ == "agent":
                # SpaCy sometimes marks "by X" as agent in passive
                for pobj in child.children:
                    if pobj.dep_ == "pobj":
                        pobj_text = _get_subtree_text(pobj)
                        if pobj_text.lower() in entity_texts:
                            subjects.append(pobj_text)

        # Determine confidence based on extraction quality
        for subj in subjects:
            for obj in objects:
                if subj.lower() == obj.lower():
                    continue

                # Direct SVO link = 0.9, passive/traversed = 0.7, prepositional = 0.6
                if not is_passive and verb_token.dep_ == "ROOT":
                    confidence = 0.9
                elif is_passive:
                    confidence = 0.7
                else:
                    confidence = 0.6

                relationships.append(
                    ExtractedRelationship(
                        subject_text=subj,
                        subject_type=entity_type_map.get(
                            subj.lower(), "UNKNOWN"
                        ),
                        predicate=predicate,
                        object_text=obj,
                        object_type=entity_type_map.get(
                            obj.lower(), "UNKNOWN"
                        ),
                        confidence=confidence,
                        extraction_tier=2,
                        extraction_method="dependency_parse",
                        evidence=sent.text.strip(),
                    )
                )

        return relationships

    def _extract_copular_relations(
        self,
        aux_token: Any,
        entity_texts: set[str],
        entity_type_map: dict[str, str],
        sent: Any,
    ) -> list[ExtractedRelationship]:
        """Extract relations from copular constructions like 'X is the director of Y'."""
        relationships = []
        subjects = []
        attributes = []

        for child in aux_token.children:
            span_text = _get_subtree_text(child)

            if child.dep_ == "nsubj" and span_text.lower() in entity_texts:
                subjects.append(span_text)

            elif child.dep_ == "attr":
                # "is the director of ACME Corp"
                # The attribute might contain a prepositional phrase with an entity
                attr_text = child.text.lower()
                for attr_child in child.children:
                    if attr_child.dep_ == "prep":
                        for pobj in attr_child.children:
                            if pobj.dep_ == "pobj":
                                pobj_text = _get_subtree_text(pobj)
                                if pobj_text.lower() in entity_texts:
                                    # Build predicate from "is_<attr>_<prep>"
                                    predicate = f"is_{attr_text}_{attr_child.text.lower()}"
                                    attributes.append((pobj_text, predicate))

        for subj in subjects:
            for obj_text, predicate in attributes:
                if subj.lower() == obj_text.lower():
                    continue
                relationships.append(
                    ExtractedRelationship(
                        subject_text=subj,
                        subject_type=entity_type_map.get(
                            subj.lower(), "UNKNOWN"
                        ),
                        predicate=predicate,
                        object_text=obj_text,
                        object_type=entity_type_map.get(
                            obj_text.lower(), "UNKNOWN"
                        ),
                        confidence=0.6,
                        extraction_tier=2,
                        extraction_method="dependency_parse",
                        evidence=sent.text.strip(),
                    )
                )

        return relationships

    def _find_entities_in_span(
        self, span: Any, entity_texts: set[str]
    ) -> list[str]:
        """Find which known entities appear in a SpaCy span."""
        found = []
        span_text_lower = span.text.lower()
        for ent_text in entity_texts:
            if ent_text in span_text_lower:
                found.append(ent_text)
        return found

    # ── Tier 3: LLM-based extraction ──────────────────────────────────────

    async def _tier3_llm(
        self, doc: PipelineDocument
    ) -> list[ExtractedRelationship]:
        """LLM-based relationship extraction via Claude API.

        Sends document text (capped to control cost) to Claude for structured
        extraction. Parses JSON response with fallback for markdown fences.
        """
        if not self._anthropic:
            return []

        if not self._budget.budget_available:
            doc.llm_enrichment_status = "budget_exhausted"
            return []

        # Cap text to ~4000 tokens (roughly 16000 chars)
        text = doc.full_text[:16000]

        try:
            response = await self._anthropic.messages.create(
                model=self._llm_model,
                max_tokens=self._llm_max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": _LLM_EXTRACTION_PROMPT.format(text=text),
                    }
                ],
            )

            # Estimate cost (rough: $3/M input, $15/M output for Sonnet)
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            self._budget.record_spend(cost)

            # Parse response — handle markdown fences
            content = response.content[0].text
            triples = self._parse_llm_json(content)

            if triples is None:
                logger.warning(
                    "tier3_json_parse_failed",
                    doc_id=doc.id,
                    content_preview=content[:200],
                )
                return []

            relationships = []
            for t in triples:
                subject_text = t.get("subject", {}).get("name", "")
                object_text = t.get("object", {}).get("name", "")
                # Skip triples with empty subject or object
                if not subject_text or not object_text:
                    continue
                try:
                    confidence = float(t.get("confidence", 0.5))
                    confidence = max(0.0, min(1.0, confidence))
                except (TypeError, ValueError):
                    confidence = 0.5
                relationships.append(
                    ExtractedRelationship(
                        subject_text=subject_text,
                        subject_type=t.get("subject", {}).get(
                            "type", "UNKNOWN"
                        ),
                        predicate=t.get("predicate", "related_to"),
                        object_text=object_text,
                        object_type=t.get("object", {}).get(
                            "type", "UNKNOWN"
                        ),
                        confidence=confidence,
                        extraction_tier=3,
                        extraction_method="llm",
                        evidence=t.get("evidence", ""),
                        temporal_qualifier=t.get(
                            "temporal_qualifier", ""
                        ),
                        implicit=bool(t.get("implicit", False)),
                    )
                )

            logger.debug(
                "tier3_llm_extraction_complete",
                doc_id=doc.id,
                relationships_found=len(relationships),
                cost_usd=round(cost, 4),
            )
            return relationships

        except Exception as exc:
            logger.warning(
                "tier3_llm_extraction_failed",
                doc_id=doc.id,
                error=str(exc),
            )
            return []

    def _parse_llm_json(self, content: str) -> list[dict] | None:
        """Parse LLM JSON response, stripping markdown fences if needed."""
        # First attempt: direct parse
        try:
            result = json.loads(content)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Second attempt: strip markdown fences
        stripped = re.sub(
            r"```(?:json)?\s*\n?", "", content
        ).strip()
        stripped = stripped.rstrip("`").strip()
        try:
            result = json.loads(stripped)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        return None


def _get_subtree_text(token: Any) -> str:
    """Get the text of a token's subtree (full noun phrase)."""
    subtree = sorted(token.subtree, key=lambda t: t.i)
    return " ".join(t.text for t in subtree)

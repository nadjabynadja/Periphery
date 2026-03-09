"""Stage 2 — Relationship Extraction (Tiered).

Three tiers of relationship extraction, applied based on source priority:
  Tier 1: Co-occurrence (all documents) — cheap entity co-occurrence matrix
  Tier 2: Dependency-based (medium-priority) — SpaCy dep parse for SVO triples
  Tier 3: LLM-based (high-priority) — Claude API for structured extraction

The system degrades gracefully when the LLM budget is exhausted.
"""

from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations

import structlog

from periphery.enrichment.budget import BudgetTracker
from periphery.enrichment.models import ExtractedRelationship, PipelineDocument
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# Co-occurrence weights by proximity
WEIGHT_SAME_SENTENCE = 1.0
WEIGHT_SAME_PARAGRAPH = 0.5
WEIGHT_SAME_DOCUMENT = 0.1

# LLM extraction prompt
_LLM_EXTRACTION_PROMPT = """\
Extract all entity-relationship-entity triples from this text.
For each triple, provide:
- subject: the source entity (name and type)
- predicate: the relationship (use specific verbs: owns, funds, directs, \
operates, transferred_to, sanctioned_by, located_in, etc.)
- object: the target entity (name and type)
- confidence: your confidence in this extraction (0.0-1.0)
- temporal_qualifier: is this relationship current, historical, or speculative?
- evidence: the exact sentence that supports this triple

Return ONLY a valid JSON array. No preamble.

Text:
{text}
"""


class RelationshipExtractionStage(EnrichmentStage):
    """Stage 2: Extract relationships at configurable depth tiers."""

    def __init__(
        self,
        budget_tracker: BudgetTracker | None = None,
        anthropic_client=None,
        llm_model: str = "claude-sonnet-4-20250514",
        tier2_min_priority: int = 3,
        tier3_min_priority: int = 1,
    ) -> None:
        self._budget = budget_tracker or BudgetTracker()
        self._anthropic = anthropic_client
        self._llm_model = llm_model
        self._tier2_min_priority = tier2_min_priority
        self._tier3_min_priority = tier3_min_priority
        self._nlp = None

    @property
    def name(self) -> str:
        return "relationship_extraction"

    def _get_nlp(self):
        if self._nlp is None:
            import spacy

            try:
                self._nlp = spacy.load("en_core_web_trf")
            except OSError:
                self._nlp = spacy.load("en_core_web_sm")
        return self._nlp

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Extract relationships using tiered approach."""
        if not doc.extracted_entities:
            return doc

        relationships: list[ExtractedRelationship] = []

        # Tier 1: Co-occurrence (always runs)
        relationships.extend(self._tier1_cooccurrence(doc))

        # Tier 2: Dependency-based (medium priority and above)
        if doc.priority <= self._tier2_min_priority:
            relationships.extend(self._tier2_dependency(doc))

        # Tier 3: LLM-based (high priority only, budget permitting)
        if (
            doc.priority <= self._tier3_min_priority
            and self._budget.budget_available
            and self._anthropic is not None
        ):
            llm_rels = await self._tier3_llm(doc)
            relationships.extend(llm_rels)

        doc.extracted_relationships = relationships
        logger.debug(
            "relationships_extracted",
            doc_id=doc.id,
            tier1=sum(1 for r in relationships if r.extraction_tier == 1),
            tier2=sum(1 for r in relationships if r.extraction_tier == 2),
            tier3=sum(1 for r in relationships if r.extraction_tier == 3),
        )
        return doc

    def _tier1_cooccurrence(self, doc: PipelineDocument) -> list[ExtractedRelationship]:
        """Tier 1: Build co-occurrence relationships from entity proximity."""
        text = doc.full_text
        entities = doc.extracted_entities
        relationships = []

        # Group entities by sentence
        sentences = self._split_sentences(text)
        paragraphs = text.split("\n\n")

        entity_sentences: dict[int, list] = defaultdict(list)
        entity_paragraphs: dict[int, list] = defaultdict(list)

        for i, ent in enumerate(entities):
            for s_idx, sent in enumerate(sentences):
                if ent.text in sent:
                    entity_sentences[s_idx].append(i)
                    break
            for p_idx, para in enumerate(paragraphs):
                if ent.text in para:
                    entity_paragraphs[p_idx].append(i)
                    break

        seen_pairs: set[tuple[int, int]] = set()

        # Same-sentence co-occurrences (strongest)
        for s_idx, ent_indices in entity_sentences.items():
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
                        predicate="co-occurs_with",
                        object_text=e2.text,
                        object_type=e2.entity_type,
                        confidence=WEIGHT_SAME_SENTENCE,
                        extraction_tier=1,
                        evidence=sentences[s_idx] if s_idx < len(sentences) else "",
                    )
                )

        # Same-paragraph co-occurrences (weaker)
        for p_idx, ent_indices in entity_paragraphs.items():
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
                        predicate="co-occurs_with",
                        object_text=e2.text,
                        object_type=e2.entity_type,
                        confidence=WEIGHT_SAME_PARAGRAPH,
                        extraction_tier=1,
                    )
                )

        # Remaining document-level co-occurrences (weakest)
        all_indices = list(range(len(entities)))
        for i, j in combinations(all_indices, 2):
            pair = (min(i, j), max(i, j))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            e1, e2 = entities[i], entities[j]
            relationships.append(
                ExtractedRelationship(
                    subject_text=e1.text,
                    subject_type=e1.entity_type,
                    predicate="co-occurs_with",
                    object_text=e2.text,
                    object_type=e2.entity_type,
                    confidence=WEIGHT_SAME_DOCUMENT,
                    extraction_tier=1,
                )
            )

        return relationships

    def _tier2_dependency(self, doc: PipelineDocument) -> list[ExtractedRelationship]:
        """Tier 2: Extract SVO triples from SpaCy dependency parse."""
        nlp = self._get_nlp()
        text = doc.full_text[:500_000]  # limit for performance
        spacy_doc = nlp(text)

        entity_texts = {e.text.lower() for e in doc.extracted_entities}
        entity_type_map = {
            e.text.lower(): e.entity_type for e in doc.extracted_entities
        }

        relationships = []
        for sent in spacy_doc.sents:
            # Find the root verb
            root = sent.root
            if root.pos_ != "VERB":
                continue

            subjects = []
            objects = []
            for child in root.children:
                if child.dep_ in ("nsubj", "nsubjpass"):
                    # Get the full noun phrase
                    span_text = _get_subtree_text(child)
                    if span_text.lower() in entity_texts:
                        subjects.append(span_text)
                elif child.dep_ in ("dobj", "pobj", "attr", "oprd"):
                    span_text = _get_subtree_text(child)
                    if span_text.lower() in entity_texts:
                        objects.append(span_text)
                # Also check prepositional objects
                elif child.dep_ == "prep":
                    for pobj in child.children:
                        if pobj.dep_ == "pobj":
                            span_text = _get_subtree_text(pobj)
                            if span_text.lower() in entity_texts:
                                objects.append(span_text)

            for subj in subjects:
                for obj in objects:
                    relationships.append(
                        ExtractedRelationship(
                            subject_text=subj,
                            subject_type=entity_type_map.get(subj.lower(), "UNKNOWN"),
                            predicate=root.lemma_,
                            object_text=obj,
                            object_type=entity_type_map.get(obj.lower(), "UNKNOWN"),
                            confidence=0.7,
                            extraction_tier=2,
                            evidence=sent.text.strip(),
                        )
                    )

        return relationships

    async def _tier3_llm(self, doc: PipelineDocument) -> list[ExtractedRelationship]:
        """Tier 3: LLM-based relationship extraction via Claude API."""
        if not self._anthropic:
            return []

        # Use relevant passages rather than the entire document
        text = doc.full_text[:8000]

        try:
            response = await self._anthropic.messages.create(
                model=self._llm_model,
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": _LLM_EXTRACTION_PROMPT.format(text=text)}
                ],
            )

            # Estimate cost (rough: $3/M input, $15/M output for Sonnet)
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            self._budget.record_spend(cost)

            # Parse response
            content = response.content[0].text
            triples = json.loads(content)
            relationships = []
            for t in triples:
                relationships.append(
                    ExtractedRelationship(
                        subject_text=t.get("subject", {}).get("name", ""),
                        subject_type=t.get("subject", {}).get("type", "UNKNOWN"),
                        predicate=t.get("predicate", "related_to"),
                        object_text=t.get("object", {}).get("name", ""),
                        object_type=t.get("object", {}).get("type", "UNKNOWN"),
                        confidence=float(t.get("confidence", 0.5)),
                        extraction_tier=3,
                        evidence=t.get("evidence", ""),
                        temporal_qualifier=t.get("temporal_qualifier", ""),
                    )
                )
            return relationships

        except Exception as exc:
            logger.warning("tier3_llm_extraction_failed", error=str(exc))
            return []

    def _split_sentences(self, text: str) -> list[str]:
        """Simple sentence splitter."""
        import re

        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _get_subtree_text(token) -> str:
    """Get the text of a token's subtree (full noun phrase)."""
    subtree = sorted(token.subtree, key=lambda t: t.i)
    return " ".join(t.text for t in subtree)

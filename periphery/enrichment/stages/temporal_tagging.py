"""Stage 3 — Temporal Tagging.

Attaches temporal context to every entity and relationship by:
1. Extracting explicit dates from DATE entities and parsing them
2. Running tense classification on sentences containing entities
3. Tagging each element as current/historical/speculative/unresolved
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from periphery.enrichment.models import PipelineDocument, TemporalContext
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# POS tag patterns for tense detection (SpaCy tags)
_PAST_TENSE_TAGS = frozenset({"VBD", "VBN"})  # past tense, past participle
_PRESENT_TENSE_TAGS = frozenset({"VBP", "VBZ", "VBG"})  # present, 3rd person, gerund
_FUTURE_INDICATORS = frozenset({"will", "shall", "would", "could", "might", "may"})
_CONDITIONAL_INDICATORS = frozenset({
    "if", "would", "could", "might", "may", "possibly",
    "potentially", "allegedly", "reportedly",
})


def _parse_date(date_str: str) -> datetime | None:
    """Attempt to parse a date string into a datetime object."""
    from dateutil import parser as dateutil_parser

    try:
        return dateutil_parser.parse(date_str, fuzzy=True).replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


class TemporalTaggingStage(EnrichmentStage):
    """Stage 3: Tag entities and relationships with temporal context."""

    def __init__(self) -> None:
        self._nlp = None

    @property
    def name(self) -> str:
        return "temporal_tagging"

    def _get_nlp(self):
        if self._nlp is None:
            import spacy

            try:
                self._nlp = spacy.load("en_core_web_trf")
            except OSError:
                self._nlp = spacy.load("en_core_web_sm")
        return self._nlp

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Attach temporal context to entities and relationships."""
        if not doc.extracted_entities:
            return doc

        nlp = self._get_nlp()
        text = doc.full_text[:500_000]
        spacy_doc = nlp(text)

        # Build sentence index for tense analysis
        sentences = list(spacy_doc.sents)

        # Document date as fallback
        doc_date = doc.published or doc.ingested

        # Extract explicit dates from DATE entities
        date_map: dict[str, datetime] = {}
        for ent in spacy_doc.ents:
            if ent.label_ == "DATE":
                parsed = _parse_date(ent.text)
                if parsed:
                    date_map[ent.text] = parsed

        # Process each extracted entity
        for entity in doc.extracted_entities:
            entity_key = f"{entity.text}:{entity.entity_type}"

            # Find the sentence containing this entity
            containing_sent = None
            for sent in sentences:
                if entity.text in sent.text:
                    containing_sent = sent
                    break

            # Determine temporal status
            status = "unresolved"
            tense_confidence = 0.0
            explicit_date = None

            if entity.entity_type == "DATE":
                parsed = _parse_date(entity.text)
                if parsed:
                    explicit_date = parsed
                    # If the date is in the past, mark historical
                    now = datetime.now(timezone.utc)
                    if parsed < now:
                        status = "historical"
                        tense_confidence = 0.9
                    else:
                        status = "speculative"
                        tense_confidence = 0.9

            elif containing_sent is not None:
                status, tense_confidence = self._classify_tense(containing_sent)

                # Check for nearby dates in the same sentence
                for date_text, date_val in date_map.items():
                    if date_text in containing_sent.text:
                        explicit_date = date_val
                        break

            doc.temporal_contexts[entity_key] = TemporalContext(
                status=status,
                explicit_date=explicit_date,
                document_date=doc_date,
                tense_confidence=tense_confidence,
            )

        # Tag relationships too
        for rel in doc.extracted_relationships:
            rel_key = f"{rel.subject_text}-{rel.predicate}-{rel.object_text}"
            # Inherit the temporal qualifier if from LLM extraction
            if rel.temporal_qualifier:
                status = rel.temporal_qualifier
                tense_confidence = 0.8
            else:
                # Use the subject entity's temporal context as a proxy
                subj_key = f"{rel.subject_text}:{rel.subject_type}"
                subj_ctx = doc.temporal_contexts.get(subj_key)
                if subj_ctx:
                    status = subj_ctx.status
                    tense_confidence = subj_ctx.tense_confidence * 0.8
                else:
                    status = "unresolved"
                    tense_confidence = 0.0

            doc.temporal_contexts[rel_key] = TemporalContext(
                status=status,
                document_date=doc_date,
                tense_confidence=tense_confidence,
            )

        logger.debug(
            "temporal_tagging_complete",
            doc_id=doc.id,
            tagged_count=len(doc.temporal_contexts),
        )
        return doc

    def _classify_tense(self, sent) -> tuple[str, float]:
        """Classify the tense of a SpaCy sentence span.

        Uses POS tag heuristics on the root verb. Returns (status, confidence).
        """
        root = sent.root

        # Check for future/conditional indicators
        sent_tokens = {t.text.lower() for t in sent}
        if sent_tokens & _FUTURE_INDICATORS:
            if sent_tokens & _CONDITIONAL_INDICATORS:
                return "speculative", 0.6
            return "speculative", 0.7

        # Check root verb tense
        if root.tag_ in _PAST_TENSE_TAGS:
            return "historical", 0.7
        if root.tag_ in _PRESENT_TENSE_TAGS:
            return "current", 0.7

        # Check auxiliary verbs
        for child in root.children:
            if child.dep_ == "aux":
                if child.tag_ in _PAST_TENSE_TAGS:
                    return "historical", 0.6
                if child.text.lower() in _FUTURE_INDICATORS:
                    return "speculative", 0.6

        return "unresolved", 0.3

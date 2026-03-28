"""Stage 1 — Entity Extraction.

Runs NER over every document using SpaCy (en_core_web_trf for accuracy)
plus a supplementary regex/pattern layer for OSINT-specific entity types
that SpaCy misses: crypto wallets, IP addresses, domains, vessel IMO
numbers, aircraft tail numbers, military units, OFAC SDN numbers,
social media handles, and document/case reference numbers.
"""

from __future__ import annotations

import re


import structlog

from periphery.enrichment.models import ExtractedEntity, PipelineDocument
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# SpaCy entity types we care about
SPACY_ENTITY_TYPES = frozenset({
    "PERSON", "ORG", "GPE", "LOC", "DATE", "MONEY", "EVENT",
    "PRODUCT", "NORP", "FAC", "LAW",
})

# ── OSINT regex patterns ────────────────────────────────────────────────

_PATTERNS: dict[str, re.Pattern[str]] = {
    # Bitcoin addresses (legacy P2PKH/P2SH and bech32)
    "CRYPTO_WALLET_BTC": re.compile(
        r"\b(?:[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})\b"
    ),
    # Ethereum addresses
    "CRYPTO_WALLET_ETH": re.compile(r"\b0x[0-9a-fA-F]{40}\b"),
    # IPv4 addresses
    "IP_ADDRESS": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    ),
    # Domain names (simplified — avoids matching plain words)
    "DOMAIN": re.compile(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
        r"(?:com|org|net|gov|edu|mil|io|co|info|biz|name|pro|aero|museum"
        r"|int|coop|travel|xxx|[a-z]{2})\b",
        re.IGNORECASE,
    ),
    # IMO numbers for vessels (IMO followed by 7 digits)
    "VESSEL_IMO": re.compile(r"\bIMO\s*(\d{7})\b", re.IGNORECASE),
    # Aircraft tail/registration numbers (common formats)
    "AIRCRAFT_TAIL": re.compile(
        r"\b[A-Z]{1,2}-[A-Z]{1,5}\b"  # ICAO format e.g. N-12345, G-ABCD
        r"|\bN\d{1,5}[A-Z]{0,2}\b"    # US FAA format
    ),
    # Military unit designations
    "MILITARY_UNIT": re.compile(
        r"\b\d{1,3}(?:st|nd|rd|th)\s+"
        r"(?:Infantry|Armored|Airborne|Cavalry|Artillery|Marine|Brigade"
        r"|Division|Regiment|Battalion|Squadron|Wing|Fleet)\b",
        re.IGNORECASE,
    ),
    # OFAC SDN numbers
    "OFAC_SDN": re.compile(r"\bSDN\s*(?:No\.?\s*)?\d{4,}\b", re.IGNORECASE),
    # Social media handles (@username)
    "SOCIAL_MEDIA_HANDLE": re.compile(r"(?<!\w)@[A-Za-z_]\w{1,30}\b"),
    # Document/case reference numbers (generic patterns)
    "CASE_REFERENCE": re.compile(
        r"\b(?:Case|Docket|No\.|Ref\.?)\s*(?:#|:)?\s*"
        r"[A-Z0-9][\w\-/:.]{3,30}\b",
        re.IGNORECASE,
    ),
}


def _get_sentence_context(text: str, start: int, end: int) -> str:
    """Extract the sentence containing the span [start, end]."""
    # Walk backward to find sentence start
    sent_start = start
    while sent_start > 0 and text[sent_start - 1] not in ".!?\n":
        sent_start -= 1
    # Walk forward to find sentence end
    sent_end = end
    while sent_end < len(text) and text[sent_end] not in ".!?\n":
        sent_end += 1
    return text[sent_start:sent_end].strip()


class EntityExtractionStage(EnrichmentStage):
    """Stage 1: Extract entities via SpaCy NER + OSINT regex patterns."""

    _nlp = None  # class-level lazy SpaCy model

    def __init__(self, spacy_model: str = "en_core_web_trf") -> None:
        self._spacy_model_name = spacy_model

    @property
    def name(self) -> str:
        return "entity_extraction"

    def _get_nlp(self):
        """Lazy-load the SpaCy model (heavy, only load once)."""
        if EntityExtractionStage._nlp is None:
            import spacy

            try:
                EntityExtractionStage._nlp = spacy.load(self._spacy_model_name)
            except OSError:
                logger.warning(
                    "spacy_model_not_found",
                    model=self._spacy_model_name,
                    fallback="en_core_web_sm",
                )
                EntityExtractionStage._nlp = spacy.load("en_core_web_sm")
        return EntityExtractionStage._nlp

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Run SpaCy NER and regex patterns over the document."""
        text = doc.full_text
        if not text:
            return doc

        entities: list[ExtractedEntity] = []

        # SpaCy NER — also stores the parsed Doc for downstream stages to reuse
        spacy_entities, spacy_doc = self._extract_spacy(text)
        entities.extend(spacy_entities)
        doc.spacy_doc = spacy_doc

        # Regex/pattern layer
        entities.extend(self._extract_patterns(text))

        # Deduplicate overlapping spans (prefer SpaCy for overlaps)
        entities = self._deduplicate_entities(entities)

        doc.extracted_entities = entities
        logger.debug(
            "entities_extracted",
            doc_id=doc.id,
            spacy_count=sum(1 for e in entities if e.extraction_method == "spacy"),
            pattern_count=sum(1 for e in entities if e.extraction_method == "pattern"),
        )
        return doc

    def _extract_spacy(self, text: str) -> tuple[list[ExtractedEntity], object]:
        """Extract entities using SpaCy NER.

        Returns (entities, spacy_doc) so the Doc can be reused by later stages.
        """
        nlp = self._get_nlp()
        # Process with a character limit to avoid OOM on huge docs
        max_chars = 1_000_000
        doc = nlp(text[:max_chars])
        entities = []
        for ent in doc.ents:
            if ent.label_ not in SPACY_ENTITY_TYPES:
                continue
            entities.append(
                ExtractedEntity(
                    text=ent.text,
                    entity_type=ent.label_,
                    start_char=ent.start_char,
                    end_char=ent.end_char,
                    confidence=1.0 if ent.kb_id_ else 0.85,
                    extraction_method="spacy",
                    context_window=_get_sentence_context(
                        text, ent.start_char, ent.end_char
                    ),
                )
            )
        return entities, doc

    def _extract_patterns(self, text: str) -> list[ExtractedEntity]:
        """Extract OSINT-specific entities using regex patterns."""
        entities = []
        for entity_type, pattern in _PATTERNS.items():
            for match in pattern.finditer(text):
                entities.append(
                    ExtractedEntity(
                        text=match.group(0),
                        entity_type=entity_type,
                        start_char=match.start(),
                        end_char=match.end(),
                        confidence=1.0,
                        extraction_method="pattern",
                        context_window=_get_sentence_context(
                            text, match.start(), match.end()
                        ),
                    )
                )
        return entities

    def _deduplicate_entities(
        self, entities: list[ExtractedEntity]
    ) -> list[ExtractedEntity]:
        """Remove duplicate/overlapping entity spans.

        For overlapping spans, prefer SpaCy extractions over regex.
        For identical spans, keep the one with higher confidence.
        """
        if not entities:
            return entities

        # Sort by start position, then by confidence descending
        entities.sort(key=lambda e: (e.start_char, -e.confidence))

        result: list[ExtractedEntity] = []
        for ent in entities:
            # Check if this overlaps with any entity already accepted
            overlaps = False
            for accepted in result:
                if ent.start_char < accepted.end_char and ent.end_char > accepted.start_char:
                    overlaps = True
                    break
            if not overlaps:
                result.append(ent)

        return result

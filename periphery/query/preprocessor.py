"""Component 8 — Query Preprocessor.

Handles typo correction, abbreviation expansion, and session context
injection before the query reaches the intent parser.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from periphery.query.models import SessionState

logger = logging.getLogger(__name__)

# ── OSINT / Intelligence Abbreviation Table ──────────────────────────────

ABBREVIATIONS: dict[str, str] = {
    "IRGC": "Islamic Revolutionary Guard Corps",
    "OFAC": "Office of Foreign Assets Control",
    "ODNI": "Office of the Director of National Intelligence",
    "NSC": "National Security Council",
    "CENTCOM": "United States Central Command",
    "EUCOM": "United States European Command",
    "INDOPACOM": "United States Indo-Pacific Command",
    "AFRICOM": "United States Africa Command",
    "SOUTHCOM": "United States Southern Command",
    "OSINT": "Open Source Intelligence",
    "HUMINT": "Human Intelligence",
    "SIGINT": "Signals Intelligence",
    "GEOINT": "Geospatial Intelligence",
    "IMINT": "Imagery Intelligence",
    "PRC": "People's Republic of China",
    "DPRK": "Democratic People's Republic of Korea",
    "ROK": "Republic of Korea",
    "UAE": "United Arab Emirates",
    "KSA": "Kingdom of Saudi Arabia",
    "NATO": "North Atlantic Treaty Organization",
    "EU": "European Union",
    "BRICS": "BRICS (Brazil, Russia, India, China, South Africa)",
    "ASEAN": "Association of Southeast Asian Nations",
    "OPEC": "Organization of the Petroleum Exporting Countries",
    "SWIFT": "Society for Worldwide Interbank Financial Telecommunication",
    "AML": "Anti-Money Laundering",
    "KYC": "Know Your Customer",
    "SDN": "Specially Designated Nationals",
    "WMD": "Weapons of Mass Destruction",
    "CBRN": "Chemical, Biological, Radiological, Nuclear",
    "IED": "Improvised Explosive Device",
    "UAS": "Unmanned Aerial System",
    "UAV": "Unmanned Aerial Vehicle",
    "EEZ": "Exclusive Economic Zone",
    "SLOC": "Sea Lines of Communication",
    "FOB": "Forward Operating Base",
    "APT": "Advanced Persistent Threat",
    "C2": "Command and Control",
    "TTPs": "Tactics, Techniques, and Procedures",
    "IOC": "Indicator of Compromise",
    "CVE": "Common Vulnerabilities and Exposures",
    "NGO": "Non-Governmental Organization",
    "INGO": "International Non-Governmental Organization",
    "IDP": "Internally Displaced Person",
    "PMC": "Private Military Company",
    "PMF": "Popular Mobilization Forces",
    "SDF": "Syrian Democratic Forces",
    "HTS": "Hay'at Tahrir al-Sham",
    "JCPOA": "Joint Comprehensive Plan of Action",
    "AUKUS": "AUKUS (Australia, United Kingdom, United States)",
    "FONOP": "Freedom of Navigation Operation",
    "ADIZ": "Air Defense Identification Zone",
    "BRI": "Belt and Road Initiative",
    "CPEC": "China-Pakistan Economic Corridor",
    "LNG": "Liquefied Natural Gas",
}

# Word boundary pattern for abbreviation matching
_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(ABBREVIATIONS.keys(), key=len, reverse=True)) + r")\b"
)


class QueryPreprocessor:
    """Preprocesses analyst queries before intent parsing."""

    def __init__(self, entity_index: Any | None = None) -> None:
        self._entity_index = entity_index

    def preprocess(
        self,
        query_text: str,
        session: SessionState | None = None,
    ) -> tuple[str, str]:
        """Preprocess a query.

        Returns (processed_query, session_context_string).
        """
        processed = query_text

        # 1. Typo correction against entity index
        processed = self._correct_typos(processed)

        # 2. Abbreviation expansion
        processed = self._expand_abbreviations(processed)

        # 3. Build session context string
        session_context = self._build_session_context(session)

        return processed, session_context

    def _correct_typos(self, text: str) -> str:
        """Correct typos by matching against the entity resolution index."""
        if self._entity_index is None:
            return text

        try:
            from rapidfuzz import fuzz
        except ImportError:
            return text

        words = text.split()
        corrected = []
        for word in words:
            # Skip short words and common words
            if len(word) <= 3:
                corrected.append(word)
                continue

            # Try exact match first
            exact = self._entity_index.lookup_exact(word)
            if exact:
                corrected.append(word)
                continue

            # Try alias match
            alias = self._entity_index.lookup_alias(word)
            if alias:
                corrected.append(word)
                continue

            # Check if this might be a misspelled entity
            # Only correct if we have a very high confidence match
            best_name = None
            best_score = 0.0
            for etype in ["ORG", "GPE", "PERSON", "LOC"]:
                match, score = self._entity_index.lookup_fuzzy(word, etype)
                if match and score > best_score:
                    best_score = score
                    best_name = match.canonical_name

            if best_name and best_score >= 0.92:
                logger.debug("typo_correction: %s -> %s (%.2f)", word, best_name, best_score)
                corrected.append(best_name)
            else:
                corrected.append(word)

        return " ".join(corrected)

    def _expand_abbreviations(self, text: str) -> str:
        """Expand known OSINT/intelligence abbreviations."""
        def _replace(match: re.Match) -> str:
            abbrev = match.group(1)
            expansion = ABBREVIATIONS.get(abbrev, abbrev)
            return f"{abbrev} ({expansion})"

        return _ABBREV_PATTERN.sub(_replace, text)

    def _build_session_context(self, session: SessionState | None) -> str:
        """Build context string from previous queries in the session."""
        if session is None or not session.previous_queries:
            return ""

        parts = ["Previous queries in this session:"]
        for pq in session.previous_queries[-5:]:
            query = pq.get("query", "")
            summary = pq.get("summary", "")
            parts.append(f"  Q: {query}")
            if summary:
                parts.append(f"  A: {summary}")

        if session.bookmarked_entities:
            parts.append(f"\nBookmarked entities: {', '.join(session.bookmarked_entities[:10])}")

        if session.geographic_focus:
            parts.append(f"\nGeographic focus: {session.geographic_focus}")

        return "\n".join(parts)

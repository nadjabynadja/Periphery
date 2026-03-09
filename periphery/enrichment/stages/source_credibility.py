"""Stage 5 — Source Credibility Tagging.

Tags every document (and its extracted elements) with a credibility tier
based on the source configuration. The tiers are:

  tier_1: Wire services, government primary sources, court filings
  tier_2: Established journalism, major outlets, academic preprints
  tier_3: Niche/specialist sources, analyst blogs, trade publications
  tier_4: Unverified, social media, forums, Telegram channels
"""

from __future__ import annotations

import structlog

from periphery.enrichment.models import PipelineDocument, SourceCredibility
from periphery.enrichment.pipeline import EnrichmentStage

logger = structlog.get_logger(__name__)

# Default credibility tier mapping by source category
_CATEGORY_TIER_MAP: dict[str, int] = {
    # Tier 1: Primary authoritative sources
    "sanctions": 1,
    "government": 1,
    "CVE": 1,
    # Tier 2: Established journalism and academia
    "news": 2,
    "academic": 2,
    # Tier 3: Specialist/analyst sources
    "geopolitical": 3,
    "analysis": 3,
    "trade": 3,
    # Tier 4: Default for unknown
    "social": 4,
    "forum": 4,
    "telegram": 4,
}

# Override by specific source name for finer control
_SOURCE_TIER_OVERRIDES: dict[str, int] = {
    # Wire services → tier 1
    "Reuters Top News": 1,
    "Reuters World News": 1,
    "AP News Top Stories": 1,
    # Established outlets → tier 2
    "BBC World News": 2,
    "Al Jazeera": 2,
    "NYT World": 2,
    # Specialist → tier 3
    "Krebs on Security": 3,
    "Bleeping Computer": 3,
    "International Crisis Group": 3,
    "War on the Rocks": 3,
    "Foreign Affairs": 3,
    # Academic → tier 2
    "arXiv cs.AI": 2,
    "arXiv cs.CR (Cryptography & Security)": 2,
    "arXiv cs.CL (Computation & Language)": 2,
    "SSRN Recent Papers": 2,
}


class SourceCredibilityStage(EnrichmentStage):
    """Stage 5: Tag documents with source credibility tier."""

    def __init__(
        self,
        category_tiers: dict[str, int] | None = None,
        source_overrides: dict[str, int] | None = None,
    ) -> None:
        self._category_tiers = category_tiers or _CATEGORY_TIER_MAP
        self._source_overrides = source_overrides or _SOURCE_TIER_OVERRIDES

    @property
    def name(self) -> str:
        return "source_credibility"

    async def process(self, doc: PipelineDocument) -> PipelineDocument:
        """Tag the document with source credibility."""
        # Check source name overrides first, then category mapping
        tier = self._source_overrides.get(doc.source_name)
        if tier is None:
            tier = self._category_tiers.get(doc.source_category, 4)

        doc.source_credibility = SourceCredibility(
            source_credibility_tier=tier,
            source_name=doc.source_name or doc.source_feed,
            source_url=doc.source_feed,
            source_category=doc.source_category,
        )

        logger.debug(
            "source_credibility_tagged",
            doc_id=doc.id,
            source=doc.source_name,
            category=doc.source_category,
            tier=tier,
        )
        return doc

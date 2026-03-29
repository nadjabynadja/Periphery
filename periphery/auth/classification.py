"""Data classification framework for Periphery.

Provides a hierarchy of classification levels and helper functions for
determining access based on user clearance.
"""

from __future__ import annotations

from enum import Enum


class DataClassification(str, Enum):
    PUBLIC = "PUBLIC"
    PII = "PII"
    CUI = "CUI"
    PROPRIETARY = "PROPRIETARY"
    CLASSIFIED = "CLASSIFIED"


# Ordered from lowest to highest sensitivity
CLASSIFICATION_HIERARCHY = [
    DataClassification.PUBLIC,
    DataClassification.PII,
    DataClassification.CUI,
    DataClassification.PROPRIETARY,
    DataClassification.CLASSIFIED,
]

# All classification values as strings (for validation)
ALL_CLASSIFICATIONS = [c.value for c in DataClassification]

# Source type → default classification mapping
SOURCE_TYPE_CLASSIFICATIONS: dict[str, DataClassification] = {
    "nc_voter": DataClassification.PII,
    "fec_contributions": DataClassification.PII,
    "nc_campaign_finance": DataClassification.PII,
    "nc_parcels": DataClassification.PII,
    "irs_exempt_orgs": DataClassification.PUBLIC,
    "nc_sos_business": DataClassification.PUBLIC,
    "nc_rod": DataClassification.PII,
    "icij_offshore": DataClassification.PII,
    "ofac_sanctions": DataClassification.PUBLIC,
    "gdelt_doc": DataClassification.PUBLIC,
}


def highest_classification(classifications: list[DataClassification]) -> DataClassification:
    """Return the highest classification from a list."""
    if not classifications:
        return DataClassification.PUBLIC
    max_idx = max(CLASSIFICATION_HIERARCHY.index(c) for c in classifications)
    return CLASSIFICATION_HIERARCHY[max_idx]


def classification_allows(
    user_clearance: list[DataClassification] | list[str],
    data_classification: DataClassification | str,
) -> bool:
    """Check if a user's clearance list allows access to data at the given classification."""
    # Normalize to strings for comparison
    clearance_strs = [c.value if isinstance(c, DataClassification) else c for c in user_clearance]
    data_str = data_classification.value if isinstance(data_classification, DataClassification) else data_classification
    return data_str in clearance_strs


def classify_source_type(source_type: str) -> DataClassification:
    """Return the default classification for a given source type."""
    return SOURCE_TYPE_CLASSIFICATIONS.get(source_type, DataClassification.PUBLIC)

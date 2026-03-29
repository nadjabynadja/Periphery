"""Tests for the data classification framework."""

import pytest

from periphery.auth.classification import (
    ALL_CLASSIFICATIONS,
    CLASSIFICATION_HIERARCHY,
    DataClassification,
    classification_allows,
    classify_source_type,
    highest_classification,
)


class TestDataClassification:
    def test_enum_values(self):
        assert DataClassification.PUBLIC == "PUBLIC"
        assert DataClassification.PII == "PII"
        assert DataClassification.CUI == "CUI"
        assert DataClassification.PROPRIETARY == "PROPRIETARY"
        assert DataClassification.CLASSIFIED == "CLASSIFIED"

    def test_hierarchy_order(self):
        assert CLASSIFICATION_HIERARCHY[0] == DataClassification.PUBLIC
        assert CLASSIFICATION_HIERARCHY[-1] == DataClassification.CLASSIFIED

    def test_all_classifications(self):
        assert len(ALL_CLASSIFICATIONS) == 5
        assert "PUBLIC" in ALL_CLASSIFICATIONS
        assert "PII" in ALL_CLASSIFICATIONS


class TestHighestClassification:
    def test_empty_returns_public(self):
        assert highest_classification([]) == DataClassification.PUBLIC

    def test_single(self):
        assert highest_classification([DataClassification.PII]) == DataClassification.PII

    def test_multiple(self):
        result = highest_classification([
            DataClassification.PUBLIC,
            DataClassification.PII,
            DataClassification.CUI,
        ])
        assert result == DataClassification.CUI

    def test_all_public(self):
        result = highest_classification([DataClassification.PUBLIC, DataClassification.PUBLIC])
        assert result == DataClassification.PUBLIC

    def test_classified_wins(self):
        result = highest_classification([
            DataClassification.PUBLIC,
            DataClassification.CLASSIFIED,
            DataClassification.PII,
        ])
        assert result == DataClassification.CLASSIFIED


class TestClassificationAllows:
    def test_allows_matching(self):
        assert classification_allows(
            [DataClassification.PUBLIC, DataClassification.PII],
            DataClassification.PII,
        )

    def test_denies_missing(self):
        assert not classification_allows(
            [DataClassification.PUBLIC],
            DataClassification.PII,
        )

    def test_string_comparison(self):
        assert classification_allows(["PUBLIC", "PII"], "PII")
        assert not classification_allows(["PUBLIC"], "CLASSIFIED")

    def test_mixed_types(self):
        assert classification_allows(
            [DataClassification.PUBLIC, "PII"],
            DataClassification.PII,
        )


class TestClassifySourceType:
    def test_nc_voter(self):
        assert classify_source_type("nc_voter") == DataClassification.PII

    def test_icij(self):
        assert classify_source_type("icij_offshore") == DataClassification.PII

    def test_ofac(self):
        assert classify_source_type("ofac_sanctions") == DataClassification.PUBLIC

    def test_gdelt(self):
        assert classify_source_type("gdelt_doc") == DataClassification.PUBLIC

    def test_unknown_defaults_public(self):
        assert classify_source_type("unknown_source") == DataClassification.PUBLIC

    def test_rss_defaults_public(self):
        assert classify_source_type("") == DataClassification.PUBLIC

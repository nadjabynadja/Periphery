"""Tests for FEC Individual Contributions data source."""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from periphery.ingest.sources.base import make_document_id
from periphery.ingest.sources.fec_contributions import (
    FEC_FIELDS,
    FECContributionsSource,
    _build_contribution_content,
    _build_fec_url,
    _cycle_to_short,
    _parse_fec_line,
)
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fec_line(**overrides: str) -> str:
    """Build a pipe-delimited FEC line from field overrides."""
    defaults = {
        "CMTE_ID": "C00000001",
        "AMNDT_IND": "N",
        "RPT_TP": "Q3",
        "TRANSACTION_PGI": "P",
        "IMAGE_NUM": "202310010001",
        "TRANSACTION_TP": "15",
        "ENTITY_TP": "IND",
        "NAME": "DOE, JOHN",
        "CITY": "RALEIGH",
        "STATE": "NC",
        "ZIP_CODE": "27601",
        "EMPLOYER": "ACME CORP",
        "OCCUPATION": "ENGINEER",
        "TRANSACTION_DT": "10012023",
        "TRANSACTION_AMT": "500",
        "OTHER_ID": "",
        "TRAN_ID": "SA11AI.1234",
        "FILE_NUM": "1234567",
        "MEMO_CD": "",
        "MEMO_TEXT": "",
        "SUB_ID": "4100120231234567890",
    }
    defaults.update(overrides)
    fields = [defaults[f] for f in FEC_FIELDS]
    return "|".join(fields) + "\n"


def _make_fec_zip(lines: list[str], tmp_dir: Path) -> Path:
    """Create a temporary ZIP containing itcont.txt with the given lines."""
    zip_path = tmp_dir / "indiv24.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        content = "".join(lines)
        zf.writestr("itcont.txt", content)
    return zip_path


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestFECParsing:
    def test_parse_valid_line(self):
        line = _make_fec_line()
        row = _parse_fec_line(line)
        assert row is not None
        assert row["CMTE_ID"] == "C00000001"
        assert row["NAME"] == "DOE, JOHN"
        assert row["STATE"] == "NC"
        assert row["TRANSACTION_AMT"] == "500"
        assert row["SUB_ID"] == "4100120231234567890"

    def test_parse_short_line_returns_none(self):
        row = _parse_fec_line("too|few|fields")
        assert row is None

    def test_parse_strips_whitespace(self):
        line = _make_fec_line(NAME="  DOE, JANE  ", CITY="  DURHAM  ")
        row = _parse_fec_line(line)
        assert row["NAME"] == "DOE, JANE"
        assert row["CITY"] == "DURHAM"

    def test_build_contribution_content(self):
        row = {
            "NAME": "DOE, JOHN",
            "CITY": "RALEIGH",
            "STATE": "NC",
            "ZIP_CODE": "27601",
            "EMPLOYER": "ACME CORP",
            "OCCUPATION": "ENGINEER",
            "CMTE_ID": "C00000001",
            "TRANSACTION_AMT": "500",
            "TRANSACTION_DT": "10012023",
            "TRANSACTION_TP": "15",
            "ENTITY_TP": "IND",
            "RPT_TP": "Q3",
        }
        content = _build_contribution_content(row)
        assert "Contributor: DOE, JOHN" in content
        assert "RALEIGH, NC 27601" in content
        assert "ACME CORP" in content
        assert "$500" in content

    def test_cycle_to_short(self):
        assert _cycle_to_short("2024") == "24"
        assert _cycle_to_short("2022") == "22"
        assert _cycle_to_short("2020") == "20"

    def test_build_fec_url(self):
        url = _build_fec_url("2024")
        assert "indiv24.zip" in url
        assert "2024" in url


# ---------------------------------------------------------------------------
# Filtering tests
# ---------------------------------------------------------------------------

class TestFECFiltering:
    def test_state_filter(self):
        """Only NC records should be included."""
        lines = [
            _make_fec_line(STATE="NC", SUB_ID="111", NAME="NC PERSON"),
            _make_fec_line(STATE="CA", SUB_ID="222", NAME="CA PERSON"),
            _make_fec_line(STATE="NC", SUB_ID="333", NAME="ANOTHER NC"),
            _make_fec_line(STATE="NY", SUB_ID="444", NAME="NY PERSON"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = _make_fec_zip(lines, tmp_path)

            source = FECContributionsSource(
                data_dir=tmp,
                cycles=["2024"],
                state_filter="NC",
            )

            emitted: list[IngestedDocument] = []
            source._on_documents = lambda docs: emitted.extend(docs)

            count = source._process_cycle_sync(zip_path, "2024")
            assert count == 2
            assert len(emitted) == 2
            assert all("NC" in d.content for d in emitted)

    def test_empty_sub_id_skipped(self):
        lines = [
            _make_fec_line(STATE="NC", SUB_ID="", NAME="NO SUB ID"),
            _make_fec_line(STATE="NC", SUB_ID="555", NAME="HAS SUB ID"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = _make_fec_zip(lines, tmp_path)

            source = FECContributionsSource(data_dir=tmp, cycles=["2024"])

            emitted: list[IngestedDocument] = []
            source._on_documents = lambda docs: emitted.extend(docs)

            count = source._process_cycle_sync(zip_path, "2024")
            assert count == 1


# ---------------------------------------------------------------------------
# Document building tests
# ---------------------------------------------------------------------------

class TestFECDocumentBuilding:
    def test_document_structure(self):
        lines = [_make_fec_line()]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = _make_fec_zip(lines, tmp_path)

            source = FECContributionsSource(data_dir=tmp, cycles=["2024"])

            emitted: list[IngestedDocument] = []
            source._on_documents = lambda docs: emitted.extend(docs)

            source._process_cycle_sync(zip_path, "2024")
            assert len(emitted) == 1

            doc = emitted[0]
            assert doc.source_feed == "FEC Individual Contributions"
            assert doc.source_category == "campaign_finance"
            assert doc.source_credibility_tier == 1
            assert doc.content_quality == "full"
            assert doc.data_classification == "PII"
            assert "DOE, JOHN" in doc.title
            assert "$500" in doc.title
            assert "C00000001" in doc.title
            assert doc.metadata["source_type"] == "fec_contributions"
            assert doc.metadata["cycle"] == "2024"
            assert doc.metadata["SUB_ID"] == "4100120231234567890"

    def test_document_id_deterministic(self):
        sub_id = "4100120231234567890"
        id1 = make_document_id("fec_contributions", sub_id)
        id2 = make_document_id("fec_contributions", sub_id)
        assert id1 == id2

    def test_document_url_contains_name_and_state(self):
        lines = [_make_fec_line(NAME="DOE, JOHN")]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = _make_fec_zip(lines, tmp_path)

            source = FECContributionsSource(data_dir=tmp, cycles=["2024"], state_filter="NC")

            emitted: list[IngestedDocument] = []
            source._on_documents = lambda docs: emitted.extend(docs)

            source._process_cycle_sync(zip_path, "2024")
            doc = emitted[0]
            assert "contributor_name=" in doc.url
            assert "contributor_state=NC" in doc.url


# ---------------------------------------------------------------------------
# Batch emit tests
# ---------------------------------------------------------------------------

class TestFECBatchEmit:
    def test_batching(self):
        """Documents are emitted in batches of batch_size."""
        lines = [_make_fec_line(SUB_ID=str(i)) for i in range(25)]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = _make_fec_zip(lines, tmp_path)

            source = FECContributionsSource(
                data_dir=tmp,
                cycles=["2024"],
                batch_size=10,
            )

            emit_calls: list[list[IngestedDocument]] = []
            source._on_documents = lambda docs: emit_calls.append(list(docs))

            count = source._process_cycle_sync(zip_path, "2024")
            assert count == 25
            # Should be 3 batches: 10, 10, 5
            assert len(emit_calls) == 3
            assert len(emit_calls[0]) == 10
            assert len(emit_calls[1]) == 10
            assert len(emit_calls[2]) == 5


# ---------------------------------------------------------------------------
# Constructor / config tests
# ---------------------------------------------------------------------------

class TestFECConfig:
    def test_defaults(self):
        source = FECContributionsSource()
        assert source.name == "fec_contributions"
        assert source.category == "campaign_finance"
        assert source._state_filter == "NC"
        assert source._cycles == ["2024"]
        assert source._batch_size == 10000
        assert source.default_poll_interval == 604800

    def test_custom_config(self):
        source = FECContributionsSource(
            data_dir="/tmp/fec",
            cycles=["2024", "2022"],
            batch_size=5000,
            state_filter="VA",
            poll_interval=86400,
            enabled=False,
        )
        assert source._state_filter == "VA"
        assert source._cycles == ["2024", "2022"]
        assert source._batch_size == 5000
        assert source.poll_interval == 86400
        assert source.enabled is False

    def test_missing_zip_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = FECContributionsSource(data_dir=tmp)
            count = source._process_cycle_sync(Path(tmp) / "nonexistent.zip", "2024")
            assert count == 0

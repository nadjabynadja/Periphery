"""Tests for NC Voter Registration data source."""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from periphery.ingest.sources.base import make_document_id
from periphery.ingest.sources.nc_voter import (
    NCVoterSource,
    NCVOTER_COLUMNS,
    NCVHIS_COLUMNS,
    _build_voter_content,
    _build_history_record,
    _parse_row,
)
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

# Tab-delimited voter registration row (67 fields matching NCVOTER_COLUMNS)
_SAMPLE_VOTER_FIELDS = [
    "92",            # county_id
    "WAKE",          # county_desc
    "000123456",     # voter_reg_num
    "AA12345",       # ncid
    "DOE",           # last_name
    "JANE",          # first_name
    "M",             # middle_name
    "",              # name_suffix_lbl
    "A",             # status_cd
    "ACTIVE",        # voter_status_desc
    "AV",            # reason_cd
    "VERIFIED",      # voter_status_reason_desc
    "123 MAIN ST",   # res_street_address
    "RALEIGH",       # res_city_desc
    "NC",            # state_cd
    "27601",         # zip_code
    "",              # mail_addr1
    "",              # mail_addr2
    "",              # mail_addr3
    "",              # mail_addr4
    "",              # mail_city
    "",              # mail_state
    "",              # mail_zipcode
    "9195551234",    # full_phone_number
    "N",             # confidential_ind
    "01/15/2010",    # registr_dt
    "W",             # race_code
    "NL",            # ethnic_code
    "DEM",           # party_cd
    "F",             # gender_code
    "1985",          # birth_year
    "41",            # age_at_year_end
    "NC",            # birth_state
    "",              # drivers_lic
    "",              # ssn
    "",              # no_dl_ssn_chkbx
    "",              # hava_id_req
    "01-01",         # precinct_abbrv
    "PRECINCT 01",   # precinct_desc
    "RAL",           # municipality_abbrv
    "RALEIGH",       # municipality_desc
    "",              # ward_abbrv
    "",              # ward_desc
    "02",            # cong_dist_abbrv
    "10A",           # super_court_abbrv
    "10",            # judic_dist_abbrv
    "15",            # nc_senate_abbrv
    "36",            # nc_house_abbrv
    "D1",            # county_commiss_abbrv
    "DISTRICT 1",    # county_commiss_desc
    "T1",            # township_abbrv
    "TOWNSHIP 1",    # township_desc
    "WK",            # school_dist_abbrv
    "WAKE COUNTY",   # school_dist_desc
    "",              # fire_dist_abbrv
    "",              # fire_dist_desc
    "",              # water_dist_abbrv
    "",              # water_dist_desc
    "",              # sewer_dist_abbrv
    "",              # sewer_dist_desc
    "",              # sanit_dist_abbrv
    "",              # sanit_dist_desc
    "",              # rescue_dist_abbrv
    "",              # rescue_dist_desc
    "",              # munic_dist_abbrv
    "",              # munic_dist_desc
    "",              # dist_1_abbrv
    "",              # dist_1_desc
    "01-01",         # vtd_abbrv
    "VTD 01-01",     # vtd_desc
]

_SAMPLE_VOTER_LINE = "\t".join(_SAMPLE_VOTER_FIELDS) + "\n"

# Confidential voter
_CONFIDENTIAL_FIELDS = list(_SAMPLE_VOTER_FIELDS)
_CONFIDENTIAL_FIELDS[24] = "Y"  # confidential_ind
_CONFIDENTIAL_FIELDS[3] = "CC99999"  # different ncid
_CONFIDENTIAL_LINE = "\t".join(_CONFIDENTIAL_FIELDS) + "\n"

# Voter history row (15 fields matching NCVHIS_COLUMNS)
_SAMPLE_HISTORY_FIELDS = [
    "92",            # county_id
    "WAKE",          # county_desc
    "000123456",     # voter_reg_num
    "11/03/2020",    # election_lbl
    "11/03/2020 GENERAL",  # election_desc
    "ABSENTEE ONESTOP",    # voting_method
    "DEM",           # voted_party_cd
    "DEMOCRATIC",    # voted_party_desc
    "01-01",         # pct_label
    "PRECINCT 01",   # pct_description
    "AA12345",       # ncid
    "92",            # voted_county_id
    "WAKE",          # voted_county_desc
    "01-01",         # vtd_label
    "VTD 01-01",     # vtd_description
]

_SAMPLE_HISTORY_LINE = "\t".join(_SAMPLE_HISTORY_FIELDS) + "\n"

_SAMPLE_HISTORY_2_FIELDS = list(_SAMPLE_HISTORY_FIELDS)
_SAMPLE_HISTORY_2_FIELDS[3] = "03/03/2020"
_SAMPLE_HISTORY_2_FIELDS[4] = "03/03/2020 PRIMARY"
_SAMPLE_HISTORY_2_FIELDS[5] = "IN-PERSON"
_SAMPLE_HISTORY_2_LINE = "\t".join(_SAMPLE_HISTORY_2_FIELDS) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_zip(tmp_dir: Path, filename: str, content_name: str, lines: list[str], header: str) -> Path:
    """Create a test ZIP file with a single tab-delimited text file."""
    zip_path = tmp_dir / filename
    with zipfile.ZipFile(zip_path, "w") as zf:
        data = header + "\n" + "".join(lines)
        zf.writestr(content_name, data)
    return zip_path


# ---------------------------------------------------------------------------
# Tests: field parsing
# ---------------------------------------------------------------------------

class TestFieldParsing:
    def test_parse_voter_row(self):
        row = _parse_row(_SAMPLE_VOTER_LINE, NCVOTER_COLUMNS)
        assert row is not None
        assert row["ncid"] == "AA12345"
        assert row["first_name"] == "JANE"
        assert row["last_name"] == "DOE"
        assert row["party_cd"] == "DEM"
        assert row["county_desc"] == "WAKE"
        assert row["confidential_ind"] == "N"
        assert row["res_street_address"] == "123 MAIN ST"
        assert row["zip_code"] == "27601"

    def test_parse_history_row(self):
        row = _parse_row(_SAMPLE_HISTORY_LINE, NCVHIS_COLUMNS)
        assert row is not None
        assert row["ncid"] == "AA12345"
        assert row["election_lbl"] == "11/03/2020"
        assert row["voting_method"] == "ABSENTEE ONESTOP"
        assert row["voted_party_cd"] == "DEM"

    def test_parse_row_too_few_fields(self):
        short_line = "field1\tfield2\n"
        row = _parse_row(short_line, NCVOTER_COLUMNS)
        assert row is None

    def test_parse_row_strips_whitespace(self):
        fields = list(_SAMPLE_VOTER_FIELDS)
        fields[5] = "  JANE  "  # first_name with extra whitespace
        line = "\t".join(fields) + "\n"
        row = _parse_row(line, NCVOTER_COLUMNS)
        assert row["first_name"] == "JANE"


# ---------------------------------------------------------------------------
# Tests: confidential_ind preserved in metadata
# ---------------------------------------------------------------------------

class TestConfidentialField:
    def test_confidential_voter_included(self):
        """Confidential records are ingested, with confidential_ind in metadata."""
        source = NCVoterSource(data_dir="/tmp/test", enabled=False)
        row = _parse_row(_CONFIDENTIAL_LINE, NCVOTER_COLUMNS)
        assert row is not None
        doc = source._build_document(row, row["ncid"], [])
        assert doc.metadata["confidential_ind"] == "Y"

    def test_non_confidential_voter_field(self):
        source = NCVoterSource(data_dir="/tmp/test", enabled=False)
        row = _parse_row(_SAMPLE_VOTER_LINE, NCVOTER_COLUMNS)
        doc = source._build_document(row, row["ncid"], [])
        assert doc.metadata["confidential_ind"] == "N"


# ---------------------------------------------------------------------------
# Tests: document ID generation
# ---------------------------------------------------------------------------

class TestDocumentId:
    def test_deterministic_id(self):
        id1 = make_document_id("nc_voter", "AA12345")
        id2 = make_document_id("nc_voter", "AA12345")
        assert id1 == id2
        assert len(id1) == 24

    def test_different_ncids_different_ids(self):
        id1 = make_document_id("nc_voter", "AA12345")
        id2 = make_document_id("nc_voter", "BB99999")
        assert id1 != id2


# ---------------------------------------------------------------------------
# Tests: voting history linking
# ---------------------------------------------------------------------------

class TestVotingHistoryLinking:
    def test_build_history_record(self):
        row = _parse_row(_SAMPLE_HISTORY_LINE, NCVHIS_COLUMNS)
        record = _build_history_record(row)
        assert record["election_lbl"] == "11/03/2020"
        assert record["election_desc"] == "11/03/2020 GENERAL"
        assert record["voting_method"] == "ABSENTEE ONESTOP"
        assert record["voted_party_cd"] == "DEM"
        assert record["voted_county_desc"] == "WAKE"

    def test_history_map_built_from_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            header = "\t".join(NCVHIS_COLUMNS)
            history_zip = _create_test_zip(
                tmp_path,
                "ncvhis_Statewide.zip",
                "ncvhis_Statewide.txt",
                [_SAMPLE_HISTORY_LINE, _SAMPLE_HISTORY_2_LINE],
                header,
            )

            source = NCVoterSource(data_dir=tmp, enabled=False)
            history_map = source._build_history_map(history_zip)

            assert "AA12345" in history_map
            assert len(history_map["AA12345"]) == 2
            elections = [h["election_lbl"] for h in history_map["AA12345"]]
            assert "11/03/2020" in elections
            assert "03/03/2020" in elections


# ---------------------------------------------------------------------------
# Tests: content field formatting
# ---------------------------------------------------------------------------

class TestContentFormatting:
    def test_content_structure(self):
        row = _parse_row(_SAMPLE_VOTER_LINE, NCVOTER_COLUMNS)
        content = _build_voter_content(row, 2)

        assert "Voter: JANE M DOE" in content
        assert "Party: DEM | Status: ACTIVE" in content
        assert "County: WAKE | Precinct: PRECINCT 01" in content
        assert "123 MAIN ST, RALEIGH, NC 27601" in content
        assert "CD-02, SD-15, HD-36" in content
        assert "Registered: 01/15/2010" in content
        assert "Birth Year: 1985" in content
        assert "Race: W | Ethnicity: NL | Gender: F" in content
        assert "Elections Voted: 2" in content

    def test_content_with_zero_history(self):
        row = _parse_row(_SAMPLE_VOTER_LINE, NCVOTER_COLUMNS)
        content = _build_voter_content(row, 0)
        assert "Elections Voted: 0" in content


# ---------------------------------------------------------------------------
# Tests: metadata structure
# ---------------------------------------------------------------------------

class TestMetadataStructure:
    def test_document_metadata(self):
        source = NCVoterSource(data_dir="/tmp/test", enabled=False)
        row = _parse_row(_SAMPLE_VOTER_LINE, NCVOTER_COLUMNS)
        history = [
            {"election_lbl": "11/03/2020", "election_desc": "GENERAL",
             "voting_method": "IN-PERSON", "voted_party_cd": "DEM",
             "voted_county_desc": "WAKE"},
        ]
        doc = source._build_document(row, "AA12345", history)

        assert isinstance(doc, IngestedDocument)
        assert doc.id == make_document_id("nc_voter", "AA12345")
        assert doc.source_feed == "NC Voter Registration"
        assert doc.source_category == "voter_registration"
        assert doc.source_credibility_tier == 1
        assert doc.content_quality == "full"
        assert "JANE DOE" in doc.title
        assert "DEM" in doc.title
        assert "WAKE" in doc.title
        assert "AA12345" in doc.url

        meta = doc.metadata
        assert meta["source_type"] == "nc_voter"
        assert meta["county_desc"] == "WAKE"
        assert meta["party_cd"] == "DEM"
        assert meta["ncid"] == "AA12345"
        assert len(meta["voting_history"]) == 1
        assert meta["voting_history"][0]["election_lbl"] == "11/03/2020"

        # Sensitive fields should be stripped
        assert "drivers_lic" not in meta
        assert "ssn" not in meta


# ---------------------------------------------------------------------------
# Tests: batch yielding
# ---------------------------------------------------------------------------

class TestBatchYielding:
    def test_process_voters_batching(self):
        """Test that voters are processed and batched correctly from ZIP."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Create voter ZIP with 25 records (5 normal + 20 more)
            voter_header = "\t".join(NCVOTER_COLUMNS)
            voter_lines = []
            for i in range(25):
                fields = list(_SAMPLE_VOTER_FIELDS)
                fields[3] = f"NC{i:05d}"  # unique ncid
                fields[5] = f"VOTER{i}"   # unique first_name
                voter_lines.append("\t".join(fields) + "\n")

            # Add one confidential record
            conf_fields = list(_SAMPLE_VOTER_FIELDS)
            conf_fields[3] = "CONF001"
            conf_fields[24] = "Y"
            voter_lines.append("\t".join(conf_fields) + "\n")

            voter_zip = _create_test_zip(
                tmp_path, "ncvoter_Statewide.zip", "ncvoter_Statewide.txt",
                voter_lines, voter_header,
            )

            # Create empty history ZIP
            history_header = "\t".join(NCVHIS_COLUMNS)
            history_zip = _create_test_zip(
                tmp_path, "ncvhis_Statewide.zip", "ncvhis_Statewide.txt",
                [], history_header,
            )

            # Process with small batch size
            source = NCVoterSource(data_dir=tmp, batch_size=10, enabled=False)
            emitted_batches: list[list[IngestedDocument]] = []

            def mock_emit(docs):
                emitted_batches.append(list(docs))

            source._on_documents = mock_emit

            history_map = source._build_history_map(history_zip)
            count = source._process_voters_sync(voter_zip, history_map)

            # 25 + 1 confidential = 26 records (all ingested)
            assert count == 26
            # With batch_size=10: batches of 10, 10, 6
            assert len(emitted_batches) == 3
            assert len(emitted_batches[0]) == 10
            assert len(emitted_batches[1]) == 10
            assert len(emitted_batches[2]) == 6

    def test_source_properties(self):
        source = NCVoterSource(data_dir="/tmp/test", enabled=False)
        assert source.name == "nc_voter"
        assert source.category == "voter_registration"
        assert source.default_poll_interval == 604800

    def test_custom_batch_size(self):
        source = NCVoterSource(data_dir="/tmp/test", batch_size=5000, enabled=False)
        assert source._batch_size == 5000

    def test_health_report(self):
        source = NCVoterSource(data_dir="/tmp/test", enabled=True)
        health = source.health()
        assert health["name"] == "nc_voter"
        assert health["enabled"] is True
        assert health["total_fetched"] == 0

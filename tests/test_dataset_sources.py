"""Tests for ICIJ Offshore Leaks and OFAC Sanctions data source integrations."""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from periphery.config import Settings
from periphery.ingest.sources.base import make_document_id
from periphery.ingest.sources.factory import build_sources
from periphery.ingest.sources.icij_offshore import (
    ICIJOffshoreSource,
    _build_entity_content,
    _content_hash,
)
from periphery.ingest.sources.ofac_sanctions import (
    OFACSanctionsSource,
    _build_sdn_content,
    _clean,
    _parse_pipe_csv,
    _SDN_FIELDS,
    _ADD_FIELDS,
    _ALT_FIELDS,
)
from periphery.rss_ingest.models import IngestedDocument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_icij_zip(entities: list[dict], officers: list[dict], relationships: list[dict]) -> bytes:
    """Build an in-memory ICIJ-style ZIP with sample CSV data."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # nodes-entities.csv
        if entities:
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=list(entities[0].keys()))
            writer.writeheader()
            writer.writerows(entities)
            zf.writestr("nodes-entities.csv", out.getvalue())

        # nodes-officers.csv
        if officers:
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=list(officers[0].keys()))
            writer.writeheader()
            writer.writerows(officers)
            zf.writestr("nodes-officers.csv", out.getvalue())

        # relationships.csv
        if relationships:
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=list(relationships[0].keys()))
            writer.writeheader()
            writer.writerows(relationships)
            zf.writestr("relationships.csv", out.getvalue())

    return buf.getvalue()


def _make_sdn_line(
    ent_num: str,
    name: str,
    sdn_type: str = "Individual",
    program: str = "SDGT",
    remarks: str = "",
) -> str:
    fields = [ent_num, name, sdn_type, program, "", "", "", "", "", "", "", remarks]
    return "|".join(fields)


SAMPLE_ENTITIES = [
    {
        "node_id": "10000001",
        "name": "Acme Offshore Ltd",
        "original_name": "",
        "jurisdiction": "VGB",
        "jurisdiction_description": "British Virgin Islands",
        "incorporation_date": "2010-03-15",
        "inactivation_date": "",
        "struck_off_date": "",
        "status": "Active",
        "company_type": "Limited Company",
        "service_provider": "Mossack Fonseca",
        "countries": "VGB;GBR",
        "sourceID": "panama-papers",
        "valid_until": "",
        "note": "",
    },
    {
        "node_id": "10000002",
        "name": "Paradise Holdings Corp",
        "original_name": "",
        "jurisdiction": "BMU",
        "jurisdiction_description": "Bermuda",
        "incorporation_date": "2015-07-22",
        "inactivation_date": "2020-01-01",
        "struck_off_date": "",
        "status": "Inactive",
        "company_type": "Trust",
        "service_provider": "Appleby",
        "countries": "BMU",
        "sourceID": "paradise-papers",
        "valid_until": "",
        "note": "",
    },
]

SAMPLE_OFFICERS = [
    {
        "node_id": "20000001",
        "name": "John Q. Smith",
        "original_name": "",
        "countries": "GBR",
        "valid_until": "2021-12-31",
        "note": "Director",
        "sourceID": "panama-papers",
    }
]

SAMPLE_RELATIONSHIPS = [
    {
        "node_id_1": "20000001",
        "node_id_2": "10000001",
        "rel_type": "officer_of",
        "link": "director of",
        "start_date": "2010-03-15",
        "end_date": "",
        "sourceID": "panama-papers",
    }
]


# ---------------------------------------------------------------------------
# ICIJ helper / unit tests
# ---------------------------------------------------------------------------


class TestICIJHelpers:
    def test_build_entity_content_entity(self):
        row = {
            "name": "Acme Ltd",
            "jurisdiction": "VGB",
            "jurisdiction_description": "British Virgin Islands",
            "incorporation_date": "2010-01-01",
            "status": "Active",
            "sourceID": "panama-papers",
            "countries": "VGB",
            "company_type": "Limited Company",
            "service_provider": "Provider A",
            "inactivation_date": "",
            "struck_off_date": "",
            "valid_until": "",
            "note": "",
        }
        content = _build_entity_content(row, "entity")
        assert "Acme Ltd" in content
        assert "British Virgin Islands" in content
        assert "2010-01-01" in content
        assert "Active" in content
        assert "panama-papers" in content

    def test_build_entity_content_officer(self):
        row = {
            "name": "Jane Doe",
            "countries": "USA",
            "valid_until": "2025-12-31",
            "note": "Beneficiary",
            "sourceID": "pandora-papers",
        }
        content = _build_entity_content(row, "officer")
        assert "Jane Doe" in content
        assert "USA" in content
        assert "pandora-papers" in content

    def test_build_entity_content_intermediary(self):
        row = {
            "name": "Big Law Firm LLP",
            "countries": "CHE",
            "status": "Active",
            "valid_until": "",
            "note": "",
            "sourceID": "paradise-papers",
        }
        content = _build_entity_content(row, "intermediary")
        assert "Big Law Firm LLP" in content
        assert "CHE" in content

    def test_content_hash_deterministic(self):
        text = "Name: Acme Ltd\nJurisdiction: VGB"
        h1 = _content_hash(text)
        h2 = _content_hash(text)
        assert h1 == h2
        assert len(h1) == 16

    def test_content_hash_different_for_different_content(self):
        h1 = _content_hash("content A")
        h2 = _content_hash("content B")
        assert h1 != h2


# ---------------------------------------------------------------------------
# ICIJOffshoreSource parsing tests (mocked download)
# ---------------------------------------------------------------------------


class TestICIJOffshoreSource:
    def _make_source(self, node_types=None, tmp_path=None) -> ICIJOffshoreSource:
        return ICIJOffshoreSource(
            enabled=True,
            node_types=node_types or ["entities", "officers"],
            data_dir=str(tmp_path or "/tmp"),
        )

    def test_parse_zip_entities(self, tmp_path):
        source = self._make_source(node_types=["entities"], tmp_path=tmp_path)
        zip_bytes = _make_icij_zip(SAMPLE_ENTITIES, [], [])
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(zip_bytes)

        docs = source._parse_zip(zip_path)

        assert len(docs) == 2
        titles = {d.title for d in docs}
        assert "Acme Offshore Ltd" in titles
        assert "Paradise Holdings Corp" in titles

    def test_parse_zip_entity_fields(self, tmp_path):
        source = self._make_source(node_types=["entities"], tmp_path=tmp_path)
        zip_bytes = _make_icij_zip(SAMPLE_ENTITIES, [], [])
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(zip_bytes)

        docs = source._parse_zip(zip_path)
        acme = next(d for d in docs if d.title == "Acme Offshore Ltd")

        assert acme.source_feed == "ICIJ Offshore Leaks"
        assert acme.source_category == "sanctions_financial"
        assert "British Virgin Islands" in acme.content
        assert "panama-papers" in acme.content
        assert acme.metadata["node_type"] == "entity"
        assert acme.metadata["source_type"] == "icij_offshore"
        assert "VGB" in acme.metadata.get("country_codes", [])

    def test_parse_zip_officers(self, tmp_path):
        source = self._make_source(node_types=["officers"], tmp_path=tmp_path)
        zip_bytes = _make_icij_zip([], SAMPLE_OFFICERS, [])
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(zip_bytes)

        docs = source._parse_zip(zip_path)

        assert len(docs) == 1
        assert docs[0].title == "John Q. Smith"
        assert docs[0].metadata["node_type"] == "officer"

    def test_parse_zip_relationships_embedded(self, tmp_path):
        source = self._make_source(
            node_types=["entities", "officers"], tmp_path=tmp_path
        )
        zip_bytes = _make_icij_zip(SAMPLE_ENTITIES, SAMPLE_OFFICERS, SAMPLE_RELATIONSHIPS)
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(zip_bytes)

        docs = source._parse_zip(zip_path)
        acme = next((d for d in docs if d.title == "Acme Offshore Ltd"), None)
        officer = next((d for d in docs if d.title == "John Q. Smith"), None)

        assert acme is not None
        assert officer is not None
        # Both entities involved in the relationship should have it in metadata
        assert "relationships" in acme.metadata
        assert any(
            r["rel_type"] == "officer_of" for r in acme.metadata["relationships"]
        )
        assert "relationships" in officer.metadata

    def test_deduplication_skips_unchanged(self, tmp_path):
        source = self._make_source(node_types=["entities"], tmp_path=tmp_path)
        zip_bytes = _make_icij_zip(SAMPLE_ENTITIES, [], [])
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(zip_bytes)

        docs1 = source._parse_zip(zip_path)
        docs2 = source._parse_zip(zip_path)  # same content, should all be skipped

        assert len(docs1) == 2
        assert len(docs2) == 0  # all hashes already seen

    def test_document_id_deterministic(self, tmp_path):
        source1 = self._make_source(node_types=["entities"], tmp_path=tmp_path)
        source2 = self._make_source(node_types=["entities"], tmp_path=tmp_path)
        zip_bytes = _make_icij_zip([SAMPLE_ENTITIES[0]], [], [])
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(zip_bytes)

        docs1 = source1._parse_zip(zip_path)
        docs2 = source2._parse_zip(zip_path)

        assert docs1[0].id == docs2[0].id

    def test_source_name_and_category(self):
        source = ICIJOffshoreSource()
        assert source.name == "icij_offshore"
        assert source.category == "sanctions_financial"
        assert source.default_poll_interval == 604800

    @pytest.mark.asyncio
    async def test_fetch_calls_download_and_parse(self, tmp_path):
        source = self._make_source(node_types=["entities"], tmp_path=tmp_path)
        zip_bytes = _make_icij_zip(SAMPLE_ENTITIES, [], [])

        # Mock the download to write sample zip to disk
        async def fake_download(session, dest):
            dest.write_bytes(zip_bytes)

        with patch.object(source, "_download_zip", side_effect=fake_download):
            mock_session = AsyncMock()
            docs = await source.fetch(mock_session)

        assert len(docs) == 2


# ---------------------------------------------------------------------------
# OFAC helper / unit tests
# ---------------------------------------------------------------------------


class TestOFACHelpers:
    def test_clean_strips_placeholder(self):
        assert _clean("-0-") == ""
        assert _clean(" -0- ") == ""
        assert _clean("RUSSIA") == "RUSSIA"
        assert _clean('  "ACME"  ') == "ACME"

    def test_parse_pipe_csv_basic(self):
        line = "1234|SMITH, JOHN|Individual|SDGT|||||||"
        rows = _parse_pipe_csv(line, _SDN_FIELDS)
        assert len(rows) == 1
        assert rows[0]["ent_num"] == "1234"
        assert rows[0]["SDN_Name"] == "SMITH, JOHN"
        assert rows[0]["SDN_Type"] == "Individual"
        assert rows[0]["Program"] == "SDGT"

    def test_parse_pipe_csv_multiple_rows(self):
        lines = "\n".join([
            "1|ENTITY ONE|Entity|IRAN|||||||",
            "2|VESSEL ALPHA|Vessel|DPRK-SHIPPING|||||||",
            "3|PERSON B|Individual|SDGT|||||||",
        ])
        rows = _parse_pipe_csv(lines, _SDN_FIELDS)
        assert len(rows) == 3
        assert rows[1]["SDN_Name"] == "VESSEL ALPHA"

    def test_build_sdn_content_individual(self):
        sdn = {
            "ent_num": "1234",
            "SDN_Name": "SMITH, JOHN",
            "SDN_Type": "Individual",
            "Program": "SDGT",
            "Title": "Mr",
            "Call_Sign": "",
            "Vess_Type": "",
            "Tonnage": "",
            "GRT": "",
            "Vess_Flag": "",
            "Vess_Owner": "",
            "Remarks": "DOB 1970-01-01",
        }
        addresses = [
            {"address": "123 Main St", "city_state_zip": "Moscow", "country": "RUSSIA", "add_remarks": ""}
        ]
        alt_names = [
            {"alt_type": "a.k.a.", "alt_name": "SMYTH, IVAN", "alt_remarks": ""}
        ]
        content = _build_sdn_content(sdn, addresses, alt_names)

        assert "SMITH, JOHN" in content
        assert "Individual" in content
        assert "SDGT" in content
        assert "DOB 1970-01-01" in content
        assert "Moscow" in content
        assert "RUSSIA" in content
        assert "SMYTH, IVAN" in content
        assert "a.k.a." in content

    def test_build_sdn_content_vessel(self):
        sdn = {
            "ent_num": "5678",
            "SDN_Name": "MV SHADOW",
            "SDN_Type": "Vessel",
            "Program": "DPRK-SHIPPING",
            "Title": "",
            "Call_Sign": "XYZ123",
            "Vess_Type": "Cargo",
            "Tonnage": "50000",
            "GRT": "45000",
            "Vess_Flag": "NORTH KOREA",
            "Vess_Owner": "SHADOW SHIPPING CO.",
            "Remarks": "",
        }
        content = _build_sdn_content(sdn, [], [])
        assert "MV SHADOW" in content
        assert "XYZ123" in content
        assert "NORTH KOREA" in content


# ---------------------------------------------------------------------------
# OFACSanctionsSource parsing tests (mocked download)
# ---------------------------------------------------------------------------


class TestOFACSanctionsSource:
    SDN_SAMPLE = "\n".join([
        "1|HEZBOLLAH|Entity|SDGT|||||||||",
        "2|AL AQSA BANK|Entity|SDGT-IRAN|||||||||",
        "3|SMITH, IVAN|Individual|SDGT|Mr||||||DOB 1970",
    ])

    ADD_SAMPLE = "\n".join([
        "1|1|123 Beirut St|Beirut|LEBANON|",
        "3|1|456 Moscow Rd|Moscow|RUSSIA|",
    ])

    ALT_SAMPLE = "\n".join([
        "1|1|a.k.a.|PARTY OF GOD|",
        "3|1|f.k.a.|IVANOV, SERGEI|",
    ])

    def _make_source(self, include_consolidated=False):
        return OFACSanctionsSource(enabled=True, include_consolidated=include_consolidated)

    def test_parse_sdn_basic(self):
        source = self._make_source()
        docs = source._parse_all(self.SDN_SAMPLE, self.ADD_SAMPLE, self.ALT_SAMPLE, "")
        assert len(docs) == 3
        names = {d.title for d in docs}
        assert "HEZBOLLAH" in names
        assert "AL AQSA BANK" in names
        assert "SMITH, IVAN" in names

    def test_sdn_document_fields(self):
        source = self._make_source()
        docs = source._parse_all(self.SDN_SAMPLE, self.ADD_SAMPLE, self.ALT_SAMPLE, "")
        hezb = next(d for d in docs if d.title == "HEZBOLLAH")

        assert hezb.source_feed == "OFAC SDN List"
        assert hezb.source_category == "sanctions_financial"
        assert hezb.source_credibility_tier == 1
        assert hezb.metadata["sanctioned"] is True
        assert hezb.metadata["source_type"] == "ofac_sdn"
        assert "SDGT" in hezb.metadata["sanction_programs"]
        # Address joined
        assert "Beirut" in hezb.content
        # Alt name joined
        assert "PARTY OF GOD" in hezb.content

    def test_sdn_document_id_deterministic(self):
        source1 = self._make_source()
        source2 = self._make_source()
        docs1 = source1._parse_all(self.SDN_SAMPLE, "", "", "")
        docs2 = source2._parse_all(self.SDN_SAMPLE, "", "", "")

        ids1 = {d.id for d in docs1}
        ids2 = {d.id for d in docs2}
        assert ids1 == ids2

    def test_deduplication(self):
        source = self._make_source()
        docs1 = source._parse_all(self.SDN_SAMPLE, "", "", "")
        docs2 = source._parse_all(self.SDN_SAMPLE, "", "", "")  # all same content
        assert len(docs1) == 3
        assert len(docs2) == 0  # all skipped

    def test_consolidated_creates_separate_feed(self):
        source = self._make_source(include_consolidated=True)
        cons_sample = "\n".join([
            "100|CONS ENTITY A|Entity|IRAN|||||||||",
        ])
        docs = source._parse_all("", "", "", cons_sample)
        assert any(d.source_feed == "OFAC Consolidated" for d in docs)

    def test_missing_ent_num_skipped(self):
        source = self._make_source()
        bad_sdn = "|NO ENT NUM|Individual|SDGT||||||||"
        docs = source._parse_all(bad_sdn, "", "", "")
        assert len(docs) == 0

    def test_source_name_and_category(self):
        source = OFACSanctionsSource()
        assert source.name == "ofac_sanctions"
        assert source.category == "sanctions_financial"
        assert source.default_poll_interval == 86400

    @pytest.mark.asyncio
    async def test_fetch_returns_documents(self):
        source = self._make_source()

        async def fake_fetch_text(session, url, timeout):
            if "sdn.csv" in url:
                return self.SDN_SAMPLE
            elif "add.csv" in url:
                return self.ADD_SAMPLE
            elif "alt.csv" in url:
                return self.ALT_SAMPLE
            return ""

        with patch.object(source, "_fetch_text", side_effect=fake_fetch_text):
            mock_session = AsyncMock()
            docs = await source.fetch(mock_session)

        assert len(docs) == 3


# ---------------------------------------------------------------------------
# Factory integration tests
# ---------------------------------------------------------------------------


class TestFactoryIntegration:
    def _settings_with(self, **kwargs) -> Settings:
        return Settings(
            # Disable all other sources to keep it simple
            opensky_enabled=False,
            adsb_enabled=False,
            maritime_enabled=False,
            celestrak_enabled=False,
            osm_enabled=False,
            cctv_enabled=False,
            **kwargs,
        )

    def test_icij_source_created_when_enabled(self):
        settings = self._settings_with(icij_enabled=True)
        sources = build_sources(settings)
        icij_sources = [s for s in sources if s.name == "icij_offshore"]
        assert len(icij_sources) == 1
        assert icij_sources[0].enabled is True

    def test_icij_source_disabled_by_default(self):
        settings = self._settings_with(icij_enabled=False)
        sources = build_sources(settings)
        icij_sources = [s for s in sources if s.name == "icij_offshore"]
        assert len(icij_sources) == 1
        assert icij_sources[0].enabled is False

    def test_ofac_source_created_when_enabled(self):
        settings = self._settings_with(ofac_enabled=True)
        sources = build_sources(settings)
        ofac_sources = [s for s in sources if s.name == "ofac_sanctions"]
        assert len(ofac_sources) == 1
        assert ofac_sources[0].enabled is True

    def test_ofac_source_disabled_by_default(self):
        settings = self._settings_with(ofac_enabled=False)
        sources = build_sources(settings)
        ofac_sources = [s for s in sources if s.name == "ofac_sanctions"]
        assert len(ofac_sources) == 1
        assert ofac_sources[0].enabled is False

    def test_icij_node_types_from_config(self):
        settings = self._settings_with(
            icij_enabled=True,
            icij_node_types="entities,intermediaries",
        )
        sources = build_sources(settings)
        icij = next(s for s in sources if s.name == "icij_offshore")
        assert isinstance(icij, ICIJOffshoreSource)
        assert icij._node_types == ["entities", "intermediaries"]

    def test_ofac_consolidated_flag(self):
        settings = self._settings_with(
            ofac_enabled=True,
            ofac_include_consolidated=False,
        )
        sources = build_sources(settings)
        ofac = next(s for s in sources if s.name == "ofac_sanctions")
        assert isinstance(ofac, OFACSanctionsSource)
        assert ofac._include_consolidated is False

    def test_icij_poll_interval_from_config(self):
        settings = self._settings_with(icij_enabled=True, icij_poll_interval=1234)
        sources = build_sources(settings)
        icij = next(s for s in sources if s.name == "icij_offshore")
        assert icij.poll_interval == 1234

    def test_ofac_poll_interval_from_config(self):
        settings = self._settings_with(ofac_enabled=True, ofac_poll_interval=43200)
        sources = build_sources(settings)
        ofac = next(s for s in sources if s.name == "ofac_sanctions")
        assert ofac.poll_interval == 43200

    def test_both_sources_in_build_output(self):
        settings = self._settings_with(icij_enabled=True, ofac_enabled=True)
        sources = build_sources(settings)
        names = [s.name for s in sources]
        assert "icij_offshore" in names
        assert "ofac_sanctions" in names


# ---------------------------------------------------------------------------
# __init__ exports
# ---------------------------------------------------------------------------


class TestModuleExports:
    def test_icij_importable_from_package(self):
        from periphery.ingest.sources import ICIJOffshoreSource as Cls
        assert Cls is ICIJOffshoreSource

    def test_ofac_importable_from_package(self):
        from periphery.ingest.sources import OFACSanctionsSource as Cls
        assert Cls is OFACSanctionsSource

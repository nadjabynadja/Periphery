# Dataset Ingestors — Changelog

**Date:** 2026-03-19  
**Author:** James Beard (Principal Engineer)

---

## Summary

Added two new bulk-dataset data source ingestors for financial intelligence: ICIJ Offshore Leaks and the OFAC Sanctions Lists (SDN + Consolidated). Both integrate into the existing `DataSource` base class and `SourcesDaemon` polling loop, flowing documents through the full enrichment pipeline (entity extraction → LLM verification → embedding → crystallization).

---

## New Files

### `periphery/ingest/sources/icij_offshore.py`

`ICIJOffshoreSource(DataSource)` — polls the ICIJ Offshore Leaks database.

**Behaviour:**
- Downloads `full-oldb.LATEST.zip` (~500MB) streaming to `/app/data/` (configurable via `icij_data_dir`)
- Parses up to three node types: `entities`, `officers`, `intermediaries` (comma-separated via config)
- Loads `relationships.csv` and embeds relationship metadata onto each related node document
- Deduplication via SHA-256 content hash (`_seen_hashes` set); re-fetches only changed entries on poll
- Default poll interval: 7 days (604800 seconds)
- Produces `IngestedDocument` with:
  - `source_feed`: `"ICIJ Offshore Leaks"`
  - `source_category`: `"sanctions_financial"`
  - `source_credibility_tier`: 2
  - `url`: `https://offshoreleaks.icij.org/nodes/{node_id}`
  - `metadata`: all CSV fields + `source_type`, `node_type`, `country_codes`, `jurisdiction_code`, `relationships`

### `periphery/ingest/sources/ofac_sanctions.py`

`OFACSanctionsSource(DataSource)` — polls OFAC SDN and Consolidated Non-SDN lists.

**Behaviour:**
- Downloads four CSVs in parallel: `sdn.csv`, `add.csv`, `alt.csv`, `cons_prim.csv`
- Handles pipe-delimited (`|`) format (no header row) as used by the OFAC SDN list
- Joins addresses (`add.csv`) and alt names (`alt.csv`) onto each entity by `ent_num`
- `_parse_cons_csv()` auto-detects delimiter and optional header row for the consolidated list
- Deduplication via SHA-256 content hash; only emits changed entries
- Default poll interval: 1 day (86400 seconds) — sanctions lists update frequently
- Produces `IngestedDocument` with:
  - `source_feed`: `"OFAC SDN List"` or `"OFAC Consolidated"`
  - `source_category`: `"sanctions_financial"`
  - `source_credibility_tier`: 1 (U.S. government primary source)
  - `metadata`: all fields + `source_type`, `sanctioned: true`, `sanction_programs: [list]`, `addresses`, `alt_names`

### `scripts/ingest_datasets.py`

Standalone bulk-ingest CLI:

```
python scripts/ingest_datasets.py --icij --ofac
python scripts/ingest_datasets.py --icij --node-types entities
python scripts/ingest_datasets.py --ofac --no-consolidated --dry-run
```

Options:
- `--icij` / `--ofac` — select which datasets to ingest
- `--db PATH` — SQLite path (default: `./data/periphery_documents.db`)
- `--data-dir PATH` — download directory for ICIJ ZIP (default: `/app/data`)
- `--no-consolidated` — skip OFAC Consolidated list
- `--node-types` — comma-separated ICIJ node types
- `--dry-run` — parse and count without writing to DB

Shows progress every 10,000 (ICIJ) / 1,000 (OFAC) documents.

### `tests/test_dataset_sources.py`

37 tests covering:
- Helper functions (`_build_entity_content`, `_content_hash`, `_clean`, `_parse_pipe_csv`, `_build_sdn_content`)
- ICIJ ZIP parsing with in-memory mock ZIPs
- ICIJ relationship embedding in metadata
- ICIJ deduplication (content hash deduplification)
- OFAC SDN parsing with pipe-delimited sample data
- OFAC address + alt-name joining
- OFAC consolidated list with auto-delimiter detection
- OFAC deduplication
- Document field correctness (source_feed, source_category, credibility tier, metadata keys)
- Deterministic document IDs across source instances
- Factory integration (sources created/disabled per config flags)
- Config flag pass-through (poll intervals, node_types, include_consolidated)
- Module-level exports from `periphery.ingest.sources`

---

## Modified Files

### `periphery/config.py`

Added 7 new settings:

| Setting | Type | Default | Description |
|---|---|---|---|
| `icij_enabled` | `bool` | `False` | Enable ICIJ Offshore Leaks polling |
| `icij_poll_interval` | `int` | `604800` | Seconds between ICIJ re-downloads |
| `icij_node_types` | `str` | `"entities,officers,intermediaries"` | Comma-separated node types to ingest |
| `icij_data_dir` | `str` | `"/app/data"` | Download directory for the ZIP |
| `ofac_enabled` | `bool` | `False` | Enable OFAC Sanctions polling |
| `ofac_poll_interval` | `int` | `86400` | Seconds between OFAC re-downloads |
| `ofac_include_consolidated` | `bool` | `True` | Whether to include the Consolidated Non-SDN list |

### `periphery/ingest/sources/factory.py`

- Imported `ICIJOffshoreSource` and `OFACSanctionsSource`
- Added both sources to `build_sources()` at the end of the list, gated on their respective `*_enabled` config flags

### `periphery/ingest/sources/__init__.py`

- Updated module docstring
- Exported `ICIJOffshoreSource` and `OFACSanctionsSource` in `__all__`

### `tests/test_sources.py`

- Updated `TestFactory::test_build_sources_all_disabled` assertion from `len == 6` to `len == 8` to account for the two new sources (both disabled by default)

---

## Test Results

```
479 passed, 3 skipped — all tests green
```

---

## Notes

- Both sources are **disabled by default** (`*_enabled = False`). Enable via env vars `ICIJ_ENABLED=true` / `OFAC_ENABLED=true` or `.env` file.
- The ICIJ ZIP (~500MB+) is streamed to disk — not held in memory. Parsing runs in a thread executor to avoid blocking the async event loop.
- OFAC CSVs are downloaded in parallel via `asyncio.gather`. Download failures for `add.csv` / `alt.csv` are logged as warnings (non-fatal); SDN download failure raises and triggers the source's exponential backoff.
- Content-hash deduplication means on subsequent polls only changed or new entities are emitted downstream. First run will ingest everything.

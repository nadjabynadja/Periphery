# Changelog — 2026-03-18

**Author:** James Beard, Principal Engineer  
**Scope:** Critical and high-priority security/stability fixes from CODE_REVIEW.md  
**Test result:** 415 passed, 3 skipped (integration tests requiring model download), 0 failed

---

## Critical Fixes

### C1 — API Keys / Repository Hygiene
- Added `nohup.out`, `sqlforthis.txt`, `*.db`, and `package-lock.json` to `.gitignore`
- Removed `nohup.out` and `sqlforthis.txt` from git tracking (`git rm --cached`)
- Added TODO comment to `.env.example` noting that committed API keys (Anthropic, Mapbox, Exa) must be manually rotated by a human with dashboard access

### C2/C3 — Unauthenticated Admin/Command Endpoints

**`periphery/config.py`**
- Added `admin_api_key: str = ""` setting. When set (via `ADMIN_API_KEY` env var), all admin and command endpoints require an `X-Admin-Key` header matching this value. When unset, endpoints return 403.

**`periphery/commands/router.py`**
- Added `_check_admin_key()` helper that enforces the admin key requirement
- Added `X-Admin-Key` header check to all endpoints: `/force-ingest`, `/run-collect`, `/continuous-collect`, `/status`, `/stop/{command_name}`
- Also fixed L9 here (see below)

**`periphery/query/api.py`**
- Added `_check_admin_key()` helper
- Added `X-Admin-Key` header requirement to `POST /admin/backfill-entities`
- Replaced `feedback: dict[str, Any]` with `FeedbackRequest` Pydantic model on `POST /feedback/{query_id}` — validates `rating`, `notes`, `tags` fields
- Replaced `body: dict[str, Any]` with `AnnotationRequest` Pydantic model on `POST /annotate` — validates `annotation_type`, `target_type`, `target_id`, `data`, `session_id`

**`periphery/auth/router.py`**
- Added `_check_admin_key()` helper
- Added `X-Admin-Key` header requirement to `POST /orgs`

### C4 — Database Pool Fallback Missing row_factory
- **Already present in the codebase** — the fallback path in `get_connection()` already set `db.row_factory = aiosqlite.Row`. No code change needed; confirmed correct.

### C5 — In-Memory Document Store (Legacy Documentation)

**`periphery/ingest/router.py`**
- Added a prominent comment on the `_documents` dict explaining it is a legacy in-memory store, not persisted across restarts, used only by the legacy `/ingest/` endpoint, and linking to the RSS pipeline's DB-backed `DocumentStore` as the replacement pattern
- Added `# TODO: Replace with a database-backed store` note

### C6 — Connection Pool Race

**`periphery/db.py`**
- Added `_pool_init_lock: asyncio.Lock | None` module-level variable with lazy `_get_init_lock()` accessor
- `init_pool()` now uses a double-checked locking pattern: fast path checks without lock, then acquires the lock and re-checks before creating the pool — prevents two concurrent callers from initializing the pool twice during startup

---

## High Priority Fixes

### H5 — Callback Signature Mismatch

**`periphery/crystallizer/worker.py`** (`_crystallize_legacy`)
- Removed the early `coherence_scores = await self.on_crystallize(vectors, labels)` call that passed raw NumPy arrays — incompatible with the `critic_callback` signature in `main.py` which expects a `LivingOntologySnapshot`
- Added `await self.on_crystallize(snapshot)` call after the snapshot is built, matching the multi-space path exactly
- The callback invocation is wrapped in a try/except to prevent a callback failure from crashing the crystallizer

### H6 — WebSocket Unbounded Memory

**`periphery/ws/router.py`**
- Added `cleanup_dead_connections()` method to `ConnectionManager` that:
  - Removes snapshot subscribers with non-`CONNECTED` state (dead without clean close frame)
  - Removes dead connections from all query subscriber sets
  - Removes empty query subscriber dict entries (keys for completed/dead queries)
- The `disconnect_query` method already deleted empty entries on clean disconnect; `cleanup_dead_connections()` handles the silent-disconnect case

### H7 — Settings Bypass in api.py

**`periphery/query/api.py`**
- Changed line 13 from `settings = Settings()` (bypasses lru_cache, creates a second Settings instance) to `settings = get_settings()` — now uses the shared cached singleton consistent with the rest of the codebase

### H8 — N+1 Query in Search Router

**`periphery/search/router.py`** (`search_documents`)
- Replaced the per-document enrichment query loop (26 queries for a 25-result page) with a single batch `SELECT document_id, entities, relationships FROM document_enrichments WHERE document_id IN (...)` query
- Builds an `enrichment_map: dict[str, tuple[int, int]]` from the batch result and looks up counts from it when building the results list — reduces to 1 extra query per search request regardless of page size

### H10 — Pickle FAISS ID Maps

**`periphery/ingest/store.py`**
- Removed `import pickle`; added `import json`
- `FAISSStore.save()`: replaced `pickle.dump((id_to_pos, pos_to_id), f)` with `json.dump({"id_to_pos": ..., "pos_to_id": ...}, f)` — int keys serialized as strings (JSON requirement)
- `FAISSStore.load()`: replaced `pickle.load(f)` with `json.load(f)` with explicit `int()` key conversion on `pos_to_id`
- `MultiSpaceIndexManager.initialize()`: same pickle→JSON replacement for per-space ID map loading
- `MultiSpaceIndexManager.save()`: same for bulk save
- `MultiSpaceIndexManager.save_space()`: same for single-space save
- All ID map files now use text mode (`"r"`/`"w"` with `encoding="utf-8"`) instead of binary mode

### L9 — Hardcoded `.venv/bin/python`

**`periphery/commands/router.py`**
- Replaced all three `str(_PROJECT_ROOT / ".venv" / "bin" / "python")` command arguments with `sys.executable`
- Added `import sys`; removed hardcoded venv path references
- This ensures the correct Python interpreter is always used regardless of deployment environment (Docker, system install, CI)

### M14 — Frontend Double WebSocket

**`frontend/src/api/client.ts`**
- Removed `snapshotPWS` (`PeripheryWebSocket` instance connecting to `/ws/snapshot`)
- Removed `onSnapshotUpdate()`, `onNewDocument()`, `getConnectionStatus()`, `onConnectionStatusChange()` helper functions that all delegated to the unused `snapshotPWS`
- The `subscribeToQuery()` function is retained (it uses `PeripheryWebSocket` for per-query WS connections)
- The active `wsManager` (`WebSocketManager` class) used by `App.tsx` is kept unchanged
- Added explanatory comment clarifying which approach is active

**`frontend/src/api/index.ts`**
- Removed exports of `snapshotPWS`, `onSnapshotUpdate`, `onNewDocument`, `getConnectionStatus`, `onConnectionStatusChange` (no longer exported from client.ts)

---

## Test Results

```
415 passed, 3 skipped, 0 failed
```

The 3 skipped tests are `@pytest.mark.skip(reason="Requires model download and full app init")` — intentionally excluded integration tests, pre-existing.

All fixes are surgical and do not break existing functionality. The `test_critic.py` suite (requires PyTorch) and all other suites pass fully with the changes applied.

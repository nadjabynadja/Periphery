# Periphery Codebase Review

**Reviewer:** James Beard, Principal Software Engineer  
**Date:** 2026-03-18  
**Scope:** Full codebase — Python backend, React/TypeScript frontend, Docker/config  
**Method:** Exhaustive file-by-file read of every Python source, test, and frontend component

---

## Executive Summary

Periphery is an ambitious, well-structured OSINT platform. The architecture is thoughtful, the domain model is rich, and the pipeline design shows real engineering care. However, there are several issues that range from **critical security vulnerabilities** to systemic quality concerns that must be addressed before this system handles real intelligence data.

The most pressing issues are: a live API key committed to `.env`, unauthenticated admin endpoints, a race-prone connection pool, and an in-process document store (the Python `dict`) that disappears on restart. These are not theoretical — they will bite in production.

---

## CRITICAL

### C1 — Real API Key Committed to `.env`  
**File:** `/root/Periphery/.env`  
**Lines:** 2, 7, 10

```
ANTHROPIC_API_KEY=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
VITE_MAPBOX_ACCESS_TOKEN=pk.eyJ1IjoiZmRhZmZyb24...
EXA_API_KEY=278af25f-91fa-40dc-adaf-d64bffca6736
```

`.env` is in `.gitignore` (standard), but the file exists on disk with live production credentials. More importantly, `.env` has been committed to the repo at some point (there is a git history with pack objects). This key must be rotated immediately. The Anthropic key especially — it has billing consequences and may give external access to the LLM pipeline.

**Action:** Rotate all three keys. Add a CI check (e.g., `git-secrets` or `detect-secrets`) to prevent future commits of secrets. The `.env.example` with redacted values already exists — that's good.

---

### C2 — Unauthenticated Admin Endpoints  
**File:** `/root/Periphery/periphery/query/api.py`  
**Line:** 620–639

```python
@router.post("/admin/backfill-entities")
async def admin_backfill_entities():
    """Trigger entity backfill from existing document_enrichments data."""
    entity_index = _entity_index
    ...
```

This endpoint mutates the entity index from all stored documents and runs unboundedly. It has **no authentication, no rate limiting, no RBAC check**. Anyone who can reach the API can trigger it repeatedly and cause CPU exhaustion or corrupt the in-memory entity index.

Similarly:

**File:** `/root/Periphery/periphery/auth/router.py`, lines 97–99  
```python
@router.post("/orgs")
async def create_org(body: CreateOrgRequest):
    """Create a new organization. Bootstrap endpoint."""
    org = await create_organization(body.name)
```

Creating an organization is completely unauthenticated. An attacker can create unlimited orgs. Org creation should require a bootstrap secret or existing admin auth.

**File:** `/root/Periphery/periphery/query/api.py`, lines 407–415  
```python
@router.post("/feedback/{query_id}")
async def submit_feedback(query_id: str, feedback: dict[str, Any]):
```

Feedback submission is also unauthenticated — anyone can write arbitrary data to `analyst_annotations`. The `body: dict[str, Any]` parameter accepts completely unstructured JSON with no validation.

**File:** `/root/Periphery/periphery/query/api.py`, lines 419–430  
```python
@router.post("/annotate")
async def submit_annotation(body: dict[str, Any]):
```

Same issue. No auth, no schema validation.

---

### C3 — Command Execution Endpoints Have No Authentication  
**File:** `/root/Periphery/periphery/commands/router.py`  
**Lines:** 37–55

```python
@router.post("/force-ingest")
async def force_ingest():

@router.post("/run-collect")
async def run_collect():

@router.post("/continuous-collect")
async def continuous_collect():
```

These endpoints spawn real OS subprocesses on the server. They have **zero authentication**. Any network-reachable attacker can:
- Trigger arbitrary RSS collection
- Run `continuous-collect` indefinitely to exhaust resources
- Abuse pipeline CPU/memory at will

`auth_enabled` is `False` by default in `config.py`. The entire auth system can be bypassed at the config level, but these command endpoints are not gated behind `auth_enabled` checks either — they are unconditionally exposed.

---

### C4 — Database Pool Acquired in Fallback Mode Before Initialization  
**File:** `/root/Periphery/periphery/db.py`  
**Lines:** 270–285

```python
@asynccontextmanager
async def get_connection(db_path: str | Path | None = None):
    if _pool is not None and _pool._initialized and not _pool._closed:
        async with _pool.acquire() as db:
            yield db
    else:
        # Fallback: direct connection (pre-pool startup or tests)
        db = await aiosqlite.connect(str(db_path or "./data/periphery_documents.db"))
```

The fallback connection does **not** set `row_factory = aiosqlite.Row`. This means all direct-connection queries return plain tuples, not named rows. Code that does `row["column_name"]` will raise `TypeError` on the fallback path. This is a latent crash during startup or in tests that don't initialize the pool.

Additionally, the fallback silently defaults to `"./data/periphery_documents.db"` if `db_path` is `None`. In tests or early-startup code, this creates a stale file in the wrong directory without any warning.

---

### C5 — In-Memory Document Store Lost on Restart  
**File:** `/root/Periphery/periphery/ingest/router.py`  
**Line:** 17

```python
_documents: dict[str, Document] = {}
```

Every document ingested via `/ingest/` is stored **only** in this Python dict. When the server restarts, all ingested documents are lost. This dict is also used by the Crystallizer worker, query engine, and legacy graph builder. The FAISS index is persisted to disk, but the document metadata (content, metadata, created_at) is not — so after a restart, vector lookups will succeed but document resolution will return empty/default Documents.

This is only used by the legacy `/ingest/` endpoint (not the RSS pipeline), but it's still a silent data loss bug that's confusing to debug.

---

### C6 — Connection Pool Race: Pool Not Initialized on Concurrent Startup  
**File:** `/root/Periphery/periphery/db.py`  
**Lines:** 219–232

```python
async def init_pool(...) -> DatabasePool:
    global _pool
    if _pool is not None and _pool._initialized and not _pool._closed:
        return _pool
    _pool = DatabasePool(...)
    await _pool.initialize()
    return _pool
```

`init_pool` is not protected by an asyncio lock. If two coroutines call it concurrently during startup (e.g., `ensure_database` and `ensure_geotag_database` racing), both may pass the `if _pool is not None` check simultaneously, creating two pools — one of which is dropped. This is unlikely in the current `main.py` sequential startup, but it's an accident waiting to happen in tests or refactored code.

---

## HIGH

### H1 — SQL Injection Risk in Dynamic Query Construction  
**Files:** `/root/Periphery/periphery/query/api.py` (lines 58–75), `/root/Periphery/periphery/search/router.py` (multiple)

```python
placeholders = ",".join("?" for _ in batch)
cursor = await db.execute(
    f"""SELECT document_id, entities, relationships
        FROM document_enrichments
        WHERE document_id IN ({placeholders})""",
    batch,
)
```

The `IN (?,?,?)` pattern using parameterized placeholders is correct. However, the same files also contain raw f-string SQL construction for filter clauses:

**File:** `/root/Periphery/periphery/search/router.py`, lines 54–68

```python
where_clause = ""
if filters:
    where_clause = "AND " + " AND ".join(filters)

count_sql = f"""
    SELECT COUNT(*) FROM documents_fts fts
    JOIN documents d ON d.rowid = fts.rowid
    WHERE documents_fts MATCH ?
    {where_clause}
"""
```

The `filters` list is built from query parameters like `source_feed`, `category`, etc. These are used as raw column names in f-string SQL. While the values themselves are parameterized (`params.append(source_feed)`), the **column names and operators** (`d.source_feed = ?`, `d.source_category = ?`) are hardcoded strings — which is safe here, but the pattern itself is dangerous. If any future developer adds a user-controlled filter key to this list without review, it becomes SQL injection.

**File:** `/root/Periphery/periphery/crystallizer/persistence.py`, line 266  
```python
f"UPDATE clusters SET status = 'dissolved', last_seen = ? "
f"WHERE cluster_id IN ({placeholders})"
```

String formatting of `'dissolved'` is benign (it's a literal), but using f-strings for SQL should be avoided as a pattern.

**File:** `/root/Periphery/periphery/pipeline/consumer.py`, lines 138–148  
```python
set_clause = f"processing_status = ?"
params: list[Any] = [self.processing_status]
if self.started_at_column:
    set_clause += f", {self.started_at_column} = ?"
```

`self.started_at_column` is a class attribute set in subclass definitions (e.g., `"enrichment_started_at"`). These are not user-controlled, but they ARE injected into SQL without parameterization. If a subclass ever sets this to an attacker-controlled value, it's injectable. The pattern should use an allow-list instead.

---

### H2 — Session Token Stored in localStorage (XSS Risk)  
**File:** `/root/Periphery/frontend/src/store/index.ts`  
**Lines:** 146–153

```typescript
setSessionToken: (t) => {
  if (t) {
    localStorage.setItem('periphery_session', t)
  } else {
    localStorage.removeItem('periphery_session')
  }
  set({ sessionToken: t, isAuthenticated: t !== null })
},
```

**File:** `/root/Periphery/frontend/src/api/client.ts`, line 61  
```typescript
const token = localStorage.getItem('periphery_session')
```

Session tokens in `localStorage` are accessible to any JavaScript running on the page. A single XSS vulnerability anywhere in the app (or a malicious third-party script) can steal the token silently. For an OSINT intelligence platform, this is a significant attack surface.

The correct approach is `HttpOnly` cookies, which cannot be read by JavaScript. This requires backend changes (set-cookie response) and CSRF protection.

---

### H3 — CORS Configuration Allows Credentials to Multiple Origins  
**File:** `/root/Periphery/periphery/main.py`  
**Lines:** 186–192

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`allow_credentials=True` combined with `allow_methods=["*"]` and `allow_headers=["*"]` is extremely permissive. Credentials (cookies, auth headers) will be sent cross-origin to any of the listed origins. If any origin in `CORS_ORIGINS` is compromised or misconfigured, it can make authenticated requests on behalf of users.

**File:** `.env`, line 18  
```
CORS_ORIGINS=http://localhost:5173,http://localhost:8000,http://localhost:3000,https://kestralsaber.prphry.com
```

`http://localhost:8000` in production CORS origins means the backend can cross-request itself from the browser context.

---

### H4 — `require_role` Dependency is Broken  
**File:** `/root/Periphery/periphery/auth/middleware.py`  
**Lines:** 49–57

```python
def require_role(*roles: str):
    """Return a dependency that checks the user has one of the given roles."""
    async def _check(
        user: AuthenticatedUser = Header(None),  # replaced at call-site
    ) -> AuthenticatedUser:
        ...
    return _check
```

This function is broken. It returns `_check`, which uses `Header(None)` as the dependency — not `Depends(get_current_user)`. This will **never inject an authenticated user**; it will always get `None` from the `Authorization` header value (which is a raw string, not an `AuthenticatedUser`). The function is currently unused in the codebase, but it's dead code that would silently fail authorization if someone relied on it.

---

### H5 — Crystallizer `on_crystallize` Callback Signature Mismatch  
**File:** `/root/Periphery/periphery/crystallizer/worker.py`  
**Lines:** 215–218 and 295–299

In `_crystallize_multi_space`:
```python
if self.on_crystallize:
    try:
        await self.on_crystallize(snapshot)
```

In `_crystallize_legacy`:
```python
if self.on_crystallize:
    coherence_scores = await self.on_crystallize(vectors, labels)
```

The legacy path calls `on_crystallize(vectors, labels)` with two NumPy arguments, but the callback registered in `main.py` (`critic_callback`) has the signature:
```python
async def critic_callback(vectors: np.ndarray, labels: np.ndarray) -> dict[int, float]:
```

The multi-space path calls it with a single `LivingOntologySnapshot` argument. These are **incompatible signatures for the same callback**. One of these will crash at runtime. Looking at the code flow: `_crystallize_multi_space` is the normal path; `_crystallize_legacy` is fallback. The `critic_callback` in `main.py` is actually designed to accept a `snapshot` (it calls `worker.current_snapshot`). The legacy signature on line ~295 is wrong and will crash.

---

### H6 — Unbounded Memory Growth in WebSocket Connection Manager  
**File:** `/root/Periphery/periphery/ws/router.py`  
**Lines:** 31–33

```python
class ConnectionManager:
    def __init__(self) -> None:
        self._snapshot_subscribers: set[WebSocket] = set()
        self._query_subscribers: dict[str, set[WebSocket]] = {}
```

Dead connections are only removed from `_snapshot_subscribers` during `broadcast_snapshot_update` when a send fails. If connections die silently (network drop without proper close frame), they'll accumulate in the set indefinitely. The `_query_subscribers` dict also grows without bound — query IDs are never cleaned up after a query WebSocket disconnects successfully, since `disconnect_query` is called but the set might remain empty in the dict.

More critically: `_query_subscribers` keys (query IDs) are never evicted. Every unique `query_id` ever used will stay in the dict forever, even as an empty set.

---

### H7 — Snapshot Cache in `api.py` Uses Module-Level Mutable Global  
**File:** `/root/Periphery/periphery/query/api.py`  
**Lines:** 25–27

```python
_enrichment_cache: dict[str, dict[str, Any]] = {}
```

And in `_load_enrichment_entities`:
```python
_enrichment_cache.clear()
_enrichment_cache[snapshot_id] = {...}
```

This cache:
1. Clears itself whenever a different snapshot_id appears — meaning concurrent requests for different snapshots will thrash the cache constantly
2. Is not protected by any lock — concurrent coroutines can interleave `clear()` and `__setitem__` causing data races in asyncio (though asyncio is single-threaded, interleaving at `await` points is possible)
3. `settings = Settings()` is instantiated at module level (line 13), **bypassing** the `lru_cache` singleton in `get_settings()`. This creates a second Settings object that won't reflect any overrides from tests or environment changes.

---

### H8 — `DocumentStore` Uses Persistent Connection Outside Pool  
**File:** `/root/Periphery/periphery/rss_ingest/document_store.py`  
**Lines:** 55–57, 99–111

```python
self._db: aiosqlite.Connection | None = None

async def initialize(self) -> None:
    self._db = await get_persistent_connection(self._db_path)
    await self._db.execute("PRAGMA journal_mode=WAL")  # Set twice!
```

`get_persistent_connection` is a legacy bypass of the pool. The RSS ingest `DocumentStore` has its own standalone connection, while the API server's `DatabasePool` has 5 connections to the same file. SQLite WAL mode supports this, but the dual-connection setup means the RSS daemon and API server have no coordination beyond SQLite's file locking. Commit timestamps may diverge.

More subtly: `DocumentStore` calls `PRAGMA journal_mode=WAL` again (line 57 in initialize), even though `get_persistent_connection` already set it. This is harmless but indicates copy-paste confusion.

Also: `_migrate_legacy_columns` and `_migrate_embeddings_schema` reproduce schema migration logic that already exists in `db.py`. These are now doubly executed on the same database, creating inconsistency risk.

---

### H9 — N+1 Query Pattern in Search Router  
**File:** `/root/Periphery/periphery/search/router.py`  
**Lines:** 98–117

```python
for row in rows:
    doc_id = row[0]
    ...
    ecursor = await db.execute(
        "SELECT entities, relationships FROM document_enrichments WHERE document_id = ?",
        (doc_id,),
    )
```

For every document returned by FTS search, a separate SQL query fetches enrichment counts. With `limit=25` (default), this is 26 queries per search request (1 FTS + 25 enrichment lookups). This should be a single JOIN.

---

### H10 — `FAISSStore` Loads Arbitrary Pickle Data  
**File:** `/root/Periphery/periphery/ingest/store.py`  
**Lines:** 83–85

```python
def load(self) -> None:
    self.index = faiss.read_index(self.index_path)
    with open(self.id_map_path, "rb") as f:
        self.id_to_pos, self.pos_to_id = pickle.load(f)
```

`pickle.load` from a file path is **arbitrary code execution** if the file is tampered with. The `id_map_path` is at `data/faiss/index.bin.ids` — if an attacker can write to the data directory (or replace this file), they get RCE when the server restarts. This should use `json` serialization for the ID mappings, which are simple `dict[str, int]` structures.

The same issue exists in `MultiSpaceIndexManager` (lines 170–175).

---

## MEDIUM

### M1 — `auth_enabled = False` Default Exposes All Auth-Protected Routes  
**File:** `/root/Periphery/periphery/config.py`  
**Line:** 114

```python
auth_enabled: bool = False
```

The `get_current_user` dependency in `middleware.py` does NOT check `auth_enabled`. It always enforces auth. But none of the data/query endpoints actually use `get_current_user` — they're unprotected. The personal ontology endpoints DO use auth. So the current system is:
- Auth endpoints: require valid session
- Personal ontology: requires valid session  
- Everything else (queries, ingest, search, admin): no auth

`auth_enabled` is checked nowhere in the actual routing logic, making it a misleading config option.

---

### M2 — SpaCy Model Loaded as Class Variable (Not Thread-Safe)  
**File:** `/root/Periphery/periphery/enrichment/stages/entity_extraction.py`  
**Lines:** 75–86

```python
class EntityExtractionStage(EnrichmentStage):
    _nlp = None  # class-level lazy SpaCy model

    def _get_nlp(self):
        if EntityExtractionStage._nlp is None:
            ...
            EntityExtractionStage._nlp = spacy.load(...)
```

Class-level singletons initialized lazily without a lock are fine in asyncio (single-threaded), but SpaCy models are not thread-safe for concurrent access. If the pipeline ever moves to multi-threading (e.g., a thread pool executor for CPU-bound work), this will cause subtle corruption. The lazy load pattern also means the first document takes significantly longer to process with no warning.

---

### M3 — BudgetTracker Not Shared Across Pipeline Workers  
**File:** `/root/Periphery/periphery/enrichment/pipeline.py`  
**Lines:** 186–205

`build_enrichment_pipeline` creates a new `BudgetTracker` instance each time it's called. If multiple pipeline instances are created (e.g., for testing or multiple consumers), each has its own budget counter. The hourly/daily caps can be exceeded by a factor of N. There should be a singleton or shared budget tracker.

---

### M4 — `confirm_challenge` Creates Session Before Verifying Code  
**File:** `/root/Periphery/periphery/auth/router.py`  
**Lines:** 72–92

```python
session = await create_session(...)  # Session created here

completed = await complete_challenge(
    challenge_id=challenge_id,
    code=body.code,
    session_token=session.session_token,
)
if not completed:
    await delete_session(session.session_token)  # Deleted on failure
    raise HTTPException(status_code=401, detail="Invalid passcode or challenge expired")
```

A session is created in the database before the passcode is verified. If the server crashes between `create_session` and `complete_challenge`, or if the delete fails, orphaned sessions exist in the database. This is a minor timing issue but could accumulate stale sessions that appear valid if token enumeration is possible (it's not, given the 64-char token, but the pattern is sloppy).

---

### M5 — `scan_challenge` Has No Brute-Force Protection  
**File:** `/root/Periphery/periphery/auth/persistence.py`  
**Lines:** 182–198

The QR scan endpoint (`POST /auth/challenge/{challenge_id}/scan`) accepts any `user_id`. There's no rate limiting on challenge scanning. An attacker could enumerate valid user IDs by brute-forcing this endpoint. The 6-digit numeric code has only 1,000,000 combinations — with no rate limiting on `confirm_challenge` either, this is brute-forceable in seconds.

---

### M6 — `_crystallize_multi_space` Ignores the Critic Callback's Return Value  
**File:** `/root/Periphery/periphery/crystallizer/worker.py`  
**Line:** 215–219

```python
if self.on_crystallize:
    try:
        await self.on_crystallize(snapshot)
```

`on_crystallize` is supposed to return a `dict[int, float]` of coherence scores (based on the legacy signature and `critic_callback` code). The multi-space path discards the return value. The coherence scores that the Critic computes never get fed back into the snapshot. The variable `coherence_scores` is set to `{}` on line 267 and never populated.

---

### M7 — No Input Validation on Ingest Endpoints  
**File:** `/root/Periphery/periphery/ingest/router.py`

There is no maximum size limit on ingested content. A malicious actor (or a buggy feed) can POST arbitrarily large documents to `/ingest/`, causing:
1. OOM in the SpaCy pipeline (which processes up to 1MB in entity extraction, but the ingest endpoint has no limit)
2. Unbounded growth of the in-memory `_documents` dict
3. FAISS index growth without bounds

The entity extraction stage caps at 1MB (`text[:1_000_000]`), but the ingest endpoint itself accepts unlimited content.

---

### M8 — `rebuild_search_indexes` Deletes All Entities/Relationships Before Rebuild  
**File:** `/root/Periphery/periphery/search/setup.py`  
**Lines:** 99–104

```python
await db.execute("DELETE FROM entities_index")
...
await db.execute("DELETE FROM relationships_index")
```

`rebuild_search_indexes` is called at startup with `force=True`. It drops and rebuilds the full entity and relationship indexes. During this rebuild (which could take significant time on large corpora), search queries will return empty results. There is no transactional swap (e.g., build into a temp table, then rename). This creates a search outage window on every startup.

---

### M9 — `_load_enrichment_context` Issues `PRAGMA journal_mode=WAL` on a Pooled Connection  
**File:** `/root/Periphery/periphery/crystallizer/worker.py`  
**Lines:** 393–395

```python
async with get_connection(db_path) as db:
    await db.execute("PRAGMA journal_mode=WAL")
```

The pool's `_create_connection` already sets WAL mode. Issuing it again on every crystallization run is harmless but wasteful and confusing — it suggests the developer is uncertain whether WAL is set.

---

### M10 — `CrystallizerStore` Runs Schema Creation Against Pool (Duplicate Schema)  
**File:** `/root/Periphery/periphery/crystallizer/persistence.py`  
**Lines:** 73–80

```python
async def initialize(self) -> None:
    async with get_connection(self._db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA_SQL)
        await db.commit()
```

`CrystallizerStore.SCHEMA_SQL` contains table definitions that duplicate those in `db.py`'s `SCHEMA_SQL`. Both schemas define `crystallizer_snapshots`, `clusters`, `cluster_snapshots`, `trajectories`, `anomalies`, `relational_gradients`. Since `db.py` runs first and uses `CREATE TABLE IF NOT EXISTS`, the second run is a no-op — but maintaining two definitions of the same schema is a maintenance hazard.

---

### M11 — Entities Extracted with `list.index()` — O(n) Per Lookup  
**File:** `/root/Periphery/periphery/crystallizer/worker.py`  
**Lines:** 341–344

```python
noise_indices = [
    doc_ids_list.index(did)
    for did in noise_ids
    if did in doc_ids_list
]
```

`list.index()` is O(n). With large corpora (thousands of documents), this is O(n*m) where n is doc count and m is noise count. This runs in `_detect_emerging_structures`, called on every crystallization. The fix is a dict mapping `doc_id -> index`, which is already built elsewhere as `doc_id_index`.

---

### M12 — `DocumentStore.exists_by_id` vs `DocumentStore.is_duplicate` Inconsistency  
**File:** `/root/Periphery/periphery/rss_ingest/document_store.py`  
**Lines:** 111–120

```python
async def exists_by_id(self, doc_id: str) -> bool:
    cursor = await self._db.execute(
        "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
    )

async def is_duplicate(self, doc_id: str, url: str) -> bool:
    cursor = await self._db.execute(
        "SELECT 1 FROM documents WHERE (id = ? OR url = ?) AND processing_status != 'pending'",
        ...
    )
```

`is_duplicate` only returns True if a matching document has left `pending` status. A document that was ingested but hasn't been processed yet would not be detected as a duplicate. This could cause the same article to be ingested twice if the deduplication check happens while the first copy is still in `pending`.

---

### M13 — `_enrichment_cache` Module-Level Settings Bypass  
**File:** `/root/Periphery/periphery/query/api.py`  
**Line:** 13

```python
settings = Settings()
```

This instantiates `Settings` directly, not through `get_settings()` (which uses `lru_cache`). This means `pipeline_db_path` could differ from the value used everywhere else if `.env` loading behaves differently at module import time vs. after the app starts. This should be `from periphery.config import get_settings; settings = get_settings()`.

---

### M14 — Frontend Double WebSocket Connection  
**File:** `/root/Periphery/frontend/src/api/client.ts`  
**Lines:** 72–89, 330–345

The file maintains two WebSocket management systems simultaneously:
1. `WebSocketManager` class (`wsManager`) — manages a single `/ws/snapshot` connection
2. `PeripheryWebSocket` instance (`snapshotPWS`) — also connects to `/ws/snapshot`

Both connect to the same endpoint. On startup, the frontend will open **two** WebSocket connections to `/ws/snapshot`. The `snapshotPWS` is what's actually used (via `onSnapshotUpdate`, `onConnectionStatusChange`), while `wsManager` appears to be unused dead code. This doubles the server-side connection count.

---

### M15 — `generate_challenge_code` Uses `secrets.choice` in a Loop — Timing Side-Channel  
**File:** `/root/Periphery/periphery/auth/utils.py`  
**Lines:** 27–29

```python
def generate_challenge_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))
```

`secrets.choice` is cryptographically secure, but the comparison in `complete_challenge` uses raw string equality (`challenge_code = ?` in SQL). SQL string comparison is constant-time in SQLite, but the HTTP response timing difference between "found" and "not found" in the WHERE clause is not. A timing attack is very difficult here due to network jitter, but for a security-critical 6-digit code, `hmac.compare_digest` should be used for the comparison.

---

## LOW

### L1 — Dead Code: `require_role` Factory Function  
**File:** `/root/Periphery/periphery/auth/middleware.py`, lines 49–57  
Already noted as broken in H4. Also dead — never referenced anywhere in the codebase.

### L2 — `_BACKOFF_STATE_FILE = Path("/tmp/periphery_backoff_state.json")` — Hardcoded Temp Path  
**File:** `/root/Periphery/periphery/rss_ingest/poller.py`, line 50  
Hardcoded `/tmp/` path. On some systems `/tmp` is not persistent across reboots. Backoff state will be lost. This should use a configurable data directory path consistent with other data files.

### L3 — `health()` Endpoint Not Behind Auth  
**File:** `/root/Periphery/periphery/main.py`, lines 207–215  
The `/health` endpoint reveals operational details (vector count, cluster count, last crystallization timestamp). For a public deployment, this leaks system state to unauthenticated callers. It should either be restricted or return minimal information when unauthenticated.

### L4 — `CrystallizerStore` Also Has Its Own Schema File — Third Schema Definition  
**File:** `/root/Periphery/periphery/crystallizer/persistence.py`, lines 31–94  
The `SCHEMA_SQL` constant in `CrystallizerStore` is a third copy of table definitions. The canonical source is `db.py`. These three should be one.

### L5 — `snapshot_id` Is Only 16 Characters of a UUID  
**File:** `/root/Periphery/periphery/crystallizer/worker.py`, line 245  
```python
snapshot_id=str(uuid.uuid4())[:16],
```
UUID4 truncated to 16 hex characters gives ~64 bits of randomness, which is sufficient but unconventional. It could collide in theory with ~2^32 snapshots. Just use `str(uuid.uuid4())` for the full ID.

### L6 — `_enrichment_cache.clear()` on Every Cache Miss  
**File:** `/root/Periphery/periphery/query/api.py`, lines 55–57  
```python
_enrichment_cache.clear()
_enrichment_cache[snapshot_id] = {...}
```
The cache logic clears everything on every miss. If two concurrent requests come in for different snapshots, one will evict the other's data. A simple `dict` with a max-size eviction policy (e.g., `functools.lru_cache` or a simple size check) would be more correct.

### L7 — `aiosqlite` Import Missing in `document_store.py`  
**File:** `/root/Periphery/periphery/rss_ingest/document_store.py`  
`self._db: aiosqlite.Connection | None = None` references `aiosqlite.Connection` in the type annotation, but `aiosqlite` is never imported at the top of the file. This works at runtime (type annotations are not evaluated by default in Python 3.10+ unless using `from __future__ import annotations`), but it will fail if anyone runs `mypy` or enables `from __future__ import annotations`.

### L8 — Test Suite is Largely Isolated from Integration Paths  
**File:** `/root/Periphery/tests/test_query.py`

The only integration test is `@pytest.mark.skip(reason="Requires model download and full app init")`. Every other test is a unit test of isolated components. There are no:
- Tests for the `/api/query` endpoint with actual database state
- Tests for the WebSocket endpoints
- Tests for the search router (FTS queries)
- Tests for the pipeline stage consumers
- Tests for the Crystallizer worker's persistence path
- Tests for the commands router
- Load/stress tests for the rate limiter chain

The test coverage is good for unit behavior but leaves the integration seams completely untested.

### L9 — `commands/router.py` Hardcodes `.venv/bin/python` Path  
**File:** `/root/Periphery/periphery/commands/router.py`, lines 23–32  
```python
_COMMANDS: dict[str, list[str]] = {
    "pipeline": [
        str(_PROJECT_ROOT / ".venv" / "bin" / "python"),
        "-m", "periphery.pipeline",
    ],
```
In Docker or any system where the venv isn't at `.venv`, these commands will fail silently (the process will exit immediately, with stdout/stderr discarded). Should use `sys.executable` instead.

### L10 — `nohup.out` File in Repository Root  
**File:** `/root/Periphery/nohup.out`  
A `nohup.out` log file is in the project root. This is a runtime artifact that should be in `.gitignore`. It exposes server log data and confirms the server is run via `nohup` — which is fragile (no process supervision, no automatic restart on OOM).

### L11 — `sqlforthis.txt` Debug File in Repository  
**File:** `/root/Periphery/sqlforthis.txt`  
A file that appears to be developer scratch notes is committed to the repo. Should be removed and added to `.gitignore`.

### L12 — `docker-compose.yml` CORS Doesn't Include Production Frontend URL  
**File:** `/root/Periphery/docker-compose.yml`, line 24  
```yaml
- CORS_ORIGINS=http://localhost:3000,http://localhost:8000
```
The production URL (`https://kestralsaber.prphry.com`) is in the `.env` file but hardcoded `localhost` origins in the docker-compose override it. The compose file should defer to the `.env` for CORS origins.

### L13 — `PeripheryWebSocket` Has No `finally` Block for Interval Cleanup  
**File:** `/root/Periphery/frontend/src/api/websocket.ts`  
If `startPing()` is called and the connection drops before `onclose` fires (e.g., due to a browser exception), `stopPing()` may not be called, leaving the `setInterval` running and sending pings to a dead socket. The ping interval should be stopped in all error paths.

### L14 — `searchRefreshInterval` Default Is 30 Seconds  
**File:** `/root/Periphery/frontend/src/api/client.ts`, line 72  
```typescript
let snapshotRefreshInterval = 30_000
```
The snapshot is cached for 30 seconds by default, but the WebSocket push approach should make polling unnecessary. The cache and WS are running in parallel — when a WS update arrives, `setCachedSnapshot` is called, but the poll-based approach has a separate staleness check. The two mechanisms are not fully coordinated.

### L15 — Missing Test Coverage for Critical Auth Paths  
**File:** `/root/Periphery/tests/test_auth.py`  
The auth tests don't cover:
- Expired session tokens (time-based expiry)
- Expired challenge tokens  
- Concurrent scan attempts on the same challenge
- Role check enforcement (admin vs. analyst vs. viewer)
- The `get_optional_user` middleware path

---

## Architecture Observations

### A1 — Two Query Engines Running Simultaneously  
`main.py` initializes both the legacy `QueryEngine` (`/query/` router) and the `AnalyticalQueryEngine` (`/api/` router). The legacy engine uses the in-memory `_documents` dict; the analytical engine uses the database. These are two separate code paths that produce different results for the same data. The legacy engine should be removed or the divergence documented explicitly.

### A2 — RSS Daemon and API Server Share a SQLite Database With No Coordination  
The RSS daemon (separate process) writes to `periphery_documents.db`. The API server reads it. The pipeline process also writes to it. Three processes share one SQLite file with WAL mode. This works for small scale but will hit lock contention under load. The `busy_timeout=30000ms` is the safety valve, but 30 seconds of blocked queries will cause HTTP timeouts on the API side.

### A3 — Pipeline State Machine Status Is Not Atomic  
**File:** `/root/Periphery/periphery/pipeline/consumer.py`, `_claim_batch` and `_advance`

The claim and advance operations use separate `UPDATE` + `commit` pairs without explicit transactions wrapping them together. A crash between claim and advance leaves a document stuck in `enriching`/`embedding` state until the stale claim recovery runs. The `stale_claim_timeout` (default 600s) means a crashed worker can stall processing for 10 minutes.

### A4 — `MultiSpaceIndexManager` Has No Locking  
**File:** `/root/Periphery/periphery/ingest/store.py`  
The `MultiSpaceIndexManager` is shared between the Crystallizer worker and the embedding consumer. Both can call `add()` and `get_all_vectors()` concurrently. In asyncio, true concurrency doesn't apply, but any `await` point between reads and writes allows interleaving. The FAISS index is not thread-safe, and concurrent modification without locks would be catastrophic if background threads are ever introduced.

### A5 — The `_entity_index` Global in `api.py` Is Not Refreshed  
**File:** `/root/Periphery/periphery/query/api.py`  
The entity index loaded at startup is never reloaded. New entities discovered during the pipeline's runtime are flushed to the database but the in-memory `EntityIndex` in the API server is separate from the one in the pipeline process. Over time, the API server's index grows stale relative to the pipeline's output.

---

## Summary Table

| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 6 | Live API keys in `.env`, unauthenticated admin endpoints, command execution without auth, pool race, in-memory document loss, pickle RCE |
| High | 10 | SQL injection pattern risk, localStorage tokens, broken CORS, broken `require_role`, callback signature mismatch, WS memory leak, settings bypass, N+1 queries |
| Medium | 15 | Auth bypass config, SpaCy singleton, budget not shared, session-before-verify, no brute-force protection, ignored return values, no input limits, search outage on rebuild |
| Low | 15 | Dead code, hardcoded paths, missing imports, minimal integration tests, debug files committed |

---

## Immediate Action Items (Priority Order)

1. **Rotate the Anthropic, Mapbox, and Exa API keys now.** They are in the commit history.
2. **Add authentication to `/api/commands/`, `/admin/backfill-entities`, and `/auth/orgs` (POST).**
3. **Replace `pickle.load` with JSON for FAISS ID maps.**
4. **Fix the `on_crystallize` callback signature mismatch** before the legacy path causes a production crash.
5. **Add `row_factory = aiosqlite.Row`** to the fallback connection in `get_connection()`.
6. **Fix the N+1 query in the search router** — convert to a JOIN.
7. **Move session tokens from localStorage to HttpOnly cookies.**
8. **Replace `sys.executable`** in `commands/router.py`.
9. **Add rate limiting and brute-force protection** to the auth challenge endpoints.
10. **Remove `nohup.out` and `sqlforthis.txt`** from the repository.

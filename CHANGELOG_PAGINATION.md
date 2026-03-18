# CHANGELOG: Pagination Refactor & Performance Improvements

**Date:** 2026-03-18  
**Author:** James Beard, Principal Engineer  
**Branch context:** Performance improvements for `/api/snapshot` endpoint

---

## Problem

The `/api/snapshot` endpoint was returning all 37K+ entities and 948K+ relationships in a single JSON blob, producing multi-MB responses on every page load and poll cycle. This caused:
- Slow initial load times
- High memory pressure on the frontend
- Wasted bandwidth on 30-second refresh cycles
- The previous 2000/5000 entity/relationship caps were a hack that discarded real data

---

## Changes

### 1. GZip Compression (`periphery/main.py`)

Added `GZipMiddleware` from Starlette with a 1000-byte minimum threshold:

```python
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
```

**Middleware order:** GZip is added *after* CORS so it wraps the outer response correctly (middleware in FastAPI/Starlette applies in reverse registration order).

**Result:** Snapshot response reduced from ~2.8MB to ~502KB over the wire (~80% compression ratio).

---

### 2. Snapshot Endpoint Slim-Down (`periphery/query/api.py`)

**`GET /api/snapshot`** now returns only:
- Snapshot metadata (`snapshot_id`, `generated_at`, `timestamp`)
- `corpus_stats` (document/entity/relationship totals)
- `total_entities` and `total_relationships` counts (so frontend knows totals without fetching all)
- `entity_count` and `relationship_count` (backward-compat aliases)
- `clusters` (with rendering)
- `trajectories`, `anomalies`, `gradients`, `convergence_alerts`, `emerging_structures`

**Removed from snapshot response:** `entities` and `relationships` arrays (moved to paginated endpoints).

The enrichment cache is warmed lazily on first snapshot request — subsequent `/api/entities` and `/api/relationships` calls return instantly from cache.

**Removed the 2000/5000 artificial caps** — pagination handles volume now.

---

### 3. New Paginated Entity Endpoint (`periphery/query/api.py`)

**`GET /api/entities`**

Query parameters:
| Param | Default | Description |
|-------|---------|-------------|
| `page` | 1 | Page number (1-indexed) |
| `limit` | 200 | Entities per page (max 1000) |
| `cluster_id` | — | Filter to entities in a specific cluster |
| `entity_type` | — | Filter by entity type (case-insensitive) |
| `search` | — | Text search on name and aliases |
| `sort_by` | `source_count` | Sort field: `source_count`, `confidence`, `name` |

Response:
```json
{
  "total": 5084,
  "page": 1,
  "limit": 200,
  "entities": [...]
}
```

---

### 4. New Paginated Relationship Endpoint (`periphery/query/api.py`)

**`GET /api/relationships`**

Query parameters:
| Param | Default | Description |
|-------|---------|-------------|
| `page` | 1 | Page number (1-indexed) |
| `limit` | 200 | Relationships per page (max 1000) |
| `entity_id` | — | Filter to relationships involving this entity (subject or object) |
| `cluster_id` | — | Filter to relationships involving entities in a cluster |
| `min_confidence` | — | Minimum confidence threshold |
| `sort_by` | `confidence` | Sort field: `confidence` |

Response:
```json
{
  "total": 447884,
  "page": 1,
  "limit": 200,
  "relationships": [...]
}
```

---

### 5. Frontend Types (`frontend/src/api/types.ts`)

- `OntologySnapshot.entities` and `relationships` made **optional** (`EntityNode[]?`, `Relationship[]?`)
- Added `total_entities?: number` and `total_relationships?: number` fields
- Added `PaginatedEntities` response type
- Added `PaginatedRelationships` response type

---

### 6. Frontend API Client (`frontend/src/api/client.ts`)

- Added `getEntities(params)` method for paginated entity fetching
- Added `getRelationships(params)` method for paginated relationship fetching

---

### 7. Frontend Store (`frontend/src/store/index.ts`)

- Added `entities: EntityNode[]` field (separate from snapshot)
- Added `relationships: Relationship[]` field (separate from snapshot)
- Added `setEntities` and `setRelationships` actions
- Added `loadingEntities: boolean` state and `setLoadingEntities` action

---

### 8. App.tsx (`frontend/src/App.tsx`)

- After loading snapshot, immediately kicks off `getEntities({ limit: 500 })` and stores entities in the store
- WebSocket snapshot delta also refreshes entities
- View mode stats bar reads from `snapshot.total_entities` / `snapshot.total_relationships`

---

### 9. SystemStatusBar (`frontend/src/components/SystemStatusBar.tsx`)

- Entity/relationship counts now read from `snapshot.total_entities` / `snapshot.total_relationships` with fallback to legacy `entity_count` / `relationship_count`
- Ticker reads from store `entities` instead of `snapshot.entities`

---

### 10. DataFeedSidebar (`frontend/src/components/DataFeedSidebar.tsx`)

- Feed entries derived from store `entities` instead of `snapshot.entities`

---

### 11. QueryBar (`frontend/src/components/query/QueryBar.tsx`)

- Autocomplete suggestions read from store `entities` instead of `snapshot.entities`

---

### 12. DetailPanel (`frontend/src/components/detail/DetailPanel.tsx`)

- `RelationshipView`: resolves entity names from store `entities` with fallback to `snapshot.entities`
- `AnomalyView`: resolves related entity names from store `entities` with fallback to `snapshot.entities`

---

### 13. OntologyGraph (`frontend/src/components/graph/OntologyGraph.tsx`)

- Simulation uses store `entities` and `relationships` when available, falls back to `snapshot.entities/relationships`
- useEffect dependency array updated to include `storeEntities` and `storeRelationships`

---

## Test Results

```
415 passed, 3 skipped, 3 warnings in 14.72s
```

All existing tests pass. No regressions.

## Live Endpoint Verification

```bash
# Snapshot: lightweight, no entities/relationships
curl http://localhost:8000/api/snapshot
# → 502KB gzip-compressed (~2.8MB uncompressed), 89 clusters, 0 entities array

# Entities: paginated
curl http://localhost:8000/api/entities?limit=10
# → total: 5084, page: 1, limit: 10, entities: [10 items]

# Relationships: paginated
curl http://localhost:8000/api/relationships?limit=10
# → total: 447884, page: 1, limit: 10, relationships: [10 items]
```

---

## Architecture Principle

> Snapshot = living ontology metadata (clusters, trajectories, anomalies, gradients)  
> Entities + Relationships = on-demand via pagination  
> Cache = enrichment cache keyed by snapshot_id, warmed on first snapshot hit

This keeps the 30-second snapshot polling cycle lightweight while entities load once on startup and refresh on-demand.

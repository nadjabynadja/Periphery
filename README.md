# Periphery

Data infrastructure where schema is emergent, not predefined.

Structure is a continuous output of the system, not an input to it.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Query Interface                        │
│         Natural language → Intent → Retrieval → Synthesis   │
│         Multi-space search · Legibility gradient rendering   │
├─────────────────────────────────────────────────────────────┤
│   Continuous Critic       │        Crystallizer              │
│   Ensemble coherence      │  HDBSCAN clustering              │
│   scoring (neural +       │  Living ontology snapshots       │
│   source/temporal/spatial) │  Anomaly & trajectory detection  │
├─────────────────────────────────────────────────────────────┤
│                   Enrichment Pipeline                        │
│   Entity extraction · Relationship extraction                │
│   Temporal tagging · Geospatial resolution                   │
│   Source credibility · LLM-powered (budget-capped)           │
├─────────────────────────────────────────────────────────────┤
│                      Ingest Mesh                             │
│   JSON, CSV, plaintext, file uploads, RSS feeds              │
│   sentence-transformers + FAISS (5 embedding spaces)         │
├─────────────────────────────────────────────────────────────┤
│                     Storage Layer                            │
│   SQLite (WAL mode) · Connection pooling · Async I/O         │
└─────────────────────────────────────────────────────────────┘
```

**Layer 1 — Ingest Mesh**: Accepts JSON, CSV, plaintext, file uploads, and RSS feeds. Embeds everything into five vector spaces (semantic, entity, relational, temporal, geospatial) using sentence-transformers and FAISS.

**Layer 2 — Enrichment Pipeline**: Async stage-chaining pipeline that extracts entities (spaCy NER), relationships, temporal context, and geospatial data from each document. LLM enrichment via Claude with configurable hourly/daily budget caps.

**Layer 3 — Crystallizer**: Background process running HDBSCAN density-based clustering. Produces a living ontology with cluster trajectories, anomaly detection, relational gradients, and convergence alerts. Snapshots capture state over time.

**Layer 4 — Continuous Critic**: Ensemble scoring system combining a PyTorch neural network with source diversity, temporal consistency, cross-space agreement, and stability signals. Scores every emergent structure without gating output.

**Layer 5 — Query Interface**: Natural language queries parsed into intents, executed across multiple embedding spaces, and synthesized via Claude API. Results rendered along a legibility gradient: high-confidence = solid entities, low-confidence = probabilistic haze.

## Process Architecture

Periphery runs as three cooperating processes that share a SQLite database:

| Process | Command | Purpose |
|---------|---------|---------|
| API server | `uvicorn periphery.main:app` | HTTP + WebSocket endpoints |
| Pipeline orchestrator | `python -m periphery.pipeline` | Enrichment → embedding → crystallization |
| RSS ingest daemon | `python -m periphery.rss_ingest` | Feed polling, dedup, full-article fetching |

## Quick Start (Docker)

1. Clone the repo and copy the example env file:
```bash
git clone <repo-url> && cd periphery
cp .env.example .env
```

2. Edit `.env` with your API keys (at minimum, `ANTHROPIC_API_KEY` and `VITE_MAPBOX_ACCESS_TOKEN`)

3. Launch:
```bash
docker compose up -d
```

4. Open `http://localhost:3000` in your browser.

The first startup takes 5-10 minutes as the backend downloads embedding models (~500MB).
The Photon geocoder container downloads its planet database (~66GB) on first run —
this can take several hours. Set `REGION=united-states` in docker-compose.yml
to download only US data (~3GB) for faster setup.

### Without Photon (lighter setup)

Comment out the `photon` service in docker-compose.yml. Geocoding will fall back to
the GeoNames local database and Nominatim API.

## Quick Start (Local)

### Setup

```bash
# Install everything (backend + frontend)
make setup

# Or manually:
pip install -e ".[dev]"
cd frontend && npm install && cd ..
```

### Configure

```bash
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY
# Works without it — just no LLM-powered synthesis or enrichment
```

### Run

```bash
# Start all processes (RSS ingest, pipeline, API server)
make run

# Or start with frontend dev server (hot-reload on port 5173)
make dev

# Or start individual processes:
make api        # API server only (port 8000)
make pipeline   # Pipeline orchestrator only
make rss        # RSS collection (30 seconds)
```

### Usage

```bash
# Ingest data
curl -X POST http://localhost:8000/ingest/ \
  -H "Content-Type: application/json" \
  -d '{"content": "Machine learning is a subset of AI.", "content_type": "text/plain"}'

# Ingest JSON
curl -X POST http://localhost:8000/ingest/ \
  -H "Content-Type: application/json" \
  -d '{"content": "{\"name\": \"Alice\", \"role\": \"engineer\"}", "content_type": "application/json"}'

# Search
curl -X POST http://localhost:8000/ingest/search \
  -H "Content-Type: application/json" \
  -d '{"query": "artificial intelligence", "top_k": 5}'

# Trigger crystallization
curl -X POST http://localhost:8000/crystallizer/crystallize

# View living ontology snapshot
curl http://localhost:8000/crystallizer/snapshot

# Analytical query (NLP-powered)
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What patterns exist in the data?"}'

# View critic scores
curl http://localhost:8000/critic/scores

# Pipeline status
curl http://localhost:8000/pipeline/stats
```

## Running Tests

```bash
make test

# Or directly:
pip install -e ".[dev]"
pytest tests/ -v
```

## API Endpoints

### Ingest

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ingest/` | POST | Ingest a document |
| `/ingest/batch` | POST | Ingest multiple documents |
| `/ingest/upload` | POST | Ingest a file upload |
| `/ingest/search` | POST | Vector similarity search |
| `/ingest/stats` | GET | Store statistics |

### Crystallizer

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/crystallizer/clusters` | GET | Current cluster assignments |
| `/crystallizer/graph` | GET | Full ontology graph |
| `/crystallizer/graph/{id}` | GET | Subgraph around a node |
| `/crystallizer/crystallize` | POST | Trigger re-clustering |
| `/crystallizer/stats` | GET | Crystallizer statistics and telemetry |
| `/crystallizer/snapshot` | GET | Full living ontology snapshot |
| `/crystallizer/snapshot/clusters` | GET | All detected clusters |
| `/crystallizer/snapshot/clusters/{id}` | GET | Specific cluster detail |
| `/crystallizer/snapshot/anomalies` | GET | Unresolved anomalies |
| `/crystallizer/snapshot/trajectories` | GET | Trajectory patterns |
| `/crystallizer/snapshot/gradients` | GET | Top relational gradients |
| `/crystallizer/snapshot/convergences` | GET | Convergence alerts |
| `/crystallizer/snapshot/emerging` | GET | Emerging structures |
| `/crystallizer/bridges` | GET | Bridge documents between clusters |

### Critic

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/critic/scores` | GET | Coherence scores for all clusters |
| `/critic/monitoring` | GET | Model version, score distribution, alerts |
| `/critic/explanations` | GET | Confidence explanations for structures |
| `/critic/score-trend` | GET | Confidence score trend over time |
| `/critic/retrain` | POST | Trigger critic retraining |
| `/critic/score-snapshot` | POST | Score current crystallizer snapshot |
| `/critic/evaluate` | POST | Evaluate document coherence |
| `/critic/outliers` | GET | Lowest-coherence structures |
| `/critic/train` | POST | Trigger adversarial training |

### Query

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/query/` | POST | Natural language query (legacy) |
| `/query/similar` | POST | Pure vector similarity search |
| `/api/query` | POST | Analytical query with NLP synthesis |

### Pipeline

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/pipeline/stats` | GET | Pipeline status, throughput, consumer health |
| `/pipeline/embedding-stats` | GET | Multi-space embedding index stats |

### Commands

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/commands/force-ingest` | POST | Run enrichment pipeline |
| `/api/commands/run-collect` | POST | Run RSS collection (30s) |
| `/api/commands/continuous-collect` | POST | Run continuous RSS collection |
| `/api/commands/status` | GET | Running process status |
| `/api/commands/stop/{name}` | POST | Stop a running process |

### WebSocket

| Endpoint | Description |
|----------|-------------|
| `/ws/snapshot` | Live crystallizer snapshot updates |
| `/ws/query/{query_id}` | Per-query progress updates |

### System

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Root with layer info |
| `/health` | GET | System health with vector/cluster counts |

## Configuration

Key settings via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required for LLM synthesis and enrichment |
| `enrichment_concurrency` | 4 | Parallel enrichment workers |
| `enrichment_llm_hourly_cap_usd` | 5 | Hourly LLM spend limit ($) |
| `enrichment_llm_daily_cap_usd` | 50 | Daily LLM spend limit ($) |
| `crystallizer_interval` | 300 | Clustering interval (seconds) |
| `crystallizer_min_cluster_size` | 5 | Minimum cluster size for HDBSCAN |
| `rss_enabled` | true | Enable RSS feed ingestion |
| `rss_fetch_full_articles` | true | Fetch full article content |
| `pipeline_enrichment_batch_size` | 10 | Documents per enrichment batch |
| `pipeline_embedding_batch_size` | 20 | Documents per embedding batch |
| `pipeline_crystallization_batch_size` | 50 | Documents before re-crystallization |

See `periphery/config.py` for the full list of options.

## Frontend

React + TypeScript + Tailwind application with:

- **Ontology graph** — interactive cluster visualization
- **Geographic overlay** — map-based entity locations
- **Temporal timeline** — time-series cluster analysis
- **Query bar** — natural language interface with confidence-rendered results
- **Data feed sidebar** — RSS feed status and ingestion tracking
- **System status bar** — pipeline and process health

```bash
cd frontend
npm install
npm run dev   # Vite dev server on port 5173
```

## Design Principles

- Schema is observation, not imposition
- Structure is output, not input
- Confidence is visible, not assumed
- Drift is structurally impossible — there is no fixed schema to drift from

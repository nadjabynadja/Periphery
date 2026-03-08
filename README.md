# Periphery

Data infrastructure where schema is emergent observation, not predefined imposition.

Structure is a continuous output of the system, not an input to it.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 Query Interface                  │
│         Natural language → Intent resolution     │
│         Legibility gradient visualization        │
├─────────────────────────────────────────────────┤
│   Continuous Critic    │     Crystallizer        │
│   Adversarial coherence│  HDBSCAN clustering     │
│   scoring network      │  Ontology graph builder  │
├─────────────────────────────────────────────────┤
│                  Ingest Mesh                     │
│     Any format → Embedding → Vector space        │
│     sentence-transformers + FAISS                │
└─────────────────────────────────────────────────┘
```

**Layer 1 — Ingest Mesh**: Accepts JSON, CSV, plaintext, file uploads. Embeds everything into a unified vector space using sentence-transformers. No schema definitions, no type mappings.

**Layer 2 — Crystallizer**: Background process running HDBSCAN density-based clustering. Detects emergent structure — entities and relationships precipitate from the data. Outputs a living ontology graph via NetworkX.

**Layer 3 — Continuous Critic**: PyTorch coherence-scoring network trained adversarially on synthetic structural perturbations. Scores every emergent entity and relationship. Does not gate output — it scores it.

**Layer 4 — Query Interface**: Natural language queries resolved against crystallized state via Claude API. Results rendered along a legibility gradient: high-confidence = solid entities, low-confidence = probabilistic haze.

## Quick Start

### Backend

```bash
# Install dependencies
pip install -e ".[dev]"

# Configure (optional — works without Claude API key, just no synthesized answers)
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY

# Run the server
uvicorn periphery.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
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

# View emergent graph
curl http://localhost:8000/crystallizer/graph

# Query with natural language
curl -X POST http://localhost:8000/query/ \
  -H "Content-Type: application/json" \
  -d '{"question": "What patterns exist in the data?"}'

# View critic scores
curl http://localhost:8000/critic/scores

# Train the critic
curl -X POST http://localhost:8000/critic/train?epochs=10
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ingest/` | POST | Ingest a document |
| `/ingest/batch` | POST | Ingest multiple documents |
| `/ingest/upload` | POST | Ingest a file upload |
| `/ingest/search` | POST | Vector similarity search |
| `/ingest/stats` | GET | Store statistics |
| `/crystallizer/clusters` | GET | Current cluster assignments |
| `/crystallizer/graph` | GET | Full ontology graph |
| `/crystallizer/graph/{id}` | GET | Subgraph around a node |
| `/crystallizer/crystallize` | POST | Trigger re-clustering |
| `/crystallizer/bridges` | GET | Bridge documents |
| `/critic/scores` | GET | Coherence scores |
| `/critic/evaluate` | POST | Score a specific document |
| `/critic/outliers` | GET | Lowest-coherence documents |
| `/critic/train` | POST | Train critic network |
| `/query/` | POST | Natural language query |
| `/query/similar` | POST | Pure vector similarity |
| `/health` | GET | System health |

## Design Principles

- Schema is observation, not imposition
- Structure is output, not input
- Confidence is visible, not assumed
- Drift is structurally impossible — there is no fixed schema to drift from

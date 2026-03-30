#!/bin/bash
set -e
mkdir -p /app/data/faiss /app/data/indices
python -m spacy validate 2>/dev/null || python -m spacy download en_core_web_sm

echo "[periphery] Initializing databases..."
python -c "
import asyncio, sys, os
from periphery.db import ensure_database, ensure_geotag_database, close_pool
async def init():
    docs_db = os.environ.get('PIPELINE_DB_PATH', '/app/data/periphery_documents.db')
    geotag_db = os.environ.get('GEOTAG_DB_PATH', '/app/data/geotag_embeddings.db')
    try:
        await ensure_database(docs_db)
        await ensure_geotag_database(geotag_db)
        print(f'[periphery] Databases ready: {docs_db}, {geotag_db}')
    finally:
        await close_pool()
try:
    asyncio.run(init())
except Exception as e:
    print(f'[periphery] Database init failed: {e}', file=sys.stderr)
    import traceback; traceback.print_exc(); sys.exit(1)
" || { echo "[periphery] FATAL: database initialization failed"; exit 1; }

echo "[periphery] Starting API server on port 8000 (API only mode)..."
exec uvicorn periphery.main:app --host 0.0.0.0 --port 8000 --log-level info

#!/bin/bash
set -e

mkdir -p /app/data/faiss /app/data/indices

# Initialize SpaCy model
python -m spacy validate 2>/dev/null || python -m spacy download en_core_web_sm

# Initialize databases (all four domain DBs + geotag)
echo "[periphery] Initializing databases..."
python -c "
import asyncio, sys, os
from periphery.db import ensure_database, ensure_collection_database, ensure_geotag_database, close_pool

async def init():
    # Collection databases
    rss_db = os.environ.get('DB_RSS_PATH', '/app/data/rss.db')
    gdelt_db = os.environ.get('DB_GDELT_PATH', '/app/data/gdelt.db')
    sanctions_db = os.environ.get('DB_SANCTIONS_PATH', '/app/data/sanctions.db')

    # Analytical database
    analytical_db = os.environ.get('DB_ANALYTICAL_PATH',
                    os.environ.get('PIPELINE_DB_PATH', '/app/data/analytical.db'))

    # Geotag database
    geotag_db = os.environ.get('GEOTAG_DB_PATH', '/app/data/geotag_embeddings.db')

    try:
        # Create collection databases with minimal schema
        for db_path in [rss_db, gdelt_db, sanctions_db]:
            await ensure_collection_database(db_path)
            print(f'[periphery] Collection DB ready: {db_path}')

        # Create analytical database with full schema
        await ensure_database(analytical_db)
        print(f'[periphery] Analytical DB ready: {analytical_db}')

        # Create geotag database
        await ensure_geotag_database(geotag_db)
        print(f'[periphery] Geotag DB ready: {geotag_db}')
    finally:
        await close_pool()

try:
    asyncio.run(init())
except Exception as e:
    print(f'[periphery] Database init failed: {e}', file=sys.stderr)
    import traceback; traceback.print_exc(); sys.exit(1)
" || { echo "[periphery] FATAL: database initialization failed"; exit 1; }

# Execute the provided command (API server, pipeline, or source runner)
exec "$@"

#!/bin/bash
set -e

# Ensure data directory exists
mkdir -p /app/data

# Initialize SpaCy model if not present (should be baked into image, but safety check)
python -m spacy validate 2>/dev/null || python -m spacy download en_core_web_sm

# Start RSS daemon in background
echo "[periphery] Starting RSS ingest daemon..."
python -m periphery.rss_ingest --no-server &
RSS_PID=$!

# Start enrichment pipeline in background
echo "[periphery] Starting enrichment pipeline..."
python -m periphery.pipeline &
PIPELINE_PID=$!

# Trap signals to clean up background processes
cleanup() {
    echo "[periphery] Shutting down..."
    kill $RSS_PID $PIPELINE_PID 2>/dev/null || true
    wait $RSS_PID $PIPELINE_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# Start API server in foreground
echo "[periphery] Starting API server on port 8000..."
exec uvicorn periphery.main:app --host 0.0.0.0 --port 8000 --log-level info

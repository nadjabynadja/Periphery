#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Activate virtual environment
if [[ -d ".venv" ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "ERROR: Virtual environment not found. Run 'bash scripts/setup.sh' first."
    exit 1
fi

# Check for .env
if [[ ! -f ".env" ]]; then
    echo "WARN: No .env file found. Copying from .env.example..."
    cp .env.example .env
    echo "      Edit .env to set ANTHROPIC_API_KEY for full functionality."
fi

# Check frontend
if [[ -d "frontend/dist" ]]; then
    echo "Frontend: http://localhost:8000/app"
else
    echo "WARN: Frontend not built. Run 'cd frontend && npm run build' for the UI."
    echo "      The API will still work at http://localhost:8000"
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

# Collect PIDs for cleanup
PIDS=()

cleanup() {
    echo ""
    echo "Shutting down all processes..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    # Wait briefly for graceful shutdown, then force-kill stragglers
    sleep 2
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    echo "All processes stopped."
}

trap cleanup EXIT INT TERM

echo ""
echo "Starting Periphery (2 processes)..."
echo ""

# Process 1: Enrichment Pipeline
echo "  [pipeline] Enrichment pipeline (enrichment → embedding → crystallization)"
python -m periphery.pipeline &
PIDS+=($!)

# Process 3: API / Frontend Server
echo "  [api]      API server on http://$HOST:$PORT"
echo ""
echo "  Health:    http://localhost:${PORT}/health"
echo "  Docs:      http://localhost:${PORT}/docs"
echo ""

uvicorn periphery.main:app --host "$HOST" --port "$PORT" --log-level info &
PIDS+=($!)

echo "All processes started. Press Ctrl+C to stop."
echo ""

# Wait for any child to exit — if one dies, bring everything down
wait -n "${PIDS[@]}" 2>/dev/null || true
echo "A process exited. Shutting down remaining processes..."

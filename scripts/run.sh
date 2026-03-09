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

echo ""
echo "Starting Periphery..."
echo "  API:     http://localhost:8000"
echo "  Health:  http://localhost:8000/health"
echo "  Docs:    http://localhost:8000/docs"
echo ""

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec uvicorn periphery.main:app --host "$HOST" --port "$PORT" --log-level info

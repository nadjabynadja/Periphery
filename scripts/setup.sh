#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Periphery MVP Setup ==="
echo ""

# ---------------------------------------------------------------------------
# 1. Check Python version
# ---------------------------------------------------------------------------
PYTHON=${PYTHON:-python3}
PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)

if [[ -z "$PY_VERSION" ]]; then
    echo "ERROR: Python 3 not found. Install Python >= 3.11 and re-run."
    exit 1
fi

PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 11) )); then
    echo "ERROR: Python >= 3.11 required (found $PY_VERSION)."
    exit 1
fi
echo "[OK] Python $PY_VERSION"

# ---------------------------------------------------------------------------
# 2. Create virtual environment
# ---------------------------------------------------------------------------
if [[ ! -d ".venv" ]]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv .venv
fi
echo "[OK] Virtual environment at .venv/"

# Activate
# shellcheck disable=SC1091
source .venv/bin/activate

# ---------------------------------------------------------------------------
# 3. Install Python dependencies
# ---------------------------------------------------------------------------
echo "Installing Python dependencies (this may take a few minutes)..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"
echo "[OK] Python dependencies installed"

# ---------------------------------------------------------------------------
# 4. Download spaCy model
# ---------------------------------------------------------------------------
SPACY_MODEL="${ENRICHMENT_SPACY_MODEL:-en_core_web_sm}"
echo "Downloading spaCy model: $SPACY_MODEL ..."
python -m spacy download "$SPACY_MODEL" --quiet 2>/dev/null || python -m spacy download "$SPACY_MODEL"
echo "[OK] spaCy model ready"

# ---------------------------------------------------------------------------
# 5. Create data directories
# ---------------------------------------------------------------------------
echo "Creating data directories..."
mkdir -p data/faiss
mkdir -p data/indices
mkdir -p data/critic_checkpoints
mkdir -p data/critic_training
echo "[OK] Data directories created"

# ---------------------------------------------------------------------------
# 6. Create .env from template
# ---------------------------------------------------------------------------
if [[ ! -f ".env" ]]; then
    cp .env.example .env
    echo ""
    echo ">>> IMPORTANT: Edit .env and set your ANTHROPIC_API_KEY <<<"
    echo "    Without it, LLM enrichment and query synthesis will be disabled."
    echo ""
else
    echo "[OK] .env already exists"
fi

# ---------------------------------------------------------------------------
# 7. Frontend setup
# ---------------------------------------------------------------------------
if command -v node &>/dev/null; then
    NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
    if (( NODE_VERSION >= 18 )); then
        echo "Installing frontend dependencies..."
        cd frontend
        npm install --silent 2>/dev/null || npm install
        echo "Building frontend..."
        npm run build
        cd "$PROJECT_ROOT"
        echo "[OK] Frontend built at frontend/dist/"
    else
        echo "WARN: Node.js >= 18 required for frontend (found v$NODE_VERSION). Skipping frontend build."
        echo "      The API will still work without the frontend."
    fi
else
    echo "WARN: Node.js not found. Skipping frontend build."
    echo "      Install Node.js >= 18 and run: cd frontend && npm install && npm run build"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env and set ANTHROPIC_API_KEY (if not done)"
echo "  2. Run:  bash scripts/run.sh"
echo "  3. Open: http://localhost:8000/app (frontend)"
echo "  4. Or:   http://localhost:8000/health (API health check)"

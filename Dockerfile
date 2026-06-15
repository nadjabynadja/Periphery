# Stage 1: Install Python dependencies (cached unless pyproject.toml changes)
FROM python:3.13-slim AS backend-deps

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy pyproject.toml and minimal package structure for pip install
COPY pyproject.toml .
COPY periphery/__init__.py ./periphery/__init__.py

RUN pip install --no-cache-dir . \
    && python -m spacy download en_core_web_sm

# Stage 2: Runtime
FROM python:3.13-slim AS backend

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from deps stage
COPY --from=backend-deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=backend-deps /usr/local/bin /usr/local/bin

# Copy application source
COPY periphery/ ./periphery/
COPY scripts/ ./scripts/
COPY Makefile .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Create data directory (will be mounted as a volume at runtime)
RUN mkdir -p /app/data

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]

# Default command run by docker-entrypoint.sh via `exec "$@"`.
# Bind to the platform-provided $PORT (Railway/Heroku-style) and fall back
# to 8000 for local runs. Without a CMD the entrypoint execs an empty command
# and the container exits after DB init, so no web server ever starts.
CMD ["sh", "-c", "uvicorn periphery.main:app --host [IP_ADDRESS] --port ${PORT:-8000} --log-level info"]

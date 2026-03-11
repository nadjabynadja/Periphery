.PHONY: setup run dev test smoke clean api pipeline rss rss-continuous

setup:
	bash scripts/setup.sh

# Start all three processes (RSS ingest, enrichment pipeline, API server)
run:
	bash scripts/run.sh

# Start all backend processes + frontend dev server (hot-reload)
dev:
	@echo "Starting backend processes and frontend dev server (port 5173)..."
	@bash scripts/run.sh &
	@cd frontend && npm run dev

# Individual process targets
api:
	.venv/bin/uvicorn periphery.main:app --host 0.0.0.0 --port 8000 --log-level info

pipeline:
	.venv/bin/python -m periphery.pipeline

rss:
	.venv/bin/python -m periphery.rss_ingest --no-server --duration 30

rss-continuous:
	.venv/bin/python -m periphery.rss_ingest --no-server

test:
	.venv/bin/pytest tests/ -v

smoke:
	bash scripts/smoke_test.sh

clean:
	rm -rf data/ .venv/ frontend/dist/ frontend/node_modules/

.PHONY: setup run dev test smoke clean

setup:
	bash scripts/setup.sh

run:
	bash scripts/run.sh

dev:
	@echo "Starting backend (port 8000) and frontend dev server (port 5173)..."
	@bash scripts/run.sh &
	@cd frontend && npm run dev

test:
	.venv/bin/pytest tests/ -v

smoke:
	bash scripts/smoke_test.sh

clean:
	rm -rf data/ .venv/ frontend/dist/ frontend/node_modules/

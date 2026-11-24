# DAR Careers developer commands. Paths are relative to the repository root.
SHELL := /bin/bash
BACKEND := cd backend &&
PY := uv run --project backend python

.PHONY: setup dev backend frontend test eval eval-live eval-report demo registry

setup:
	@test -f .env || cp .env.example .env
	$(BACKEND) uv sync
	cd frontend && npm install
	@echo "Setup complete. Add DAR_LLM_API_KEY to .env for AI features, then run: make dev"

backend:
	$(BACKEND) uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev -- --host 127.0.0.1

dev:
	@echo "API http://127.0.0.1:8000  ·  App http://127.0.0.1:5173"
	@trap 'kill 0' EXIT; $(MAKE) backend & $(MAKE) frontend & wait

test:
	$(BACKEND) uv run pytest tests -q

eval:
	$(PY) -m evals.runner --mode offline

eval-live:
	$(PY) -m evals.runner --mode live --repetitions 2

eval-report:
	$(PY) -m evals.report

demo:
	DAR_DEMO_MODE=true DAR_DATABASE_URL=sqlite:///storage/demo.db $(MAKE) dev

registry:
	$(PY) scripts/build_registry.py

discover-boards:
	$(PY) scripts/discover_boards_tavily.py --limit 300

expand-registry:
	uv run --project backend --with openpyxl python scripts/expand_registry.py --lse-xlsx "Issuer list.xlsx"

export-companies:
	uv run --project backend --with openpyxl python scripts/export_companies.py

build-frontend:
	cd frontend && npm run build

# Production-style single entry point (app + API on 127.0.0.1:8000) for the Cloudflare Tunnel
serve: build-frontend
	$(BACKEND) DAR_REQUIRE_AUTH=true uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers

tunnel:
	cloudflared tunnel run dar-careers

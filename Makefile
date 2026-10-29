# Run inside WSL/Linux. Python tooling uses uv; the venv lives outside the (possibly Windows) checkout.
# On Windows without WSL, run these commands inside containers instead (built from the Dockerfile, `test` target).
export UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/basketiq
RUN = uv run --frozen

.PHONY: install up up-llm down logs migrate test test-all lint typecheck security golden golden-real golden-unseen traceability openapi fixtures seed-perf bench

install:            ## install all dependencies (OCR + LLM extras, dev tools)
	uv sync --frozen --all-extras

up:                 ## full local stack (API, worker, scheduler, Postgres, Redis, storage, mock OIDC)
	docker compose up -d --build

up-llm:             ## full stack plus Ollama; also set LLM_ENABLED: true in config/app-config.yaml
	docker compose --profile llm up -d --build
	docker compose exec ollama ollama pull qwen2.5:3b-instruct
	@grep -Eq '^LLM_ENABLED: true' config/app-config.yaml || echo "WARNING: LLM_ENABLED is not true in config/app-config.yaml; the LLM agent stays off. Edit it and run: docker compose restart api worker"

down:
	docker compose down

logs:
	docker compose logs -f api worker scheduler

migrate:
	$(RUN) alembic upgrade head

test:               ## unit + API + golden (no OCR); needs Postgres on localhost:5432
	$(RUN) pytest -m "not ocr and not golden_real and not llm"

test-all:           ## everything including PaddleOCR and S3 integration
	$(RUN) pytest -m "not golden_real and not llm"

lint:
	$(RUN) ruff format --check src tests scripts alembic
	$(RUN) ruff check src tests scripts alembic

typecheck:
	$(RUN) mypy

security:
	$(RUN) bandit -q -r src -ll
	$(RUN) pip-audit --skip-editable

golden:             ## synthetic golden set against the committed baseline
	$(RUN) python -m basketiq.eval.golden --dir tests/golden/synthetic --baseline tests/golden/baseline.json
	$(RUN) python -m basketiq.eval.golden --dir tests/golden/synthetic --ocr --baseline tests/golden/baseline_ocr.json

golden-real:        ## real receipts (kept outside git): make golden-real GOLDEN_REAL_DIR=../Receipts
	GOLDEN_REAL_DIR=$(GOLDEN_REAL_DIR) $(RUN) pytest -m golden_real tests/golden

golden-unseen: GOLDEN_REAL_DIR ?= ../Receipts
golden-unseen:      ## real receipts read as if no shop had a parser (generic parser); add --force-unknown by hand
	$(RUN) python -m basketiq.eval.golden --dir $(GOLDEN_REAL_DIR) --ocr --unseen --baseline tests/golden/baseline_unseen_A0.json

traceability:       ## requirement → test matrix (fails on uncovered test-plan scenarios)
	$(RUN) python scripts/traceability.py --out reports/traceability.md

openapi:            ## refresh the API contract for the frontend
	$(RUN) python scripts/export_openapi.py openapi.json

fixtures:
	$(RUN) python scripts/make_synthetic_fixtures.py

seed-perf:          ## seed a performance account: make seed-perf LINES=5000
	$(RUN) python scripts/seed_perf.py --lines $(or $(LINES),5000)

bench:
	$(RUN) python scripts/seed_perf.py --lines $(or $(LINES),5000) --bench

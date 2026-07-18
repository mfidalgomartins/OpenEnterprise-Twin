SHELL := /bin/bash
.DEFAULT_GOAL := help

ifneq (,$(wildcard .env))
include .env
export
endif

PYTHON_BOOTSTRAP ?= python3.12
VENV ?= .venv
PYTHON := $(VENV)/bin/python
PIP := $(PYTHON) -m pip
COMPOSE ?= docker compose
DEV_HOST ?= 127.0.0.1
API_PORT ?= 8000
FRONTEND_PORT ?= 5173
DEMO_SEED ?= 731
DEMO_REPLICATIONS ?= 100
DEMO_TIMEOUT_SECONDS ?= 600

OPENENTERPRISE_TWIN_DATABASE_URL ?= postgresql+psycopg://openenterprise_twin:openenterprise_twin@127.0.0.1:5432/openenterprise_twin
OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY ?= artifacts
OPENENTERPRISE_TWIN_EXPERIMENT_WORKERS ?= 2
OPENENTERPRISE_TWIN_REPLICATION_WORKERS_PER_EXPERIMENT ?= 4
OPENENTERPRISE_TWIN_EXPERIMENT_SHUTDOWN_TIMEOUT_SECONDS ?= 5
OPENENTERPRISE_TWIN_CORS_ALLOWED_ORIGINS ?= ["http://$(DEV_HOST):$(FRONTEND_PORT)"]
VITE_API_BASE_URL ?= http://$(DEV_HOST):$(API_PORT)

export OPENENTERPRISE_TWIN_DATABASE_URL
export OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY
export OPENENTERPRISE_TWIN_EXPERIMENT_WORKERS
export OPENENTERPRISE_TWIN_REPLICATION_WORKERS_PER_EXPERIMENT
export OPENENTERPRISE_TWIN_EXPERIMENT_SHUTDOWN_TIMEOUT_SECONDS
export OPENENTERPRISE_TWIN_CORS_ALLOWED_ORIGINS

.PHONY: help install backend-install frontend-install db migrate seed dev test lint demo build docker-build e2e

help: ## Show the supported developer commands.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "%-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(PYTHON):
	@command -v $(PYTHON_BOOTSTRAP) >/dev/null || { \
		echo "Python 3.12 is required. Set PYTHON_BOOTSTRAP to its executable." >&2; \
		exit 1; \
	}
	$(PYTHON_BOOTSTRAP) -m venv $(VENV)

$(VENV)/.backend-installed: backend/pyproject.toml | $(PYTHON)
	$(PIP) install --upgrade pip
	$(PIP) install -e './backend[dev]'
	@touch $@

frontend/node_modules/.install-stamp: frontend/package-lock.json frontend/package.json
	cd frontend && npm ci
	@touch $@

backend-install: $(VENV)/.backend-installed ## Install the backend and development tools.

frontend-install: frontend/node_modules/.install-stamp ## Install locked frontend dependencies.

install: backend-install frontend-install ## Install backend and frontend dependencies.

db: ## Start PostgreSQL 16 and wait for its health check.
	$(COMPOSE) up -d --wait db

migrate: backend-install db ## Start PostgreSQL and apply all migrations.
	cd backend && ../$(PYTHON) -m alembic upgrade head

seed: migrate ## Migrate and seed the versioned Northstar baseline scenario.
	cd backend && ../$(PYTHON) -m openenterprise_twin.cli.demo seed

dev: install seed ## Start PostgreSQL, API and frontend with Northstar seeded.
	@set -euo pipefail; \
	(cd backend && exec ../$(PYTHON) -m uvicorn openenterprise_twin.api.app:create_app \
		--factory --reload --host $(DEV_HOST) --port $(API_PORT)) & api_pid=$$!; \
	(cd frontend && exec env VITE_API_BASE_URL='$(VITE_API_BASE_URL)' \
		npm run dev -- --host $(DEV_HOST) --port $(FRONTEND_PORT)) & frontend_pid=$$!; \
	cleanup() { kill $$api_pid $$frontend_pid 2>/dev/null || true; }; \
	trap cleanup EXIT; \
	trap 'exit 130' INT TERM; \
	echo "API:      http://$(DEV_HOST):$(API_PORT)/docs"; \
	echo "Frontend: http://$(DEV_HOST):$(FRONTEND_PORT)"; \
	while kill -0 $$api_pid 2>/dev/null && kill -0 $$frontend_pid 2>/dev/null; do sleep 1; done; \
	echo "A development process exited; stopping the local stack." >&2; \
	exit 1

test: install ## Run backend and frontend tests, excluding the long benchmark.
	cd backend && ../$(PYTHON) -m pytest -m 'not performance' -q
	cd frontend && npm run test -- --run

lint: install ## Run lint, import-boundary and type checks.
	cd backend && ../$(PYTHON) -m ruff check src tests
	cd backend && ../$(PYTHON) -m mypy src
	cd backend && ../$(VENV)/bin/lint-imports
	cd frontend && npm run lint
	cd frontend && npm run typecheck

demo: backend-install ## Create the flagship paired experiment through the API.
	cd backend && ../$(PYTHON) -m openenterprise_twin.cli.demo run \
		--api-url 'http://$(DEV_HOST):$(API_PORT)' \
		--frontend-url 'http://$(DEV_HOST):$(FRONTEND_PORT)' \
		--seed $(DEMO_SEED) \
		--replications $(DEMO_REPLICATIONS) \
		--timeout $(DEMO_TIMEOUT_SECONDS)

build: install ## Build the backend wheel and production frontend bundle.
	$(PIP) wheel --no-deps --wheel-dir backend/dist ./backend
	cd frontend && npm run build

docker-build: ## Build the production backend and frontend container images.
	docker build --file backend/Dockerfile --tag openenterprise-twin-api:local backend
	docker build --file frontend/Dockerfile --tag openenterprise-twin-web:local frontend

e2e: install ## Run Playwright, including an isolated full-stack browser flow.
	@set -euo pipefail; \
	tmp_dir=$$(mktemp -d); \
	OPENENTERPRISE_TWIN_DATABASE_URL="sqlite+pysqlite:///$$tmp_dir/e2e.db" \
	OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY="$$tmp_dir/artifacts" \
	OPENENTERPRISE_TWIN_EXPERIMENT_WORKERS=1 \
	OPENENTERPRISE_TWIN_REPLICATION_WORKERS_PER_EXPERIMENT=1 \
	OPENENTERPRISE_TWIN_CORS_ALLOWED_ORIGINS='["http://127.0.0.1:4173"]' \
		$(PYTHON) -m uvicorn openenterprise_twin.api.app:create_app \
		--factory --host 127.0.0.1 --port $(API_PORT) \
		>"$$tmp_dir/api.log" 2>&1 & api_pid=$$!; \
	cleanup() { kill $$api_pid 2>/dev/null || true; rm -rf "$$tmp_dir"; }; \
	trap cleanup EXIT; \
	for attempt in $$(seq 1 30); do \
		if $(PYTHON) -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$(API_PORT)/health', timeout=2)" >/dev/null 2>&1; then break; fi; \
		if [ "$$attempt" = 30 ]; then cat "$$tmp_dir/api.log"; exit 1; fi; \
		sleep 1; \
	done; \
	cd frontend && LIVE_E2E=1 VITE_API_BASE_URL='http://127.0.0.1:$(API_PORT)' npm run e2e

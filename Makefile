.PHONY: install format lint type test test-unit test-integration test-security check migrate run worker up down clean help

# ── Variables ─────────────────────────────────────────────────────────────────
PYTHON := python3
UV := uv

# ── Help ──────────────────────────────────────────────────────────────────────
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Setup ─────────────────────────────────────────────────────────────────────
install: ## Install all dependencies (including dev)
	$(UV) sync --all-groups

# ── Code quality ──────────────────────────────────────────────────────────────
format: ## Format code: black (style) + ruff --fix (auto-fixes) + flake8 (PEP8 gate)
	$(UV) run black --line-length=100 src tests
	$(UV) run ruff check --fix src tests
	$(UV) run flake8 src tests

lint: ## Lint with ruff and flake8 (no auto-fix — read-only check)
	$(UV) run ruff check src tests
	$(UV) run flake8 src tests

type: ## Run mypy type checker
	$(UV) run mypy src

# ── Tests ─────────────────────────────────────────────────────────────────────
test-unit: ## Run unit tests only (no external infra)
	$(UV) run pytest -m unit

test-integration: ## Run integration tests (requires testcontainers: postgres + rabbitmq)
	$(UV) run pytest -m integration

test-security: ## Run security tests (redaction / fail-closed / cross-tenant)
	$(UV) run pytest -m security

test: ## Run all tests
	$(UV) run pytest

# ── Pre-commit gate ────────────────────────────────────────────────────────────
check: format lint type test-unit ## Run format + lint + type + unit tests (CI gate)
	@echo "✓ format + lint + type + unit tests passed"

# ── Database ──────────────────────────────────────────────────────────────────
migrate: ## Run alembic migrations (upgrade head)
	$(UV) run alembic upgrade head

migrate-down: ## Rollback one migration
	$(UV) run alembic downgrade -1

migrate-history: ## Show migration history
	$(UV) run alembic history --verbose

migrate-current: ## Show current migration
	$(UV) run alembic current

# ── Application ───────────────────────────────────────────────────────────────
run: ## Start the FastAPI webhook server (reload mode)
	$(UV) run uvicorn iara.api.app:app --reload --host 0.0.0.0 --port 8000

ui: ## Start the Streamlit test UI (http://localhost:8501)
	$(UV) run streamlit run ui.py

worker: ## Start the background worker (job consumer + outbox drainer)
	$(UV) run python -m iara.workers.main

# ── Docker infra ──────────────────────────────────────────────────────────────
up: ## Start local infrastructure (postgres + rabbitmq)
	docker compose up -d postgres rabbitmq

down: ## Stop local infrastructure
	docker compose down

up-all: ## Start full stack (app + worker + infra)
	docker compose up -d

logs: ## Tail docker compose logs
	docker compose logs -f

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean: ## Remove .pyc, __pycache__, .mypy_cache, .pytest_cache, .coverage
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .pytest_cache .coverage htmlcov .ruff_cache
	@echo "✓ cleaned"

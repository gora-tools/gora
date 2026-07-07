.PHONY: help install install-dev lint format format-check typecheck test test-cov verify run-api run-cli docker-build docker-smoke clean

IMAGE ?= gora:smoke

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install core dependencies
	uv sync

install-dev: ## Install all dependencies including dev tools
	uv sync --all-extras

lint: ## Run linter
	uv run ruff check src/ tests/

format: ## Format code
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

format-check: ## Check formatting (CI parity, no writes)
	uv run ruff format --check src/ tests/

typecheck: ## Run type checker
	uv run mypy src/gora/

test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage
	uv run pytest --cov=gora --cov-report=term-missing

verify: lint format-check typecheck test ## Full gate — mirrors CI (lint, format, types, tests)

run-api: ## Run FastAPI development server
	uv run uvicorn gora.main:app --reload --port 8000

run-cli: ## Run GORA CLI (pass ARGS="status" etc.)
	uv run gora $(ARGS)

docker-build: ## Build the release image from a clean tracked-files export (reproduces CI)
	git archive --format=tar HEAD | docker build -f deployments/docker/Dockerfile -t $(IMAGE) -

docker-smoke: docker-build ## Build image and smoke-test that the CLI boots
	docker run --rm $(IMAGE) version

clean: ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

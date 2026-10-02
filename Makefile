.PHONY: help sync install install-dev lint format format-check typecheck test test-cov verify verify-export verify-linux run-api run-cli docker-build docker-smoke clean

# Gate-defining file (ADR-0016 rule 7). The gate is defined here once; CI calls these
# targets and nothing else (TESTING rule 1).
#
# `sync` is the only target that installs, and it installs exactly uv.lock: --locked fails
# on a lockfile that does not match pyproject.toml instead of rewriting it (rule 4). Every
# other target runs the synced environment as it is (`uv run --no-sync`), so none of them
# re-locks, re-syncs or drops extras.

IMAGE ?= gora:smoke
# Python 3.12 and uv on Linux, as CI. Pinning it by digest is separate work.
LINUX_IMAGE ?= ghcr.io/astral-sh/uv:python3.12-bookworm
UV_RUN := uv run --no-sync

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

sync: ## Install uv.lock exactly, all extras; fails on a stale lockfile
	uv sync --locked --all-extras

install: ## Install core dependencies only, from uv.lock
	uv sync --locked

install-dev: sync ## Install all dependencies including dev tools (same as sync)

lint: ## Run linter (check mode, no cache)
	$(UV_RUN) ruff check --no-cache src/ tests/

format: ## Format code (writes; not a gate step)
	$(UV_RUN) ruff format src/ tests/
	$(UV_RUN) ruff check --fix src/ tests/

format-check: ## Check formatting (check mode, no cache)
	$(UV_RUN) ruff format --check --no-cache src/ tests/

typecheck: ## Type-check the package and the tests (no cache)
	$(UV_RUN) mypy --cache-dir=/dev/null src/gora tests

test: ## Run tests (no cache provider)
	$(UV_RUN) pytest -p no:cacheprovider

test-cov: ## Run tests with coverage
	$(UV_RUN) pytest -p no:cacheprovider --cov=gora --cov-report=term-missing

verify: sync lint format-check typecheck test ## Full gate, check mode only; what CI runs

verify-export: ## Pre-push gate: `make verify` on committed HEAD, in a fresh export
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "verify-export: the working tree is dirty. This target checks the commit, not the tree: commit or stash first." >&2; \
		exit 1; \
	fi
	@set -e; \
	commit=$$(git rev-parse HEAD); \
	dir=$$(mktemp -d "$${TMPDIR:-/tmp}/gora-verify.XXXXXX"); \
	trap 'rm -rf "$$dir"' EXIT; \
	echo "verify-export: checking commit $$commit"; \
	git archive --format=tar HEAD | tar -x -C "$$dir"; \
	git -C "$$dir" -c init.defaultBranch=main init -q; \
	git -C "$$dir" add -A; \
	$(MAKE) -C "$$dir" verify; \
	echo "verify-export: commit $$commit is green"
# The export gets an index of exactly HEAD's tracked files: the suite-size guard reads it.

verify-linux: ## verify-export inside a Linux container, as root (CI runs on Linux)
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "verify-linux: the working tree is dirty. This target checks the commit, not the tree: commit or stash first." >&2; \
		exit 1; \
	fi
	@echo "verify-linux: checking commit $$(git rev-parse HEAD) in $(LINUX_IMAGE)"
	git archive --format=tar HEAD | docker run --rm -i -e UV_LOCKED=1 \
		-e TREE=$$(git rev-parse 'HEAD^{tree}') $(LINUX_IMAGE) sh -euc '\
		mkdir /src && tar -x -C /src && cd /src; \
		git -c init.defaultBranch=main init -q && git add -A; \
		test "$$(git write-tree)" = "$$TREE" || { echo "verify-linux: export is not HEAD tree" >&2; exit 1; }; \
		git -c user.name=verify-linux -c user.email=verify-linux@localhost commit -q -m export; \
		make verify-export'
# The container checks HEAD's tree, byte for byte: its own commit must have the same tree.

run-api: ## Run FastAPI development server
	$(UV_RUN) uvicorn gora.main:app --reload --port 8000

run-cli: ## Run GORA CLI (pass ARGS="status" etc.)
	$(UV_RUN) gora $(ARGS)

docker-build: ## Build the release image from a clean tracked-files export (reproduces CI)
	@git diff --quiet && git diff --cached --quiet || echo "⚠️  working tree is dirty — docker-smoke builds committed HEAD (git archive), NOT your uncommitted changes. Commit first to test them."
	git archive --format=tar HEAD | docker build -f deployments/docker/Dockerfile -t $(IMAGE) -

docker-smoke: docker-build ## Run the image as production does: read-only, offline
	docker run --rm --read-only --network none $(IMAGE) version
	docker run --rm --read-only --network none \
		-v "$(CURDIR)/contracts/examples/release-manifest.yaml:/manifest/release-manifest.yaml:ro" \
		$(IMAGE) status -m /manifest/release-manifest.yaml

clean: ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

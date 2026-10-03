.PHONY: help sync install install-dev lint format format-check typecheck test test-cov verify verify-export verify-linux run-api run-cli docker-platform-check docker-build docker-smoke clean

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
# The platform of every container gate: CI's runner and the release image are linux/amd64.
# linux/arm64 runs natively on Apple silicon but is not CI parity; nothing falls back to it.
PLATFORM ?= linux/amd64
# Printed at the start and end of a container target when PLATFORM is not CI's.
PARITY_NOTE = $(if $(filter linux/amd64,$(PLATFORM)),,@echo "PLATFORM=$(PLATFORM) is not CI parity")
# Every gate container is started by this script (TESTING rule 8): labelled gora.gate, named
# per run, bounded from inside by timeout(1), removed on exit, INT and TERM, and refused while
# another gate container is left. A recipe execs it, because make signals only its recipe.
GATE_RUN := scripts/gate/run-container.sh
# Inner limits, in seconds. verify-linux: about 4x its wall time under amd64 emulation.
VERIFY_LINUX_TIMEOUT ?= 900
PROBE_TIMEOUT ?= 60
SMOKE_TIMEOUT ?= 60
# What the release image's ENTRYPOINT runs; docker-smoke checks it and runs it under timeout.
IMAGE_ENTRYPOINT := /app/.venv/bin/gora

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

docker-platform-check: ## Refuse a PLATFORM this Docker host cannot run; no fallback
	$(PARITY_NOTE)
	@case "$(PLATFORM)" in \
		linux/amd64) want=x86_64 ;; \
		linux/arm64) want=aarch64 ;; \
		*) echo "docker-platform-check: PLATFORM=$(PLATFORM) is not supported: linux/amd64 (CI parity) or linux/arm64" >&2; exit 2 ;; \
	esac; \
	out=$$(mktemp "$${TMPDIR:-/tmp}/gora-platform.XXXXXX"); \
	$(GATE_RUN) docker-platform-check $(PROBE_TIMEOUT) --platform "$(PLATFORM)" -- $(LINUX_IMAGE) uname -m > "$$out" & pid=$$!; \
	trap 'kill -TERM $$pid 2>/dev/null; wait $$pid; rm -f "$$out"; exit 143' TERM; \
	trap 'kill -TERM $$pid 2>/dev/null; wait $$pid; rm -f "$$out"; exit 130' INT; \
	rc=0; wait $$pid || rc=$$?; got=$$(cat "$$out"); rm -f "$$out"; \
	if [ $$rc = 2 ]; then exit 2; fi; \
	if [ $$rc != 0 ]; then got="(docker run failed, exit $$rc)"; fi; \
	echo "docker-platform-check: $(PLATFORM) runs as uname -m: $$got"; \
	if [ "$$got" != "$$want" ]; then \
		echo "docker-platform-check: this Docker host cannot run $(PLATFORM) containers (expected $$want, got $$got)." >&2; \
		echo "  colima: start it with --vm-type vz --vz-rosetta" >&2; \
		echo "  Docker Desktop: enable Rosetta for x86_64/amd64 emulation" >&2; \
		echo "  PLATFORM=linux/arm64 runs natively here, but it is not CI parity." >&2; \
		exit 2; \
	fi

verify-linux: docker-platform-check ## verify-export in a Linux container, as root, on PLATFORM
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "verify-linux: the working tree is dirty. This target checks the commit, not the tree: commit or stash first." >&2; \
		exit 1; \
	fi
	@echo "verify-linux: checking commit $$(git rev-parse HEAD) in $(LINUX_IMAGE) on $(PLATFORM), limit $(VERIFY_LINUX_TIMEOUT) s"
	@exec $(GATE_RUN) verify-linux $(VERIFY_LINUX_TIMEOUT) --archive HEAD -i --platform "$(PLATFORM)" \
		-e UV_LOCKED=1 -e TREE=$$(git rev-parse 'HEAD^{tree}') -- $(LINUX_IMAGE) sh -euc '\
		mkdir /src && tar -x -C /src && cd /src; \
		git -c init.defaultBranch=main init -q && git add -A; \
		test "$$(git write-tree)" = "$$TREE" || { echo "verify-linux: export is not HEAD tree" >&2; exit 1; }; \
		git -c user.name=verify-linux -c user.email=verify-linux@localhost commit -q -m export; \
		make verify-export'
	$(PARITY_NOTE)
# The container checks HEAD's tree, byte for byte: its own commit must have the same tree.
# A timed-out run exits 124.

run-api: ## Run FastAPI development server
	$(UV_RUN) uvicorn gora.main:app --reload --port 8000

run-cli: ## Run GORA CLI (pass ARGS="status" etc.)
	$(UV_RUN) gora $(ARGS)

docker-build: docker-platform-check ## Build the release image on PLATFORM, no layer cache
	@git diff --quiet && git diff --cached --quiet || echo "⚠️  working tree is dirty — docker-smoke builds committed HEAD (git archive), NOT your uncommitted changes. Commit first to test them."
	@git archive --format=tar HEAD | docker build --pull --no-cache --platform "$(PLATFORM)" \
		-f deployments/docker/Dockerfile -t $(IMAGE) - & pid=$$!; \
	trap 'kill -TERM $$pid 2>/dev/null; wait $$pid; exit 143' TERM; \
	trap 'kill -TERM $$pid 2>/dev/null; wait $$pid; exit 130' INT; \
	wait $$pid
	$(PARITY_NOTE)
# --no-cache: a cached layer skips its RUN step, so it is a result cache (ADR-0016 rule 4).

docker-smoke: docker-build ## Run the image as production does: read-only, offline, PLATFORM
	@got=$$(docker image inspect -f '{{.Os}}/{{.Architecture}}' $(IMAGE)); \
	entry=$$(docker image inspect -f '{{json .Config.Entrypoint}}' $(IMAGE)); \
	echo "docker-smoke: $(IMAGE) is $$got, user $$(docker image inspect -f '{{.Config.User}}' $(IMAGE)), entrypoint $$entry"; \
	if [ "$$got" != "$(PLATFORM)" ]; then echo "docker-smoke: $(IMAGE) is $$got, not $(PLATFORM)" >&2; exit 2; fi; \
	if [ "$$entry" != '["$(IMAGE_ENTRYPOINT)"]' ]; then echo "docker-smoke: entrypoint is $$entry, not $(IMAGE_ENTRYPOINT)" >&2; exit 2; fi
	@exec $(GATE_RUN) docker-smoke $(SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-- $(IMAGE) $(IMAGE_ENTRYPOINT) version
	@exec $(GATE_RUN) docker-smoke $(SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-v "$(CURDIR)/contracts/examples/release-manifest.yaml:/manifest/release-manifest.yaml:ro" \
		-- $(IMAGE) $(IMAGE_ENTRYPOINT) status -m /manifest/release-manifest.yaml
	$(PARITY_NOTE)
# The smoke runs the entrypoint's binary under timeout(1) and checks the entrypoint itself.

clean: ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

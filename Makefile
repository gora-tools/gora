.PHONY: help sync sync-test install install-dev lint format format-check typecheck test test-cov check check-steps behaviour behaviour-steps verify verify-steps verify-export verify-export-steps verify-linux run-api run-cli docker-platform-check docker-build docker-smoke image-smoke clean

# Gate-defining file (ADR-0016 rule 7). The gate is defined here once; CI calls these
# targets (TESTING rule 1).
#
# Gates are placed by what they read (ADR-021 section 4). Source checks (`check`: ruff,
# format, mypy, shellcheck) read text and run once; behaviour checks (`behaviour`: pytest)
# run code and run on every platform. `verify` runs both. Each syncs only the dependency
# group it needs, and installs exactly uv.lock: --locked fails on a lockfile that does not
# match pyproject.toml instead of rewriting it (rule 4). Every other target runs the synced
# environment as it is (`uv run --no-sync`), so none of them re-locks, re-syncs or drops
# extras.

IMAGE ?= gora:smoke
UV_RUN := uv run --no-sync
# Every image a gate runs is pinned by digest in deployments/docker/gate-images.Dockerfile.
# Read once, when first used; image.sh prints nothing unless the pin is a digest.
LINUX_IMAGE ?= $(eval LINUX_IMAGE := $$(shell scripts/gate/image.sh linux-gate))$(LINUX_IMAGE)
# The exact Python of the release image, declared once in deployments/docker/Dockerfile and
# read by scripts/gate/versions.sh, as CI reads it. Read once, when first used.
RELEASE_PYTHON = $(eval RELEASE_PYTHON := $$(shell scripts/gate/versions.sh | sed -n 's/^python=//p'))$(RELEASE_PYTHON)
# The Docker host's own platform. Container gates run on it by default (ADR-021 section 5):
# no gate runs under emulation. Read once, when first used.
NATIVE_PLATFORM = $(eval NATIVE_PLATFORM := $$(shell docker version --format '{{.Server.Os}}/{{.Server.Arch}}' 2>/dev/null))$(NATIVE_PLATFORM)
PLATFORM ?= $(NATIVE_PLATFORM)
# Another PLATFORM still runs, for diagnosis, and says so at its start and end: emulated, so
# its result is not a gate result. Native hardware for every platform is CI's (ADR-021).
# (No comma in the message: it would end the $(if) argument.)
EMULATED_NOTE = $(if $(filter-out $(NATIVE_PLATFORM),$(PLATFORM)),@echo "PLATFORM=$(PLATFORM) is emulated on this $(or $(NATIVE_PLATFORM),unknown) Docker host: not a gate")
# Every gate container is started by this script (TESTING rule 8): its image pulled under
# PULL_TIMEOUT, labelled gora.gate, named per run, bounded from inside by timeout(1), removed
# on exit, INT and TERM, and refused while another gate container is left. A recipe execs
# it, because make signals only its recipe.
GATE_RUN := scripts/gate/run-container.sh
# Every gate run on the host is bounded from outside by this script: GNU timeout, or no run.
BOUNDED := scripts/gate/bounded.sh
# Limits, in seconds (TESTING rule 8): each at least 10x a green native run with its
# downloads (TESTING records the runs). verify-export's is above verify's, so verify's own
# limit ends a hung suite first. A pull is bounded by the network, not by a run.
CHECK_TIMEOUT ?= 180
BEHAVIOUR_TIMEOUT ?= 600
VERIFY_TIMEOUT ?= 600
VERIFY_EXPORT_TIMEOUT ?= 720
VERIFY_LINUX_TIMEOUT ?= 600
DOCKER_BUILD_TIMEOUT ?= 300
PULL_TIMEOUT ?= 300
PROBE_TIMEOUT ?= 60
SMOKE_TIMEOUT ?= 60
export PULL_TIMEOUT
# What the release image's ENTRYPOINT runs; image-smoke checks it and runs it under timeout.
IMAGE_ENTRYPOINT := /app/.venv/bin/gora
# A distribution from each dependency group; image-smoke fails if the image carries one.
GROUP_TOOLS := pytest pytest-timeout jsonschema ruff mypy shellcheck-py

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

sync: ## Install uv.lock exactly, all extras, both tool groups; fails on a stale lockfile
	uv sync --locked --all-extras --group check

sync-test: ## Install uv.lock exactly, all extras, the test group only
	uv sync --locked --all-extras --group test

install: ## Install core dependencies only, from uv.lock
	uv sync --locked

install-dev: sync ## Install all dependencies including dev tools (same as sync)

lint: ## Lint (check mode, no cache): ruff, and shellcheck on every tracked *.sh
	$(UV_RUN) ruff check --no-cache src/ tests/
	@n=$$(git ls-files '*.sh' | wc -l | tr -d ' '); \
	if [ "$$n" -eq 0 ]; then echo "lint: git lists no *.sh file to check" >&2; exit 1; fi; \
	echo "shellcheck --norc: $$n tracked *.sh"; \
	git ls-files -z '*.sh' | xargs -0 $(UV_RUN) shellcheck --norc
# --norc: no .shellcheckrc, in the tree or in a home directory, can switch a check off.

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

check: ## Source checks, once per commit (CI: check): lint, format, types, under CHECK_TIMEOUT
	@exec $(BOUNDED) $(CHECK_TIMEOUT) $(MAKE) --no-print-directory check-steps
check-steps: sync lint format-check typecheck

behaviour: ## Behaviour checks, per platform (CI: test): the test group and pytest, bounded
	@exec $(BOUNDED) $(BEHAVIOUR_TIMEOUT) $(MAKE) --no-print-directory behaviour-steps
behaviour-steps: sync-test test

verify: ## Full local gate: source and behaviour checks, check mode, under VERIFY_TIMEOUT
	@exec $(BOUNDED) $(VERIFY_TIMEOUT) $(MAKE) --no-print-directory verify-steps
verify-steps: sync lint format-check typecheck test

verify-export: ## Pre-push gate: `make verify` on committed HEAD, in a fresh export, bounded
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "verify-export: the working tree is dirty. This target checks the commit, not the tree: commit or stash first." >&2; \
		exit 1; \
	fi
	@exec $(BOUNDED) $(VERIFY_EXPORT_TIMEOUT) $(MAKE) --no-print-directory verify-export-steps
verify-export-steps:
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
	$(EMULATED_NOTE)
	@if [ -z "$(PLATFORM)" ]; then \
		echo "docker-platform-check: cannot read the Docker host's platform; is the Docker daemon running?" >&2; \
		exit 2; \
	fi
	@case "$(PLATFORM)" in \
		linux/amd64) want=x86_64 ;; \
		linux/arm64) want=aarch64 ;; \
		*) echo "docker-platform-check: PLATFORM=$(PLATFORM) is not supported: linux/amd64 or linux/arm64" >&2; exit 2 ;; \
	esac; \
	out=$$(mktemp "$${TMPDIR:-/tmp}/gora-platform.XXXXXX"); \
	$(GATE_RUN) docker-platform-check $(PROBE_TIMEOUT) --platform "$(PLATFORM)" -- $(LINUX_IMAGE) uname -m > "$$out" & pid=$$!; \
	trap 'kill -TERM $$pid 2>/dev/null; wait $$pid; rm -f "$$out"; exit 143' TERM; \
	trap 'kill -TERM $$pid 2>/dev/null; wait $$pid; rm -f "$$out"; exit 130' INT; \
	rc=0; wait $$pid || rc=$$?; got=$$(cat "$$out"); rm -f "$$out"; \
	if [ $$rc = 2 ] || [ $$rc = 124 ] || [ $$rc = 137 ]; then exit $$rc; fi; \
	if [ $$rc != 0 ]; then got="(docker run failed, exit $$rc)"; fi; \
	echo "docker-platform-check: $(PLATFORM) runs as uname -m: $$got"; \
	if [ "$$got" != "$$want" ]; then \
		echo "docker-platform-check: this Docker host cannot run $(PLATFORM) containers (expected $$want, got $$got)." >&2; \
		echo "  $(NATIVE_PLATFORM) is this host's own platform, and the default." >&2; \
		echo "  To emulate another one for diagnosis: colima --vm-type vz --vz-rosetta, or Docker Desktop's Rosetta setting." >&2; \
		exit 2; \
	fi

verify-linux: docker-platform-check ## Behaviour checks on committed HEAD in a Linux container, as root, on PLATFORM
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
		make behaviour'
	$(EMULATED_NOTE)
# The container checks HEAD's tree, byte for byte, then runs the behaviour checks in that
# fresh export; the source checks do not depend on the platform and ran on the host. A
# timed-out run exits 124.

run-api: ## Run FastAPI development server
	$(UV_RUN) uvicorn gora.main:app --reload --port 8000

run-cli: ## Run GORA CLI (pass ARGS="status" etc.)
	$(UV_RUN) gora $(ARGS)

docker-build: docker-platform-check ## Build the release image on PLATFORM from HEAD, no layer cache, bounded
	@git diff --quiet && git diff --cached --quiet || echo "⚠️  working tree is dirty — docker-smoke builds committed HEAD (git archive), NOT your uncommitted changes. Commit first to test them."
	@exec $(BOUNDED) $(DOCKER_BUILD_TIMEOUT) sh -c 'git archive --format=tar HEAD | \
		docker build --pull --no-cache --platform "$(PLATFORM)" -f deployments/docker/Dockerfile -t $(IMAGE) -'
	$(EMULATED_NOTE)
# --no-cache: a cached layer skips its RUN step, so it is a result cache (ADR-0016 rule 4).
# A local build is for the smoke test only; a release image is built in CI (ADR-021).

docker-smoke: docker-build ## Build the release image, then image-smoke it
	@$(MAKE) --no-print-directory image-smoke

image-smoke: ## Run IMAGE as production does (read-only, offline, PLATFORM) and check it
	$(EMULATED_NOTE)
	@scripts/gate/pull.sh $(IMAGE) "$(PLATFORM)" $(PULL_TIMEOUT)
	@got=$$(docker image inspect --platform "$(PLATFORM)" -f '{{.Os}}/{{.Architecture}}' $(IMAGE) 2>/dev/null || \
		docker image inspect -f '{{.Os}}/{{.Architecture}}' $(IMAGE)); \
	entry=$$(docker image inspect -f '{{json .Config.Entrypoint}}' $(IMAGE)); \
	echo "image-smoke: $(IMAGE) is $$got, user $$(docker image inspect -f '{{.Config.User}}' $(IMAGE)), entrypoint $$entry"; \
	if [ "$$got" != "$(PLATFORM)" ]; then echo "image-smoke: $(IMAGE) is $$got, not $(PLATFORM)" >&2; exit 2; fi; \
	if [ "$$entry" != '["$(IMAGE_ENTRYPOINT)"]' ]; then echo "image-smoke: entrypoint is $$entry, not $(IMAGE_ENTRYPOINT)" >&2; exit 2; fi
	@if [ -z "$(RELEASE_PYTHON)" ]; then echo "image-smoke: no PYTHON_VERSION read from deployments/docker/Dockerfile" >&2; exit 2; fi
	@echo "image-smoke: expecting Python $(RELEASE_PYTHON) (deployments/docker/Dockerfile) and none of: $(GROUP_TOOLS)"
	@exec $(GATE_RUN) image-smoke $(SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-e WANT="Python $(RELEASE_PYTHON)" -- $(IMAGE) sh -euc '\
		got=$$(/app/.venv/bin/python --version); echo "image-smoke: $$got"; \
		test "$$got" = "$$WANT" || { echo "image-smoke: $$got, not $$WANT" >&2; exit 1; }'
	@exec $(GATE_RUN) image-smoke $(SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-- $(IMAGE) /app/.venv/bin/python -c 'import sys, importlib.metadata as m; \
		found = [n for n in sys.argv[1:] if any(m.distributions(name=n))]; \
		print("image-smoke: dependency-group tools in the image:", " ".join(found) or "none"); \
		sys.exit(1 if found else 0)' $(GROUP_TOOLS)
	@exec $(GATE_RUN) image-smoke $(SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-- $(IMAGE) $(IMAGE_ENTRYPOINT) version
	@exec $(GATE_RUN) image-smoke $(SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-v "$(CURDIR)/contracts/examples/release-manifest.yaml:/manifest/release-manifest.yaml:ro" \
		-- $(IMAGE) $(IMAGE_ENTRYPOINT) status -m /manifest/release-manifest.yaml
	$(EMULATED_NOTE)
# The smoke runs the entrypoint's binary under timeout(1) and checks the entrypoint itself.

clean: ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

.PHONY: help sync sync-test lint format format-check typecheck test test-cov check check-steps behaviour behaviour-steps verify verify-steps verify-export verify-export-steps verify-linux run-api run-cli docker-platform-check docker-build docker-smoke image-smoke clean

# Gate-defining file (ADR-0016 rule 7). The gate is defined here once; CI calls these
# targets (TESTING rule 1).
#
# Gates are placed by what they read (ADR-021 section 4). Source checks (`check`: ruff,
# format, mypy, shellcheck) read text and run once; behaviour checks (`behaviour`: pytest)
# run code and run on every platform. `verify` runs both. Each syncs only the dependency
# group it needs, and installs exactly uv.lock: --locked fails on a lockfile that does not
# match pyproject.toml instead of rewriting it (rule 4). Each also installs the group
# `build` and builds the project with it, not isolated: an isolated build resolves the
# backend from the index, unhashed (TESTING rule 3; tests/unit/test_build_pins.py reads every
# sync). Every other target runs the synced environment as it is (`uv run --no-sync`), so
# none of them re-locks, re-syncs or drops extras.

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
# A bounded run is named by its limit's variable, and the name and its value come from these
# two macros alone, so that they cannot disagree: the report of a run says `VERIFY_TIMEOUT`,
# and that is the key of the limit's row in TESTING rule 8 (tests/unit/test_limits.py holds
# every use to them).
#   $(call bounded,NAME)           the command prefix of a run bounded by $(NAME)
#   $(call gate_run,TARGET,NAME)   the command prefix of a container bounded by $(NAME)
bounded = $(BOUNDED) $(1) $($(1))
gate_run = $(GATE_RUN) $(1) $(2) $($(2))
# Limits, in seconds (TESTING rule 8): each at least 10x the slowest of at least three green
# runs at rest, rounded up to a whole minute, and every run ends with a line that says how much
# of its limit it used (TESTING records the runs). An outer limit is above the limits it
# contains, so that the inner one ends a hung run first. A pull is bounded by the network, not
# by a run.
CHECK_TIMEOUT ?= 180
BEHAVIOUR_TIMEOUT ?= 600
VERIFY_TIMEOUT ?= 600
VERIFY_EXPORT_TIMEOUT ?= 720
VERIFY_LINUX_TIMEOUT ?= 600
RED_FIRST_TIMEOUT ?= 1500
DOCKER_BUILD_TIMEOUT ?= 300
PULL_TIMEOUT ?= 300
PROBE_TIMEOUT ?= 60
SMOKE_TIMEOUT ?= 60
export PULL_TIMEOUT
# What the release image's ENTRYPOINT runs; image-smoke checks it and runs it under timeout.
IMAGE_ENTRYPOINT := /app/.venv/bin/gora
# GORA's own license, as the image's label and its distribution's metadata state it. The
# base image and the dependencies carry their own.
IMAGE_LICENSE := Apache-2.0
# The sha256 of standard input, on a GNU or a BSD userland, as scripts/gate/*.sh take it.
SHA256 := { sha256sum 2> /dev/null || shasum -a 256; } | cut -d' ' -f1
# The commit image-smoke expects the image to carry (L9): its revision label, and the
# revision `gora version` prints. Default HEAD, the commit docker-build builds. Read once.
REVISION ?= $(eval REVISION := $$(shell git rev-parse HEAD 2>/dev/null))$(REVISION)
# Every distribution the dependency groups of pyproject.toml name; image-smoke fails if the
# image carries one. tests/unit/test_build_pins.py holds the two lists equal.
GROUP_TOOLS := pytest pytest-cov pytest-timeout jsonschema pyyaml ruff mypy types-PyYAML types-jsonschema shellcheck-py hatchling
# The build backend's exact version, from pyproject.toml's group `build` (TESTING rule 3);
# image-smoke checks that it built the wheel in the image. Read once.
BUILD_BACKEND = $(eval BUILD_BACKEND := $$(shell sed -n 's/^ *"hatchling==\([0-9.]*\)".*/\1/p' pyproject.toml))$(BUILD_BACKEND)
# The exact Debian version of git the release image installs (TESTING rule 3); image-smoke
# checks the installed package and `git --version` against it. Read once.
RELEASE_GIT = $(eval RELEASE_GIT := $$(shell sed -n 's/^ARG GIT_VERSION=//p' deployments/docker/Dockerfile))$(RELEASE_GIT)
# The user the release image runs as, the USER of deployments/docker/Dockerfile (never root);
# image-smoke compares the image's configured user with it. Read once.
RELEASE_USER = $(eval RELEASE_USER := $$(shell sed -n 's/^USER //p' deployments/docker/Dockerfile))$(RELEASE_USER)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

sync: ## Install uv.lock exactly, all extras, both tool groups and the build backend; fails on a stale lockfile
	uv sync --locked --all-extras --group check --group build --no-build-isolation-package gora

sync-test: ## Install uv.lock exactly, all extras, the test group and the build backend
	uv sync --locked --all-extras --group test --group build --no-build-isolation-package gora

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
	@exec $(call bounded,CHECK_TIMEOUT) $(MAKE) --no-print-directory check-steps
check-steps: sync lint format-check typecheck

behaviour: ## Behaviour checks, per platform (CI: test): the test group and pytest, bounded
	@exec $(call bounded,BEHAVIOUR_TIMEOUT) $(MAKE) --no-print-directory behaviour-steps
behaviour-steps: sync-test test

verify: ## Full local gate: source and behaviour checks, check mode, under VERIFY_TIMEOUT
	@exec $(call bounded,VERIFY_TIMEOUT) $(MAKE) --no-print-directory verify-steps
verify-steps: sync lint format-check typecheck test

verify-export: ## Pre-push gate: `make verify` on committed HEAD, in a fresh export, bounded
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "verify-export: the working tree is dirty. This target checks the commit, not the tree: commit or stash first." >&2; \
		exit 1; \
	fi
	@exec $(call bounded,VERIFY_EXPORT_TIMEOUT) $(MAKE) --no-print-directory verify-export-steps
verify-export-steps:
	@set -e; \
	commit=$$(git rev-parse HEAD); \
	dir=$$(mktemp -d "$${TMPDIR:-/tmp}/gora-verify.XXXXXX"); \
	trap 'rm -rf "$$dir"' EXIT; \
	echo "verify-export: checking commit $$commit"; \
	git archive --format=tar HEAD | tar -x -C "$$dir"; \
	git -C "$$dir" -c init.defaultBranch=main init -q; \
	git -C "$$dir" -c core.excludesFile=/dev/null add -A; \
	if [ "$$(git -C "$$dir" write-tree)" != "$$(git rev-parse 'HEAD^{tree}')" ]; then \
		echo "verify-export: the export's index is not HEAD's tree: an ignore rule left a tracked file out" >&2; \
		exit 1; \
	fi; \
	$(MAKE) -C "$$dir" verify; \
	echo "verify-export: commit $$commit is green"
# The export's index is exactly HEAD's tracked files, and is checked to be, whatever the
# host's global excludes say (core.excludesFile is not read): the suite-size guard reads it.

docker-platform-check: ## Refuse a PLATFORM this Docker host cannot run; no fallback
	$(EMULATED_NOTE)
	@if [ -z "$(LINUX_IMAGE)" ]; then \
		echo "docker-platform-check: no gate image read from deployments/docker/gate-images.Dockerfile (scripts/gate/image.sh linux-gate)" >&2; \
		exit 2; \
	fi
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
	$(call gate_run,docker-platform-check,PROBE_TIMEOUT) --platform "$(PLATFORM)" -- $(LINUX_IMAGE) uname -m > "$$out" & pid=$$!; \
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
	@exec $(call gate_run,verify-linux,VERIFY_LINUX_TIMEOUT) --archive HEAD -i --platform "$(PLATFORM)" \
		-e UV_LOCKED=1 -e TREE=$$(git rev-parse 'HEAD^{tree}') -- $(LINUX_IMAGE) sh -euc '\
		mkdir /src && tar -x -C /src && cd /src; \
		git -c init.defaultBranch=main init -q && git add -A; \
		test "$$(git write-tree)" = "$$TREE" || { echo "verify-linux: export is not HEAD tree" >&2; exit 1; }; \
		make behaviour'
	$(EMULATED_NOTE)
# The container checks HEAD's tree, byte for byte, then runs the behaviour checks in that
# fresh export; the source checks do not depend on the platform and ran on the host. A
# timed-out run exits 124. Without a LINUX_IMAGE, docker-platform-check, which it runs
# first, stops with exit 2.

red-first: ## Red-first proof (ADR-016 rule 1): TESTS, or CHECK=docker-smoke with EXPECT, on exported HEAD with BASE/FILES or PATCH applied
	@exec $(call bounded,RED_FIRST_TIMEOUT) scripts/gate/red-first.sh --tests "$$TESTS" \
		--check "$$CHECK" --expect "$$EXPECT" \
		--base "$$BASE" --files "$$FILES" --patch "$$PATCH"
# TESTS: pytest node ids or paths, space-separated. With BASE=<rev> FILES="<paths>", each path
# takes its content at BASE (removed when BASE lacks it); with PATCH=<file>, a diff is applied
# with git apply; both may be combined. The proof is exit 0 and `red-first: <n> failed`; green
# is exit 1, anything else exit 2. The tests run in an export of committed HEAD, like
# verify-export's, and the working tree is only read: a dirty one is refused. The variables
# reach the script through the environment, which is how make passes a command-line variable.
# Instead of TESTS, CHECK=docker-smoke EXPECT="<text>" runs the export's own docker-smoke,
# which builds the image from the export, as it is and with the change applied: the proof is
# exit 0, a failure that printed EXPECT. Exactly one of TESTS and CHECK; EXPECT only with CHECK.
# RED_FIRST_TIMEOUT bounds both modes, at least 10x red-first's own tests run twice, with the
# margin the other limits have (TESTING rule 8 records the runs).

run-api: ## Run FastAPI development server
	$(UV_RUN) uvicorn gora.main:app --reload --port 8000

run-cli: ## Run GORA CLI (pass ARGS="version" etc.)
	$(UV_RUN) gora $(ARGS)

docker-build: docker-platform-check ## Build the release image on PLATFORM from HEAD, no layer cache, bounded
	@git diff --quiet && git diff --cached --quiet || echo "⚠️  working tree is dirty — docker-smoke builds committed HEAD (git archive), NOT your uncommitted changes. Commit first to test them."
	@exec $(call bounded,DOCKER_BUILD_TIMEOUT) sh -c 'commit=$$(git rev-parse HEAD) && \
		git archive --format=tar "$$commit" | docker build --pull --no-cache --platform "$(PLATFORM)" \
		--build-arg GORA_REVISION="$$commit" -f deployments/docker/Dockerfile -t $(IMAGE) -'
	$(EMULATED_NOTE)
# --no-cache: a cached layer skips its RUN step, so it is a result cache (ADR-0016 rule 4).
# A local build is for the smoke test only; a release image is built in CI (ADR-021). The
# image carries the commit it is built from as GORA_REVISION (L9).

docker-smoke: docker-build ## Build the release image, then image-smoke it
	@$(MAKE) --no-print-directory image-smoke

image-smoke: ## Run IMAGE as production does (read-only, offline, PLATFORM) and check it
	$(EMULATED_NOTE)
	@scripts/gate/pull.sh $(IMAGE) "$(PLATFORM)" $(PULL_TIMEOUT)
	@got=$$(docker image inspect --platform "$(PLATFORM)" -f '{{.Os}}/{{.Architecture}}' $(IMAGE) 2>/dev/null || \
		docker image inspect -f '{{.Os}}/{{.Architecture}}' $(IMAGE)); \
	entry=$$(docker image inspect -f '{{json .Config.Entrypoint}}' $(IMAGE)); \
	user=$$(docker image inspect -f '{{.Config.User}}' $(IMAGE)); \
	echo "image-smoke: $(IMAGE) is $$got, user $$user, entrypoint $$entry"; \
	if [ "$$got" != "$(PLATFORM)" ]; then echo "image-smoke: $(IMAGE) is $$got, not $(PLATFORM)" >&2; exit 2; fi; \
	if [ "$$entry" != '["$(IMAGE_ENTRYPOINT)"]' ]; then echo "image-smoke: entrypoint is $$entry, not $(IMAGE_ENTRYPOINT)" >&2; exit 2; fi; \
	if [ -z "$(RELEASE_USER)" ]; then echo "image-smoke: no USER read from deployments/docker/Dockerfile" >&2; exit 2; fi; \
	if [ "$$user" != "$(RELEASE_USER)" ]; then echo "image-smoke: the image runs as '$$user', not $(RELEASE_USER) (the USER of deployments/docker/Dockerfile)" >&2; exit 2; fi
	@if [ -z "$(REVISION)" ]; then echo "image-smoke: no REVISION to check the image against (git rev-parse HEAD)" >&2; exit 2; fi
	@format='{{index .Config.Labels "org.opencontainers.image.revision"}}'; \
	label=$$(docker image inspect --platform "$(PLATFORM)" -f "$$format" $(IMAGE) 2>/dev/null || \
		docker image inspect -f "$$format" $(IMAGE)); \
	echo "image-smoke: label org.opencontainers.image.revision=$$label, expecting $(REVISION)"; \
	if [ "$$label" != "$(REVISION)" ]; then echo "image-smoke: the image is labelled $$label, not $(REVISION)" >&2; exit 1; fi
	@format='{{index .Config.Labels "org.opencontainers.image.licenses"}}'; \
	label=$$(docker image inspect --platform "$(PLATFORM)" -f "$$format" $(IMAGE) 2>/dev/null || \
		docker image inspect -f "$$format" $(IMAGE)); \
	echo "image-smoke: label org.opencontainers.image.licenses=$$label, expecting $(IMAGE_LICENSE)"; \
	if [ "$$label" != "$(IMAGE_LICENSE)" ]; then echo "image-smoke: the image is labelled licenses=$$label, not $(IMAGE_LICENSE)" >&2; exit 1; fi
	@if [ -z "$(BUILD_BACKEND)" ]; then echo "image-smoke: no hatchling==X.Y.Z read from the group build of pyproject.toml" >&2; exit 2; fi
	@echo "image-smoke: expecting the wheel of gora built by hatchling $(BUILD_BACKEND) (pyproject.toml, group build)"
	@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-e WANT="hatchling $(BUILD_BACKEND)" -- $(IMAGE) /app/.venv/bin/python -c 'import os, sys, importlib.metadata as m; \
		got = [line.split(": ", 1)[1] for line in (m.distribution("gora").read_text("WHEEL") or "").splitlines() if line.startswith("Generator: ")]; \
		print("image-smoke: gora wheel Generator:", ", ".join(got) or "absent"); \
		sys.exit(None if got == [os.environ["WANT"]] else "image-smoke: the wheel of gora was not built by " + os.environ["WANT"])'
	@git cat-file -e "$(REVISION):LICENSE" && git cat-file -e "$(REVISION):NOTICE" || \
		{ echo "image-smoke: $(REVISION) holds no LICENSE or NOTICE to check the image against" >&2; exit 2; }
	@license=$$(git show "$(REVISION):LICENSE" | $(SHA256)) && notice=$$(git show "$(REVISION):NOTICE" | $(SHA256)) && \
	exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-e WANT_LICENSE="$(IMAGE_LICENSE)" -e LICENSE_SHA256="$$license" -e NOTICE_SHA256="$$notice" \
		-- $(IMAGE) /app/.venv/bin/python -c 'import hashlib, os, sys, importlib.metadata as m; \
		d = m.distribution("gora"); \
		expr = d.metadata.get("License-Expression"); \
		names = sorted(d.metadata.get_all("License-File") or []); \
		want = {"LICENSE": os.environ["LICENSE_SHA256"], "NOTICE": os.environ["NOTICE_SHA256"]}; \
		got = {n: sorted(hashlib.sha256(f.locate().read_bytes()).hexdigest() for f in (d.files or []) if f.parts[-2:] == ("licenses", n)) for n in want}; \
		print("image-smoke: gora License-Expression:", expr, "License-File:", " ".join(names)); \
		[print("image-smoke: licenses/" + n + " sha256", ",".join(got[n]) or "absent", "expecting", want[n]) for n in sorted(want)]; \
		sys.exit(None if expr == os.environ["WANT_LICENSE"] and names == sorted(want) and all(got[n] == [want[n]] for n in want) \
			else "image-smoke: gora in the image is not " + os.environ["WANT_LICENSE"] + " with LICENSE and NOTICE as $(REVISION) holds them")'
	@if [ -z "$(RELEASE_PYTHON)" ]; then echo "image-smoke: no PYTHON_VERSION read from deployments/docker/Dockerfile" >&2; exit 2; fi
	@echo "image-smoke: expecting Python $(RELEASE_PYTHON) (deployments/docker/Dockerfile) and none of: $(GROUP_TOOLS)"
	@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-e WANT="Python $(RELEASE_PYTHON)" -- $(IMAGE) sh -euc '\
		got=$$(/app/.venv/bin/python --version); echo "image-smoke: $$got"; \
		test "$$got" = "$$WANT" || { echo "image-smoke: $$got, not $$WANT" >&2; exit 1; }'
	@if [ -z "$(RELEASE_GIT)" ]; then echo "image-smoke: no GIT_VERSION read from deployments/docker/Dockerfile" >&2; exit 2; fi
	@echo "image-smoke: expecting the Debian package git $(RELEASE_GIT) (deployments/docker/Dockerfile), run at GORA_GIT's default"
	@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-e WANT="$(RELEASE_GIT)" -- $(IMAGE) /app/.venv/bin/python -c 'import os, re, subprocess, sys; \
		from gora.config.settings import Settings; \
		run = lambda *argv: subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False).stdout.strip(); \
		want = os.environ["WANT"]; upstream = re.sub(r"-[^-]*$$", "", want.split(":", 1)[-1]); git = Settings().git; \
		package = run("dpkg-query", "--show", "--showformat=$${Version}", "git"); \
		version = run(git, "--version") if os.access(git, os.X_OK) else ""; \
		print("image-smoke: package git", package or "absent", "and", git, "prints", version or "nothing"); \
		sys.exit(None if package == want and version == "git version " + upstream else "image-smoke: the image does not run git " + want + " at " + git)'
	@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-- $(IMAGE) /app/.venv/bin/python -c 'import sys, importlib.metadata as m; \
		found = [n for n in sys.argv[1:] if any(m.distributions(name=n))]; \
		print("image-smoke: dependency-group tools in the image:", " ".join(found) or "none"); \
		sys.exit(1 if found else 0)' $(GROUP_TOOLS)
	@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only --network none --platform "$(PLATFORM)" \
		-e WANT="revision: $(REVISION)" -- $(IMAGE) sh -euc '\
		got=$$($(IMAGE_ENTRYPOINT) version); printf "%s\n" "$$got"; \
		want=$$(printf "%s\nversion:  not set" "$$WANT"); \
		test "$$got" = "$$want" || { echo "image-smoke: gora version printed something else; expected:" >&2; \
			printf "%s\n" "$$want" >&2; exit 1; }'
	$(EMULATED_NOTE)
# The smoke runs the entrypoint's binary under timeout(1) and checks the entrypoint itself.
# `gora version` must print REVISION and no version: the image carries no version (L9).

clean: ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

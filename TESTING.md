# GORA Testing Policy

Authoritative — as binding as an ADR. In the spirit of Debian Policy: quality is a **rule,
not a wish**. GORA is a release-control mechanism, so it holds itself to a higher bar than
what it manages. "Release when it's ready."

## Non-negotiable rules

1. **The gate is defined once, and placed by what it reads** (ADR-021 section 4). The checks
   are `Makefile` targets, and CI calls them.
   - **Source checks**, `make check`: `make sync` (`uv sync --locked --all-extras --group
     check --group build --no-build-isolation-package gora`: a stale `uv.lock` fails it),
     then, in check mode with no cache, ruff check +
     shellcheck on every tracked `*.sh` + ruff format --check + mypy on `src/gora` and
     `tests`. They read text, whose result does not depend on the processor: **once** per
     commit.
   - **Behaviour checks**, `make behaviour`: `make sync-test` (the `test` group, and `build`
     as above), then pytest. They run code: on **every supported platform**.
   - `make verify` runs both on one machine.

   The development tools are two PEP 735 dependency groups in `pyproject.toml`, installed
   only when a target names one and never in the release image: `test` (pytest,
   pytest-timeout, pytest-cov, jsonschema) and `check` (ruff, mypy and its stubs,
   shellcheck-py, and `test`, because mypy reads the tests). A behaviour run installs no
   tool that has no build for its platform: `shellcheck-py` ships no `linux/arm64` wheel. No
   step writes what it checks (ADR-016 rule 4). CI runs every check on the exact Python and
   uv the release image is built with; locally any patch of the minor version
   `.python-version` names, and any uv from the floor up (rule 3). The suite refuses any other
   minor version. The group `build` (the build backend, rule 3) is installed by every sync
   that installs the project, and builds it: an isolated build would resolve the backend
   from the index, unhashed.

   **CI is the gate of record** (ADR-021 section 5): a commit is green when CI is green, on
   native hardware for every supported platform. Local runs save a round trip and catch most
   failures early; they are never reported as evidence. `.github/workflows/ci.yml`:

   | Check | Runner | Runs |
   | --- | --- | --- |
   | `check` | `ubuntu-24.04` | `make check` |
   | `test (linux/amd64)`, `test (linux/arm64)` | `ubuntu-24.04`, `ubuntu-24.04-arm` | `make behaviour` |
   | `image (linux/amd64)`, `image (linux/arm64)` | `ubuntu-24.04`, `ubuntu-24.04-arm` | the release image built from the checkout (tracked files only, no layer cache, bases pulled by digest), pushed by digest to a registry on the runner, `make image-smoke` on that digest, the commit built included (L9) |
   | `assemble` | `ubuntu-24.04` | both images, byte for byte, into one image index, checked to hold exactly `linux/amd64` and `linux/arm64` at the digests the image jobs built, the index and each platform manifest carrying the commit built as `org.opencontainers.image.revision` (`scripts/gate/revision.sh`, L9) |

   `image` and `assemble` run `.github/actions/build-image` and
   `.github/actions/assemble-index`, the steps the release workflow runs for a candidate
   (ADR-021 sections 1–2): every pull request rehearses the release path, and nothing is
   pushed to GHCR from CI.

   A step of a workflow or an action that holds a pipe runs under `shell: bash`, which is
   `pipefail`, or sets `pipefail` itself: GitHub's default shell ends a pipe with the status of
   its last command, so `versions.sh | tee` exiting 2 would leave the outputs empty and the
   step green. `tests/unit/test_build_pins.py` reads every `run:`.

   Two more required checks run from their own workflows. `pr-title` (`ubuntu-24.04`,
   `.github/workflows/pr-title.yml`) checks every pull request title as a Conventional Commit
   with `scripts/gate/pr-title.sh` (L11), when the title can change and on every new head, and
   that the subject it makes on `main`, with ` (#<number>)`, is at most 72 bytes; the
   titles Dependabot generates are exempt from the length by actor, never by text.
   `gate-change` is the commit status of `.github/workflows/gate-change.yml`, which marks a
   change to a gate-defining file, or a test removed, no longer collected or switched off,
   until it is reviewed (ADR-016 rule 7).
2. **Gates reproduce production, not the working tree.** The pre-push gate is
   **`make verify-export`**: `make verify` on committed `HEAD`, in a fresh `git archive`
   export, so the working tree and the caches cannot make it green. It refuses a dirty tree
   and prints the commit it checked. **`make verify-linux`** runs the behaviour checks on
   `HEAD` inside a Linux container, as root; use it on macOS. `make docker-smoke` builds the
   release image from `git archive HEAD` and runs `make image-smoke` on it, read-only and
   offline (`--read-only --network none`): the image's platform, entrypoint and user (the
   Dockerfile's `USER`), its
   `org.opencontainers.image.revision` label against the commit built, its
   `org.opencontainers.image.licenses` label (`Apache-2.0`), the `gora` distribution's
   `License-Expression` and its `LICENSE` and `NOTICE`, byte for byte as the commit built
   holds them, `python --version` against the Dockerfile's `PYTHON_VERSION`, no tool of
   either dependency group in the image, then `version`, which must print that commit as
   its revision and no version (L9).
   Every one of these checks a commit: **commit first**, then run them.

   The container gates (`verify-linux`, `docker-build`, `docker-smoke`) run on the **Docker
   host's own platform** by default (ADR-021 section 5): `linux/arm64` on Apple silicon,
   `linux/amd64` on an x86-64 host. No gate runs under emulation. `make
   docker-platform-check` runs first and stops (exit 2) when the host cannot run `PLATFORM`.
   Another platform can be requested for diagnosis (`PLATFORM=linux/amd64` on Apple silicon,
   with colima `--vm-type vz --vz-rosetta` or Docker Desktop's Rosetta setting); such a run
   says at its start and end `PLATFORM=linux/amd64 is emulated on this linux/arm64 Docker
   host: not a gate`, and is never reported as a gate result. Native hardware for the other
   platform is CI's.
3. **Determinism first — dogfood the thesis.** Committed lockfiles, digest-pinned artifacts,
   reproducible builds. Apply GORA's own standard to GORA.

   **Exact where a result is produced, a range where a developer works** (ADR-021, amendment
   2026-10-03). CI and the release image are where results are produced: there every input
   is one exact version. A developer's machine is a safety net (rule 1): there Python and uv
   are a range, so a patch release does not ripple onto it. **One declaration per input**;
   everything else derives from it or is checked against it, by
   `tests/unit/test_build_pins.py`, which fails on an unpinned input or two that disagree:

   | Input | Declared once in | Exact or range | Derived or checked | Updated by |
   | --- | --- | --- | --- | --- |
   | Python, release and CI | `ARG PYTHON_VERSION` in `deployments/docker/Dockerfile`, under the `python:3.12-slim-trixie` digest | exact patch | the image build fails on a base with another interpreter; `scripts/gate/versions.sh` gives it to CI's setup-uv (`python-version`), and the `test` job checks the suite ran on it; `make image-smoke` compares `python --version` | the reviewer, in the pull request that moves the base digest |
   | Python, developer | `.python-version` | minor (`3.12`): any patch | the suite refuses another minor version; the test checks it is the release's minor | by hand, with a new minor version (a decision, not an update) |
   | uv, release and CI | the `uv` stage of `deployments/docker/Dockerfile`, by digest | exact | `versions.sh` gives it to setup-uv (`version`); the `linux-gate` image carries the same version | Dependabot, `docker` (with `linux-gate`, one group) |
   | uv, developer | `[tool.uv] required-version` in `pyproject.toml` | floor (`>=0.8.12`) | uv refuses to run below it (every uv from 0.5.21 reads the setting); the test keeps it a floor, not above the pin | by hand, only when the project needs a newer uv feature or lock format — never with a pin bump. The floor is the lowest uv proven to run `make verify-export` here (0.8.12, 2026-10-21: the first uv that installs a package built without isolation after the group it needs, which every sync of the gates relies on; 0.8.11 and the releases below it fail on the missing backend, not on the lock, which 0.5.21 reads at revision 5) |
   | Base images | `python:3.12-slim-trixie` by the digest of its multi-platform index, in both stages: the tag names the Debian release | exact | the two stages carry one digest, and the codename is the one `debian.sources` names | Dependabot, `docker`; minor and major versions are ignored |
   | Gate images | `deployments/docker/gate-images.Dockerfile`: `linux-gate` (`verify-linux`, `docker-platform-check`), `buildkit` (the builder), `registry` (the CI rehearsal), each by digest | exact | read by `scripts/gate/image.sh`, never typed elsewhere | Dependabot, `docker` |
   | Actions | each `uses:`, by full commit SHA, version in a comment | exact | — | Dependabot, `github-actions` (workflows and `.github/actions/*`) |
   | Runners | `runs-on`, `ubuntu-24.04` and `ubuntu-24.04-arm`; never `ubuntu-latest` | a label: GitHub updates the image behind the name | the test allows these two names only | by hand |
   | Python packages | `uv.lock`, `--locked` everywhere | exact | every third-party module `src/gora` imports is provided by a dependency `pyproject.toml` declares, not by a dependency of one (`tests/unit/test_declared_dependencies.py`) | Dependabot, `uv`: one group, one pull request a week; `increase` raises each `pyproject.toml` floor with it, because a floor is the lowest version proven: the one `uv.lock` carries and CI runs |
   | Build backend | the group `build` of `pyproject.toml`, `hatchling==X.Y.Z`, locked with hashes in `uv.lock` | exact in the release image; `[build-system] requires` is a floor where a developer builds | every `uv sync` that installs the project (`make sync`, `make sync-test`, `scripts/gate/red-first.sh`, the image) installs that group and passes `--no-build-isolation-package gora`; the image then drops the group; `dev-mode-dirs` in `pyproject.toml` keeps the editable install of those syncs free of `editables`, which is no package of the lock; the test reads every one of those syncs and keeps the floor at or below the pin; `make image-smoke` checks that the wheel's `Generator` is that version | Dependabot, `uv`, with the Python packages |
   | Debian packages, release image | `deployments/docker/debian.sources`: for `trixie` and `trixie-updates`, and for `trixie-security`, an import of `snapshot.debian.org`, replacing the base's sources; `deployments/docker/debian.lock`: the SHA-256 of each Release file that snapshot serves; both inputs of the artifact (L8) | exact (the Release files' hashes) | the build compares the Release files apt fetched with the lock and fails on any other set; `apt-get update` fails on any error, so an unreachable snapshot fails the build, as another apt source does, never falling back to the live archive; `tests/unit/test_build_pins.py` holds the form of both files and the codename | by hand, with `GIT_VERSION`: the updater and the image scan are decided, not built (ADR-011, release-model section 13) |
   | git, release image | `ARG GIT_VERSION` in the runtime stage of `deployments/docker/Dockerfile`, the Debian package version | exact (epoch, upstream version and Debian revision) | the version the snapshot holds, or the install fails; the install names it alone, with no recommended package, and apt adds only the packages it depends on (`tests/unit/test_build_pins.py` reads the command); `make image-smoke` compares the installed package and `git --version` | by hand, with the snapshot |
   | buildx, cosign, syft | an exact version in the action input or workflow `env` | exact | buildx is set up in `release.yml` and in the actions `assemble-index` and `build-image`: the test holds one action pin, one version and one `cache-binary` across the three | by hand |
   | crane | version and SHA-256 per platform in `scripts/gate/crane.sh` | exact | the download is checked against the SHA-256 | by hand, from the release's `checksums.txt` |

   A Dependabot pull request that moves the Python base digest to a new patch fails the image
   build, visibly, until `PYTHON_VERSION` names that patch; if the pinned uv cannot install it
   for CI, the uv pin moves in the same pull request.
4. **Every bug becomes a test or check.** A defect found once gets a regression guard so it
   cannot return.
   The guard comes with its red-first proof (ADR-016 rule 1); a test's is made by `make red-first
   TESTS="<pytest node ids or paths>"` and one of `BASE=<rev> FILES="<paths>"` (each path as
   at `BASE`, removed if `BASE` lacks it) or `PATCH=<file>` (a diff, applied with `git apply`);
   both may be combined. It runs the tests on an export of committed `HEAD`, as `make
   verify-export` does, and first as `HEAD` has them: the baseline. A test already red there
   would "fail" whatever the change did, so a baseline that is not green ends the run, with
   each test that failed or errored as `baseline: <id>` and nothing applied. Only then the
   change is applied, the environment synced again and the tests run again. The working tree
   is only read: it is refused when dirty and never changed. The last `red-first:` line is the verdict:
   `red-first: <n> failed` (exit 0) is the proof; `red-first: GREEN — nothing failed` (exit 1)
   is not one; `red-first: ERROR (...)` (exit 2) is a baseline that is not green, a collection
   error, a test that does not exist, a failed sync or a refusal, none of which is a kill.
   `make` shows any failure as its own exit 2, and the report of the limit (rule 8) ends the
   output, so the last `red-first:` line is what to read. The script
   is `scripts/gate/red-first.sh`, tested by `tests/unit/test_red_first.py`.

   A defect in what the image build reads (the Dockerfile, `debian.lock`, the rest of the
   build context) reaches no test, and `make docker-build` builds committed `HEAD`, so it
   has a mode of its own: `make red-first CHECK=docker-smoke EXPECT="<text>"`, with the same
   `BASE`/`FILES` or `PATCH`. Exactly one of `TESTS` and `CHECK`; `CHECK` accepts
   `docker-smoke` only; `EXPECT` is required with `CHECK` and refused without it; anything
   else is exit 64. The export of `HEAD` is committed (a fixed author, no hook, no signing)
   and its tree must be `HEAD`'s. The check is the export's own `make docker-smoke` under
   `env -i`, with only `PATH`, `HOME`, `TMPDIR`, `LC_ALL` and the caller's `DOCKER_*`
   variables, so no make variable of the caller reaches it and it builds on the native
   platform. First as `HEAD` has it: the baseline must exit 0 and must not print `EXPECT`,
   else a failure that printed it would prove nothing, and nothing is applied. Then the
   change is applied, the export is committed again and `docker-smoke` runs again; a kill is
   a second run that exits non-zero and whose output holds `EXPECT` as a fixed string.
   `EXPECT` is matched against the output as shown, after `scrub`, so an absolute path in it
   is written `<path>`, and an `EXPECT` that names one is refused (exit 64); how it is
   chosen, and why, is ADR-016 rule 1. The last `red-first:` line: `red-first: docker-smoke failed, as
   EXPECT says` (exit 0), with each shown line holding `EXPECT` above it, is the proof;
   `red-first: GREEN — docker-smoke passed` (exit 1) is not one; `red-first: ERROR (...)`
   (exit 2) is `baseline docker-smoke failed`, `EXPECT is in the green baseline's output` or
   `docker-smoke failed without EXPECT` (a failed pull or a refused network is not the
   defect), each with the last 20 lines above it, or a refusal, an export or a patch that
   failed. No verdict prints an exit code: `make` ends every failed recipe with its own exit
   2, and what failed is in the lines above. The run's image is removed on every exit, INT
   and TERM included. This mode syncs no environment.
5. **Test the real thing, not a proxy.** Supply chain verified by `cosign verify` /
   attestation checks in CI, never assumed. The release pipeline self-verifies its own
   artifact (signature + SBOM + provenance) before it is trusted. A checker of foreign
   evidence is proven on a statement a foreign producer made, committed as a fixture; its own
   producer's statement is the second fixture, not the first.
6. **No network and no machine config in automated tests — every layer.** External systems
   are behind bounded adapters (ADR-006) and are **mocked** in unit and integration tests
   alike (`httpx.MockTransport`, fakes, FastAPI `TestClient` + dependency overrides). Fast,
   deterministic, offline. The Git adapter is tested on the real git instead: it reaches no
   external system, only a local repository, and a fake of git would prove nothing about the
   git that runs (below).

   Enforced by a plugin that `tests/conftest.py` registers. It is active for the whole pytest
   session, from configuration to exit, which covers collection and module imports, fixtures
   of every scope, every thread, and every Python child process:
   - Settings come from code defaults and the test itself. No file is read from the cwd,
     and `GORA_*` variables are removed from the environment.
   - Tests never touch the real home. Each test gets its own `HOME`, `XDG_STATE_HOME`,
     `XDG_CONFIG_HOME` and `GORA_AUDIT_LOG` under its `tmp_path` (the `_isolated_home`
     fixture), in the test's process and in every child, so the audit journal a mutating
     command writes (ADR-017) never lands in `~/.local/state`. `GORA_AUDIT_LOG` is the one
     `GORA_*` variable a test starts with.
   - Outcomes do not depend on the terminal the suite runs in. The variables Rich, Typer,
     Click and pytest read for colour, terminal type and size are removed: `FORCE_COLOR`,
     `NO_COLOR`, `CLICOLOR`, `CLICOLOR_FORCE`, `PY_COLORS`, `GITHUB_ACTIONS`, `TERM`,
     `COLORTERM`, `COLUMNS`, `LINES`, `TERMINAL_WIDTH`, `TTY_COMPATIBLE`, `TTY_INTERACTIVE`,
     `_TYPER_FORCE_DISABLE_TERMINAL`. A test that needs one sets it itself; child processes
     inherit the cleaned environment.
   - `socket.getaddrinfo`, and `connect`, `connect_ex`, `sendto` and `sendmsg` on any
     non-Unix socket, raise `NetworkBlockedError`, and the attempt is recorded, whether or
     not the code under test catches the error. `sendto` and `sendmsg` are blocked because a
     UDP datagram needs no `connect`.
   - Where the attempt is charged:
     - In the main thread, inside a test (its setup, call or teardown): that test fails.
     - Anywhere else, no single test is charged: the session fails (exit 1) under the
       header "network access not charged to any test", and each entry names where the
       attempt happened. This covers collection, the setup of a session-, package-, module-
       or class-scoped fixture, and any other thread, including one that runs while a test
       does (for example asyncio's resolver thread) or after the last test. Threads a test
       leaves behind are joined, with a 5 s grace, before the session is judged.
   - A Python child process (for example the real CLI on a pty) runs under the same block:
     a `sitecustomize` the guard writes leads `PYTHONPATH`, in the session's environment
     and in any `env` passed to `subprocess.Popen`. A sitecustomize it shadows still runs.
     The child's attempt is charged to the test that started it, whatever the child did
     with the error and whatever its exit code; a child started outside any test, or one
     that reaches out after its test has ended, fails the session.
   - There is no API to switch it off. No fixture exposes it, a test's `monkeypatch.undo()`
     does not reach it, and `-p no:gora-hermetic-guard` is a usage error.

   Known limits:
   - The teardown of a broader-scoped fixture runs inside the teardown of the test that
     closes that scope, so it is charged to that test. It is still red.
   - An attempt from a thread that outlives the grace period is still blocked, but it comes
     too late to change the exit code. The same holds for a child process still running
     when the session ends: it is not waited for.
   - A child started from another thread while a test runs is charged to that test.
   - A child gets the network block only. Its settings come from its environment, which
     has no `GORA_*` variable except the test's own `GORA_AUDIT_LOG`, unless the test passed
     one.

   Bypasses that only deliberate sabotage reaches, left to review:
   - patching `socket` back, or calling the `_socket` C module directly;
   - network access outside Python sockets (`subprocess` running a non-Python program,
     `ctypes`);
   - a Python child started with `-I`, `-E` or `-S`, which ignore `PYTHONPATH` or skip
     `sitecustomize`, or a grandchild started with an `env` that leaves the guard out;
   - reaching into the plugin through `config.pluginmanager` and unregistering it or
     editing its state;
   - `Settings(_env_file=...)`, or writing `env_file` back into `Settings.model_config`;
   - running pytest with `--noconftest`.

   A test run needs one program besides Python: **git**.
   `tests/integration/test_identifiers_against_git.py` runs the real `git check-ref-format`
   to show that every identifier GORA accepts is a ref git accepts (ADR-003),
   `tests/integration/test_input_globs_against_git.py` runs `git ls-files` with `glob`
   pathspecs over an index-only repository to show that GORA matches an input glob as git does
   (ADR-026 section 9), and `tests/integration/test_tag_message_against_git.py` runs
   `git tag -a -F` and `git interpret-trailers` to show that git keeps the rc tag's message
   byte for byte and reads the one trailer GORA reads (ADR-027 sections 1 and 4).
   `tests/integration/test_git_adapter.py` runs the Git adapter itself on repositories built
   with git's plumbing, hostile and rewritten ones among them, and compares the history it
   reads with `git log`; on GORA's own `main`, committed as a bundle
   (`tests/fixtures/git/gora-main.bundle`), it compares every first-parent commit's changed
   paths with `git diff-tree` and every tree with `git ls-tree`. Without git
   they fail rather than skip: a differential test that cannot run proves nothing. They run
   git with no user or system configuration (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL` set
   to the null device, its own `HOME`), so the machine's git config cannot change a verdict.
7. **Fail closed.** On any failure the delegated agent stops and reports — no work-arounds.
8. **A gate ends.** Every gate run ends in bounded time, green or red, and leaves nothing
   running. Incident: on 2026-10-02 two `verify-linux` runs hung on linux/amd64 under Rosetta
   and their containers stayed up for hours. Proven cause: processes started from a thread
   pool deadlock the whole interpreter under that emulation. Over fresh containers the git
   oracle hung 15 of 18 times with 8 threads (vfork), 5 of 18 with fork, 0 of 30 sequential;
   0 of 5 on native arm64 and natively on macOS. In 17 of the 20 hangs nothing inside the
   process ran again: no pytest-timeout timer, no faulthandler dump. Since ADR-021 no gate
   runs under emulation: a green `verify-linux` took about 121 s emulated, and takes 65 s
   natively (the runs below).
   - **Every test has a timeout**: pytest-timeout, 120 s from setup to teardown, over 5x the
     slowest test under amd64 emulation (15.7 s). Method `signal`: it ended 2 of 17 hangs and
     printed the stacks; the `thread` method's timer ran in none of 3. `tests/conftest.py`
     refuses to run without the plugin, with a timeout that is not positive, with another
     method, or with a test that sets its own `timeout` marker.
   - **Tests do not fan subprocesses out over threads.** A child process is started one at a
     time: the conftest `Popen` wrapper raises `ConcurrentSpawnError` for a second one while
     the first is being created, the state every hang above was in.
   - **Every gate run is bounded from outside.** `make verify`, `verify-export`, `check`,
     `behaviour` and `red-first` run under `scripts/gate/bounded.sh`: GNU timeout (`timeout` on Linux,
     `gtimeout` on macOS, from Homebrew coreutils), TERM to every process of the run at the
     limit, KILL 10 s later, exit 124. Without it the gate does not start (exit 2): it never
     runs unbounded. `docker-build` is bounded the same way. In CI every job also has
     `timeout-minutes`.
   - **Container gates are bounded, labelled and removed.** Every container `verify-linux`,
     `docker-platform-check` and `image-smoke` start goes through
     `scripts/gate/run-container.sh`: its image pulled first under `PULL_TIMEOUT` (300 s; an
     image already present for the platform is not pulled), label `gora.gate=<target>`, a
     name unique to the run, `timeout(1)` as its entrypoint (exit 124 at the limit), removed on
     exit, INT and TERM. A run refuses to start (exit 2) while a `gora.gate` container is left,
     and prints the `docker rm -f` that removes it. The limit inside the container is the
     guarantee: it holds when the hung process can run nothing and when `make` itself is
     killed.
   - **Every bounded run says how much of its limit it used.** `bounded.sh` and
     `run-container.sh` end each run, green, red or at its limit, with one line on standard
     error, made by `scripts/gate/limit-report.sh`:

         bounded.sh: VERIFY_TIMEOUT: 184 s of 1860 s (10%), load 5.04 on 10 CPUs

     The name is the limit's variable in the Makefile and the key of its row below; the Makefile
     passes name and value together from two macros, `bounded` and `gate_run`, and a call
     without a name is refused (exit 64). The seconds are whole (`SECONDS`: a second under at
     most) and the share is rounded up. The load is the 1-minute average when the run started,
     since a gate loads the machine itself: the first field of `/proc/loadavg` on Linux, else
     `sysctl -n vm.loadavg` on macOS; the CPUs are `getconf _NPROCESSORS_ONLN`; a source that
     cannot be read prints `unknown`. When the run took more than a tenth of its limit the line
     ends with `; above a tenth of the limit, TESTING rule 8 does not hold`: the run has
     outgrown the margin this rule gives its limit, and the limit is measured again (below).
     The line never changes the exit code, which is the command's: a slow machine never turns a
     correct gate red, only the limit does. A container's line is for the `docker run`, not the
     pull before it, and prints the load and CPUs of the machine its script runs on, the Docker
     host; a `bounded.sh` inside the gate container (`verify-linux`'s `make behaviour`) prints
     the container's. A pull is named `PULL_TIMEOUT` and never gets the sentence: the network
     bounds it, not a run. `make red-first`'s output ends with the line, after the verdict.
     `tests/unit/test_bounded.py` runs the scripts.
   - **The limits** are each at least 10x the slowest of at least three green runs measured at
     rest, rounded up to a whole minute. At rest is the load at the start of the run under the
     CPU count, as the line of the run prints them (the line printed on the machine that started
     it). The Makefile's limits are measured on this machine (Apple silicon, 10 CPUs; colima
     with 4), downloads included: `make check`, `behaviour` and `verify` once from an empty uv
     cache and no `.venv`, `verify-export` and `verify-linux` always from nothing. CI's are the
     slowest of the last five green runs of each job on `main`, read with `gh`; the release
     workflow runs on a tag, so its rows are its last run, dated. A limit below its 10x is
     raised; no measurement lowers one. An outer limit is above the limits it contains, so that
     the inner one ends a hung run first: `VERIFY_EXPORT_TIMEOUT` above `VERIFY_TIMEOUT`,
     `VERIFY_LINUX_TIMEOUT` above `BEHAVIOUR_TIMEOUT`, `RED_FIRST_TIMEOUT` above
     `DOCKER_BUILD_TIMEOUT`, and CI's `check` and `test` jobs above `CHECK_TIMEOUT` and
     `BEHAVIOUR_TIMEOUT`. `tests/unit/test_limits.py` holds this table to the Makefile and the
     workflows: one row for each `*_TIMEOUT` of the Makefile (but `PULL_TIMEOUT`) and for each
     job's `timeout-minutes`, named by its source (the variable, or `workflow file:job id`) with
     its value; no row without a source; each limit at least 10x its slowest run; each measured
     run at rest; the nesting above.

     Measured on 2026-10-24, the release rows on the date of the release run. The table's form
     is the test's: these columns, one row on one line. Load is the highest at the start of the
     row's runs and CPUs those of the machine that printed it; a job of the code host has
     neither.

     | Limit | Value | Date | Runs | Slowest | Load | CPUs | Run |
     | --- | --- | --- | --- | --- | --- | --- | --- |
     | `CHECK_TIMEOUT` | 180 s | 2026-10-24 | 5 | 14 s | 4.87 | 10 | `make check`: 14 s from an empty uv cache and no `.venv`, 2 s to 3 s with one |
     | `BEHAVIOUR_TIMEOUT` | 1740 s | 2026-10-24 | 5 | 174 s | 4.70 | 10 | `make behaviour`: 174 s from nothing, 155 s to 159 s with a `.venv`; 64 s inside the Linux gate container at a load under 1 on 4 CPUs |
     | `VERIFY_TIMEOUT` | 1860 s | 2026-10-24 | 9 | 184 s | 5.04 | 10 | `make verify`: 184 s from nothing, 160 s to 163 s with a `.venv`; 168 s to 178 s inside `verify-export` |
     | `VERIFY_EXPORT_TIMEOUT` | 1920 s | 2026-10-24 | 4 | 179 s | 3.55 | 10 | `make verify-export`: 168 s to 170 s, 179 s from an empty uv cache; above `VERIFY_TIMEOUT` |
     | `VERIFY_LINUX_TIMEOUT` | 1800 s | 2026-10-24 | 3 | 65 s | 3.84 | 10 | `make verify-linux`, `linux/arm64`: CPython and every wheel downloaded; above `BEHAVIOUR_TIMEOUT` |
     | `RED_FIRST_TIMEOUT` | 1500 s | 2026-10-24 | 6 | 97 s | 4.02 | 10 | `make red-first`: 92 s to 97 s proving its own tests red (they run twice), 34 s to 45 s with `CHECK=docker-smoke`; a run of more tests is held to the same limit |
     | `DOCKER_BUILD_TIMEOUT` | 300 s | 2026-10-24 | 3 | 24 s | 3.82 | 10 | the build of `make docker-smoke`, no layer cache: 23 s to 24 s |
     | `PROBE_TIMEOUT` | 60 s | 2026-10-24 | 6 | 1 s | 3.84 | 10 | `docker-platform-check`, in `docker-smoke` and `verify-linux`: 0 s to 1 s |
     | `SMOKE_TIMEOUT` | 60 s | 2026-10-24 | 18 | 1 s | 4.00 | 10 | each smoke container of `docker-smoke`, six to a run: 0 s to 1 s |
     | `ci.yml:check` | 10 min | 2026-10-24 | 5 | 15 s | - | - | job `check` |
     | `ci.yml:test` | 30 min | 2026-10-24 | 5 | 102 s | - | - | job `test`: `linux/amd64` 102 s, `linux/arm64` 86 s; above `BEHAVIOUR_TIMEOUT` |
     | `ci.yml:image` | 20 min | 2026-10-24 | 5 | 59 s | - | - | job `image`: `linux/amd64` 59 s, `linux/arm64` 54 s |
     | `ci.yml:assemble` | 10 min | 2026-10-24 | 5 | 21 s | - | - | job `assemble` |
     | `release.yml:guard` | 5 min | 2026-10-24 | 0 | 0 s | - | - | job `guard` has never run: it was added after the last release run, so its limit is not measured |
     | `release.yml:build` | 30 min | 2026-10-03 | 1 | 72 s | - | - | job `build` of the last release run: `linux/arm64` 72 s, `linux/amd64` 62 s |
     | `release.yml:assemble` | 15 min | 2026-10-03 | 1 | 25 s | - | - | job `assemble` of the last release run |
     | `release.yml:verify` | 15 min | 2026-10-03 | 1 | 28 s | - | - | job `verify` of the last release run |
     | `pr-title.yml:pr-title` | 5 min | 2026-10-24 | 5 | 6 s | - | - | job `pr-title`, the last five runs, on pull requests |
     | `gate-change.yml:classifier` | 5 min | 2026-10-24 | 5 | 11 s | - | - | job `classifier`, the last five green runs, on pull requests |

     `PULL_TIMEOUT` (300 s) has no row: a pull is bounded by the network, not by a run. Proven
     red on 2026-10-03: a pull from an unroutable registry ended at a 10 s limit with no
     container started; a test that blocks SIGALRM, so pytest-timeout cannot end it, ran past
     that timeout's 120 s and ended at a 200 s outer limit with nothing left running.
   - **No result cache.** `docker-build` runs `--pull --no-cache`, and CI's image build
     `no-cache` and `pull`: a cached layer skips its `RUN` step, so a result is reused instead
     of produced (ADR-016 rule 4). CI keeps no cache of any kind: setup-uv's cache is off
     (`enable-cache: false`) and setup-buildx caches no binary, so every run downloads its
     inputs, each checked against a pin. Locally uv's download cache is allowed: every
     artifact it holds is checked against the hashes in `uv.lock`, so it can change how fast a
     step runs, never its result.

## Test layers

- **Unit** — the identifier grammar; versions from Conventional Commits; which commit is a
  candidate; the change log and the rc tag's message; the release files' loader, writer,
  models and rules (ADR-026); the evidence rules (`tests/fixtures/`); the audit sink; the
  notifier and the adapters with fake transports.
- **Integration** — CLI end-to-end via the Typer runner; API via FastAPI `TestClient`; the Git
  adapter on real repositories.
  CLI output is held to one class rule: no printed value can change how the terminal renders
  or lays out output. Every character of Unicode general category Cc, Cf, Zl or Zp is shown
  as `\xNN` (below U+0100), `\uNNNN` (up to U+FFFF) or `\UNNNNNNNN`, lowercase hex, and a
  backslash as `\\`. Each hostile value goes through every command path piped, and through
  both on a pty. An argument a command does not take is a usage error, exit 2, the
  argument shown escaped. The printer itself is tested with every hostile value directly
  (`tests/unit/test_cli_print.py`).
- **Build / supply chain** — image builds from a clean tracked-files export; signature, SBOM,
  and provenance verified from the digest.
- **Self-release** — planned, not built: the top-level integration test in which GORA
  registers and promotes itself (the Milestone 3 exit, ADR-009).

## Production-grade requirement

GitHub and Slack are **production requirements** (ADR-014). Their adapters must be prod-grade
tested: narrow, typed, audited, mocked in unit tests, and exercised end-to-end. New adapters
or transports do not merge without tests at every applicable layer above.

## Coverage expectation

Every public behaviour has a test. Prefer a small number of meaningful tests over coverage
theatre — but a code path that can fail silently in a release is a code path that must be
tested. When in doubt, add the test.

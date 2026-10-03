# GORA Testing Policy

Authoritative — as binding as an ADR. In the spirit of Debian Policy: quality is a **rule,
not a wish**. GORA is a release-control mechanism, so it holds itself to a higher bar than
what it manages. "Release when it's ready."

## Non-negotiable rules

1. **The gate is defined once, and placed by what it reads** (ADR-021 section 4). The checks
   are `Makefile` targets, and CI calls them.
   - **Source checks**, `make check`: `make sync` (`uv sync --locked --all-extras --group
     check`: a stale `uv.lock` fails it), then, in check mode with no cache, ruff check +
     shellcheck on every tracked `*.sh` + ruff format --check + mypy on `src/gora` and
     `tests`. They read text, whose result does not depend on the processor: **once** per
     commit.
   - **Behaviour checks**, `make behaviour`: `make sync-test` (the `test` group only), then
     pytest. They run code: on **every supported platform**.
   - `make verify` runs both on one machine.

   The development tools are two PEP 735 dependency groups in `pyproject.toml`, installed
   only when a target names one and never in the release image: `test` (pytest,
   pytest-timeout, pytest-cov, jsonschema) and `check` (ruff, mypy and its stubs,
   shellcheck-py, and `test`, because mypy reads the tests). A behaviour run installs no
   tool that has no build for its platform: `shellcheck-py` ships no `linux/arm64` wheel. No
   step writes what it checks (ADR-0016 rule 4). CI runs every check on the exact Python and
   uv the release image is built with; locally any patch of the minor version
   `.python-version` names, and any uv from the floor up (rule 3). The suite refuses any other
   minor version.

   **CI is the gate of record** (ADR-021 section 5): a commit is green when CI is green, on
   native hardware for every supported platform. Local runs save a round trip and catch most
   failures early; they are never reported as evidence. `.github/workflows/ci.yml`:

   | Check | Runner | Runs |
   | --- | --- | --- |
   | `check` | `ubuntu-24.04` | `make check` |
   | `test (linux/amd64)`, `test (linux/arm64)` | `ubuntu-24.04`, `ubuntu-24.04-arm` | `make behaviour` |
   | `image (linux/amd64)`, `image (linux/arm64)` | `ubuntu-24.04`, `ubuntu-24.04-arm` | the release image built from the checkout (tracked files only, no layer cache, bases pulled by digest), pushed by digest to a registry on the runner, `make image-smoke` on that digest |
   | `assemble` | `ubuntu-24.04` | both images, byte for byte, into one image index, checked to hold exactly `linux/amd64` and `linux/arm64` at the digests the image jobs built |

   `image` and `assemble` run `.github/actions/build-image` and
   `.github/actions/assemble-index`, the steps the release workflow runs for a candidate
   (ADR-021 sections 1–2): every pull request rehearses the release path, and nothing is
   pushed to GHCR from CI.
2. **Gates reproduce production, not the working tree.** The pre-push gate is
   **`make verify-export`**: `make verify` on committed `HEAD`, in a fresh `git archive`
   export, so the working tree and the caches cannot make it green. It refuses a dirty tree
   and prints the commit it checked. **`make verify-linux`** runs the behaviour checks on
   `HEAD` inside a Linux container, as root; use it on macOS. `make docker-smoke` builds the
   release image from `git archive HEAD` and runs `make image-smoke` on it, read-only and
   offline (`--read-only --network none`): the image's platform and entrypoint,
   `python --version` against the Dockerfile's `PYTHON_VERSION`, no tool of either
   dependency group in the image, then `version`, and `status` on the example manifest.
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
   | Python, release and CI | `ARG PYTHON_VERSION` in `deployments/docker/Dockerfile`, under the `python:3.12-slim` digest | exact patch | the image build fails on a base with another interpreter; `scripts/gate/versions.sh` gives it to CI's setup-uv (`python-version`), and the `test` job checks the suite ran on it; `make image-smoke` compares `python --version` | the reviewer, in the pull request that moves the base digest |
   | Python, developer | `.python-version` | minor (`3.12`): any patch | the suite refuses another minor version; the test checks it is the release's minor | by hand, with a new minor version (a decision, not an update) |
   | uv, release and CI | the `uv` stage of `deployments/docker/Dockerfile`, by digest | exact | `versions.sh` gives it to setup-uv (`version`); the `linux-gate` image carries the same version | Dependabot, `docker` (with `linux-gate`, one group) |
   | uv, developer | `[tool.uv] required-version` in `pyproject.toml` | floor (`>=0.5.21`) | every uv from 0.5.21 refuses to run below it; the test keeps it a floor, not above the pin | by hand, only when the project needs a newer uv feature or lock format — never with a pin bump. The floor is the lowest uv proven to run `make verify-export` here (0.5.21, 2026-10-03: also the first uv that reads the setting at all) |
   | Base images | `python:3.12-slim` by the digest of its multi-platform index, in both stages | exact | the two stages carry one digest | Dependabot, `docker`; minor and major versions are ignored |
   | Gate images | `deployments/docker/gate-images.Dockerfile`: `linux-gate` (`verify-linux`, `docker-platform-check`), `buildkit` (the builder), `registry` (the CI rehearsal), each by digest | exact | read by `scripts/gate/image.sh`, never typed elsewhere | Dependabot, `docker` |
   | Actions | each `uses:`, by full commit SHA, version in a comment | exact | — | Dependabot, `github-actions` (workflows and `.github/actions/*`) |
   | Runners | `runs-on`, `ubuntu-24.04` and `ubuntu-24.04-arm`; never `ubuntu-latest` | exact | — | by hand |
   | Python packages | `uv.lock`, `--locked` everywhere | exact | — | Dependabot, `uv` |
   | buildx, cosign, syft | an exact version in the action input or workflow `env` | exact | — | by hand |
   | crane | version and SHA-256 per platform in `scripts/gate/crane.sh` | exact | the download is checked against the SHA-256 | by hand, from the release's `checksums.txt` |

   A Dependabot pull request that moves the Python base digest to a new patch fails the image
   build, visibly, until `PYTHON_VERSION` names that patch; if the pinned uv cannot install it
   for CI, the uv pin moves in the same pull request.
4. **Every bug becomes a test or check.** A defect found once gets a regression guard so it
   cannot return.
5. **Test the real thing, not a proxy.** Supply chain verified by `cosign verify` /
   attestation checks in CI, never assumed. The release pipeline self-verifies its own
   artifact (signature + SBOM + provenance) before it is trusted.
6. **No network and no machine config in automated tests — every layer.** External systems
   are behind bounded adapters (ADR-006) and are **mocked** in unit and integration tests
   alike (`httpx.MockTransport`, fakes, FastAPI `TestClient` + dependency overrides). Fast,
   deterministic, offline.

   Enforced by a plugin that `tests/conftest.py` registers. It is active for the whole pytest
   session, from configuration to exit, which covers collection and module imports, fixtures
   of every scope, every thread, and every Python child process:
   - Settings come from code defaults and the test itself. No file is read from the cwd,
     and `GORA_*` variables are removed from the environment.
   - Tests never touch the real home. Each test gets its own `HOME`, `XDG_STATE_HOME`,
     `XDG_CONFIG_HOME` and `GORA_AUDIT_LOG` under its `tmp_path` (the `_isolated_home`
     fixture), in the test's process and in every child, so the audit journal a mutating
     command writes (ADR-0017) never lands in `~/.local/state`. `GORA_AUDIT_LOG` is the one
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
   to show that every identifier GORA accepts is a ref git accepts (ADR-003). Without git it
   fails rather than skips: a differential test that cannot run proves nothing. It runs git
   with no user or system configuration (`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL` set to
   the null device, its own `HOME`), so the machine's git config cannot change a verdict.
7. **Fail closed.** On any failure the delegated agent stops and reports — no work-arounds.
8. **A gate ends.** Every gate run ends in bounded time, green or red, and leaves nothing
   running. Incident: on 2026-10-02 two `verify-linux` runs hung on linux/amd64 under Rosetta
   and their containers stayed up for hours. Proven cause: processes started from a thread
   pool deadlock the whole interpreter under that emulation. Over fresh containers the git
   oracle hung 15 of 18 times with 8 threads (vfork), 5 of 18 with fork, 0 of 30 sequential;
   0 of 5 on native arm64 and natively on macOS. In 17 of the 20 hangs nothing inside the
   process ran again: no pytest-timeout timer, no faulthandler dump. Since ADR-021 no gate
   runs under emulation: a green `verify-linux` took about 121 s emulated, and takes 37 s
   natively (the runs below).
   - **Every test has a timeout**: pytest-timeout, 120 s from setup to teardown, over 5x the
     slowest test under amd64 emulation (15.7 s). Method `signal`: it ended 2 of 17 hangs and
     printed the stacks; the `thread` method's timer ran in none of 3. `tests/conftest.py`
     refuses to run without the plugin, with a timeout that is not positive, with another
     method, or with a test that sets its own `timeout` marker.
   - **Tests do not fan subprocesses out over threads.** A child process is started one at a
     time: the conftest `Popen` wrapper raises `ConcurrentSpawnError` for a second one while
     the first is being created, the state every hang above was in.
   - **Every gate run is bounded from outside.** `make verify`, `verify-export`, `check` and
     `behaviour` run under `scripts/gate/bounded.sh`: GNU timeout (`timeout` on Linux,
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
   - **The limits** are each at least 10x a green native run with its downloads, measured on
     2026-10-03 (Apple silicon, colima 4 CPUs; then CI, PR #48):

     | Run | Green, native | Limit |
     | --- | --- | --- |
     | `make check` | 15.4 s from an empty uv cache, 3.5 s warm | 180 s |
     | `make behaviour` | 39 s | 600 s |
     | `make verify` | 44 s | 600 s |
     | `make verify-export` | 45 s | 720 s, above `verify`'s, so that one ends a hung suite first |
     | `make verify-linux`, `linux/arm64` | 37 s, CPython and every wheel downloaded | 600 s |
     | `make docker-smoke` (build and smoke) | 8 s | build 300 s, each smoke run 60 s |
     | CI `check` | 17 s | 10 min |
     | CI `test (linux/amd64)`, `test (linux/arm64)` | 60 s, 49 s (pytest 47.5 s, 40.0 s) | 15 min |
     | CI `image (linux/amd64)`, `image (linux/arm64)` | 47 s, 44 s | 20 min |
     | CI `assemble` | 17 s | 10 min |

     A pull is bounded by the network, not by a run. Proven red on 2026-10-03: a pull from an
     unroutable registry ended at a 10 s limit with no container started; a test that blocks
     SIGALRM, so pytest-timeout cannot end it, ran past that timeout's 120 s and ended at a
     200 s outer limit with nothing left running.
   - **No result cache.** `docker-build` runs `--pull --no-cache`, and CI's image build
     `no-cache` and `pull`: a cached layer skips its `RUN` step, so a result is reused instead
     of produced (ADR-0016 rule 4). CI keeps no cache of any kind: setup-uv's cache is off
     (`enable-cache: false`) and setup-buildx caches no binary, so every run downloads its
     inputs, each checked against a pin. Locally uv's download cache is allowed: every
     artifact it holds is checked against the hashes in `uv.lock`, so it can change how fast a
     step runs, never its result.

## Test layers

- **Unit** — pure domain invariants (immutability, digest preservation, supersession, the
  identifier grammar and the invariants ADR-003 lists as enforced; stage order is not enforced
  yet, ADR-003) and service sequencing with mocked adapters.
- **Integration** — CLI end-to-end via the Typer runner; API via FastAPI `TestClient`.
  CLI output is held to one class rule: no printed value can change how the terminal renders
  or lays out output. Every character of Unicode general category Cc, Cf, Zl or Zp is shown
  as `\xNN` (below U+0100), `\uNNNN` (up to U+FFFF) or `\UNNNNNNNN`, lowercase hex, and a
  backslash as `\\`. Each hostile value goes through every command path piped, and through
  three of them on a pty. Since the identifier grammar (ADR-003), a hostile value in an
  identifier argument ends that path as a usage error, exit 2, the value shown escaped and
  nothing opened; in a manifest field it ends it as a refused load that does not print it.
  The printers themselves are tested with every hostile value directly
  (`tests/unit/test_cli_print.py`), since manifest content no longer reaches them.
- **Build / supply chain** — image builds from a clean tracked-files export; signature, SBOM,
  and provenance verified from the digest.
- **Self-release** — the top-level integration test: GORA releases and promotes itself.

## Production-grade requirement

GitHub and Slack are **production requirements** (ADR-014). Their adapters must be prod-grade
tested: narrow, typed, audited, mocked in unit tests, and exercised end-to-end. New adapters
or transports do not merge without tests at every applicable layer above.

## Coverage expectation

Every public behaviour has a test. Prefer a small number of meaningful tests over coverage
theatre — but a code path that can fail silently in a release is a code path that must be
tested. When in doubt, add the test.

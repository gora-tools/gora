# GORA Testing Policy

Authoritative — as binding as an ADR. In the spirit of Debian Policy: quality is a **rule,
not a wish**. GORA is a release-control mechanism, so it holds itself to a higher bar than
what it manages. "Release when it's ready."

## Non-negotiable rules

1. **CI parity.** `make verify` (ruff check + ruff format --check + mypy + pytest) mirrors CI
   exactly and must be green before any push. No exceptions.
2. **Gates reproduce production, not the working tree.** `make docker-smoke` builds from
   `git archive HEAD` (tracked files only) and runs in CI on every PR. Local build checks
   for a dependency/Dockerfile change require **commit → docker-smoke** (a dirty tree tests
   the old HEAD).
3. **Determinism first — dogfood the thesis.** Committed lockfiles, digest-pinned artifacts,
   reproducible builds. Apply GORA's own standard to GORA.
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
7. **Fail closed.** On any failure the delegated agent stops and reports — no work-arounds.

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

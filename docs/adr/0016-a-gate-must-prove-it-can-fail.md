# ADR-016: A Gate Must Prove It Can Fail

**Status:** Accepted
**Date:** 2026-10-01

---

## Context

GORA's gates are `make verify` locally and the `lint-and-test` job in CI: ruff check, ruff
format --check, mypy, pytest, plus `make docker-smoke` in CI (TESTING rules 1–2). Until now a
green run was read as evidence that the change is sound. That reading is only valid if each
step *could* have gone red on the kind of defect it is there to catch.

On 2026-10-01 the gates were audited at `f3fc325`: defects were injected one at a time into a
throwaway clone and every gate step was run against them. 86 probes: 37 went red as expected,
33 passed **silently green**, 16 could not be measured. Every finding below was then re-run
independently on the same commit, in a different environment (Linux, uv, CPython 3.14.7), one
command per claim, except where a row says otherwise. Anything that did not reproduce is left
out of this record.

| Injected defect or check | Gate result | What it shows |
| --- | --- | --- |
| Rename `tests/unit/test_manifest.py` out of the `test_*` pattern | 59 → **51** tests collected, all steps exit 0 | The suite can shrink without any step noticing |
| 26 hand-written mutants, 1–2 per function in domain/services/CLI (audit figure; the 18 kills were not re-run) | **8 survive** all four steps; all 8 re-run | Tests run the code but do not assert on what changed |
| The 8 survivors: all four digest-regex weakenings (`{64}` → `+`, drop `$`) in `candidate.py` and `manifest.py`; `cut-rc` wipes `stages`; `sort_keys=True` and `exclude_none=False` in manifest serialisation; `partition` → `rpartition` in `_derive_release` | ruff 0 · format 0 · mypy 0 · `59 passed` | Each one changes behaviour, e.g. a regex mutant makes `cut-rc -d sha256:abc` exit 0 where the original exits 1 |
| Line coverage of the three files that carry all 8 survivors | **100% / 100% / 100%** | Coverage cannot see this class of defect |
| A type error already present in `tests/` (`test_promotion_pr.py:77`, `[arg-type]`) | mypy exit 0 | The step reads `src/gora/` only; defects in `tests/` are outside its scope |
| `pyproject.toml` constraint changed without relocking | `uv sync --all-extras` (the CI step) exit 0 and rewrites `uv.lock`; `uv lock --check` exit 1 | The sync step repairs its own input instead of failing on it |
| `.env` with Slack enabled, outbound traffic trapped by a local proxy | **16** CONNECTs to `slack.com`, all from integration tests, 0 from unit tests; no test fails | TESTING rule 6 covers unit tests only, and nothing enforces even that |
| Mutant (rollback skips `audit.record`) **plus** `--deselect` of its test in `pyproject.toml`, in one diff | `1 failed` → `58 passed, 1 deselected`, ruff 0, mypy 0 | A change can weaken the gate that judges it, in the same diff |
| CLI: `promote prod` straight after `cut-rc`; re-`cut-rc` of the same RC version with another digest; `rollback` to a never-seen digest; `rollback` of a stage that does not exist | exit 0 each | ADR-003/004 invariants (linear promotion, RC↔digest 1:1, rollback to a *known* state) have no executable check. TESTING.md lists linear promotion as unit-tested |
| Audit sink: `basicConfig` / `addHandler` / `dictConfig` in `src/` | 0 matches | `audit.record()` is tested, but in the CLI the event reaches no handler |

There was also one failure of the measuring itself. While the network finding was being
reproduced, the proxy trap read **0** twice. The first time the environment's lowercase
`https_proxy` took precedence over the one being set. The second time the trap server was not
running. Both zeros looked exactly like "no network calls". The 16 only became meaningful
after a calibration request, a direct POST, registered as 1. A probe, too, can be a gate that
cannot fail.

## Decision

**A check counts as a gate only if it has been shown to go red on a defect of the class it
claims to catch.** This covers a CI step, a `make` target, a test that guards an invariant and
an audit probe. A green result from a check that has never been seen red is not evidence and is
not reported as evidence.

1. **Red first.** A new gate step, guard or invariant test comes with a demonstration that it
   fails: the test fails on the code without the fix, or the step goes red on an injected
   defect. The demonstration (command and its red output) goes in the PR description. It
   extends TESTING rule 4, which says a test must exist; this rule says it must be shown to
   bite.
2. **Calibrate before you count zero.** A check that asserts an absence (no network, no diff,
   no errors) first shows that it registers the presence. A zero from an uncalibrated probe is
   recorded as *not measured*.
3. **The suite guards its own size.** The guard lives inside the suite and compares collected
   test files with the test files tracked in the git index. It does not compare against a
   remembered number, and it does not compare a glob with itself, because renaming a file out
   of the pattern lowers both sides at once.
4. **A gate step does not repair its input.** A step that can rewrite what it checks cannot
   fail on it. Gate steps run in check mode against committed inputs: the lockfile is checked,
   never regenerated, and the formatter checks, never writes.
5. **A step's scope is what it actually reads.** When the scope is narrower than the claim
   made for it (mypy on `src/gora/` only, TESTING rule 6 for the unit layer only), either the
   scope is widened or the claim is narrowed in writing. A gap between the two is a defect.
6. **Every ADR invariant has an enforcement class:** `test`, `runtime-check`, `CI-step` or
   `review-only`. `review-only` is a valid answer, but it has to be written down. An invariant
   with no stated class is treated as unenforced. New and amended ADRs state the class next to
   each invariant. Classifying ADR-001…015 is backlog work.
7. **Gate-defining files are part of the gate.** These are `.github/workflows/ci.yml`,
   `Makefile`, `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]`,
   dev dependencies), `uv.lock`, `tests/conftest.py`, `deployments/docker/Dockerfile`, and
   inline suppressions (`# type: ignore`, `# noqa`). A diff that touches any of them is a gate
   change and is reviewed as one. The repository has no CODEOWNERS file. Today the only control
   is a human reading the diff. This ADR records that fact and adds no mechanism.

The way to measure falsifiability is the one used here: hand-written mutants (1–2 per
function) plus defect-injection probes per gate step, with the numbers recorded. The
measurement is repeated when gate steps or gate-defining files change materially. **Not
decided here:**

- whether mutation testing becomes a standing, automated mechanism;
- whether "a human reading the diff" (rule 7) is an accepted risk for a solo repository, or
  gate-defining files need an outside control such as CODEOWNERS or a required approval.

## Why not otherwise

- **"Every PR comes with tests."** The suite already has 59 tests, and every one of the 8
  surviving mutants sits in a file that has tests. A test count says how many tests run, not
  whether any of them would fail. The count can also drop from 59 to 51 on a green run.
- **"A coverage percentage."** The three files carrying all 8 survivors are covered at 100%
  of lines. Coverage measures execution, not assertion. A coverage target would have been met
  in full while the digest regex could be weakened to accept `sha256:abc` unnoticed. A target on it
  would reward exactly the "coverage theatre" TESTING.md warns against.
- **"CI mirrors `make verify`, so green is green."** Parity shows that two gates agree, not
  that either of them can fail. Both are green on all 8 survivors and on the
  defect-plus-`--deselect` diff.
- **"Review will catch it."** Review stays the control for rule 7 and for `review-only`
  invariants. A reviewer looking at a green check, however, cannot tell which green is
  evidence and which is a step that could not have failed. These rules make that visible.

## Consequences

- A green result carries its falsifier: for each gate step it is known which defect class
  turns it red.
- Each new check costs one red demonstration. This applies to checks and gates, not to every
  code change.
- Some invariants will end up `review-only`. That is recorded rather than implied.
- The audit's findings are not fixed by this ADR. Each becomes a backlog item: a suite-size
  guard, assertions that kill the 8 survivors, the mypy scope, check-mode locking, a network
  guard covering the integration layer, enforcement of the ADR-003/004 invariants above, and an
  audit sink in the CLI.
- Until the open question on standing mutation testing is answered, re-measurement is manual.

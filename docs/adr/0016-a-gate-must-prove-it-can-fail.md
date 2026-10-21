# ADR-016: A Gate Must Prove It Can Fail

**Status:** Accepted
**Date:** 2026-10-01
**Amended:** 2026-10-01 — rule 7: threat model for gate-defining files decided
**Amended:** 2026-10-02 — rules 3, 4, 5 and 7 enforced; see *Enforcement*. Rule 7: the
signal is the commit status `gate-change`
**Amended:** 2026-10-03 — *Enforcement* follows the gates placed by platform (ADR-021);
rule 7: the status is the only context named `gate-change`
**Amended:** 2026-10-08 — rule 7: `gate-change` is a required check
**Amended:** 2026-10-16 — rule 7: a suppression in any spelling its tool reads; entries for
2026-10-01 to 2026-10-03 (correction)
**Amended:** 2026-10-20 — rule 7: the Debian snapshot of the release image is gate-defining

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
   change and is reviewed as one.
   *Amended 2026-10-20 (ADR-011):* so are `deployments/docker/debian.sources` and
   `deployments/docker/debian.lock`, the Debian snapshot the release image's packages come
   from and the hashes that pin it.

   **Threat model: accepted risk.** This is a solo repository, so its owner is the only
   reviewer and the control is the owner reading the diff. Two compensating measures back that
   review:
   - the test-count floor of rule 3, inside the suite;
   - a check defined on the base branch, not taken from the PR, that flags every PR touching a
     gate-defining file, so a gate change is never reviewed as an ordinary one. It reads only
     the list of changed paths and never executes code from the PR.

   The floor alone does not hold: it lives in a gate-defining file, and the same PR can lower
   it. The base-defined check makes that edit visible.

   *Amended 2026-10-01:* the threat model above is decided, and "Not decided here" loses the
   question it answers; it replaces "Today the only control is a human reading the diff".

   The check's signal is the **commit status `gate-change`** on the PR's head commit (amended
   2026-10-02), not the result of its workflow job: a status on the head is what a ruleset can
   require. It becomes a required status in a ruleset after its first live run, and a human
   makes that change.

The way to measure falsifiability is the one used here: hand-written mutants (1–2 per
function) plus defect-injection probes per gate step, with the numbers recorded. The
measurement is repeated when gate steps or gate-defining files change materially. **Not
decided here:**

- whether mutation testing becomes a standing, automated mechanism.

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
- **CODEOWNERS or one required approval for gate-defining files.** In a solo repository the
  code owner and the approver are the author of the change, so the rule adds a click, not a
  second pair of eyes. Rule 7 uses a check the PR cannot edit instead.
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
  guard covering the integration layer, enforcement of the ADR-003/004 invariants above, an
  audit sink in the CLI, and the base-defined gate-file check of rule 7.
- Until the open question on standing mutation testing is answered, re-measurement is manual.

## Enforcement

*Added 2026-10-02.* How rules 3, 4, 5 and 7 are enforced, with the enforcement class of rule 6
for each. Every guard below was shown red before it was trusted (rule 1); the red outputs are
in the PR that added it.

| Rule | Enforced by | Class |
| --- | --- | --- |
| 3. The suite guards its own size | `tests/conftest.py`: on a full run (`pytest` with no path, as `make verify` runs it), every `.py` file under `tests/` in the git index must be collected, or be one of the short list of non-test modules (`__init__.py`, `conftest.py`, `tests/audit_fakes.py`). An untracked file is collected and not counted. Items count before deselection | `test` |
| 4. A gate step does not repair its input | The checks are `Makefile` targets, and CI runs them: `make check` once, `make behaviour` on native `linux/amd64` and `linux/arm64` (ADR-021). Each first syncs with `uv sync --locked`, which fails on a stale `uv.lock`, and only the dependency group it needs; every other step runs with `uv run --no-sync`, ruff with `--no-cache` and `format --check`, shellcheck on every tracked `*.sh` with `--norc`, mypy with no cache, pytest with no cache provider. CI also sets `UV_LOCKED=1` and keeps no cache: setup-uv's is off, setup-buildx caches no binary. The image builds with `uv sync --locked`, and without a layer cache (`--pull --no-cache` locally, `no-cache` and `pull` in CI): a cached layer skips its `RUN` step. `make verify-export` runs the gate on committed `HEAD` in a fresh export, so neither the working tree nor a cache can make it green | `CI-step` |
| 5. A step's scope is what it actually reads | mypy reads `src/gora` and `tests`. The exact Python and uv are declared once, in `deployments/docker/Dockerfile` (`PYTHON_VERSION`, the uv stage); the image build fails on a base with another interpreter, `image-smoke` compares `python --version`, and CI runs its checks on both through `scripts/gate/versions.sh`, the `test` job checking the suite's interpreter. Locally `.python-version` names the minor version and `required-version` a uv floor; the suite refuses to run on another minor version. `tests/unit/test_build_pins.py` fails when any build input is unpinned, typed twice or two pins disagree (TESTING rule 3) | `CI-step` (mypy, image, interpreter), `test` (minor version, pins) |
| 7. Gate-defining files are part of the gate | `.github/workflows/gate-change.yml`, on `pull_request_target`: defined on the base branch, reads the PR's file list and patches through the API, runs nothing from the PR. Its job's check run is named `gate-change classifier`, so the commit status is the only context named `gate-change`: an event for another label posts no status and the job still succeeds, and a green check run of the same name could sit on a head whose status is failure (a test fails if a workflow, job or step takes that name). A PR that touches a gate-defining path, or adds a line carrying `# noqa`, `type: ignore` or `shellcheck disable` in any file, is approved only by the event that adds the label `gate-change-reviewed` while the event's head is the PR's head. A label the PR already carries approves nothing: `opened`, `reopened` and `synchronize` remove it and post `failure`; removing the label posts `failure`; an event for any other label posts nothing and leaves the status as it was. The path list is in the workflow, and it adds the files that can override a listed one (`conftest.py` at any depth, `pytest.ini`, `tox.ini`, `setup.cfg`, `ruff.toml`, `mypy.ini`, a nested `pyproject.toml`, `GNUmakefile`), `scripts/gate/`, which starts every gate container and bounds every gate run, `.github/actions/`, the image steps CI and the release workflow share, and `deployments/docker/gate-images.Dockerfile`, the images the gates run. It posts the commit status `gate-change` on the head commit the event names: `pending`, then `success`, `failure`, or `error` when it cannot read the files; a run whose head is no longer the PR's posts nothing. The decision is a pure function, unit-tested from the workflow file | `CI-step`, then `review-only` for what the label attests |

*Amended 2026-10-03 (ADR-021):* the rows of rules 4 and 5 follow the gates placed by platform:
`make check` once, `make behaviour` on each native platform, and the exact Python and uv
declared once in the release Dockerfile. In the row of rule 7, the status is the only context
named `gate-change`.

*Amended 2026-10-16:* a line carries an inline suppression when it holds, in any case and spacing,
what a gate's tool reads as one: ruff's `noqa`, on a line or for a file (`ruff:` or `flake8:`
before it); mypy's `type: ignore` and its inline configuration (`mypy:`); shellcheck's
`disable=` directive; and the formatter's `fmt: off`, `fmt: skip` and `yapf: disable`. The
patterns are wider than what the tools accept, measured with ruff 0.16.10, mypy 2.4.0 and
shellcheck 0.11.0: the three exact spellings let `#noqa`, `# NOQA`, `# type:ignore[x]` and
`# shellcheck  disable=` through. A false match asks for a review; a miss would let a
suppression through unreviewed.

What is still not covered:

- Rule 3 applies to a full run only. `pytest tests/unit` runs without it, as does a CI that
  passes paths; that edit is in the Makefile or a workflow, which rule 7 flags.
- The rule 7 check cannot judge the PR that introduces it: `pull_request_target` runs the
  workflow from the base branch, where it does not exist yet. Its first live run is the next PR
  that touches a gate-defining file.
- The status is required only once a ruleset requires it, after the check's first live run.
  Until then a red `gate-change` is visible but does not block a merge.

  *Amended 2026-10-08 (correction):* ruleset `protect-main` requires `gate-change`, so a red
  status blocks a merge.
- Approval is the label event on the current head, never the label set. A label carried into
  `opened` or `reopened` (for example `gh pr create --label`) is removed and does not count; an
  event for another label does not change the status.
- Runs are serialised per PR, and a run still pending when a newer event arrives is replaced
  and never judges its event. A replaced `synchronize` run leaves the label in place and the
  new head without a passing status (no status, or a failure) until someone adds the label
  again, removing it first if it is still there. This fails closed.
- The check cannot tell who added the label. It is applied by the same person who wrote the
  change (the accepted risk of rule 7). The check makes a gate change visible; it does not make
  it reviewed.

The 8 mutants of the 2026-10-01 audit that survived every step are closed since #44: six are
killed, and B10 and B12 have become equivalent mutants, since the grammar matches with
`fullmatch`, where a dropped `$` changes nothing. This is the result recorded with #44; it was
not re-measured by the change that added this section.

## Amendments

### 2026-10-01 — rule 7: the threat model for gate-defining files

Rule 7 decides its threat model: an accepted risk for a solo repository, held by the owner's
review and two compensating measures, the floor of rule 3 and a check defined on the base
branch. "Not decided here" loses the question, and "Why not otherwise" gains CODEOWNERS.

### 2026-10-02 — rules 3, 4, 5 and 7 enforced; the signal of rule 7

*Enforcement* is added, with the mechanism and the class of rules 3, 4, 5 and 7. The signal of
rule 7 is the commit status `gate-change` on the pull request's head, which a ruleset can
require.

### 2026-10-03 — *Enforcement* follows the gates placed by platform

ADR-021 places the gates by what they read. The rows of rules 4 and 5 are restated for it, and
the status `gate-change` is the only context of that name.

### 2026-10-08 — `gate-change` is a required check

Rule 7 (*Enforcement*) is amended in place: the ruleset `protect-main` requires `gate-change`,
so a red status blocks a merge.

### 2026-10-16 — rule 7: a suppression in any spelling its tool reads

The check matched three exact spellings, and ruff, mypy and shellcheck accept others (`#noqa`,
`# type:ignore[x]`, `# shellcheck  disable=`), file-level forms (`ruff: noqa`, `mypy:`) and
the formatter's own. It now matches all of them in any case and spacing. A correction with it:
the amendments of 2026-10-01 to 2026-10-03 gain their entries and their notes in place.

### 2026-10-20 — rule 7: the Debian snapshot of the release image

ADR-011 installs the release image's packages from `deployments/docker/debian.sources`,
pinned by `deployments/docker/debian.lock`; they decide what the image carries as the
Dockerfile does, so they are gate-defining, and the classifier's path list holds them.

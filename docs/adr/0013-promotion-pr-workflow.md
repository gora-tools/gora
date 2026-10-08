# ADR-013: Promotion PR Workflow (GitHub adapter)

**Status:** Proposed
**Date:** 2026-07-07
**Amended:** 2026-10-02 — the key of a promotion; promotion computed from the base branch
**Amended:** 2026-10-03 — gates, one open pull request per artifact and stage, GitHub App (ADR-022)
**Amended:** 2026-10-04 — the stage loop opens promotions; approval gate (ADR-023)
**Amended:** 2026-10-08 — titles, versions, order of promotion (ADR-024); numbering (ADR-014)
**Amended:** 2026-10-08 — no dependencies between artifacts (ADR-025)

---

## Context

Milestone 4 (ADR-009) is the first end-to-end **automated** workflow. When this ADR was
written (2026-07-07), `gora promote <stage>` mutated the manifest locally and a human opened
the PR by hand. M4 automates the
generation: GORA itself produces a **deterministic promotion PR** through a bounded GitHub
adapter.

This ADR concretizes ADR-006 (tool adapter contracts — the GitHub adapter) and ADR-004
(promotion model) for that workflow. It does not re-decide those; it specializes them.

**ADR numbering:** this takes ADR-013. The planned Project Intelligence / ADR Sync ADR moves
to **ADR-014** (superseding the tentative numbers noted in ADR-011 and ADR-012 consequences,
which predate M4 claiming a number).

*Amended 2026-10-08 (ADR-014):* ADR-014 took another subject and pinned Project Intelligence /
ADR Sync to **ADR-020** (ADR-014, "Context").

## Decision

### Promotion is a controlled PR (GORA opens, a human merges)

`gora promote <stage> --open-pr` (and the `promotion_pr` workflow) performs:

1. Load the manifest (Git-native state).
2. Apply the domain promotion (`ReleaseManifest.promote`) — same digest moves to the stage
   (ADR-004), in memory.
3. Write the updated manifest to a **new branch**.
4. Open a **pull request** via the GitHub adapter.

GORA **opens** the promotion PR; it **never merges** it. Merge is a controlled/human
decision (ADR-006 controlled-mutation; `main` is PR-only). The PR *is* the review, approval
gate, and audit record.

*Amended 2026-10-03 (ADR-022):* each stage has a gate. For `gate: human` a person merges, as
above. For `gate: auto` GORA enables the platform's auto-merge, and the platform merges the pull
request once the required checks pass. GORA still has no merge operation: it never merges by its
own decision; an automatic merge is the policy's decision, reviewed in Git.

*Amended 2026-10-04 (ADR-023 sections 8 and 10):* nobody merges a GORA pull request by hand, and
the heading above, "a human merges", and the boundary "Does not merge PRs (human / policy)" read
accordingly. GORA enables the code host's auto-merge on every pull request it opens, and the host
merges it once its gate is met: `auto`, the other required checks; `approval`, the other required
checks and the reviews the policy names (the `human` gate is replaced); a rollback or a
rejection, the other required checks, since the request of a person whom policy allows is the
decision. GORA opens a promotion pull request by itself through the stage loop, as well as on
request (`gora promote`).

### Bounded GitHub adapter (the M4 subset)

Per ADR-006 (narrow, explicit operations only), M4 implements a minimal subset:

- `commit_file(path, content, branch, message)` — write the manifest on a branch.
- `create_pull_request(head, base, title, body)` — open the promotion PR.
- `get_pull_request_status(number)` — read state (open/merged/checks).

`merge_pull_request`, issues, etc. from ADR-006's full list are **out of scope** for M4.
GORA does not merge.

**Client: `httpx`**, wrapped so only these operations are exposed — not a full SDK
(PyGithub), whose "does everything" surface contradicts ADR-002/006. Safety comes from the
adapter being narrow, not from the library. Typed I/O (pydantic), explicit timeouts, TLS
verification on, token never logged.

### Determinism & idempotency

- Deterministic branch name derived from the promotion, e.g.
  `gora/promote-<stage>-<version>` (stable for the same inputs).
- Stable, generated PR title/body (states stage, version, digest).

  *Amended 2026-10-08 (ADR-024 section 8):* the title is a Conventional Commit of ADR-024
  section 8, so the required title check passes it.
- **Idempotent:** if an open promotion PR for the same (stage, digest) already exists, update
  it rather than open a duplicate.

*Amended 2026-10-02 (ADR-003):* The key GORA uses is (stage, version), the one the branch name
`gora/promote-<stage>-<version>` carries: a second open for the same stage and version finds the
branch and the open pull request, commits the manifest to that branch, and reuses the pull
request. With ADR-003 invariant a) enforced, a version maps to one digest within the manifest, so
(stage, version) fixes (stage, digest): no two opens under one key can promote two digests, and
the re-cut that would try is refused before any branch exists. The branch is not renamed. The
converse does not hold, on purpose: the final-release retag (`1.2.0-rc.3` and `1.2.0` on one
digest, ADR-004) gives two keys for one digest, and each is its own promotion of its own release
identity, with its own pull request. Invariant a) covers the manifest as it is, not its history
(ADR-003, "Not enforced yet"), so a version that left the manifest and was cut again on another
digest reuses its old branch and pull request, which the commit then updates to the new content.
*Enforcement class:* `test` (`tests/unit/test_promotion_pr.py`).

*Amended 2026-10-03 (ADR-022):* in a repository with several artifacts the key is (artifact,
stage, version); the branch of a group or platform-state promotion is decided with the command
model.

*Amended 2026-10-04 (ADR-023 section 6):* a promotion's key is (stage, artifact), one open pull
request each (L14). The branches are `gora/promote/<stage>/<artifact>` for a promotion, a group
promotion and a rollback of kind A; `gora/rollback/<stage>` for a rollback of kind B;
`gora/reject/<artifact>/<version>` for a rejection on its own. The same key with the same
content finds the open pull request; with new content, GORA closes it, deletes its branch and
opens a new one, except that a promotion or another rollback is refused while a rollback of that
artifact into that stage is open. This replaces `gora/promote-<stage>-<version>`. GORA's commit
and the pull request body end with a trailer, one line per artifact:
`Gora-promotion: <stage> <artifact> <version> <digest>`.

*Amended 2026-10-08 (ADR-024 sections 1, 2 and 4; ADR-025 section 1):* the two names of one digest
are `1.2.0-rc` and `1.2.0`. New content for an open promotion comes only from a person's request;
the stage loop never replaces an open pull request. There is no group promotion (ADR-025 section
1).

### Auth (per the onboarding model)

M4 is self-first: a token from settings (`GORA_GITHUB_TOKEN`, or `GITHUB_TOKEN` in CI). The
**GitHub App** path (installation tokens, multi-repo) is the "register, don't wire" model for
managing *other* repos — deferred until then. Auth is the adapter's concern (ADR-006); the
core stays auth-agnostic.

*Amended 2026-10-03 (ADR-022 section 14):* the GitHub App is no longer deferred; it is GORA's
identity on GitHub. A pull request opened with `GITHUB_TOKEN` runs its workflows only after an
approval, and a tag pushed with it starts none, so an automatic gate needs the App. Installation
tokens are created per run and expire; the private key is a secret of the release repository.

### Auditability — every step is written

Every mutating adapter call (`commit_file`, `create_pull_request`) emits an audit event
(caller, operation, target repo/branch/PR, version, digest, timestamp, result) through the
same audit path as `cut-rc`/`promote`. GORA records **every step of the artifact's journey** —
the Git- and audit-backed trail is the mechanism's core value.

### Boundary (what M4 does NOT do)

- Does not merge PRs (human / policy).
- Does not deploy or reconcile (that is CD).
- Does not run other projects' CI.
- Promotion-PR is *generation only*; downstream reconciliation is separate.

### Promotion is computed from the base branch, not from a local file

*Amended 2026-10-02 (ADR-002):* The workflow above loads "the manifest", and the implementation
reads it from a local file, then commits the whole file to the promotion branch. If the local copy
is behind the base branch, the pull request silently reverts whatever else changed on base: the
diff shows it, but the promotion is no longer a function of Git state. That contradicts ADR-002
(Git is the source of truth). Decided:

- The promotion is computed from the manifest **as it is on the base branch**: GORA reads
  `release-manifest.yaml` at the head of `base` through the GitHub adapter, applies the domain
  operation in memory, and commits the result. A local file plays no part in `--open-pr`.
- The commit carries only that change: the new file is the base file plus the promotion, so
  the pull request diff is exactly the promotion.
- Branches under `gora/` belong to GORA. When an open promotion pull request exists and its
  base has moved, GORA resets its own branch to the current head of base and recomputes before
  committing, so the pull request always shows the promotion against today's base. GORA never
  moves a branch outside `gora/`.

  *Amended 2026-10-04 (ADR-023 section 8):* a recomputation keeps the approvals. An approval
  binds to the promotion, not to the head commit, and counts while the branch carries exactly
  the promotion GORA computed; a commit anyone else pushes to the branch turns GORA's status
  red.
- When the pull request is merged, `main` holds the new release state; nothing else records
  it. For GORA's own repository, `main` changes only through pull requests, so a promotion PR
  is the only way release state changes there. The local `promote` command stays for
  manifests that are not branch-protected (local work, tests).

## Consequences

- M4 exit (ADR-009) is met: GORA generates deterministic promotion PRs end-to-end. *Amended
  2026-10-08 (correction):* met for the generation of the pull request; the computation from the
  base branch decided on 2026-10-02 is not built yet (release-model section 13).
- The GitHub adapter is the reusable seam for M5 (API/Slack triggers) and for the
  "register, don't wire" onboarding.
- A thin `httpx` adapter keeps both the dependency surface and the capability surface small
  (ADR-002/006).
- The audit trail now spans CLI operations *and* adapter mutations — reinforcing the "every
  step recorded" value.
- Project Intelligence / ADR Sync is renumbered to **ADR-014**. *Amended 2026-10-08 (ADR-014):* it
  is pinned to ADR-020 (ADR-014).

## Amendments

### 2026-10-02 — the key of a promotion; promotion computed from the base branch

Two notes are recorded in place: a promotion is keyed by (stage, version), which ADR-003's
invariant ties to one digest; and a promotion is computed from the manifest on the base branch,
not from a local file (ADR-002).

### 2026-10-03 — gates, one open pull request per artifact and stage, GitHub App

ADR-022 decides the lifecycle on trunk. For this ADR:

- Gates are `auto` (the platform's auto-merge, enabled by GORA) or `human`; GORA has no merge
  operation either way.
- At most one promotion pull request is open per artifact and stage. When a newer candidate
  reaches the stage before, GORA closes the older pull request and deletes its branch. In a
  repository with one artifact the (stage, version) key above still names the branch; with
  several, the key gains the artifact, and the branch of a group or platform-state promotion is
  decided with the command model.
- GORA keeps its open promotion branches current with the base, as decided above, so an
  automatic merge under strict required checks is not left behind.
- GORA posts a required status on every pull request to `main`: success when nothing is
  promoted; failure when a promotion's candidate is superseded before the sign-off gate, or
  rejected.
- The GitHub App is GORA's identity on GitHub.

### 2026-10-04 — the stage loop opens promotions; approval gate

ADR-023 sections 6, 8 and 10, amended in place above:

- GORA opens a promotion pull request by itself when every verification of the stage before is
  green, as well as on request (`gora promote`).
- It enables the code host's auto-merge on every pull request it opens; for `approval`, its
  required status waits for the reviews the policy names. The `human` gate of the 2026-10-03
  amendment is replaced by `approval`: nobody merges a GORA pull request by hand. A rollback or
  a rejection is merged on the request of a person whom policy allows.
- Approvals survive the recomputation of a promotion branch.
- The branch keys are `gora/promote/<stage>/<artifact>`, `gora/rollback/<stage>` and
  `gora/reject/<artifact>/<version>`.

### 2026-10-08 — titles, versions, order of promotion; ADR numbering

ADR-024 decides the versions and the order of promotion. For this ADR:

- GORA's pull request titles are Conventional Commits (ADR-024 section 8).
- Versions are `X.Y.Z-rc` and, once in `prod`, `X.Y.Z` on the same digest.
- GORA's required status no longer fails a promotion for a superseded candidate: there is no
  supersession. It fails for a rejected version or one that is no longer eligible (ADR-024
  section 3).
- A request for a named version replaces the open pull request of the pair; the stage loop never
  replaces one (ADR-024 section 4).
- Project Intelligence / ADR Sync is ADR-020, not ADR-014; the M4 exit is met for generation,
  and the computation from the base branch is not built yet.

### 2026-10-08 — no dependencies between artifacts

ADR-025 decides that GORA has no dependencies between artifacts. For this ADR: there is no group
promotion branch.

# ADR-013: Promotion PR Workflow (GitHub adapter)

**Status:** Proposed
**Date:** 2026-07-07

---

## Context

Milestone 4 (ADR-009) is the first end-to-end **automated** workflow. Today `gora promote
<stage>` mutates the manifest locally and a human opens the PR by hand. M4 automates the
generation: GORA itself produces a **deterministic promotion PR** through a bounded GitHub
adapter.

This ADR concretizes ADR-006 (tool adapter contracts — the GitHub adapter) and ADR-004
(promotion model) for that workflow. It does not re-decide those; it specializes them.

**ADR numbering:** this takes ADR-013. The planned Project Intelligence / ADR Sync ADR moves
to **ADR-014** (superseding the tentative numbers noted in ADR-011 and ADR-012 consequences,
which predate M4 claiming a number).

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
- **Idempotent:** if an open promotion PR for the same (stage, digest) already exists, update
  it rather than open a duplicate.

*Amended 2026-10-02.* The key GORA uses is (stage, version), the one the branch name
`gora/promote-<stage>-<version>` carries: a second open for the same stage and version finds
the branch and the open pull request, commits the manifest to that branch, and reuses the
pull request. With ADR-003 invariant a) enforced, a version maps to one digest within the
manifest, so (stage, version) fixes (stage, digest): no two opens under one key can promote
two digests, and the re-cut that would try is refused before any branch exists. The branch
is not renamed. The converse does not hold, on purpose: the final-release retag
(`1.2.0-rc.3` and `1.2.0` on one digest, ADR-004) gives two keys for one digest, and each is
its own promotion of its own release identity, with its own pull request. Invariant a)
covers the manifest as it is, not its history (ADR-003, "Not enforced yet"), so a version
that left the manifest and was cut again on another digest reuses its old branch and pull
request, which the commit then updates to the new content. *Enforcement class:* `test`
(`tests/unit/test_promotion_pr.py`).

### Auth (per the onboarding model)

M4 is self-first: a token from settings (`GORA_GITHUB_TOKEN`, or `GITHUB_TOKEN` in CI). The
**GitHub App** path (installation tokens, multi-repo) is the "register, don't wire" model for
managing *other* repos — deferred until then. Auth is the adapter's concern (ADR-006); the
core stays auth-agnostic.

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

## Consequences

- M4 exit (ADR-009) is met: GORA generates deterministic promotion PRs end-to-end.
- The GitHub adapter is the reusable seam for M5 (API/Slack triggers) and for the
  "register, don't wire" onboarding.
- A thin `httpx` adapter keeps both the dependency surface and the capability surface small
  (ADR-002/006).
- The audit trail now spans CLI operations *and* adapter mutations — reinforcing the "every
  step recorded" value.
- Project Intelligence / ADR Sync is renumbered to **ADR-014**.

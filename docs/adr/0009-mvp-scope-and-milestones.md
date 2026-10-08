# ADR-009: MVP Scope and Milestones

**Status:** Proposed
**Date:** 2026-04-11
**Amended:** 2026-10-02 — Milestone 3 exit, and the order of work to reach it
**Amended:** 2026-10-03 — order of work after the gate pull request; where the server runs
**Amended:** 2026-10-04 — approvals, not merges by hand (ADR-023)
**Amended:** 2026-10-08 — Milestone 3 exit restated in the model of ADR-024 (ADR-024)

---

## Context

GORA has a broad long-term vision (ADR-001). Without explicit MVP boundaries and sequencing, the project risks premature expansion. This ADR defines what gets built first and in what order.

## Decision

### MVP objectives

The MVP must prove that GORA can:

1. Execute a deterministic release workflow end-to-end (self-releasing)
2. Manage release contracts for GORA itself and at least one external project (cally-platform)
3. Establish the core release engine (manifest, RC, promotion, rollback)
4. Validate the CLI as the primary interaction model
5. Remain lightweight prior to durable orchestration adoption

### MVP target projects

| Project | Role |
| --------- | ------ |
| gora | Self-releasing — GORA manages its own release pipeline |
| cally-platform | External proving ground — SaaS platform for real-world validation |
| zrjes-tooling | DevOps tooling repo — secondary validation target |

### Milestone sequence

#### Milestone 1: Foundation (current)

Lock architecture decisions, establish terminology, define release contract.

*Amended 2026-10-08 (correction):* Milestone 1 is done; what is built is listed in
release-model section 13.

Deliverables: ADR set, manifest spec, repository structure.

Exit: stable conceptual model, core terminology fixed, MVP boundaries explicit.

#### Milestone 2: Core release engine

Implement minimal release engine as CLI.

Commands: `gora status`, `gora cut-rc`, `gora promote`, `gora rollback`.

Deliverables: manifest parser, manifest validator, digest-aware RC creation, stage promotion logic, rollback logic.

Exit: a single-service release flow can be controlled through Git-defined manifest state.

#### Milestone 3: Self-release (dogfooding)

GORA releases itself using its own release engine.

Deliverables: GORA's own release manifest, CI integration producing immutable artifacts, promotion of GORA through its own stages.

Exit: GORA can demonstrate deterministic candidate selection and promotion on itself.

#### Milestone 4: GitHub adapter + Promotion PR workflow

First end-to-end automated workflow.

Deliverables: GitHub adapter (PR/commit operations), Promotion PR workflow, Slack/API trigger path.

Exit: GORA can generate deterministic promotion PRs end-to-end.

#### Milestone 5: API and Slack integration

Expose release engine through FastAPI and Slack Bolt.

Deliverables: HTTP API for release operations, Slack commands for status/promote/rollback, intent routing layer.

Exit: release operations accessible through CLI, API, and Slack.

#### Milestone 6: Operational adapters and diagnostic workflows

Add read-only operational visibility.

Deliverables: ArgoCD adapter (read-only), Kubernetes adapter (read-only), Deployment Verification workflow, Incident Triage workflow.

Exit: GORA provides operational diagnostic value beyond release control.

#### Milestone 7: cally-platform integration

Deploy GORA release management for cally-platform.

Exit: GORA manages release lifecycle for a real SaaS application.

#### Milestone 8: Temporal readiness evaluation

Formal go/no-go on durable orchestration.

Evaluate: do current workflows require approval wait states, long-running execution, retry/resume/compensation, or multi-stage orchestration?

Deliverable: architecture decision on Temporal adoption.

### Post-MVP roadmap

- GitOps integration (ArgoCD reconciliation from GORA manifests)
- Kargo integration for promotion orchestration

  *Amended 2026-10-08 (ADR-012):* not planned: GORA is the promotion, and Kargo would be a
  second writer of promotion state (L16).
- Policy engine and approval gates

  *Amended 2026-10-08 (ADR-023 section 8):* the gates of ADR-023 are part of the Milestone 3
  exit (Amendments).
- Progressive delivery integration (Argo Rollouts)
- Public open-source release
- SaaS wrapper (visualization, audit UX, multi-tenant control)

### Build order priority

1. Highest-value workflows first
2. Favor synchronous execution initially
3. Git-backed / read-only before privileged mutations
4. Validate each layer before expanding scope

## Consequences

- Milestones 1-3 are release-engine focused (no agent/workflow complexity)
- Self-releasing (Milestone 3) is a hard gate — it validates the entire model
- cally-platform integration validates GORA against real-world multi-service delivery
- Temporal decision is data-driven, not speculative

## Amendments

### 2026-10-02 — Milestone 3 exit, and the order of work to reach it

GORA has cut and promoted its own candidates (`0.2.0-rc.5` is in `dev` and `staging`), but by
hand through the CLI, without verified evidence, and never to a final release. Milestone 3 is
therefore redefined by the real scenario it must demonstrate, and in practice it absorbs the
promotion-PR part of Milestone 4:

**Exit:** GORA releases its own `0.2.0` through its own mechanism, end to end:

1. A pushed `v0.2.0-rc.N` tag builds one image; its signature, provenance and SBOM are
   attached to the digest (ADR-011, ADR-018 section 2). *(Amended 2026-10-03: one image
   index for `linux/amd64` and `linux/arm64`, ADR-021.)*
2. `cut-rc` records that digest only after GORA has verified the evidence (ADR-018 section 4).
3. GORA opens a promotion pull request per stage, `dev`, then `staging`, computed from the
   manifest on `main` (ADR-013); a human merges each one. The policy enforces the order.
   *(Amended 2026-10-04: each one merges as its gate says, through the code host's auto-merge,
   and a person approves an `approval` gate; nobody merges by hand, ADR-023. The exit therefore
   needs the gates and GORA's required status of ADR-023 section 8.)*
4. GORA opens the final promotion pull request; after the merge it tags the **same digest**
   `0.2.0` in the registry and creates the git tag `v0.2.0` (ADR-018 section 7). No rebuild.
5. Every step is in the audit journal (ADR-017), each decision has a VSA, and Slack shows the
   release with `Image: <image>@<digest>` (ADR-015).

**Order of work** (each a reviewed PR; none starts before the previous one is merged):

1. Gates and an immutable image: `make verify` does not trust caches; the release image runs
   the installed CLI without syncing at start (ADR-011 amendment); container gates.
2. ADR-018, implemented: the release workflow on a reusable workflow with
   `actions/attest` provenance (SLSA Build L3) and a CycloneDX SBOM, exact signer identity, a
   build only for a pre-release tag; the verification adapter and `release-policy.yaml` with
   the promotion graph in `cut-rc` and `promote`; the VSA.
3. ADR-013 revised: promotion computed from `main`; then the final-release retag (registry
   adapter, git tag).
4. The release of `0.2.0` itself, as the exit above.

After the exit: a command to move to the next release line (`0.3.0`), which today needs a
reviewed manual edit of the manifest (ADR-003).

*Amended 2026-10-08 (ADR-024 sections 1 and 9):* the exit and the order of work above are
restated in the 2026-10-08 amendment, in the model of ADR-022, ADR-023 and ADR-024. "A command
to move to the next release line" is not needed: there are no release lines.

### 2026-10-03 — order of work after the gate pull request; where the server runs

- Step 1 of the order of work is done (#46: check-only, bounded gates and an immutable image).
- A step is added before ADR-018 is implemented: **build inputs pinned and two platforms**.
  Base images and uv by digest, actions by commit SHA; the release image becomes one image
  index for `linux/amd64` and `linux/arm64`, built on native runners; CI runs source checks
  once and behaviour checks on both platforms (ADR-021). ADR-018 is implemented on that
  workflow, so its evidence covers the index from the start.
- Milestone 3 exit, step 1, now reads "one image index" (ADR-021).
- The Milestone 5 server (ADR-014) is deployed Kubernetes-first, with Git as its only release
  state (ADR-019). The Milestone 3 exit needs no server: the core and its CLI are complete.
  The server starts with the Slack/API trigger path of Milestone 4 and grows in Milestone 5.

### 2026-10-04 — approvals, not merges by hand

ADR-023 decides that nobody merges a GORA pull request by hand: a person approves a promotion and
the code host's auto-merge merges it. Step 3 of the Milestone 3 exit is amended in place, and the
exit now needs the gates and GORA's required status.

### 2026-10-08 — Milestone 3 exit restated in the model of ADR-024

The exit of 2026-10-02 mixes the tag-started build and `cut-rc` with the gates of ADR-023, and
names only `dev` and `staging`, while the model needs `prod` as the last stage. It is restated:

**Exit:** GORA promotes a version of itself into `prod` through its own mechanism, end to end:

1. A merge to `main` that changes GORA's inputs is built once into one image index, with its
   signature, provenance and SBOM attached to the digest (ADR-011, ADR-018 section 2, ADR-021).
2. `gora register` verifies the evidence, computes the version (ADR-024 section 1) and creates
   the annotated tag `vX.Y.Z-rc` on the source commit and the registry tag `X.Y.Z-rc`.
3. GORA opens the promotion into `dev`, `staging` and `prod` in turn (ADR-024 section 9), each
   once the stage before has verified the version; the code host's auto-merge merges each when
   its gate is met, and nobody merges by hand (ADR-023 section 8).
4. The merge into `prod` creates the tag `vX.Y.Z` on the same source commit, the registry tag
   `X.Y.Z` on the same digest, and the platform state `prod/YYYY.MM.DD.N`. No rebuild.
5. Every step is in the audit journal (ADR-017), each decision has a VSA, and Slack shows the
   promotion with `Image: <image>@<digest>` (ADR-015).

The order of work toward the exit is kept in the backlog (CLAUDE.md, "Notes"), not in this ADR. "A
command to move to the next release line" is not needed: there are no release lines (ADR-024
section 1). The post-MVP list gains a note: the gates of ADR-023 belong to the Milestone 3 exit.
Kargo integration is not planned (ADR-012).

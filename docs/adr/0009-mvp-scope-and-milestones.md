# ADR-009: MVP Scope and Milestones

**Status:** Proposed
**Date:** 2026-04-11

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
- Policy engine and approval gates
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

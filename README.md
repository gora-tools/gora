# GORA

## Deterministic GitOps Release Engine

Manages the full release lifecycle from candidate creation through promotion, verification, and rollback. Policy-driven. Git-backed. Platform-agnostic. Self-releasing.

## Problem

Modern delivery pipelines have build systems and deployment systems, but no authoritative release contract between them.

Release state is fragmented across pipelines, scripts, and human communication. Teams lack clarity on what artifact is eligible for promotion, how promotion is controlled, and how rollback is performed safely.

## How GORA works

```txt
CI produces immutable artifacts
    -> GORA defines release state in Git (manifest)
        -> CD deploys from that state
```

GORA is the release control layer between CI and CD. It is not a build engine and not a cluster reconciler.

## Core principles

- Git is the source of release state
- Artifacts are immutable (digest-based)
- Promotion moves the same artifact (no rebuild)
- Only one active candidate exists per release line
- Release is a state, not an event
- Rollback restores a known prior state via Git

## Status

Milestone 2 complete — the core release engine works: `status`, `cut-rc`, `promote`, and `rollback` via a service layer, 30 tests green, `make verify` and `make docker-smoke` passing. Milestone 3 (self-release) is next. Architecture is documented as ADRs.

## Documentation

Architecture Decision Records are in [`docs/adr/`](docs/adr/):

| ADR | Decision |
| --- | --- |
| [001](docs/adr/ADR-001-vision-and-positioning.md) | Vision and positioning |
| [002](docs/adr/ADR-002-architectural-principles.md) | Architectural principles |
| [003](docs/adr/ADR-003-release-contract-model.md) | Release contract model (manifest + RC) |
| [004](docs/adr/ADR-004-promotion-and-rollback-model.md) | Promotion and rollback model |
| [005](docs/adr/ADR-005-technology-stack.md) | Technology stack |
| [006](docs/adr/ADR-006-tool-adapter-contracts.md) | Tool adapter contracts |
| [007](docs/adr/ADR-007-workflow-execution-model.md) | Workflow execution model |
| [008](docs/adr/ADR-008-repository-structure.md) | Repository structure |
| [009](docs/adr/ADR-009-mvp-scope-and-milestones.md) | MVP scope and milestones |
| [010](docs/adr/ADR-010-agent-architecture.md) | Agent architecture |
| [011](docs/adr/ADR-011-self-release-pipeline.md) | Self-release pipeline (signed OCI: image + SBOM + provenance) |
| [012](docs/adr/ADR-012-positioning-vs-promotion-executors.md) | Positioning vs promotion executors (Kargo / Argo) |
| [013](docs/adr/ADR-013-promotion-pr-workflow.md) | Promotion PR workflow (GitHub adapter) |
| [014](docs/adr/ADR-014-api-slack-intent-routing.md) | API, Slack & intent routing (M5) |
| [015](docs/adr/ADR-015-slack-connector.md) | Slack connector (notifications & commands) |

Workflow catalog: [`docs/catalog/workflow-catalog.md`](docs/catalog/workflow-catalog.md)

## License

Private. Not yet licensed for public distribution.

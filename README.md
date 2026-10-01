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

Under active development. The milestone roadmap is [ADR-009](docs/adr/0009-mvp-scope-and-milestones.md); the current release is whatever the newest `v*` tag points at; `make verify` is the gate every change passes, and mirrors CI exactly.

## Documentation

Architecture Decision Records are in [`docs/adr/`](docs/adr/):

| ADR | Decision |
| --- | --- |
| [0001](docs/adr/0001-vision-and-positioning.md) | Vision and positioning |
| [0002](docs/adr/0002-architectural-principles.md) | Architectural principles |
| [0003](docs/adr/0003-release-contract-model.md) | Release contract model (manifest + RC) |
| [0004](docs/adr/0004-promotion-and-rollback-model.md) | Promotion and rollback model |
| [0005](docs/adr/0005-technology-stack.md) | Technology stack |
| [0006](docs/adr/0006-tool-adapter-contracts.md) | Tool adapter contracts |
| [0007](docs/adr/0007-workflow-execution-model.md) | Workflow execution model |
| [0008](docs/adr/0008-repository-structure.md) | Repository structure |
| [0009](docs/adr/0009-mvp-scope-and-milestones.md) | MVP scope and milestones |
| [0010](docs/adr/0010-agent-architecture.md) | Agent architecture |
| [0011](docs/adr/0011-self-release-pipeline.md) | Self-release pipeline (signed OCI: image + SBOM + provenance) |
| [0012](docs/adr/0012-positioning-vs-promotion-executors.md) | Positioning vs promotion executors (Kargo / Argo) |
| [0013](docs/adr/0013-promotion-pr-workflow.md) | Promotion PR workflow (GitHub adapter) |
| [0014](docs/adr/0014-api-slack-intent-routing.md) | API, Slack & intent routing (M5) |
| [0015](docs/adr/0015-slack-connector.md) | Slack connector (notifications & commands) |
| [0016](docs/adr/0016-a-gate-must-prove-it-can-fail.md) | A gate must prove it can fail (falsifiable gates) |

Workflow catalog: [`docs/catalog/workflow-catalog.md`](docs/catalog/workflow-catalog.md)

## License

Private. Not yet licensed for public distribution.

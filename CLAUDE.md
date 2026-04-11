# GORA — AI Agent Context

## What GORA is

GORA is a deterministic GitOps Release Engine. It manages the full release lifecycle from candidate creation through promotion, verification, and rollback. Policy-driven. Git-backed. Platform-agnostic. Self-releasing.

GORA is the release control layer between build systems (CI) and deployment/reconciliation systems (CD). It is not a build engine, not a cluster reconciler, and not a replacement for CI/CD.

## Core model

- CI produces immutable artifacts
- GORA defines release state in Git (manifest)
- CD deploys from that state
- Artifact = unit of deployment
- Manifest = release contract (digest-only, immutable)
- Promotion = controlled movement of same digest through environments

## Architectural principles

- Deterministic code owns workflow sequencing
- LLMs interpret intent but do not control privileged execution
- Slack is interface only, never source of truth
- Git / metadata repositories remain authoritative state
- External integrations occur only through bounded tool adapters
- Adapters expose narrow, explicit operations only
- Mutating operations require policy enforcement
- No unrestricted model-to-system execution

## Key constraint

GORA is a mechanism that supports feature-based delivery. It is NOT an implementation of any specific organization's delivery model. Feature flags and application-level rollback are application concerns — GORA operates at the artifact/infrastructure level.

## Technology stack

Python, FastAPI, Slack Bolt, synchronous workflow execution (initially). Temporal deferred until workflow complexity justifies it. Monorepo, single service for MVP.

## Documentation

All architecture decisions are in `docs/adr/` as Architecture Decision Records. The ADR set is authoritative. When generating recommendations, code, or implementation plans:

- Do not suggest changes that violate architectural principles (ADR-002)
- Do not introduce orchestration complexity before justified (ADR-007)
- Do not collapse deterministic workflow logic into prompt-only agent behavior (ADR-010)
- Preserve release-engine-first positioning (ADR-001)

## Repository structure

See `docs/adr/ADR-008-repository-structure.md` for the full layout. Source code goes in `src/gora/`.

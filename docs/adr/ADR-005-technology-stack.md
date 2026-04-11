# ADR-005: Technology Stack

**Status:** Accepted
**Date:** 2026-04-11

---

## Context

GORA needs a technology stack that supports rapid MVP development by a small team (initially one person), while remaining production-grade and extensible for long-term evolution.

The stack must support: API surface for Slack/CLI/HTTP triggers, bounded tool adapters for external systems, synchronous workflow execution (initially), and future evolution toward durable orchestration.

## Decision

### MVP stack

| Component | Choice | Rationale |
| --------- | ------ | --------- |
| Language | Python | Fast MVP iteration, strong ecosystem for API/automation/AI |
| API framework | FastAPI | Async-capable, typed, OpenAPI generation, production-grade |
| Slack integration | Slack Bolt (Python) | Official SDK, event/command/interaction handling |
| CLI framework | Typer | Type-hint based, auto-generated help, Click underneath |
| Workflow execution | Service-layer synchronous | No orchestration overhead until justified |
| Data persistence | Git-only (manifest as release state) | Auditable, deterministic, zero-ops |
| CI | GitHub Actions | Native for GitHub, free tier, sufficient for MVP |
| Containerization | Docker | Standard packaging and deployment |

### Persistence model

**MVP: Git-only.** Release manifests live in Git. All promotion history is Git log. Rollback is Git revert. RC state, stage pointers, and release identity — all represented as manifest commits. No database required.

**Future: Git + SQLite.** When the number of managed services and RC history grows (dozens of services, thousands of RCs), add SQLite as a local cache/index for fast queries and filtering. Zero-ops, file-based, fits CLI-first model.

**Later: Git + Postgres.** Only if GORA evolves into a hosted multi-tenant service. Postgres for metadata, audit history, multi-user state. Not on the MVP horizon.

Git always remains the source of truth for release state. Any database layer is a read-optimized projection, not the primary store.

### CI strategy

**Now: GitHub Actions.** Native integration with GitHub repos (gora, cally-platform, zrjes-tooling). Sufficient for: running tests, building artifacts, publishing container images, validating manifests.

**Later: Tekton.** Migration when Kubernetes infrastructure is in place and cloud-native CI becomes justified. GORA's CI adapter (ADR-006) abstracts the provider, so migration is bounded.

### Explicitly deferred

| Component | Deferred until |
| --------- | -------------- |
| Temporal | Workflows require approval wait states, long-running execution, or retry/resume/compensation (see ADR-007) |
| Postgres | State management complexity exceeds what Git-backed manifests + SQLite can handle |
| Redis | Caching or pub/sub requirements emerge |
| Frontend/UI | Core release engine is proven and CLI/Slack interface is insufficient |
| Tekton | Kubernetes infrastructure is available and GHA limitations are blocking |

### CLI

GORA CLI is the primary local interface for release operations:

```txt
gora status
gora cut-rc
gora promote
gora rollback
```

Implementation: Python CLI with Typer, operating directly on Git-backed manifests.

### Build and development

| Tool | Purpose |
| ---- | ------- |
| Make | Task runner (lint, test, build, run) |
| pytest | Testing |
| ruff | Linting and formatting |
| mypy | Type checking |
| Docker | Local and CI builds |
| uv | Python dependency management |

## Consequences

- Repository structure follows Python project conventions (see ADR-008)
- The previous Go-style layout (cmd/, internal/, pkg/) from early planning is superseded
- All adapters, workflows, and core logic are Python modules
- Migration to Go for performance-critical components remains possible but is not planned for MVP
- Git-only persistence means GORA has zero infrastructure dependencies for MVP — only Git and a Python runtime
- CI adapter abstraction ensures GitHub Actions → Tekton migration is bounded

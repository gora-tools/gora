# ADR-005: Technology Stack

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-10-08 — persistence, rollback and CLI follow the release model (ADR-022, 023)

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

*Amended 2026-10-08 (ADR-022 sections 6 and 10):* Git stays the only store, but two
statements above are replaced. A rollback is a promotion pull request forward to a digest the
stage held before, never a `git revert`. A candidate is not in the manifest: it lives in its
version tag, its registry tag and its verification attestation; the manifest holds what each
stage holds.

**Future: Git + SQLite.** When the number of managed services and RC history grows (dozens of services, thousands of RCs), add SQLite as a local cache/index for fast queries and filtering. Zero-ops, file-based, fits CLI-first model.

**Later: Git + Postgres.** Only if GORA evolves into a hosted multi-tenant service. Postgres for metadata, audit history, multi-user state. Not on the MVP horizon.

Git always remains the source of truth for release state. Any database layer is a read-optimized projection, not the primary store.

### CI strategy

**Now: GitHub Actions.** Native integration with GitHub repos (gora, cally-platform, zrjes-tooling). Sufficient for: running tests, building artifacts, publishing container images, validating manifests.

**Later: cloud-native CI (Tekton *or* Argo Workflows).** When Kubernetes infrastructure exists and in-cluster CI is justified. The choice is deliberately left open — Tekton (CNCF-neutral) vs Argo Workflows (unifies with an Argo CD / Rollouts / Kargo stack) — and decided *then*, not now, to avoid committing to an engine not yet used. GORA stays **CI-engine-agnostic**: the CI adapter (ADR-006) abstracts the provider, so GORA never depends on any one CI engine.

### Explicitly deferred

| Component | Deferred until |
| --------- | -------------- |
| Temporal | Workflows require approval wait states, long-running execution, or retry/resume/compensation (see ADR-007). *Amended 2026-10-08 (ADR-023 section 8):* a wait for an approval is an open pull request on the code host, not a wait state inside GORA. |
| Postgres | State management complexity exceeds what Git-backed manifests + SQLite can handle |
| Redis | Caching or pub/sub requirements emerge |
| Frontend/UI | Core release engine is proven and CLI/Slack interface is insufficient |
| Cloud-native CI (Tekton / Argo Workflows) | Kubernetes infrastructure is available and GHA limitations are blocking; engine chosen then, via the CI adapter |

### CLI

GORA CLI is the primary local interface for release operations:

```txt
gora status
gora cut-rc
gora promote
gora rollback
```

Implementation: Python CLI with Typer, operating directly on Git-backed manifests.

*Amended 2026-10-08 (ADR-023 section 2):* `register` replaces `cut-rc`, and the CLI, the HTTP
API and Slack share one grammar.

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
- CI adapter abstraction keeps GORA CI-engine-agnostic (GitHub Actions now; Tekton or Argo Workflows later) — migration is bounded

## Amendments

### 2026-10-08 — persistence, rollback and the CLI follow the release model

Git stays the only store of release state. A rollback is a forward promotion, never a
`git revert`; a candidate lives in its tags and attestation, not in the manifest; the CLI's
commands are those of ADR-023; an approval waits as an open pull request, not inside GORA.

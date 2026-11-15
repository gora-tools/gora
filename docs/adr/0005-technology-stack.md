# ADR-005: Technology Stack

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-10-08 — persistence, rollback and CLI follow the release model (ADR-022, 023)
**Amended:** 2026-10-08 — durable orchestration is not planned (correction)
**Amended:** 2026-11-15 — the HTTP client is httpx2 (ADR-013 chose httpx)

---

## Context

GORA needs a technology stack that supports rapid MVP development by a small team (initially one person), while remaining production-grade and extensible for long-term evolution.

The stack must support: API surface for Slack/CLI/HTTP triggers, bounded tool adapters for external systems, synchronous workflow execution (initially), and future evolution toward durable orchestration.

*Amended 2026-10-08 (correction, ADR-007):* durable orchestration is not planned.

## Decision

### MVP stack

| Component | Choice | Rationale |
| --------- | ------ | --------- |
| Language | Python | Fast MVP iteration, strong ecosystem for API/automation/AI |
| API framework | FastAPI | Async-capable, typed, OpenAPI generation, production-grade |
| HTTP client | httpx2 | Maintained fork of httpx 0.28.1, same API; `TestClient` and the SDKs use it |
| Slack integration | Slack Bolt (Python) | Official SDK, event/command/interaction handling |
| CLI framework | Typer | Type-hint based, auto-generated help, Click underneath |
| Workflow execution | Service-layer synchronous | No orchestration overhead until justified |
| Data persistence | Git-only (manifest as release state) | Auditable, deterministic, zero-ops |
| CI | GitHub Actions | Native for GitHub, free tier, sufficient for MVP |
| Containerization | Docker | Standard packaging and deployment |

*Amended 2026-11-15 (the HTTP client; ADR-013, ADR-006):* GORA's one HTTP client library is
`httpx2`, in place of the `httpx` that ADR-013 chose, and `httpx` and `httpcore` leave
`uv.lock`. Measured on 2026-11-15: `httpx` 0.28.1 (2024-12-06) is the last stable `httpx`, and
its maintainers' line is `1.0.devN`, with breaking changes. `httpx2` 2.16.0 (2026-10-21) is the
latest release of the fork, on `httpcore2` 2.16.0 (2026-10-21) and `truststore` 0.10.5
(2026-10-12). `httpx2` is published from GitHub Actions with PyPI Trusted Publishing, and its
wheels carry no PEP 740 attestation, so the bytes GORA runs are the ones `uv.lock` pins by hash.
Starlette 1.7.0's `TestClient` imports `httpx2` first and falls back to `httpx` with a
deprecation warning, and `anthropic` 2.8.0, `openai` 3.51.0 and `mcp` 2.3.0 require `httpx2`.
The names the adapters and the tests use exist in `httpx2` with the same meaning, and the limits
ADR-006 measured are the same: 65,536 characters in a URL and 100 KiB in an incomplete event.
GORA's TLS context is its own (ADR-006, the egress rule) and goes to `httpx2` as `verify`,
unchanged, so the trust store, the proxy rule and every bound are as they were; `httpcore2`
imports `truststore` and does not use it under that context.

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

### 2026-10-08 — durable orchestration is not planned

A correction: the Context names an evolution toward durable orchestration; ADR-007 records that
phase 3 is not planned.

### 2026-11-15 — the HTTP client is httpx2

The stack names its HTTP client: `httpx2`, a maintained fork of `httpx` 0.28.1 with the same
API, which ADR-013 had chosen as `httpx`. The note under "MVP stack" holds the measurements.
ADR-013, ADR-006 and ADR-015 say `httpx2` where they name the library GORA runs on; what they
measured on `httpx` stays as it was written.

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

## Verify

Python 3.12 + uv; targets in `Makefile`.

```bash
make install-dev   # install deps incl. dev tools (uv)
make verify        # full gate: lint + format-check + typecheck + test (CI parity)
make docker-smoke  # build release image and verify the CLI boots (Docker)
```

`make verify` mirrors CI exactly (ruff check, ruff format --check, mypy, pytest) — it must
be green before any push. `make docker-smoke` builds `deployments/docker/Dockerfile` from a
clean **tracked-files-only** export (`git archive HEAD`, so it reproduces CI's checkout and
can't be fooled by ignored/untracked files) and runs `gora version` in the container. It also
runs in CI on every PR, so a broken build dependency fails there, not at release time.
Because it archives committed `HEAD`, **commit before running `make docker-smoke`** to test a
dependency/Dockerfile change — on a dirty tree it rebuilds the old HEAD (and prints a warning).

Individual targets also exist: `make lint | format | format-check | typecheck | test |
test-cov`. `make run-cli ARGS="status"` / `make run-api` for a manual smoke; `make clean`
resets caches.

## Notes

Where this repository keeps its open queue and its settled decisions. `/park` reads this
block rather than guessing, so it stays accurate or the skill writes to the wrong place.

```txt
Backlog: ~/workspace/notes/gora/backlog.md
ADR:     docs/adr/
```

The split is deliberate: **decisions belong to the project, the queue belongs to you.**
Anyone reading this code needs the ADRs, so they are tracked here. The open queue is private
thinking about the project and lives in the control plane alongside the other areas — see
`~/workspace/notes/gora/index.md`.

Because the backlog address is outside this repository, an agent working here **does not
write it**: it returns the entry as a `Parked:` field in its report, and the human files it.
ADR files follow the naming already in `docs/adr/` (`ADR-NNN-slug.md`), and a decision that
is still a question is a backlog entry, not an ADR.

## Commit & delegation

Global rules — Conventional Commits, no-AI-authorship, and the cowork⇄agent handoff
protocol + guardrails — live in `~/workspace/CLAUDE.md`. Only repo-specific notes above.
PRs on this repo: `gh` (GitHub).

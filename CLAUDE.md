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

See `docs/adr/0008-repository-structure.md` for the full layout. Source code goes in `src/gora/`.

## Verify

Python and uv are pinned (`.python-version` to the patch, `[tool.uv] required-version` in
`pyproject.toml`: uv refuses to run at another version); targets in `Makefile`.

```bash
make sync           # install uv.lock exactly, all extras, both tool groups (--locked)
make check          # source checks, once per commit: ruff, shellcheck, format --check, mypy
make behaviour      # behaviour checks, per platform: the test group and pytest
make verify         # both, on this machine: the local safety net before a push
make verify-export  # pre-push gate: make verify on committed HEAD, in a fresh export
make verify-linux   # behaviour checks on HEAD in a Linux container of this host's platform
make docker-smoke   # build the release image natively, run it read-only and offline
```

**CI is the gate of record** (ADR-021): `check` once, `test` and `image` on native
`linux/amd64` and `linux/arm64` runners, and `assemble`, which rehearses the release's image
index on every pull request. Local runs save a round trip; they are never evidence.

**Pre-push: `make verify-export`**, plus `make verify-linux` from macOS. Both check committed
`HEAD`, not the working tree, and refuse a dirty tree; `make docker-smoke` also builds from
`git archive HEAD`. **Commit first**, then run them.

Every local gate runs natively and is bounded from outside: `PLATFORM` defaults to the
Docker host's own platform, and another one runs only as a diagnosis that says it is
emulated and not a gate. `make verify`, `verify-export`, `check` and `behaviour` run under
GNU timeout (`gtimeout` on macOS, from coreutils) or not at all; every gate image is pulled
under a time limit first. Every gate container is bounded and labelled `gora.gate`; a
leftover one stops the next run (exit 2) and the message names the `docker rm -f` that
clears it (TESTING rule 8).

Individual targets also exist: `make lint | format | format-check | typecheck | test |
test-cov`. `make run-cli ARGS="status"` / `make run-api` for a manual smoke; `make clean`
resets caches.

## Notes

Where this repository keeps its open queue and its settled decisions. `/park` reads these
addresses rather than guessing, so they stay accurate or the skill writes to the wrong place.

    Backlog: ~/workspace/notes/gora/backlog.md
    ADR:     docs/adr/   (`NNNN-title-with-dashes.md`, as adr-tools and MADR do)

Two addresses and nothing else. *Why* the queue lives outside this repository:
`~/workspace/docs/adr/0017-decisions-travel-queues-stay.md`. *Who* may write these paths:
Agent guardrails in `~/workspace/CLAUDE.md` — that rule lives in one place, on purpose.

## Commit & delegation

Global rules — Conventional Commits, no-AI-authorship, and the cowork⇄agent handoff
protocol + guardrails — live in `~/workspace/CLAUDE.md`. Only repo-specific notes above.
PRs on this repo: `gh` (GitHub).

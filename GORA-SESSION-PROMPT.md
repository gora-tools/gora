# GORA Session Context Prompt

Copy this into a new session to restore full context.

---

## Project

GORA — deterministic GitOps release-control mechanism (the authoritative release contract between CI and CD). Bridges Feature-Based Delivery, Platform Releases, and AI-driven SDLC through digest-pinned release manifests, policy-based promotion, and Git as the single source of truth. Mechanism-first, not a platform (see notes/gora/POSITIONING.md, external).

Repository: gora (github.com/zuwarskejes-labs/gora)
Related: cally-platform (B2B SaaS proving ground)

## Architecture

13 ADRs in `docs/adr/` (ADR-014 Project Intelligence / ADR Sync planned). Read them — they are authoritative. Key decisions:

- ADR-001: Release engine positioning, self-releasing requirement
- ADR-002: Non-negotiable principles — deterministic code, bounded adapters
- ADR-003: Manifest v0.1 (single artifact) → v0.2 (Release BOM)
- ADR-004: Promotion preserves digest, rollback is infra-level
- ADR-006: Tool adapter contracts (GitHub, ArgoCD, K8s, CI/CD, AWS)
- ADR-009: 8 milestones. Self-release (M3) is hard gate
- ADR-010: Agent is assistant, not execution engine
- ADR-011: Self-release pipeline — signed OCI (image + SBOM + provenance), env-style stages from `dev`
- ADR-012: Positioning vs promotion executors (GORA vs Kargo — different, cluster-optional segment)
- ADR-013: Promotion PR workflow (bounded GitHub adapter; GORA opens PRs, never merges)

## Current state

- **Milestone 1: Foundation** — DONE.
- **Milestone 2: Core release engine** — DONE. `status`, `cut-rc`, `promote`, `rollback` via a `services/` layer (`ReleaseService`: load→mutate→persist→audit).
- **Milestone 3: Self-release** — DONE + dogfooded. `release.yml` (tag `v*`): build → cosign sign → syft SBOM → BuildKit registry-native provenance → GHCR; a `verify` job self-verifies (cosign verify + SBOM + provenance) with the in-workflow token; then `cut-rc` + `promote dev` record the real digest. Current released candidate: `0.2.0-rc.5`.
- **Milestone 4: GitHub adapter + Promotion PR** — DONE + dogfooded. Bounded `httpx` adapter (`src/gora/adapters/github/`); `gora promote <stage> --open-pr --repo owner/name` opens a deterministic, idempotent, audited promotion PR (GORA never merges). Demonstrated: GORA opened its own dev→staging PR.
- **Milestone 5: API + Slack** — NEXT (ADR-009). FastAPI + Slack Bolt + intent routing; the read-only visualization UI ("watch the artifact move through gates/envs") lands here.
- 43 tests. `make verify` = CI parity (ruff check + format --check + mypy + pytest); `make docker-smoke` builds from `git archive HEAD` (tracked-only) and also runs in CI on every PR. Verification standard (with teeth): gates reproduce CI, determinism, every bug → a test, pipeline self-verifies.
- Tech: Python 3.12, Typer, Pydantic, httpx, uv, GitHub Actions (FastAPI + Slack Bolt arrive at M5).
- Delegation: cowork drafts diffs + a `🔸 TASK HANDOFF`; a Claude Code agent on real hardware runs the gate, commits (git identity via `includeIf` — never set manually), opens the PR — **never merges**; a human merges (`main` is PR-only). Full protocol in `~/.claude/CLAUDE.md`.

## Three product pillars

1. **Release mechanism** (gora release + agent) — MVP focus
2. **Flow automation** (task/doc management + agent) — ADR Sync workflow designed in `docs/catalog/workflow-catalog.md`, cally-platform has working `project.yml` + setup script
3. **Infra management** (tofu + atlantis + agent) — future

## Conventions

- I (user) communicate in Russian with English technical terminology
- I value determinism, sequential development, committed state at each step
- Don't commit without asking. Don't add Co-authored-by trailer. Don't add trailing summaries.
- ADRs are the source of truth. Don't violate them.
- GitHub first, Bitbucket later. Jenkins dropped — pure GitOps. CI = GitHub Actions now; cloud-native CI (Tekton *or* Argo Workflows) deferred until k8s infra (GORA stays CI-engine-agnostic via adapter)
- Target delivery stack (adapters, not GORA internals): cosign + SBOM + provenance, Kargo, ArgoCD, Argo Rollouts, Kyverno

## What to do

Read the auto-memory (MEMORY.md index + linked files) for full context. Read relevant ADRs before making any design decisions. Check `CLAUDE.md` for agent instructions.

Current task: [FILL IN WHEN STARTING NEW SESSION]

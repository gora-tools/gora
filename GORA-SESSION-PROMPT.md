# GORA Session Context Prompt

Copy this into a new session to restore full context.

---

## Project

GORA — deterministic GitOps control plane. Bridges Feature-Based Delivery, Platform Releases, and AI-driven SDLC through immutable platform snapshots, policy-based promotion, and Git as the single source of truth.

Repository: gora (github.com/zuwarskejes-labs/gora)
Related: cally-platform (B2B SaaS proving ground)

## Architecture

11 ADRs in `docs/adr/` (ADR-012 GORA-vs-executors and ADR-013 Project Intelligence planned). Read them — they are authoritative. Key decisions:

- ADR-001: Release engine positioning, self-releasing requirement
- ADR-002: Non-negotiable principles — deterministic code, bounded adapters
- ADR-003: Manifest v0.1 (single artifact) → v0.2 (Release BOM)
- ADR-004: Promotion preserves digest, rollback is infra-level
- ADR-006: Tool adapter contracts (GitHub, ArgoCD, K8s, CI/CD, AWS)
- ADR-009: 8 milestones. Self-release (M3) is hard gate
- ADR-010: Agent is assistant, not execution engine
- ADR-011: Self-release pipeline — signed OCI (image + SBOM + provenance), env-style stages from `dev`

## Current state

- **Milestone 1: Foundation** — DONE. ADRs, manifest spec, repo structure, CI green
- **Milestone 2: Core release engine** — DONE. `status`, `cut-rc`, `promote`, `rollback` all working as full CLI commands via a `services/` layer (`ReleaseService`: load→mutate→persist→audit). 30 tests; `make verify` + `make docker-smoke` green
- **Milestone 3: Self-release** — NEXT (hard gate). Own `release-manifest.yaml`, release workflow (signed OCI), promote GORA through its own stages
- Tech: Python 3.12, FastAPI, Typer, Slack Bolt, uv, GitHub Actions
- Domain + services implemented and tested: ReleaseCandidate, ReleaseManifest, StageState, PromotionResult, ReleaseService
- Delegation: cowork drafts diffs + handoff; a Claude Code agent runs `make verify`/`docker-smoke`, commits, opens PR (see `~/.claude/CLAUDE.md`)

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

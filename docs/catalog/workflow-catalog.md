# Workflow Catalog

## Purpose

Canonical inventory of GORA workflows. Each entry defines purpose, trigger, classification, required adapters, and target phase.

For execution model and classification rules, see [ADR-007](../adr/ADR-007-workflow-execution-model.md).

---

## Release / Promotion Workflows

### Promotion PR Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Generate a promotion pull request from approved release input |
| Trigger | CLI / Slack / API |
| Classification | Synchronous |
| Adapters | GitHub |
| Approval | No |
| Temporal | No |
| Phase | MVP (Milestone 4) |

### Release Promotion Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Execute full promotion lifecycle through merge, deploy, and verification |
| Trigger | Slack / API |
| Classification | Durable |
| Adapters | GitHub, ArgoCD, Kubernetes |
| Approval | Yes |
| Temporal | Yes |
| Phase | Post-MVP (Phase 3+) |

### Rollback Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Coordinate deterministic rollback of deployment / release state |
| Trigger | CLI / Slack / API / Automated Policy |
| Classification | Durable |
| Adapters | GitHub, ArgoCD, Kubernetes |
| Approval | Yes |
| Temporal | Yes |
| Phase | Post-MVP (Phase 3+) |

---

## Deployment / Operations Workflows

### Deployment Verification Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Validate deployment health after promotion or sync |
| Trigger | Post-deploy / Manual / Scheduled |
| Classification | Synchronous |
| Adapters | ArgoCD, Kubernetes |
| Approval | No |
| Temporal | No |
| Phase | MVP (Milestone 6) |

---

## Incident / Diagnostic Workflows

### Incident Triage Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Aggregate operational data for incident summarization and analysis |
| Trigger | Slack / Incident Channel / API |
| Classification | Synchronous |
| Adapters | Kubernetes, ArgoCD, AWS, CI/CD |
| Approval | No |
| Temporal | No |
| Phase | MVP (Milestone 6) |

### Incident Remediation Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Execute governed remediation playbook for known incident classes |
| Trigger | Slack / API / Policy Engine |
| Classification | Durable |
| Adapters | Kubernetes, AWS, CI/CD |
| Approval | Yes |
| Temporal | Yes |
| Phase | Post-MVP (Phase 3+) |

---

## Infrastructure Workflows

### Terraform PR Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Generate infrastructure/configuration pull requests from structured requests |
| Trigger | Slack / API |
| Classification | Synchronous |
| Adapters | GitHub |
| Approval | No |
| Temporal | No |
| Phase | MVP (Milestone 6) |

### Infrastructure Provision Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Coordinate full infrastructure provisioning / validation lifecycle |
| Trigger | Slack / API / Internal Platform Request |
| Classification | Durable |
| Adapters | GitHub, AWS, CI/CD |
| Approval | Yes |
| Temporal | Yes |
| Phase | Post-MVP (Phase 3+) |

---

---

## Project Intelligence Workflows

### ADR Sync Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Parse `## Implementation` sections from ADR markdown files and generate GitHub issues for uncovered tasks |
| Trigger | CLI / Post-ADR merge (GitHub Actions) / Manual |
| Classification | Synchronous |
| Adapters | GitHub |
| Approval | Yes — agent proposes, human confirms before creation |
| Temporal | No |
| Phase | Idea (not scheduled) |

#### Problem

ADRs are the source of truth for architectural decisions, but implementation tasks are tracked separately — in a project board, a todo list, or a human's head. Over time these drift apart: ADR says "implement X", but no issue was ever created, or the issue body lost context from the original decision.

#### Convention: `## Implementation` section in ADRs

Each ADR that has actionable work includes a structured `## Implementation` section:

```markdown
## Implementation

### cally-platform
- [ ] `feat:` Slack webhook step in deploy-staging.yml and deploy.yml — `[infra, p1]`
- [ ] `feat:` Telegram notification for critical failures — `[infra, p2]`

### gora
- [ ] `docs:` Notification adapter interface in adapter contracts — `[docs, p2]`
```

Format per task line:

- Checkbox `- [ ]` — marks it as a task (checked = already done / issue exists)
- Conventional commit prefix — becomes the issue title prefix
- Description — becomes the issue title suffix
- `[labels, ...]` — label hints for the GitHub adapter
- Repo header (`### repo-name`) — which repo the issue belongs to

This format is human-readable, writable without tooling, and machine-parseable.

#### Agent role (per ADR-010)

The agent does **not** create issues autonomously. It:

1. **Reads** the ADR file (or diff of a newly merged ADR)
2. **Extracts** task items from `## Implementation` sections
3. **Enriches** each task with context from the ADR body — decision rationale, constraints, links to related ADRs — to produce a meaningful issue body (not just the one-line task)
4. **Diffs** against existing open issues in the target repo (by title match) to avoid duplicates
5. **Proposes** a list of issues to create, with full body drafts, for human review
6. **Waits** for approval — human can edit, reject individual items, or approve all
7. After approval: **deterministic GitHub adapter** creates the issues, applies labels, sets milestone

The agent's value is in steps 3 and 4 — writing a rich issue body from ADR context, and deduplication. Creation is fully deterministic and auditable.

#### Data flow

```
ADR-*.md merged to main
  → GORA CLI / GitHub Action trigger
    → Agent: parse ## Implementation, enrich with ADR context, diff vs open issues
      → Proposal output (list of issue drafts)
        → Human approval (CLI confirm / Slack button / PR comment)
          → GitHub adapter: create issues, set labels + milestone
            → Issues appear in org project board automatically
```

#### What this enables

- **ADRs drive the backlog** — no manual translation from decision to task
- **Issue bodies have context** — not just "add Slack webhook" but why, which files, what the constraints are, links to the ADR
- **Drift detection** — re-running sync on an existing ADR checks if new tasks were added or tasks were completed (checkbox ticked) and reconciles
- **AI-assisted but human-controlled** — agent drafts, human ships

#### Future extension: drift report

Run against all ADRs in a repo and produce a report:

- Tasks with no issue → proposed for creation
- Issues closed but ADR checkbox still open → flagged for update
- ADRs with no `## Implementation` section → flagged for author review

---

## Prioritization rules

1. High-frequency workflows first
2. High-leverage workflows second
3. Low-risk (read-only / Git-backed) before privileged mutations
4. Synchronous before durable

## MVP workflow set

1. Promotion PR Workflow
2. Deployment Verification Workflow
3. Incident Triage Workflow
4. Terraform PR Workflow

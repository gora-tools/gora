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
|-------|-------|
| Purpose | Execute full promotion lifecycle through merge, deploy, and verification |
| Trigger | Slack / API |
| Classification | Durable |
| Adapters | GitHub, ArgoCD, Kubernetes |
| Approval | Yes |
| Temporal | Yes |
| Phase | Post-MVP (Phase 3+) |

### Rollback Workflow

| Field | Value |
|-------|-------|
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
|-------|-------|
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
|-------|-------|
| Purpose | Aggregate operational data for incident summarization and analysis |
| Trigger | Slack / Incident Channel / API |
| Classification | Synchronous |
| Adapters | Kubernetes, ArgoCD, AWS, CI/CD |
| Approval | No |
| Temporal | No |
| Phase | MVP (Milestone 6) |

### Incident Remediation Workflow

| Field | Value |
|-------|-------|
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
|-------|-------|
| Purpose | Generate infrastructure/configuration pull requests from structured requests |
| Trigger | Slack / API |
| Classification | Synchronous |
| Adapters | GitHub |
| Approval | No |
| Temporal | No |
| Phase | MVP (Milestone 6) |

### Infrastructure Provision Workflow

| Field | Value |
|-------|-------|
| Purpose | Coordinate full infrastructure provisioning / validation lifecycle |
| Trigger | Slack / API / Internal Platform Request |
| Classification | Durable |
| Adapters | GitHub, AWS, CI/CD |
| Approval | Yes |
| Temporal | Yes |
| Phase | Post-MVP (Phase 3+) |

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

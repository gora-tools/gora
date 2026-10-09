# Workflow Catalog

## Purpose

Canonical inventory of GORA workflows. Each entry defines purpose, trigger, classification, required adapters, and target phase.

For execution model and classification rules, see [ADR-007](../adr/0007-workflow-execution-model.md).

GORA has no workflow that writes to a cluster, to cloud infrastructure or to a pipeline: it
promotes through pull requests on the release repository, CI builds and CD deploys (ADR-001,
ADR-006, ADR-019).

---

## Release / Promotion Workflows

The release workflows follow the release model
([`docs/architecture/release-model.md`](../architecture/release-model.md)): ADR-022, ADR-023 and
ADR-024. All are synchronous and level-triggered: each run computes from Git, the code host and
the registry (ADR-027 section 2), completes in seconds, and leaves its result as tags, pull
requests, statuses and attestations. A wait for an approval is an open pull request on the host,
not a wait inside GORA, so none needs a durable-execution engine (ADR-023 section 8, ADR-007).

### Registration

| Field | Value |
| ----- | ----- |
| Purpose | Turn a verified build into a candidate: verify its evidence, compute its version, create its tags; the stage loop then proposes it to the first stage |
| Trigger | The project's CI (`gora register`) |
| Classification | Synchronous |
| Adapters | Registry (read evidence; add the registry tag `X.Y.Z-rc`), GitHub (the annotated tag `vX.Y.Z-rc`) |
| Approval | No |
| Phase | MVP (Milestone 3) |

### Stage loop

| Field | Value |
| ----- | ----- |
| Purpose | Open each next promotion pull request itself once the stage's current version has finished its verifications: the oldest offered version into an `approval` stage once it is eligible, the newest eligible version into an `auto` stage; hold a stage after a rollback; tag the final version when a version enters `prod` |
| Trigger | Events (registration, merge, status, review) and a periodic reconciliation; `gora promote` on request |
| Classification | Synchronous |
| Adapters | GitHub (pull requests, statuses, reviews, tags); registry (final retag) |
| Approval | As the stage's gate says: `auto`, or `approval` by reviews on the host |
| Phase | MVP (Milestone 3) |

### Rollback and rejection

| Field | Value |
| ----- | ----- |
| Purpose | Open a rollback pull request (kind A or B) or a rejection pull request on a person's request |
| Trigger | CLI / Slack / API (`gora rollback`, `gora reject`) |
| Classification | Synchronous |
| Adapters | GitHub |
| Approval | The request of a person whom policy allows is the decision; the host's auto-merge merges once the other required checks pass |
| Phase | MVP (with the stage loop) |

---

## Deployment / Operations Workflows

### Deployment Verification Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Read deployment health through read-only adapters, for diagnosis; it is not a stage verification, which a reporter posts as a commit status (ADR-024 section 7) |
| Trigger | Post-deploy / Manual / Scheduled |
| Classification | Synchronous |
| Adapters | ArgoCD, Kubernetes |
| Approval | No |
| Phase | MVP (Milestone 6) |

---

## Incident / Diagnostic Workflows

### Incident Triage Workflow

| Field | Value |
| ----- | ----- |
| Purpose | Read operational data through read-only adapters and summarize it for the person who decides a rollback; it writes nothing and is never a stage verification (ADR-024 section 7) |
| Trigger | Slack / API |
| Classification | Synchronous |
| Adapters | Kubernetes, ArgoCD, AWS, CI/CD — all read-only (ADR-006) |
| Approval | No |
| Phase | MVP (Milestone 6) |

---

## Ideas

- **ADR Sync / Project Intelligence**: turn the open tasks of ADRs into issues on the code
  host. Not decided and not scheduled; the idea is kept in the backlog (`CLAUDE.md`, "Notes").

## MVP workflow set

1. Registration and the stage loop
2. Rollback and rejection
3. Deployment Verification Workflow
4. Incident Triage Workflow

The milestones are in [ADR-009](../adr/0009-mvp-scope-and-milestones.md); the order of work is
in the backlog (`CLAUDE.md`, "Notes").

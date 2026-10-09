# ADR-006: Tool Adapter Contracts

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-10-08 — no merge, no cluster write, no pipeline trigger (ADR-019, 022, 023, 024)
**Amended:** 2026-10-09 — the registry adapter attaches GORA's verification summaries (ADR-027)

---

## Context

GORA integrates with external systems: GitHub, ArgoCD, Kubernetes, CI/CD pipelines, cloud providers. Without explicit boundaries, these integrations become uncontrolled, unauditable, and dangerous.

## Decision

Tool adapters are the only approved integration boundary between GORA and external systems.

### Architectural role

```txt
Agent / Workflow Layer
        |
        v
  Tool Adapter Layer
        |
        v
  External Systems
```

Adapters isolate business logic from provider-specific APIs and prevent unrestricted direct system access.

### Design principles

**Bounded capability surface.** Adapters expose narrow, explicit operations only. Never expose: raw shell execution, raw kubectl passthrough, raw AWS CLI passthrough, arbitrary HTTP proxying, or unrestricted provider SDK access.

**Deterministic behavior.** Adapter operations must be predictable, typed, schema-validated, and safe for workflow execution.

**Auditability.** Every invocation emits audit metadata: caller identity, workflow/execution ID, operation invoked, target resources, timestamp, result/failure state.

**Idempotency.** Where practical, mutating operations must be designed for idempotent retry.

### Common contract requirements

All adapter methods define: operation name, input schema, output schema, side effects, idempotency expectations, retry semantics, authorization requirements.

### Adapter classification

| Classification | Description | Examples |
| --------------- | --------------- | ----------- |
| Read-only | Safe for diagnostic/informational use | `getDeploymentStatus()`, `listPods()`, `getPipelineStatus()` |
| Controlled mutation | Requires policy checks, optional approval | `mergePullRequest()`, `syncApplication()`, `triggerPipeline()` |
| Privileged mutation | Requires strict approval/workflow governance | Production deploy triggers, rollback operations, infrastructure provisioning |

*Amended 2026-10-08 (ADR-019, ADR-022, ADR-023):* three examples above are operations GORA never
has. `mergePullRequest()`: GORA never merges; the code host's auto-merge merges a GORA pull request
once its gate is met (L15; ADR-013, ADR-023 section 8). `syncApplication()` and production deploy
triggers: GORA writes desired state to Git and never writes to a cluster or deploys (ADR-019 section
6). `triggerPipeline()`: GORA never builds (L7; ADR-022 section 3). A rollback is a pull request,
not an adapter call (ADR-022 section 10). The adapters below are amended accordingly.

### Defined adapters

#### GitHub Adapter

Operations: `createPullRequest()`, `updatePullRequest()`, `mergePullRequest()`, `closePullRequest()`, `createIssue()`, `commentOnIssue()`, `getPullRequestStatus()`, `getChangedFiles()`, `fetchFileContent()`, `commitGeneratedContent()`

Use cases: promotion PR generation, manifest updates, release metadata commits.

*Amended 2026-10-08 (ADR-022, ADR-023):* `mergePullRequest()` is removed (L15). The operations the
release model needs are narrower and named in ADR-022 and ADR-023: read files and changed paths at a
commit, commit files on a `gora/*` branch and delete that branch, open, update and close a pull
request, enable the host's auto-merge on it, comment on it, post GORA's commit status, read reviews
and commit statuses, read team membership, read the repository's rules and settings, and create an
annotated tag. The release model needs no issue operation.

#### ArgoCD Adapter

Operations: `getApplicationStatus()`, `getSyncStatus()`, `syncApplication()`, `refreshApplication()`, `waitForHealthy()`, `getDeploymentHistory()`

Use cases: promotion monitoring, deployment verification, post-deploy health validation.

*Amended 2026-10-08 (ADR-019 section 6, ADR-024 section 7):* the Argo CD adapter is read-only:
`syncApplication()` and `refreshApplication()` are removed, since GORA never writes to a cluster and
Argo CD reconciles from Git (ADR-019 section 6). What it reads is shown in `gora status`; it is
never a verification, which is a commit status (ADR-024 section 7).

#### Kubernetes Adapter

Operations: `getDeploymentStatus()`, `listPods()`, `getPodLogs()`, `getRecentEvents()`, `getResourceHealthSummary()`, `describeWorkload()`

Restrictions: initially read-only unless explicitly expanded.

Use cases: incident triage, rollout diagnostics, health summarization.

*Amended 2026-10-08 (ADR-019 section 6):* the Kubernetes adapter is read-only, with no expansion:
GORA never writes to a cluster.

#### CI/CD Adapter

Operations: `triggerPipeline()`, `getPipelineStatus()`, `getBuildArtifacts()`, `getBuildMetadata()`, `retryPipeline()`, `cancelPipeline()`

Providers: Jenkins, Tekton, GitHub Actions (abstracted behind common interface).

*Amended 2026-10-08 (ADR-022 section 3, ADR-023 section 2):* the CI/CD adapter is read-only:
`triggerPipeline()`, `retryPipeline()` and `cancelPipeline()` are removed, since GORA never builds
(L7) and a project's CI hands GORA its digest (`gora register`, ADR-023 section 2).

#### AWS Adapter

Operations: `lookupResource()`, `getCloudWatchMetrics()`, `fetchLogInsights()`, `triggerRunbook()`, `getResourceHealth()`, `fetchTagMetadata()`

Restrictions: no arbitrary infrastructure mutation without explicit approval model.

*Amended 2026-10-08 (ADR-019 section 6):* the AWS adapter is read-only: `triggerRunbook()` is
removed, since GORA never writes to infrastructure, and the incident workflows only read. The
registry adapter of ADR-018 section 7 (read a manifest by digest, write it under a new tag, never
new content) is GORA's other bounded write.

*Amended 2026-10-09 (ADR-027 section 2):* the registry adapter also attaches GORA's signed
verification summaries to a digest, as OCI referrers: content of GORA's own, never an image or a
tag.

### Adapter registration

Adapters register through a capability registry:

```yaml
adapters:
  github:
    capabilities:
      - createPullRequest
      - mergePullRequest
  argocd:
    capabilities:
      - getApplicationStatus
      - syncApplication
```

*Amended 2026-10-08 (ADR-006 notes above):* the example names two removed operations; a registry
lists only the operations that remain.

This enables policy enforcement and future dynamic capability discovery.

## Consequences

- No workflow or agent may bypass the adapter layer to reach external systems
- New external integrations require a new adapter with explicit contract definition
- Adapter contracts serve as the security boundary for all GORA operations
- The adapter registry becomes the source of truth for what GORA can do

## Amendments

### 2026-10-08 — no merge, no cluster write, no pipeline trigger

The release model (ADR-019, ADR-022, ADR-023) removes operations this ADR listed: GORA never
merges (L15), never writes to a cluster (ADR-019 section 6), never builds or triggers a build
(L7). The GitHub adapter keeps the narrow operations the model names; the Argo CD and CI/CD
adapters are read-only, and what they read is never a verification, which is a commit status
(ADR-024 section 7); the registry adapter of ADR-018 retags a digest and writes nothing new.

### 2026-10-09 — the registry adapter attaches GORA's verification summaries

ADR-027 records the outcome of a stage's verifications as a signed verification summary on the
digest. For this ADR: the registry adapter attaches GORA's summaries as referrers of a digest, a
write of GORA's own content; "writes nothing new" in the amendment of 2026-10-08 reads with that
exception.

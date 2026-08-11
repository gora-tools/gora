# ADR-006: Tool Adapter Contracts

**Status:** Accepted
**Date:** 2026-04-11

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

### Defined adapters

#### GitHub Adapter

Operations: `createPullRequest()`, `updatePullRequest()`, `mergePullRequest()`, `closePullRequest()`, `createIssue()`, `commentOnIssue()`, `getPullRequestStatus()`, `getChangedFiles()`, `fetchFileContent()`, `commitGeneratedContent()`

Use cases: promotion PR generation, manifest updates, release metadata commits.

#### ArgoCD Adapter

Operations: `getApplicationStatus()`, `getSyncStatus()`, `syncApplication()`, `refreshApplication()`, `waitForHealthy()`, `getDeploymentHistory()`

Use cases: promotion monitoring, deployment verification, post-deploy health validation.

#### Kubernetes Adapter

Operations: `getDeploymentStatus()`, `listPods()`, `getPodLogs()`, `getRecentEvents()`, `getResourceHealthSummary()`, `describeWorkload()`

Restrictions: initially read-only unless explicitly expanded.

Use cases: incident triage, rollout diagnostics, health summarization.

#### CI/CD Adapter

Operations: `triggerPipeline()`, `getPipelineStatus()`, `getBuildArtifacts()`, `getBuildMetadata()`, `retryPipeline()`, `cancelPipeline()`

Providers: Jenkins, Tekton, GitHub Actions (abstracted behind common interface).

#### AWS Adapter

Operations: `lookupResource()`, `getCloudWatchMetrics()`, `fetchLogInsights()`, `triggerRunbook()`, `getResourceHealth()`, `fetchTagMetadata()`

Restrictions: no arbitrary infrastructure mutation without explicit approval model.

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

This enables policy enforcement and future dynamic capability discovery.

## Consequences

- No workflow or agent may bypass the adapter layer to reach external systems
- New external integrations require a new adapter with explicit contract definition
- Adapter contracts serve as the security boundary for all GORA operations
- The adapter registry becomes the source of truth for what GORA can do

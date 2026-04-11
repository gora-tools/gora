# ADR-003: Release Contract Model

**Status:** Accepted
**Date:** 2026-04-11

---

## Context

Without a deterministic release contract, teams lose clarity on what artifact is being tested, what is eligible for promotion, whether final release rebuilt the artifact, and what rollback actually means.

GORA needs a contract that makes release state explicit, Git-native, and auditable.

## Decision

### Release manifest

The release manifest is the authoritative contract between artifact production and deployment reconciliation. It lives in Git and defines:

- The active release line
- The current release candidate (RC)
- The deployed digest per environment/stage
- Promotion and rollback history (via Git log)

#### Manifest example (v0.1)

```yaml
release: 1.2.0

candidate:
  version: 1.2.0-rc.3
  image: ghcr.io/org/service
  digest: sha256:abc123

stages:
  dev:
    version: 1.2.0-rc.3
    digest: sha256:abc123
  prod:
    version: 1.1.9
    digest: sha256:def456
```

#### Manifest rules

- Candidate MUST map 1:1 to an immutable artifact digest
- Same digest MUST be promoted across stages (no rebuild)
- Final promotion MUST reuse the validated candidate digest
- Stages represent deployed state, not intended future state
- Manifest MUST remain Git-managed and auditable

### Release candidate (RC) model

An RC is the current testable candidate for release. It is not a branch state, not a build number — it is a concrete, immutable artifact selected for validation.

#### RC rules

- Only one active RC exists at a time per release line
- RC MUST map 1:1 to an immutable artifact digest
- A new RC supersedes the previous active RC
- RC validation and final promotion MUST refer to the same digest
- RC is a promotion identity, not just a build identity

#### RC lifecycle

```
1. Build artifact    — CI produces immutable artifact, publishes to registry
2. Cut RC            — GORA selects specific digest, records as current candidate
3. Validate RC       — QA/testing evaluates that exact candidate
4. Replace RC        — If fixes needed, new candidate cut (prior RC superseded)
5. Promote to final  — Same digest promoted, no rebuild, no mutation
```

#### Build identity vs release identity

These are separate concerns:

| Type | Purpose | Examples |
|------|---------|---------|
| Build identity | CI traceability | `dev-a1b2c3d`, `build-1042` |
| Release identity | Promotion and release control | `1.2.0-rc.3`, `1.2.0` |

A build may exist without becoming an RC. An RC must always point to a real immutable build output.

### v0.1 non-goals

The first version intentionally does not define: multi-service release bundles, approval metadata, policy rules, rollout strategy details, deployment health signals, Kargo-specific or ArgoCD-specific fields. These are layered on later without changing the core contract.

## Consequences

- The manifest schema must be extensible (adding fields must not break existing contracts)
- All GORA operations (cut-rc, promote, rollback) operate on this contract
- CD systems consume the manifest — they do not define release state
- The contract is the single source of truth for "what is released where"

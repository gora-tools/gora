# ADR-001: Vision and Positioning

**Status:** Accepted
**Date:** 2026-04-11

---

## Context

Modern delivery pipelines have build systems (CI) and deployment systems (CD), but no authoritative release contract between them.

Release state is fragmented across pipelines, scripts, wiki pages, and human communication. Teams lack clarity on what artifact is being tested, what is eligible for promotion, how promotion is controlled, and how rollback is performed safely.

Existing tools address pieces of this problem. Kargo handles Kubernetes-native promotion. ArgoCD reconciles Git to cluster state. Tekton runs pipelines. But no tool owns the full release lifecycle as a deterministic, platform-agnostic mechanism.

## Decision

GORA is a deterministic GitOps Release Engine.

It manages the full release lifecycle from candidate creation through promotion, verification, and rollback. Policy-driven. Git-backed. Platform-agnostic. Self-releasing.

### Core positioning

GORA sits between CI and CD as the release control layer:

```txt
CI produces immutable artifacts
    -> GORA defines release state in Git
        -> CD deploys from that state
```

### What GORA is

- A release contract and promotion engine
- A mechanism that supports feature-based delivery patterns
- The "brain" that knows what to promote, when, by what policy, with what guarantees
- Platform-agnostic: works with Jenkins, Tekton, ArgoCD, Kargo, Argo Rollouts, and others

### What GORA is not

- A build engine (not a replacement for Tekton, Jenkins, GitHub Actions)
- A cluster reconciler (not a replacement for ArgoCD)
- A runtime traffic router (not a replacement for Argo Rollouts)
- An implementation of any specific organization's delivery model

### Self-releasing requirement

GORA must manage its own release pipeline. If GORA cannot release itself, the model is incomplete. This is an architectural requirement, not an aspiration.

### Target scale

From single-repo projects with one environment to multi-cluster enterprise SaaS platforms with complex promotion graphs.

## Consequences

- All architectural decisions must preserve release-engine-first positioning
- Platform scope expansion is additive only, built on top of the release control core
- External tools (Kargo, ArgoCD, Tekton, Kyverno) are "friends" — possible adapters/backends, not competitors
- GORA does not own application-level concerns like feature flags; it operates at the infrastructure/artifact level
- The release contract data model must be extensible from day one to support both simple and complex topologies

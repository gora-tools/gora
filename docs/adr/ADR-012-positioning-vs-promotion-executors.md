# ADR-012: Positioning vs Promotion Executors (Kargo / Argo)

**Status:** Proposed
**Date:** 2026-07-07

---

## Context

GORA's promotion overlaps with existing promotion executors — most closely **Kargo**
(Akuity). Any technical evaluator will ask "why not just use Kargo?". This ADR fixes the
boundary so the answer is deterministic and, more importantly, so the codebase does not
drift into re-implementing Kargo.

## Background — what Kargo is

A Kubernetes-native continuous-promotion platform. Its domain model is expressed as
Kubernetes CRDs:

- **Warehouse** — subscribes to artifact sources (images, Git, Helm charts), stages them.
- **Freight** — a bundle of versioned artifact references, promoted as a single unit.
- **Stage** — pipeline stages, chained into a delivery graph.
- **Promotion** — a request to move Freight into a Stage, with promotion steps.
- **Project** — the grouping.

Kargo requires a Kubernetes cluster and integrates with Argo CD. Its API surface is
Kubernetes CRDs (`kubectl` / GitOps).

## Decision

### Boundary

GORA is a **lightweight, cluster-optional, Git-native release-control layer**. It is *not* a
Kubernetes-native promotion executor and does not compete on Kargo's turf. Two supported
relationships, kept explicitly distinct:

- **GORA above executors** — GORA owns the release contract and intent (*what / why*);
  Kargo / Argo CD execute the promotion (*how*). Complementary.
- **GORA instead of the heavy stack** — for teams without K8s / Argo, GORA is the whole
  release brain, no cluster required.

### What GORA deliberately does NOT do (to stay distinct)

- No Kubernetes CRD API or in-cluster controllers as the primary model. GORA's contract is a
  plain Git manifest, operable with CLI + Git alone.
- No Freight / Warehouse machinery. GORA's manifest is intentionally simpler — one digest per
  artifact (v0.1), digest-only. The minimalism is the product, not a gap.
- No cluster requirement. GORA works with GitHub Actions + a VM.

### Differentiators to preserve

- Release identity = **digest**, immutable (ADR-003).
- Deterministic engine + bounded adapters (ADR-002, ADR-006) — Kargo / Argo CD are things
  GORA can *drive as adapters*, never dependencies GORA is built on.
- Cluster-optional, few dependencies.

## Consequences

- The manifest and CLI must remain fully usable with **zero Kubernetes**. Any K8s
  integration is an adapter (ADR-006), never a core requirement.
- Resist feature-parity with Kargo. The v0.2 BOM enrichment (ADR-003) absorbs *concepts*
  from prior art but keeps the manifest digest-only and thin.
- When GORA runs alongside Argo / Kargo, GORA emits desired state (the manifest) and the
  executor reconciles — no double-ownership of promotion state.
- Market / product framing lives outside the repo (private strategy note); this ADR captures
  only the **technical** boundary.
- The planned Project Intelligence / ADR Sync ADR becomes **ADR-014**.

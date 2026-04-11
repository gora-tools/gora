# ADR-002: Architectural Principles

**Status:** Accepted
**Date:** 2026-04-11

---

## Context

GORA integrates with external systems (Git, registries, deployment controllers, cloud providers) and may include AI-assisted workflows. Without explicit architectural constraints, systems like this degrade into prompt-driven spaghetti with uncontrolled blast radius.

## Decision

The following principles are non-negotiable and govern all GORA design decisions.

### Release state

- Git is the source of truth for release state
- OCI registry is the source of artifact identity
- Release is a state, not an event
- Artifacts are immutable; promotion moves the same digest

### Execution model

- Deterministic code owns workflow sequencing
- LLMs interpret intent but do not control privileged execution
- No unrestricted model-to-system execution is permitted

### Interface boundaries

- Slack is interface only, never source of truth
- Git / metadata repositories remain authoritative state
- External integrations occur only through bounded tool adapters
- Adapters expose narrow, explicit operations only
- Never expose raw shell, raw kubectl, raw AWS CLI, arbitrary HTTP proxying, or unrestricted provider SDK access

### Governance

- Mutating operations require policy enforcement and approval where appropriate
- Every adapter invocation must be attributable and auditable
- Privileged actions must pass policy enforcement before execution

### Evolution

- Favor pragmatic phased implementation over premature overengineering
- Do not introduce orchestration complexity (Temporal) before justified by workflow requirements
- Prefer a well-structured monorepo with a single primary service for MVP
- Expand platform scope only as additive evolution on top of the release control core

## Consequences

- Every new adapter, workflow, or integration must be evaluated against these principles
- Violations are design bugs, not tradeoffs
- These principles apply regardless of who is implementing (human or AI agent)

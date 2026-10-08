# ADR-010: Agent Architecture

**Status:** Accepted; implementation pending
**Date:** 2026-04-11
**Amended:** 2026-10-08 — the agent coordinates no change to infrastructure (correction)
**Amended:** 2026-10-08 — accepted (status)

---

## Context

GORA may include AI-assisted workflows for intent interpretation, release summarization, and operational diagnostics. Without explicit boundaries, AI integration degrades into "prompt-driven infrastructure" — unreliable, unauditable, and dangerous.

## Decision

### Strategic principle

GORA uses AI as an assistant layer, not as the execution engine. The agent interprets intent and proposes actions; deterministic workflow code executes the important parts.

### Agent role boundaries

**Agent responsibilities:**

- Intent classification (parsing user commands into workflow types)
- Parameter extraction (identifying service, version, target environment from natural language)
- Summarization (release notes, deployment status, incident context)
- Drafting (PR descriptions, ADR text, runbook entries)
- Risk assessment (flagging potential issues in promotion candidates)

**Agent must NOT:**

- Own workflow state
- Make promotion decisions autonomously
- Execute privileged operations directly
- Bypass the adapter layer
- Retry or compensate on behalf of failed workflows

### Architecture

```text
Slack / CLI / API
    -> Agent Layer (intent parsing, routing, formatting)
        -> Workflow Engine (deterministic execution)
            -> Tool Adapters (bounded external access)
                -> External Systems
```

The agent layer is optional. All workflows must be executable without AI involvement (via CLI or API with explicit parameters).

### Agent evolution phases

**Phase 1 (MVP):** No custom agent. Intent routing is deterministic (CLI commands, Slack slash commands map directly to workflows).

**Phase 2:** Lightweight intent parsing. Natural language Slack messages mapped to known workflow types. Parameter extraction from unstructured input.

**Phase 3:** AI-assisted diagnostics and summarization. Release risk analysis, incident context aggregation, deployment health interpretation.

### Guardrails

1. Slack is interface only, never source of truth
2. Git / metadata repos remain authoritative state
3. LLM interprets intent; deterministic code executes workflows
4. Models never receive unrestricted infrastructure access
5. All mutating operations require policy enforcement
6. Tool adapters expose bounded capabilities only

### Long-term vision

GORA agent evolves toward an engineering operations assistant capable of: release promotion assistance, deployment governance, incident diagnostics, infrastructure workflow coordination, operational knowledge capture.

*Amended 2026-10-08 (correction, ADR-001):* GORA stays a release engine; the agent reads
infrastructure for diagnosis and coordinates no change to it (the workflow catalog).

This evolution is additive. The deterministic release engine remains the core. The agent is an interface enhancement, not a replacement for explicit workflow logic.

## Consequences

- MVP has no AI dependency — all operations work through CLI and explicit commands
- Agent capabilities are layered on incrementally, validated by usage data
- The agent layer never becomes a required path for release operations
- AI integration is always bounded by the same adapter contracts as any other caller

## Amendments

### 2026-10-08 — the agent coordinates no change to infrastructure

A correction: the long-term vision named "infrastructure workflow coordination"; GORA stays a
release engine (ADR-001), and the workflow catalog has no workflow that writes to
infrastructure. The agent reads infrastructure for diagnosis only.

### 2026-10-08 — accepted

The decision is accepted: the release model and the ADRs after it build on it. Its status was
Proposed.

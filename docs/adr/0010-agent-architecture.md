# ADR-010: Agent Architecture

**Status:** Proposed
**Date:** 2026-04-11

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

This evolution is additive. The deterministic release engine remains the core. The agent is an interface enhancement, not a replacement for explicit workflow logic.

## Consequences

- MVP has no AI dependency — all operations work through CLI and explicit commands
- Agent capabilities are layered on incrementally, validated by usage data
- The agent layer never becomes a required path for release operations
- AI integration is always bounded by the same adapter contracts as any other caller

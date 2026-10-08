# ADR-007: Workflow Execution Model

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-07-07 — the durable-execution engine is not pre-committed
**Amended:** 2026-10-04 — a wait held by the code host is not a GORA wait state (ADR-023)
**Amended:** 2026-10-08 — "Temporal" reads as the durable engine of phase 3 (correction)

---

## Context

GORA executes workflows ranging from simple PR generation to complex multi-stage release orchestration with approvals. The execution model must start simple and evolve without architectural breakage.

## Decision

### Phased execution model

GORA evolves through three execution phases. Each phase is a superset of the previous.

#### Phase 1: Native validation

Validate target workflows using existing tooling (GitHub Copilot, Slack integrations, Claude) before custom platform buildout. Record recurring workflow candidates and refine the catalog based on observed demand.

Exit criteria: top workflows validated, native tooling limitations documented.

#### Phase 2: Synchronous service execution

Custom GORA agent with synchronous workflow execution. FastAPI service handles requests end-to-end within a single request lifecycle.

Suitable for workflows that: complete in seconds or minutes, require no long-lived state, have no pause/resume semantics, require no approval wait states.

#### Phase 3: Durable orchestration

Introduced only when workflows require: approval wait states, long-running execution, retry/resume/compensation semantics, multi-stage orchestration over time.

**Engine choice is not pre-committed.** Temporal is the reference durable-execution engine, but its self-hosted footprint (Cassandra/Postgres + Elasticsearch + a server cluster) works against GORA's lightweight, few-dependencies ethos (its core needs no cluster, ADR-019). When durability is finally justified, evaluate lighter Postgres-based options (e.g. DBOS, Hatchet, Restate) **before** Temporal. The dispatch layer abstracts the execution backend, so the choice stays bounded (specifics re-checked at decision time — these tools move fast).

### Decision rule for Temporal adoption

**Temporal NOT required when:**

- Workflow completes within minutes
- No long-lived state required
- No human approval wait states
- No retry/resume semantics needed
- Output is a Git artifact only (PR, commit, comment)

**Temporal REQUIRED when:**

- Workflow spans long-running operations
- Human approval required mid-workflow
- Retry/compensation/resume logic needed
- Workflow coordinates multiple external systems over time

*Amended 2026-10-04 (ADR-023 section 8):* a wait that the code host holds, as an open pull
request waiting for its reviews, is not a wait state inside GORA. A run that ends once it has
opened the pull request, and a new run started by the review event or by the periodic
reconciliation, keep no long-lived state of their own; GORA's stage loop is a series of such
runs and stays within phase 2. Nor is the stage loop multi-stage orchestration over time in the
sense of phase 3: each run reads the current state from Git and the code host, acts once and
ends; the sequence lives in Git and on the host, not in a workflow GORA keeps.

### Workflow classification

| Type | Characteristics | Execution |
| --- | --- | --- |
| Synchronous | Completes in seconds/minutes, no wait states, no approval | Direct service execution |
| Durable | Long-running, multi-stage, approval waits, retry/resume | Temporal-backed |

*Amended 2026-10-08 (correction):* "Temporal" in the decision rule and in this table reads as
the durable-execution engine chosen when phase 3 is justified, which "Engine choice is not
pre-committed" above leaves open between Temporal and lighter options.

### LLM role in workflows

The LLM assists but does not control workflow execution:

**LLM responsibilities:** intent classification, summarization, argument extraction, drafting PR text, generating release notes, suggesting remediation.

**LLM must NOT:** track state, manage retries, handle approvals, decide whether a step already ran, perform raw privileged actions.

Deterministic code owns the workflow sequencing. The LLM interprets intent and proposes actions; coded workflow logic executes.

## Consequences

- MVP workflows are all synchronous (see ADR-009 for specific list)
- Workflow implementations must be structured so synchronous workflows can later migrate to Temporal activities without rewrite
- The workflow dispatch layer must abstract execution backend (sync vs Temporal)
- Temporal adoption requires a formal go/no-go architecture decision based on real usage data

## Amendments

### 2026-07-07 — the durable-execution engine is not pre-committed

Phase 3 gains the paragraph "Engine choice is not pre-committed": Temporal is the reference, and
lighter Postgres-based engines are evaluated first when durability is justified.

### 2026-10-04 — a wait held by the code host is not a GORA wait state

ADR-023 decides the stage loop. The decision rule is amended in place: an approval that the
code host holds on an open pull request is not "human approval required mid-workflow"; a
level-triggered loop that recomputes from Git and the host on every event stays within phase 2;
nor is the loop multi-stage orchestration over time, since the sequence lives in Git and on the
host.

### 2026-10-08 — "Temporal" reads as the durable engine of phase 3

A correction: the decision rule and the classification table name Temporal; they read as the
engine chosen when phase 3 is justified, as the paragraph of 2026-07-07 decides.

# ADR-008: Repository Structure

**Status:** Proposed
**Date:** 2026-04-11

---

## Context

GORA is a Python/FastAPI project (ADR-005). The repository must support MVP development by a small team while remaining extensible for future growth. An earlier draft used Go-style layout (cmd/internal/pkg); this ADR supersedes it with Python conventions.

## Decision

### Design goals

1. Keep core platform boundaries explicit
2. Separate interfaces from workflow logic and adapter logic
3. Support incremental implementation without major refactoring
4. Remain understandable for a single maintainer or small team
5. Provide room for future Temporal-based orchestration

### Repository layout

```
gora/
  README.md
  CLAUDE.md
  Makefile
  pyproject.toml
  .gitignore

  docs/
    adr/                         # see the ADR table in README.md
    reference/
      feature-based-delivery/
    catalog/
      workflow-catalog.md

  src/
    gora/
      __init__.py
      main.py                    # FastAPI app entry point

      config/
        __init__.py
        settings.py

      api/
        __init__.py
        routes.py                # HTTP API handlers

      transport/
        __init__.py
        slack/
          __init__.py
          handlers.py            # Slack Bolt command/event handlers
        cli/
          __init__.py
          commands.py            # CLI commands (Click/Typer)

      agent/
        __init__.py
        intents.py               # Intent parsing, command classification
        routing.py               # Maps requests to workflows
        formatting.py            # Response formatting (Slack/API)

      workflows/
        __init__.py
        promotion_pr/
          __init__.py
          workflow.py
        deployment_verification/
          __init__.py
          workflow.py
        incident_triage/
          __init__.py
          workflow.py
        terraform_pr/
          __init__.py
          workflow.py

      adapters/
        __init__.py
        base.py                  # Base adapter interface
        github/
          __init__.py
          adapter.py
        argocd/
          __init__.py
          adapter.py
        kubernetes/
          __init__.py
          adapter.py
        cicd/
          __init__.py
          adapter.py
        aws/
          __init__.py
          adapter.py

      domain/
        __init__.py
        release/
          __init__.py
          manifest.py            # Manifest model and operations
          candidate.py           # RC model
          promotion.py           # Promotion state machine
          identifiers.py         # Identifier grammar (ADR-003)
          schema.py              # Published JSON Schema, generated from the model
        deployment/
          __init__.py
        incident/
          __init__.py

      services/
        __init__.py
        release.py               # Release operation sequencing (cut-rc/promote/rollback)

      policy/
        __init__.py
        engine.py                # Policy enforcement

      audit/
        __init__.py
        logger.py                # Audit event generation

  contracts/
    schemas/                     # Release contract JSON Schemas (generated, committed)
    examples/                    # Example manifests

  tests/
    unit/
    integration/
    fixtures/

  scripts/
    dev/
    ci/

  deployments/
    docker/
      Dockerfile
    kubernetes/
```

### Service layer

`services/` holds the deterministic sequencing layer between transports and
the domain. A service loads Git-native state (the manifest), applies a domain
operation, persists the result, and emits an audit event. This keeps release
logic out of transports (CLI/API/Slack), so every interface shares one
implementation and one audit path — directly satisfying ADR-002 ("deterministic
code owns workflow sequencing") and ADR-004 (mutating operations must be
auditable). Domain models stay pure (no persistence, no audit); transports stay
thin (parse input, render output).

This layer was added in the 2026-06-05 amendment (see below); the original draft
had transports calling the domain directly, which would have duplicated
sequencing and audit logic across CLI, API, and Slack.

### MVP implementation focus

For MVP, focus implementation in:

- `src/gora/main.py` (FastAPI bootstrap)
- `src/gora/transport/slack/` (Slack Bolt handlers)
- `src/gora/transport/cli/` (CLI commands: status, cut-rc, promote, rollback)
- `src/gora/services/release.py` (release operation sequencing)
- `src/gora/agent/` (intent routing)
- `src/gora/workflows/promotion_pr/`
- `src/gora/adapters/github/`
- `src/gora/domain/release/` (manifest, candidate, promotion models)

The rest can remain minimal placeholders until required.

## Consequences

- This supersedes the earlier Go-style layout from previous planning
- Standard Python packaging with pyproject.toml
- The layout supports future Temporal workers (separate entry point consuming same workflow modules)
- Module boundaries map 1:1 to architectural boundaries from ADR-002

## Amendments

### 2026-06-05 — add `services/` layer

Added `src/gora/services/` as the deterministic sequencing layer between
transports and the domain (see "Service layer" above). Introduced during
Milestone 2 (`cut-rc` implementation) to avoid duplicating load → mutate →
persist → audit logic across CLI, API, and Slack.

### 2026-10-02 — `contracts/schemas` exists

`contracts/schemas/release-manifest.v0.1.schema.json` is the published JSON Schema of the
manifest (ADR-003, "Published contract"). It is generated from the model by
`src/gora/domain/release/schema.py` and committed; a test fails when the two differ. The
identifier grammar it publishes lives in `src/gora/domain/release/identifiers.py`.

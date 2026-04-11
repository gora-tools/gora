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
  .env.example
  .gitignore

  docs/
    adr/
      ADR-001-vision-and-positioning.md
      ADR-002-architectural-principles.md
      ADR-003-release-contract-model.md
      ADR-004-promotion-and-rollback-model.md
      ADR-005-technology-stack.md
      ADR-006-tool-adapter-contracts.md
      ADR-007-workflow-execution-model.md
      ADR-008-repository-structure.md
      ADR-009-mvp-scope-and-milestones.md
      ADR-010-agent-architecture.md
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
        deployment/
          __init__.py
        incident/
          __init__.py

      policy/
        __init__.py
        engine.py                # Policy enforcement

      audit/
        __init__.py
        logger.py                # Audit event generation

  contracts/
    schemas/                     # Release contract JSON/YAML schemas
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

### MVP implementation focus

For MVP, focus implementation in:

- `src/gora/main.py` (FastAPI bootstrap)
- `src/gora/transport/slack/` (Slack Bolt handlers)
- `src/gora/transport/cli/` (CLI commands: status, cut-rc, promote, rollback)
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

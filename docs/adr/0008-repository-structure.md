# ADR-008: Repository Structure

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-06-05 — `services/` layer
**Amended:** 2026-10-02 — `contracts/schemas` exists
**Amended:** 2026-10-03 — `docs/architecture/`
**Amended:** 2026-10-08 — the layout is the repository as it is
**Amended:** 2026-10-08 — no room kept for Temporal workers (ADR-007)
**Amended:** 2026-10-08 — accepted (status)
**Amended:** 2026-10-09 — the release files (ADR-026)
**Amended:** 2026-10-13 — the release model loads through a rule
**Amended:** 2026-10-20 — `deployments/docker/debian.sources` and `debian.lock` (ADR-011)
**Amended:** 2026-10-22 — `tests/fixtures/` holds a git history too (correction)

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

*Amended 2026-10-08 (ADR-007):* durable orchestration is not planned, so goal 5 and "future
Temporal workers" in Consequences carry no design.

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
    architecture/
      release-model.md           # the map of the release model and its laws (ADR-022)
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

*Amended 2026-10-08 (correction):* the tree above is the plan of 2026-04; the tree below is the
repository as it is and replaces it; packages marked "placeholder" hold only `__init__.py` until
their milestone. A change that adds or moves a top-level directory or a package updates this tree
in the same pull request.

```
gora/
  README.md                      # entry point: what GORA is, the ADR index
  CLAUDE.md                      # rules for agents working in this repository
  TESTING.md                     # test policy
  LICENSE, NOTICE                # Apache-2.0 and its notice; the image carries both
  SECURITY.md, CONTRIBUTING.md   # reporting a vulnerability; contributing
  Makefile                       # every gate target (CLAUDE.md, "Verify")
  pyproject.toml, uv.lock, .python-version
  release-policy.yaml            # GORA's own release policy (ADR-026)
  artifacts/gora/artifact.yaml   # GORA's own artifact declaration (ADR-026)

  .github/
    CODEOWNERS                   # the owners of the policy and the declarations (ADR-026)
    workflows/                   # ci.yml, gate-change.yml, pr-title.yml, release.yml
    actions/                     # build-image, assemble-index: steps CI and release share
    dependabot.yml

  docs/
    adr/                         # decisions; the index is in README.md
    architecture/
      release-model.md           # the map of the release model and its laws
    catalog/
      workflow-catalog.md
    reference/                   # positioning and prior art; never a decision
    diagrams/                    # diagrams an ADR links to

  src/gora/
    main.py                      # FastAPI app entry point
    build_info.py                # revision and version the image was built from
    text.py                      # safe text for terminals and Slack
    config/settings.py
    api/routes.py                # HTTP API: /healthz
    transport/
      cli/commands.py            # CLI (Typer)
      slack/                     # placeholder: incoming Slack commands
    agent/                       # placeholder: intent routing (ADR-010)
    workflows/                   # placeholders: deployment_verification, incident_triage
    adapters/
      github/adapter.py          # bounded GitHub adapter (ADR-006, ADR-013)
      git/adapter.py             # read-only reads of a local clone (ADR-006)
      slack/adapter.py           # bounded Slack adapter (ADR-015)
      argocd/, kubernetes/, cicd/, aws/   # placeholders
    domain/
      release/                   # the identifier grammar; versions; candidates; change log; rc tags
      release_repository/        # release files: YAML 1.2 loader, canonical writer, models, schemas
      evidence/                  # provenance, a CI signer's certificate, the build level
      notification.py
      deployment/, incident/     # placeholders
    services/                    # notifier.py
    policy/                      # placeholder
    audit/                       # logger.py, sink.py: the audit journal (ADR-017)

  contracts/
    schemas/v1alpha1/            # published JSON Schemas (generated, committed)
    examples/                    # example release files, also test fixtures

  tests/
    unit/
    integration/
    fixtures/                    # recorded evidence and a git history, never fetched (TESTING 6)

  scripts/
    gate/                        # the scripts the gates run (ADR-016, ADR-021)

  deployments/docker/
    Dockerfile                   # the release image; the exact Python and uv
    gate-images.Dockerfile       # the images the gates run in
    debian.sources               # the Debian snapshot the release image installs from
    debian.lock                  # the hashes of that snapshot's Release files
```

`state/` and `rejections/` are not in the tree: they appear with GORA's first write
(ADR-026 section 16).

*Amended 2026-10-09 (ADR-026 sections 1, 11 and 16):* when ADR-026 is implemented, the tree gains
`release-policy.yaml`, `artifacts/gora/artifact.yaml`, `state/`, `rejections/`,
`.github/CODEOWNERS` and `contracts/schemas/v1alpha1/`, and loses `release-manifest.yaml` and the
v0.1 schema, in the pull request that makes the change, as the rule above asks.

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

*Amended 2026-10-08 (correction):* this was the focus of 2026-04. The order of work is kept in the
backlog (CLAUDE.md, "Notes"), and the commands are those of ADR-023.

## Consequences

- This supersedes the earlier Go-style layout from previous planning
- Standard Python packaging with pyproject.toml
- The layout supports future Temporal workers (separate entry point consuming same workflow modules)

  *Amended 2026-10-08 (ADR-007):* durable orchestration is not planned; no room is kept for
  Temporal workers.
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

### 2026-10-03 — `docs/architecture/`

`docs/architecture/release-model.md` is the map of the release model: vocabulary, diagrams and
the laws with their enforcement points. ADR-022 is the decision it follows; `CLAUDE.md` loads it
first.

*Amended 2026-10-13 (PR 93):* `CLAUDE.md` no longer loads it at session start; the path-scoped
rule `.claude/rules/release-model.md` has an agent read it before changing a file under `src/`,
`tests/`, `docs/adr/` or `docs/architecture/`.

### 2026-10-08 — the layout is the repository as it is

The layout section shows the repository as it is on 2026-10-08, with placeholders marked, and
a change that adds or moves a top-level directory or a package updates it in the same pull
request. The plan of 2026-04 named files that were never written (`agent/intents.py`,
`policy/engine.py`, `workflows/*/workflow.py`) and missed files that exist. "MVP implementation
focus" gains a note: the order of work is kept in the backlog.

### 2026-10-08 — no room kept for Temporal workers

Goal 5 and the Consequences line on Temporal workers gain a note: durable orchestration is not
planned (ADR-007).

### 2026-10-08 — accepted

The decision is accepted: the release model and the ADRs after it build on it. Its status was
Proposed.

### 2026-10-09 — the release files

ADR-026 decides the files of the release repository. For this ADR: the tree records the release
files and the published schemas of ADR-026 when they exist, and loses `release-manifest.yaml`
and the v0.1 schema then.

### 2026-10-13 — the release model loads through a rule

Since PR 93 the release model is not read at session start. `CLAUDE.md` names it without
importing it, and the path-scoped rule `.claude/rules/release-model.md` has an agent read it in
full before changing a file under `src/`, `tests/`, `docs/adr/` or `docs/architecture/`. The
2026-10-03 entry carries the note.

### 2026-10-20 — `deployments/docker/debian.sources` and `debian.lock`

The Debian snapshot the release image installs its packages from, and the hashes that pin
it (ADR-011), sit beside the Dockerfile; the tree shows them.

### 2026-10-22 — `tests/fixtures/` holds a git history too

A correction. The tree said that `tests/fixtures/` holds recorded evidence; it holds a git
history as well (`tests/fixtures/git/gora-main.bundle`). The tree shows it.

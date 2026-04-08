# Roadmap

## Status

GORA is currently in the design and foundation stage.

The immediate goal is to lock release semantics and build a usable core before expanding into integrations or UI.

## Phase 0 — Foundation

Goal: define the project clearly before writing code.

Deliverables:

- vision
- architecture
- manifest specification
- RC model
- promotion model
- working model
- repository structure

Success criteria:

- the project has a stable conceptual model
- core terminology is fixed
- MVP boundaries are explicit

## Phase 1 - Core CLI

Goal: implement the minimal release engine.

Initial commands:

- `gora status`
- `gora cut-rc`
- `gora promote`
- `gora rollback`

Deliverables:

- manifest parser
- manifest validator
- digest-aware RC creation
- stage promotion logic
- rollback logic

Success criteria:

- a single-service release flow can be controlled through Git-defined manifest state

## Phase 2 — Dogfooding / Demo

Goal: make GORA release and demonstrate itself using a small example setup.

Deliverables:

- example manifests
- demo service or static preview
- basic pipeline integration
- visible stage progression

Success criteria:

- GORA can demonstrate deterministic candidate selection and promotion on a real example

## Phase 3 — GitOps Integration

Goal: integrate the core engine into a real GitOps workflow.

Potential integrations:

- Jenkins
- GitHub Actions
- Argo CD
- Argo Rollouts

Success criteria:

- manifest updates drive deployment behavior through a GitOps reconciler

## Phase 4 — Promotion Orchestration

Goal: add richer release progression semantics across environments.

Potential direction:

- Kargo integration
- policy-aware promotion
- approval gates
- multi-stage release flows

Success criteria:

- promotion becomes controller-driven rather than purely manual or script-based

## Phase 5 — Public Core

Goal: prepare GORA for public open-source release.

Deliverables:

- cleaned public docs
- sanitized examples
- stable CLI surface
- contributor guidance

Success criteria:

- the project is understandable and useful outside the original private context

## Phase 6 — SaaS Wrapper

Goal: build a hosted control plane around the core.

Potential capabilities:

- visualization
- history and audit UX
- release dashboards
- multi-tenant promotion control
- policy management

Success criteria:

- hosted value is additive, while core release semantics remain self-hostable

## Current Priorities

1. Lock documentation and working model.
2. Finalize manifest shape and CLI boundaries.
3. Make the first initialization commit.
4. Start the minimal CLI implementation.

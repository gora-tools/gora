# Working Model

## What GORA Is

GORA is a GitOps-native release contract and promotion engine.

It defines release state between CI and CD and makes candidate selection, promotion, and rollback deterministic.

## What GORA Is Not

GORA is not:

- a source build engine
- a CI runner
- a deployment reconciler
- a runtime rollout controller
- a general-purpose workflow engine

## Current Operating Assumptions

- Git is the source of release state.
- OCI registry is the source of artifact identity.
- A release candidate maps 1:1 to an immutable digest.
- Only one active candidate exists at a time.
- Final release is a promotion of the same validated digest.
- Promotion is represented as Git state.
- Rollback restores a known prior state.

## MVP Boundaries

The initial MVP should stay narrow:

- single-service focus
- manifest-driven state
- CLI-first
- no UI required
- no multi-cluster support yet
- no policy engine yet
- no full Kargo integration yet

## Architecture Direction

Near-term:

- Jenkins or GitHub Actions can produce artifacts
- GORA updates or validates release state
- Argo CD can deploy from Git-defined manifest state

Long-term:

- Tekton for build pipelines
- Kargo for promotion orchestration
- Argo CD for reconciliation
- Argo Rollouts for progressive delivery
- optional SaaS wrapper for visibility and management

## Release Model

- Build identity is for traceability.
- Release identity is for promotion.
- Promotion moves the same digest.
- RC is the current testable candidate.
- Final is a retag or identity transition of the same digest.

## Strategic Intent

The project should evolve from private design work to a self-hostable open-source core, and later to a hosted wrapper if the model proves strong enough.

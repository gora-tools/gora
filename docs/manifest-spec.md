# Manifest Specification (v0.1)

## Purpose

Defines the release state of the platform.

The manifest is the contract between artifact production and deployment reconciliation.

CI produces immutable artifacts.
GORA defines which artifact is the current release candidate and how it progresses.
CD deploys from the manifest-defined state.

## Goals

The manifest must make the following explicit:

- what release line is currently active
- which artifact is the current release candidate
- which digest is deployed in each stage
- how promotion and rollback are represented in Git

## Example

```yaml
release: 1.2.0

candidate:
  version: 1.2.0-rc.3
  image: ghcr.io/org/service
  digest: sha256:abc123

stages:
  dev:
    version: 1.2.0-rc.3
    digest: sha256:abc123

  prod:
    version: 1.1.9
    digest: sha256:def456
```

## Rules

- The candidate MUST map 1:1 to an immutable artifact digest.
- The same digest MUST be promoted across stages.
- Final promotion MUST reuse the same digest that was validated as the release candidate.
- Stages MUST represent deployed state, not intended future state.
- Rollback MUST point to a previously known manifest state.
- A new release candidate supersedes the previous active candidate.
- The manifest MUST remain Git-managed and auditable.

Non-Goals (v0.1)

## This specification does not yet define

- multi-service release bundles
- approval metadata
- policy rules
- rollout strategy details
- deployment health signals
- Kargo-specific or Argo-specific fields

Those can be layered on later without changing the core contract.

## Design Notes

The first version of the manifest is intentionally small.

The goal of v0.1 is not to model every operational concern.
The goal is to establish a deterministic, Git-native release contract with minimal ambiguity.

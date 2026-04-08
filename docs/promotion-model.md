# Promotion Model (v0.1)

## Purpose

Defines how artifacts move from build output to release candidate to final release.

Promotion in GORA is linear, digest-based, and deterministic.

## Core Idea

Promotion does not create a new artifact.

Promotion moves the same immutable artifact through release stages.

That is the foundation.

## Release Flow

```text
build -> candidate -> validated candidate -> final
```

Or, expressed by identity

```text
build identity -> rc identity -> final identity
```

And, expressed by artifact behavior

```text
same digest -> same digest -> same digest
```

## Model

### Build Phase

CI builds and publishes the artifact.

Examples of build identity:

- dev-\<shortsha\>
- branch-\<shortsha\>
- build-\<pipeline-id\>

This identity is useful for traceability, but it is not yet a release decision.

### Candidate Selection

GORA selects a specific artifact digest as the current release candidate.

Example:

- build: dev-a1b2c3d
- digest: sha256:abc123
- RC version: 1.2.0-rc.3

The candidate is written to the manifest.

### Stage Promotion

The selected candidate moves through environments by updating the manifest-defined stage state.

Example:

```text
dev -> qa -> preprod -> prod
```

Each stage continues to point to the same digest until a new candidate supersedes it.

### Final Promotion

If validation passes, final release reuses the same digest.

Example:

- 1.2.0-rc.3 -> 1.2.0
- digest remains sha256:abc123

This is a retag or release identity transition, not a rebuild.

## Promotion Rules

- Promotion MUST preserve digest identity.
- Final release MUST reuse the validated candidate digest.
- Stage progression MUST remain visible in Git state.
- Promotion MUST be auditable.
- Rollback MUST restore a known prior manifest state.
- A failed candidate MUST be replaced, not silently mutated.

## Linear Release Principle

GORA assumes a linear release promotion model.

That means:

- one active candidate
- one forward promotion path
- explicit supersession of prior candidates
- no ambiguous parallel candidate state in the same release line

This keeps the model understandable and operationally safe.

## Rollback Model

Rollback is not a rebuild.

Rollback means restoring a prior known release state.

Operationally, this can be represented as:

- manifest revert
- stage pointer revert
- digest reassignment to a known good prior state

The rollback target must already be known and auditable.

## Promotion vs Deployment

These are related but distinct.

Promotion

A release control decision.
Moves a selected artifact into the next approved release state.

Deployment

The act of reconciling runtime state to the promoted Git-defined target.

GORA controls promotion.
CD systems execute deployment.

## Summary

Promotion in GORA is:

- artifact-preserving
- linear
- Git-defined
- digest-based
- auditable

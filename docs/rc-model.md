# RC Model (v0.1)

## Definition

A Release Candidate (RC) is the current testable candidate for release.

An RC must map 1:1 to an immutable artifact digest.

It is not a vague label.
It is not just a branch state.
It is not just a build number.

It is a concrete, testable artifact selected for validation.

## Core Rules

- Only one active RC exists at a time.
- An RC MUST map 1:1 to an immutable artifact digest.
- A new RC supersedes the previous active RC.
- The currently active RC MUST be represented in the manifest.
- RC validation and final promotion MUST refer to the same digest.
- RC is a promotion identity, not just a build identity.

## Why This Matters

Without a strict RC model, teams lose clarity around:

- what QA is actually testing
- what is currently eligible for release
- whether final promotion rebuilt the artifact
- what rollback actually means

A usable RC model must eliminate ambiguity.

## RC Lifecycle

### 1. Build Artifact

CI produces an immutable artifact and publishes it.

Example:

- image tag for traceability: `dev-<shortsha>`
- real artifact identity: `sha256:<digest>`

### 2. Cut RC

GORA selects a specific artifact digest and records it as the current candidate.

Example:

- `1.2.0-rc.3` -> `sha256:abc123`

### 3. Validate RC

QA and preproduction testing evaluate that exact candidate.

### 4. Replace RC if Needed

If fixes are required, a new candidate is cut.

Example:

- `1.2.0-rc.4` -> `sha256:def456`

At that point, the prior RC is no longer the active candidate.

### 5. Promote to Final

If the RC passes, final release is a promotion of the same digest.

No rebuild.
No mutation.
No ambiguity.

## RC vs Build Identity

These must remain separate.

### Build Identity

Used for traceability of a CI output.

Examples:

- `dev-a1b2c3d`
- `build-1042`
- `branch-shortsha`

### Release Identity

Used for promotion and human-readable release control.

Examples:

- `1.2.0-rc.3`
- `1.2.0`

A build may exist without becoming an RC.
An RC must always point to a real immutable build output.

## RC and Branches

A release branch can be one source of candidate selection, but branch history is not enough by itself.

The critical object is still:

- RC version
- immutable digest
- manifest pointer to the current candidate

The release contract cannot depend on tribal interpretation of branch history.

## Summary

An RC is the current release candidate selected for validation.

It must be:

- unique
- explicit
- immutable
- traceable
- promotable without rebuild

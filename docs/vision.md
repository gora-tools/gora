# GORA — GitOps Release Automation

Deterministic promotion. Immutable artifacts. Policy-driven delivery.

## Problem

Modern delivery pipelines lack a deterministic release contract.

CI builds artifacts.
CD deploys them.
But there is no authoritative system that defines:

- what exactly is being released
- which artifact is the current candidate
- how promotion between environments is controlled
- how rollback is performed deterministically

Release state is fragmented across:

- CI pipelines
- deployment jobs
- manual processes
- human communication

## Solution

GORA introduces a Git-native release contract.

- Artifacts are immutable (digest-based)
- Release state is defined in Git (manifest)
- Promotion is controlled, linear, and deterministic
- Deployment systems consume the manifest, not pipelines

CI produces artifacts.
GORA defines release state.
CD deploys from that state.

## Core Idea

GORA is to infrastructure what feature flags are to application delivery.

It separates:

- deployment from release
- build from promotion
- artifact creation from release control

## Principles

- Git is the source of truth
- Artifacts are immutable
- Promotion moves the same artifact
- Only one active candidate exists
- Release is a state, not an event

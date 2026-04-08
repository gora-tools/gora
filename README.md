# GORA

GitOps Release Automation

Deterministic promotion. Immutable artifacts. Policy-driven delivery.

## What GORA Is

GORA is a Git-native release control layer for modern delivery systems.

It sits between CI and CD:

- CI produces immutable artifacts
- GORA defines release state in Git
- CD deploys from that state

The goal is to make release promotion explicit, linear, and deterministic.

## Problem

Modern delivery pipelines often have build systems and deployment systems, but no authoritative release contract.

That creates ambiguity around:

- what artifact is actually being tested
- what artifact is currently deployable
- how promotion between environments is controlled
- how rollback is performed safely

Release state becomes fragmented across pipelines, scripts, wiki pages, and human communication.

## Core Idea

GORA treats release state as a first-class Git object.

Artifacts are immutable.
Release candidates are explicit.
Promotion moves the same digest forward.
Final release reuses the validated candidate digest.

## Principles

- Git is the source of release state
- Artifacts are immutable
- Promotion moves the same artifact
- Only one active candidate exists
- Release is a state, not an event

## Scope

GORA is not a CI engine and not a CD reconciler.

It is the release contract and promotion layer between them.

It can work with systems such as:

- Jenkins
- Tekton
- Argo CD
- Argo Rollouts
- Kargo

## Repository Layout

```text
gora/
  README.md
  git-structure.txt

  docs/
    vision.md
    architecture.md
    manifest-spec.md
    rc-model.md
    promotion-model.md
    roadmap.md
    working-model.md

  contracts/
    release/
    schemas/
    examples/

  tools/
    cli/

  examples/
    manifests/

  sandbox/

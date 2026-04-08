# Architecture

## Overview

GORA sits between CI and CD as a release contract and promotion control layer.

The architectural idea is simple:

- CI produces immutable artifacts
- GORA defines release state in Git
- CD deploys from that Git-defined state

GORA is not a build engine.
GORA is not a cluster reconciler.
GORA is the release control layer between them.

## Problem in Traditional Delivery

Most delivery systems already have CI and CD, but they lack a deterministic release contract.

The result is fragmented release state across:

- pipelines
- deployment jobs
- scripts
- manual requests
- wiki pages
- human communication

This creates ambiguity around:

- what exactly is being tested
- what exactly is deployed
- what artifact is eligible for promotion
- what can be rolled back safely

## GORA Positioning

GORA defines the platform release state in Git.

That state becomes the contract for deployment systems.

Conceptually:

```text
CI -> immutable artifact -> GORA manifest in Git -> CD deploys from manifest

Not
CI -> pipeline logic -> deployment side effects -> Git somewhere on the side
```

## Legacy-Compatible Model

The initial operational path can support existing delivery systems

```text
Jenkins -> build/publish artifact -> update manifest -> Argo CD deploys
```

This allows teams to adopt GORA without rewriting everything at once

## Target Model

The target model is a fully GitOps-native promotion flow

```text
Tekton or Jenkins -> artifact publish -> release manifest -> Kargo promotion -> Argo CD -> Argo Rollouts
```

Where:
  • CI publishes artifacts
  • GORA defines release candidate and release state
  • Kargo manages promotion between stages
  • Argo CD reconciles Git to cluster state
  • Argo Rollouts provides progressive delivery behavior inside the cluster

## Core Components

### Artifact Registry

Stores immutable build outputs.

Examples:
  • OCI images
  • chart packages

The digest is the real identity of the artifact.

## Git Repository

Stores the release contract.

This is where the manifest lives and where promotion is represented as state transitions.

## GORA CLI / Engine

Responsible for:
  • cutting release candidates
  • promoting candidates between stages
  • performing deterministic rollback
  • validating manifest integrity

### CD Reconciler

Argo CD or equivalent reads desired state from Git and reconciles the cluster.

### Progressive Delivery Layer

Argo Rollouts or equivalent controls canary, blue/green, or staged rollout behavior once a version has already been selected for deployment.

## Architectural Principles

  • Git is the source of release state
  • Registry is the source of artifact identity
  • Promotion moves the same digest
  • Release is stateful and explicit
  • Deployment is downstream of release state
  • Rollback is Git-native and auditable

## What GORA Is Not

GORA is not:

  • a source-code build system
  • a replacement for Tekton, Jenkins, or GitHub Actions
  • a replacement for Argo CD
  • a general workflow engine
  • a runtime traffic router

It can integrate with those systems, but its purpose is narrower and more precise.

## Evolution Path

Phase 1

Docs and release contract definition.

Phase 2

CLI-based candidate and promotion engine.

Phase 3

Dogfooding via example manifests and demo environments.

Phase 4

Kargo and Argo integration.

Phase 5

Public core and later SaaS wrapper.

## Summary

GORA is a release control plane.

It brings explicit, deterministic promotion semantics into a GitOps delivery model by making release state a first-class object in Git.

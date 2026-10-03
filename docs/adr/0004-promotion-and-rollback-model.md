# ADR-004: Promotion and Rollback Model

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-10-03 — sign-off gate, supersession, rollback kinds (ADR-022)

---

## Context

Promotion is the core operation in GORA. Without a strict model, promotion degrades into ad-hoc pipeline logic where artifacts get rebuilt, digests change silently, and rollback becomes tribal knowledge.

## Decision

### Promotion model

Promotion does not create a new artifact. Promotion moves the same immutable artifact through release stages. That is the foundation.

#### Release flow

```text
build -> candidate -> validated candidate -> final
```

Expressed by artifact behavior:

```text
same digest -> same digest -> same digest
```

#### Stage promotion

The selected candidate moves through environments by updating the manifest-defined stage state:

```text
dev -> staging -> preprod -> prod
```

Each stage points to the same digest until a new candidate supersedes it. The promotion graph is configurable — simple linear pipelines for small projects, complex DAGs for multi-cluster enterprise setups.

#### Final promotion

If validation passes, final release reuses the same digest:

```text
1.2.0-rc.3 -> 1.2.0
digest remains sha256:abc123
```

This is a retag or release identity transition, not a rebuild.

*Amended 2026-10-03 (ADR-022):* the final promotion is the pull request across the **sign-off
gate**, a gate set in policy. Stages before it carry the rc version, stages after it the final
version, on the same digest.

#### Promotion rules

- Promotion MUST preserve digest identity
- Final release MUST reuse the validated candidate digest
- Stage progression MUST remain visible in Git state
- Promotion MUST be auditable
- A failed candidate MUST be replaced, not silently mutated

### Linear release principle

GORA assumes a linear release promotion model per release line:

- One active candidate
- One forward promotion path
- Explicit supersession of prior candidates
- No ambiguous parallel candidate state in the same release line

*Amended 2026-10-03 (ADR-022):* every merge that changes an artifact's inputs is a candidate, so
"one active candidate" is held by supersession: only the newest non-rejected candidate of an
artifact passes the sign-off gate, and a superseded or rejected candidate never moves forward.
GORA registers candidates in the first-parent order of `main` (ADR-022 sections 6 and 8).

### Rollback model

Rollback is an artifact-level infrastructure operation. It restores a prior known release state by pointing to a previously validated digest.

#### Rollback mechanisms

- Manifest revert (Git revert to known good state)
- Stage pointer revert (specific stage reverted to previous digest)
- Digest reassignment to known good prior state

*Amended 2026-10-03 (ADR-022):* these three are replaced by two kinds, both a promotion pull
request forward to a digest the stage held before and that is not rejected. Kind A returns one
artifact; kind B returns every application of a stage to a named platform state
`prod/YYYY.MM.DD.N`. Migrations never roll back. GORA computes the target and opens the pull
request; it does not `git revert` the manifest (ADR-022 section 10).

#### Rollback rules

- Rollback is not a rebuild
- Rollback target must already be known and auditable
- Rollback is represented as Git state change (auditable)
- Rollback does not require redeployment from source — it promotes a known good digest

#### Relationship to feature-level rollback

GORA operates at the artifact/infrastructure level. Application-level rollback (e.g., feature flag disable) is a separate concern owned by the application layer. GORA may integrate with feature flag systems as an adapter, but does not own feature-level enablement or rollback. Both mechanisms are valid and complementary; they operate at different layers.

### Promotion vs deployment

These are related but distinct:

| Concept | Definition | Owner |
| --------- | ----------- | ------- |
| Promotion | A release control decision. Moves a selected artifact into the next approved release state. | GORA |
| Deployment | The act of reconciling runtime state to the promoted Git-defined target. | CD system (ArgoCD, etc.) |

GORA controls promotion. CD systems execute deployment.

## Consequences

- Promotion graph topology must be configurable (not hardcoded to a specific pipeline)
- All rollback operations go through Git, making them auditable by default
- The state machine must track: candidate → promoted-to-stage → final-release, and rollback at any point
- Feature flags and application-level rollback are explicitly out of GORA's scope

## Amendments

### 2026-10-03 — sign-off gate, supersession, rollback kinds

ADR-022 decides the lifecycle on trunk. For this ADR: the final promotion is the sign-off gate;
"one active candidate" is held by supersession, newest non-rejected only; rollback is kind A (one
artifact) or kind B (a platform state), always a forward promotion, never a rewrite of history.

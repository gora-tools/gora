# GORA vs Feature-Based Delivery

GORA takes the best of Feature-Based Delivery but **rejects one claim**:
that seasonal / feature releases fully replace a reproducible platform release state.

## What we keep from Feature-Based Delivery

- A **feature** is the primary unit of customer value (may span repos/services,
  deployed and enabled independently, controlled by flags).
- **Deployment ≠ release.** Deployment makes software *available*; release makes
  behavior *available*.
- `main` stays promotable (L4); there are no release branches (L1).
- Each change is its own unit through the stages: GORA offers every version the stage before
  verified to every stage a person approves, in order, and never skips one there by itself (ADR-024
  section 4), so the version that carries a feature is tested and promoted on its own.

## Where we diverge

Feature-Based Delivery optimizes for *feature release*. It does not fully solve *platform
release*. Large systems still need rollback boundaries, reproducible states, auditability,
DR points, and promotion governance. They do not need freezes or release trains: GORA has
neither (ADR-022 section 12); its reproducible states are the platform states of
[`docs/architecture/release-model.md`](../architecture/release-model.md).

|           | Feature World                                    | Platform World (GORA)                               |
|-----------|--------------------------------------------------|-----------------------------------------------------|
| Unit      | Feature / flag                                   | Release manifest (digest-pinned; a Release BOM)     |
| Lifecycle | Not Released → Releasing → Released → Deprecated | Candidate `X.Y.Z-rc` → promotions → `X.Y.Z` in `prod` → state |
| Rollback  | Disable a flag                                   | Return a stage to what it held before (L18)         |
| Owned by  | LaunchDarkly / OpenFeature / Unleash             | GORA                                                |

**Key insight: `Feature Release ≠ Release manifest`.** A single feature can be deployed,
enabled, and released on its own — but at that same moment the system as a whole *is* a
specific composition: runtime and dependency versions, infrastructure and config versions,
and the combined state of every other feature. The feature is one variable; the release
manifest pins the full state.

## Two safety strategies

- **Feature thinking:** turn off a feature flag.
- **Release-state thinking:** return `prod` to what it held before: one artifact, or a whole
  platform state `prod/YYYY.MM.DD.N`, to digests that are not rejected (L18).

This is the old RC argument: if `1.4.3-rc` is rejected, `1.4.2-rc`, which a stage already
verified, can still move on and become final as that exact digest (ADR-004, ADR-024). That is
release-state thinking, not feature thinking.

A seasonal/feature release is a *reporting* artifact; the release manifest is an
*operational* artifact. They solve different problems, so one cannot replace the other.

## The bridge

The **release manifest** is the contract linking the two worlds: features move
independently, but at any moment the manifest pins the exact composition — the state you can
promote, roll back, and audit. Feature flags and application-level rollback stay an
application concern (ADR-004); GORA operates at the artifact / release level.

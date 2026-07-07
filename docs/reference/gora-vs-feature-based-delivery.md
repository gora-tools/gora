# GORA vs Feature-Based Delivery

GORA takes the best of Feature-Based Delivery but **rejects one claim**:
that seasonal / feature releases fully replace a reproducible platform release state.

## What we keep from Feature-Based Delivery

- A **feature** is the primary unit of customer value (may span repos/services,
  deployed and enabled independently, controlled by flags).
- **Deployment ≠ release.** Deployment makes software *available*; release makes
  behavior *available*.
- `main` stays releasable; long-lived release branches fade.

## Where we diverge

Feature-Based Delivery optimizes for *feature release*. It does not fully solve *platform
release*. Large systems still need rollback boundaries, reproducible states, auditability,
DR points, freeze points, and promotion governance.

|           | Feature World                                    | Platform World (GORA)                               |
|-----------|--------------------------------------------------|-----------------------------------------------------|
| Unit      | Feature / flag                                   | Release manifest (digest-pinned; a Release BOM)     |
| Lifecycle | Not Released → Releasing → Released → Deprecated | Candidate → RC → Promotion → Released → Archive      |
| Rollback  | Disable a flag                                   | Return the whole release to a verified digest       |
| Owned by  | LaunchDarkly / OpenFeature / Unleash             | GORA                                                |

**Key insight: `Feature Release ≠ Release manifest`.** A single feature can be deployed,
enabled, and released on its own — but at that same moment the system as a whole *is* a
specific composition: runtime and dependency versions, infrastructure and config versions,
and the combined state of every other feature. The feature is one variable; the release
manifest pins the full state.

## Two safety strategies

- **Feature thinking:** turn off a feature flag.
- **Release-state thinking:** roll the whole release back to a previously verified digest.

This is the old RC argument: if `rc.3` is bad, take `rc.2` and promote *that* exact verified
digest to final (ADR-004). That is release-state thinking, not feature thinking.

A seasonal/feature release is a *reporting* artifact; the release manifest is an
*operational* artifact. They solve different problems, so one cannot replace the other.

## The bridge

The **release manifest** is the contract linking the two worlds: features move
independently, but at any moment the manifest pins the exact composition — the state you can
promote, roll back, and audit. Feature flags and application-level rollback stay an
application concern (ADR-004); GORA operates at the artifact / release level.

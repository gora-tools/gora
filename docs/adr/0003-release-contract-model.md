# ADR-003: Release Contract Model

**Status:** Accepted
**Date:** 2026-04-11

---

## Context

Without a deterministic release contract, teams lose clarity on what artifact is being tested, what is eligible for promotion, whether final release rebuilt the artifact, and what rollback actually means.

GORA needs a contract that makes release state explicit, Git-native, and auditable.

## Decision

### Release manifest

The release manifest is the authoritative contract between artifact production and deployment reconciliation. It lives in Git and defines:

- The active release line
- The current release candidate (RC)
- The deployed digest per environment/stage
- Promotion and rollback history (via Git log)

#### Manifest example (v0.1)

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

#### Manifest rules

- Candidate MUST map 1:1 to an immutable artifact digest
- Same digest MUST be promoted across stages (no rebuild)
- Final promotion MUST reuse the validated candidate digest
- Stages represent deployed state, not intended future state
- Manifest MUST remain Git-managed and auditable

### Release candidate (RC) model

An RC is the current testable candidate for release. It is not a branch state, not a build number — it is a concrete, immutable artifact selected for validation.

#### RC rules

- Only one active RC exists at a time per release line
- RC MUST map 1:1 to an immutable artifact digest
- A new RC supersedes the previous active RC
- RC validation and final promotion MUST refer to the same digest
- RC is a promotion identity, not just a build identity

#### RC lifecycle

```text
1. Build artifact    — CI produces immutable artifact, publishes to registry
2. Cut RC            — GORA selects specific digest, records as current candidate
3. Validate RC       — QA/testing evaluates that exact candidate
4. Replace RC        — If fixes needed, new candidate cut (prior RC superseded)
5. Promote to final  — Same digest promoted, no rebuild, no mutation
```

#### Build identity vs release identity

These are separate concerns:

| Type | Purpose | Examples |
| ---- | ------- | -------- |
| Build identity | CI traceability | `dev-a1b2c3d`, `build-1042` |
| Release identity | Promotion and release control | `1.2.0-rc.3`, `1.2.0` |

A build may exist without becoming an RC. An RC must always point to a real immutable build output.

### v0.1 scope and non-goals

v0.1 is a single-artifact manifest. It covers one release line, one candidate, and stage progression for one service. This is sufficient for GORA self-release and single-service projects.

v0.1 intentionally does not define: multi-service release composition, approval metadata, policy rules, rollout strategy details, deployment health signals, SBOM references, Kargo-specific or ArgoCD-specific fields.

### Schema evolution: v0.1 → v0.2 (Release BOM)

The manifest evolves from single-artifact contract to Release BOM (Bill of Materials) — a multi-artifact manifest that describes the complete release composition of a system.

#### v0.1 — Single artifact (GORA self-release, single-service projects)

```yaml
version: v0.1
release: "0.1.0"

candidate:
  version: "0.1.0-rc.1"
  image: "ghcr.io/zuwarskejes-labs/gora"
  digest: "sha256:abc123..."

stages:
  dev:
    version: "0.1.0-rc.1"
    digest: "sha256:abc123..."
```

#### v0.2 — Release BOM (cally-platform, multi-service systems)

```yaml
version: v0.2
system: "cally-platform"
release: "2026.Q2"

artifacts:
  cally-api:
    image: "ghcr.io/zuwarskejes-labs/cally-api"
    candidate:
      version: "1.3.0-rc.2"
      digest: "sha256:abc123..."
    stages:
      dev:
        digest: "sha256:abc123..."
      prod:
        digest: "sha256:old456..."

  cally-worker:
    image: "ghcr.io/zuwarskejes-labs/cally-worker"
    candidate:
      version: "2.1.0-rc.1"
      digest: "sha256:def789..."
    stages:
      dev:
        digest: "sha256:def789..."
      prod:
        digest: "sha256:old012..."
```

#### BOM design principles

- Each artifact in BOM has its own RC, stages, and promotion path
- Promotion can be per-artifact (promote only cally-api) or coordinated (promote api + worker as release unit)
- BOM is the composition layer; individual artifact contracts follow the same rules as v0.1
- `version` field in manifest root identifies schema version — parsers must check this first

#### Prior art and v0.2 enrichment

The Release BOM is a clean re-architecture of a production-proven multi-service BOM model
(see [`docs/reference/prior-art-bom-poc.md`](../reference/prior-art-bom-poc.md)). GORA's
key improvement over it: **release identity is the digest**, not `version + commit`. When
v0.2 is formalized (Milestone 7), fold in the concepts that model proved: a `status`
lifecycle (`rc → promoting → released`), global + per-artifact **locking**, a hotfix
counter, an explicit **dependency graph** (`dependsOn`) with a shared-library flag,
`artifact_type`, and `promotedBy`. Absorb the concepts, keep the manifest digest-only and
thin.

For v0.2 also consider **two explicit version planes**: the platform/BOM version owned by
GORA in **CalVer** (a platform is a release train, not an API — e.g. `2026.3.0`, RC suffix
`-rc.N`, digest carried in build metadata `+sha256.…`), with an optional human **codename**
(1:1 alias, never authoritative, never used for ordering); while each component keeps its own
**SemVer**, which the BOM merely records. CalVer sorts deterministically and communicates
*when*, which is what promotion, rollback, and audit need.

*Amended 2026-10-02 (ADR-018):* the digest is **not** carried in build metadata. Build metadata
(`+…`) takes no part in SemVer precedence, so it cannot tell two releases apart, and `+` is not
allowed in an OCI tag. A GORA version is SemVer without build metadata; the digest stays a
separate field. The Release BOM binds to existing standards (in-toto release predicate or
CycloneDX BOM-Link) and is distinct from an SBOM; see ADR-018 section 5.

#### BOM vs SBOM

These are complementary, not competing:

| Concept | What it describes | Owner |
| ------- | ------------------- | ----------------------- |
| Release BOM (GORA) | What artifacts compose a release, at what versions/digests | GORA manifest |
| SBOM (cosign/provenance) | What is inside an artifact — dependencies, licenses, provenance | CI / supply chain tooling |

GORA may store SBOM references as metadata per artifact, but does not generate or own SBOMs.

### Versioning model

GORA follows semantic versioning for its own releases:

- `0.1.0` — foundation, single-artifact manifest, CLI skeleton
- `0.x.y` — pre-1.0 development, breaking changes possible
- `1.0.0` — stable release contract, production-grade CLI and API

Manifest schema version (`v0.1`, `v0.2`) is independent of GORA release version. Schema versions are additive — v0.2 parser must understand v0.1 manifests.

## Consequences

- The manifest schema must be extensible (adding fields must not break existing contracts)
- All GORA operations (cut-rc, promote, rollback) operate on this contract
- CD systems consume the manifest — they do not define release state
- The contract is the single source of truth for "what is released where"
- v0.1 manifest is sufficient for Milestones 1-4 (GORA self-release)
- v0.2 BOM is required when cally-platform integration begins (Milestone 7)
- Schema version field enables forward-compatible parsing

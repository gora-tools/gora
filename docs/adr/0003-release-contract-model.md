# ADR-003: Release Contract Model

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-10-02 — identifier grammar, enforced invariants, published schema
**Amended:** 2026-10-03 — candidates leave the manifest, versions computed, platform states (ADR-022)
**Amended:** 2026-10-08 — one version per candidate (ADR-024); `.lock`, `promotedBy` (ADR-023)
**Amended:** 2026-10-08 — no dependencies between artifacts (ADR-025)

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

*Amended 2026-10-03 (ADR-022):* the manifest no longer records the current candidate. A candidate
lives in its version tag, its registry tag and its verification attestation; the manifest holds
the stages (ADR-022 sections 6 and 9).

*Amended 2026-10-08 (ADR-024 section 1):* nor a release line, which exists in manifest v0.1
only: every candidate has its own version.

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

Digests in the examples of this ADR are shortened for reading; a real one is
`sha256:` and 64 hex digits (see "Identifier grammar").

#### Manifest rules

- Candidate MUST map 1:1 to an immutable artifact digest
- Same digest MUST be promoted across stages (no rebuild)
- Final promotion MUST reuse the validated candidate digest
- Stages represent deployed state, not intended future state
- Manifest MUST remain Git-managed and auditable

How each rule is enforced is listed under "Invariants and their enforcement".

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

*Amended 2026-10-03 (ADR-022):* steps 1 and 2 are one event. A merge to `main` that changes an
artifact's inputs is built once and registered by GORA as a candidate; nobody selects it. Step 4
is supersession: a newer candidate replaces the active one, and only the newest non-rejected
candidate passes the sign-off gate, which is step 5.

*Amended 2026-10-08 (ADR-024 sections 1 to 4):* every candidate has its own version `X.Y.Z-rc`,
computed from the previous candidate's; several are in flight at once and none supersedes
another. Step 4 is a rejection and a new candidate from the next commit; step 5 is the promotion
into `prod`, where the version becomes `X.Y.Z`. "Only one active RC per release line" and "a new
RC supersedes the previous active RC" describe manifest v0.1 and its invariants below.

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

*Amended 2026-10-03 (ADR-022):* this example predates ADR-022. In v0.2 there is no `candidate`
block and no platform `release`: candidates live in tags, and a platform has states, not a
version.

#### BOM design principles

- Each artifact in BOM has its own RC, stages, and promotion path
- Promotion can be per-artifact (promote only cally-api) or coordinated (promote api + worker as release unit)
- BOM is the composition layer; individual artifact contracts follow the same rules as v0.1
- `version` field in manifest root identifies schema version — parsers must check this first

*Amended 2026-10-08 (ADR-024 section 1, ADR-025 section 1):* every artifact is promoted on its
own; "coordinated (promote api + worker as release unit)" is replaced, since GORA has no group
promotion, and each candidate has its own version, not an RC of a line.

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

*Amended 2026-10-03 (ADR-022):* a platform has states, not a version, and it is not a release
train. A state is a manifest commit tagged `prod/YYYY.MM.DD.N` (ADR-022 section 9): no rc
suffix, no codename. The components keep SemVer. Of the prior-art concepts, `dependsOn`,
`artifact_type` and `promotedBy` stay for v0.2, and `dependsOn` also orders a group promotion. The
hotfix counter is not adopted: a hotfix is an ordinary candidate. Nor is the lock: one open
promotion pull request per artifact and stage and a `main` tested as it lands replace it, and a
lock that holds changes back is a freeze (ADR-022 section 12). The `status` lifecycle
`rc → promoting → released` becomes rc version, promotion pull request, final version.

*Amended 2026-10-08 (ADR-025 section 1):* `dependsOn` is not kept: GORA has no dependencies
between artifacts, and each artifact is promoted on its own. Nor does `artifact_type` give GORA
a rule: GORA has no kinds of artifact.

*Amended 2026-10-08 (ADR-023 section 6, ADR-024 section 1):* `promotedBy` is not written to Git,
since GORA writes no personal data there; who asked and who approved stay in the code host's
records and the audit journal. The lifecycle reads `X.Y.Z-rc`, promotion pull requests, and
`X.Y.Z` on entering `prod`.

#### BOM vs SBOM

These are complementary, not competing:

| Concept | What it describes | Owner |
| ------- | ------------------- | ----------------------- |
| Release BOM (GORA) | What artifacts compose a release, at what versions/digests | GORA manifest |
| SBOM (cosign/provenance) | What is inside an artifact — dependencies, licenses, provenance | CI / supply chain tooling |

GORA may store SBOM references as metadata per artifact, but does not generate or own SBOMs.

### Identifier grammar

*Added 2026-10-02.* Every identifier that enters GORA, as a manifest field or as a CLI
argument, is checked against one grammar, defined once in `src/gora/domain/release/identifiers.py`.
A manifest field that breaks it refuses the load (`ManifestLoadError`, naming the field path and
the rule, never the value). A CLI argument that breaks it is a usage error: exit 2, the
parameter, the value shown escaped and the rule, before the audit journal is opened
(ADR-017), so nothing is written or sent.

| Identifier | Grammar | Source |
| --- | --- | --- |
| version | SemVer 2.0.0: `MAJOR.MINOR.PATCH`, optional `-PRERELEASE`, numeric identifiers without leading zeros; **no build metadata**; **not ending in `.lock`**; at most 128 characters | semver.org 2.0.0 (its grammar, with `\d` read as `[0-9]`); 128 is the OCI tag length; git check-ref-format, because a version is also the tag `v<version>` and the end of the branch `gora/promote-<stage>-<version>` (ADR-013) |
| release line | `MAJOR.MINOR.PATCH`, the SemVer core | semver.org 2.0.0 |
| stage | `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$` | RFC 1123 label, letter first: a stage name is also part of a branch name, a YAML key and often a namespace |
| digest | `^sha256:[a-f0-9]{64}$` | OCI image-spec digest, sha256, lowercase hex |
| image | a repository name: an explicit registry host (one with a dot or a port, or `localhost`), then lowercase path components separated as in the reference grammar; no tag, no digest; at most 255 characters | distribution/reference grammar and its `NameTotalLengthMax`; Red Hat guidance on short image names: always fully qualified, never resolved through a search list |
| repository (`--repo`) | `owner/name`: owner `^[A-Za-z0-9][A-Za-z0-9-]{0,38}$`, name `^[A-Za-z0-9._-]{1,100}$` and not `.` or `..`, exactly one slash | GitHub naming limits |
| base branch (`--base`) | `^[A-Za-z0-9._/-]{1,255}$`; not `HEAD`, no leading `-`, no `..`, no leading, trailing or doubled `/`, no trailing `.`, no component that starts with `.` or ends in `.lock` | git check-ref-format `--branch` |

**Why no build metadata.** SemVer rule 10: build metadata MUST be ignored when determining
precedence, so two versions that differ only in it are the same version to every tool that
orders releases; it cannot tell two releases apart. The OCI distribution tag grammar,
`[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}`, has no `+`, and a GORA version becomes an image tag.
Every version the grammar accepts is a valid tag (test). The digest is its own field
(amended by ADR-018 above).

**Why a version never changes content.** SemVer rule 3: once a version is released, its
contents MUST NOT be modified; a change is a new version. Repositories hold the same line:
Sonatype Nexus with redeploy disabled refuses a second upload of a version, JFrog Release
Bundles v2 answer 409 to an existing version, Harbor tag immutability keeps a tag on its
digest. GORA states it as invariant a) below.

**Why not `.lock`.** A GORA version does not stay inside the manifest: the release workflow
tags the commit `v<version>`, and a promotion pull request lives on the branch
`gora/promote-<stage>-<version>`. git refuses a ref, and so either of these, whose last
component ends in `.lock`. The version grammar is therefore bounded by git's ref grammar for
the same reason it is bounded by the OCI tag grammar: it becomes one. Only a final `.lock`
identifier is refused; `1.2.0-lock` and `1.2.0-a.lock.1` are valid versions and valid refs.
Whatever the version, base branch and stage grammars accept, git accepts too: a test runs the
real `git check-ref-format` over their samples, generated variants and every promotion branch
they can form. GORA may be stricter than git, never looser.

*Amended 2026-10-08 (ADR-023 section 6):* the promotion branch is now
`gora/promote/<stage>/<artifact>`, which holds no version; the version still ends the tag
`v<version>` and the rejection branch `gora/reject/<artifact>/<version>`, so the rule stands.

**Portable patterns.** Every pattern is in the common subset of Python `re` and ECMA-262 (no
named groups, no lookbehind, no `\A` or `\Z`, no possessive or atomic groups, no `\d`, `\w`,
`\s`, no inline flags; a lookahead is in both, and the version pattern uses one for `.lock`),
because the published schema hands them to ECMA-262 consumers. GORA checks a value with
`fullmatch` after a length check. One difference stays and is recorded in a test: Python's `$`
also matches before a final newline, so a validator built on Python `re` accepts `dev\n` where
ECMA-262 and GORA refuse it.

Choices this grammar makes that its sources leave open: a registry host keeps the case
distribution allows (`GHCR.io` is valid) and is compared as written, so two spellings of one
host are two images; an IPv6 literal host is not accepted; a `.lock` suffix is refused on any
branch component, as git does, not only at the end; `@` is refused in a branch name by the
character set, although git check-ref-format `--branch` accepts the name `@`: stricter than
git, and harmless for a base branch. No accepted value of any grammar can hold the `:name:`
shape Slack reads as an emoji (test).

### Invariants and their enforcement

*Added 2026-10-02.* Enforcement classes per ADR-016 rule 6. The model (`ReleaseManifest`)
holds the invariants: an operation that would break one raises `ContractViolationError` with a
value-free reason before anything changes, and the service writes, audits and notifies
nothing. The CLI prints the reason on stderr, says the manifest is unchanged, and exits 1.

| Invariant | Enforced by | Class |
| --- | --- | --- |
| Every identifier matches its grammar | the model's field types; CLI parameter callbacks | `runtime-check`, `test` (`tests/unit/test_identifiers.py`, `tests/integration/test_cli_contract.py`, `tests/integration/test_cli_literal_output.py`) |
| What the version, base branch and stage grammars accept, git accepts as a ref | the grammars above | `test` against the real git binary (`tests/integration/test_identifiers_against_git.py`) |
| a) Within a manifest a version maps to one digest, across the candidate and every stage; a digest may carry several versions (`1.2.0-rc.3` and `1.2.0` on one digest is the final-release retag of ADR-004) | `cut-rc` and `rollback` refuse a version recorded with another digest | `runtime-check`, `test` (`tests/unit/test_manifest_contract.py`, `tests/integration/test_cli_contract.py`) |
| Re-cutting the active candidate (same version, digest and image) is not an event | `cut-rc` writes nothing, audits nothing, notifies nothing, exits 0 and says so | `runtime-check`, `test` (same files) |
| b) The candidate's `MAJOR.MINOR.PATCH` is the release line; stages may hold older lines | `cut-rc` refuses another line; a fresh manifest takes its line from the version | `runtime-check`, `test` (same files) |
| c) One image per manifest (v0.1 is a single-artifact contract) | `cut-rc` refuses an image other than the active candidate's | `runtime-check`, `test` (same files) |
| d) A rollback targets an existing stage | `rollback` refuses a stage the manifest does not have | `runtime-check`, `test` (same files) |
| e) A manifest that already breaks a) or b) is not loaded | the model validator; `from_yaml` raises `ManifestLoadError` | `runtime-check`, `test` (same files) |
| Loading invents nothing: an absent `created_at` stays absent; `cut-rc` sets it | `created_at` is optional; load and write are byte-stable across processes | `test` (`tests/unit/test_manifest_contract.py`) |
| Candidate MUST map 1:1 to an immutable artifact digest; RC MUST map 1:1 to a digest | invariant a) and the digest grammar | as a) |
| Same digest MUST be promoted across stages (no rebuild) | `promote` copies the candidate's version and digest | `runtime-check`, `test` (`tests/unit/test_manifest.py`) |
| Only one active RC per release line; a new RC supersedes the previous one | the manifest has one `candidate`, which `cut-rc` replaces; invariant b) | `runtime-check`, `test` (`tests/unit/test_manifest.py`, `tests/unit/test_release_service.py`) |
| Stages represent deployed state; an RC is a promotion identity | GORA records what it promoted and does not observe a cluster | `review-only` |
| Manifest MUST remain Git-managed and auditable | Git is the store; every mutation is in the audit journal (ADR-017) | `review-only` (Git), `test` (journal) |

**Not enforced yet.** Three rules of ADR-003 and ADR-004 have no executable check, and each
needs something GORA does not have; ADR-018 decides them, and they are not implemented in
part:

- *Stage order* (audit E4: `cut-rc` then `promote prod` succeeds) and *final from validated*
  (audit E3: a digest that never passed a lower stage can reach prod; "Final promotion MUST
  reuse the validated candidate digest", "RC validation and final promotion MUST refer to the
  same digest"). They need the promotion graph of `release-policy.yaml`.
- *Rollback to a known digest* (audit E9: a rollback may name a digest the stage never held).
  It needs the manifest's Git history, read through a read-only git adapter.
- For the same reason invariant a) holds within the manifest as it is, not across its
  history: a version that has left the manifest (a superseded candidate never promoted) can be
  cut again with another digest. Closing that needs the same git adapter.

**Moving to a new release line** is not a command. `cut-rc --release` must equal the
manifest's line, and invariant b) refuses a candidate off it, so a new line starts with a
reviewed edit of the manifest that changes `release` and removes or replaces `candidate` in the
same commit; the stages keep their versions.

*Amended 2026-10-03 (ADR-022):* once versions are computed (ADR-022 section 5), the line follows
from the commits and is never edited by hand. This paragraph holds until then.

*Amended 2026-10-08 (ADR-024 section 1):* in the model there is no release line: each
candidate's version follows from the previous candidate's. The release line exists in manifest
v0.1 only.

### Published contract

*Added 2026-10-02.* The JSON Schema of manifest v0.1 is published at
`contracts/schemas/release-manifest.v0.1.schema.json` (draft 2020-12). It is generated from the
model (`python -m gora.domain.release.schema`) and written deterministically: sorted keys,
two-space indent, ASCII, one trailing newline. A test fails when the committed file differs
from the generated one; GORA's own manifests validate against it, and documents GORA refuses
fail it. Its patterns are the grammars above; stage names are checked through
`propertyNames`, and every object refuses unknown fields. Invariants a) and b) relate fields to
each other, which JSON Schema does not express: the schema states them in its description and
GORA enforces them. *Class:* `test` (`tests/unit/test_published_schema.py`).

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

## Amendments

### 2026-10-02 — identifier grammar, enforced invariants, published schema

The identifier grammar, the invariants the manifest enforces and the published JSON Schema are
recorded in place, and the digest is not carried in build metadata (ADR-018).

### 2026-10-03 — candidates leave the manifest; versions computed; platform states

ADR-022 decides the lifecycle on trunk. For this ADR:

- A candidate is a commit on `main` whose inputs differ from those of the artifact's newest
  registered candidate (ADR-022 section 4), built once and registered by GORA: an annotated tag `vX.Y.Z-rc.N` on the source commit, a registry tag on the digest and
  a verification attestation. It is not written to the manifest; the schema version that
  implements ADR-022 has no `candidate` block, and v0.1 manifests stay readable.
- "One active RC per release line" holds as supersession: only the newest non-rejected candidate
  passes the sign-off gate (ADR-022 section 8).
- The line and the rc number are computed from Conventional Commits and existing tags (ADR-022
  section 5); `cut-rc` is replaced by registration once it is implemented, and the invariants
  above that name `cut-rc` move to it.
- A platform has states (`prod/YYYY.MM.DD.N`, CalVer labels), not a version, and no train. The
  manifest's `release` field goes with `candidate`: the line is computed.

### 2026-10-08 — every candidate has its own version

ADR-024 decides the versions. For this ADR: every candidate has its own version `X.Y.Z-rc`,
computed from the previous candidate's, and none supersedes another; the version becomes
`X.Y.Z` when it enters `prod`; the release line and "one active RC per line" belong to manifest
v0.1. The `.lock` rule now rests on the tag and the rejection branch (ADR-023 section 6).
`promotedBy` is not kept in Git (ADR-023 section 6).

### 2026-10-08 — no dependencies between artifacts

ADR-025 decides that GORA has no dependencies between artifacts. For this ADR: `dependsOn` is not
kept for v0.2, and `artifact_type` gives GORA no rule.

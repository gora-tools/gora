# ADR-018: Trust and Promotion Policy

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-02
**Amended:** 2026-10-02 — section 7, release mechanics
**Amended:** 2026-10-03 — evidence for a multi-platform image index (ADR-021)
**Amended:** 2026-10-03 — candidate record, order of promotion, tags on the source commit (ADR-022)
**Amended:** 2026-10-08 — the final version on entering `prod` (ADR-024); who writes (ADR-023)
**Amended:** 2026-10-08 — registration needs registry access and the trust root (correction)
**Amended:** 2026-10-09 — builders, the SBOM floor, the policy digest, the pointer (ADR-026)
**Amended:** 2026-10-09 — SBOM form, summaries, signers, any tool, a computed Release BOM (ADR-027)

---

## Context

ADR-003 and ADR-004 say what a release is: one immutable digest that moves through stages and
is never rebuilt. They do not say when GORA may *trust* a digest, in which order stages may be
entered, what a final release requires, or which digest a rollback may target. Today:

- `cut-rc` and `promote` accept any digest that matches `^sha256:[a-f0-9]{64}$`. Nothing checks
  that the digest was built by GORA's own pipeline, signed, or described by an SBOM.
- `promote` accepts any stage in any order: `cut-rc` then `promote prod` succeeds (audit E4).
  A digest that never passed a lower stage can reach prod (audit E3).
- `rollback` accepts any digest that matches the pattern, including one never seen before
  (audit E9).
- The release pipeline (ADR-011) signs the image with cosign keyless and attaches a syft SPDX
  SBOM as a cosign attestation, but its SLSA provenance comes from BuildKit and is **not
  signed**. Unsigned provenance is SLSA Build L1 at most. The verify job runs after the image
  is pushed, and it matches the signer with a regular expression.

GORA must release itself to production with the same mechanism it offers to others (ADR-001,
ADR-009 M3). That requires a decided trust model before the code that enforces it.

Research behind this ADR (2026-10-02, sources below): SLSA v1.2 Build track and its
verification and VSA specifications; GitHub artifact attestations; Sigstore cosign; the
in-toto attestation predicates; Kargo (Freight verified per stage, rollback to the last
verified Freight); JFrog Release Bundles v2 and Sonatype Nexus (a version never changes
content); CISA SBOM minimum elements; BSI TR-03183-2; Red Hat SBOM guidelines.

## Decision

### 1. The registry holds artifacts; the manifest holds releases

A digest in the registry is not a release. It becomes a release candidate only when GORA has
verified it and recorded it in the manifest. Publishing an image before verification finishes
is therefore harmless: it cannot be promoted. This is how the audit finding "rc.2 and rc.4
published before the verify job finished" is closed, rather than by reordering CI alone.

*Amended 2026-10-03 (ADR-022):* "recorded" means registered. GORA creates the annotated tag
`vX.Y.Z-rc.N` on the source commit, naming the digest, adds the registry tag and attaches the
VSA; the manifest holds the stages only. A digest GORA has not registered still cannot be
promoted. *Amended 2026-10-08 (ADR-024 section 2):* the tags are `vX.Y.Z-rc` and `X.Y.Z-rc`.

### 2. What a digest must carry

Every candidate digest must carry, attached to the digest itself:

| Evidence | Form | Producer |
| --- | --- | --- |
| Signature | Sigstore keyless signature | cosign in the release workflow |
| Provenance | SLSA provenance v1 (`https://slsa.dev/provenance/v1`), signed | GitHub artifact attestations (`actions/attest`), run from a **reusable workflow**, `push-to-registry: true` |
| SBOM | **CycloneDX 1.6 JSON**, one per image, as an in-toto attestation (`https://cyclonedx.org/bom`) | syft in the release workflow |

**Provenance through GitHub artifact attestations from a reusable workflow** gives SLSA Build
L3: the signing identity belongs to the reusable workflow, which the calling build steps cannot
reach. With `push-to-registry` the attestation is stored in the registry next to the digest,
so verification needs the registry and the Sigstore trust root, not the GitHub API.

*Amended 2026-10-09 (ADR-027 section 7):* the producer is any tool that writes SLSA provenance v1 as
a signed in-toto statement from a reusable workflow: `cosign attest`, or `actions/attest`, which a
private repository can use only on GitHub Enterprise Cloud. GORA checks the statement, its predicate
and its signer, never the tool; GORA's own release workflow will use `cosign`. The signature may be
keyless or by a key; a key gives Build L3 only when the build steps cannot reach it (ADR-027
section 7).

**CycloneDX 1.6 is the SBOM format.** BSI TR-03183-2, the technical guideline the EU Cyber
Resilience Act work points to, accepts CycloneDX 1.6 or newer and SPDX 3.0.1 or newer; syft
emits CycloneDX 1.6 and does not emit SPDX 3. One format, not two: the SPDX 2.3 attestation
is replaced, not kept alongside. The SBOM content meets the CISA minimum elements (author,
timestamp, tool name and version, generation context, and per component: name, version,
identifiers, hash, licence, dependency relationships), including transitive dependencies.

*Amended 2026-10-03 (ADR-021):* a candidate is an OCI image index for `linux/amd64` and
`linux/arm64`. The evidence above is required for every digest a node can pull: a signature on
the index and on each platform manifest; provenance for each platform manifest and for the
index; one SBOM per platform manifest. `cut-rc` verifies all of them and refuses the candidate
if any is missing.

*Amended 2026-10-09 (ADR-026 section 4):* the SBOM is CycloneDX 1.6 or newer, the versions
BSI TR-03183-2 accepts. 1.6 is the floor, which a project's policy may raise
(`evidence.sbom.minVersion`) and never lower.

*Amended 2026-10-09 (ADR-027 section 6):* what GORA checks of an SBOM is its form: an attestation
for every platform manifest, signed by one of the artifact's builders, its subject that
manifest's digest, CycloneDX JSON, its `specVersion` at least the policy's floor; GORA reads no
other field. Its content is the project's. The CISA minimum elements above are the
commitment of GORA's own release workflow.

### 3. Who is trusted: the policy file

`release-policy.yaml`, versioned in Git next to the manifest, holds:

- **Trusted signers:** a list of `(OIDC issuer, exact identity)` pairs. For GORA itself:
  issuer `https://token.actions.githubusercontent.com`, identity the exact reusable release
  workflow of `zuwarskejes-labs/gora` at a release tag ref. **No regular expressions.** Another
  CI provider (Bitbucket Pipelines, GitLab) is one more pair; the provenance generator lives in
  that provider's pipeline, the verification in GORA does not change.
- **Required evidence:** the predicate types of section 2, and the provenance fields a verifier
  must check (SLSA verification spec: builder id, source repository, build type; unknown
  external parameters are rejected).
- **Promotion graph:** the ordered stages, e.g. `dev -> staging -> prod`. ADR-004 says the graph
  is configurable; this file is where it is configured.

*Amended 2026-10-03 (ADR-022):* with the build on `main`, the trusted identity is the reusable
release workflow at `refs/heads/main`, not at a tag ref (section 7 note).

*Amended 2026-10-09 (ADR-026 sections 4, 5 and 15):* the trusted signers are the policy's named
`builders`, each an issuer and an exact identity, and each artifact's declaration names the ones
that may sign its candidates. The required evidence, the predicate types of section 2 and the
provenance fields listed above, is the same for every project and is not a policy field, so no
policy can weaken it; the policy holds only the SBOM floor. The promotion graph is the policy's
`stages`. The policy digest is the SHA-256 of the RFC 8785 serialisation of the policy and the
artifact's declaration that decided, as parsed, so comments and layout never change it.

*Amended 2026-10-09 (ADR-027 section 7):* a trusted signer is a Sigstore identity, an issuer and an
exact identity with the instance whose trust root verifies it (the public one, or GitHub's), or a
public key, whose signature needs no transparency log. A private repository need not publish its
name in a public log.

GORA records the **digest of the policy file** with every decision, so a decision can be
replayed against the exact policy that made it.

### 4. When GORA verifies, and what it does with the result

- **At `cut-rc`:** the digest must carry every piece of evidence from section 2, from a trusted
  signer. Anything missing or unverifiable is a refusal (fail-closed), not a warning.
- **At `promote <stage>`:** the digest must be the active candidate, and it must already be in
  the stage directly before `<stage>` in the promotion graph. Evidence is verified again at
  promote time against the current policy; an earlier stage's result is not trusted blindly.
- **Final release** (`1.2.0-rc.3 -> 1.2.0`, ADR-004) is the same digest under its final version,
  allowed only for a digest that reached the last stage before the final one. It is a retag,
  never a rebuild.
- **Rollback** targets only a digest that the manifest's own Git history shows in that stage
  before. GORA reads that history through a read-only Git adapter (ADR-006); the operator does
  not type an arbitrary digest.

*Amended 2026-10-03 (ADR-022):* "at `cut-rc`" becomes registration. At a promotion the digest is
the one the stage before holds; the first stage receives the newest registered candidate. The
"active candidate" condition applies at the **sign-off gate**, a gate set in policy, as the
newest non-rejected candidate of the artifact (ADR-022 section 8); the final promotion is the
pull request across that gate. A rollback also refuses a rejected digest, and is kind A (one
artifact) or kind B (a platform state) (ADR-022 section 10).

*Amended 2026-10-08 (ADR-024 sections 2 and 3):* a promotion takes an eligible version, one the
stage before held and verified; there is no sign-off gate and no newest-only condition. "The
first stage receives the newest registered candidate" is replaced: the first stage takes
registered candidates in its own order. The final promotion is the promotion into `prod`.

Each verification produces a **SLSA Verification Summary Attestation (VSA,
`https://slsa.dev/verification_summary/v1`)**, signed with GORA's own OIDC identity and
attached to the digest as an OCI referrer. The VSA names the verifier, the resource, the policy
(URI and digest), the result and the verified SLSA level. The manifest records only a pointer:
VSA digest, policy digest, result, time. The manifest stays the contract; the VSA is the
portable evidence a CD system or an admission controller (Sigstore policy-controller, Kyverno)
can check without trusting GORA's file.

*Amended 2026-10-09 (ADR-026 section 6):* the pointer is `spec.evidence` of the entry's
StageState file, and every entry has one: GORA writes an entry only after a check, a rollback
included.

*Amended 2026-10-09 (ADR-027 sections 2 and 7):* GORA writes a second kind of verification summary
on a digest, one for each result of a stage's verification it counts: `PASSED` or `FAILED`, its
policy URI naming the manifest commit and, in its fragment, the stage, the artifact and the
verification, with the status it records in an extension field. The code host deletes commit
statuses by age; these summaries keep the results as long as the digest. Both kinds are signed as an
attester the policy names, a Sigstore identity or a key, which "signed with GORA's own OIDC
identity" above reads as.

### 5. Release BOM is not an SBOM

An SBOM says what is inside one artifact. The v0.2 Release BOM (ADR-003) says which artifacts,
at which digests, form one system release. They stay separate documents. When v0.2 is
formalised it binds to existing standards rather than inventing a format: the in-toto release
predicate (one release identity, every artifact digest as a subject) or a CycloneDX BOM whose
components link to each image SBOM through BOM-Link. The Release BOM identity is content-derived
(a hash of its canonical artifact list), as Kargo does for Freight.

*Amended 2026-10-09 (ADR-026 sections 2 and 8):* the composition of `prod` is the `state/prod/`
tree at a platform state; the v0.2 Release BOM is replaced, and the Release BOM built from that
tree is left to ADR-027.

*Amended 2026-10-09 (ADR-027 section 5):* the Release BOM is computed, never stored:
`gora bom` prints the composition of a stage at a commit, for `prod` at a platform state, as a
CycloneDX 1.6 JSON document whose identity is the SHA-256 of its RFC 8785 serialisation, which
replaces "a hash of its canonical artifact list" above. CycloneDX is the one format; the in-toto
release predicate is not used.

### 6. Version identity

A GORA version is SemVer 2.0 **without build metadata** and at most 128 characters. Build
metadata does not take part in SemVer precedence, so it cannot tell two releases apart, and
`+` is not allowed in an OCI tag, which a GORA version becomes. The grammar itself is decided
in ADR-003.

### 7. Release mechanics: one build per candidate, the final is a retag

*Added 2026-10-02.* The release workflow (ADR-011) builds an image on a pushed `v*` tag. Today
that includes a final tag such as `v0.2.0`, which would build a **new** image for the final
release: a different digest from the one that passed every stage. That is the rebuild ADR-004
forbids. The mechanics are therefore fixed as follows:

- **A build happens only for a release candidate.** The release workflow runs on a git tag
  `v<version>` whose version has a pre-release part (`v0.2.0-rc.3`), and refuses, as its first
  step, a version without one. A final tag never reaches a build.
- **The final release is a retag, performed by GORA, after review.** The final promotion is a
  promotion PR like any other (ADR-013): it records the final version on the digest that
  reached the last stage before the final one (section 4). When that PR is merged, GORA
  - adds the registry tag `<version>` (`0.2.0`) to that same digest, through a bounded registry
    adapter (ADR-006): it reads the existing manifest by digest and writes it under the new
    tag, never pushes new content;
  - creates the git tag `v<version>` on the merge commit of the promotion PR, through the
    GitHub adapter;
  - emits the VSA and the notification for the final release.
- **Tags never move.** Before tagging, GORA reads the tag: if it already points at the same
  digest the step is a no-op; if it points at another digest, GORA refuses. The check is GORA's and does
  not rely on a registry immutability rule, which not every registry offers.
- **Signatures and attestations carry over** because they are bound to the digest, not to a
  tag: the final release is verified with the same evidence as the candidate.
- **Who runs it.** First the operator, with the CLI, right after the merge (ADR-011 automation
  boundary). Then a workflow triggered by the merge of a GORA promotion PR into `main`. That
  workflow's OIDC identity is the identity that signs GORA's VSA (Consequences).

*Amended 2026-10-03 (ADR-022):* two corrections. The build runs on a commit to `main` that changed
the artifact's inputs, not on a pushed tag; GORA creates the rc tag after verification (until
then, the operator's tag starts the build, as today). Every version tag, rc and final, goes on
the **source commit** the digest was built from, not on the merge commit of the promotion pull
request, which in the single-repository shape holds later changes; the tag is annotated and
names the digest: GORA creates a tag object and its ref, not a bare ref. The workflow that runs
GORA authenticates as GORA's GitHub App (ADR-022 section 14). With the build on `main`, the
trusted signer of section 3 is the reusable release workflow at `refs/heads/main`, not at a tag
ref; the open question in Consequences is about that ref. The manifest's verification pointer is
per stage entry; a candidate has no manifest entry.

*Amended 2026-10-08 (ADR-024 section 2):* a candidate's version is `X.Y.Z-rc`, and the final
retag happens when the promotion into `prod` is merged: GORA tags the same source commit
`vX.Y.Z` and adds the registry tag `X.Y.Z` to the same digest. "First the operator, with the
CLI" under **Who runs it** is replaced: a write runs only as GORA's own machine identity, from
the release repository's pipeline or GORA's server, and a person never lends GORA their token
(ADR-023 sections 7 and 11, L22). The build guard named in Consequences, which refuses a version
without a pre-release part, belongs to the tag-triggered build and ends with it, since a build
on `main` has no version yet (ADR-022 section 6).

*Amended 2026-10-09 (ADR-027 section 7):* "That workflow's OIDC identity is the identity that signs
GORA's VSA" reads: GORA signs as an attester the policy names, a key or a Sigstore identity.

## Why not otherwise

- **slsa-github-generator.** It was the reference way to reach SLSA Build L3 on GitHub, but the
  project is no longer maintained and its README sends new users to GitHub artifact
  attestations.
- **BuildKit provenance alone.** It is useful build metadata but unsigned: SLSA Build L1. It
  cannot prove which workflow produced the digest.
- **A regular expression for the signer.** A pattern trusts every workflow it happens to match,
  including future ones; an exact identity trusts one.
- **Verification only at admission (Kyverno, policy-controller).** Admission protects a cluster,
  not the release decision: the decision is GORA's, made before any cluster sees the digest
  (ADR-019), and a promotion that admission would later reject should not be made. Admission
  stays as defence in depth, checking GORA's VSA.
- **The full verification result inside the manifest.** It would make the manifest the
  evidence store and tie it to one verifier's format. A signed VSA on the digest is the
  standard, portable form; the manifest keeps a pointer.
- **SPDX 2.3 alongside CycloneDX.** Two formats double the attestations to verify and keep in
  sync, and SPDX 2.3 does not meet BSI TR-03183-2.
- **Building the final release from its git tag.** It produces a new digest that no stage has
  run, even from the same commit: build inputs (base images, timestamps, the toolchain) are not
  bit-for-bit reproducible today. The final must be the digest that was verified.
- **Trusting the previous stage's result at promote.** Policies change and keys are revoked;
  verifying again at each promotion is cheap and keeps every decision tied to the current
  policy digest.

## Consequences

- `cut-rc` needs registry access and the Sigstore trust root. A verification adapter (ADR-006,
  read-only) wraps it; tests use recorded fixtures, never the network (TESTING rule 6).

  *Amended 2026-10-08 (correction, ADR-023 section 2):* registration (`gora register`) replaces
  `cut-rc` and needs the same.
- GORA needs its own signing identity for VSAs: a GitHub Actions OIDC identity when it runs in
  CI. Signing VSAs from a laptop is out of scope until that identity question is decided.
- The release workflow changes (ADR-011): reusable build workflow, `actions/attest` for
  provenance and SBOM, CycloneDX 1.6, exact signer identity in its own verify step.
- The manifest schema gains an optional verification pointer per candidate and per stage entry,
  as an additive v0.1 change (ADR-003: schema versions are additive).
- New bounded operations: a registry adapter that reads a manifest by digest and writes it
  under a tag (ADR-006), and a GitHub adapter operation that creates a tag ref. The release
  workflow gains a guard that refuses a version without a pre-release part.
- `release-policy.yaml` becomes part of the release contract and goes through review like the
  manifest.
- Open questions, to be answered when implemented, not assumed: OCI 1.1 referrers support in
  GHCR for cosign v3 bundles; the exact reusable-workflow identity string GitHub issues for a
  tag ref.
- Until implemented, the gaps listed in Context remain, and ADR-003 names E3, E4 and E9 as not
  enforced.

*Amended 2026-10-03 (ADR-022):* the verification pointer is per stage entry only; the GitHub
adapter creates an annotated tag object and its ref, not a bare ref; the open question about the
identity string concerns `refs/heads/main`, not a tag ref.

*Amended 2026-10-09 (ADR-026 sections 6 and 12):* the verification pointer is `spec.evidence` of a
StageState file, in the schema of ADR-026, not a v0.1 change; `release-policy.yaml` is reviewed
as L25 asks, by its owners.

*Amended 2026-10-09 (ADR-027 sections 2 and 7):* GORA signs its summaries as an attester the policy
names (`attesters`), a Sigstore identity or a key, and only a run with GORA's write identity signs.
GORA's own attester is a key kept beside its App's key, so no laptop and no public log takes part.
`actions/attest` above is one tool among others (section 2).

## Sources

- SLSA v1.2 levels, verification, VSA: https://slsa.dev/spec/v1.2/verifying-artifacts ,
  https://slsa.dev/spec/v1.2/verification_summary
- slsa-github-generator status:
  https://github.com/slsa-framework/slsa-github-generator/blob/main/internal/builders/container/README.md
- GitHub artifact attestations and SLSA L3:
  https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/increase-security-rating
- cosign verify-attestation: https://github.com/sigstore/cosign/blob/main/doc/cosign_verify-attestation.md
- in-toto predicates (SLSA, CycloneDX, VSA, release):
  https://github.com/in-toto/attestation/tree/main/spec/predicates
- CycloneDX BOM-Link: https://cyclonedx.org/capabilities/bomlink/
- BSI TR-03183-2 format requirement (secondary summary): https://sbomify.com/2026/01/26/sbom-generation-tools-comparison/
- syft output formats: https://github.com/anchore/syft/wiki/output-formats
- CISA SBOM minimum elements (2025 draft; a 2026 final edition is reported by secondary sources):
  https://www.cisa.gov/news-events/alerts/2025/08/22/cisa-requests-public-comment-updated-guidance-software-bill-materials
- Red Hat SBOM guidelines (component vs product SBOM):
  https://redhatproductsecurity.github.io/security-data-guidelines/sbom/
- Kargo Freight and verification: https://docs.kargo.io/user-guide/how-to-guides/working-with-freight
- JFrog Release Bundles v2: https://docs.jfrog.com/governance/docs/understanding-release-bundles-v2
- SemVer 2.0.0: https://semver.org/

## Amendments

### 2026-10-02 — section 7, release mechanics

Section 7 is recorded with the mechanics of the final release: a retag of the same digest,
checked by GORA before it tags, with the signatures and attestations bound to the digest.

### 2026-10-03 — evidence for a multi-platform image index

ADR-021 makes a candidate one OCI image index with a manifest per platform (`linux/amd64`,
`linux/arm64`), each built and smoke-tested on its own runner. Section 2 is amended in place:
signature on the index and on each platform manifest, provenance for each platform manifest
(from its build job) and for the index (from the job that assembles it), one CycloneDX SBOM per
platform manifest. The index digest is what the manifest records and what section 7 retags.

### 2026-10-03 — candidate record, order of promotion, tags on the source commit

ADR-022 decides the lifecycle on trunk. Sections 1, 3, 4, 7 and the Consequences are amended in
place: a candidate is
registered as tags and a VSA, not written to the manifest; a promotion takes the digest of the
stage before, and the sign-off gate admits only the newest non-rejected candidate; the build runs
on `main`, every version tag goes on the source commit as an annotated tag, and the trusted signer
is the release workflow at `refs/heads/main`.

### 2026-10-08 — the final version is made by entering `prod`; who runs a write

ADR-024 decides the versions and the order of promotion. Sections 1, 4 and 7 are amended in
place: a promotion takes an eligible version, one the stage before held and verified; the final
retag happens when a version enters `prod`; a write runs only as GORA's machine identity; the
pre-release build guard ends with the tag-triggered build.

### 2026-10-08 — registration needs registry access and the trust root

A correction: Consequences named `cut-rc`; registration replaces it (ADR-023 section 2).

### 2026-10-09 — builders, the SBOM floor, the policy digest, the pointer

ADR-026 decides the release files. For this ADR: CycloneDX 1.6 or newer, the floor a policy field
that only rises (section 2); the trusted signers are the policy's named builders, chosen per
artifact, and the required evidence is not a policy field (section 3); the policy digest is
RFC 8785 over the policy and the declaration (section 3); the evidence pointer is `spec.evidence`
of a StageState (section 4, Consequences); the composition of `prod` is the `state/prod/` tree
and its Release BOM is left to ADR-027 (section 5).

### 2026-10-09 — SBOM form, summaries, signers, any tool, a computed Release BOM

ADR-027 decides the release record. For this ADR: the producer of provenance is any tool, and GORA
checks an SBOM's form, not its content (section 2); a trusted signer is a Sigstore identity or a
public key (section 3); a second kind of verification summary records each result of a stage's
verifications (section 4); the Release BOM is computed as CycloneDX (section 5); GORA signs as an
attester the policy names (section 7, Consequences).

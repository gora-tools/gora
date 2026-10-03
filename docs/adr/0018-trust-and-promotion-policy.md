# ADR-018: Trust and Promotion Policy

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-02
**Amended:** 2026-10-02 — section 7, release mechanics
**Amended:** 2026-10-03 — evidence for a multi-platform image index (ADR-021)

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

Each verification produces a **SLSA Verification Summary Attestation (VSA,
`https://slsa.dev/verification_summary/v1`)**, signed with GORA's own OIDC identity and
attached to the digest as an OCI referrer. The VSA names the verifier, the resource, the policy
(URI and digest), the result and the verified SLSA level. The manifest records only a pointer:
VSA digest, policy digest, result, time. The manifest stays the contract; the VSA is the
portable evidence a CD system or an admission controller (Sigstore policy-controller, Kyverno)
can check without trusting GORA's file.

### 5. Release BOM is not an SBOM

An SBOM says what is inside one artifact. The v0.2 Release BOM (ADR-003) says which artifacts,
at which digests, form one system release. They stay separate documents. When v0.2 is
formalised it binds to existing standards rather than inventing a format: the in-toto release
predicate (one release identity, every artifact digest as a subject) or a CycloneDX BOM whose
components link to each image SBOM through BOM-Link. The Release BOM identity is content-derived
(a hash of its canonical artifact list), as Kargo does for Freight.

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

### 2026-10-03 — evidence for a multi-platform image index

ADR-021 makes a candidate one OCI image index with a manifest per platform (`linux/amd64`,
`linux/arm64`), each built and smoke-tested on its own runner. Section 2 is amended in place:
signature on the index and on each platform manifest, provenance for each platform manifest
(from its build job) and for the index (from the job that assembles it), one CycloneDX SBOM per
platform manifest. The index digest is what the manifest records and what section 7 retags.

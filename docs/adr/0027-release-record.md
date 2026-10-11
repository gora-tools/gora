# ADR-027: The Release Record — Verification Results, Signers, Journal, Change Log, Release BOM

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-09
**Amends:** ADR-006, ADR-007, ADR-009, ADR-011, ADR-017, ADR-018, ADR-019, ADR-022, ADR-023, ADR-024, ADR-026
**Amended:** 2026-10-09 — a signer's identity in `gora.tools/v1alpha1` (ADR-026)
**Amended:** 2026-10-10 — a CI identity is bound to its repository; GORA's attester is keyless
**Amended:** 2026-10-11 — an rc tag's trailer is read back (correction)

---

## Context

ADR-026 decided the files of the release repository and left the release record to this ADR:
what GORA keeps of what happened, where, and for how long. Four questions were open:

- **The audit journal under automatic writes.** ADR-017 writes the journal to a file in the
  home directory of whoever runs a command. In the release repository's pipeline the runner is
  thrown away after the run, and so is the file; ADR-017 "No rotation" names automatic writers
  as its falsifier, and ADR-023 left where a pipeline run's journal is kept to "decided with the
  server".
- **The change log.** ADR-022 section 5 says GORA produces it from the same Conventional
  Commits as the version, without an external tool; its form and its place were left open.
- **The Release BOM.** ADR-018 section 5 asks for a document of which artifacts, at which
  digests, form one state of the system, bound to an existing standard, with an identity
  derived from its content. ADR-026 made the composition of `prod` the `state/prod/` tree.
- **The SBOM.** ADR-018 section 2 decided CycloneDX, and ADR-026 put its version floor in the
  policy; what GORA checks of an SBOM a project's CI hands over was not written down.

A fifth question came up while this ADR was prepared, and it is the most urgent. **GitHub
deletes commit statuses by age.** From 2026-10-01 the retention period of a repository applies
to checks, workflow runs and commit statuses, including those created by third-party
integrations: 90 days by default, at most 400 days for a private repository and 90 for a public
one. Before that date they were kept for 400 days or more whatever the setting. The release
model computes "verified" from commit statuses alone (ADR-024 sections 3 and 7). So when `prod`
has held a version for longer than the retention period, its statuses are gone, the stage loop
sees a holding that has not finished its verifications, and it never proposes the next version
into `prod`: `gora status` would show the artifact verifying forever, which L24 forbids. The same
holds in every stage, and for a version that waits its turn longer than that. The release
model's rule for every change stops here, and this ADR is the decision the maintainer took on it.

A sixth question followed from the fifth. Recording a result means signing it, and ADR-018 knew
only keyless signers, through Sigstore, and provenance made with GitHub artifact attestations.
In a private repository GitHub serves both its own Sigstore instance and artifact attestations
only on Enterprise Cloud; everywhere else a keyless signature enters the public log with the
repository's name. A customer's private repository may not accept that, and GORA should not
depend on a host's plan or on one signing service.

The decisions were taken with the maintainer on 2026-10-09, in the spirit he gave for the
release record: as for microservices, the same approach as ADR-026. One fact has one place, a
process owns what it emits, and nothing is written by hand. And the more independently GORA does
its work, the better: what customers ask of it is security, privacy and reliability.

## Decision

### 1. Where each fact of the record lives

| Fact | Where | Written by | Kept |
| --- | --- | --- | --- |
| What each stage holds, when it changed, through which pull request | `state/` on `main`, its commits and their trailers | GORA, through its pull requests (L16) | as long as Git |
| A rejection and its reason | `rejections/` (ADR-026 section 7) | GORA | as long as Git |
| A candidate, a final version, a platform state | tags (L10, L17) | GORA | as long as Git |
| What changed in a version | the message of its version tag (section 4) | GORA | as long as Git |
| Who asked, who approved, who merged; what a promotion brings | the pull request, its reviews and GORA's comment on it (ADR-023 section 6); its body (section 4) | the code host and GORA | as long as the host keeps the pull request |
| The evidence of a build | attestations on the digest (ADR-018 sections 2 and 3) | the project's CI | as long as the registry keeps the digest |
| That a digest passed GORA's evidence check, and each result of a stage's verifications | GORA's verification summaries on the digest (ADR-018 section 4; section 2 here) | GORA | as long as the registry keeps the digest |
| What a process of GORA did | the audit journal (section 3) | that process | as the platform that runs it keeps its logs |
| The composition of a stage at a commit | computed from `state/` (section 5) | nobody: a view | — |

- **GORA never rests a decision on data the host deletes by age.** Commit statuses, check runs
  and workflow runs are inputs that expire; whatever GORA needs from them after the moment it
  read them is recorded in one of the places above, each kept as long as the thing it is about.
  A required check of an open GORA pull request that has expired is run again: GORA recreates
  its commit on the branch, with the same change, so the checks run on it; an approval stays,
  since it binds to the promotion (ADR-023 section 8).
- **A view is computed, never stored.** The change log of a range, the Release BOM and
  `gora history` are computed from the record each time and give the same result each time. Two
  copies are written once and never read back by GORA: the message of a version tag, fixed with
  the tag, and the body of a promotion pull request, which the host keeps with the pull request
  (section 4).

  *Amended 2026-10-11 (correction):* the message of an rc tag is read back in one part: it ends
  with the trailer `Gora-candidate: <artifact> <version> <digest>`, the one part of the message
  GORA reads to find which digest a candidate is. A registration asked again for a commit that
  carries the artifact's rc tag compares the digest it is given with it (ADR-024 section 1),
  and a promotion into the first stage starts from it. The trailer is an index, not a ground
  for trust: once a project is connected only GORA's identity creates version tags (L10), and
  whoever reads one checks the digest again, a promotion by its evidence (ADR-018 section 4).
  GORA reads it only from the last line of the message, after a blank line, and only when it
  names the artifact and the version the tag's name names. The change log is never read back.
- **Nothing in Git names a person** (ADR-023 section 6). Who asked and who approved stay in the
  host's record and the journal.

### 2. Every verification result is attested

When GORA counts a verification's result in a stage, it records it as a signed SLSA
Verification Summary Attestation on the digest, the predicate it already writes when the
evidence check lets a digest in (ADR-018 section 4). One attestation per result: a verification
is itself a check with a pass or a fail.

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{ "name": "ghcr.io/acme/cally-api", "digest": { "sha256": "<64 hex>" } }],
  "predicateType": "https://slsa.dev/verification_summary/v1",
  "predicate": {
    "verifier": { "id": "git+https://github.com/acme/cally-release#attester/gora-pipeline" },
    "timeVerified": "2026-10-09T12:04:00Z",
    "resourceUri": "oci://ghcr.io/acme/cally-api@sha256:<64 hex>",
    "policy": {
      "uri": "git+https://github.com/acme/cally-release@<manifest commit>#stage-verification/qa/api/e2e",
      "digest": { "sha256": "<64 hex>" }
    },
    "verificationResult": "PASSED",
    "verifiedLevels": ["SLSA_BUILD_LEVEL_3"],
    "https://gora.tools/attestations/stage-verification/v1": {
      "stage": "qa",
      "artifact": "api",
      "version": "1.4.0-rc",
      "manifestCommit": "<40 hex>",
      "verification": "e2e",
      "state": "success",
      "reporter": "app/e2e-runner",
      "createdAt": "2026-10-09T12:03:40Z"
    }
  }
}
```

- **One result of one holding.** The holding is the stage's entry for the artifact as the
  manifest commit that set it left it (ADR-024 section 3). The policy URI names that commit, and
  its fragment names the check, `stage-verification/<stage>/<artifact>/<verification>`, so a
  consumer that reads only the fields the specification defines tells this summary from the one
  of the evidence check, whose URI has no fragment (ADR-026 section 15); the policy digest is that
  of ADR-026 section 15 at that commit, the policy and the declaration that decided which
  verifications apply and who reports them. The extension field, named by a URI as the
  specification allows, repeats the holding and records the status GORA counted: its state, its
  reporter and the time the host created it. A consumer that does not know the field ignores it.
  `verifier.id` names the attester of section 7 that signed it.
- **The result.** A status in a final state is attested: `success` as `PASSED`; `failure` or
  `error` as `FAILED`, with `verifiedLevels` `["FAILED"]`, as the specification asks. Otherwise
  `verifiedLevels` carries the build level the evidence check verified. A `pending` status is not
  a result and is not attested.
- **When.** On every run of the loop, for every counted status in a final state that is newer
  than the newest attested result of its verification in that holding. The loop runs on every
  status event and as a periodic reconciliation (ADR-023 section 8), so a result is attested
  within minutes of being reported.
- **Who writes it.** Only a run of the stage loop that holds GORA's write identity (L22) attests;
  a read, a dry run or a person's command computes from what exists and writes nothing. It signs
  as an attester the policy names (section 7).
- **How GORA reads it.** An attestation counts for a holding only when it is signed by an
  attester the policy names, its subject is the entry's digest, its extension names the entry's
  stage, artifact and setting commit, and its policy digest is that of the setting commit. For
  each verification of the holding, the result is the one with the newest creation time among
  its counted status on the host and its counted attestations. A `pending` status newer than
  every attested result stands while the host keeps it: a check run again is verifying until it
  reports. A holding is finished and verified as ADR-024 section 3 says, from those results.
  While the statuses exist, this is what they say; once the host has deleted them, it is what
  they said.
- **What could still be lost.** A status the host deletes before any run of GORA has seen it,
  which takes GORA not running at all for the whole retention period (90 days by default; a
  project may set it lower). A pending check run again that expires unreported leaves the result
  attested before it: the holding's state then changes with no event, at the next run, and
  `gora status` shows the change.
- **Portable.** Where the registry serves OCI referrers (ADR-018, Consequences, names it an open
  question for GHCR), a CD system or an admission controller can require a `PASSED` summary for
  each verification of `staging` before a digest runs in `prod`, as it can require its
  provenance.
- **Kept.** The registry keeps the attestations of every digest a stage holds or may roll back
  to, as it keeps the digest (a condition of connecting).

### 3. The audit journal is a stream where nobody runs a command

- **A process that runs without a person writes its journal to standard error**, one JSON event
  per line, in the format ADR-017 decided: the release repository's pipeline and GORA's server.
  `GORA_AUDIT_LOG=-` selects the stream; GORA's server always uses it, and the reference workflow
  of the release repository sets it. The platform that runs the process keeps the stream, as it
  keeps every service's logs: the pipeline's run log, or the log store of the cluster. GORA does
  not write a log store of its own.
- **Standard error, not standard output.** Standard output carries a command's result
  (`--output json`), which a pipeline parses; an event mixed into it would break the parse. Every
  platform keeps standard error as it keeps standard output.
- **What changes for a stream.** `-` is file descriptor 2, written as it is: no path is resolved,
  no file is created, and there is no regular-file check, no `fsync` and no check of a last
  byte, which a stream does not have. Each event is one `write`, under one lock in the process.
  Another writer of the same stream, a web server's log or a traceback, can still land between
  the parts of a write longer than the pipe's atomic size; a reader keeps only the lines that
  parse as events, as ADR-017 already asks of a reader.
- **The rest of ADR-017's failure policy applies.** The stream is checked before any mutation; a
  write that fails after a mutation completes the operation and exits 3. The events that missed
  it would be printed on standard error, which is the stream itself: when that is broken, nothing
  more can be said, and the exit code is the record.
- **A person's command keeps the file.** `gora` run by hand writes the file of ADR-017, so the
  file is still appended to only by a person, and "No rotation" holds as decided. Its falsifier
  stands: the day anything automatic appends to the file.
- **The journal is a log, not the record of decisions.** Who asked for a promotion, a rollback or
  a rejection is in GORA's comment on the pull request, and who approved is in its reviews; both
  stay as long as the pull request (section 1). What only the journal holds is what never became
  a pull request: a refused request or an error. It names the account that asked (ADR-017): in a
  public release repository the run log is public and kept at most 90 days, so a project that
  cannot accept that runs GORA's writes from its server or a private repository.

### 4. The change log

- **Computed by GORA, from the commits that make the version.** The change log of a range of an
  artifact's versions is the first-parent commits of `main` after the source commit of the
  range's start, up to and including the source commit of its end, that changed the artifact's
  inputs: the same commits that compute a version (ADR-024 section 1). No external tool and no
  second configuration (ADR-022 section 5).
- **Its form.** Grouped in this order: breaking changes, features, fixes, then every other type,
  each group in first-parent order. Each line is the commit's description, its scope in
  parentheses when it has one, and the first 12 hexadecimal digits of the commit. A subject that
  is not a Conventional Commit goes to the last group as it is. Plain text that reads as Markdown.
  No author: nothing in Git names a person.
- **Written once, into the tag.** The message of the annotated tag `vX.Y.Z-rc` already names the
  artifact, the image, the digest and the policy digest (ADR-022 section 6); it gains the change
  log since the previous registered candidate, and for the first candidate its own commit. The
  message of `vX.Y.Z`, created when the version enters `prod`, names the same and the platform
  state the merge made, and carries the change log since the version `prod` held when this one
  entered it, and for the first final version from the first candidate, inclusive. So after a
  rollback, the next final version lists everything that `prod` did not run. Tags never move
  (L10), so the text is fixed with the version it describes.

  *Amended 2026-10-11 (correction):* the rc tag's message is, each part after a blank line:
  `<artifact> <version>`; `Image: <image>@<digest>` and `Policy digest: <policy digest>`; the
  change log; and the trailer of section 1, its last line. The groups are named
  `Breaking changes:`, `Features:`, `Fixes:` and `Other changes:`, the last holding every
  other type; a group with no commit is left out, and a breaking change is listed only among
  the breaking changes. A line is `- <description> (<scope>) <12 hexadecimal digits>`, the
  scope only when there is one; a NUL or a lone surrogate, which no message of a tag can hold,
  is written as U+FFFD. GORA writes the message byte by byte: no line begins with `#` or ends
  with white space, and no blank line follows another, so git's cleanup of a message changes
  nothing.
- **Shown where a person decides.** The body of a promotion pull request carries, above its
  trailers (ADR-023 section 6), the change log from the version the stage holds to the version it
  promotes, or from the first candidate when the stage holds nothing yet; a rollback's body lists
  the changes it takes out. When the text would exceed what the host accepts in a body, the body
  lists the count per group and the command that prints it in full. The merge commit does not
  carry it: GORA sets the body of the squash commit to its trailers alone, as ADR-023 section 6
  sets the merge message where the host allows it, so no change log enters `main`.
- **Asked for.** `gora changelog ARTIFACT [--from VERSION] [--to VERSION]` prints it for any
  range (API `GET /v1/projects/{p}/artifacts/{a}/changelog?from=&to=`); by default, from the
  version `prod` holds to the newest registered candidate.
- **No `CHANGELOG.md`, no host release object.** A file would be a second copy that GORA writes
  into a repository whose code it does not own; a GitHub Release is a second, editable copy that
  exists on one host only.

### 5. The Release BOM is computed

- **The composition is `state/<stage>/` at a commit**, and for `prod` at a platform state
  (ADR-026 section 2). `gora bom [STAGE] [--at STATE | COMMIT]` prints it as a CycloneDX 1.6 JSON
  document (API `GET /v1/projects/{p}/stages/{s}/bom?at=`); the defaults are `prod` and its newest
  platform state. It is never stored: the tree and its protected tag are the record (L17).
- **Its content.** `metadata.component` has type `application`, the project's name (its release
  repository's path) and as its version the platform state or the commit. One component per
  entry, sorted by artifact name: type `container`, the artifact's name and version, a hash with
  `alg` `SHA-256` and the digest's hexadecimal content, and the package URL
  `pkg:oci/<last segment of the image>@sha256%3A<hex>?repository_url=<image>`. Each image's own
  SBOM is found from its digest through the registry's referrers, where the registry serves them,
  as a consumer finds its
  provenance. The document has no `serialNumber` and no `metadata.timestamp`, both optional:
  CycloneDX asks for a new serial number for every document generated and the time it was
  generated, and a view generated again must give the same bytes.
- **Deterministic.** The same stage at the same commit gives the same bytes; the document's
  identity is the SHA-256 of its RFC 8785 serialisation, which replaces the "hash of its
  canonical artifact list" of ADR-018 section 5.
- **Trusted through what it is made of.** The tree is protected by L16 and its state tag by L17,
  and each digest carries its own evidence and GORA's attestations. A consumer that must check
  the BOM without Git checks each digest's attestations, which say the same.

### 6. What GORA checks of an SBOM

- **Its form, not its content.** At registration and at every promotion (ADR-018 section 4) GORA
  checks, for every platform manifest of a candidate, that an SBOM attestation exists, signed by
  one of the artifact's builders, whose subject is that manifest's digest, whose `bomFormat` is
  `CycloneDX`, JSON, and whose `specVersion` is at least the policy's floor (ADR-026 section 4).
  It reads no other field, so a CycloneDX version newer than the running GORA knows passes, and a
  rollback of GORA never refuses a digest for its SBOM's version.
- **Its content is the project's**, as what a migration does is (ADR-025): GORA does not judge
  its components or its completeness. GORA's own release workflow keeps the commitment ADR-018
  section 2 made for its SBOM.

### 7. Signers: an identity or a key, from any tool, on any plan

GORA depends on no plan of a host and no signing service. It verifies standard formats and the
signer the policy names, never the tool or the service that made a signature.

- **A signer is an identity or a key.** Every signer the policy names, a builder (ADR-026
  section 4) or an attester of GORA's own summaries, is one of:
  - **a Sigstore identity:** an `issuer` and an exact `identity`, no pattern, and `sigstore`, the
    instance whose trust root verifies it: `public` (the default) or `github`, GitHub's own
    instance, which serves a private repository only on Enterprise Cloud;
  - **a public key:** `publicKey`, in PEM. A signature by a key needs no certificate and no
    transparency log; GORA verifies it with the key alone.

  *Amended 2026-10-09 (ADR-026 sections 3 and 4, from building the models):* in schema
  `gora.tools/v1alpha1` an identity is an `https` URI, a workload's (a reusable workflow, a CI
  pipeline), as ADR-003's grammar states; an email address, a person's or a service account's,
  and other URI schemes (`spiffe://`) are refused, and a later schema version admits one when a
  project needs it. A key is an uncompressed P-256 point or an Ed25519 key not of small order.
  The policy refuses a signer named both as a builder and as an attester.

  ```yaml
  builders:
    cally-ci:
      issuer: https://token.actions.githubusercontent.com
      identity: https://github.com/acme/cally-ci/.github/workflows/build.yml@refs/heads/main
  attesters:
    gora-pipeline:
      publicKey: |
        -----BEGIN PUBLIC KEY-----
        <base64>
        -----END PUBLIC KEY-----
  ```

- *Amended 2026-10-10:* **A CI identity is bound to its repository.** A Sigstore identity of a
  CI workload names the workflow that signed, not the repository whose run signed: on GitHub
  Actions a reusable workflow in a public repository can be called from any repository, and the
  certificate of that run names the workflow all the same. So GORA also checks fields Fulcio
  records in the certificate of a CI run:
  - the source repository, by the host's immutable identifier (`1.3.6.1.4.1.57264.1.15`;
    GitHub's `repository_id`, GitLab's `project_id`), against the identifier GORA reads from the
    host for the repository the signer's role names: a builder's, the artifact's source
    repository; an attester's, the release repository. A renamed or transferred repository
    keeps its signatures, and a name someone else registers later gains none;
  - the source ref (`1.3.6.1.4.1.57264.1.14`), exactly `refs/heads/main`: a builder's by L5, an
    attester's by L22;
  - for a builder, the source repository digest (`1.3.6.1.4.1.57264.1.13`), exactly the source
    commit the image carries (L9), so a run on `main` that built another commit is refused.

  Every Sigstore identity GORA counts is a CI workload's; a certificate without these fields,
  with one of them empty, or with them in another form, is refused, and such a signer signs with
  a key. The policy names none of them: they follow from what the signer signs for.
- **Why both.** A Sigstore identity needs no key to keep, but its signature enters the
  instance's public log with the signer's identity, and so a private repository's name, unless
  the plan offers GitHub's instance. A key keeps everything private and needs no service to sign
  or to verify, at the price of keeping it. A project chooses per signer; the model holds the same
  either way.
- **Where an attester's key lives.** Only where GORA's writes run (L22), and there only within
  reach of the job that attests: in the release repository's pipeline, an environment of its own
  for `main` that only the job of GORA's reusable attesting workflow uses, or a cloud KMS whose
  access trusts only that workflow's identity; on GORA's server, the server alone. No reporter's
  job and no other workflow reaches it, since whoever holds it can sign a result for any
  verification. A key in a KMS is named the same way, by its public key.
- *Amended 2026-10-10:* **Where an attester's identity is issued.** On GitHub Actions an
  attester that is a Sigstore identity is a reusable workflow at `main`, called from the
  release repository; every job of it runs in an environment of its own whose deployment
  branches are `main` only, and runs a GORA image by digest (for GORA itself, the version
  `prod` holds, ADR-024 section 9), never code from its caller's checkout. It attests only
  results it reads from the host itself (section 2), never one its caller hands it. No
  reporter's job and no other workflow calls it. A run in another repository may be issued the
  same identity; the check above refuses whatever it signs.
- **A key signs without a log.** GORA's tools sign with a key without uploading to a
  transparency log (cosign: `--tlog-upload=false`; verified with `--insecure-ignore-tlog`), since
  cosign uploads key signatures too by default. The key alone verifies the signature.
- **Time is the signer's claim.** A record counts only when its `createdAt` is not after its
  `timeVerified`, and its `timeVerified` is not after the time GORA reads it, with five minutes of
  allowed skew. A key signer's records are trusted as far as its key: removing the attester
  revokes them, under the rule of switching below.
- **A key builder and the build level.** SLSA Build L3 rests on a signing identity that the build
  steps cannot reach (ADR-018 section 2). A builder that is a key gives Build L3 only when the key
  is reachable by the reusable workflow's job alone, as a KMS key whose access trusts only that
  workflow's identity is; otherwise GORA's evidence check records `SLSA_BUILD_LEVEL_2`.
- **Switching a signer.** The new one is added, then the old one removed: a builder as ADR-026
  section 5 says, an attester when no holding a stage holds or may roll back to depends on the
  old one's attestations alone; GORA's required status refuses that removal otherwise and lists
  the holdings (ADR-026 section 13).
- **Any tool.** Provenance is SLSA provenance v1 as an in-toto statement, made from a reusable
  workflow whose identity the steps it runs cannot reach (ADR-018 section 2), by any tool that
  writes it: `cosign attest`, or GitHub's `actions/attest`, which a private repository can use
  only on Enterprise Cloud. GORA checks the statement, its predicate and its signer.
- **What GORA asks of a host's plan.** Only the conditions of connecting (release-model section
  9). On GitHub, a private repository has the rulesets, required checks and protected tags they
  need from GitHub Team; no Enterprise Cloud feature is a condition.

### 8. GORA's own release repository

GORA's own pipeline sets `GORA_AUDIT_LOG=-`. Its attester is a key in an environment of its own for
`main`, used only by the job of its reusable attesting workflow, and it signs without a log, so its
summaries reach no public log. Its images will be signed, and their provenance and SBOM attested,
keyless with `cosign` from a reusable release workflow; its plan does not offer GitHub's instance to
a private repository. Today `release.yml` signs the images keyless and attests an SPDX SBOM, and the
provenance is BuildKit's, unsigned (release-model section 13). It keeps its repository's retention
as it is: no decision of GORA rests on it. Its first state file, the first promotion of `0.2.1-rc`
into `dev` (ADR-026 section 16), is the first holding it attests.

*Amended 2026-10-10:* the repository is public since 2026-10-10, so a keyless signature names
nothing private. GORA's attester is a Sigstore identity on the public instance rather than a
key: the issuer `https://token.actions.githubusercontent.com` and the identity of its reusable
attesting workflow at `main`,
`https://github.com/gora-tools/gora/.github/workflows/attest.yml@refs/heads/main`, a workflow
not built yet, issued as section 7 says and bound to the repository `gora-tools/gora` at
`refs/heads/main`. There is no key to keep, and its summaries enter the public log. Its builder
will be the same kind of identity, its reusable release workflow at `main` (ADR-018 section 3).
"Its plan does not offer GitHub's instance to a private repository" no longer applies; its
signatures are on the public instance. Its journal, the run log (`GORA_AUDIT_LOG=-`), is public
and kept as long as the repository's retention allows, at most 90 days for a public repository
(section 3); it names the code-host accounts that asked.

### 9. Invariants and their enforcement

Per ADR-016 rule 6. All are planned.

| Invariant | Enforced by | Class |
| --- | --- | --- |
| Every counted result in a final state has an attestation; a newer one adds one | the stage loop | `runtime-check`, `test` |
| Only a run with GORA's write identity attests; a read or a dry run writes nothing | the stage loop | `test` |
| An attestation counts only when signed by a named attester and bound to the holding's digest, stage, artifact, setting commit and policy digest | attestation verification | `runtime-check`, `test` |
| For each verification, the result with the newest creation time decides | the eligibility computation | `test` |
| A signer is a Sigstore identity with its instance, or a public key; a key signature needs no log | signature verification | `runtime-check`, `test` |
| A record's times are within the bounds of section 7 | attestation verification | `runtime-check`, `test` |
| An attester's key is reachable only by the attesting job | the reference workflow; review | `review-only` |
| A CI identity counts only from a certificate whose source repository identifier, source ref and, for a builder, source commit are the ones its role requires | signature verification | `runtime-check`, `test` |
| An attester's identity is issued only to the attesting workflow's jobs in the release repository at `main` | the reference workflow; review | `review-only` |
| No signer is both a builder and an attester | the policy model (ADR-026 section 4) | `runtime-check`, `test` |
| An attester is removed only when no holding a stage holds or may roll back to depends on it alone | GORA's required status | `runtime-check` |
| An expired required check of an open GORA pull request runs again | the stage loop | `runtime-check`, `test` |
| A process without a person writes its journal to the stream; a person's command to the file | the sink | `test` |
| The change log of a range is the commits that compute the version, in the stated form and order | the change log computation | `test` |
| A version tag's message carries its change log, written once; no change log enters `main` | registration, final tagging, the merge message | `runtime-check`, `test` |
| An rc tag's message ends with its trailer, read back only when it agrees with the tag's name; whoever reads it checks the digest again | registration, the stage loop | `runtime-check`, `test` |
| The same stage at the same commit gives the same Release BOM bytes | the BOM computation | `test` |
| An SBOM's form is checked, never its content | the evidence check | `runtime-check`, `test` |

*Amended 2026-10-10:* the rows on a CI identity's repository and on where an attester's
identity is issued are added (section 7).

*Amended 2026-10-11:* the row on an rc tag's trailer is added (section 1). The change log and
the reading of the trailer exist (`gora.domain.release.changelog`,
`gora.domain.release.tags`); no command uses them yet, so the rows stay planned.

## Why not otherwise

- **Commit statuses as the only record of verification.** The host deletes them by age, and the
  stage loop would stall on any stage that holds a version longer than the retention period.
- **A record of the outcome in Git** (`verifications/<stage>/<artifact>/<version>.yaml`, written
  like a rejection). It keeps every fact in Git, at the price of one more GORA pull request for
  every holding of every stage, as many as the promotions themselves. The outcome is evidence
  about a digest, and evidence about a digest already lives on the digest (ADR-018).
- **Requiring the longest retention and treating older holdings as finished.** It rests on a
  setting GORA does not control, is capped at 90 days for a public repository, and loses the
  outcome: a failed holding would read as finished with nothing to say whether it passed.
- **One attestation per holding, once it has finished.** A verification that reports long after
  the others would find their statuses gone; each result attested as GORA sees it leaves nothing
  to lose but what GORA never saw.
- **Keyless signatures only.** A private repository without GitHub Enterprise Cloud would have
  to publish its name in the public Sigstore log with every signature, which a customer's
  security and privacy rules may forbid; a key needs no external service to sign or verify.
- **Keys only.** A Sigstore identity needs no key to keep and binds a signature to a workflow; a
  project that accepts the public log, or has GitHub's instance, should not have to manage keys.
- **GitHub artifact attestations as the one way to make provenance.** A private repository can
  use them only on Enterprise Cloud; the statement they write is standard, and so is the one
  `cosign attest` writes.
- **A durable journal store now** (an append-only bucket with object lock, through an adapter).
  The decisions are already durable on the host and in Git; the journal holds operations, which
  a platform keeps as it keeps every service's logs. An adapter can add a store when a project
  needs one, without changing the event.
- **The journal on standard output.** It would corrupt a command's machine-readable result.
- **A `CHANGELOG.md` or a GitHub Release.** A second copy of a fact in Git, editable, and in the
  first case written by GORA into code it does not own.
- **git-cliff, semantic-release or release-please.** The version is part of GORA's contract;
  computing its change log from the same commits needs no second tool and no second
  configuration (ADR-022, "Why not otherwise").
- **A stored, signed Release BOM per platform state.** A second place for the composition, with
  storage of its own; the tree, its protected tag and each digest's attestations already carry
  everything it would say.
- **An in-toto release predicate for the BOM** (ADR-018 section 5 named it beside CycloneDX).
  CycloneDX is already the project's SBOM format; one format, as ADR-018 decided for SBOMs.
- **Checking an SBOM's content.** Completeness depends on the build and the ecosystem; a rule in
  GORA would be a second build policy. The form, the signer and the subject are what GORA can
  check for every project the same way.

## Consequences

**Laws.** No law changes. L24 is held for holdings older than the host's retention, through
section 2, and its "verified" reads the attested results once the statuses are gone.

Amendments, each recorded in the amended ADR:

- **ADR-006:** the registry adapter also attaches GORA's signed verification summaries to a
  digest: content of GORA's own, never an image or a tag.
- **ADR-007:** each run of the loop reads Git, the code host and the registry.
- **ADR-009:** step 2 of the plan of work (provenance from any tool; GORA's own uses `cosign`).
- **ADR-011:** GORA's own provenance and SBOM are attested with `cosign`.
- **ADR-017:** "The journal has a path" (`-` is the stream), "The sink" (the stream for a process
  without a person, `GORA_AUDIT_LOG=-`, and what does not apply to a stream), "No rotation" (the
  file stays a person's), Consequences (the stream needs no writable `HOME`).
- **ADR-018:** section 2 (provenance from any tool; what GORA checks of an SBOM), section 3 (a
  signer is a Sigstore identity or a public key), section 4 (a second kind of verification
  summary, one per result of a stage's verifications, signed as an attester), section 5 (the
  Release BOM is computed, CycloneDX, and its identity), section 7 and Consequences (who signs
  GORA's summaries).
- **ADR-019:** section 6 (the evidence a promotion counts is the statuses and GORA's attested
  records of them).
- **ADR-022:** section 5 (the change log is decided), section 6 (the rc tag's message carries
  it), Consequences (the release record is this ADR's).
- **ADR-023:** sections 2 and 4 (`gora changelog`, `gora bom`), section 6 (a promotion pull
  request's body carries the change log; the squash commit's body is the trailers alone),
  section 8 ("when the loop runs": Git, the code host and the registry; an expired required
  check runs again), section 11 (what the model needs of a host, a plan and a registry),
  Consequences (where a pipeline run's journal is kept).
- **ADR-024:** section 2 (the final tag's message), section 3 (verified, from the result with
  the newest creation time among a status and its attested records), section 7 (verified is no
  longer computed from statuses alone).
- **ADR-026:** sections 3 and 4 (the policy names its `attesters`; a builder or an attester is a
  Sigstore identity or a public key, with their grammars), section 13 (GORA's required status
  refuses the removal of an attester a holding depends on, and lists those holdings).

In `docs/architecture/release-model.md`: the vocabulary (verified, policy), section 4
(verifications, when the loop runs), section 9 (the registry keeps the attestations), section 11
(`gora changelog`, `gora bom`) and section 13. In `docs/catalog/workflow-catalog.md`: each run
reads Git, the code host and the registry.

## Sources

- GitHub changelog, Actions retention will cover checks, workflow runs and statuses (2026-08-27):
  https://github.blog/changelog/2026-08-27-actions-retention-will-cover-checks-workflow-runs-and-statuses
- GitHub, configuring the retention period for checks, workflow runs, commit statuses, artifacts
  and logs:
  https://docs.github.com/en/organizations/managing-organization-settings/configuring-the-retention-period-for-github-actions-artifacts-and-logs-in-your-organization
- SLSA, Verification Summary Attestation: https://slsa.dev/spec/v1.2/verification_summary
- GitHub, using artifact attestations (Enterprise Cloud for private repositories):
  https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations
- GitHub, about rulesets (plans):
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets
- Sigstore cosign, signing with keys and without a transparency log:
  https://docs.sigstore.dev/cosign/signing/signing_with_containers/
- GitHub, enable auto-merge with a commit body (GraphQL `enablePullRequestAutoMerge`):
  https://docs.github.com/en/graphql/reference/mutations#enablepullrequestautomerge
- in-toto attestation framework: https://github.com/in-toto/attestation
- CycloneDX 1.6 JSON schema: https://cyclonedx.org/schema/bom-1.6.schema.json
- CycloneDX, cross-BOM references: https://cyclonedx.org/use-cases/cross-bom-references/
- Package URL specification: https://github.com/package-url/purl-spec
- The Twelve-Factor App, logs: https://12factor.net/logs
- Conventional Commits 1.0.0: https://www.conventionalcommits.org/en/v1.0.0/
- RFC 8785, JSON Canonicalization Scheme: https://www.rfc-editor.org/rfc/rfc8785
- RFC 9562, UUIDs (name-based, version 5): https://www.rfc-editor.org/rfc/rfc9562
- GitHub, reusing workflow configurations (access to reusable workflows):
  https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations
- GitHub, OIDC with reusable workflows (`job_workflow_ref` names the called workflow; the other
  claims are the caller's):
  https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows
- Sigstore, OIDC usage in Fulcio (the SAN of a GitHub Actions certificate):
  https://docs.sigstore.dev/fulcio/oidc-in-fulcio
- Sigstore Fulcio, OID information (source repository digest, ref and identifier), and the claim
  mapping per issuer, `config/identity/config.yaml` of sigstore/fulcio:
  https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md
  https://github.com/sigstore/fulcio/blob/main/config/identity/config.yaml
- GitHub, GitHub-hosted runners reference (arm64 vCPUs, public and private repositories):
  https://docs.github.com/en/actions/reference/runners/github-hosted-runners

## Amendments

### 2026-10-09 — a signer's identity in `gora.tools/v1alpha1`

Building the models of ADR-026 fixed the forms of section 7's signers in the first schema
version: an identity is an `https` URI, a key an uncompressed P-256 point or an Ed25519 key not of
small order; the policy refuses a signer that is both a builder and an attester, a row of section
9.

### 2026-10-10 — a CI identity is bound to its repository; GORA's attester is keyless

A Sigstore identity of a CI workload names the workflow, which any repository may call when it
is public; section 7 now binds it, through fields of the certificate, to the repository (by
its immutable identifier) and the ref its role requires, and a builder's to the commit it built,
and states where an attester's identity is issued; section 9 gains the two rows. GORA's
repository became public on 2026-10-10, and section 8 makes its attester a Sigstore identity on
the public instance instead of a key that signs without a log. Corrected with it: ADR-018
(section 3 and Consequences), ADR-023 (section 3), ADR-026 sections 4 and 5, ADR-011 and
ADR-009 (the reason for `cosign`), ADR-021 (arm64 runners) and release-model (L5's enforcement,
section 13's merge queue). The Context and "Why not otherwise" stand: they give the reasons a
project with a private repository has for a key.

### 2026-10-11 — an rc tag's trailer is read back

Building the rc tag's message (M3 step 2) showed that GORA read nothing in Git to find which
digest a candidate is, which registration needs when it is asked again for a commit that
carries the artifact's rc tag (ADR-024 section 1): the message named the digest, and section 1
said the message is never read back. The message of an rc tag now ends with the trailer
`Gora-candidate`, which GORA reads, an index and not a ground for trust. Section 4 states the
message's form, and section 9 gains the row. Corrected with it: ADR-022 section 6.

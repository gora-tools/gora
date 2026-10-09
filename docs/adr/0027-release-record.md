# ADR-027: The Release Record — Verification Outcomes, Journal, Change Log and Release BOM

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-09
**Amends:** ADR-006, ADR-007, ADR-017, ADR-018, ADR-019, ADR-022, ADR-023, ADR-024, ADR-026

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
into `prod`: `gora status` would show the artifact verifying forever, which L24 forbids. The
release model's rule for every change stops here, and this ADR is the decision the maintainer
took on it.

The decisions were taken with the maintainer on 2026-10-09, in the spirit he gave for the
release record: as for microservices, the same approach as ADR-026. One fact has one place, a
process owns what it emits, and nothing is written by hand.

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
| That a digest passed GORA's evidence check, and the outcome of a stage's verifications | GORA's verification summaries on the digest (ADR-018 section 4; section 2 here) | GORA | as long as the registry keeps the digest |
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
- **Nothing in Git names a person** (ADR-023 section 6). Who asked and who approved stay in the
  host's record and the journal.

### 2. The outcome of a stage's verifications is attested

When the version a stage holds has finished its verifications with a result (ADR-024 section 3:
one red, or all green), GORA records the outcome as a signed SLSA Verification Summary
Attestation on the digest, the predicate it already writes when the evidence check lets a digest
in (ADR-018 section 4):

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{ "name": "ghcr.io/acme/cally-api", "digest": { "sha256": "<64 hex>" } }],
  "predicateType": "https://slsa.dev/verification_summary/v1",
  "predicate": {
    "verifier": { "id": "https://github.com/acme/cally-release/.github/workflows/gora-attest.yml@refs/heads/main" },
    "timeVerified": "2026-10-09T12:04:00Z",
    "resourceUri": "oci://ghcr.io/acme/cally-api@sha256:<64 hex>",
    "policy": {
      "uri": "git+https://github.com/acme/cally-release@<manifest commit>#stage-verification/qa/api",
      "digest": { "sha256": "<64 hex>" }
    },
    "verificationResult": "PASSED",
    "verifiedLevels": ["SLSA_BUILD_LEVEL_3"],
    "https://gora.tools/attestations/stage-verification/v1": {
      "stage": "qa",
      "artifact": "api",
      "version": "1.4.0-rc",
      "manifestCommit": "<40 hex>",
      "verifications": [
        { "name": "deploy", "state": "success", "reporter": "app/argocd-notifications", "createdAt": "2026-10-09T11:58:12Z" },
        { "name": "e2e", "state": "success", "reporter": "app/e2e-runner", "createdAt": "2026-10-09T12:03:40Z" }
      ]
    }
  }
}
```

- **One holding.** The holding is the stage's entry for the artifact as the manifest commit that
  set it left it (ADR-024 section 3). The policy URI names that commit, and its fragment names the
  check, `stage-verification/<stage>/<artifact>`, so a consumer that reads only the fields the
  specification defines tells this summary from the one of the evidence check, whose URI has no
  fragment (ADR-026 section 15); the policy digest is that of ADR-026 section 15 at that commit,
  the policy and the declaration that decided which verifications apply. The extension field, named
  by a URI as the specification allows, repeats the holding and records, for each verification
  the policy lists, the newest status GORA counted: its name, its state, its reporter and the time
  the host created it. A consumer that does not know the field ignores it.
- **The result.** `PASSED` when every verification is green, `FAILED` when one is red; on
  `FAILED`, `verifiedLevels` is `["FAILED"]`, as the specification asks, and otherwise the build
  level the evidence check verified. A holding finished only because its version is rejected is
  not attested: the rejection in Git keeps it finished (ADR-026 section 7).
- **Who writes it.** Only a run of the stage loop that holds GORA's write identity (L22) attests;
  a read, a dry run or a person's command computes from what exists and writes nothing. The run
  signs from a reusable workflow of the release repository on `main` that takes no input that
  becomes the attestation's content: it runs GORA, which computes the outcome itself, so another
  job that calls it can only make GORA attest what is true. Signing is keyless: through GitHub's
  own Sigstore instance where the plan offers it to a private repository (Enterprise Cloud),
  otherwise through the public instance, whose log publishes the signer's identity and so the
  repository's name, as a build's signatures already do.
- **Which signers count.** The policy names them, as it names builders (ADR-026 section 4):

  ```yaml
  attesters:
    gora-pipeline:
      issuer: https://token.actions.githubusercontent.com
      identity: https://github.com/acme/cally-release/.github/workflows/gora-attest.yml@refs/heads/main
  ```

  An attester is switched like a builder: the new one is added, then the old one removed. The
  removal is refused while a stage holds, or may roll back to, a holding whose only attestation
  the old one signed, and GORA's required status lists those holdings, as it lists what a
  tightening of evidence narrows. The server's signer is named here once the server is built
  (ADR-019 section 5); until then only the pipeline attests.
- **When.** On the first run of the loop after the holding finished, and again on any run where
  the newest counted status of one of its verifications is newer than the record the
  attestations of the holding hold for it, so a check run again before its status expires still
  decides.
- **How GORA reads it.** An attestation counts for a holding only when it is signed by an
  attester the policy names, its subject is the entry's digest, its extension names the entry's
  stage, artifact and setting commit, and its policy digest is that of the setting commit. For
  each verification of the holding, the result is the one with the newest creation time among
  its counted status on the host and its records in every counted attestation of the holding. A
  holding is finished and verified as ADR-024 section 3 says, from those results. While the
  statuses exist, this is what they say; once the host has deleted them, it is what they said.
- **What is not attested.** A holding that has not finished when its earlier statuses expire
  loses them: it shows as verifying until its reporters report again, and `gora status` names
  the verifications it lacks. A deployment's checks report within minutes or hours; the case is
  named, not handled.
- **Portable.** Where the registry serves OCI referrers (ADR-018, Consequences, names it an open
  question for GHCR), a CD system or an admission controller can require that a digest passed
  `staging` before it runs in `prod`, as it can require its provenance.
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

### 7. GORA's own release repository

GORA's own pipeline sets `GORA_AUDIT_LOG=-` and names its attesting workflow in its policy; it
signs through the public Sigstore instance, as its image signatures already do, since its plan
does not offer GitHub's instance to a private repository.
It keeps its repository's retention as it is: no decision of GORA rests on it. Its first state
file, the first promotion of `0.2.1-rc` into `dev` (ADR-026 section 16), is the first holding it
attests.

### 8. Invariants and their enforcement

Per ADR-016 rule 6. All are planned.

| Invariant | Enforced by | Class |
| --- | --- | --- |
| A holding finished with a result has an attestation of it; a newer counted status adds one | the stage loop | `runtime-check`, `test` |
| Only a run with GORA's write identity attests; a read or a dry run writes nothing | the stage loop | `test` |
| An attestation counts only when signed by a named attester and bound to the holding's digest, stage, artifact, setting commit and policy digest | attestation verification | `runtime-check`, `test` |
| For each verification, the result with the newest creation time decides | the eligibility computation | `test` |
| An attester is removed only when no holding a stage holds or may roll back to depends on it alone | GORA's required status | `runtime-check` |
| An expired required check of an open GORA pull request runs again | the stage loop | `runtime-check`, `test` |
| A process without a person writes its journal to the stream; a person's command to the file | the sink | `test` |
| The change log of a range is the commits that compute the version, in the stated form and order | the change log computation | `test` |
| A version tag's message carries its change log, written once; no change log enters `main` | registration, final tagging, the merge message | `runtime-check`, `test` |
| The same stage at the same commit gives the same Release BOM bytes | the BOM computation | `test` |
| An SBOM's form is checked, never its content | the evidence check | `runtime-check`, `test` |

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
section 2, and its "verified" reads the attested outcome once the statuses are gone.

Amendments, each recorded in the amended ADR:

- **ADR-006:** the registry adapter also attaches GORA's signed verification summaries to a
  digest: content of GORA's own, never an image or a tag.
- **ADR-007:** each run of the loop reads Git, the code host and the registry.
- **ADR-017:** "The journal has a path" (`-` is the stream), "The sink" (the stream for a process
  without a person, `GORA_AUDIT_LOG=-`, and
  what does not apply to a stream), "No rotation" (the file stays a person's), Consequences (the
  stream needs no writable `HOME`).
- **ADR-018:** section 2 (what GORA checks of an SBOM), section 4 (a second verification
  summary, the outcome of a stage's verifications), section 5 (the Release BOM is computed,
  CycloneDX, and its identity), Consequences (who signs the summaries, and from where).
- **ADR-019:** section 6 (the evidence a promotion counts is the statuses and GORA's attested
  record of them).
- **ADR-022:** section 5 (the change log is decided), section 6 (the rc tag's message carries
  it), Consequences (the release record is this ADR's).
- **ADR-023:** sections 2 and 4 (`gora changelog`, `gora bom`), section 6 (a promotion pull
  request's body carries the change log; the squash commit's body is the trailers alone),
  section 8 ("when the loop runs": Git, the code host and the registry; an expired required
  check runs again), section 11 (what the model needs of a host and a registry), Consequences
  (where a pipeline run's journal is kept).
- **ADR-024:** section 2 (the final tag's message), section 3 (verified, from the result with
  the newest creation time among a status and its attested records), section 7 (verified is no
  longer computed from statuses alone).
- **ADR-026:** section 4 (the policy names its `attesters`), section 13 (GORA's required status
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
- GitHub, artifact attestations (Sigstore instance for private repositories):
  https://docs.github.com/en/actions/concepts/security/artifact-attestations
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

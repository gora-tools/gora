# ADR-026: The Release Repository — Files, Writers and Schema

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-09
**Amends:** ADR-003, ADR-008, ADR-011, ADR-013, ADR-018, ADR-019, ADR-022, ADR-023, ADR-024
**Amended:** 2026-10-09 — the policy names its attesters (ADR-027)

---

## Context

ADR-019 section 1 and ADR-023 section 3 decided that a project is one release repository, the
single source of truth for one system, the same for one artifact or a hundred, and that each
artifact has its own directory there so a team owns its declaration. They left the files and
their schema open. ADR-022 to ADR-025 then decided what those files must carry: a candidate per
merge, a version per candidate, stages as an ordered list ending in `prod`, gates `auto` and
`approval`, rejections, rollbacks that hold the stage, and no dependencies between artifacts.

Today GORA's own release repository holds one file, `release-manifest.yaml` (schema v0.1,
ADR-003): one artifact, a `candidate` block and a release line, none of which the model keeps.
There is no `release-policy.yaml`. The manifest is read with PyYAML, a YAML 1.1 loader.

Open before registration and the stage loop can be built (release-model section 13):

- which files the release repository holds, who writes each, and where a stage's state lives;
- their format, their schema, and how both change over time;
- how a pipeline asks GORA whether a commit changed an artifact's inputs (ADR-022 section 4 says
  the workflow asks GORA; ADR-023 section 2 has no command for it);
- the form of a rejection (ADR-022 section 8 and ADR-023 section 10 left it open);
- how artifacts and stages are added and removed;
- whether a hundred services need groups beyond the artifact;
- how GORA itself guarantees that a change of policy is its owners' decision.

The audit journal under automatic writes, the change log and the Release BOM are the release
record, left to ADR-027. The SBOM a candidate carries is ADR-018 section 2's, one CycloneDX
document per platform manifest (ADR-021 section 3); this ADR only puts its version floor in the
policy.

Research (2026-10-06 to 2026-10-09, sources below): how Kargo, Argo CD (Git generators, Source
Hydrator, Image Updater), Flux, GitOps Promoter, Octopus/Codefresh, Akuity and Cloudogu lay out
configuration and state; what Kargo users report at 40 to 50 applications; how Google, Amazon and
Netflix release and roll back; how Renovate keeps many automated pull requests current; GitHub
code owners, commit signatures, merge queues and `GITHUB_TOKEN`; the Kubernetes API conventions
and deprecation policy; YAML 1.2, ruamel.yaml and PyYAML; RFC 8785. The decisions were taken with
the maintainer on 2026-10-09.

## Decision

### 1. Who writes what

```text
release-policy.yaml                  kind Policy      people (release owners), pull requests
artifacts/<artifact>/artifact.yaml   kind Artifact    people (the artifact's team), pull requests
state/<stage>/<artifact>.yaml        kind StageState  GORA only, through its pull requests
rejections/<artifact>/<X.Y.Z>.yaml   kind Rejection   GORA only, through its pull requests
.github/CODEOWNERS                   host file        people (release owners), pull requests
```

- **Policy and state are written by different hands, without exception.** The policy and the
  declarations of artifacts are decisions of people, and GORA checks that their owners decided each
  one (L25, section 13). The state of the stages and the rejections follow from GORA's
  operations, and only GORA writes them (L16). GORA never writes a policy or a declaration; it
  validates every change to one on its required status, so a change that breaks the schema or a
  law never merges (L23). In the map's words: a **law** is in GORA's code, **policy** is the
  project's declaration inside the laws (`release-policy.yaml` and the declarations), and
  **state** is what GORA writes.
- This is the split every surveyed tool makes between what people author and what a machine
  writes: GitOps Promoter's DRY branch and read-only environment branches, the hydrated paths
  only Argo CD's Source Hydrator writes, Kargo's sources and builds. Google keeps configuration
  in source control under review and gates who may release by access control; here the review is
  the owners' decision and the access control is the policy.
- **The manifest is the `state/` tree.** `release-manifest.yaml` is retired (section 16).
- **No release file is ever an input** (L8, section 9).
- **Nothing else is a release file.** What a CD system renders from the state is its own output,
  never a source of truth. On another code host, that host's own code-owner file takes the place
  of `.github/CODEOWNERS`, as its connector names.

### 2. The layout, and why there are no groups

- **The state is laid out by stage.** A CD system consumes one stage: Argo CD's Git file
  generator reads `state/prod/*.yaml` and makes one Application per artifact with the digest as a
  parameter, with no image updater writing back; a deploy-on-merge pipeline reads one file
  (`gora digest`). A platform state is the `state/prod/` tree at a tagged commit; `gora history
  prod` is the history of that path; a rollback of kind B changes files of one directory.
- **One file per artifact and stage.** Promotions of different artifacts never change the same file,
  so they do not conflict with each other when the host brings a branch up to date in strict mode or
  tests it in a merge queue, which drops a conflicting pull request from the queue. Argo CD Image
  Updater writes one file per application for the same reason; Renovate shows what many automated
  pull requests against one file cost. The history of one artifact is
  `git log -- 'state/*/api.yaml'`.
- **Declarations are laid out by artifact.** Organisation decides ownership, and ownership is a
  directory a team owns, as Flux gives teams `apps/` and Kargo lays out a monorepo "by
  application and then by stage". The directory holds `artifact.yaml` only.
- **Stages are directories on `main`**, never branches (L1) and never cluster resources (ADR-019
  section 5). A branch per environment is the anti-pattern Octopus, Akuity and Flux name.
- **`artifacts/` is flat, and GORA has no groups.** A group in the path becomes part of an
  artifact's identity (Argo CD's Git generators make a directory name an application's name), so
  moving a service to another team would rename it and break its history. A group field with no
  behaviour is a label GORA must keep consistent for nothing; one that changed the order of
  promotion or a rollback would be a dependency, which L21 forbids. The needs behind groups are
  met without one: ownership by the code owners of `artifacts/<artifact>/` and the host's teams;
  a team's own approvals by the artifact's approvers (section 5); views by `gora status
  --artifact PATTERN` (section 13). Kargo users at 40 to 50 applications asked for views and for
  batch promotion; GORA gives the first and refuses the second (L20). A name prefix such as `db-`
  or `ml-` is a team's convention; GORA gives it no meaning.

### 3. The file format

Every release file GORA reads is one YAML document whose top-level fields are, in this order:

```yaml
apiVersion: gora.tools/v1alpha1
kind: Policy | Artifact | StageState | Rejection
metadata: { ... }          # absent in a Policy, which is one per repository
spec: { ... }
writtenBy: 0.3.0           # in a StageState and a Rejection only: the GORA version that wrote it
```

- **The API group is the project's own domain**, `gora.tools`, registered by the maintainer on
  2026-10-07, as the Kubernetes API conventions ask: a DNS name its author owns. Once files in
  the wild use it, it never changes.
- **The version is `v1alpha1`** until the first external project connects; then `v1`
  (section 12).
- **Path and content agree.** `release-policy.yaml` is at the root; the `Artifact` named `api`
  is `artifacts/api/artifact.yaml`; the `StageState` of `api` in `qa` is `state/qa/api.yaml`; the
  `Rejection` of `api` version `1.1.0` is `rejections/api/1.1.0.yaml`. A file whose path and
  content disagree is refused, and so is any other file under `artifacts/`, `state/` or
  `rejections/`, beyond the files of a retiring artifact or a removed stage and the rejections of
  a retired artifact (section 14).
- **Names.** Artifact and stage names follow the stage grammar of ADR-003: an RFC 1123 label,
  letter first, at most 63 characters. They share one namespace: no artifact shares a name with a
  stage, so a version tag never looks like a state tag, and a name the history has used for
  either is never used again by either, beyond a declaration restored while its retirement is
  open (section 14). The last stage is `prod`. A project is named by its release repository's
  path (ADR-023 section 3); no file repeats it.
- **Other identifiers** are checked against a grammar too, as ADR-003 asks of every identifier:
  the names of verifications and builders follow the same RFC 1123 label grammar; a principal is
  `team/<slug>` or `user/<login>`, a team or an account of the code host, in the host's own
  grammar for those names, and `app/<slug>`, an App of the code host, only as a reporter; a
  repository is its path on the host, `github.com/<owner>/<name>`, in lower case; an input is a
  glob of section 9. An email address is never a principal (ADR-023 section 7).

### 4. Policy

One per project, owned by the release owners:

```yaml
apiVersion: gora.tools/v1alpha1
kind: Policy
spec:
  stages:
    - name: dev
      gate: auto
      verify: [deploy, integration]
    - name: qa
      gate: approval
      approvers: { team/qa: 1 }
      verify: [deploy, e2e]
    - name: staging
      gate: approval
      approvers: { team/qa: 1 }
      verify: [deploy, smoke]
    - name: prod
      gate: approval
      approvers: { team/release-owners: 1 }
      verify: [deploy, smoke]
  reporters:
    deploy: app/argocd-notifications
    integration: app/cally-ci
    e2e: app/e2e-runner
    contract: app/cally-ci
    smoke: app/cally-ci
  builders:
    cally-ci:
      issuer: https://token.actions.githubusercontent.com
      identity: https://github.com/acme/cally-ci/.github/workflows/build.yml@refs/heads/main
  requests:
    promote:  { dev: [team/developers], qa: [team/qa], staging: [team/qa],
                prod: [team/release-owners] }
    rollback: { dev: [team/developers, team/release-owners],
                qa: [team/qa, team/release-owners],
                staging: [team/qa, team/release-owners], prod: [team/release-owners] }
    reject:   [team/qa, team/release-owners]
    migrate:  [team/release-owners]
  evidence:
    sbom: { format: cyclonedx, minVersion: "1.6" }
  branchAgeDays: 30
```

- **The release owners are not a field.** They are the code owners of `release-policy.yaml` and
  `.github/CODEOWNERS`, which `.github/CODEOWNERS` names, the same for both (section 13); a second
  statement of them could contradict the first.
- **`stages`** is the ordered promotion graph. Each stage has a gate, `auto` or `approval`, and
  at least one verification. `approvers` maps a group to the number of reviews it must give
  (ADR-022 section 7, ADR-023 section 8); a number is at least 1 and at most the group's members.
  A gate is met when one review can be assigned to each required place, each review from a
  distinct person who belongs to the group of its place. An `auto` stage names no approvers, and
  `prod`, when its gate is `approval`, names its own; a pipeline with no person in it has every
  gate `auto`, `prod` included (ADR-024 section 5).
- **`reporters`** name, for each verification, the one identity whose commit status GORA counts
  (ADR-024 section 7): a dedicated App, never GORA's own identity and never a token any workflow
  of the repository can use. Every verification named in the policy or a declaration has one.
- **`builders`** are the trusted signers of ADR-018 section 3, each named, with an issuer and an
  exact identity, no pattern. The release owners decide whom to trust; a declaration chooses
  among them.

  *Amended 2026-10-09 (ADR-027 section 2):* the policy also has `attesters`, of the same shape:
  the identities whose summaries of a stage's verifications GORA counts, the reusable workflow
  that signs them in the release repository's pipeline first.
- **`requests`** name who may ask for each action, per stage for `promote` and `rollback`
  (ADR-023 section 7), and who may ask for a migration of the state (section 12). Every stage
  names who may ask for `promote` and for `rollback`, so a stage held after a rollback always has
  someone who may lift the hold (ADR-024 section 6). Nobody has a right the policy does not name,
  the release owners included: they decide every stage through the policy they own, and act in a
  stage where the policy names them, as the example does for every rollback.
- **`evidence`** sets the floor of the SBOM version: CycloneDX 1.6 at least (ADR-018 section 2);
  a policy may raise it and never lower it. The rest of the required evidence, its predicate
  types (ADR-018 section 2) and the provenance fields a verifier checks (ADR-018 section 3), is
  the same for every project and is not a field, so no policy can weaken it (L23).
- **`branchAgeDays`** is the age past which `gora branches` reports an unmerged branch (L6).
- There is no field for a schedule, a window, a freeze, a batch, a dependency or a kind of
  artifact; an unknown field is refused (L20, L21, section 10).

### 5. Artifact

One per deployable unit, owned by its team:

```yaml
apiVersion: gora.tools/v1alpha1
kind: Artifact
metadata:
  name: api
spec:
  image: ghcr.io/acme/cally-api
  source:
    repository: github.com/acme/cally-api
    tagPrefix: ""
    inputs: ["src/**", "pom.xml", "Dockerfile"]
  builders: [cally-ci]
  initialVersion: 0.1.0-rc
  stable: false
  stages:
    qa:
      approvers: { team/api-qa: 1 }
      verify: [contract]
```

- **`source.repository`** is the code repository the artifact is built from; with one repository
  for code and release files, it is the release repository itself. It may change only to a
  repository that holds every version tag of the artifact, as when a repository is moved, and
  where no other artifact has the same tag prefix.
- **`image`** is the image repository the artifact is pushed to. It may change only to a
  repository that holds every digest of the artifact a stage holds or may roll back to, as when a
  registry is moved and the digests are copied with it; from then on GORA writes the new image
  into every entry it writes.
- **`source.tagPrefix`** is empty for the first artifact ever declared for a source repository and
  the artifact's name followed by `/` for every later one (`api/v1.0.2`). The first artifact keeps
  its empty prefix when a second is declared, so its tags never change. The prefix never changes
  once a version tag exists. One pull request declares at most one artifact for a source repository
  that has none yet, so "first" is never ambiguous.
- **`builders`** names one or more of the policy's builders; a candidate's evidence must be
  signed by one of them (ADR-018 section 3). A builder is switched by adding the new one, then
  removing the old one once no stage holds a digest it signed and none waits in a stage. A
  builder may serve many artifacts, so what keeps a CI from registering into another artifact
  (ADR-023 section 3) is the whole of the check: a builder the artifact names, and provenance
  whose source repository is the artifact's and whose subject is a digest in the artifact's
  image.
- **`initialVersion`** is the first candidate's version (ADR-024 section 1), `X.Y.Z-rc`, ordered
  after every version tag of the artifact that exists (release-model section 9). It may change
  until the artifact's first candidate is registered, and never after.
- **`stable`** is the declaration that the major version leaves `0` (ADR-024 section 1): once
  `true`, the next candidate is at least `1.0.0-rc`, and it never goes back to `false`. A new
  declaration with `stable: true` has an `initialVersion` of at least `1.0.0-rc`.
- **`stages`** adds to the policy for this artifact, stage by stage, and never takes away:
  - `verify` adds verifications to the stage's list;
  - `approvers`, on an `approval` stage only, adds groups; for a group the stage also names, the
    higher of the two numbers counts.

  So a team's own QA can approve its services in addition to what the stage asks, and no
  declaration weakens a gate the release owners set. An `approval` stage other than `prod` may
  leave its approvers to the declarations only when every declaration names some for it. A
  declaration never names a stage the policy does not have and never changes a gate.

### 6. StageState

Written by GORA only, one per artifact and stage the artifact has entered:

```yaml
# yaml-language-server: $schema=https://gora.tools/schemas/v1alpha1/stage-state.json
apiVersion: gora.tools/v1alpha1
kind: StageState
metadata:
  stage: qa
  artifact: api
spec:
  image: ghcr.io/acme/cally-api
  digest: sha256:<64 hex>
  version: 1.4.0-rc
  sourceCommit: <40 or 64 hex>
  evidence:
    vsa: sha256:<64 hex>
    policy: sha256:<64 hex>
    result: passed
    time: "2026-10-09T12:00:00Z"
writtenBy: 0.3.0
```

- **`image` and `digest`** together are the pull-safe reference a CD system needs; the file is
  enough to deploy from.
- **`version`** is the rc version in every stage before `prod` and the final version in `prod`
  (L12). **`sourceCommit`** is the commit the digest was built from (L9, L10).
- **`evidence`** is the pointer of ADR-018 section 4: the VSA's digest, the policy digest of
  section 15, the result and the time (RFC 3339, UTC) of the evidence check that let this digest
  in. Every StageState has it: GORA writes an entry only after a check, and every write into a
  stage checks, a rollback included, since a rollback is a forward promotion (L18, ADR-018
  section 4).
- **`writtenBy`** is the GORA version that wrote the file, so an older GORA can say which
  version it needs (section 12).
- **The content of an entry is its `spec`.** An entry is unchanged while its content is,
  whatever schema version or `writtenBy` the file has (ADR-024 sections 3 and 6).
- **Held is computed, not written.** An artifact is held in a stage while the newest change to
  the content of its entry there set an older version than the one before it (ADR-024 section
  6). No field records it, so nothing can contradict the history.

### 7. Rejection

Written by GORA only, from `gora reject` or a rollback (ADR-023 section 10):

```yaml
# yaml-language-server: $schema=https://gora.tools/schemas/v1alpha1/rejection.json
apiVersion: gora.tools/v1alpha1
kind: Rejection
metadata:
  artifact: api
  version: 1.1.0
spec:
  digest: sha256:<64 hex>
  reason: login fails on Safari
writtenBy: 0.3.0
```

- **One file per digest.** The rc and the final version of a digest are one build, so the file
  is named by `X.Y.Z` and rejects both names (ADR-023 section 10). Versions are never reused
  (L11), so the name is unique.
- **`reason`** is the text the person gave. Who asked is not in Git (ADR-023 section 6): the
  host's record of the pull request and the audit journal keep it.
- **A rejection's content never changes and the file is never removed** (L16). A rejected
  version never moves forward and is never a rollback target (L12, L18).
- **A rejection on its own** is a pull request from `gora/reject/<artifact>/<version>` that adds
  this file; a rollback adds, in its own pull request, the files of the versions it rejects that
  are not rejected yet, extending the rule ADR-023 section 6 sets for a rejection on its own, and
  GORA closes an open rejection pull request of a version the rollback rejects; a rejection of a
  version an open rollback rejects finds that rollback and opens nothing (ADR-023 section 6). So
  two pull requests never add the same file.

### 8. What each GORA pull request changes

| Pull request and branch (ADR-023 section 6) | Changes | Merges when (L15) |
| --- | --- | --- |
| Promotion, `gora/promote/<stage>/<artifact>` | writes `state/<stage>/<artifact>.yaml` | the stage's gate is met |
| Rollback kind A, `gora/promote/<stage>/<artifact>` | writes that file; adds the rejection of the version it leaves | the checks pass; the request decided |
| Rollback kind B, `gora/rollback/prod` | writes every file it moves; adds the rejections it names | the checks pass; the request decided |
| Rejection, `gora/reject/<artifact>/<version>` | adds `rejections/<artifact>/<X.Y.Z>.yaml` | the checks pass; the request decided |
| Retirement, `gora/retire/<name>` | deletes the files of a retiring artifact or a removed stage | the checks pass; people decided (section 14) |
| Migration, `gora/migrate` | rewrites GORA's files in the policy's version, content unchanged | the checks pass; the request decided |

- **A promotion writes the stage before's content** (L12): the same digest, version (`X.Y.Z`
  without `-rc` when the stage is `prod`) and source commit, the declaration's image, and the
  evidence of the check it made. The first stage's entry is written from the registered candidate.
- **A platform state** is a first-parent commit of `main` that adds, deletes or changes the
  content of an entry under `state/prod/` (L17). A migration changes no content and is not one.
- **Titles** (ADR-024 section 8, extended): `retire(<name>): <artifact | stage>` and
  `migrate: <from> to <to>`.
- **Trailers**, under the rules of ADR-023 section 6: `Gora-promotion: <stage> <artifact>
  <version> <digest>` for every entry a pull request sets, `Gora-rejection: <artifact> <version>
  <digest>`, `Gora-retirement: <name>` and `Gora-migration: <from> <to>`.
- **Open pull requests** (L14): a retirement counts as the open pull request of every pair whose
  file it deletes. A migration counts for none: it changes no content, GORA recomputes it when
  `main` moves, and a promotion merged meanwhile is carried into it.

### 9. Inputs and `gora changes`

- **Glob grammar.** An input is a path relative to the source repository's root, matched
  case-sensitively: `*` within one path component, `**` across components, `?` one character,
  `[...]` a class. No negation, no `..`, no leading `/`, no backslash.
- **No release file is ever an input.** Where the source repository is the release repository,
  `release-policy.yaml`, `.github/CODEOWNERS`, `artifacts/**`, `state/**` and `rejections/**` are
  removed from every input set before matching, and a glob whose fixed leading components name
  one of them is refused. A commit that changes only release files, a merged promotion included,
  never creates a candidate (L8).
- **`gora changes [--source REPOSITORY] [COMMIT]`** is a read command: it lists the artifacts
  declared with that source repository whose inputs at COMMIT differ from those of their newest
  registered candidate. Defaults: the repository of the current clone and its head;
  `--output json` gives the list a pipeline builds. API:
  `GET /v1/projects/{p}/changes?source={repository}&commit={sha}`.
- **An artifact with no registered candidate** is listed for every first-parent commit of its
  source repository merged after its declaration was merged (the host's merge times), other than
  a commit that changes only release files. The first build after its declaration is its first
  candidate (ADR-022 section 4, ADR-024 section 1).
- It refuses a commit off the first-parent history of `main` (L1, L5). It reads the declarations
  at the head of the release repository's `main`; `gora register` checks L8 again before it
  registers, since a declaration may change between the two.

### 10. Strict parsing and canonical writing

- **YAML 1.2, never 1.1.** GORA reads release files with ruamel.yaml and a resolver limited to
  the YAML 1.2 core schema (null, boolean, integer, float, string), without the resolvers that
  ruamel.yaml's safe loader adds beyond it: timestamps, `0b` integers, `_` in numbers, the merge
  key `<<` and the value key `=`. So `no` and `on` are strings, `010` is ten, and `1:30`,
  `1_000` and `2026-10-09T12:00:00Z` are strings, which the model validates. PyYAML is replaced.
- **Refused:** a duplicate key; a mapping key that is not a string; a directive (`%YAML`,
  `%TAG`); an anchor, an alias or a merge key; an explicit tag; more than one document; a
  non-finite float, an integer outside ±(2^53 − 1) or a lone surrogate (RFC 8785 cannot carry
  them); a file that is not UTF-8; a file larger than 256 KiB; an unknown field at any level.
- **Errors name the file, the field path and the rule, never the value** (ADR-003, "Identifier
  grammar").
- **GORA writes canonically**: the top-level order of section 3, the schema's order inside,
  two-space indent, block style, one trailing newline, the schema modeline first. The same
  content written by the same GORA version gives the same bytes.
- **GORA never edits a file people write**, so comments and layout in the policy and the
  declarations are never lost.

### 11. The published schema

- One JSON Schema per kind, draft 2020-12, with `$schema` and `$id`
  (`https://gora.tools/schemas/v1alpha1/policy.json`, `artifact.json`, `stage-state.json`,
  `rejection.json`), generated from the models and committed under `contracts/schemas/v1alpha1/`. A
  test fails when a committed schema differs from the generated one, as for v0.1.
- The models are the gate; the schema is their published form for editors and other tools.
  Until `gora.tools` serves the schemas, the committed files are the copy of record.

### 12. Schema versions and `gora migrate`

- **A schema version never changes.** Any change to a schema, an optional field added included,
  is a new schema version: an older reader refuses a field it does not know (section 10). Alpha
  versions follow the same rule: a change to `v1alpha1` is `v1alpha2`.
- **Every version of GORA supports a set of schema versions: it reads and writes every one of
  them**, and converts between each and the newest without loss, both ways, as the Kubernetes API
  conventions ask of conversions. Files of different schema versions live side by side, so
  nothing has to migrate at once. A file in a schema version the running GORA does not support
  is refused, naming the schema version and, for a file GORA wrote, its `writtenBy`.
- **The version before.** For a version of GORA, the version before is the final version that
  GORA's own `prod` held just before it (ADR-024 section 9). A version of GORA supports every
  schema version the version before supports, beyond one whose drop the notes of the version
  before announced; from the announcement on, `gora connect check` reports every file in that
  schema version.
- **The policy's schema version decides what GORA writes.** Every state and rejection file GORA
  writes is in the `apiVersion` of the policy at the base it computes from. GORA's required status
  refuses a policy in a schema version that the running GORA does not support, or that the
  version before it does not support (the first version of GORA with this layout has no version
  before it to ask), a policy older than any declaration and a declaration newer than the policy.
  So people move the policy first and the declarations after it, and the other way round to go
  back. A GORA that does not support the policy's schema version writes nothing and says which
  one it needs.
- **What this guarantees.** GORA can be rolled back to the version before without converting a
  file: that version reads and writes every one. A rollback further back, or past a version
  before that is rejected, goes one step at a time: people move the policy and the declarations
  to a schema version the target supports, and `gora migrate` converts GORA's files to it, run by
  the version in `prod` or, when that one cannot act, by a version of GORA that supports both
  schema versions, named in the workflow as ADR-024 section 9 names the digest of a rollback.
- **People migrate their files.** `gora migrate --print [--to VERSION]` writes to standard output
  each policy or declaration not in the target schema version (the policy's, or VERSION),
  converted to it; the owners commit it in a pull request (L25).
- **GORA migrates its files.** `gora migrate` (with `--dry-run`; API `POST
  /v1/projects/{p}/state:migrate`) rewrites every state and rejection file not in the policy's
  schema version, newer or older, the files of a retiring artifact or a removed stage included, in
  one pull request from `gora/migrate`, asked for by a person in `requests.migrate`. The request is
  the decision, and the pull request merges once the other required checks pass (L15). With nothing
  to convert, it opens nothing and says so.
- **A migration changes no content and blocks nothing.** No entry's or rejection's content
  changes, and the required check of L16 compares each parsed `spec` before and after: so it is
  not a platform state, ends no hold and resets no verification. The stage loop and every
  request go on while it is open.

### 13. Owners, reviews and views

| Path | Written by | Code owners |
| --- | --- | --- |
| `release-policy.yaml`, `.github/CODEOWNERS` | people, pull requests | the release owners |
| `artifacts/<artifact>/` | people, pull requests | the artifact's team |
| `state/`, `rejections/` | GORA only (L16) | none |

- **The owners decide every change to policy, and GORA checks it (L25).** GORA's required status on
  a pull request that changes a release file people write (`release-policy.yaml`,
  `.github/CODEOWNERS`, anything under `artifacts/`) is green only when every such path it changes
  is decided by its owners; a renamed file counts as both of its paths, and other files in the pull
  request are not GORA's to decide. The owners of a path are those `.github/CODEOWNERS` names on the
  base branch (GitHub also reads the base's file for a pull request); a path with no owner there,
  such as a new artifact's directory, is decided by the release owners. A path is decided when an
  owner reviewed exactly that change and no owner asks for changes:
  - **the review:** an owner's approval, or, from the owner who opened the pull request, a
    comment review whose body is exactly `/approve`, since GitHub does not let an author approve;
    no other comment of the author counts, so a remark on a line never decides. A dismissed
    review never counts;
  - **exactly that change:** a review holds while the path's content at the pull request's head
    and at its merge base are what they were at the commit it reviewed, as an approval binds to a
    promotion (ADR-023 section 8). So a later commit that leaves the path's change different, by
    anyone, ends it, and one that only brings the branch up to date, such as the host's "Update
    branch", does not;
  - **no owner asks for changes:** no owner of the path has, as their latest review, a request for
    changes; a request ends only with a later review of the same owner, and a dismissal does not
    end it.

  Owners and their teams' members are read at each evaluation, on a merge group as well (ADR-023
  section 12). The decision is an act on the host, authenticated by it and bound to the change. Who
  wrote the commits does not matter, so commits need no signature, and a commit an agent made for a
  person is decided like any other. GORA cannot tell who used an owner's credential: whoever holds
  it decides as the owner. That is an accepted risk, as ADR-016 rule 7 accepts that its check cannot
  tell who added a label; a project holds it by its own rule that no tool acting with an owner's
  credential gives a review, as this repository's CLAUDE.md does. A team decides its own
  declaration, the release owners decide the policy and who owns what, and a one-person project
  decides alone. GORA's guarantee does not rest on the host's review settings, and the connect
  condition of release-model section 9 stands: the host's review rules never block a GORA pull
  request.
- **L25 holds from the first commit whose `.github/CODEOWNERS` meets the rules below.** The pull
  request that adds the file has no owners on its base: it is decided as the repository's own
  rules say, and `gora connect check` then checks the file.
- **The code-owner file is a condition of connecting.** `.github/CODEOWNERS` is the only one
  (GitHub reads `.github/`, then the root, then `docs/`, and uses the first it finds); its
  patterns are anchored (`/state/`, `/rejections/`, `/artifacts/api/`), since an unanchored one
  matches the name anywhere; `/release-policy.yaml` and `/.github/CODEOWNERS` have the same
  owners, the release owners; every directory under `artifacts/` has an owner; `/state/` and
  `/rejections/` come last with no owner, since the last matching pattern wins; every owner is a
  visible team or an account with write access, never an email address; and the host reports no
  error in it (on GitHub, the API that lists CODEOWNERS errors). `gora connect check` refuses
  the file otherwise, and GORA's required status refuses a pull request that would break one of
  these rules.
- **Code owners decide who may change policy; approvers decide promotions.** A code-owner rule
  cannot require two groups, which an `approval` gate can.
- **Policy is validated by the GORA that runs from `main`**, never by code from the pull request,
  as ADR-016 rule 7 does for the files that define the gate: the release repository's pipeline
  definition on `main`, or GORA's server, after each event of the pull request, a review
  included, and on the periodic reconciliation (ADR-023 section 8). On GitHub Actions a review
  event runs the pull request's own workflow, which has no key, so the run from `main` follows
  it (`workflow_run`) or the reconciliation catches it; in GORA's own repository, the
  version `prod` holds (ADR-024 section 9). Its required status refuses a change that breaks the
  schema, a law or section 14, and lists, for a change that tightens evidence, what it narrows
  (section 14).

  *Amended 2026-10-09 (ADR-027 section 2):* it also refuses the removal of an attester while a
  stage holds, or may roll back to, a holding whose only attestation that attester signed, and
  lists those holdings.
- **Views.** `gora status [STAGE] [--artifact PATTERN]` and the API filter artifacts by a glob on
  their name (`gora status prod --artifact 'db-*'`); this is a view, not a group.

### 14. Changing the policy

- **Adding an artifact** is one pull request that adds its declaration and its line in
  `.github/CODEOWNERS`, decided by the release owners (L25); GORA takes it from the next run of
  the stage loop. A name the history has used for an artifact or a stage is refused.
- **Retiring an artifact** takes two pull requests:
  1. People delete `artifacts/<artifact>/`, a change its owners decide (L25). When `prod` holds the
     artifact and `prod`'s gate is `approval`, it also needs the reviews of that gate for the
     artifact as they stand on the base, counted as L25 counts a review (an approval, or the
     author's `/approve` when the author belongs to the group) and bound to the change, since it
     takes a service out of production. It is refused while a GORA rollback pull request that
     carries the artifact is open.
  2. From that merge on, the artifact is **retiring**: GORA closes its open pull requests and
     registers, promotes, rolls back and rejects nothing for it; its state files stay valid without
     a declaration, changed only by `gora/retire/<artifact>`, which deletes them in every stage that
     is not removed and which GORA opens and opens again whenever none is open, and by a migration.
     Restoring the same declaration, decided as in step 1, cancels the retirement while that pull
     request is not merged. When it merges, the artifact is retired.

  Its version tags and rejections stay; its name is never used again. Renaming is retiring one
  artifact and declaring another.
- **Adding a stage** inserts it anywhere before `prod`, under a name never used before. It starts
  empty and is offered only versions not older than the one the stage before holds at the commit
  that added it; a new first stage is offered registered candidates not older than the one the
  old first stage holds then. So it does not receive the artifact's whole past, and a version
  that waits in the stage before can move on.
- **Removing a stage** other than `prod` takes two pull requests, as a retirement does: the release
  owners remove it from the policy, which is refused while a declaration names it, a rollback into
  it is open or a retirement is open; from that merge on, the stage is removed: GORA closes the open
  promotions into it; its files stay valid, changed only by `gora/retire/<stage>`, which deletes
  them, a retiring artifact's included, and by a migration; and the stage after it is offered what
  the stage before the removed one has verified, or, when the removed stage was the first,
  registered candidates. Renaming or reordering a stage is removing it and adding another.
- **Declarations first, in a stated order.** A policy change that a declaration would contradict
  (removing a stage it names, making that stage `auto` while it names approvers there, removing a
  builder or a reporter it uses) is refused; the declarations change first. To add an `approval`
  stage whose approvers come from the declarations, the policy names approvers for it first, the
  declarations add theirs, and the policy then leaves them to the declarations; removing such a
  stage takes the same steps backwards.
- **Gates, approvers, verifications, reporters and requests** may change otherwise at any time;
  the stage loop applies the change to open pull requests on its next run. Whether a version is
  verified in a stage is computed with the verifications and their reporters as the policy and
  the declaration name them at the commit that set the entry (ADR-024 section 3), so a change
  never alters what was verified before it.
- **Evidence** is checked against the current policy at every write into a stage, a rollback
  included (ADR-018 section 4, L18). A policy change that tightens it (a higher SBOM floor, a
  builder removed from an artifact) narrows what may enter a stage: GORA's required status on
  that change lists, per artifact and stage, the versions the stage held before and the versions
  that wait there that the new policy would refuse, in its check output and a comment on the
  pull request, so the release owners decide with the consequence in front of them.
- **A rollback of kind B** leaves an artifact the named state does not hold, or one retiring or
  retired since, as it is; naming it in `--reject` is refused.

### 15. The policy digest

- The policy digest a decision records (ADR-018 section 3) is the SHA-256 of the RFC 8785 (JSON
  Canonicalization Scheme) serialisation of the array `[policy, artifact]`, the two documents
  that decided, as parsed (section 10), each in its own `apiVersion`, with no default filled in.
- In a VSA it is the policy field `{uri, digest}`, the URI naming the release repository and the
  commit the documents were read at.
- The same documents give the same digest whatever their comments or layout, so a decision can
  be checked again against exactly the policy that made it.

### 16. GORA's own release repository

GORA is its own release repository and its one artifact, `gora`
(`ghcr.io/zuwarskejes-labs/gora`), with an empty tag prefix and `initialVersion: 0.2.1-rc`
(ADR-024 section 9). Its stages are `dev` (`auto`), `staging` (`approval`) and `prod`
(`approval`); the maintainer is the release owner, the approver and the one who may ask. The
reporter of its verifications is a second App, `app/gora-verify`, whose key is only in the jobs
that run those checks, never GORA's own App, whose status is the gate.

- **It starts the layout empty.** The pull request that adds `release-policy.yaml`,
  `artifacts/gora/artifact.yaml` and `.github/CODEOWNERS` also deletes `release-manifest.yaml`,
  whose history stays in Git. No entry is carried over: the v0.1 entries record versions GORA
  never registered (ADR-024 section 9), and the first state file is the first promotion of
  `0.2.1-rc` into `dev`, with its evidence. GORA's code for v0.1 and the published v0.1 schema
  are removed with it.
- **It meets L25 as any project does.** The maintainer owns every path of the policy and opens every
  pull request to it, the ones agents prepare included, so each change is decided by the
  maintainer's own `/approve` review of it. That step is new: today the maintainer reads the change
  and merges it by hand; once the layout is on `main`, the review comes before the merge of every
  change to policy. No commit needs a signature, and an agent's commits are decided like the
  maintainer's, by that review. CLAUDE.md ("Pull requests") holds the rule that an agent never
  submits a review; GORA cannot check it (section 13).

### 17. Invariants and their enforcement

Per ADR-016 rule 6. All are planned.

| Invariant | Enforced by | Class |
| --- | --- | --- |
| The refusals of section 10; core-schema types only, no timestamp | the loader and the models | `runtime-check`, `test` |
| Path and content agree; no other file under `artifacts/`, `state/`, `rejections/` beyond those of section 14; grammars; one namespace of names, never reused | the loader; GORA's required status | `runtime-check`, `test` |
| Policy limits of section 4 (every stage names who may ask for `promote` and `rollback`); declaration limits of section 5 (adds only; approvers only on `approval` stages; a non-empty set of approver groups per artifact and `approval` stage; numbers within a group's members) | policy validation | `runtime-check`, `test` |
| Tag prefix rule; prefix fixed once tagged; repository changed only to one that holds the artifact's tags; image changed only to one that holds its digests; `initialVersion` fixed once a candidate is registered; `stable` never back to `false`; builder switch | policy validation | `runtime-check`, `test` |
| No release file is an input; a glob naming one is refused | input matching | `runtime-check`, `test` |
| Canonical bytes; every file GORA writes is in the policy's schema version; that schema version supported by the running GORA and the version before it; versions of the policy and the declarations in order; conversions lose nothing both ways | the writer; GORA's required status; conversion tests | `runtime-check`, `test` |
| A migration changes no content | `gora migrate`; the required check of L16 compares each `spec` | `runtime-check`, `test` |
| Committed schemas equal the generated ones | the drift test | `test` |
| The policy digest is stable for the same documents | RFC 8785 serialisation | `test` |
| `state/` and `rejections/` change only from `gora/*`; a rejection's content never changes and it is never removed (L16) | a required check | `runtime-check` |
| Section 14: retiring and removed stages, declarations first, what a tightening narrows | GORA's required status on policy changes | `runtime-check` |
| Every change to policy is its owners' decision (L25): the review, its binding to the change, no open request for changes | GORA's required status | `runtime-check` |
| The code-owner file of section 13 | `gora connect check`; GORA's required status | `runtime-check` |

## Why not otherwise

- **One state file for all artifacts.** Every promotion would change it, and each merge would put
  every other open promotion in conflict.
- **The state inside each artifact's directory** (`artifacts/api/state/qa.yaml`). A CD system
  could not take one stage by path, a platform state would need every diff parsed, and files
  people own and files GORA writes would share one owned directory.
- **A branch or a cluster resource per stage.** Against L1 and ADR-019 section 5, and the
  anti-pattern the surveyed tools name.
- **Groups of artifacts** (`artifacts/<group>/<artifact>/`, or a `group` field). See section 2.
- **A declaration that replaces a stage's approvers or verifications.** A team could remove the
  release owners' or QA's review from its own service in a change only that team decides; adding
  only keeps every stage's floor where the release owners set it, with no stage treated apart.
- **Builders declared by each team.** A team could trust any signer for its own service; the
  trusted signers stay with the release owners (ADR-018 section 3), and a team chooses among them.
- **GORA writing policy files** (a migration of the policy, a retirement that deletes a
  declaration). An approval binds to a change people made; a file GORA rewrote whenever `main`
  moves would leave people approving GORA's text, or a reviewed change altered after its review.
- **The host's code-owner review as the guarantee.** It is a setting GORA does not control, it
  dismisses approvals only when set to and when the diff changes, and an author cannot approve
  their own pull request, so a one-person project could never meet it. GORA's own check (L25)
  holds the same guarantee on every host.
- **Commit signatures as the owner's decision.** A signature says whose key made a commit, not
  who decided the change: an agent or a co-author commits for a person, a commit pushed after
  the decision carries no decision, and every committer would need a key the host knows. The
  review is the act that decides, bound to the change; a project may still require signed
  commits through the host's own rule. Neither a signature nor a review tells GORA who held the
  credential that made it.
- **An approval of a retirement bound to the head commit.** In strict mode every merge to `main`
  moves the head, so it would never hold on a busy `main` (ADR-023, "Why not otherwise");
  approvals bind to the change instead.
- **A `held` field.** It could contradict the history it summarises; the history decides alone.
- **Converting GORA's v0.1 manifest.** It would write state files without evidence for versions
  GORA never registered: the one exception this layout would have.
- **A stage graph fixed for good.** A project that adds a QA environment would need a new
  release repository; section 14 adds and removes stages in the same two steps as artifacts.
- **A single `version:` field.** It cannot tell four kinds of file apart.
- **PyYAML**, or ruamel.yaml's safe loader as it is. YAML 1.1, and duplicate keys kept silently;
  the safe loader resolves timestamps, which RFC 8785 cannot carry.
- **A tag prefix that depends on the number of artifacts.** Adding a second artifact would change
  the first one's tags.
- **A policy digest over raw bytes.** A comment would change the digest of a policy that did not
  change.

## Consequences

**Laws.** Through this ADR:

- **L14** gains: "A retirement counts as the open pull request of every pair whose file it
  deletes; a migration counts for none."
- **L15** reads, at its end: "…; a rollback, a rejection or a migration, those and the request of
  a person whom policy allows; a retirement of an artifact or a stage, those, once people have
  decided it."
- **L16** reads: "The state (`state/`) and the rejections (`rejections/`) change only through a
  GORA pull request: a promotion, a rollback, a rejection, a retirement of an artifact or a
  stage, or a migration that changes no content. A rejection's content never changes and it is
  never removed." Its mechanism: a required check refusing a change to those paths from outside
  `gora/*`, and a migration that changes a `spec`.
- **L17** reads: "Every change to what `prod` holds is one commit on `main` that adds, deletes or
  changes the content of an entry under `state/prod/`, tagged `prod/YYYY.MM.DD.N`. A state tag
  never moves."
- **L23** reads: "Policy cannot switch a law off; a change of policy that would break one is
  refused by GORA's required status, and a project whose policy breaks one is not connected."
- **L24**'s states gain a first one, **retiring** (ADR-024 section 10): the artifact's
  declaration or the stage has been removed, and GORA opens nothing for the pair but the
  retirement.
- **L25**, new: "Every change to policy is its owners' decision. A pull request that changes the
  policy, the code-owner file or a declaration is decided, path by path, by the owners the base
  branch names (the release owners for a path with none): an owner reviewed exactly that change,
  approving it or, as the one who opened it, with an `/approve` review, and no owner asks for
  changes." Enforced by GORA's required status; planned.

Amendments, each recorded in the amended ADR:

- **ADR-003:** "Release manifest" (the manifest is the `state/` tree; v0.1 is retired with GORA's
  own manifest, section 16); the v0.2 sketch is replaced by this ADR's schema; "Identifier
  grammar" (the identifiers of section 3); "Published contract" (the schemas of section 11);
  "Versioning model" and Consequences (schema versions, section 12; the Release BOM is
  ADR-027's).
- **ADR-008:** the layout gains `artifacts/`, `state/`, `rejections/`, `.github/CODEOWNERS` and
  `contracts/schemas/v1alpha1/`, and loses `release-manifest.yaml` and the v0.1 schema.
- **ADR-011:** "Self-manifest" (GORA's own release files are those of section 16;
  `release-manifest.yaml` is deleted).
- **ADR-013:** a promotion reads and writes `state/<stage>/<artifact>.yaml` at the head of the
  base branch; the branch keys `gora/retire/<name>` and `gora/migrate`.
- **ADR-018:** section 2 (CycloneDX 1.6 or newer, the floor a policy field that only rises);
  section 3 (the trusted signers are the policy's named builders, chosen per artifact; the
  required evidence is not a policy field; the policy digest of section 15); section 4 (every
  entry has its evidence pointer); section 5 (the state of `prod` is the release
  composition; its Release BOM is ADR-027's); Consequences (the evidence pointer is
  `spec.evidence` of a StageState).
- **ADR-019:** section 1 (the files), section 2 (people write policy, GORA writes state and
  rejections), section 3 (a project declares its artifacts in `artifacts/`).
- **ADR-022:** section 4 (inputs per artifact; `gora changes`; an artifact never registered),
  section 5 ("Tag names": the first artifact of a repository keeps the empty prefix), section 8
  and Consequences (the form of a rejection is this ADR's; the rest of the release record is
  ADR-027's), section 9 (the manifest is the `state/` tree; v0.1 is retired, not kept readable),
  section 15 (where the policy fields live; L23), section 16 (the release files).
- **ADR-023:** section 2 and section 4 (`gora changes`, `gora migrate`, `gora status --artifact`),
  section 3 (the layout is this ADR; a builder may serve many artifacts, and the check of section 5
  keeps a CI out of another artifact), section 6 (the keys and trailers of section 8; a rollback
  adds only rejections not yet recorded), section 7 (who may ask for a migration; every stage names
  who may ask for `promote` and `rollback`; an App is never an approver or an asker), section 8 (the
  approvers' numbers and groups may come from a declaration), section 10 (the form of a rejection),
  Consequences (the journal under automatic writes is ADR-027's).
- **ADR-024:** section 1 (`stable` declares leaving `0`), section 3 (an entry's content is its
  `spec`; "the policy at the manifest commit" is the policy and the declaration there, with their
  reporters; what a new stage is offered), section 6 (an entry's content is its `spec`), section 8
  (the titles of retirements and migrations), section 9 (GORA's state starts empty; what a rollback
  of GORA needs of the schema versions), section 10 (the state "retiring").

In `docs/architecture/release-model.md`: the vocabulary (release files, manifest, owners, policy,
retire), the lifecycle's step 8, section 4 (what a declaration adds; the L24 states), section 5 (the
tag prefix), section 6 (the state tree), section 7 (what kind B leaves as it is), section 9 (the
code-owner file; GORA's titles include `retire` and `migrate`), L14 to L17, L23, L24 and the new L25
in section 10, section 11 (`gora changes`, `gora migrate`, `--artifact`), and section 13. In README:
the ADR index; the line on where GORA's own stages are recorded and the settings
`GORA_MANIFEST_PATH` and `GORA_MANIFEST_REPO` go with the v0.1 code, in the pull request that
removes it. In CLAUDE.md: the laws are L1 to L25, and an agent never submits a review (section 13).

## Sources

- Trunk Based Development, branch for release: https://trunkbaseddevelopment.com/branch-for-release/
- Google SRE book, release engineering: https://sre.google/sre-book/release-engineering/
- Google Cloud, reliable releases and rollbacks:
  https://cloud.google.com/blog/products/gcp/reliable-releases-and-rollbacks-cre-life-lessons
- Amazon Builders' Library, automating safe, hands-off deployments:
  https://d1.awsstatic.com/builderslibrary/pdfs/automating-safe-hands-off-deployments-clareliguori.pdf
- Netflix, Managed Delivery:
  https://blog.spinnaker.io/managed-delivery-evolving-continuous-delivery-at-netflix-eb74877fb33c
- Kargo, patterns: https://docs.kargo.io/user-guide/patterns
- Kargo, key concepts: https://release-1-0.docs.kargo.io/concepts/
- Kargo, promotion steps: https://docs.kargo.io/user-guide/reference-docs/promotion-steps
- Kargo discussion 3584, large installations: https://github.com/akuity/kargo/discussions/3584
- Argo CD, best practices: https://argo-cd.readthedocs.io/en/stable/user-guide/best_practices/
- Argo CD, Git generators:
  https://argo-cd.readthedocs.io/en/latest/operator-manual/applicationset/Generators-Git/
- Argo CD, Source Hydrator: https://argo-cd.readthedocs.io/en/stable/user-guide/source-hydrator/
- Argo CD Image Updater, update methods:
  https://argocd-image-updater.readthedocs.io/en/stable/basics/update-methods/
- Flux, repository structure: https://fluxcd.io/docs/guides/repository-structure
- GitOps Promoter, structuring repositories:
  https://argo-gitops-promoter.readthedocs.io/en/latest/structuring-your-repos/
- Octopus (Codefresh), modelling GitOps environments:
  https://octopus.com/blog/how-to-model-your-gitops-environments
- Akuity, GitOps best practices: https://akuity.io/blog/gitops-best-practices-whitepaper
- Cloudogu, GitOps repository patterns, part 6:
  https://platform.cloudogu.com/en/blog/gitops-repository-patterns-part-6-examples/
- Renovate, updating and rebasing: https://docs.renovatebot.com/updating-rebasing/
- GitHub, managing a merge queue:
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- GitHub, `GITHUB_TOKEN`: https://docs.github.com/en/actions/concepts/security/github_token
- GitHub, about code owners:
  https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
- GitHub, list CODEOWNERS errors: https://docs.github.com/rest/repos/repos#list-codeowners-errors
- GitHub, REST API endpoints for pull request reviews:
  https://docs.github.com/en/rest/pulls/reviews
- GitHub, dismissing a pull request review:
  https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/dismissing-a-pull-request-review
- GitHub, about commit signature verification:
  https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification
- Kubernetes API conventions:
  https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md
- Kubernetes deprecation policy: https://kubernetes.io/docs/reference/deprecation-policy/
- YAML 1.2.2, changes from 1.1: https://yaml.org/spec/1.2.2/ext/changes/
- ruamel.yaml: https://pypi.org/project/ruamel.yaml/
- RFC 8785, JSON Canonicalization Scheme: https://www.rfc-editor.org/rfc/rfc8785

## Amendments

### 2026-10-09 — the policy names its attesters

ADR-027 attests the outcome of a stage's verifications. For this ADR: the policy names the
signers of those attestations, `attesters` (section 4), and GORA's required status guards their
removal (section 13).

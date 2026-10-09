# ADR-023: Command Model and the Stage Loop

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-04
**Amends:** ADR-007, ADR-009, ADR-013, ADR-014, ADR-015, ADR-017, ADR-019, ADR-022
**Amended:** 2026-10-08 — order of promotion, skip by request, hold after rollback (ADR-024)
**Amended:** 2026-10-08 — no dependencies between artifacts (ADR-025)
**Amended:** 2026-10-09 — a version in "Addressing" is `X.Y.Z-rc` (ADR-024)
**Amended:** 2026-10-09 — the release files, `changes`, `migrate`, keys, approvers (ADR-026)
**Amended:** 2026-10-09 — `changelog`, `bom`, the change log in a PR, attested results (ADR-027)

---

## Context

ADR-022 decided the lifecycle on trunk and named new operations: register a candidate, reject,
roll back one artifact or a platform state, report branches. It left their shape to "the
command-model ADR". Today GORA has five CLI commands (`status`, `cut-rc`, `promote`,
`rollback --to-version --to-digest`, `version`), two HTTP endpoints, both reads (`GET /status`
and the liveness probe `GET /healthz`), and Slack notifications only. ADR-014 sketched mutating
endpoints as verbs (`POST /cut-rc`, `POST /promote`), and ADR-015 a Slack command per verb.

Three questions remained open:

- How one operation looks the same to a person at a terminal, to a pipeline, to the HTTP API
  and to Slack, without logic in any transport (ADR-014, ADR-019 section 5).
- What moves an artifact from one stage to the next once a promotion is merged. A merged
  promotion only changes the manifest; the CD system deploys it, tests run, and someone must
  open the next promotion. A step that waits for a person to remember it is a place where an
  artifact stalls unseen.
- How GORA acts on code hosts other than GitHub (GitLab, Bitbucket), where ADR-022 section 14
  decided only the GitHub App.

Research behind this ADR (2026-10-04, sources below): Google API Improvement Proposals for
resource-oriented design, custom methods, request identification, change validation and errors;
the CLI conventions of `git`, `kubectl` and `gh`; GitHub, GitLab and Bitbucket documentation on
auto-merge, merge queues, merge trains, protected tags and machine credentials. The decisions
were taken with the maintainer on 2026-10-04.

## Decision

### 1. One grammar, three transports

Every operation is one service operation with a typed input and a typed output. The CLI, the
HTTP API and Slack are transports over it and hold no logic (ADR-014, ADR-019 section 5). The
same words mean the same operation everywhere:

- **CLI:** `gora [-p PROJECT] <command> [arguments] [options]`.
- **HTTP API:** the resource method that the command maps to (section 4).
- **Slack:** one slash command, `/gora`, followed by the same words as the CLI. A button in a
  Slack message calls the API, never a private path.

### 2. The commands

Commands that write are named by their verb; commands that read are named by what they show.

| Command | What it does | Writes |
| --- | --- | --- |
| `gora status [STAGE]` | What each stage holds, the state of each artifact in each stage (section 9) and how long it has been in it | nothing |
| `gora history STAGE` | The manifest commits that changed the stage: what changed, when, through which pull request; for `prod`, its platform states `prod/YYYY.MM.DD.N` | nothing |
| `gora digest STAGE [ARTIFACT]` | The digest a stage holds, for a pipeline that deploys on merge (ADR-019 section 6) | nothing |
| `gora register ARTIFACT --digest DIGEST` | The project's CI hands over a build: GORA verifies its evidence (ADR-018), computes the version and creates the rc tag (ADR-022 section 6) | tags, attestation; the first stage's pull request |
| `gora promote STAGE [ARTIFACT]` | Opens the promotion pull request into STAGE, or finds the open one; across the sign-off gate this is the sign-off pull request | pull request |
| `gora reject ARTIFACT VERSION --reason TEXT` | Records a person's rejection (ADR-022 section 8; section 10) | pull request |
| `gora rollback STAGE ARTIFACT --to VERSION --reason TEXT` | Rollback kind A (ADR-022 section 10): the artifact returns to VERSION, and the version it leaves is rejected (section 10) | pull request |
| `gora rollback STAGE --to STATE --reject ARTIFACT[,ARTIFACT…] --reason TEXT` | Rollback kind B, to a platform state `prod/YYYY.MM.DD.N`; STAGE is `prod`, the only stage with platform states (L17). For each named artifact, at least one, the version the stage held before the rollback is rejected (section 10) | pull request |
| `gora branches` | Unmerged branches older than the policy's age (ADR-022 section 13) | nothing |
| `gora connect check` | Whether the project meets the conditions to connect (release-model section 9) | nothing |
| `gora connections` | Whether each adapter is configured and healthy (ADR-015) | nothing |
| `gora version` | The revision GORA was built from and the version it runs as | nothing |

- There is no `approve`, `merge` or `signoff` command anywhere. An approval is a review on the
  promotion pull request, on the code host (section 8). GORA never merges (L15).
- `cut-rc` is replaced by `register`. `rollback --to-digest` is removed: a rollback target is a
  version or a platform state, and GORA reads its digest from the manifest's history (L18).
- A person rarely types `promote`: the stage loop opens promotion pull requests by itself
  (section 8). The command is a request, for example to reopen a pull request someone closed.
  `promote` builds nothing and creates no version: it moves forward a digest the stage before
  has verified, never a rejected one. Moving a stage to an older digest is a rollback; a new
  version comes only from a new commit on `main` (L8).

  *Amended 2026-10-08 (ADR-024 sections 2, 4 and 6):* the command is
  `gora promote STAGE [ARTIFACT] [--version VERSION]`. With `--version` it skips forward to that
  eligible version, in any stage, and it is how a person lifts the hold that a rollback puts on
  a stage. "Across the sign-off gate this is the sign-off pull request" is replaced: there is no
  sign-off gate. `register` creates the tags and attestation and starts a run of the stage loop;
  it opens no pull request by itself ("the first stage's pull request" is replaced), and the loop
  opens the promotion into the first stage when that stage allows it.

  *Amended 2026-10-09 (ADR-026 sections 9, 12 and 13):* three more forms. `gora changes
  [--source REPOSITORY] [COMMIT]` reads which artifacts' inputs changed at a commit, for the
  pipeline that builds them; it writes nothing. `gora migrate` rewrites GORA's files in the
  policy's schema version through a pull request; `gora migrate --print [--to VERSION]` writes the
  people's files converted to standard output and writes nothing. `gora status` takes
  `--artifact PATTERN`, a glob on artifact names: a view, not a group.

  *Amended 2026-10-09 (ADR-027 sections 4 and 5):* two more read commands. `gora changelog
  ARTIFACT [--from VERSION] [--to VERSION]` prints what changed in a range of versions, and
  `gora bom [STAGE] [--at STATE | COMMIT]` prints the composition of a stage as a CycloneDX
  document. Neither writes anything.

### 3. Addressing

- **Project.** A project is one release repository (ADR-019 section 1): one manifest, one
  policy, one system whose artifacts are promoted through the same stages, depend on each other
  (`dependsOn`, migrations) and roll back together (kind B). The model is the same for one
  artifact or a hundred. The artifacts may be built in any number of code repositories (one per
  service, a monorepo, or both); that is the shared shape. Policy names each artifact's code
  repository and the identity that builds it, so a CI cannot register into another artifact or
  project (ADR-018). Inside the release repository each artifact has its own directory, so a
  team owns its artifacts' declarations (for example through code owners) without splitting the
  source of truth; the files and their schema are decided with the manifest schema (ADR-003).
  A separate system, with its own stages and no dependency on or rollback with this one, is a
  separate project with its own release repository; one system is never split across two. A
  project is selected with the global option `-p, --project`, as `gh -R` or `kubectl -n` select
  theirs. Inside a clone of a release repository the project is that repository. A project's
  full name is the path of its release repository on its host (`github.com/acme/cally-release`);
  short names are aliases in the user's configuration.

  *Amended 2026-10-08 (ADR-025 section 1):* the artifacts of a project are promoted through the
  same stages and roll back together (kind B); GORA has no dependencies between them, so
  "depend on each other (`dependsOn`, migrations)" above is replaced.

  *Amended 2026-10-09 (ADR-026 sections 1, 2 and 5):* the files and their schema are decided in
  ADR-026, not with the manifest schema of ADR-003: each artifact's declaration is
  `artifacts/<artifact>/artifact.yaml`, owned by its team through `.github/CODEOWNERS`. A builder
  named in the policy may serve many artifacts; what keeps a CI from registering into another
  artifact is the whole check: a builder the artifact's declaration names, and provenance whose
  source repository is the artifact's and whose subject is a digest in the artifact's image.
- **Artifact.** Named in every command that acts on one, unless the project declares exactly
  one artifact. When an artifact `dependsOn` another, a promotion of it carries the other in the
  same group pull request (ADR-022 section 9). A rollback never carries a migration artifact
  (L21). *Amended 2026-10-08 (ADR-025 sections 1 to 3):* a promotion carries one artifact,
  and a migration is an artifact like any other: a rollback carries it like any other.
- **Versions and states.** A version is written as GORA prints it, `1.4.0` or `1.4.0-rc.2`,
  always for one artifact. A platform state is its tag, `prod/2026.10.05.1`.
  *Amended 2026-10-09 (ADR-024 section 1):* `1.4.0-rc`, without a counter, as the note after
  the examples says.
- **Digests.** A person never types a digest. `register` is the only command that takes one,
  and its caller is the project's CI.

Examples:

```bash
gora -p cally status prod
gora -p cally promote qa api
gora -p cally reject api 1.4.0-rc.2 --reason "login fails on Safari"
gora -p cally rollback prod api --to 1.3.2 --reason "checkout errors after 1.4.0"
gora -p cally rollback prod --to prod/2026.10.05.1 --reject api --reason "incident 412"
gora -p cally digest prod api --output json
gora -p gora promote staging          # one artifact: no name
```

*Amended 2026-10-08 (ADR-024 section 1):* a candidate's version is `X.Y.Z-rc`, without a
counter: the examples read `1.4.0-rc` where they show `1.4.0-rc.2`.

### 4. The HTTP API is resource-oriented

The API follows resource-oriented design (AIP-121): resources with standard methods, and a
custom method `:verb` over `POST` for an action that is not a standard method (AIP-136). Each
CLI command maps to the methods of one resource:

| CLI | API (`/v1` prefix) |
| --- | --- |
| `status` | `GET projects/{p}/stages` (list), `GET projects/{p}/stages/{s}` (get) |
| `history STAGE` | `GET projects/{p}/stages/{s}/states` |
| `digest STAGE ARTIFACT` | `GET projects/{p}/stages/{s}/artifacts/{a}` |
| `register ARTIFACT` | `POST projects/{p}/artifacts/{a}/candidates` |
| `promote STAGE ARTIFACT` | `POST projects/{p}/stages/{s}/promotions` |
| `reject ARTIFACT VERSION` | `POST projects/{p}/artifacts/{a}/versions/{v}:reject` |
| `rollback STAGE ...` | `POST projects/{p}/stages/{s}:rollback` |
| `branches` | `GET projects/{p}/branches` |
| `connect check` | `GET projects/{p}/conditions` |
| `connections` | `GET projects/{p}/connections` |
| `version` | `GET version` |

*Amended 2026-10-08 (ADR-024 section 4):* `promote` takes an optional `version`, the API form of
`--version`.

*Amended 2026-10-09 (ADR-026 sections 9, 12 and 13):* `changes` is
`GET projects/{p}/changes?source={repository}&commit={sha}`; `migrate` is
`POST projects/{p}/state:migrate`; `status` takes the filter `artifact`.

*Amended 2026-10-09 (ADR-027 sections 4 and 5):* `changelog` is
`GET projects/{p}/artifacts/{a}/changelog?from=&to=`; `bom` is
`GET projects/{p}/stages/{s}/bom?at=`.

- A write accepts a `request_id`; the same request sent again returns the first result instead
  of acting twice (AIP-155).
- A write accepts `validate_only`: it checks everything and returns the plan without acting
  (AIP-163). It is the API form of `--dry-run`.
- Errors follow AIP-193. A refusal by a law names the law and the reason, for example
  `L13: 1.4.0-rc.2 is superseded by 1.4.0-rc.3`. *Amended 2026-10-08 (ADR-024 section 3):*
  for example `L12: 1.4.2-rc is not verified in qa`.
- The API is versioned in its path. A change that breaks a client is a new version. The
  liveness probe `GET /healthz` stays outside `/v1`; it is not part of the API's contract.

### 5. Output and exit codes

- The CLI prints text for a person by default. `--output json` prints exactly the body the API
  returns for the same operation. One JSON Schema per result describes both; the schemas are
  generated from the models and published under `contracts/schemas/`, like the manifest's
  (ADR-003).
- The exit codes stay: `0` done, `1` refused or failed, `2` usage, `3` done with an incomplete
  audit (ADR-017). A refusal by a law exits `1` and prints the law, as the API error does.

### 6. Dry run, idempotency and branch keys

- Every command that writes has `--dry-run`: it computes and prints the plan (the manifest
  diff, the tags, the pull request) and changes nothing.
- Every write is idempotent by its key (ADR-013): running it again finds the pull request or
  the tag it made and reports it; nothing is created twice.
- A GORA pull request comes from the branch its key names:
  - a promotion, a group promotion and a rollback of kind A: `gora/promote/<stage>/<artifact>`.
    A group is keyed by the artifact it is promoted for; the artifacts it carries count as
    having that pull request open (L14);
  - a rollback of kind B: `gora/rollback/<stage>`; it counts as the open pull request of every
    artifact it changes;
  - a rejection on its own: `gora/reject/<artifact>/<version>`; a rollback carries its
    rejections in its own pull request. A rejection of a digest that is already rejected, or
    that has an open rejection or rollback pull request under another of its versions, finds
    that one and opens nothing.

  One key holds at most one open pull request. A request with the same key and the same content
  finds it. While a rollback pull request is open, a promotion or another rollback of its
  artifacts into its stage is refused, by the loop and on request, and the refusal names that
  pull request: it merges, or a person closes it, first. Otherwise a request with the same key
  and new content closes the open pull request, deletes its branch and opens a new one (L14);
  opening a group promotion or a rollback also closes every other open promotion of the
  artifacts it carries into that stage. This replaces the key `gora/promote-<stage>-<version>`
  of ADR-013.

  *Amended 2026-10-08 (ADR-024 sections 4 and 8, ADR-025 section 1):* there is no group
  promotion: "a group promotion", "a group is keyed by the artifact it is promoted for" and
  "opening a group promotion" above are replaced. The stage loop never replaces an open pull
  request. New content comes from a person's request, such as a named version, which replaces
  the open promotion of the pair, or from a rollback, which also closes every other open
  promotion of the artifacts it carries into that stage. GORA's pull requests have the
  Conventional Commit titles of ADR-024 section 8.

  *Amended 2026-10-09 (ADR-026 sections 7 and 8):* two more keys. `gora/retire/<name>` deletes
  the state files of a retiring artifact or a removed stage and counts as the open pull request
  of every pair whose file it deletes; `gora/migrate` rewrites GORA's files in the policy's
  schema version, changes no content and counts for none. A rollback adds only the rejections
  not recorded yet, and GORA closes an open rejection pull request of a version it rejects.
  Their trailers are `Gora-rejection: <artifact> <version> <digest>`, `Gora-retirement: <name>`
  and `Gora-migration: <from> <to>`.

  *Amended 2026-10-09 (ADR-027 section 4):* the body of a promotion pull request carries, above
  its trailers, the change log from the version the stage holds to the version it promotes, or,
  when that would not fit the host's limit, its count per group and the command that prints it.
  The squash commit's body is GORA's trailers alone, set when GORA enables auto-merge, so no
  change log enters `main`.
- GORA's commit on a promotion branch, and the body of its pull request, end with a Git trailer
  that names the promotion, one line per artifact:
  `Gora-promotion: <stage> <artifact> <version> <digest>`. The key is written in Git's own style
  (`Signed-off-by`, `Co-authored-by`); GORA writes that form and reads the key in any case, as
  Git does. The squash commit takes the body (release-model section 9), so the manifest's
  history on `main` says which merge promoted what, and the commit a review was given on says
  which promotion it approved.
- A trailer is trusted only where nobody else can change it. GORA's status is red while the pull
  request's body does not end with exactly the trailers GORA wrote; where the host lets the
  merge message be set when auto-merge is enabled (on GitHub, the commit body of the auto-merge
  request), GORA sets it. On `main`, GORA reads a trailer only when it agrees with the commit's
  diff. Only GORA's identity creates, updates or deletes `gora/*` branches.
- GORA writes no personal data into Git: no name, email or account of a person in a file, a
  commit message or a trailer. Its commits are authored by its own identity. Who asked for an
  action and who approved it stay where they can be managed: the code host's own record (reviews,
  and a comment GORA adds to its pull request) and GORA's audit journal (ADR-017), which records
  the host account's identifier.

### 7. Where a write runs, and who may ask for it

- A command that writes runs only where GORA's own machine identity on the code host is
  available (section 11): the release repository's pipeline, and later the server. The pipeline
  is GitHub Actions for the MVP; a Tekton task is the same call, later (ADR-019 section 4).
  Without that identity a write refuses, unless it is a dry run. A person never lends GORA
  their token.
- A person asks for a write through a channel that reaches that identity: a manual run of the
  release repository's pipeline whose input is the same command line, the HTTP API, or Slack.
- **Who asks.** The service takes the asker from the transport, never from the request's
  content:
  - the pipeline: the person who started the run (on GitHub Actions, the run's triggering
    actor);
  - the HTTP API: the subject of the caller's sign-in on the server (ADR-019 section 5);
  - Slack: the Slack user, mapped to the code-host account that the user links once through
    the server's sign-in; a user without a link may only read.
- **Who may ask.** Policy names, per action and, for `promote` and `rollback`, per stage, the
  groups allowed to ask (for example, `rollback prod` by `release-owners`), and, per stage, the
  approvers. Groups are the code host's teams (GitHub teams, GitLab groups, Bitbucket groups),
  so membership is kept on the host, where it is already managed; a short list may name host
  accounts instead, as the project's own choice in its policy. An email address is never an
  identity: in a commit anyone can set it. On GitHub, reading teams needs the App's organization
  permission "Members: read". The service checks it, not the transport, and the audit journal
  records the account that asked and the identity that acted (ADR-017).
- Reads need read access to what they show: the release repository, and for `branches` and
  `connect check`, the project's code repositories and their settings.

*Amended 2026-10-09 (ADR-026 sections 3, 4 and 12):* `requests.migrate` names who may ask for a
migration of GORA's files. Every stage names who may ask for `promote` and for `rollback`, so a
stage held after a rollback always has someone who may lift the hold. An App of the code host is
a principal only as a reporter, never an approver or one who may ask.

### 8. The stage loop

A merged promotion changes only the manifest. The loop below is the contract that moves an
artifact from there, the same for every stage:

1. **Promotion.** GORA opens the promotion pull request into the stage, posts its required
   status as pending, then enables the code host's auto-merge on it (on GitHub, auto-merge
   cannot be enabled on a pull request that is already mergeable), then evaluates the gate. The
   first stage's promotion is opened when a candidate is registered; every other one when the
   stage before is verified (step 5).
2. **Gate.** The stage's gate decides when the pull request may merge:
   - `auto`: when the other required checks pass;
   - `approval`: when the other required checks pass and the approvals the policy names are
     given, for example `approvers: { qa: 1, release-owners: 1 }`. Approvals are reviews on the
     pull request by members of the named groups, given by distinct people: one review counts
     toward one group.

   GORA's required status turns green when its gate is met, and the host's auto-merge merges
   the pull request. The required status is bound to GORA's identity as its only source (on
   GitHub, the required check's expected source app), so nobody else can turn it green. Nobody
   clicks merge after approving, and GORA still never merges (L15). The sign-off is the
   approval on the pull request across the sign-off gate (ADR-022 section 8). Approvals of a
   promotion are reviews, never labels: a label is not bound to a person's decision on a pull
   request.

   *Amended 2026-10-09 (ADR-026 sections 4 and 5):* the approvers of a stage for an artifact are
   the policy's for the stage together with those the artifact's declaration adds; for a group
   both name, the higher number counts. A number is at least 1 and at most the group's members,
   and a gate is met when one review can be assigned to each required place.
3. **Deployment.** The merge is a manifest commit on `main`; the CD system deploys the stage's
   digest (ADR-019 section 6).
4. **Verification.** The policy lists the stage's verifications, for example
   `verify: [deploy, smoke, e2e]`. GORA runs none of them. The CD system and the test systems
   report each result as a commit status named `<stage>/<artifact>/<verification>` (for example
   `qa/api/e2e`) on the manifest commit that set the artifact's entry in the stage. GORA also
   counts a status on any later commit of `main` on which that entry is unchanged, because a CD
   system reports on the revision it synced; among those, the newest status of a name decides.
   The policy names the identity that reports each verification, and GORA counts a status only
   from that identity. For Argo CD, GORA can read the application's health through a read-only
   adapter instead of a reported `deploy` (ADR-019 section 6).
5. **Next promotion.** When every verification of the stage is green, GORA opens the promotion
   pull request into the next stage, and the loop repeats. When one is red, the stage holds a
   failed digest: GORA opens nothing further for it. The way on is a fix forward, a new
   candidate through every stage; a rollback contains the defect meanwhile, and a rejection
   records it.

**An approval binds to the promotion, not to a commit.** GORA keeps its promotion branches
current with `main` (ADR-013), so the head of an open promotion pull request changes whenever
`main` moves. An approval counts while the pull request promotes the same digests into the same
stage, on whatever head GORA recomputed:

- A review counts only if the commit it was given on is GORA's commit for exactly that
  promotion: made by GORA's identity (on GitHub, created through the API by the App, which the
  host attributes and signs), with a diff against its base that is exactly the promotion its
  trailer names (section 6). A review given on a head with any other change never counts, even
  after GORA recomputes the branch.
- The head must carry exactly the promotion GORA computed: a commit anyone else pushes to the
  branch turns GORA's status red.
- A promotion of another digest is another pull request (L14) and needs its own approvals.
- GORA reads the reviews itself. A review that the host marks stale because GORA recomputed the
  branch still counts; a review that a person dismissed does not (to verify with the adapter:
  on GitHub both leave the review `DISMISSED`).
- The host's own required-review and code-owner rules do not block GORA's pull requests: the
  release repository requires no host review on `main`, or GORA's identity is exempt from the
  rule that requires reviews and from no other (on GitHub, a bypass actor of a ruleset that
  holds only that rule; to verify with the adapter).

**The loop moves only forward.** It opens a promotion only for the newest non-rejected
candidate that may enter, registered after the one the stage holds (ADR-022 section 6). It never
moves a stage back to an older candidate: that is a rollback, and only a person asks for one
(section 10).

*Amended 2026-10-08 (ADR-024 sections 2 to 4 and 7):* there is no sign-off gate; the approval on
the promotion into a stage is that stage's decision. The loop opens a promotion into a stage only
when the version the stage holds has finished its verifications, and proposes (ADR-024 section 3)
into an `approval` stage the oldest offered version once it is eligible, into an `auto` stage the
newest eligible version. A person may ask for any eligible version, in any stage, with
`gora promote STAGE [ARTIFACT] --version VERSION`. Registration opens no pull request by itself:
"the first stage's promotion is opened when a candidate is registered", "every other one when the
stage before is verified (step 5)", step 5's "when every verification of the stage is green, GORA
opens the promotion pull request into the next stage" and "the newest non-rejected candidate"
above are replaced: a verified version becomes offered to the next stage, which takes it in its
own order. A verification is a commit status from its reporter; Argo CD reports `deploy` through
its notifications controller, and GORA's reading of an application's health in step 4 is replaced,
since the health of an application is its state now and cannot be read again for an older version.

**When the loop runs.** On every event that can move it (a registration, a merge, a status, a
review), and as a periodic reconciliation: a re-check at a fixed interval, so a missed event
delays the loop but never strands an artifact. The re-check never holds back or batches a
candidate; it is not a schedule in the sense of L20. The loop is level-triggered: each run
computes what to do from Git and the code host alone and keeps no state of its own between
runs. A wait for an approval is the open pull request on the host, not a wait inside GORA:
each run completes in seconds and its output is pull requests, tags and statuses. The loop stays
within the synchronous execution of ADR-007 phase 2 and needs no durable orchestration; ADR-007
is amended to say so.

*Amended 2026-10-09 (ADR-027 section 2):* the code host deletes commit statuses by age. Each run
computes from Git, the code host and the registry, where GORA attests each verification result it
counts; it still keeps no state of its own between runs. A required check of an open GORA pull
request that has expired runs again: GORA recreates its commit on the branch with the same change,
and the approvals stay.

A policy example (field names are illustrative; the schema is decided with
`release-policy.yaml`):

```yaml
stages:
  dev:     { gate: auto,                                                  verify: [deploy, smoke] }
  qa:      { gate: approval, approvers: { qa: 1 },                        verify: [deploy, e2e] }
  preprod: { gate: approval, signoff: true,
             approvers: { qa: 1, release-owners: 1 },                     verify: [deploy, smoke] }
  prod:    { gate: approval, approvers: { release-owners: 1 },            verify: [deploy, smoke] }
reporters: { deploy: argocd, smoke: smoke-tests, e2e: e2e-tests }
requests:
  promote:  { dev: [developers], qa: [qa], preprod: [qa, release-owners], prod: [release-owners] }
  rollback: { dev: [developers], qa: [qa], preprod: [release-owners], prod: [release-owners] }
  reject:   [qa, release-owners]
```

*Amended 2026-10-08 (ADR-024 section 2):* `signoff` no longer exists: a version becomes final
by entering `prod`.

*Amended 2026-10-09 (ADR-026 section 4):* the schema is decided; the example of record is
ADR-026 section 4, where stages are a list, principals are written `team/<slug>`, and builders,
the SBOM floor and who may ask for a migration are fields.

### 9. No artifact stalls unseen (law L24)

Every artifact in every stage is measured against what may enter it: for the first stage, the
newest non-rejected registered candidate; for any other stage, the digest the stage before
holds. The pair is in exactly one of these states, the first that applies, which `gora status`
shows with the time spent in it:

1. **at the gate:** a GORA pull request for the pair is open (L14), a promotion or a rollback,
   and its GORA status is not red for a reason below;
2. **blocked, with the reason:** for example, the stage holds a rejected digest or one whose
   verification failed; a verification in the stage before failed; the candidate is superseded
   or rejected (L13); its evidence fails the check at promotion (ADR-018 section 4); or a person
   closed its promotion pull request (the loop does not open the same promotion again;
   `gora promote` does);
3. **waiting:** the stage before holds a newer digest whose promotion GORA has not opened yet:
   its verifications have not all reported, or the loop has not run since they did;
4. **in sync:** nothing newer may enter: the stage holds that digest, or a candidate registered
   after it.

The order makes the states exclusive. A pair that meets none of them is a defect: GORA reports
it in `gora status` and fails its own run, and never shows it as in sync.

*Amended 2026-10-08 (ADR-024 section 10):* the states above are replaced by those of ADR-024
section 10, in their order: at the gate, held, verifying, blocked with the reason, ready,
waiting, in sync. What may enter a stage is an eligible version (ADR-024 section 3), not the
newest candidate; "superseded" is no longer a reason.

### 10. Rollbacks and rejections

- **The request is the decision.** A rollback or a rejection is asked for by a person whom
  policy allows for that action (and, for a rollback, that stage; section 7), with a reason.
  GORA's required status turns green once the other required checks pass, without the stage's
  approvers, and the host's auto-merge merges the pull request. A rollback is an emergency that
  contains a defect; a second approval would hold the defect in the stage.
- **A rollback rejects.** A rollback of kind A rejects the version it leaves. A rollback of kind
  B rejects, for each artifact the request names (at least one), the version the stage held
  before the rollback; a named artifact that the rollback does not move is refused. When a
  platform state of many artifacts is restored, the person names the culprits. Every other
  artifact it moved back is not rejected: the loop proposes the version the stage before holds
  forward again, through the stage's gate. The rejections are recorded in the rollback's own
  pull request (their form is decided with the release record). A rejected version never moves
  forward again and is never a rollback target (L13, L18); the way on is a fix: a new commit on
  `main`, a new candidate, every stage. This replaces "a rollback rejects nothing" of ADR-022
  section 10, so no version can come back by accident and no separate hold is needed.

  *Amended 2026-10-08 (ADR-024 sections 3 and 6):* "the loop proposes the version the stage
  before holds forward again" and "no separate hold is needed" are replaced: a rollback holds
  the stage for every artifact it moved until a person asks with `gora promote`, so an artifact
  that kind B moved back and did not reject returns when a person decides. A rejected version
  never moves forward by L12 (`L13` above is replaced by ADR-024). Kind B is refused if it
  would move an artifact to a newer version or to a rejected digest; an artifact the named
  state does not contain keeps its version, and migrations move like any artifact (ADR-025
  section 3).

  *Amended 2026-10-09 (ADR-026 section 7):* the form of a rejection is decided: one file
  `rejections/<artifact>/<X.Y.Z>.yaml` per digest, written only by GORA, whose content never
  changes and which is never removed. A kind B rollback leaves an artifact retiring or retired
  since the named state as it is.
- **A rejection holds the digest.** The rc version and the final version of one digest are one
  build: rejecting either rejects both, and GORA refuses to promote, sign off or roll back to
  that digest under any of its versions. *Amended 2026-10-08 (ADR-024 section 2):* there is no
  sign-off; GORA refuses to promote or roll back to it.
- **A rejected version still counts in the line.** The next version is computed from the newest
  final version tag of the artifact, rejected or not: a rejection drops a version out of
  "newest" at the sign-off gate (L13), never out of the line, so a fix after a rejected `1.1.0`
  is `1.1.1-rc.1`, not a second `1.1.0`.

  *Amended 2026-10-08 (ADR-024 section 1):* a rejected version keeps its number, and the next
  candidate's version is computed after it; there is no line and no sign-off gate.
- **Rejection on its own.** A person whom policy allows rejects a candidate or a version that
  fails its tests, for example QA rejecting an rc version, without a rollback.
- **From the request.** GORA's status is red on every open promotion of a digest being
  rejected, and the loop opens none. While a rollback pull request is open, a promotion or
  another rollback of its artifacts into its stage is refused (section 6). Once the pull request
  is merged, GORA closes every open promotion of the rejected digests, in every stage.

### 11. GORA's identity on any code host (law L22, generalised)

GORA acts on a code host only as its own machine identity on that host, never with a person's
token, with the narrowest scope the host allows and a credential that expires. Its secret lives
only where GORA's writes run: the release repository's pipeline or GORA's server. In the
pipeline, the secret is available only to the pipeline definition on `main` (on GitHub Actions,
an environment whose deployment branches are `main` only), so a run from any other ref holds no
GORA identity and cannot name an asker. ADR-022 section 14 decided it for GitHub; the same rule
holds for every host, each through its own adapter (ADR-006):

| | GitHub | GitLab | Bitbucket Cloud |
| --- | --- | --- | --- |
| GORA's identity | GitHub App; installation tokens expire after an hour | project or group access token, a bot user; an expiry is required (365 days at most by default); on GitLab.com, Premium or Ultimate | repository or workspace access token; the workspace can require an expiry |
| Auto-merge | yes | auto-merge when the pipeline succeeds | merge when builds pass |
| Protected branches with required checks | yes; for private repositories, on paid plans | "pipelines must succeed" on every tier | merge checks enforced on Premium: to verify with the connector |
| A required status only GORA's identity can set | yes (the required check's expected source app) | external status checks, on Ultimate: to verify with the connector | to verify with the connector |
| Protected tags (L10, L17) | tag rulesets, on the same plans as protected branches | protected tags | none found in its documentation: to verify with the connector |
| A review names the commit it was given on | yes | to verify with the connector | to verify with the connector |
| A stale dismissal is told apart from a person's | to verify with the adapter | to verify with the connector | to verify with the connector |
| Merge queue | free for public repositories of an organization; private repositories on Enterprise Cloud | merge trains, Premium | open beta since April 2026, Standard and Premium |

The model needs only these: pull requests (merge requests on GitLab), commit statuses, a
required status that only GORA's identity can set, reviews that name the commit they were given
on, auto-merge, protected branches with required checks, and protected tags. A host or a plan
that lacks one of them does not meet the conditions to connect, and `gora connect check` names
what is missing (L23). GitHub is the first adapter; the others come with their connectors, which
settle each "to verify" above.

*Amended 2026-10-09 (ADR-027 sections 2 and 7):* the model also needs a registry that keeps the
attestations of every digest a stage holds or may roll back to, and a signer for GORA's verification
summaries, a key or a Sigstore identity; a commit status is an input that may expire, never a
record. No plan-specific feature is a condition: on GitHub, a private repository has what the model
needs from GitHub Team.

### 12. The merge queue adds throughput; it is not a condition

L3 (every commit on `main` tested as it lands) holds through required checks in strict mode on
every plan that offers protected branches with required checks. A merge queue, where the host
and the plan offer one, keeps the same guarantee while merging many pull requests in a row. L3
needs no plan beyond one that offers protected branches with required checks.

GORA works with and without a queue, and reads which applies from the host's rules for `main`
(`gora connect check` shows it). Policy does not set it, so the two can never disagree:

- **Without a queue:** required checks in strict mode. GORA keeps its open pull requests current
  with `main` (ADR-013), and the host merges a pull request once it is up to date and its gate
  is met.
- **With a queue:** the queue tests each pull request together with those ahead of it before
  they land, so a branch need not be up to date, and GORA recomputes its branch only when the
  promotion itself changes or the branch no longer merges cleanly onto `main`. Auto-merge puts a
  pull request into the queue once its gate is met. GORA posts its required status on the
  queue's merge group as well (on GitHub, the `merge_group` event) and checks there that every
  promotion in the group is still valid; one that a group ahead of it made invalid (superseded,
  rejected, a second promotion of the same artifact and stage) fails and leaves the queue.
  *Amended 2026-10-08 (ADR-024 section 3):* "superseded" no longer applies; a promotion is
  invalid when its version is rejected or no longer eligible.
- **In both:** the squash commit takes the pull request's title and body, so the trailer
  reaches `main`; the connect check verifies it for the mode in use. How a queued pull request
  is merged and with which message is to verify with the adapter on each host.

On a release repository with many artifacts, many promotion pull requests are open at once.
Without a queue every merge makes the others out of date and GORA updates them; the release
repository's checks are fast, so this costs seconds per merge. A queue removes that work.

## Why not otherwise

- **Verbs as API paths (`POST /promote`).** It works, but every new operation becomes a new
  path with its own conventions. Resources with standard and custom methods give one pattern
  for all of them, and a client knows from the path what it acts on.
- **Noun-first commands (`gora candidate register`).** The model's vocabulary is verbs:
  promote, reject, roll back. A verb first reads as the action it is.
- **An `approve` or `merge` command.** It would put a merge, or a decision that stands for one,
  in GORA's hands or in a transport. The code host already records who approved what.
- **Labels for approvals or test results.** A label is not bound to a person's decision or to a
  result, and anyone who can label can set it (ADR-016's gate-change check accepts a label only
  as the event that adds it, for that reason).
- **An approval bound to the head commit.** GORA recomputes its branch whenever `main` moves,
  so on a busy trunk an `approval` gate would ask again after every unrelated merge and might
  never pass.
- **A loop that promotes whatever the stage before has verified.** It would undo a rollback at
  once and could move a later stage back to an older version. *Amended 2026-10-08 (ADR-024
  sections 3, 4 and 6):* the loop of ADR-024 promotes verified versions only forward, never
  re-enters a version a stage passed over, and holds a stage after a rollback, which answers
  both.
- **A rollback that rejects nothing** (ADR-022 section 10 as first written). The version rolled
  back from could come back through the loop or a `promote`, and holding it out would need a
  separate state for the loop to keep. A rollback is an emergency: the way on is a fix and a new
  candidate.
- **A kind B rollback that rejects every version it moves back.** An artifact that had nothing
  to do with the incident could not return without a new commit, since a candidate needs a
  change to its inputs (L8).
- **Code owners as the approval gate.** On GitHub an approval from any one code owner of a
  file is enough, so a gate that needs two groups cannot be expressed, and the rule belongs to
  the host's required review, which also covers every other pull request. Code owners decide
  who may change an artifact's declaration in the release repository; policy approvers decide
  its promotions.
- **An email address as identity.** A commit's author email is whatever its author configured.
  The code host's account is what the host authenticates.
- **The queue as a policy setting.** The host's rules already say whether `main` has a queue;
  a second statement of the same fact could contradict it.
- **Promotions opened only on request.** A step that waits for a person to remember it is where
  an artifact stalls; the loop opens every next promotion itself.
- **A durable workflow engine for the loop.** Git and the code host already hold every state
  the loop needs; a second store of in-flight state would be a second source of truth
  (ADR-002, ADR-007).
- **Writes from a person's machine with their own token.** It would make that person GORA on
  the code host, against L22.

## Consequences

Amendments, each recorded in the amended ADR:

- **ADR-022:** section 2 (strict required checks are the mechanism of L3; a queue adds
  throughput), section 7 (gates `auto` and `approval`, approvals bound to the promotion, the
  stage loop, forward only), section 8 (the sign-off is the approval; rejection merged on the
  request; a rejection holds the digest; a rejected final version stays in the line), section 10
  (a rollback rejects the version it leaves; kind B rejects the named culprits), section 12 (a
  person who does not want a change does not approve it; an `auto` stage takes every candidate;
  the periodic re-check is not a schedule), section 14 (identity on any host; "Members: read";
  where the secret lives), section 15 (policy gains approvers, verifications and who may ask for
  what).
- **ADR-013:** the body's "a human merges" and `gate: human` are replaced: the host's
  auto-merge merges every GORA pull request once its gate is met; GORA opens promotion pull
  requests itself, on the stage loop as well as on request; approvals survive the recomputation
  of a branch; the branch keys of section 6.
- **ADR-014:** the HTTP API, read and write, and the Slack commands follow sections 1 to 4; the
  verb endpoints and the planned read endpoints are replaced. Slack never approves.
- **ADR-015:** incoming Slack commands use the same words as the CLI; an approval is a review on
  the code host.
- **ADR-019:** section 1 (a release repository is one project, one system), section 2 (every
  GORA pull request is merged by the host's auto-merge once its gate is met), section 5 (the
  server runs the stage loop on events and as a periodic reconciliation; approvals are reviews
  on the host, not server state).
- **ADR-009:** step 3 of the Milestone 3 exit: a person approves each promotion and the host
  merges it.
- **ADR-007:** a wait held by the code host as an open pull request is not a wait state inside
  GORA, and the stage loop is not multi-stage orchestration over time; the level-triggered loop
  stays within phase 2.
- **ADR-017:** the journal is attached in every process that writes, the server included; where
  a pipeline run's journal is kept beyond the run is decided with the server, and until then
  GORA's comment on its pull request is the durable record of who asked.

  *Amended 2026-10-09 (ADR-026):* where the journal is kept under automatic writes is left to
  ADR-027, the release record.

  *Amended 2026-10-09 (ADR-027 section 3):* a pipeline run and the server write the journal to
  standard error, kept by the platform that runs them; GORA's comment on its pull request stays
  the durable record of who asked.

Until the gates and GORA's required status exist, the maintainer merges GORA's pull requests by
hand after review; L15's "nobody merges by hand" takes effect with them (release-model
section 13).

In `docs/architecture/release-model.md`: the vocabulary (project, gate, verification, sign-off
gate, reject, rollback, policy), the lifecycle and the gate in sections 2 and 4, the stage loop,
rollback requests and their rejections in section 7, the connect conditions in section 9, the
commands in a new section 11, L3, L10, L14, L15, L18 and L22 reworded, L24 added, and the merge
queue and interim merges in section 13. The directory per artifact in the release repository is
decided with the manifest schema (ADR-003); ADR-003 is not amended here.

*Amended 2026-10-09 (ADR-026):* the directory per artifact and the rest of the release files are
decided in ADR-026, which amends ADR-003.

Implementation, in later pull requests: the commands and API of sections 2 to 6 replace the
current CLI as ADR-018 and ADR-022 are implemented; the stage loop needs the code host
adapter's review and status reads; result schemas are published with the commands they
describe. Until then the current commands stay as they are.

## Sources

- AIP-121, resource-oriented design: https://google.aip.dev/121
- AIP-136, custom methods: https://google.aip.dev/136
- AIP-155, request identification: https://google.aip.dev/155
- AIP-163, change validation: https://google.aip.dev/163
- AIP-193, errors: https://google.aip.dev/193
- GitHub, managing a merge queue:
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- GitHub, automatically merging a pull request:
  https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request
- GitHub, about code owners:
  https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
- GitHub, about rulesets:
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets
- Kargo, patterns (Freight of several artifacts promoted together, or independent Warehouses):
  https://docs.kargo.io/user-guide/patterns
- Akuity, GitOps best practices (environments as folders; structure follows the organisation):
  https://akuity.io/blog/gitops-best-practices-whitepaper
- GitLab, project access tokens: https://docs.gitlab.com/user/project/settings/project_access_tokens/
- GitLab, merge trains API: https://docs.gitlab.com/api/merge_trains/
- Bitbucket, require access token expiry:
  https://support.atlassian.com/bitbucket-cloud/docs/require-access-token-expiry/
- Bitbucket, automatic merging when builds pass:
  https://www.atlassian.com/blog/bitbucket/automatic-merging-when-builds-pass
- Bitbucket, merge queues open beta: https://www.atlassian.com/blog/bitbucket/merge-queues-open-beta

## Amendments

### 2026-10-08 — order of promotion, skip by request, hold after rollback

ADR-024 decides the versions and the order of promotion. Sections 2, 3, 4, 6, 8, 9, 10 and 12
and the "Why not otherwise" entry on the loop are amended in place:

- `gora promote` takes `--version` to skip forward to an eligible version, in any stage, and
  lifts the hold after a rollback; registration opens no pull request by itself (sections 2 and
  4);
- versions are `X.Y.Z-rc` (section 3);
- GORA's pull requests have the Conventional Commit titles of ADR-024 section 8; the loop never
  replaces an open pull request (section 6);
- the loop proposes into an `approval` stage the oldest offered version once it is eligible, into an
  `auto` stage the newest eligible version; there is no sign-off gate; a verification is a commit
  status, and GORA no longer reads Argo CD's health in place of `deploy` (section 8);
- the states of L24 are those of ADR-024 section 10 (section 9);
- a rollback holds the stage until a person asks; a rejected version never moves by L12; kind B
  never moves an artifact to a newer version or to a rejected digest; there is no sign-off
  (section 10);
- "superseded" no longer makes a queued promotion invalid (section 12).

### 2026-10-08 — no dependencies between artifacts

ADR-025 decides that GORA has no dependencies between artifacts. Sections 3, 6 and 10 are amended
in place: a project's artifacts have no dependencies, a promotion carries one artifact and a
rollback may carry a migration (section 3); there is no group promotion or group branch (section
6); kind B returns every artifact, migrations included (section 10).

### 2026-10-09 — a version in "Addressing" is `X.Y.Z-rc`

The note of 2026-10-08 after the examples of section 3 covers the examples; the bullet "Versions
and states" showed `1.4.0-rc.2` without one and gains it.

### 2026-10-09 — the release files, `changes`, `migrate`, keys, approvers

ADR-026 decides the release files. For this ADR: the commands `gora changes`, `gora migrate` and
`gora status --artifact`, with their API (sections 2 and 4); the layout is ADR-026's, and a builder
may serve many artifacts (section 3); the keys `gora/retire/<name>` and `gora/migrate`, their
trailers, and a rollback adding only rejections not recorded yet (section 6); who may ask for a
migration, every stage naming who may ask for `promote` and `rollback`, and an App only as a
reporter (section 7); the approvers of a stage may be added by a declaration, and the policy example
of record is ADR-026's (section 8); the form of a rejection (section 10); the journal under
automatic writes is left to ADR-027 (Consequences).

### 2026-10-09 — `changelog`, `bom`, the change log in a PR, attested results

ADR-027 decides the release record. For this ADR: the read commands `gora changelog` and `gora bom`
with their API (sections 2 and 4); the change log in a promotion pull request's body, and the squash
commit's body is the trailers alone (section 6); each run also reads the registry, where each
verification result is attested, and an expired required check runs again (section 8); what the
model needs of a host, a plan and a registry (section 11); the journal of a pipeline run
(Consequences).

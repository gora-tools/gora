# ADR-022: Lifecycle on Trunk — Candidates, Promotion, Platform States

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-03
**Amends:** ADR-003, ADR-004, ADR-008, ADR-011, ADR-013, ADR-018, ADR-019
**Amended:** 2026-10-04 — gates, the stage loop, rollback rejects, any code host (ADR-023)

---

## Context

The ADR set says what a promoted artifact is (ADR-003, ADR-004), when a digest is trusted
(ADR-018), how a promotion becomes a pull request (ADR-013) and who writes release state
(ADR-019). It leaves the lifecycle around them open:

- When a candidate comes into existence, and who creates its tag. Today the operator pushes
  `v0.2.0-rc.N` by hand and the tag starts the build (ADR-011).
- Whether a candidate is written into the manifest. If it is, every merge to `main` needs a
  pull request to the manifest (ADR-019 section 2 left this open).
- How promotion stays linear while `main` keeps moving: which candidate may become final when
  newer ones exist.
- Which commit a version tag names. ADR-018 section 7 says the merge commit of the promotion
  pull request, which in the single-repository shape holds changes the digest was not built
  from.
- What rollback is, what a platform's version is, which branches exist, how GORA acts on
  GitHub, and what GORA demands of a project that connects.

Research behind this ADR (2026-10-03, sources below): trunk-based development (release from
trunk, branch for release), SemVer 2.0, Conventional Commits, the GitHub documentation on merge
queues, auto-merge, `GITHUB_TOKEN` and GitHub App tokens, Kargo (Freight promoted per stage),
the deployment pipeline of Humble and Farley, parallel change (expand and contract) and the Go
module tag convention. The decisions were taken with the maintainer on 2026-10-03.

[`docs/architecture/release-model.md`](../architecture/release-model.md) is the map of this
model: vocabulary, diagrams and every law with its enforcement point. This ADR is the decision
the map follows.

## Decision

### 1. One trunk, fixed forward

- `main` is the only long-lived branch. There are no release, hotfix or develop branches, no
  cherry-picks and no backports.
- A fix is a pull request to `main` and a candidate like any other. It is promoted through the
  same stages, quickly, and none is skipped. A defect in a stage is contained by a rollback
  (section 10) and cured forward.
- `main` is always promotable: unfinished work is behind feature flags. Flags stay the
  application's concern (ADR-004); GORA requires them only as a condition of connecting.
- **Not in GORA's core:** two major lines maintained in production at once (for example `v10`
  and `v11`). That is two trunks; if it is ever needed, it is decided in its own ADR.

### 2. `main` is tested as it lands

- Every commit on `main` is tested as the exact tree it becomes. The mechanism is the merge
  queue: a queued pull request is tested in its merge group and merged in order, and CI runs on
  the `merge_group` event. GitHub offers the merge queue in public repositories of an
  organization and in private repositories on GitHub Enterprise Cloud. Where there is no queue,
  required status checks in strict mode (the branch must be up to date with `main`) give the
  same guarantee, one pull request at a time. GORA's own repository uses strict mode now
  (ruleset `protect-main`) and moves to the queue when it has one.
- Pull requests are squash-merged: one pull request is one commit, and the pull request title
  becomes its subject. The title is a Conventional Commit, checked by a required check
  (section 5).

*Amended 2026-10-04 (ADR-023 section 12):* the mechanism of L3 is required checks in strict mode,
on every host and plan that offers protected branches with required checks. A merge queue keeps
the same guarantee with more throughput and is used where the host offers one; it is not a
condition, and GORA's own repository does not wait for one. GORA reads from the host's rules
whether `main` has a queue and works in either mode (ADR-023 section 12).

### 3. GORA does not build; the artifact contract

- GORA never compiles or builds (ADR-001). The project's CI builds, in whatever language the
  project uses: Java, Go, .NET, Node, Python or any other. GORA reads only what no language
  changes: commits, changed paths, the digest and its evidence (ADR-018).
- An artifact is an OCI artifact identified by its digest; for GORA itself, an image index
  (ADR-021).
- **The artifact does not carry its version.** One digest is first `X.Y.Z-rc.N` and then
  `X.Y.Z`; a version built into it (a jar manifest, an assembly version, `package.json`) would
  be wrong after the sign-off, or would force a rebuild. The artifact carries its source commit
  as the OCI annotation `org.opencontainers.image.revision`, and GORA refuses a digest whose
  revision is not the commit its provenance names. An application that shows its version reads
  it from its environment at deploy time.
- A library published to a package registry (Maven, npm, NuGet, PyPI) is not a deployable
  artifact and is outside this model; it reaches production inside one.

### 4. A candidate per merge

- `release-policy.yaml` declares each artifact and its **inputs** as path globs. Release files
  (the manifest, the policy, any file GORA writes) are never inputs, so a merged promotion
  never creates a candidate.
- A **candidate** is a commit on `main` whose tree differs, in an artifact's inputs, from the
  commit of that artifact's newest registered candidate; an artifact without one takes the first
  commit merged to `main` after it is declared. The rc tags that exist before registration is
  implemented count as registered. Comparing with the newest candidate, not with the parent,
  keeps a change whose own build was never registered: the next commit carries it.
- A candidate is built once, from that commit of `main`. Whether a commit changed an artifact's
  inputs is answered by GORA from the policy in the release repository: the workflow asks GORA
  and does not repeat the globs in its own path filters, so the inputs are declared once.
- **A feature branch produces no artifact.** What CI builds for a pull request is a test: it is
  not pushed where candidates are taken from, not tagged and not promoted. GORA refuses a
  digest whose provenance names a ref other than `main`.
- A build that fails produces no candidate and no tag. `main` is red and is fixed forward.

### 5. Versions are computed

GORA computes every version itself, from the commits. No external release tool takes part.

- **Line.** From the newest final version of the artifact on `main`, over the first-parent
  commits since it that changed the artifact's inputs, GORA takes the highest bump: a breaking
  change (`!` after the type or scope, or a `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer) is
  major, and minor while the major version is `0`; `feat` is minor; any other type is patch. A
  change to inputs is at least a patch. Before an artifact's first final version, its line is
  the `initial` version in policy.
- **Source.** GORA reads the commit message on `main`. The repository makes the pull request
  title the squash commit's subject and the pull request body its body (a connect condition), so
  a breaking-change footer written in the body reaches `main`. A subject that is not a
  Conventional Commit counts as a patch, and GORA reports it.
- **Number.** `N` in `X.Y.Z-rc.N` is one more than the highest `N` of that line among the
  existing tags.
- **Grammar and order.** SemVer 2.0 without build metadata (ADR-003). Precedence follows
  SemVer: numeric identifiers compare as numbers (`rc.10` follows `rc.9`), a new line follows
  the old one (`1.1.0-rc.1` follows `1.0.1-rc.3`), and `X.Y.Z-rc.N` precedes `X.Y.Z`.
- **Tag names.** A repository with one artifact tags `vX.Y.Z-rc.N` and `vX.Y.Z`. A repository
  with several prefixes the artifact name, `api/vX.Y.Z`, as Go does for a module in a
  subdirectory. Registry tags carry no `v`. Artifact names and stage names are disjoint, so an
  artifact tag never looks like a state tag (section 9).
- The change log is produced by GORA from the same commits; its form is decided with the
  release record.

### 6. The candidate record

- **Registration.** When GORA has verified a build's evidence (ADR-018 section 4), it computes
  the version and:
  - creates the annotated git tag `vX.Y.Z-rc.N` on the **source commit**, its message naming
    the artifact, the image, the digest and the policy digest, so Git alone lists every
    candidate;
  - adds the registry tag `X.Y.Z-rc.N` to the digest;
  - attaches the verification summary attestation to the digest (ADR-018 section 4).
- **A candidate is not written to the manifest.** A candidate is not the state of any stage;
  recording it there would turn every merge into a pull request to the manifest.
- **Order.** GORA registers candidates in the first-parent order of `main` and never tags an
  older commit after a newer one. A build that finishes after a newer commit of the same
  artifact was registered is not registered: its change is already in the newer candidate.
  Registration and final tagging of one artifact run one at a time, and a merged sign-off counts
  as the artifact's newest final version before its tag exists, so no rc of an older line can
  be registered after it.
- **Tags never move** (ADR-018 section 7). A tag ruleset forbids updating or deleting version
  tags, and only GORA's App may create them (section 14).

### 7. Promotion and gates

- Policy declares the ordered stages, a gate per stage, `auto` or `human`, and the sign-off gate
  (section 8).
- A promotion is a pull request, opened by GORA, that moves into a stage the digest of the stage
  before it; the first stage receives the newest registered candidate. Evidence is verified
  again at each promotion (ADR-018 section 4).
- **At most one open promotion pull request per artifact and stage.** When a newer candidate
  reaches the stage before, GORA closes the older pull request and deletes its branch.
- **Gates.** For `auto`, GORA enables the platform's auto-merge on the pull request in the run
  that opens it, while its checks are still pending, and the platform merges it once the
  required checks pass (through the queue, where there is one). For `human`, a person merges
  it. GORA's adapter has no merge operation (ADR-013): GORA never merges by its own decision.
  An automatic merge is the policy's decision, which is reviewed in Git like any change.
- GORA keeps its open promotion branches current: when `main` moves, it recomputes each one
  against the new base (ADR-013, "computed from the base branch"), so strict required checks do
  not leave a pull request behind.
- GORA posts a required status on every pull request to `main`: success for a pull request that
  promotes nothing, and for a promotion, whether it is still valid. A candidate superseded before
  the sign-off gate, or a rejected one, turns it to failure, and GORA closes the pull request.

*Amended 2026-10-04 (ADR-023 sections 6, 8 and 9):* the gates are `auto` and `approval`;
`human` is replaced. For `approval`, the policy names the groups whose reviews are required and
how many. GORA's required status turns green when the other required checks pass and those
reviews are given, and the code host's auto-merge, enabled when GORA opens the pull request,
merges it. Nobody merges by hand, and GORA still never merges. An approval binds to the
promotion, not to a commit: it survives GORA's recomputation of the branch against a newer
`main`, and counts only while the branch carries exactly the promotion GORA computed. GORA opens
the first stage's promotion at registration, and the promotion into each next stage by itself
once every verification of the stage before is green: the stage loop. The loop moves only
forward, to a candidate registered after the one the stage holds. The branch keys are those of
ADR-023 section 6.

### 8. The sign-off gate and supersession

- Stages before the sign-off gate carry the rc version; stages after it carry the final version,
  on the same digest. Merging the pull request across the sign-off gate is the sign-off.
- **Only the newest non-rejected candidate of the artifact passes the sign-off gate.** Every
  merge that changes inputs gets an rc version at once, so if an older candidate became final,
  a newer rc of the same line would order before the final version it postdates, and versions
  on `main` would stop growing with its history. An older candidate is superseded and never
  moves forward. This is not a freeze: merging never stops, and QA promotes the newest
  candidate and tests what changed since the last one. Supersession ends at the sign-off gate:
  a final version is never superseded and moves on through the stages after the gate, whatever
  newer candidates exist. ADR-003 already holds one active candidate per line; this section
  makes it the rule of the gate.
- **Rejection** is a person's decision, recorded by GORA in the release repository through a
  pull request (release state, ADR-019 section 2). Its form is decided with the release record.
  A rejected candidate or version never moves forward, is never a rollback target, and drops out
  of "newest".
- When the sign-off pull request is merged, GORA creates the annotated tag `vX.Y.Z` on the same
  source commit and the registry tag `X.Y.Z` on the same digest, and emits the verification
  summary attestation and the notification (ADR-018 section 7).

*Amended 2026-10-04 (ADR-023 sections 8 and 10):* nobody merges by hand. The sign-off is met as
the gate says: at an `approval` gate, the approval on the pull request across the sign-off gate
is the sign-off, and the code host's auto-merge merges it. GORA opens the promotion of the
newest candidate; QA tests what changed and approves. A rejection is asked for by a person whom
policy allows to reject; that request is the decision, and the rejection pull request is merged
by the host's auto-merge once the other required checks pass. A rejection holds the digest: the
rc version and the final version of one digest are rejected together. A rejected final version
drops out of "newest" at this gate but stays in the line, so the next version is computed after
it (section 5).

### 9. The manifest and platform states

- The manifest holds, per stage, the digest and version there and a pointer to their
  verification. It changes only through a GORA promotion pull request, rollbacks included. The
  manifest schema version that implements this ADR has no `candidate` block and no `release`
  field, since the line is computed; v0.1 manifests remain readable (ADR-003).
- **Platform states.** The last stage of the promotion graph is named `prod` (a connect
  condition). Every merged change of `prod` is tagged `prod/YYYY.MM.DD.N` on the manifest
  commit. `YYYY.MM.DD` is the UTC committer date of that
  commit; `N` is its position among the same day's commits that changed `prod`, in first-parent
  order. The tag is computed from history, so computing it again gives the same tag; there is
  no limit per day; states are ordered by date, then by `N` as a number.
  A state tag never moves and is protected by a tag ruleset like a version tag.
- Platform states are CalVer and artifacts are SemVer. They are separate namespaces and never
  compared: a state says when and what, an artifact version promises compatibility. A platform
  has states, not a version. This replaces the platform version of ADR-003 (`2026.3.0` with an
  rc suffix).
- Artifacts linked by `dependsOn` (ADR-003) are promoted in one group pull request, in
  dependency order: one commit, one state.

### 10. Rollback

- A rollback is a promotion forward to a digest the stage held before, according to the
  manifest's history, and that is not rejected. It opens a promotion pull request; its merge is
  a new manifest commit and, for `prod`, a new state tag. History is never rewritten.
- **Kind A, one artifact:** the stage's entry for that artifact returns to an earlier version.
- **Kind B, a platform state:** every application artifact of the stage returns to its digest
  in a named state tag.
- Migration artifacts never roll back (section 11). Kind B restores applications only, and GORA
  refuses it if an application of that state does not run on the current schema.
- A rollback rejects nothing. Rejecting the version rolled back from is a separate decision
  (section 8); until it is made, that version may be promoted again.
- GORA computes the target state and opens a promotion pull request; it does not `git revert` the
  manifest. A revert undoes one commit, which can hold more or less than the stage needs, and
  bypasses the checks a promotion runs.
- The command shapes are decided in the command-model ADR (ADR-023).

*Amended 2026-10-04 (ADR-023 section 10):* a rollback is asked for, with a reason, by a person
whom policy allows for that stage; the request is the decision, and the rollback pull request is
merged by the host's auto-merge once the other required checks pass. **A rollback rejects:**
kind A rejects the version it leaves; kind B rejects, for each artifact the request names (at
least one), the version the stage held before the rollback, and a named artifact that the
rollback does not move is refused; every other artifact it moved back is not rejected, and the
loop proposes the version the stage before holds forward again, through the stage's gate. The
rejections are recorded in the rollback's own pull request. "A rollback rejects nothing" and
"may be promoted again" above are replaced: a version rolled back from never comes back, and the
way on is a fix and a new candidate through every stage.

### 11. Migrations

- A database migration is its own artifact: an image of the project's migration tool (Flyway,
  Liquibase, Alembic, goose, an EF Core bundle), with its own inputs, SemVer line and stages.
  GORA orders it; the CD system runs it (a Kubernetes Job, an Argo CD PreSync hook, a pipeline
  step).
- **Forward only:** no down migration runs in a stage.
- **Expand, then contract:** schema `N` works with the application version before it, so an
  application can roll back without touching data. A contract step is promoted only when no
  rollback target in that stage needs the old schema.
- **Order:** the application `dependsOn` its migration, and the group promotion applies the
  migration first.
- How a migration artifact declares the oldest application it serves is decided with the
  Release BOM schema (ADR-003 v0.2); GORA uses it to refuse a rollback below it.

### 12. No release train

GORA has no schedules, windows, freezes or batches, and its policy schema refuses fields for
them. A candidate moves when it passes its gates. A person who does not want a change in a stage
does not merge its promotion pull request; GORA neither holds changes back nor groups them,
except the `dependsOn` group of section 9, which is atomicity, not a schedule.

*Amended 2026-10-04 (ADR-023 section 8):* nobody merges by hand. A person who does not want a
change in a stage does not approve its promotion (an `approval` gate); an `auto` stage takes
every candidate that passes its checks. The stage loop's periodic re-check is reconciliation: it
never holds back or batches a candidate, so it is not a schedule.

### 13. Branch hygiene

- The repository deletes a head branch when its pull request is merged; GORA checks the setting
  when a project connects.
- GORA deletes its own `gora/*` branches when their pull request is merged or closed.
- GORA reports unmerged branches older than the age set in policy. It never deletes them: a
  person's unmerged work is theirs to delete.

### 14. GORA's identity on GitHub

GORA acts on GitHub repositories (branches, tags, pull requests, statuses, auto-merge) as its own
**GitHub App**, never with `GITHUB_TOKEN` and never with a person's token.

- A tag pushed with `GITHUB_TOKEN` starts no workflow, and a pull request opened with it starts
  its workflows only in an approval-required state, so an `auto` gate could not pass without a
  person. A personal access token is a person's identity and lives long.
- The workflow that runs GORA creates an installation token with
  `actions/create-github-app-token`; the token expires after an hour. The App holds the minimal
  permissions its operations need (expected: contents and pull requests read and write, commit
  statuses read and write, metadata read), fixed when the App is built.
- The App's private key is a secret of the release repository only. In the shared shape
  (ADR-019 section 1) the App is also installed on the project repositories, where the version
  tags go.
- Registry writes (the registry tag of section 6) use the registry's own credential; for GHCR in
  GitHub Actions that is the workflow's `GITHUB_TOKEN` with `packages: write`. A registry write
  starts nothing GORA depends on.
- Another platform (Bitbucket) follows the same principle with its own mechanism, decided with
  its connector.
- This replaces the deferral in ADR-013 "Auth".

*Amended 2026-10-04 (ADR-023 sections 7 and 11):* the rule holds on every code host: GORA acts
only as its own machine identity on that host, with the narrowest scope the host allows and a
credential that expires, never with a person's token. On GitLab that is a project or group
access token (a bot user), on Bitbucket Cloud a repository or workspace access token. The secret
lives only where GORA's writes run: the release repository's pipeline, where only the pipeline
definition on `main` can reach it, or GORA's server. On GitHub the App also needs the
organization permission "Members: read", to check who may ask for an action and whose review
counts as an approval.

### 15. Laws and policy

- A **law** is an invariant in GORA's code, the same for every project and not configurable.
  **Policy** is a project's declaration inside the laws: artifacts and inputs, stages, gates,
  the sign-off gate, the initial version, the branch age.

  *Amended 2026-10-04 (ADR-023 sections 7 and 8):* and, per stage, the approvers of an
  `approval` gate and the verifications; per action (and, for `promote` and `rollback`, per
  stage), the groups who may ask for it.
- A policy that would break a law is refused when the project connects. GORA sets the conditions
  and a project meets them (ADR-019 section 3).
- The laws, each with its enforcement point and whether it is in force or planned, are listed in
  `docs/architecture/release-model.md` section 10. A law changes only through an ADR.

### 16. Vocabulary

GORA's verb is **promote**. The gate where an rc version becomes final is the **sign-off gate**.
"Release" is not an event or a verb of this model. It remains in names already fixed: the
product, the files `release-manifest.yaml` and `release-policy.yaml`, the `release.yml`
workflow, and nouns of the ADR set (release repository, release files, release state, release
line, Release BOM, release record). Renaming them is a separate decision.

## Why not otherwise

- **Release branches with cherry-picks.** A second line of truth, and a cherry-picked fix ships
  as an artifact that never existed on `main`. Trunk-based development keeps branch-for-release
  for teams that ship on a schedule (monthly, for example) and need bug-fix releases between;
  continuous delivery teams roll forward on trunk, and so does GORA.
- **An rc version assigned when a candidate enters QA**, the labelling of a deployment pipeline
  where each stage pulls the latest good build. It avoids supersession at the sign-off, but what
  runs in `dev` would have no version, only a commit, and the version a build receives would
  depend on when QA takes it. The decision is an rc version on every merge, so every stage
  names what it runs; the newest-only rule of section 8 is what keeps that linear.
- **Candidates recorded in the manifest.** Every merge would need a pull request to the
  manifest, and the manifest would become a build log rather than the state of the stages.
- **Version tags on the merge commit of the promotion pull request** (ADR-018 section 7 as first
  written). In the single-repository shape that commit holds later changes than the digest; a
  version tag must name the tree its digest was built from.
- **A release train or a freeze.** Batching makes every change wait for the slowest one, and a
  freeze is a train with a calendar. Promotion happens when a candidate is ready.
- **One version scheme for artifacts and states.** SemVer on a platform state would promise a
  compatibility that a set of services cannot; CalVer on an artifact tells its consumers
  nothing.
- **A counter for state tags.** A counter depends on what was tagged before and on concurrent
  runs; a position derived from history gives the same answer every time.
- **An external release tool (git-cliff, semantic-release) for versions.** The version is part
  of GORA's contract; computing it from the same commits needs no further dependency and no
  second configuration.
- **`GITHUB_TOKEN` or a personal access token.** See section 14.
- **Deleting stale branches automatically.** It destroys unmerged work that is not GORA's.
- **`git revert` of the manifest as rollback.** See section 10.

## Consequences

Amendments, each recorded in the amended ADR:

- **ADR-003:** candidates leave the manifest; the line and the number are computed (no release
  line edited by hand); the platform version and the train wording are replaced by platform
  states; the prior-art hotfix counter and lock are not adopted.
- **ADR-004:** the sign-off gate and supersession; rollback kinds A and B replace the listed
  mechanisms.
- **ADR-008:** `docs/architecture/` holds the release model.
- **ADR-011:** the build runs on a commit to `main`; GORA creates the rc tag after verification.
- **ADR-013:** gates `auto` and `human`; one open promotion pull request per artifact and stage;
  a required status on every pull request to `main`; promotion branches kept current; the key
  gains the artifact; the GitHub App is GORA's identity.
- **ADR-018:** section 1 (where a candidate is recorded), section 3 (the trusted signer at
  `refs/heads/main`), section 4 (the order of promotion and the sign-off gate), section 7 (build
  trigger, annotated tags on the source commit), Consequences (pointer per stage entry).
- **ADR-019:** section 2 (how a candidate reaches the default branch: it does not; the refs GORA
  creates; an `auto` gate is merged by the platform's auto-merge).

Implementation, in later pull requests:

- `release.yml` builds on a commit to `main` that changed inputs, not on a pushed tag; CI runs on
  `merge_group` once there is a queue.
- The commands for registration, rejection, rollback A and B and the branch report are designed
  once, for CLI, API and Slack alike, in the command-model ADR (ADR-023). The release record
  (rejections, change log, rollback records) has its own ADR.
- `release-policy.yaml` gains artifacts and inputs, the initial version, stages and gates, the
  sign-off gate and the branch age; the manifest schema drops `candidate` and `release`.
- The maintainer sets what only a person may set: the GitHub App, the repository settings
  (delete branches after merge, allow auto-merge; squash commits keep taking the PR title and
  body), the tag ruleset for `prod/*`, tag creation restricted to the App, and a required
  PR-title check.
- Until registration exists, the operator pushes rc tags by hand and the tag starts the build;
  the tags pushed so far stay valid (ADR-011).

## Sources

- Trunk-based development, release from trunk: https://trunkbaseddevelopment.com/release-from-trunk/
- Trunk-based development, branch for release: https://trunkbaseddevelopment.com/branch-for-release/
- Semantic Versioning 2.0.0 (precedence, rule 11; major version zero, rule 4): https://semver.org/
- Conventional Commits 1.0.0: https://www.conventionalcommits.org/en/v1.0.0/
- GitHub, managing a merge queue (availability, `merge_group`):
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- GitHub, `GITHUB_TOKEN` (events it triggers, approval-required runs):
  https://docs.github.com/en/actions/concepts/security/github_token
- GitHub, automatically merging a pull request:
  https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request
- `actions/create-github-app-token`: https://github.com/actions/create-github-app-token
- Kargo, Freight: https://docs.kargo.io/user-guide/how-to-guides/working-with-freight
- J. Humble, D. Farley, *Continuous Delivery* (2010), chapter 5, the deployment pipeline.
- M. Fowler, Parallel Change (expand and contract): https://martinfowler.com/bliki/ParallelChange.html
- Go modules, version tags of a module in a subdirectory: https://go.dev/ref/mod#vcs-version
- OCI image annotations (`org.opencontainers.image.revision`):
  https://github.com/opencontainers/image-spec/blob/main/annotations.md

## Amendments

### 2026-10-04 — gates, the stage loop, a rollback rejects, identity on any code host

ADR-023 decides the command model and the stage loop. Sections 2, 7, 8, 10, 12, 14 and 15 are
amended in place:

- strict required checks are the mechanism of L3; a merge queue adds throughput (section 2);
- a gate is `auto` or `approval` (reviews by the groups the policy names, bound to the
  promotion, then the host's auto-merge); nobody merges by hand; GORA opens each next
  promotion once the stage before is verified, only forward (sections 7, 8 and 12);
- a rollback or a rejection is merged on the request of a person policy allows; a rollback
  rejects the version it leaves (kind A) or the versions the request names (kind B) (sections 8
  and 10);
- a rejection holds the digest (rc and final together), and a rejected final version stays in
  the line (section 8);
- GORA's machine identity rule holds on every code host, its secret only where its writes run
  (section 14);
- policy gains approvers, verifications and who may ask for what (section 15);
- the Consequences line on ADR-013 reads `auto` and `approval`.

Law L24 (no artifact stalls unseen) is added in release-model.md.

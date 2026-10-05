# Release model

This is the map of how an artifact travels from a merge on `main` to production, and of the
laws that hold on the way. The decisions behind it are Architecture Decision Records:
[ADR-022](../adr/0022-lifecycle-on-trunk.md) decides this model and amends the ADRs it
changes; [ADR-023](../adr/0023-command-model-and-stage-loop.md) decides the stage loop and the
commands. If this map and an ADR disagree, the ADR is right and the map has a defect.

**The rule for every change.** A change, a recommendation or a plan that contradicts a law
below stops there: it is raised with the maintainer, never worked around and never
reinterpreted. A law changes only through an ADR.

## 1. Vocabulary

GORA's verb is **promote**. In this model an artifact is promoted or it stays where it is;
"release" is not an event and not a verb. The word survives only in names already fixed: the
product, the files `release-manifest.yaml` and `release-policy.yaml`, the `release.yml`
workflow, and nouns the ADRs use (release repository, release files, release state, release
line, Release BOM, release record).

| Term | Meaning |
| --- | --- |
| Project | One release repository: one manifest, one policy, one system whose artifacts are promoted through the same stages, depend on each other and roll back together. The same for one artifact or a hundred; the artifacts may be built in any number of code repositories. A separate system is a separate project. |
| Artifact | One deployable unit, identified by an OCI digest (for GORA itself, an image index). Written in any language: GORA sees the digest and its evidence, never the build. |
| Inputs | The paths whose change changes an artifact, declared per artifact in `release-policy.yaml` as path globs. Release files are never inputs. |
| Candidate | A commit on `main` whose artifact inputs differ from those of the artifact's newest registered candidate, built once by the project's CI, verified and registered by GORA. |
| Registration | GORA's step that turns a verified build into a candidate: it computes the version and creates the tag. |
| rc version | `X.Y.Z-rc.N`, a candidate's identity. |
| Final version | `X.Y.Z`, the same digest after the sign-off gate. |
| Stage | An environment of the promotion graph declared in policy (`dev`, `qa`, `preprod`, `prod` in the examples). |
| Promotion | A pull request, opened by GORA, that moves a digest into a stage. Never a build. |
| Gate | What a promotion PR needs before the code host's auto-merge merges it: `auto`, the other required checks; `approval`, those and the reviews of the groups the policy names. Nobody merges by hand. |
| Verification | A check of a stage after its deployment (`deploy`, `smoke`, `e2e`), reported by the CD or a test system as the commit status `<stage>/<artifact>/<name>`; for `deploy`, Argo CD's health may stand in. When all are green, GORA opens the promotion into the next stage. |
| Sign-off gate | The one gate, set in policy, where an rc version becomes the final version. Meeting it is the sign-off: at an `approval` gate, the approval on that PR. |
| Supersede | A newer candidate of the same artifact replaces an older one that has not crossed the sign-off gate; the older one never moves forward again. A final version is never superseded. |
| Reject | A recorded decision of a person whom policy allows, that a candidate or a version is bad (every version of that digest, rc and final): it never moves forward and is never a rollback target. A kind A rollback rejects the version it leaves; kind B, the versions of the artifacts the request names. |
| Platform state | A manifest commit that changed `prod`, tagged `prod/YYYY.MM.DD.N`. |
| Rollback | A forward promotion to a digest the stage held before, asked for by a person whom policy allows, in an emergency. Kind A: one artifact, whose version left is rejected. Kind B: a whole platform state, rejecting the versions of the artifacts the request names. |
| Law | An invariant in GORA's code. The same for every project; not configurable. |
| Policy | A project's declaration inside the laws: artifacts and inputs, stages, gates and their approvers, verifications, the sign-off gate, who may ask for what. |

## 2. The lifecycle

```mermaid
flowchart LR
  PR["pull request<br/>feature branch"] -->|"squash, tested as it lands"| M["commit on main"]
  M -->|"inputs changed"| B["project CI builds once<br/>digest + provenance"]
  B --> V["GORA verifies the evidence"]
  V -->|"tag vX.Y.Z-rc.N<br/>on the source commit"| C(["candidate"])
  C -->|"gate: auto"| DEV["dev"]
  DEV -->|"gate: approval"| QA["qa"]
  QA ==>|"sign-off gate<br/>final version, same digest"| PRE["preprod"]
  PRE -->|"gate: approval"| PROD["prod"]
  PROD -.->|"tag prod/YYYY.MM.DD.N"| S[("platform state")]
```

1. A developer works on a short-lived feature branch. Its CI builds and tests, but nothing it
   builds is registered, tagged or promoted (L5).
2. The pull request is squash-merged; `main` receives exactly the tree that was tested (L2,
   L3). One pull request is one commit on `main`, and its title is a Conventional Commit (L11).
3. If the commit changed an artifact's inputs (L8), the project's CI builds that artifact once
   and hands GORA the digest with its provenance (L7, L9).
4. GORA verifies the evidence (ADR-018), computes the version (L11) and tags the source commit
   `vX.Y.Z-rc.N` (L10). The candidate exists.
5. GORA opens the promotion PR to the first stage when it registers the candidate. `dev` is
   `gate: auto`: the code host merges the PR once the required checks pass (L15).
6. Each next stage takes the digest of the stage before it, through a promotion PR that GORA
   opens once the stage before is verified, merged when its gate is met (L12, L14, L15, L24).
7. At the sign-off gate the candidate must be the newest non-rejected candidate of its artifact
   (L13). The PR records the final version on the same digest; its approval is the sign-off.
   When it is merged GORA tags the same source commit `vX.Y.Z` and adds the registry tag to the
   same digest (L10, L12).
8. A merged promotion that changes `prod` is a platform state, tagged `prod/YYYY.MM.DD.N` (L17).
9. The CD system applies what the manifest says (ADR-019 section 6) and reports the result;
   the stage's verifications report theirs. GORA never deploys and never tests (section 4).

## 3. Branches and candidates

```mermaid
gitGraph
  commit id: "c6" tag: "v1.0.0"
  commit id: "c7 fix" tag: "v1.0.1-rc.1"
  branch feature-a
  checkout feature-a
  commit id: "a1"
  commit id: "a2"
  checkout main
  commit id: "c8 feat" tag: "v1.1.0-rc.1 + v1.1.0"
  commit id: "c9 docs"
  commit id: "c10 fix" tag: "v1.1.1-rc.1"
```

- `main` is the only long-lived branch (L1). A feature branch ends in one squash commit (`c8` is
  `feature-a`) and is deleted when it is merged (L6); `a1` and `a2` never become artifacts
  (L5).
- `c9` changed no input of the artifact, so it is not a candidate (L8).
- Every version tag sits on the source commit its digest was built from: `v1.1.0-rc.1` and the
  final `v1.1.0` share `c8` (L10).
- A hotfix is `c10`: an ordinary pull request, promoted through the same stages (L19).

## 4. Stages, gates and linearity

```mermaid
flowchart LR
  C(["candidate"])
  subgraph RC["rc version: X.Y.Z-rc.N"]
    DEV["dev"]
    QA["qa"]
  end
  subgraph FINAL["final version: X.Y.Z, the same digest"]
    PRE["preprod"]
    PROD["prod"]
  end
  C -->|"gate: auto"| DEV
  DEV -->|"gate: approval"| QA
  QA ==>|"sign-off gate: newest non-rejected only"| PRE
  PRE -->|"gate: approval"| PROD
```

The stages, their gates and the place of the sign-off gate are policy. Stages before the
sign-off gate carry rc versions; stages after it carry the final version. The digest never
changes on the way (L12).

**Why only the newest candidate passes the sign-off gate.** Every merge that changes inputs
gets an rc version at once. Suppose a commit is `1.0.1-rc.7` and a later one is `1.0.1-rc.9`.
If the first became `1.0.1`, SemVer would order `1.0.1-rc.9` before `1.0.1` although it
contains more, and versions on `main` would stop growing with its history. So the sign-off gate
accepts only the newest non-rejected candidate of the artifact (L13); an older one is superseded
and never moves forward. This is not a freeze: merging never stops. GORA opens the promotion of
the newest candidate; QA tests what changed since the last one and approves. A rejected
candidate drops out: if `1.0.1-rc.9` is rejected, the newest is the candidate before it.
Supersession ends at the sign-off gate: a final version is never superseded and moves on through
the later stages whatever is newer behind it (step 8 below).

**Registration follows history.** GORA registers candidates in the first-parent order of
`main` and never tags an older commit after a newer one (L10). A commit whose build finishes
after a newer commit was registered is not registered: its change is already in the newer
candidate. The version is computed at registration, from the last final version: a commit
registered after a sign-off gets the next line, never a pre-release of a version that already
exists. Registration and final tagging of one artifact run one at a time, and a merged sign-off
counts as the newest final version before its tag exists.

**The stage loop.** A merged promotion changes only the manifest; this loop moves the artifact
on, the same for every stage (ADR-023 section 8):

```mermaid
flowchart LR
  P["promotion PR<br/>opened by GORA"] -->|"gate met:<br/>host auto-merge"| M["manifest commit"]
  M --> D["CD deploys the stage's digest"]
  D --> V["verifications report<br/>deploy, smoke, e2e"]
  V -->|"all green"| N["GORA opens the PR<br/>into the next stage"]
  V -->|"one red"| F["stage failed: shown in status;<br/>fix forward or roll back"]
  N --> P
```

- **Gate.** `auto`: the other required checks pass. `approval`: they pass and the groups the
  policy names have approved the PR. GORA's required status turns green, and the code host's
  auto-merge, enabled when GORA opened the PR, merges it. The sign-off is the approval on the PR
  across the sign-off gate. Approvals of a promotion are reviews, never labels.
- **An approval binds to the promotion.** GORA recomputes its branch whenever `main` moves; an
  approval given on GORA's commit for that promotion stays while the branch carries exactly
  that promotion. A commit anyone else pushes to it turns GORA's status red. Only GORA's
  identity can set GORA's status.
- **Verifications.** The policy lists them per stage. The CD and the test systems run them and
  report each as the commit status `<stage>/<artifact>/<name>` on the manifest commit that set
  the artifact's entry in the stage, or on a later commit where that entry is unchanged; GORA
  counts a status only from the identity the policy names for it. For Argo CD, GORA can read the
  application's health instead of a reported `deploy`.
- **Forward only.** The loop opens a promotion only for the newest non-rejected candidate
  registered after the one the stage holds. Moving a stage back is a rollback, which a person
  asks for and which rejects (section 7).
- **No artifact stalls unseen (L24).** Every artifact in every stage is in exactly one state,
  the first that applies, shown by `gora status` with the time spent in it: at the gate, with
  one open promotion or rollback PR; blocked, with the reason (for example the stage holds a
  rejected or failed digest, a verification before it failed, a superseded or rejected
  candidate, a PR a person closed); waiting for the stage before to verify; in sync (nothing
  newer may enter).
- **When it runs.** On every event (a registration, a merge, a status, a review) and as a
  periodic reconciliation, so a missed event delays the loop but never strands an artifact. The
  re-check never holds back or batches a candidate, so it is not a schedule (L20). Each run
  computes from Git and the code host alone and keeps no state of its own.

One artifact, step by step (the history of section 3):

| Step | Event | dev | qa | preprod | prod |
| --- | --- | --- | --- | --- | --- |
| 1 | Start | | | 1.0.0 | 1.0.0 |
| 2 | `c7` (`fix`) registered as `1.0.1-rc.1`; dev is `auto` | 1.0.1-rc.1 | | 1.0.0 | 1.0.0 |
| 3 | dev verified; GORA opens the promotion into qa; QA approves it | 1.0.1-rc.1 | 1.0.1-rc.1 | 1.0.0 | 1.0.0 |
| 4 | `c8` (`feat`) registered as `1.1.0-rc.1`. `1.0.1-rc.1` is superseded: GORA closes its open sign-off PR, if any, and refuses a new one | 1.1.0-rc.1 | 1.0.1-rc.1 | 1.0.0 | 1.0.0 |
| 5 | QA approves `1.1.0-rc.1` into qa, tests the change and approves the sign-off PR; the host merges it: `1.1.0`, same digest | 1.1.0-rc.1 | 1.1.0-rc.1 | 1.1.0 | 1.0.0 |
| 6 | `c9` (`docs`) changes no input: no candidate | 1.1.0-rc.1 | 1.1.0-rc.1 | 1.1.0 | 1.0.0 |
| 7 | `c10` (`fix`) registered as `1.1.1-rc.1`, computed after the final `1.1.0` | 1.1.1-rc.1 | 1.1.0-rc.1 | 1.1.0 | 1.0.0 |
| 8 | Promotion of `1.1.0` to prod approved and merged; state `prod/2026.10.09.1`. `1.1.1-rc.1` is newer, but a final version is never superseded | 1.1.1-rc.1 | 1.1.0-rc.1 | 1.1.0 | 1.1.0 |

## 5. Versions

Two kinds of version, in separate namespaces, never compared with each other:

| Namespace | Example | Points at | Answers |
| --- | --- | --- | --- |
| Artifact version, git tag | `v1.1.0-rc.1`, `v1.1.0`; `api/v1.1.0` when the repository holds several artifacts | the source commit | what changed in the artifact (SemVer: a compatibility promise) |
| Artifact version, registry tag | `1.1.0-rc.1`, `1.1.0` | the digest | the same, for whoever pulls the image |
| Platform state | `prod/2026.10.05.2` | the manifest commit | what a stage held, and since when (CalVer: no promise) |

How GORA computes an artifact version (L11):

- **Line.** From the newest final version tag of the artifact on `main`, rejected or not (a
  rejection drops a version out of "newest" at the sign-off gate, never out of the line), over
  the first-parent commits since it that changed the artifact's inputs, take the highest bump: a
  breaking change (`!` after the type or scope, or a `BREAKING CHANGE:` or `BREAKING-CHANGE:`
  footer) is major, and minor while the major is `0`; `feat` is minor; anything else is patch. A
  change to inputs is at least a patch. Before the first final version the line is the `initial`
  version in policy.
- **Source.** GORA reads the commit message on `main`: the pull request title is its subject and
  the pull request body its body (repository settings, section 9). A subject that is not a
  Conventional Commit counts as a patch and is reported.
- **Number.** `N` is one more than the highest `rc.N` of that line among the existing tags.
- **Order.** SemVer 2.0 precedence: `rc.10` follows `rc.9` (numeric identifiers compare as
  numbers), `1.1.0-rc.1` follows `1.0.1-rc.3`, and `X.Y.Z-rc.N` precedes `X.Y.Z`.
- **No build metadata, nothing inside the artifact.** A version is SemVer without `+…`
  (ADR-003). The artifact does not carry its version (jar manifest, assembly version,
  `package.json`); it carries its source commit as `org.opencontainers.image.revision` (L9),
  because one digest is first `X.Y.Z-rc.N` and then `X.Y.Z`. An application that shows its
  version reads it from its environment at deploy time.

```mermaid
flowchart LR
  SC["source commit c8 on main"]
  T1["git tag v1.1.0-rc.1"] --> SC
  T2["git tag v1.1.0"] --> SC
  R1["registry tag 1.1.0-rc.1"] --> D[("one digest<br/>sha256:...")]
  R2["registry tag 1.1.0"] --> D
  SC -. "built once into" .-> D
```

## 6. Platform states

The manifest says which digest each stage holds. It changes only through a GORA promotion PR
(L16), so every state of a stage is one commit on `main`. A commit that changes `prod`, the
last stage, is tagged `prod/YYYY.MM.DD.N` (L17).

- **Computed from history, not counted.** `YYYY.MM.DD` is the UTC committer date of the
  manifest commit; `N` is its position among that day's commits that changed `prod`, in
  first-parent order. Running the computation again gives the same tag. A promotion every day
  of a month gives `prod/2026.10.01.1` to `prod/2026.10.31.1`; five in one day give `.1` to
  `.5`. There is no limit.
- **Ordered as numbers.** GORA orders states by the date, then by `N` as a number: `.10`
  follows `.9`.
- **A label of a state, not a promise.** CalVer has no major or minor; it says when, which is
  what audit and rollback need. Compatibility is the artifacts' SemVer.
- **Dependent artifacts move together.** Artifacts linked by `dependsOn` (ADR-003) are promoted
  in one group promotion PR: one commit, one state.

The manifest's commits on `main`, each a merged promotion PR:

```mermaid
gitGraph
  commit id: "prod api 1.4.0" tag: "prod/2026.10.05.1"
  commit id: "dev api 1.5.0-rc.1"
  commit id: "prod db 7.1.0, api 1.5.0" tag: "prod/2026.10.05.2"
  commit id: "dev api 1.5.1-rc.1"
  commit id: "rollback prod api 1.4.0" tag: "prod/2026.10.06.1"
```

The rollback of the last commit is possible because `db 7.1.0` was an expand step that
`api 1.4.0` still runs on (section 8).

## 7. Rollback

A rollback is a promotion forward to a digest the stage held before and that is not rejected.
It opens a promotion PR, creates a new manifest commit and, for `prod`, a new state tag. History
is never rewritten (L18).

```mermaid
flowchart TB
  subgraph KA["Kind A: one artifact"]
    A1["gora rollback prod api --to 1.4.0"] --> A2{"held by prod before<br/>and not rejected?"}
    A2 -->|"yes"| A3["PR: api to that digest,<br/>the version left rejected"]
    A2 -->|"no"| A4["refused"]
  end
  subgraph KB["Kind B: a platform state"]
    B1["gora rollback prod --to prod/2026.10.05.1<br/>--reject api"] --> B2{"every application of that state<br/>runs on the current schema?"}
    B2 -->|"yes"| B3["PR: that state's application digests,<br/>migrations unchanged, named versions rejected"]
    B2 -->|"no"| B4["refused"]
  end
  A3 --> N["merge: new manifest commit<br/>new tag prod/YYYY.MM.DD.N"]
  B3 --> N
```

- The commands in the diagram are those of ADR-023 section 2 (section 11 here).
- A rollback is an emergency, asked for with a reason by a person whom policy allows for that
  stage. The request is the decision: the rollback PR merges once the other required checks
  pass (ADR-023 section 10).
- A rollback rejects. Kind A rejects the version it leaves. Kind B rejects, for each artifact
  the request names (at least one), the version the stage held before the rollback; a named
  artifact the rollback does not move is refused. Every other artifact it moved back is not
  rejected: the loop proposes the version the stage before holds forward again, through the
  stage's gate. A rejected version never comes back: the way on is a fix, a new candidate and
  every stage.
- GORA computes the target and opens a PR; it never `git revert`s a manifest commit, which can
  hold the promotions of other artifacts made since (ADR-022 section 10).
- Rollback restores an artifact, not data: migration artifacts never roll back (L21).

## 8. Migrations

A database migration is an artifact like any other (L21): an image of the project's migration
tool (Flyway, Liquibase, Alembic, goose, an EF Core bundle), its own inputs (`db/**`), its own
SemVer line and the same stages. GORA orders it; the CD system runs it (a Kubernetes Job, an Argo
CD PreSync hook, a pipeline step).

```mermaid
flowchart LR
  subgraph G["one group promotion PR"]
    M["db 7.1.0<br/>expand: add a column"] -->|"applied first"| A["api 1.5.0, dependsOn db<br/>uses the column"]
  end
  A -. "later, when no rollback target needs the old schema" .-> K["db 7.2.0<br/>contract: drop the old column"]
```

- **Forward only.** No down migration runs in a stage.
- **Expand, then contract.** Schema `N` works with the application version before it, so an
  application can roll back without touching data. A contract step is promoted only when no
  rollback target in that stage still needs the old schema.
- **Order by `dependsOn`.** The migration comes first in the group promotion.

## 9. What a project meets to connect

GORA sets the conditions; a project that does not meet them is not connected (ADR-019
section 3, L23).

- `main` protected: pull requests only, squash only, linear history, no force-push, no deletion.
- Every commit tested as it lands: required checks in strict (up-to-date) mode, or a merge
  queue where the host offers one, which adds throughput. GORA reads which from the host's rules
  and works in either mode (ADR-023 section 12).
- Required checks include GORA's status, posted on every pull request to `main` (success when
  nothing is promoted), and the Conventional Commits check of the PR title.
- A squash commit takes the PR title as its subject and the PR body as its body.
- The repository deletes head branches after merge, and allows auto-merge.
- Tag rules: version tags and state tags cannot be updated or deleted, and only GORA's machine
  identity creates them.
- GORA's own machine identity on the code host, with its minimal permissions (on GitHub, its
  GitHub App); its secret only where GORA's writes run: the release repository's pipeline,
  reachable only by the pipeline definition on `main` (on GitHub Actions, an environment whose
  deployment branches are `main` only), or GORA's server.
- GORA's required status bound to GORA's identity as its only source (on GitHub, the required
  check's expected source app).
- Only GORA's identity creates, updates or deletes `gora/*` branches (on GitHub, a branch
  ruleset on `gora/**` with GORA's App as its only bypass actor; whether it lets the host
  delete a merged head branch is to verify with the adapter, and GORA deletes its own branches
  anyway).
- The approver groups and the groups who may ask exist on the code host as its teams (a short
  list may name host accounts; an email address is never an identity). The host's own
  required-review rules do not block GORA's PRs: `main` requires no host review, or GORA's
  identity is exempt from that rule alone.
- The CD and test systems report each verification as a commit status, from the identity the
  policy names for it, or, for `deploy`, Argo CD's health is readable.
- CI builds a candidate only from `main`, once, and hands over a digest with provenance; the
  image carries its source commit, not its version.
- Every artifact declared in policy with its inputs, and migrations declared as artifacts.
- The last stage of the promotion graph is named `prod`.
- `main` always promotable: unfinished work behind flags.

A host or a plan that lacks one of these (protected branches with required checks, a required
status only GORA's identity can set, reviews that name their commit, protected tags) does not
connect; `gora connect check` names what is missing.

## 10. Laws

Status: "in force", a mechanism refuses a violation today; "by design", GORA has no operation
that could break it; "contract", the project commits to it when it connects; "follows", it is a
consequence of other laws; "planned", the law is decided and binding, and until its mechanism
exists the operator and the review hold it.

| # | Law | Enforced by | Decided in | Now |
| --- | --- | --- | --- | --- |
| L1 | One trunk. `main` is the only long-lived branch: no release, hotfix or develop branches, no cherry-picks. | GORA registers candidates only from `main` | ADR-022 §1 | planned |
| L2 | Nothing is pushed to `main`. Every change is a pull request, squash-merged; history linear; no force-push, no deletion. | ruleset `protect-main` | ADR-019 §2, ADR-022 §2 | in force |
| L3 | `main` never receives an untested combination: every commit is tested as the exact tree it lands as. | required checks in strict mode; a merge queue where the host offers one | ADR-022 §2, ADR-023 §12 | in force |
| L4 | `main` is always promotable: unfinished work is behind flags; a red `main` is fixed forward. | connect contract; review | ADR-022 §1, §4 | contract |
| L5 | A feature branch produces no artifact: nothing built from it is registered, tagged or promoted, or pushed where candidates come from. | GORA refuses a digest whose provenance names a ref other than `main` | ADR-022 §4 | planned |
| L6 | A merged branch is deleted. GORA deletes its own `gora/*` branches; unmerged branches past the policy's age are reported, never deleted. | repository setting (connect check); GORA | ADR-022 §13 | planned |
| L7 | GORA never builds. The project's CI builds each candidate once and hands over a digest with provenance. | GORA has no build operation; evidence check (ADR-018) | ADR-001, ADR-022 §3 | by design; evidence check planned |
| L8 | A candidate is a commit on `main` whose declared inputs differ from those of the artifact's newest registered candidate. Release files are never inputs. | policy validation; GORA | ADR-022 §4 | planned |
| L9 | An artifact does not carry its version; it carries its source commit (`org.opencontainers.image.revision`). | GORA refuses a digest whose revision is not the source commit | ADR-022 §3 | GORA's own image in force (CI `assemble` refuses an index without its revision); refusal of a digest planned |
| L10 | Only GORA creates version tags: `vX.Y.Z-rc.N` and `vX.Y.Z` on the source commit, in first-parent order, never an older commit after a newer one. A tag never moves. | tag ruleset (no update, no deletion, creation only by GORA's machine identity); GORA | ADR-018 §7, ADR-022 §6 | no update or deletion in force; creation by GORA's identity planned |
| L11 | GORA computes versions from Conventional Commits; a change to inputs is at least a patch. | required PR-title check; GORA | ADR-022 §5 | PR-title check required in `protect-main` (in force); computation planned |
| L12 | Promotion moves the digest of the stage before. Nothing is rebuilt; the final version is a retag of the same digest. | GORA | ADR-004, ADR-018 §4, ADR-022 §7, §8 | same digest across stages in force (`promote`); stage order and final retag planned |
| L13 | Only the newest non-rejected candidate of an artifact passes the sign-off gate. Superseded and rejected candidates never move forward; a final version is never superseded. | GORA's required status; GORA closes a superseded PR | ADR-022 §7, §8 | planned |
| L14 | At most one open promotion PR per artifact and stage; a newer one closes the older. | GORA; one branch per key; while a rollback PR is open, a promotion or another rollback of its artifacts into its stage is refused | ADR-022 §7, ADR-023 §6 | planned |
| L15 | GORA never merges by its own decision, and nobody merges its PRs by hand. The code host's auto-merge merges a GORA PR once its gate is met: `auto`, the other required checks; `approval`, those and the reviews the policy names; a rollback or a rejection, those and the request of a person whom policy allows. | GORA's adapter has no merge operation | ADR-013, ADR-022 §7, ADR-023 §8, §10 | no merge operation in force; gates and "nobody by hand" planned (section 13) |
| L16 | The manifest changes only through a GORA promotion PR, rollbacks included. | required check refusing a release-file change from outside `gora/*` | ADR-019 §2, ADR-022 §9 | planned |
| L17 | Every change to `prod` is one manifest commit, tagged `prod/YYYY.MM.DD.N`. A state tag never moves. | GORA; tag ruleset | ADR-022 §9 | planned |
| L18 | A rollback is a forward promotion to a digest the stage held before and that is not rejected: one artifact (A) or a platform state (B). It rejects the version it leaves (A) or the versions the request names (B). History is never rewritten. | GORA refuses any other target | ADR-018 §4, ADR-022 §10, ADR-023 §10 | planned |
| L19 | A hotfix is an ordinary pull request to `main`, promoted through the same stages. | follows from L1 and L12 | ADR-022 §1 | follows |
| L20 | No release train: no schedules, windows, freezes or batches. A candidate moves when it passes its gates. | GORA has no such feature; the policy schema refuses such fields | ADR-022 §12 | by design; schema planned |
| L21 | Migrations are their own artifacts: forward only, compatible with the application version before them, ordered by `dependsOn`, never rolled back. | the project's CI (forward only); GORA (order, rollback refusal) | ADR-022 §11 | planned |
| L22 | GORA acts on a code host only as its own machine identity there (on GitHub, its GitHub App): never a person's token, the narrowest scope the host allows, a credential that expires, the secret only where GORA's writes run (the release repository's pipeline, from `main` only, or GORA's server). | connect check; a write refuses without that identity | ADR-022 §14, ADR-023 §7, §11 | planned |
| L23 | Policy cannot switch a law off; a policy that would break one is refused when the project connects. | policy schema validation | ADR-022 §15 | planned |
| L24 | No artifact stalls unseen: every artifact in every stage is in exactly one state, the first that applies: at the gate with one open PR, blocked with the reason, waiting for the stage before to verify, or in sync. GORA opens each next promotion itself, only forward, once the stage before is verified. | GORA's stage loop, on events and as a periodic reconciliation; `gora status` | ADR-023 §8, §9, §10 | planned |

## 11. Commands

One grammar for the CLI, the HTTP API and Slack (ADR-023). Commands that write are named by
their verb; commands that read, by what they show. `-p` selects the project, a release
repository; the artifact is named unless the project has only one.

| Command | Does | API (under `/v1/projects/{p}/` unless absolute) |
| --- | --- | --- |
| `gora status [STAGE]` | what each stage holds and its L24 state | `GET stages[/{s}]` |
| `gora history STAGE` | the manifest commits that changed the stage; for `prod`, its platform states | `GET stages/{s}/states` |
| `gora digest STAGE [ARTIFACT]` | the digest a stage holds, for deploy-on-merge | `GET stages/{s}/artifacts/{a}` |
| `gora register ARTIFACT --digest D` | CI hands over a build: verify, version, rc tag | `POST artifacts/{a}/candidates` |
| `gora promote STAGE [ARTIFACT]` | open or find the promotion PR, forward only (the loop does it by itself) | `POST stages/{s}/promotions` |
| `gora reject ARTIFACT VERSION --reason R` | record a rejection | `POST artifacts/{a}/versions/{v}:reject` |
| `gora rollback STAGE ARTIFACT --to VERSION --reason R` | rollback kind A; rejects the version left | `POST stages/{s}:rollback` |
| `gora rollback prod --to STATE --reject A[,A…] --reason R` | rollback kind B; rejects the named versions | `POST stages/{s}:rollback` |
| `gora branches` | unmerged branches past the policy's age | `GET branches` |
| `gora connect check` | the conditions of section 9 | `GET conditions` |
| `gora connections` | adapter health | `GET connections` |
| `gora version` | revision and version | `GET /v1/version` |

- No `approve`, `merge` or `signoff` command: an approval is a review on the code host (L15).
- A write runs only with GORA's machine identity (L22); elsewhere only `--dry-run`, which
  prints the plan. Every write is idempotent; `--output json` prints the API's body; a refusal
  names the law.
- `/gora` in Slack takes the same words: `/gora -p cally status prod`.
- GORA's promotion and rollback PRs end with a Git trailer, one line per artifact
  (ADR-023 section 6): `Gora-promotion: <stage> <artifact> <version> <digest>`.
- GORA writes no personal data into Git; who asked and who approved stay in the host's records
  and the audit journal (ADR-017).

## 12. Outside the model

- Release branches, cherry-picks and backports.
- Two major lines in production at once (for example `v10` and `v11`, each maintained). Not
  in GORA's core; it would need its own ADR.
- Release trains, freezes, windows and schedules.
- Building and deploying: CI builds, CD deploys (ADR-001, ADR-019).
- Feature flags and application-level rollback: the application's concern (ADR-004).
- A library published to a package registry (Maven, npm, NuGet, PyPI) is not a deployable
  artifact: it reaches production inside one.

## 13. Where GORA is today

The model is decided; the implementation follows ADR-018 and ADR-023. Until then:

- The operator pushes `vX.Y.Z-rc.N` tags by hand, and the tag starts the build (ADR-011). In
  the model the build runs on `main` and GORA creates the tag.
- Manifest schema v0.1 still has the `candidate` block that `cut-rc` writes. In the model
  candidates live in tags, the registry and their verification attestations, not in the
  manifest.
- `release-policy.yaml` does not exist yet; GORA's own stages are `dev` and `staging`.
- The CLI still has `cut-rc`, `promote` and `rollback --to-version --to-digest`; the commands of
  section 11 and the stage loop are decided, not built.
- Until the gates and GORA's required status exist, the maintainer merges GORA's PRs by hand
  after review; GORA itself still has no merge operation (L15).
- This repository holds L3 through required checks in strict mode. A merge queue would add
  throughput only; GitHub offers it to private repositories only on Enterprise Cloud, and GORA
  works in either mode.

# ADR-024: Versions and Promotion — One Version per Candidate, Final in `prod`

**Status:** Accepted (decision); implementation pending
**Date:** 2026-10-08
**Amends:** ADR-003, ADR-004, ADR-009, ADR-011, ADR-013, ADR-018, ADR-019, ADR-022, ADR-023

---

## Context

ADR-022 gives every candidate an rc version of a release line, `X.Y.Z-rc.N`, computed from the
newest final version, and makes it final at a sign-off gate set in policy. Because several
candidates of one line exist at once on a moving trunk, an older one that became final would
leave a newer `X.Y.Z-rc.N+k` ordered before it by SemVer. ADR-022 section 8 therefore lets only
the newest non-rejected candidate pass the sign-off gate (L13).

A review of the model on 2026-10-08 found that rule unworkable:

- **It starves.** A candidate passes the sign-off gate only if no newer candidate of its
  artifact is registered between its registration and the merge of its sign-off. With merges
  every 30 minutes and a QA cycle of three hours, no candidate ever passes; the only ways
  through are to stop merging, a freeze that L20 forbids, or to reject the newer candidates.
  A hotfix is superseded by any unrelated merge that lands while it is tested.
- **It races.** The code host merges the sign-off pull request, not GORA, so a registration
  that reads the state just before that merge computes an rc of a version that becomes final a
  moment later.
- **One build has two names** in a stage chosen by policy.

Research behind this ADR (2026-10-07 and 2026-10-08, sources below): continuous delivery and
GitOps tools keep one identifier per build through every environment (Amazon's pipelines,
Netflix's Managed Delivery, Kargo, Octopus Deploy, Jenkins X, the deployment pipeline of Humble
and Farley); none of them renames a version between environments. Renaming an rc to a final
version is release-branch practice (Kubernetes, Kafka). A person's decision is an approval to
enter an environment, bound to one version (Keel's manual judgement, Kargo's approval of Freight
for a Stage, deployment approvals in GitLab and GitHub), and a person may take an older verified
version while newer ones exist (Keel, Kargo). An automatic environment takes the newest verified
build (Amazon, Kargo's default); GitOps Promoter documents that promoting changes one by one
stalls when commits arrive faster than they clear.

The decisions were taken with the maintainer on 2026-10-08.

## Decision

### 1. Every candidate has its own version

- **A candidate's version is computed from the previous candidate's.** Over the first-parent
  commits after the artifact's previous registered candidate, up to and including this one, that
  changed the artifact's inputs, GORA takes the highest bump: a breaking change (`!` after the
  type or scope, or a `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer) is major, and minor while
  the major version is `0`; `feat` is minor; any other type is patch. A change to inputs is at
  least a patch. A revert is bumped by its own message like any commit.
- **The first candidate** of an artifact is the first commit merged to `main` after the artifact
  is declared in policy (ADR-022 section 4). Its version is the initial version the policy
  declares, as `X.Y.Z-rc`, which orders after every version tag of the artifact that exists: a
  repository that holds `v1.4.0-rc.3` declares at least `1.4.1`.
- **Leaving `0`.** The major version leaves `0` once, by a declaration in the artifact's policy,
  and never goes back: the first candidate registered after that declaration is at least
  `1.0.0-rc`.
- **The version is `X.Y.Z-rc`.** One pre-release identifier, `rc`, says that the version has not
  entered `prod`. There is no counter: a candidate is never rebuilt, so no version is built
  twice. A candidate that fails goes no further, and a person may reject it; the next commit is
  a new candidate with a new version.
- **A version is never reused.** A rejected candidate, or one that never reaches `prod`, keeps
  its number; the next candidate is computed after it. A commit whose build failed, or finished
  after a newer commit was registered, is not a candidate (ADR-022 sections 4 and 6), and its
  changes count in the next candidate's bump.
- **Versions grow with history.** Candidates are registered in the first-parent order of `main`
  (ADR-022 section 6), so their versions grow with it, and SemVer precedence is the order of the
  commits: `1.0.2-rc < 1.0.2 < 1.1.0-rc < 1.1.0 < 1.1.1-rc`.
- **Registration is serialised per artifact.** The release pipeline runs one registration of an
  artifact at a time (on GitHub Actions, a concurrency group per artifact). Creating the rc tag
  is the commit point. A request for a commit older than the artifact's newest registered
  candidate is refused: that commit is not a candidate. A request for a commit that already
  carries the artifact's rc tag returns that registration when the digest is the same, and is
  refused when it differs. A registration that finds the tag of its computed version already
  created fails and changes nothing.
- **The source of the message, the grammar and the tag names stay** as ADR-022 section 5 sets
  them: the squash commit's subject and body, SemVer 2.0 without build metadata (ADR-003), and a
  tag prefix per artifact. A version does not depend on which candidates reached `prod`: it is
  known at registration, and the final version is the same number without `-rc` (section 2).

### 2. The final version is made by entering `prod`

- **Registration** creates the annotated git tag `vX.Y.Z-rc` on the source commit and adds the
  registry tag `X.Y.Z-rc` to the digest (ADR-022 section 6).
- **Every stage before `prod` holds `X.Y.Z-rc`.** The promotion pull request into `prod`, the last
  stage of every project (a condition of connecting), records `X.Y.Z` for the same digest. When
  it is merged, GORA creates the annotated git tag `vX.Y.Z` on the same source commit and adds
  the registry tag `X.Y.Z` to the same digest, and the merge is a platform state
  (`prod/YYYY.MM.DD.N`, ADR-022 section 9).
- **A final version is a version that entered `prod`.** Nothing else creates one: there is no
  sign-off gate set in policy and no other stage where a version changes. A version that enters
  `prod` again after a rollback (section 6) keeps the tags it has.
- **Every run completes the tags.** On every run GORA creates any missing `vX.Y.Z`, registry tag
  `X.Y.Z` and state tag of the versions `prod` holds or has held, so a run that stopped after a
  merge is completed by the next.
- **A rejection holds the digest** (ADR-023 section 10): rejecting `X.Y.Z-rc` or `X.Y.Z` rejects
  both.

### 3. What a stage may take

- **Verified in a stage.** A version is verified in a stage when, in the stage's latest holding
  of it, every verification that the policy at the manifest commit setting the entry lists for
  the artifact and stage has success as its newest status from its reporter. A status counts
  only if it is on that commit, or on a later commit of `main` where the entry is unchanged, and
  was created before the entry changed again. Every stage lists at least one verification for
  every artifact. A version stays verified in a stage after the stage has moved on.
- **Finished in a stage.** The version a stage holds has finished its verifications when the
  newest status of one of them is red, the newest status of every one is green, or it is
  rejected. A stage that holds nothing for an artifact counts as finished, and as holding a
  version older than any registered candidate. Every entry set before the artifact's first
  registered candidate counts, wherever versions are compared, as older than any registered
  candidate, and a change between two such entries never makes the artifact held (section 6).
- **Offered.** A version is offered to a stage when all of these hold:
  - it is not rejected;
  - it is newer, in the order of registration, than the version the stage holds;
  - the stage has held it before, or it is newer than every version the stage has held, so a
    version the stage passed over never enters it later, a rollback included;
  - it is verified in the stage before; for the first stage, it is a registered candidate.
- **Eligible.** An offered version may enter the stage when, in addition, its evidence passes
  the check at promotion (ADR-018 section 4). A check that cannot complete, because a registry
  or a log does not answer, is not a failure: the run opens and closes nothing for the pair and
  reports the error. Versions of different artifacts are independent: GORA has no dependencies
  between artifacts (ADR-025).
- **GORA's status follows eligibility.** GORA's required status on an open promotion is green
  only while its version is eligible and the gate is met, and on an open rollback only while its
  target is valid (ADR-023 section 10); GORA evaluates it again on every run, with or without a
  merge queue.
- **Only forward.** A stage never takes a version older than the one it holds. Moving a stage to
  an older version is a rollback, which a person asks for (ADR-023 section 10).

### 4. The stage loop

- **One version at a time.** The stage loop opens a promotion into a stage only when the version
  the stage holds has finished its verifications. A red version does not block the stage: it is
  not verified there, so it goes no further, and an eligible version may enter. Registration
  does not open a pull request by itself; it starts a run of the loop, which opens the promotion
  into the first stage when that stage allows it.
- **An open promotion stays.** The loop never replaces an open pull request: it opens the next
  promotion of a pair once the open one is merged or closed. GORA closes an open promotion when
  its version is no longer eligible, and the loop proposes again.
- **An `approval` stage is offered every version the stage before verified, in order.** Into a
  stage whose gate is `approval`, the loop proposes the oldest offered version, so the people who
  approve are offered each version in turn: `1.0.2-rc` is offered to `qa` while `dev` already
  holds `1.1.0-rc`, and `prod` receives `1.0.2` and then `1.1.0`. While that version is not
  eligible, the stage is blocked with the reason (section 10); the loop does not pass it by, and
  a person frees the stage by rejecting that version or asking for a newer one.
- **An `auto` stage takes the newest.** Into a stage whose gate is `auto`, the loop proposes the
  newest eligible version. Nobody is offered anything there, and a version it passes over is
  contained in the newer one; taking the oldest would let an automatic stage fall further behind
  `main` with every merge that comes faster than its verifications.
- **A person may ask for any eligible version.** In any stage, a person whom policy allows to ask
  for a promotion into it may ask for any eligible version:
  `gora promote STAGE [ARTIFACT] --version VERSION`. The request replaces the open promotion of
  that artifact and stage (L14), and the versions it passes over never enter that stage
  (section 3). A request is not held to the order or to the wait for the current version's
  verifications, so it also moves a stage whose verification never reports; it is held to
  everything else. In an `approval` stage, only a person skips.
- **Closing a pull request is not a skip.** The loop does not open the same promotion again after
  a person closes it. In an `approval` stage the version waits until a person asks for it or for
  a newer one, or rejects it; in an `auto` stage the loop moves on when a newer version is
  eligible. A pull request GORA closed is not one a person closed.
- **No eligible version waits for a newer one.** A version moves when it is eligible, its turn in
  the stage has come and its gate is met, whatever has been registered since (L13, L20).

### 5. Gates are `auto` and `approval`

- Every stage has a gate, `auto` or `approval` (ADR-023 section 8). There is no other kind of
  gate and no stage with a special role, other than `prod` being the last.
- An approval is a review, on the promotion pull request into a stage, by members of the groups
  the policy names for that stage. A QA team, for example, approves the promotion into `qa` when
  it takes a version into testing, and the promotion into `staging` when it has tested it.
- How many stages a project has, which are `approval` and who approves is policy. The mechanism
  is the same for a pipeline with no person in it (every gate `auto`) and for one with a QA
  stage and release owners.

### 6. A rollback holds the stage until a person asks

- A rollback sets an artifact's entry in a stage to an older version (ADR-022 section 10). After
  it, the stage loop opens no promotion into that stage for that artifact until a person asks
  for one with `gora promote`. When that promotion is merged, the loop resumes as usual.
- **Held, from Git alone:** an artifact is held in a stage while the newest change to its entry
  there set an older version than the one before it.
- A kind A rollback holds its artifact. A kind B rollback holds every artifact it moves: the
  versions of the artifacts the request names are rejected and never come back; any other may
  be asked for again by a person, as soon as the incident allows. Even in a pipeline whose gates
  are all `auto`, a person acts again after a rollback.
- **What a kind B rollback moves.** Every artifact whose version in the named state differs from
  the one the stage holds returns to its version in that state, a migration like any other
  (ADR-025 section 3). The rollback is refused if that would move an artifact to a newer version
  or to a rejected digest (L18). An artifact the named state does not contain keeps its version
  and is not held.

### 7. A verification is a commit status

- A verification of a stage is a commit status `<stage>/<artifact>/<name>` from the reporter the
  policy names (ADR-023 section 8), and "verified" is computed from commit statuses alone, so any
  run computes it again from the code host.
- Argo CD reports `deploy` like any reporter: its notifications controller posts the commit
  status on the revision it synced, from a dedicated identity. GORA's read-only Argo CD adapter
  shows an application's health in `gora status`; it does not stand in for a verification.

### 8. The titles of GORA's pull requests

GORA's pull requests have Conventional Commit titles, so a required title check passes them
like any other:

```text
promote(<stage>): <artifact> <version>
rollback(<stage>): <artifact> <version>
rollback(prod): <platform state>
reject(<artifact>): <version>
```

GORA's commits change only release files, which are never inputs (L8), so their type never takes
part in a version. A project's title check accepts any type (a condition of connecting).

### 9. GORA's own stages

- GORA's own policy is `dev` (`auto`) → `staging` (`approval`) → `prod` (`approval`). The
  maintainer approves `staging` and `prod`.
- GORA's release pipeline runs the version of GORA that `prod` holds, so GORA's own promotions
  are made by the version in `prod`. Until `prod` holds a version of GORA, the pipeline runs the
  digest its workflow names.
- If the version in `prod` cannot act, the maintainer names in the workflow, through a reviewed
  pull request, a digest that `prod` held before and that is not rejected, and asks for the
  rollback to it (L18), which that digest carries out. A further reviewed pull request removes
  the name once the rollback is merged. The workflow is code, not a release file (L16).
- The tags `v0.2.0-rc.1` to `v0.2.0-rc.6`, pushed by the operator before registration exists, are
  not candidates of this model: five of them name commits that are not on `main`, and GORA
  registered none. None of them enters `prod`, and `v0.2.0` is never created. GORA's policy declares
  the initial version `0.2.1` (section 1), which orders after all of them; the stage entries set
  before GORA's first registered candidate are older than every registered candidate (section 3).

### 10. The laws and the states of L24

Six laws are reworded and one is replaced, through this ADR:

- **L10:** "Only GORA creates version tags: `vX.Y.Z-rc` on the source commit when it registers a
  candidate, in the first-parent order of `main`, never on an older commit after a newer one;
  and `vX.Y.Z` on the same source commit when that version first enters `prod`. A tag never
  moves."
- **L11:** "GORA computes each candidate's version from the previous registered candidate's and
  the Conventional Commits since it; a change to inputs is at least a patch; a version is never
  reused."
- **L12:** "Promotion moves a digest that is not rejected and that the stage before held and
  verified (into the first stage, a registered candidate). Nothing is rebuilt; the final version
  is the rc version without `-rc`, on the same digest."
- **L13** is replaced: "A version becomes final only by entering `prod` through its gate. No
  eligible version waits for a newer one."
- **L14:** "At most one open GORA pull request per artifact and stage; a kind B rollback counts
  for every artifact it moves. The loop opens none while one is open; a person's request replaces
  an open promotion; while a rollback pull request is open, any other promotion or rollback of
  its artifacts into its stage is refused."
- **L20:** "No release train: no schedules, windows, freezes or batches. A version moves when it
  is eligible, its turn in the stage has come and its gate is met."
- **L24:** "No artifact stalls unseen: every artifact in every stage is in exactly one state, the
  first that applies. GORA opens each next promotion itself: into an `approval` stage the oldest
  offered version once it is eligible, into an `auto` stage the newest eligible version; a person
  may ask for any eligible version. After a rollback GORA opens nothing for the artifacts it
  moved until a person asks."

The states of L24, in this order, replace those of ADR-023 section 9; they are exhaustive by
construction:

1. **at the gate:** a GORA promotion or rollback pull request for the pair is open;
2. **held:** the newest change to the pair's entry set an older version than the one before it;
3. **verifying:** the version the stage holds has not finished its verifications;
4. **blocked, with the reason:** a version is offered, and the one the stage's order proposes
   (section 4) cannot get a pull request: a person closed its pull request, a rejection of it is
   open, or its evidence fails the check at promotion; in an `auto` stage, also when no offered
   version is eligible;
5. **ready:** the version the stage's order proposes is eligible, and GORA has not opened its
   pull request yet;
6. **waiting:** no version is offered, and the stage before holds a newer version that has not
   finished its verifications there;
7. **in sync:** no version is offered. A newer version the stage before holds is then one that
   failed or was rejected there, or one this stage passed over; `gora status` says which.

For the first stage, the newest registered candidate stands for what the stage before holds.

`gora status` also shows, in every state, whether the version the stage holds is verified there,
failed or rejected, and how many offered versions wait.

## Why not otherwise

- **An rc counter per release line with only the newest passing the sign-off gate** (ADR-022 as
  first written). See Context: it starves on a busy trunk, races the host's merge, and gives one
  build two names in a stage chosen by policy.
- **An rc counter per release line, any candidate passing.** When an older candidate became
  `1.0.1`, a newer `1.0.1-rc.3` would contain more and order before it, and could never become
  final itself without a second name.
- **No pre-release part at all**, every candidate `X.Y.Z`. It loses the one thing the suffix says:
  that a version has not been in `prod`. Whoever pulls `1.0.2-rc` by tag knows it.
- **A sign-off gate set in policy.** A second place where a version changes, chosen per project,
  is a second rule to reason about for no gain: the version that reaches `prod` is the one that
  needs the final name.
- **The newest eligible version into an `approval` stage.** It skips versions silently: what QA
  was asked to test would never be offered to them. For the same reason the loop does not pass
  by an offered version that is not eligible yet; the stage is blocked and shows why.
- **The oldest eligible version into an `auto` stage.** With merges that come faster than the
  stage's verifications, the stage falls behind `main` without limit, and nobody is there to
  skip.
- **A loop that promotes whatever the stage before has verified** (rejected in ADR-023). It would
  undo a rollback at once and could move a stage back. The loop here moves only forward, never
  re-enters a version it passed over, and holds a stage after a rollback.
- **A stage that takes only versions newer than every one it ever held.** After a kind B
  rollback, the artifacts that had nothing to do with the incident could not return until
  someone merged new code.
- **A loop that replaces its open promotion with a newer version.** When versions arrive faster
  than a pull request clears its checks, every pull request is replaced before it merges, and
  the stage never moves.
- **Argo CD's health read by GORA as the `deploy` verification** (ADR-023 section 8 as first
  written). The health of an application is its state now; once the stage moves on, nothing in
  Git or on the host says an older version was healthy, so "verified" could not be computed again.

## Consequences

Amendments, each recorded in the amended ADR:

- **ADR-003:** the release line and "one active RC per release line" belong to manifest v0.1;
  every candidate has its own version.
- **ADR-004:** the promotion graph is an ordered list ending in `prod`; the final promotion is
  the promotion into `prod`; supersession is replaced by eligible versions in the order of
  section 4; a rollback holds the stage.
- **ADR-009:** the exit of Milestone 3 is restated in this model, with the stages of section 9.
- **ADR-011:** GORA's own stages are those of section 9; the final tag is created when GORA's
  version enters `prod`; the tags the operator pushed are not candidates.
- **ADR-013:** the titles of section 8; the two names of one digest; GORA's status fails a
  promotion whose version is no longer eligible, not a superseded one; the loop never replaces an
  open pull request.
- **ADR-018:** sections 1 and 4 (the tags; a promotion takes an eligible version) and section 7
  (the final retag happens when a version enters `prod`).
- **ADR-019:** section 2 (no release line to edit by hand) and section 6 (deployment results
  reach GORA as commit statuses).
- **ADR-022:** section 3 (one digest is `X.Y.Z-rc` and then `X.Y.Z`), section 4 (the tags the
  operator pushed are not candidates), section 5 (the version is computed from the previous
  candidate; no line and no counter), section 6 (registration serialised; no final-tagging race),
  section 7 (eligible versions, in order), section 8 (the sign-off gate and supersession are
  replaced), section 9 (no line), section 10 (the hold; what kind B moves), section 11 (SemVer
  versions, not a line), section 12 (L20), section 15 (no sign-off gate in policy), section 16 (the
  vocabulary), the "Why not otherwise" entry on rc versions, and the Consequences.
- **ADR-023:** sections 2 and 4 (`gora promote … --version`; registration opens no pull request by
  itself), section 3 (examples), section 6 (titles; the loop never replaces an open pull request),
  section 8 (the loop of section 4; verifications are commit statuses), section 9 (the states of
  section 10), section 10 (the hold; a rejected version never moves; what kind B moves), section 12
  (no supersession in the queue), the "Why not otherwise" entry on the loop.

Corrections made with this ADR, each following the ADR that decided it and recorded in the
corrected document: ADR-001 (the promotion graph is an ordered list, ADR-022; Kargo is not a
backend, ADR-012), ADR-003 (`.lock` and `promotedBy`, ADR-023), ADR-005 (rollback is not a
`git revert`; an approval waits as a pull request; the CLI is ADR-023's), ADR-006 (no merge, no
cluster write, no pipeline trigger: ADR-019, ADR-022, ADR-023; the Argo CD adapter is never a
verification, section 7), ADR-007 (the durable engine of phase 3), ADR-008 (the layout as it is),
ADR-009 (the gates after the MVP; no Kargo integration), ADR-011 (the manifest changes only
through a pull request, ADR-019, ADR-023; notes of its amendments of 2026-10-02 and 2026-10-03
that were missing), ADR-012 (GORA is the promotion; Argo CD only applies it, Kargo has no role),
ADR-013 (Project Intelligence is ADR-020, ADR-014), ADR-015 (the API path and the notification
events, ADR-023), ADR-016 (`gate-change` is required), ADR-017 (its number in its title), ADR-018
section 7 (a write runs only as GORA's machine identity, ADR-023), ADR-019 section 3 ("project" as
ADR-023 defines it), ADR-021 (registration and the build on `main`, ADR-022, ADR-023), ADR-022
(the repository settings, the `prod/*` ruleset and the PR-title check are in place), the workflow
catalog (no workflow that writes to infrastructure), the Slack diagram, the reference pages, the
example manifest's header, README, TESTING.md and CLAUDE.md.

In `docs/architecture/release-model.md`: the vocabulary (rc and final version, verified,
eligible, held; no sign-off gate, no supersede), the lifecycle, the sections on branches,
stages, versions, rollback and the connect conditions, L10 to L14, L20 and L24, and the
commands.

Implementation, in later pull requests: the version computation, the registration and the final
tag with `gora register` and the promotion into `prod` (ADR-018); the stage loop with its order,
its hold and the states of L24 (ADR-023); GORA's pull request titles,
which today's `promote --open-pr` does not yet follow; the declaration that leaves `0`.

## Sources

- Amazon Builders' Library, "My CI/CD pipeline is my release captain":
  https://d1.awsstatic.com/builderslibrary/pdfs/ABL-CICD_pipeline_as_release_captain.pdf
- Spinnaker Managed Delivery, environment constraints:
  https://spinnaker.io/docs/guides/user/managed-delivery/environment-constraints/
- Spinnaker Managed Delivery, marking a version as bad:
  https://spinnaker.io/docs/guides/user/managed-delivery/marking-as-bad/
- Kargo, working with Freight (manual approval):
  https://docs.kargo.io/user-guide/how-to-guides/working-with-freight
- Kargo, working with Stages (auto-promotion, holds):
  https://docs.kargo.io/user-guide/how-to-guides/working-with-stages
- Octopus Deploy, releases and deployments:
  https://octopus.com/docs/best-practices/deployments/releases-and-deployments.md
- GitLab, deployment approvals: https://docs.gitlab.com/ee/ci/environments/deployment_approvals.html
- GitOps Promoter, FAQs (promotion when commits arrive faster than they clear):
  https://github.com/argoproj-labs/gitops-promoter/blob/main/docs/faqs.md
- Argo CD Notifications, GitHub commit statuses:
  https://argo-cd.readthedocs.io/en/stable/operator-manual/notifications/services/github/
- Jenkins X, introduction: https://jenkins.io/blog/2018/03/19/introducing-jenkins-x/index.html
- Dave Farley, "The Deployment Pipeline":
  https://continuousdelivery.com/wp-content/uploads/2010/01/The-Deployment-Pipeline-by-Dave-Farley-2007.pdf
- Trunk-based development, release from trunk: https://trunkbaseddevelopment.com/release-from-trunk/
- Kubernetes release manager handbook (rc and official releases from a release branch):
  https://github.com/kubernetes/sig-release/blob/master/release-engineering/handbooks/release-manager.md
- SemVer 2.0.0: https://semver.org/

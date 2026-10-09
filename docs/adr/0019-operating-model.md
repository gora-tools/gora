# ADR-019: Operating Model — GORA Owns the Release Repository

**Status:** Accepted
**Date:** 2026-10-03
**Amended:** 2026-10-03 — candidates are not release-file writes; the refs GORA creates (ADR-022)
**Amended:** 2026-10-04 — one system per release repository; the stage loop (ADR-023)
**Amended:** 2026-10-08 — section 3 reads "project" as the model does (ADR-023)
**Amended:** 2026-10-08 — no release line; verifications are commit statuses (ADR-024)
**Amended:** 2026-10-08 — no dependencies between artifacts (ADR-025)
**Amended:** 2026-10-08 — section 4 is decided, not yet how GORA runs (correction)
**Amended:** 2026-10-09 — the release files; who writes them (ADR-026)
**Amended:** 2026-10-09 — verification outcomes outlive commit statuses (ADR-027)

---

## Context

The ADR set says what a release is (ADR-003, ADR-004), how it is trusted (ADR-018) and how a
promotion becomes a pull request (ADR-013). It does not say where GORA runs, which repository
holds a project's release state, how a project connects to GORA, or what the server of
Milestone 5 (ADR-014) is in relation to the CLI. ADR-012 answered part of it with
"cluster-optional", written to keep GORA off Kargo's ground; read alone, it suggests GORA is
meant only for teams without Kubernetes. GORA serves two kinds of delivery with one core: a
pipeline that deploys from Git when a change is merged (GitHub Actions, no cluster), and
Kubernetes with Argo CD reconciling from Git.

Three shapes were compared (research 2026-10-02, sources below):

- **A CLI in CI only.** GORA runs when a pipeline calls it, as `cosign`, `argocd` or
  `terraform` do. Everything a release needs works this way, but nothing can wait for an
  event: a soak time, an Argo CD health result, an approval in Slack. There is no UI and no
  SSO.
- **A Kubernetes controller with CRDs**, as Kargo and GitOps Promoter are. Its state lives in
  the cluster (CRD status), next to Git. That is a second source of truth (ADR-002), and it is
  Kargo's ground (ADR-012). Kargo's control plane requires Kubernetes; GitOps Promoter calls
  itself experimental. Argo CD is moving toward promotion through Git branches as well (its
  Source Hydrator, beta in Argo CD 3.5, writes rendered manifests to branches).
- **A service with Git as its state.** API, worker, UI and webhooks over the same engine, the
  way Argo CD keeps desired state in Git while running in a cluster.

## Decision

### 1. The release repository is a role, not a separate repository

The **release repository** (the "BOM repository"; ADR-002 calls it the metadata repository)
is the Git repository where GORA keeps a project's release state: `release-manifest.yaml`
and `release-policy.yaml` (ADR-018). Files GORA may later render for a CD system (per-environment
image references) belong there too; how they are rendered is a later decision. The release
repository has two shapes, with the same commands and the same contract:

- **The project's own repository.** GORA releases itself this way: its manifest is at the root
  of its own repository, and GORA's commands change it there. No separate repository, and no
  special case: GORA uses the mechanism it offers to others, nothing else.
- **A shared repository for several projects**, when a team wants one place for its releases.

*Amended 2026-10-04 (ADR-023 section 3):* "several projects" means several code repositories of
one system. A release repository is one GORA project: one manifest, one policy, one system whose
artifacts are promoted through the same stages, depend on each other and roll back together. The
model is the same for one artifact or a hundred, built in any number of code repositories, each
artifact with its own directory in the release repository. A different system is a different
project, with its own release repository; one system is never split across two.

*Amended 2026-10-08 (ADR-025 section 1):* GORA has no dependencies between artifacts: "depend on
each other" above is replaced.

*Amended 2026-10-09 (ADR-026 sections 1 and 2):* the release files are `release-policy.yaml`,
`artifacts/<artifact>/artifact.yaml`, `state/<stage>/<artifact>.yaml`,
`rejections/<artifact>/<X.Y.Z>.yaml` and `.github/CODEOWNERS`. The manifest is the `state/`
tree; `release-manifest.yaml` is retired. What a CD system renders from the state is its own
output, never a release file.

### 2. GORA owns every write to release state

Only GORA writes release files: their content is what GORA's commands produce. A change
reaches the default branch only as a pull request that a human merges: GORA never merges
(ADR-013). Nothing pushes to the default branch, neither GORA nor a pipeline nor a person: the
release repository is trunk-based, and every change to it is a pull request. Besides the
`gora/*` branches its pull requests come from, the only refs GORA creates are release tags, on
a commit already merged (ADR-018 section 7). A project never writes its release state, and a
person does not edit it by hand except where an ADR names the case (ADR-003: a new release
line, until the release-line command named in ADR-009 exists).

How this holds today, and what replaces it:

- The repository ruleset requires a pull request for every change; it requires no approving
  review, so the review is the person who merges (in a repository with one maintainer, that is
  the author).
- Until GORA opens a pull request for every write, an operator runs GORA's command and opens
  the pull request from an ordinary branch (ADR-011, Milestone 3). The content is still
  GORA's; the branch is not.
- GORA opens its own pull requests for promotions, the final one included (ADR-013, ADR-018
  section 7). How a new candidate (`cut-rc`) reaches the default branch through a GORA pull
  request is not decided yet. Once every write goes through a GORA pull request, a check
  refuses a release-file change from any branch outside `gora/*`. That check is not built yet.

*Amended 2026-10-03 (ADR-022):* a candidate does not reach the default branch: it is registered
as tags and an attestation, not written to a release file, so the open question above is
answered. Besides its `gora/*` branches, GORA creates version tags, rc and final, on the source
commit (ADR-022 section 6), and state tags `prod/YYYY.MM.DD.N` on manifest commits (section 9).
The edit by hand for a new release line ends once versions are computed (ADR-022 section 5).
A promotion to a stage whose gate is `auto` is merged by the platform's auto-merge, which GORA
enables (ADR-022 section 7); GORA still never merges by its own decision, and every other
pull request is merged by a person.

*Amended 2026-10-04 (ADR-023 sections 8 and 10):* nobody merges a GORA pull request by hand. GORA
enables the code host's auto-merge on every pull request it opens, and the host merges it once
its gate is met: `auto`, the other required checks; `approval`, the other required checks and
the reviews the policy names; a rollback or a rejection, the other required checks, since the
request of a person whom policy allows is the decision. "A pull request that a human merges"
above reads accordingly. Pull requests that are not GORA's are merged as the repository's own
rules say.

*Amended 2026-10-08 (ADR-024 section 1):* the model has no release line and no release-line
command: "a new release line, until the release-line command named in ADR-009 exists" above and
"the edit by hand for a new release line" apply to manifest v0.1 only.

*Amended 2026-10-09 (ADR-026 sections 1 and 13):* "Only GORA writes release files" reads as the
release state: only GORA writes `state/` and `rejections/` (L16). The policy and the declarations
of artifacts are decisions of people, written in pull requests and decided by their owners
(L25); GORA validates every change to them on its required status and never writes them.

### 3. A project connects to GORA, on GORA's terms

A project is connected when the release repository names it, its stages and the identity
allowed to build it (`release-policy.yaml`, ADR-018 section 3). The project's CI does one
thing: it hands over a digest. GORA verifies the digest's evidence against the policy and
records it, or refuses (ADR-018 section 4). GORA sets the contract; the project meets it.

*Amended 2026-10-08 (ADR-023 section 3):* in the vocabulary of the model a project is one release
repository, and it connects with the code repositories its artifacts are built in: the release
repository names each artifact, its stages and the identity allowed to build it, and the
conditions of connecting apply to the release repository and to each code repository it names
(release-model section 9).

*Amended 2026-10-09 (ADR-026 sections 4, 5 and 13):* the policy names the stages and the trusted
builders; each artifact is declared in its own `artifacts/<artifact>/artifact.yaml`, owned by its
team through `.github/CODEOWNERS`, and names its code repository, its inputs and the builders
that may sign it.

### 4. The core is the deterministic engine and its CLI, and it is complete

The engine, the contract, the adapters (ADR-006) and the CLI are the core. Every release
operation works with the core alone: GORA runs in the release repository's pipeline (GitHub
Actions now; a Tekton task is the same call). The credentials that change release state live
there only; a project's CI holds at most the right to hand a digest over (for example, to send
a dispatch event to the release repository). GORA releases itself this way (ADR-009,
Milestone 3).

*Amended 2026-10-08 (correction):* this is the decided operating model and the exit of Milestone
3; today the operator runs the CLI on a workstation with a person's token, and `release.yml`
does not run GORA (release-model section 13).

### 5. The server is the next layer over the same services, deployed Kubernetes-first

The Milestone 5 server (ADR-014: HTTP API, Slack, intent routing, a read-only UI) calls the
same services as the CLI; no release logic lives in a transport. It adds what a pipeline run
cannot do: receive webhooks, wait for events and timers, take approvals, show the release
repository in a UI, sign users in.

- Its supported installation is an OCI image and a Helm chart on Kubernetes.
- Git remains the only release state. The server may keep in-flight workflow state (a pending
  approval, a timer; ADR-007), never release state.
- No CRD is release state. GORA is not a Kubernetes controller.

*Amended 2026-10-04 (ADR-023 sections 8 and 11):* an approval is a review on the code host,
which the server reads; the server records none of its own and keeps no pending approval as
in-flight state: the open pull request is the wait. The server runs the stage loop on events and
as a periodic reconciliation, as the release repository's pipeline does without it. It acts on a
code host only as GORA's machine identity there.

### 6. GORA writes desired state; CD applies it

GORA writes to Git and never deploys. What applies the state is the CD system, in one of two
forms:

- **A pipeline that deploys on merge.** Without a cluster, a job triggered by the merge of a
  promotion pull request reads the stage's digest from the manifest and deploys exactly that
  digest to a VM, a container service or a serverless runtime. Any CI that runs on a merge can
  do it: GitHub Actions, Bitbucket Pipelines, Jenkins. GORA does not care which.
- **A GitOps reconciler.** With Kubernetes, Argo CD reconciles the environments from Git and
  Argo Rollouts runs progressive delivery. Argo CD is the first reconciler GORA targets.

GORA reads deployment results through read-only adapters (ADR-009: Argo CD and Kubernetes in
Milestone 6, Argo Rollouts after the MVP) and uses them as evidence. GORA never writes to a
cluster.

*Amended 2026-10-08 (ADR-024 section 7):* the evidence a promotion counts is commit statuses
only. Argo CD reports `deploy` through its notifications controller, as a commit status from a
dedicated identity; GORA's read-only adapters show health and history, and are never a
verification, since the health of an application is its state now and cannot be read again for
an older version.

*Amended 2026-10-09 (ADR-027 section 2):* the code host deletes commit statuses by age, so GORA
attests the outcome of a stage's verifications on the digest once the version has finished
them, and counts each verification's newest result among the statuses and those attested
records.

### 7. Events at the boundary

CDEvents (CD Foundation, on CloudEvents) is the candidate format for events GORA receives
(an artifact published or signed) and sends (a service deployed, upgraded or rolled back). It
is evaluated when the server is built; the specification is not at 1.0 yet. It is a boundary
format, never a dependency of the core.

## Why not otherwise

- **A separate release repository for GORA itself.** It proves nothing the single-repository
  shape does not, and a special case for GORA would mean GORA is not released by the mechanism
  it offers.
- **CLI only, permanently.** No events, no approvals in Slack, no UI: the release engine would
  stay a tool that pipelines call, never a system projects connect to.

  *Amended 2026-10-08 (correction, ADR-023 sections 5 and 8):* "no approvals in Slack" is no longer
  a reason: approvals are reviews on the code host with or without a server, which still adds
  events, Slack commands and a UI.
- **A CRD controller.** A second source of truth beside Git, Kubernetes required to run
  anything, and Kargo's model rather than GORA's.
- **Hosted service first.** Release state must stay in the user's Git; a server is something a
  user can run in their own cluster before it is anything else.

## Consequences

- ADR-012 is amended: the core needs no cluster; the server is Kubernetes-first.
- ADR-007, ADR-015 and ADR-018 no longer use the phrase "cluster-optional"; they point to this
  ADR (the core needs no cluster, section 4). Outside this ADR, the phrase remains only in
  ADR-012, with an amendment that gives this reading.
- Release state has one writer. A change to it that GORA did not make is a defect to refuse,
  not a state to adopt; the check that enforces this mechanically comes once GORA opens a pull
  request for every write (section 2).
- A pipeline that deploys on merge needs to read a stage's digest from the manifest in a
  machine-readable form; that read interface is part of the core.
- The first external project (cally-platform, ADR-009 Milestone 7) chooses the
  single-repository or the shared shape when it connects; nothing in the core depends on the
  choice.
- Licensing and product packaging are not recorded here (ADR-012: market framing lives
  outside the repository). This ADR records the technical boundary they rely on: the core is
  complete without the server.

## Sources

- Kargo architecture (control plane on Kubernetes, CRDs, sharded controllers):
  https://docs.kargo.io/operator-guide/architecture
- GitOps Promoter (Kubernetes controller, "experimental"):
  https://github.com/argoproj-labs/gitops-promoter
- Promotion tools compared (GitOps Promoter, Kargo, Telefonistka, Codefresh):
  https://gitops-promoter.readthedocs.io/en/latest/tool-comparison/
- Argo CD Source Hydrator (beta in 3.5, hydrated manifests pushed to branches):
  https://argo-cd.readthedocs.io/en/latest/user-guide/source-hydrator/
- CDEvents specification: https://cdevents.dev/

## Amendments

### 2026-10-03 — candidates are not release-file writes; the refs GORA creates

ADR-022 decides the lifecycle on trunk. Section 2 is amended in place: a candidate is registered
as tags and an attestation and never written to a release file; GORA creates version tags on the
source commit and state tags on manifest commits; versions are computed, so no release line is
edited by hand once that is implemented; an `auto` gate is merged by the platform's auto-merge.

### 2026-10-04 — one system per release repository; the stage loop

ADR-023 decides the command model and the stage loop. Sections 1, 2 and 5 are amended in place:

- a release repository is one project, one system; the shared shape serves one system built in
  several code repositories (section 1);
- nobody merges a GORA pull request by hand: the host's auto-merge merges it once its gate is
  met, and a rollback or a rejection is merged on the request of a person whom policy allows
  (section 2);
- approvals are reviews on the code host, not server state; the server runs the stage loop on
  events and as a periodic reconciliation, and acts only as GORA's machine identity on the host
  (section 5).

"No approvals in Slack" under "Why not otherwise" is no longer a reason for the server: approvals
are reviews on the code host with or without it; the server still adds events, Slack commands and
a UI.

### 2026-10-08 — section 3 reads "project" as the model does

Section 3 is amended in place: a project is one release repository (ADR-023 section 3), which
connects with the code repositories its artifacts are built in.

### 2026-10-08 — no release line; deployment results reach GORA as commit statuses

ADR-024 decides the versions and that a verification is a commit status. Sections 2 and 6 are
amended in place: there is no release line to edit by hand (section 2); Argo CD reports `deploy`
through its notifications controller, and GORA's read-only adapters are never a verification
(section 6).

### 2026-10-08 — no dependencies between artifacts

ADR-025 decides that GORA has no dependencies between artifacts. Section 1 is amended in place:
a project's artifacts are promoted through the same stages and roll back together, and do not
depend on each other in GORA.

### 2026-10-08 — section 4 is decided, not yet how GORA runs

A correction: section 4 describes GORA in the release repository's pipeline as the way GORA
releases itself; it is the decision and the Milestone 3 exit, not the state of today.

### 2026-10-09 — the release files; who writes them

ADR-026 decides the release files. For this ADR: section 1 names them, and the manifest is the
`state/` tree; section 2 reads as people writing the policy and the declarations, decided by their
owners (L25), and GORA alone writing the state and the rejections (L16); in section 3 a project
declares its artifacts in `artifacts/`.

### 2026-10-09 — verification outcomes outlive commit statuses

ADR-027 records the outcome of a stage's verifications as GORA's signed attestation on the
digest. For this ADR: section 6, the evidence a promotion counts is the statuses and that
attested record of them.

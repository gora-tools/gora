# ADR-019: Operating Model — GORA Owns the Release Repository

**Status:** Accepted
**Date:** 2026-10-03

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
  of its own repository, and its own pipeline changes it. No separate repository, and no
  special case: GORA uses the mechanism it offers to others, nothing else.
- **A shared repository for several projects**, when a team wants one place for its releases.

### 2. GORA owns every write to release state

Only GORA writes release files. A change reaches the default branch only as a pull request
that a human merges: GORA never merges (ADR-013). Nothing pushes to the default branch, neither
GORA nor a pipeline nor a person: the release repository is trunk-based, and every change to it
is a reviewed pull request. Besides the `gora/*` branches its pull requests come from, the only
refs GORA creates are release tags, on a commit already merged (ADR-018 section 7). A project
never writes its release state, and a person does not edit it by hand except where an ADR names
the case (ADR-009: a new release line, until the release-line command exists).

Today this is held by branch protection (every change is a reviewed pull request) and by review:
GORA's pull requests come from its own `gora/*` branches (ADR-013). A check that refuses a
release-file change from any other branch is not built yet.

### 3. A project connects to GORA, on GORA's terms

A project is connected when the release repository names it, its stages and the identity
allowed to build it (`release-policy.yaml`, ADR-018 section 3). The project's CI does one
thing: it hands over a digest. GORA verifies the digest's evidence against the policy and
records it, or refuses (ADR-018 section 4). GORA sets the contract; the project meets it.

### 4. The core is the deterministic engine and its CLI, and it is complete

The engine, the contract, the adapters (ADR-006) and the CLI are the core. Every release
operation works with the core alone: GORA runs in the release repository's pipeline (GitHub
Actions now; a Tekton task is the same call). The credentials that change release state live
there only; a project's CI holds at most the right to hand a digest over (for example, to send
a dispatch event to the release repository). GORA releases itself this way (ADR-009,
Milestone 3).

### 5. The server is the next layer over the same services, deployed Kubernetes-first

The Milestone 5 server (ADR-014: HTTP API, Slack, intent routing, a read-only UI) calls the
same services as the CLI; no release logic lives in a transport. It adds what a pipeline run
cannot do: receive webhooks, wait for events and timers, take approvals, show the release
repository in a UI, sign users in.

- Its supported installation is an OCI image and a Helm chart on Kubernetes.
- Git remains the only release state. The server may keep in-flight workflow state (a pending
  approval, a timer; ADR-007), never release state.
- No CRD is release state. GORA is not a Kubernetes controller.

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
- **A CRD controller.** A second source of truth beside Git, Kubernetes required to run
  anything, and Kargo's model rather than GORA's.
- **Hosted service first.** Release state must stay in the user's Git; a server is something a
  user can run in their own cluster before it is anything else.

## Consequences

- ADR-012 is amended: the core needs no cluster; the server is Kubernetes-first.
- ADR-007, ADR-015 and ADR-018 no longer use the phrase "cluster-optional"; they point to this
  ADR (the core needs no cluster, section 4). The phrase remains only in ADR-012, with an
  amendment that gives this reading.
- Release state has one writer. A change to it that GORA did not make is a defect to refuse,
  not a state to adopt; the check that enforces this mechanically is future work (section 2).
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

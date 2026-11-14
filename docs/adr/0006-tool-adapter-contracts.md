# ADR-006: Tool Adapter Contracts

**Status:** Accepted
**Date:** 2026-04-11
**Amended:** 2026-10-08 — no merge, no cluster write, no pipeline trigger (ADR-019, 022, 023, 024)
**Amended:** 2026-10-09 — the registry adapter attaches GORA's verification summaries (ADR-027)
**Amended:** 2026-10-20 — the Git adapter: content from a local clone, state from the host
**Amended:** 2026-10-20 — the Git adapter runs the git `GORA_GIT` names (ADR-011)
**Amended:** 2026-10-22 — the Git adapter's list of settings is kept by hand (correction)
**Amended:** 2026-11-07 — the release image has git from GORA's base image (ADR-011)
**Amended:** 2026-11-14 — the GitHub adapter is a reader and a writer, on one bounded HTTP core (L22)

---

## Context

GORA integrates with external systems: GitHub, ArgoCD, Kubernetes, CI/CD pipelines, cloud providers. Without explicit boundaries, these integrations become uncontrolled, unauditable, and dangerous.

## Decision

Tool adapters are the only approved integration boundary between GORA and external systems.

### Architectural role

```txt
Agent / Workflow Layer
        |
        v
  Tool Adapter Layer
        |
        v
  External Systems
```

Adapters isolate business logic from provider-specific APIs and prevent unrestricted direct system access.

### Design principles

**Bounded capability surface.** Adapters expose narrow, explicit operations only. Never expose: raw shell execution, raw kubectl passthrough, raw AWS CLI passthrough, arbitrary HTTP proxying, or unrestricted provider SDK access.

**Deterministic behavior.** Adapter operations must be predictable, typed, schema-validated, and safe for workflow execution.

**Auditability.** Every invocation emits audit metadata: caller identity, workflow/execution ID, operation invoked, target resources, timestamp, result/failure state.

**Idempotency.** Where practical, mutating operations must be designed for idempotent retry.

### Common contract requirements

All adapter methods define: operation name, input schema, output schema, side effects, idempotency expectations, retry semantics, authorization requirements.

### Adapter classification

| Classification | Description | Examples |
| --------------- | --------------- | ----------- |
| Read-only | Safe for diagnostic/informational use | `getDeploymentStatus()`, `listPods()`, `getPipelineStatus()` |
| Controlled mutation | Requires policy checks, optional approval | `mergePullRequest()`, `syncApplication()`, `triggerPipeline()` |
| Privileged mutation | Requires strict approval/workflow governance | Production deploy triggers, rollback operations, infrastructure provisioning |

*Amended 2026-10-08 (ADR-019, ADR-022, ADR-023):* three examples above are operations GORA never
has. `mergePullRequest()`: GORA never merges; the code host's auto-merge merges a GORA pull request
once its gate is met (L15; ADR-013, ADR-023 section 8). `syncApplication()` and production deploy
triggers: GORA writes desired state to Git and never writes to a cluster or deploys (ADR-019 section
6). `triggerPipeline()`: GORA never builds (L7; ADR-022 section 3). A rollback is a pull request,
not an adapter call (ADR-022 section 10). The adapters below are amended accordingly.

### Defined adapters

#### GitHub Adapter

Operations: `createPullRequest()`, `updatePullRequest()`, `mergePullRequest()`, `closePullRequest()`, `createIssue()`, `commentOnIssue()`, `getPullRequestStatus()`, `getChangedFiles()`, `fetchFileContent()`, `commitGeneratedContent()`

Use cases: promotion PR generation, manifest updates, release metadata commits.

*Amended 2026-10-08 (ADR-022, ADR-023):* `mergePullRequest()` is removed (L15). The operations the
release model needs are narrower and named in ADR-022 and ADR-023: read files and changed paths at a
commit, commit files on a `gora/*` branch and delete that branch, open, update and close a pull
request, enable the host's auto-merge on it, comment on it, post GORA's commit status, read reviews
and commit statuses, read team membership, read the repository's rules and settings, and create an
annotated tag. The release model needs no issue operation.

*Amended 2026-11-14 (L10, L22; the Git adapter below):* the adapter is two objects on one HTTP
core, and the state of a repository, which the Git adapter leaves to the host, is read by the
first of them.

- **A reader with no write, and a writer on GORA's App.** `GitHubReader` reads the head of a
  branch, a repository's id, its tags and the tag objects GORA reads back; no method of it
  writes, and a test holds its list of operations as a literal, as it holds the writer's (test).
  It runs on the token of the run: ADR-022 section 14 names what GORA does to a repository, which
  is writing branches, tags, pull requests, statuses and auto-merge, and ADR-023 section 7 asks of
  a read access to what it shows. `GitHubAdapter` keeps the writes and acts as GORA's GitHub App
  (L22); it loses its read of a branch's head, which nothing called, since that is the reader's.
  Side effects of the reader: none. Retry: none. Every call is a `GET`.
- **Four operations, each checking what the host sent against what was asked (test).**
  `repository_id`: `GET /repos/{r}`, whose `full_name` is the repository asked, compared as GitHub
  compares it, without case, and whose `id` is a positive integer of at most 2**63 - 1, since
  GitHub's ids are 64-bit; the id is the identity of a repository, a name being what a rename
  moves. `branch_head`: `GET /repos/{r}/git/ref/heads/{b}`,
  whose `ref` is `refs/heads/{b}` and whose object is a commit with a git object name.
  `tag_refs`: `GET /repos/{r}/git/matching-refs/tags/{prefix}v`, with the tag prefix of the
  artifact's declaration (empty, or the artifact's name and `/`), every ref of which starts with
  what was asked and points at a commit (a lightweight tag) or a tag (an annotated one), and
  nothing else. `tag_object`: `GET /repos/{r}/git/tags/{sha}` for an annotated ref `tag_refs`
  returned, whose `sha` and `tag` are the ref's and whose object is a commit: a tag of a tag is
  refused, since following the chain is an unbounded number of requests and GORA tags commits
  only. Object names are checked by the grammar the Git adapter uses. The results are frozen
  dataclasses, `TagRef` and `TagObject`, validated where they are built, and no raw dictionary
  leaves the adapter. The adapter returns the refs the host matched, and which names are rc tags
  is the domain's to decide.
- **Measured on 2026-11-14.** `matching-refs` is not paginated: `per_page` and `page` are ignored
  and no `Link` header is sent, so every match comes in one response: 6 refs for gora, 502 for
  golang/go, 1,257 for kubernetes/kubernetes, 1,875 for aws/aws-sdk-go, 8,424 for
  googleapis/google-cloud-go, 3.86 MB in 0.6 to 0.8 s (1.25 s on a repeat asked with `per_page=5`
  and `page=2`, which answered the same 3.86 MB with no `Link`). No match is `200 []`. The prefix
  is a string prefix: `tags/v` matches `refs/tags/vnext`. A response with a `Link` header is
  refused, since it means the endpoint changed, and following it would be the code guessing
  (test). gora's own `v0.2.0-rc.1` to `v0.2.0-rc.6` are lightweight tags, and outside the rc
  grammar. The old name of a renamed repository answers `301` with a `Location` of
  `/repositories/{id}`.
- **Bounds on every request of both objects, in one core (test).** httpx's timeout, 10 s, for the
  connection and for each read; a deadline for the whole request, body included, 60 s, 48 times
  the slowest answer measured, and checked between HTTP framing events, when the headers arrive
  and as each chunk of the body does; within one event (the headers, a chunk header, a trailer),
  of at most 100 KiB (httpcore's limit on an incomplete event), each read is bounded by httpx's
  read timeout and no total deadline applies, so a host that sends such framing a byte at a time
  outlasts the deadline, and the transport is TLS to the configured host, so only that host can
  send it; a cap on the body, 64 MiB, 17 times the largest measured (about 146,000 refs at the
  measured 459 bytes a ref), checked against the length the host declares and against what
  arrives, so a body over it is refused and never read short. The body is read as it arrives and never decoded: every request
  asks for `Accept-Encoding: identity`, an answer whose `Content-Encoding` is present and not
  `identity` is refused, and the bytes counted are the bytes sent, since a stream of empty deflate
  blocks decodes to no byte and would pass the cap and the deadline for ever. A bound is a
  refusal that names it, never a truncated or a partial answer.
- **A redirect is refused, not followed (test).** A `3xx` is an error naming the status and that
  no redirect is followed, whatever the client is set to, since the identity of a repository is
  its id and never a name a redirect leads to. The writer refuses one too; before, it failed on
  one only because the body was not the JSON it expected.
- **No retry, for any status (test).** Retry semantics are defined per operation (the contract
  requirements above), and for these there are none: a run stays deterministic, and the caller
  sees the host's answer. A `403` or a `429` is an error naming the status and, when present, the
  values of `X-RateLimit-Reset` and `Retry-After`, shown only if they are printable ASCII of at
  most 64 characters, and no other header; the caller decides what waiting means.
- **A message holds no token, no query string and nothing httpx wrote (test).** It names the
  operation and the refusal's rule, and the host's own text only where it reports an error
  status, and then only as the `message` field of its JSON body, when that is printable ASCII of
  at most 200 characters: a message that holds a control character, is longer or is not a string,
  and a body that is not JSON, show nothing, and no other field or byte of the body is shown,
  since a message reaches a terminal and the host's text is not trusted there.
- **Nothing leaves the operation it was built for (test).** A value placed in a URL path with a
  part that is empty, `.` or `..` is a caller's `ValueError` before any request, since `quote`
  keeps `.` and httpx removes dot segments, so `..` would have sent the request, with the token,
  to another path. A body that does not parse as JSON, however it fails (not UTF-8, nested past
  what the parser reads), is a `GitHubError` like any other body that is not the expected one.

#### Git Adapter

*Added 2026-10-20 (ADR-018 section 4, ADR-026 section 9):* the Git adapter reads the content of a
repository, its commits, trees and blobs, from a local clone, and never writes. The state of a
repository, the head of `main`, its tags and the tag objects GORA reads back, comes from the
code host through the host's adapter (L10, L22): a clone is a cache of content addressed by
object name, where a missing object is a loud error, and a missing ref would be a silently
wrong answer. Every operation takes full object names the host gave.

- Operations: a commit with its message and the paths it changed against its first parent, a
  root commit against nothing; the first-parent commits of a commit after a given one, oldest
  first, each the same: the adapter reads and hashes the two it is given and the commits
  between them, while git's `rev-list` parses the chain down to the given one, or to the root
  for one off the chain, which the adapter refuses once it has read one commit more; the
  paths in which two commits' trees differ; the blobs and gitlinks of a commit under given
  paths, with their modes, each path one that an input glob's fixed components spell
  (ADR-026 section 9). Paths are bytes, taken from the trees and never decoded (L8); a
  message is UTF-8, a byte that is not read as the replacement character.
- Every object an answer rests on, each commit, each tree on the way and each blob, is hashed,
  SHA-1 or SHA-256 by the length of its name, and compared with its name before it is used;
  one that does not hash to its name is refused. git compares an object with its name only
  where it parses it with `parse_object`: `ls-tree` refuses a rewritten tree it is given,
  and `cat-file`, `diff-tree`, `rev-list` and `log` read a rewritten blob, tree or commit as
  it is (measured on git 2.43 and 2.56). The adapter's own check is what makes the clone a
  cache addressed by name, and tests rewrite a loose blob, tree and commit, and a pack's
  index, to show it. A tree is read as git reads it: each mode by its type (an old
  `100664` as `100644`, a type that is no file, link or tree as a gitlink), and a name git's
  fsck only warns of (`.`, `..`, `.git`) as a name, since a host keeps such histories and
  `main` is never rewritten. It is refused where git refuses to read it or its fsck reports
  an error (out of git's order, a name twice), and where a name holds `/`, which would make
  two entries one path. Tests take each verdict from `git ls-tree` and `git fsck`.
- git is the object store and nothing more. The adapter runs three commands and no other:
  `rev-parse` to ask whether the clone is shallow, `cat-file --batch` to read objects, and
  `rev-list --first-parent` to propose a chain of commits, which the adapter verifies link by
  link from the commit objects it read. It parses commits and trees and compares trees
  itself, so no setting that shapes git's listings or diffs has anything to act on. The test
  lists the settings, each with the plain git command whose output it changes, or with why
  no object read uses it, and shows both; on a clone that sets every one, with a replace
  ref, a graft and a commit-graph out of step with the commits, the adapter's answers are
  those of a clean copy. A setting git adds is a line in that list.

  *Amended 2026-10-22 (correction):* the list is kept by hand: a setting a later git adds is in it
  only once someone adds it, and no test detects one that is missing.
- Every call has an environment built from `PATH` alone, so no variable of its caller's
  reaches git: no system or global configuration or attributes, no terminal prompt, no
  replace objects, no graft file, no lazy fetch, and an empty `GIT_ALLOW_PROTOCOL`, so git
  allows no transport whatever the repository's `protocol.<name>.allow` says. On its command
  line it sets `core.commitGraph=false`, and git runs by an absolute name from the root of
  the file system. None of the three commands reads an index or a work tree, so the only
  program a repository can name for them is a promisor remote's, which no transport
  reaches. Tests show it: a hostile repository runs no program through the adapter while the
  same configuration runs one for `git status`, and a promisor remote, through `file://` or
  a command-running `ext::`, is never asked, with either of `GIT_ALLOW_PROTOCOL` and
  `GIT_NO_LAZY_FETCH` alone (a git before its 2024 security releases ignores the second, and
  fails that test).
  *Amended 2026-10-20 (ADR-011):* the absolute name is `GORA_GIT`, `/usr/bin/git` unless it is
  set, where the release image installs git. It is never looked up on `PATH`, so no
  directory on the caller's `PATH` puts another program in git's place, and a name that is
  not absolute is refused.
  *Amended 2026-11-07 (ADR-011):* git is installed at `/usr/bin/git` by GORA's base
  image, which the release image starts from; the release image installs no package.
- Each guard is held by its kind of test. One that answers a threat a test can build is
  shown needed by what git does without it. The rest narrow what git is given: no system or
  global configuration, no system attributes, no prompt, no pager, C messages. On the git a
  test runs no answer depends on them (the settings test also sets every listed setting in
  system and global files, with those two variables dropped), and one test holds the
  environment as a literal, so none is dropped unseen; nothing more is claimed for them.
- It names the repository with `--git-dir`, so git discovers none and checks no owner: a
  checkout another user owns, as a CI container's usually is, is read with no
  `safe.directory` opened. A `.git` that is a file or a link, a `commondir`,
  `objects/info/alternates` and a link anywhere under `objects`, each of which reads another
  repository's objects, are refused.
- It refuses, before anything is computed, a shallow clone and a clone git made with
  `--filter`; any clone fails loudly on the first object it lacks, named in full, and it
  never fetches. Where GORA reads a repository's history, it reads a full clone of it
  (release-model section 9).

#### ArgoCD Adapter

Operations: `getApplicationStatus()`, `getSyncStatus()`, `syncApplication()`, `refreshApplication()`, `waitForHealthy()`, `getDeploymentHistory()`

Use cases: promotion monitoring, deployment verification, post-deploy health validation.

*Amended 2026-10-08 (ADR-019 section 6, ADR-024 section 7):* the Argo CD adapter is read-only:
`syncApplication()` and `refreshApplication()` are removed, since GORA never writes to a cluster and
Argo CD reconciles from Git (ADR-019 section 6). What it reads is shown in `gora status`; it is
never a verification, which is a commit status (ADR-024 section 7).

#### Kubernetes Adapter

Operations: `getDeploymentStatus()`, `listPods()`, `getPodLogs()`, `getRecentEvents()`, `getResourceHealthSummary()`, `describeWorkload()`

Restrictions: initially read-only unless explicitly expanded.

Use cases: incident triage, rollout diagnostics, health summarization.

*Amended 2026-10-08 (ADR-019 section 6):* the Kubernetes adapter is read-only, with no expansion:
GORA never writes to a cluster.

#### CI/CD Adapter

Operations: `triggerPipeline()`, `getPipelineStatus()`, `getBuildArtifacts()`, `getBuildMetadata()`, `retryPipeline()`, `cancelPipeline()`

Providers: Jenkins, Tekton, GitHub Actions (abstracted behind common interface).

*Amended 2026-10-08 (ADR-022 section 3, ADR-023 section 2):* the CI/CD adapter is read-only:
`triggerPipeline()`, `retryPipeline()` and `cancelPipeline()` are removed, since GORA never builds
(L7) and a project's CI hands GORA its digest (`gora register`, ADR-023 section 2).

#### AWS Adapter

Operations: `lookupResource()`, `getCloudWatchMetrics()`, `fetchLogInsights()`, `triggerRunbook()`, `getResourceHealth()`, `fetchTagMetadata()`

Restrictions: no arbitrary infrastructure mutation without explicit approval model.

*Amended 2026-10-08 (ADR-019 section 6):* the AWS adapter is read-only: `triggerRunbook()` is
removed, since GORA never writes to infrastructure, and the incident workflows only read. The
registry adapter of ADR-018 section 7 (read a manifest by digest, write it under a new tag, never
new content) is GORA's other bounded write.

*Amended 2026-10-09 (ADR-027 section 2):* the registry adapter also attaches GORA's signed
verification summaries to a digest, as OCI referrers: content of GORA's own, never an image or a
tag.

### Adapter registration

Adapters register through a capability registry:

```yaml
adapters:
  github:
    capabilities:
      - createPullRequest
      - mergePullRequest
  argocd:
    capabilities:
      - getApplicationStatus
      - syncApplication
```

*Amended 2026-10-08 (ADR-006 notes above):* the example names two removed operations; a registry
lists only the operations that remain.

This enables policy enforcement and future dynamic capability discovery.

## Consequences

- No workflow or agent may bypass the adapter layer to reach external systems
- New external integrations require a new adapter with explicit contract definition
- Adapter contracts serve as the security boundary for all GORA operations
- The adapter registry becomes the source of truth for what GORA can do

## Amendments

### 2026-10-08 — no merge, no cluster write, no pipeline trigger

The release model (ADR-019, ADR-022, ADR-023) removes operations this ADR listed: GORA never
merges (L15), never writes to a cluster (ADR-019 section 6), never builds or triggers a build
(L7). The GitHub adapter keeps the narrow operations the model names; the Argo CD and CI/CD
adapters are read-only, and what they read is never a verification, which is a commit status
(ADR-024 section 7); the registry adapter of ADR-018 retags a digest and writes nothing new.

### 2026-10-09 — the registry adapter attaches GORA's verification summaries

ADR-027 records each result of a stage's verifications as a signed verification summary on the
digest. For this ADR: the registry adapter attaches GORA's summaries as referrers of a digest, a
write of GORA's own content; "writes nothing new" in the amendment of 2026-10-08 reads with that
exception.

### 2026-10-20 — the Git adapter: content from a local clone, state from the host

*Defined adapters* gains the Git adapter: read-only reads of a local clone's objects through
three commands, every object compared with its name, with the environment, the settings and
the refusals it states; the refs and tag objects it never reads, since they are the host's
state. Release-model section 9 gains the condition of a full clone, ADR-026 section 9 a note:
the head `gora changes` reads is the host's, and ADR-003 a note: the adapter its rollback
rules need exists, and the rules do not.

### 2026-10-20 — the Git adapter runs the git `GORA_GIT` names

ADR-011 puts git in the release image. The Git adapter's note on its
environment gains where that git is: `GORA_GIT`, an absolute path, `/usr/bin/git` unless
set, never looked up on `PATH`.

### 2026-10-22 — the Git adapter's list of settings is kept by hand

A correction. The Git adapter's note on the settings its test lists said that a setting git adds
is a line in that list; the list is written by hand, and nothing detects a setting missing from
it. A note in place says so.

### 2026-11-07 — the release image has git from GORA's base image

The entry of 2026-10-20 put git in the release image, and the note on `GORA_GIT` said the
release image installs it. The release image starts from GORA's base image now (ADR-011, the
entry "the release image starts from the base, by digest; the Debian files leave its inputs"),
which installs git at `/usr/bin/git`; the release image installs no package. `GORA_GIT` and the
adapter are as they were. A note in place says so.

### 2026-11-14 — the GitHub adapter reads the repository's state from the host

The Git adapter's entry of 2026-10-20 left the state of a repository, the head of `main`, its
tags and the tag objects GORA reads back, to the host's adapter. *Defined adapters* gains that
side of the GitHub adapter: a reader with no write, on the run's token, and the writer, on GORA's
App (L22), now two objects on one HTTP core that bounds every request, with a deadline, a cap on
the body, no redirect followed and no retry. The reader's four operations, the facts about the
host they rest on (`matching-refs` is not paginated, and the prefix is a string prefix), the
bounds with their reasons and the rate-limit error are in the note in place. The writer loses
its read of a branch's head: nothing called it.

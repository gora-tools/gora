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
**Amended:** 2026-11-15 — every token goes to one host, over explicit TLS (ADR-017)
**Amended:** 2026-11-15 — every request of the three adapters is bounded, and so is a path (ADR-015)
**Amended:** 2026-11-15 — the HTTP client is httpx2 (ADR-005)
**Amended:** 2026-11-15 — the signing adapter holds GORA's keys in a signer (ADR-023, ADR-017)
**Amended:** 2026-11-15 — the GitHub reader serves a port, and one entry per host chooses the adapter
**Amended:** 2026-11-15 — the Git adapter names the commit that added a file (ADR-026)
**Amended:** 2026-11-18 — the GitHub core takes only an installation token (L22, ADR-022)
**Amended:** 2026-11-24 — the boundary between GORA's App's token and another is the pipeline
step's, not built (correction)
**Amended:** 2026-11-26 — the pipeline step is the job `changes` of `candidate.yml` (correction)
**Amended:** 2026-11-24 — a host's text or header value that reflects a token is not shown
(ADR-015)
**Amended:** 2026-11-27 — the registry adapter reads a digest and its bundles on GHCR (ADR-018)

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

  *Amended 2026-11-18 (L22, ADR-022 section 14):* "the token of the run" is `GORA_GITHUB_TOKEN`,
  read through `code_host` only, an installation token of a GitHub App, never the `GITHUB_TOKEN`
  of Actions by GORA's choice. GORA takes no person's token, for a write or a read. `HttpCore`,
  the one place that sends a GitHub token, for the reader and the writer, refuses with a
  `ValueError`, before any client is built and in this order: a token that is not a `b64token`
  (RFC 6750 section 2.1: letters, digits and `- . _ ~ + /`, with `=` only at the end), which is
  the standard of a Bearer token, not a structure of GitHub's, and which both forms of an
  installation token fit (the older, of letters, digits and `_`, and the stateless, of base64url
  with dots, about 520 characters); then a token that does not start with `ghs_` and at least one
  more character, compared with case. The tokens of a user account (`ghp_`, `github_pat_`,
  `gho_`, `ghu_`, `ghr_`) are refused, whoever hands one over. Both messages are the rule's
  text, hold no part of the token, and the second does not name the prefix; the empty token is
  refused by the second. What this holds: a token of a user account is refused. Any installation
  token passes, the `GITHUB_TOKEN` of Actions and another App's included: GORA does not tell its
  App's token from another installation token, and the pipeline step of build.yml, not built, is
  to hold that boundary (ADR-022 section 14). Rejected: reading the App id inside a stateless token
  (`ghs_<app id>_<JWT>`), a structure GitHub does not promise to keep, in a token GORA treats as
  opaque. Sources: GitHub, "GitHub credential types", and GitHub's changelog of 2026-04-24,
  2026-05-15 and 2026-10-02 on stateless installation tokens.

  *Amended 2026-11-24 (correction):* nothing holds the boundary between GORA's App's token and
  another installation token today: GORA does not tell them apart, by decision (ADR-022 section
  14), and the pipeline step of `build.yml` that is to hold it is not built. The note above says
  so; what the core refuses and accepts is unchanged.

  *Amended 2026-11-26 (correction):* the step that holds the boundary is built, and it is the job
  `changes` of `.github/workflows/candidate.yml`, not a job of `build.yml` (ADR-022 section 14,
  the note of this date). It makes the App's token and hands it to `gora changes` as
  `GORA_GITHUB_TOKEN`, and a test of that file holds that the token comes from the step that makes
  it and from nowhere else. The core is unchanged: any installation token passes, and the job has
  not run yet.
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

  *Amended 2026-11-15 (the reader serves `CodeHost`):* the reader serves the port the services
  read a code host through, `CodeHost` in `services/code_host.py`, and a test holds that its
  methods are exactly the port's. `repository_id` returns the id as its decimal string, checked by
  the grammar `REPOSITORY_ID`, which `certificate.py` and `provenance.py` use too. `tag_refs` and
  `tag_object` are replaced by `tag_names(tag_prefix)`, the names under `{tag_prefix}v` and
  nothing else of a ref, and `tag(name)`, which takes a version tag and gives a `Tag` of the
  domain: its name, its commit and its message, or none for a lightweight tag. `tag` asks for
  `GET /repos/{r}/git/ref/tags/{name}`, whose `ref` is `refs/tags/{name}`: an object that is a
  commit gives the `Tag` after that one request, and one that is a tag gives a second,
  `GET /repos/{r}/git/tags/{sha}`, whose `sha` and `tag` are the ref's, whose `message` is a
  string and whose object is a commit (a tag of a tag is refused, as before). Every answer is
  checked before a `Tag` is built, and a tag the host does not hold is its 404, a `GitHubError`,
  never a "no tag" value. That endpoint matches a name exactly: `v0.0.1`, a prefix of the
  existing `v0.0.1-rc`, answers 404 (recorded on 2026-11-15, `ref-tag-v0.0.1`). `TagRef`,
  `TagObject` and `TagKind` are gone. The port carries a commit and a message and no git object,
  because the hosts differ in what they give of a tag (Bitbucket Cloud gives no name of a tag
  object, GitLab gives everything in one answer, GitHub in two requests; Bitbucket Cloud REST
  API, refs: https://developer.atlassian.com/cloud/bitbucket/rest/api-group-refs/, whose tag has
  `target`, a commit, and `message`, `tagger` and `date`, null for a lightweight tag; GitLab Tags
  API: https://docs.gitlab.com/api/tags/, whose answer has `commit`, `target` and `message`; both
  read on 2026-11-15), and GORA reads only the commit and the message (ADR-027 section 1).
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
- **Bounds on every request of both objects, in one core (test).** httpx2's timeout, 10 s, for the
  connection and for each read; a deadline for the whole request, body included, 60 s, 48 times
  the slowest answer measured, and checked between HTTP framing events, when the headers arrive
  and as each chunk of the body does; within one event (the headers, a chunk header, a trailer),
  of at most 100 KiB (httpcore2's limit on an incomplete event), each read is bounded by httpx2's
  read timeout and no total deadline applies, so a host that sends such framing a byte at a time
  outlasts the deadline, and the transport is TLS to `api.github.com`, so only that host can
  send it; a cap on the body, 64 MiB, 17 times the largest measured (about 146,000 refs at the
  measured 459 bytes a ref), checked against the length the host declares and against what
  arrives, so a body over it is refused and never read short. The body is read as it arrives and
  never decoded: every request asks for `Accept-Encoding: identity`, an answer whose
  `Content-Encoding` is present and not `identity` is refused, and the bytes counted are the
  bytes sent, since a stream of empty deflate blocks decodes to no byte and would pass the cap
  and the deadline for ever. A bound is a refusal that names it, never a truncated or a partial
  answer.

  *Amended 2026-11-15 (the egress bullet below):* the sentence on the transport said "TLS to the
  configured host", a URL a constructor took and nothing else guarded, verified against whatever
  trust store the environment named. The host is a constant of the code now, and the CA
  certificates are `certifi`'s.

  *Amended 2026-11-15 (every request of the three adapters, and a path):* the bounds above hold
  for every request of the GitHub reader, the GitHub writer and the Slack adapter (ADR-015),
  which called `httpx` with a timeout and nothing else, so that "bounded" was a name there. The
  request is written once, as `BoundedRequest` in `gora.adapters.egress`, next to the client it
  builds, and a host is a `Host` handed to it: its address, the error class it raises, the JSON
  field of its error text (`message` for GitHub, `error` for Slack) and the headers it names for
  a rate limit (`X-RateLimit-Reset` and `Retry-After` on a 403 or a 429 for GitHub, `Retry-After`
  on a 429 for Slack). No bound is a field of a `Host`: `DEADLINE`, `MAX_BODY` and `MAX_PATH` are
  one value each, the same for every host. The GitHub core, `_http.py`, keeps what is GitHub's,
  and `HttpCore` is its configuration of the shared request. For Slack, the transport the
  deadline note speaks of is TLS to `slack.com`.

  The bounds gain a cap on a path, `MAX_PATH`, 2,048 characters: the path, the string appended to
  the host after `segment` has encoded it, may not be longer, and one that is is a caller's
  `ValueError` before any request, whose message names the cap and never the path. Before it, a
  branch or a content path of 70,000 characters made a URL httpx refuses (65,536) and a
  `GitHubError` that named the operation with the whole path, and one of 3,000 was sent; the
  query is not a path, and a head long enough to make httpx2 refuse the URL is still a
  `GitHubError`, "invalid URL", with no request made. Measured on 2026-11-15 as the deadline and
  the cap on a body were: every operation of the reader and the writer called with its inputs at
  their grammars' maxima, the path taken from the request the transport receives
  (`tests/unit/test_egress.py` holds it, and fails when the cap is under 4 times the longest).
  The repository is 140 characters (`REPO`), a stage and an artifact name 63 (`STAGE`, `NAME`), a
  version 128 (`VERSION`, `FINAL_VERSION`), a tag prefix 64 (`TAG_PREFIX`). The reader checks no
  grammar of a branch, so the branches are the ones GORA names itself (ADR-023 section 6), the
  longest of which, `gora/reject/<artifact>/<version>`, is 204 characters; the content paths are
  the release files the model names (ADR-026 section 1), the longest of which,
  `rejections/<artifact>/<X.Y.Z>.yaml`, is 208. The longest path is 366 characters, the head of
  that branch; the longest content path 365; the longest tag path 236; the Slack path 17. The cap
  is 2,048, 5.6 times the longest, a round number at least 4 times it, as the deadline is 48
  times and the cap on a body 17 times what was measured; it refuses none of the paths measured,
  and it is a thirty-second of the limit httpx2 puts on a URL. A branch the reader is asked for
  has no grammar, so one whose path is over the cap is a caller's `ValueError`, by design.

  *Amended 2026-11-15 (the longest tag path):* the longest tag path was that of `tag_refs`, 236.
  The tag operation of the reader is now `tag`, and its longest path is the ref's,
  `git/ref/tags/<tag prefix>v<version>`: 354 characters, for a name of 193 (a tag prefix of 64,
  `v` and a version of 128), as `tests/unit/test_egress.py` measures it; the second request of an
  annotated tag, `git/tags/<sha>`, is shorter. The longest path is still 366, the head of the
  branch, so the cap is still 5.6 times the longest (2,048 over 366).
- **A redirect is refused, not followed (test).** A `3xx` is an error that says the host answered
  a redirect, names the status and says that no redirect is followed; the client GORA builds
  never follows one, since the identity of a repository is its id and never a name a redirect
  leads to. The writer refuses one too; before, it failed on one only because the body was not
  the JSON it expected.

  *Amended 2026-11-15:* the message said "the repository was renamed or moved", on any path. The
  core does not know that: a renamed repository's old name answers `301` (measured above), and
  another path can answer one for another reason. It says what it saw.

  *Amended 2026-11-27 (the registry adapter):* one exception, and only one. A blob of GHCR is
  served from storage: `GET /v2/<repository>/blobs/<digest>` on `ghcr.io` answers `307` with a
  `Location` on `pkg-containers.githubusercontent.com` (measured on 2026-11-27 for the config
  blob and three bundles of `ghcr.io/gora-tools/gora`). The registry adapter's blob read follows
  that one redirect, through `egress`, on these terms: the `Location` is an absolute `https` URL
  whose host is exactly the constant `pkg-containers.githubusercontent.com`, with no port, no
  user information and no fragment, and what it appends to the host is within `MAX_PATH`; the
  request carries no `Authorization` header; it is bounded as every request is, within the same
  deadline as the first; its body must hash to the blob's digest. A redirect from storage, a
  `Location` on any other host or scheme, and a `3xx` of every other request stay refused. The
  `Location` is read once, by httpx2's own URL parser (`httpx2.URL`), and must be written as that
  parser writes it back (an explicit `:443`, a host in capitals or a character it would escape is
  refused): the request is sent with the URL that was checked, so no second reading of the text
  decides where it goes. The `Location` is a signed link that expires: it is never shown, in an
  error, a log or the audit journal, neither whole nor its path or its query; an error of that
  request names the operation (`GET /v2/<repository>/blobs/<digest>`), the storage host and the
  status.
  Measured on 2026-11-27: the longest `Location` appended 440 characters to the host (whole
  URLs of 482 to 484 characters, 13 query parameters), within `MAX_PATH` (2,048), so the shared
  cap holds and the redirect has no bound of its own; httpx2 sent the `Location` byte for byte,
  without `Authorization`, through `egress.client`, `certifi`'s CA certificates verified the
  storage host, and the body hashed to the blob's digest.
- **No retry, for any status (test).** Retry semantics are defined per operation (the contract
  requirements above), and for these there are none: a run stays deterministic, and the caller
  sees the host's answer. A `403` or a `429` is an error naming the status and, when present, the
  values of `X-RateLimit-Reset` and `Retry-After`, shown only if they are printable ASCII of at
  most 64 characters, and no other header; the caller decides what waiting means. A value that
  holds a token is left out (the note of 2026-11-24 under the next entry).
- **A message holds no token, no query string and nothing httpx2 wrote (test).** It names the
  operation and the refusal's rule, and the host's own text only where it reports an error
  status, and then only as the `message` field of its JSON body, when that is printable ASCII of
  at most 200 characters: a message that holds a control character, is longer or is not a string,
  and a body that is not JSON, show nothing, and no other field or byte of the body is shown,
  since a message reaches a terminal and the host's text is not trusted there.

  *Amended 2026-11-24 (a host's text that reflects a token):* "holds no token" held for GORA's
  own text, not for what a host sends: a host that reflected the token of the request in its
  `message` or `error`, or in a header value a `Host` names, put it on standard error (a fake
  host showed it). The host's text, and each such header value, is now shown only when it holds
  neither the credential of the `Authorization` header the request sent (the part after the
  scheme, kept privately by `BoundedRequest`, and nothing else of the headers) nor a string in
  the shape of that host's tokens: a field of the `Host`, `token_shapes`, which each host states
  for itself (`gh[pousr]_` and `github_pat_` for GitHub, `xox[a-z]-` and `xapp-` for Slack),
  compiled once and matched without regard to case, so that a false match only hides the host's
  text. `egress` names no shape. A request with no `Authorization` header has no credential and
  no check of one; the shapes still apply. The error still names the operation and the status,
  and leaves out the value alone. Rejected: hiding only the exact credential, which misses a
  partial or another token, and hiding only shapes, which misses a credential with no known
  prefix. Accepted: a fragment of a token that has no prefix and is shorter than the whole is
  not detected; the host is the one GORA names in code, over verified TLS.
- **Nothing leaves the operation it was built for (test).** A value placed in a URL path with a
  part that is empty, `.` or `..` is a caller's `ValueError` before any request, since `quote`
  keeps `.` and httpx2 removes dot segments, so `..` would have sent the request, with the token,
  to another path. A body that does not parse as JSON, however it fails (not UTF-8, nested past
  what the parser reads), is a `GitHubError` like any other body that is not the expected one.
- **Every token goes to one host, over explicit TLS, and no variable of the environment reaches
  a client (test).** The host of each adapter is a constant of the code: `api.github.com` for the
  reader and the writer, `slack.com` for the Slack adapter (ADR-015). No constructor takes a URL
  or a client, so a token reaches that host or the call is refused. Every outbound client of
  GORA is built in one module, `gora.adapters.egress`: `trust_env` off, no proxy, no redirect
  followed, no retry, HTTP/1.1, and a TLS context made there, with the certificate and the host
  name verified, TLS 1.2 at the least, `VERIFY_X509_STRICT`, and the CA certificates of
  `certifi`, pinned with hashes in `uv.lock`, and no others: the machine's trust store is not
  read. No variable of the environment reaches a client: not `HTTPS_PROXY`, `HTTP_PROXY` or
  `ALL_PROXY`, each of which sends the connection, and the token, to the proxy it names, and not
  `SSL_CERT_FILE` or `SSL_CERT_DIR`, each of which replaces the trust store, so that whoever sets
  one decides whom the token goes to. Settings come from `GORA_*` variables only (ADR-017), and
  none names a proxy or a certificate; the Git adapter already builds its environment from
  `PATH` alone. The transport is a seam for tests: an adapter takes a `transport`, which tests
  hand over as `httpx2.MockTransport`, and no production caller passes one. Measured on
  2026-11-15 on the code before this amendment, with httpx 0.28.1 and httpcore 1.0.9: with
  `SSL_CERT_FILE` set to the CA of a host that is not GitHub, `repository_id()` returned and that
  host received `Authorization: Bearer <token>`; with `HTTPS_PROXY` or `ALL_PROXY` set, the
  address the connection was asked for was the proxy's; and Python 3.12's default context does
  not set `VERIFY_X509_STRICT`. The tests hold it: `tests/unit/test_egress_environment.py`
  impersonates the host inside the hermetic guard (a socket pair stands for the connection, a
  thread on its other end serves TLS with a certificate for the host's name from a CA the test
  made) and shows that under each variable no token reaches it and no proxy is asked for, for
  the reader, the writer and Slack, and that no adapter takes a URL or a client;
  `tests/unit/test_egress.py` holds the context, its CA count against the certificates in
  `certifi.where()`, and the arguments of the transport as a literal.

  *Amended 2026-11-15 (the bounds note above):* the bounded request every adapter sends through
  is in the same module, `egress`, as the client it sends over: a request goes out over no
  client but the one built there, and over no bound but the ones written there.

  *Amended 2026-11-27 (the registry adapter):* the registry adapter's hosts are constants of
  the code too: `ghcr.io` for the token and the API, and `pkg-containers.githubusercontent.com`
  for a blob's redirect, which receives no token. The image names its registry, so an image on
  any other registry is refused before any request, with an error that names its host. A `Host`
  may also name, for a status, the words its error adds (`Host.refusals`): the `Host` of GHCR's
  token request names `401`, a package that is not public, and `403`, no public package of that
  name, so `egress` writes them and no adapter reads the text of an error.
- **No 404 is an answer (test).** GitHub answers 404 for a branch that is missing as for a
  repository the token cannot see, so nothing in the core takes a 404 for "there is no such
  file". The core's `missing_ok` and the writer's `get_file_sha`, its one use and a read that
  nothing in `src` called, are removed, and every status of 400 or more is a `GitHubError`.
  The writer loses that read as it lost the read of a branch's head, and its operations are the
  five that remain.

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

  *Amended 2026-11-15 (ADR-026 section 9):* one more operation, `added_at(head, path)`: the newest
  first-parent commit of `head` at which `path` names an entry that is not a tree while its first
  parent's tree has none, the root commit when `path` is in it, and a `ValueError` when `path`
  names no such entry at `head`. It is the boundary of an artifact with no registered candidate,
  where the artifact's source is the release repository (ADR-026 section 9). `rev-list
  --first-parent` proposes the chain and the adapter verifies it link by link; presence is read
  from the trees and never from `git log`, whose answer `diff.renames` and the other settings of
  the settings test change. The walk goes from the head down and stops at the boundary, in
  batches: 64 commits, then twice the last, up to 8192, each batch one read of its commits and one
  of each level of the trees `path` crosses; the list of names `rev-list` gives is kept, and what a
  batch reads is not. Measured on 2026-11-15
  with git 2.56.0, on GORA's own full history (168 first-parent commits): 6 to 10 git processes
  and at most 0.10 s; on a full clone of golang/go (64,768): 26 to 67 processes, 1.5 to 9.4 s, 1,990
  to 64,782 commits walked (TESTING rule 8; no new limit: each process is bounded by `TIMEOUT`).
  `NotFirstParentError`, a `GitError`, is raised exactly where the adapter refuses an `after` that
  is not a first-parent commit of the head, with the message it always had; every other failure
  stays a plain `GitError`, so that a caller can name that refusal and no other.
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

#### Signing Adapter

Decided, not built (release-model section 13). It holds GORA's signing keys out of GORA's reach:
the private key of its GitHub App, and an attester's key when an attester is a key (ADR-023
section 11).

Operations: `sign(message)`, with the one algorithm its key has, and `public_key()`.

Use cases: the JWT that creates GitHub installation tokens, RS256 (ADR-023 section 11), and, when
an attester is a key, GORA's signatures as an attester (ADR-018).

- **A port, one adapter per signer.** The port is the two operations. An adapter serves one
  signer: AWS KMS first, then the others ADR-023 section 11 names, Google Cloud KMS, Azure Key
  Vault and a Vault or OpenBao Transit engine. No operation releases a key, creates one or
  changes a policy; the key is imported once, outside GORA.
- **Contract.** Input: a message of bounded length. Output: the signature, or the public key.
  Side effects: one entry in the signer's own audit log for each signature; nothing else
  changes. Retry: none, for any answer. Authorization: the signer's policy, which lets GORA's
  workload identity sign with the key and read its public key, nothing else.
- **Its credential is the workload's identity, by ADR-017's exception.** A projected web-identity
  token (Kubernetes) or the run's OIDC token (the pipeline), the token file and the role named by
  GORA's own settings, and no other credential: no static key, no shared configuration or
  profile, no instance metadata.
- **The egress rule holds for it, for every client it builds.** Its endpoint is from code
  (`kms.<region>.amazonaws.com`), the region a GORA setting checked by a grammar, with no proxy,
  `certifi`'s CA file and one attempt, as the rule above says of the HTTP adapters, for the
  signer's client and for the client its credential provider builds for itself, the STS client of
  the web identity (`sts.<region>.amazonaws.com`).
- **Measured on 2026-11-15, why each part is needed.** With botocore 1.44.3 and every one of
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, `AWS_CONFIG_FILE`,
  `AWS_SHARED_CREDENTIALS_FILE`, `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_KMS`, `HTTPS_PROXY`,
  `HTTP_PROXY` and `AWS_CA_BUNDLE` set: a default session failed on `AWS_PROFILE`
  (`ProfileNotFound`), even when the code passed no profile; with the session's config store
  rebuilt from defaults alone, the KMS client's endpoint was still `https://evil.example`, since
  `AWS_ENDPOINT_URL*` are read outside the config store; and the STS client the web-identity
  provider creates for itself took the environment's proxy and CA bundle. A session whose config
  store is built from the defaults with `environ={}` (`ConfigChainFactory`, `ConfigValueStore`),
  whose credential resolver holds one provider, the web identity
  (`AssumeRoleWithWebIdentityCredentialFetcher` over the token file GORA's settings name), with
  `ignore_configured_endpoint_urls` set, and one client factory for KMS and STS alike (endpoint
  from code, `Config(proxies={})`, `verify=certifi.where()`, one attempt), took none of them: the
  credential method was the web identity alone, with no proxy, `certifi`'s CA file and the
  endpoints `kms.<region>.amazonaws.com` and `sts.<region>.amazonaws.com`. Public botocore API
  only, no patch.
- **The SDK it needs is a decision of ADR-005** when the adapter is built.

*Enforcement class:* `review-only` until the adapter exists; the class it takes then is decided
with the code.

#### Registry Adapter

Reads a digest of an image and the Sigstore bundles attached to it, on GHCR, for the evidence
check of registration (ADR-018 sections 2 to 4). Built: the two reads below. Decided, not built
(release-model section 13): the writes of ADR-018 section 7 and ADR-027 section 2 (a registry tag
on a digest, GORA's verification summaries attached to it), a registry other than GHCR, and a
credential.

Operations: `manifest(digest)`, the bytes of the image index or image manifest at that digest,
and `bundles(digest)`, the bytes of each Sigstore bundle attached to it.

- **GHCR only, public packages only.** One entry of one mapping chooses the adapter by the
  image's registry, as `gora.adapters.code_hosts` does by the repository's host: `ghcr.io`, and
  no other. A request carries an anonymous token for `repository:<path>:pull`, which
  `https://ghcr.io/token` gives for a public package (measured: a token of 52 characters). For a
  private package the token endpoint answers `401` (measured on 2026-11-27 for this package while
  it was private), for a name with no package `403` (measured the same day), the first an error
  that says the package is not public and GORA reads public packages only, the second one that
  says there is no public package of that name (which a private package of another owner would
  answer as well); the credential a private package needs is decided with the first project that
  has one, as `sigstore: github` and `publicKey` are (ADR-005, the note of 2026-11-27).
- **Every answer is checked against what was asked.** A manifest's body hashes to the digest
  asked for, and its type is an OCI image index or an OCI image manifest, the same in its
  `Content-Type` and its `mediaType`. A blob's body hashes to its digest and has the size its
  descriptor states. A value the registry sends that is not the one expected is the adapter's
  error, never a value.
- **Bundles from the referrers fallback tag.** GHCR answers `404` to the OCI 1.1 referrers API
  (`/v2/<repository>/referrers/<digest>`, measured on 2026-11-27 for an index and both platform
  manifests), and cosign v3 keeps each digest's bundles behind the fallback tag
  `sha256-<hex>`: an OCI image index whose entries are artifact manifests of type
  `application/vnd.dev.sigstore.bundle.v0.3+json`. The adapter reads that index, keeps the
  entries of that type and ignores the others, reads each kept manifest by its digest, requires
  its `subject` to be the digest asked for, its config's `mediaType`, `digest` and `size` those
  of the OCI empty descriptor (GHCR adds an `artifactType` there, which is ignored) and exactly
  one layer of the bundle's type, and reads that layer. It returns the bundles in the index's
  order. A tag that is absent, and an index with no bundle, are errors that name the digest: it
  never answers empty, since an empty answer would read as no evidence required. Annotations, a
  predicate type among them, are unsigned and are not read: which bundle is which is decided from
  its verified payload (the Sigstore adapter, ADR-005). The referrers API is not asked: GHCR does
  not serve it.
- **Bounded.** Every request goes through `egress`, with its deadline, its cap on a body and its
  cap on a path; a blob's one redirect is the exception the redirect bullet above states. The
  fallback index holds at most 30 entries, 10 times the most measured (3, for a platform
  manifest: a signature, a provenance and an SBOM); more is an error.
- **Contract.** Input: a digest (`DIGEST`). Output: bytes, each checked against its digest.
  Side effects: none; the adapter writes nothing. Retry: none. Authorization: an anonymous token
  for one repository, sent to `ghcr.io` only.

*Enforcement class:* `test` — `tests/unit/test_registry.py`, on answers recorded from GHCR.

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

*Amended 2026-11-15 (the code host an adapter serves):* the code host an adapter serves is chosen
by the host in a repository's full name, `<host>/<owner>/<name>`, in `adapters/code_hosts.py`:
one read-only mapping with one entry per host, `github.com` today, whose factory checks the full
name with its own grammar, reads its own setting from `Settings` and returns the adapter as a
`CodeHost`. A host with no entry is a caller's `ValueError` before any setting is read or request
made. There is no registration function and no plug-in mechanism: a second host is a second entry.

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
its read of a branch's head: nothing called it. (Since 2026-11-15 the request that bounds them
is `egress`'s and the Slack adapter sends through it too: the entry of that date.)

*Amended 2026-11-18:* "on the run's token" is `GORA_GITHUB_TOKEN`, an installation token, never the
`GITHUB_TOKEN` of Actions by GORA's choice (ADR-022 section 14; the entry of that date).

### 2026-11-15 — every token goes to one host, over explicit TLS

The GitHub reader, the GitHub writer and the Slack adapter took a URL and a client from their
caller, and the client they built read the environment: a proxy variable sent the token to the
proxy, and `SSL_CERT_FILE` or `SSL_CERT_DIR` replaced the trust store (measured in the note in
place). *Defined adapters* gains the rule that ends both: the host of each adapter is a constant
of the code, every client is built in `gora.adapters.egress` with `trust_env` off, no proxy and
an explicit TLS context whose CA certificates are `certifi`'s, and no variable of the
environment reaches a client (ADR-017). The adapters lose their `base_url` and `client` and take
a `transport`, which only tests pass. The writer loses `get_file_sha`, and the core loses
`missing_ok`: no 404 is an answer, since GitHub also answers it for a missing branch and for a
repository the token cannot see. A 3xx says what the core saw, a redirect, and no longer that
the repository was renamed or moved. The bounds note on the transport, the redirect note and a
new note on the rule are in place.

### 2026-11-15 — every request of the three adapters is bounded, and so is a path

The entry of 2026-11-14 bounded the requests of the GitHub reader and the GitHub writer in a core
they shared, and ADR-015 called the Slack adapter bounded while it called `httpx` with a timeout
and nothing else: a redirect, a body that was not JSON, an `ok` that was a string, a text of a
million characters and 47 KiB of `gzip` that inflate to 50 MB each passed (measured, in ADR-015).
The request moves to `gora.adapters.egress`, written once, and each adapter is a configuration of
it: the GitHub core keeps `GITHUB_API`, its headers, `GitHubError`, its error text and its
rate-limit headers, and the Slack adapter gains the deadline, the cap on a body, the identity
encoding, the refused redirect, the safe text and `SlackError` for every failure. The bounds are
not per host. A path is bounded too, by `MAX_PATH`, which is measured, and a path over it is a
caller's `ValueError` before any request; the writer's content path and the reader's branch, the
two free-form values of a path, no longer reach httpx's limit on a URL with a `GitHubError` that
names the whole path. The notes under the bounds and the egress rule are in place.

### 2026-11-15 — the HTTP client is httpx2

The library the adapters send through is `httpx2`, a maintained fork of `httpx` 0.28.1 with the
same API, on `httpcore2` (ADR-005, which holds the decision). The bounds and the egress rule are
as they were: the limit on a URL (65,536) and the limit on an incomplete event (100 KiB) are the
same in `httpx2` and `httpcore2`, and GORA's TLS context is passed as `verify` unchanged, so the
trust store is `certifi`'s and no variable of the environment reaches a client. The statements in
the present tense name `httpx2` and `httpcore2`. What was measured on `httpx` 0.28.1 and
`httpcore` 1.0.9, and the entries above that say what the adapters called before, stay as they
were written.

### 2026-11-15 — the signing adapter holds GORA's keys in a signer

ADR-023 section 11 decides that GORA's signing keys are held by a signer that never releases
them, and ADR-017 that the adapter reaching it authenticates as the workload's identity alone.
*Defined adapters* gains the Signing Adapter, decided, not built: two operations, `sign(message)`
and `public_key()`; a port with one adapter per signer, AWS KMS first; the credential by
ADR-017's exception; the egress rule for every client it builds, the one its credential provider
builds for itself included; and the measurement of the cloud SDK that shows why each part is
needed. The SDK it needs is left to ADR-005 when it is built.

### 2026-11-15 — the GitHub reader serves a port, and one entry per host chooses the adapter

The state of a repository is read through one port, `CodeHost` (`services/code_host.py`): the
id of a repository, the head of a branch, the names of the tags under a prefix and a `Tag` by its
name, a value of the domain (`domain/release/tags.py`) that carries a commit and a message and no
git object. The GitHub reader serves it: `repository_id` returns the decimal string that the one
grammar of a repository id accepts, and `tag_refs` and `tag_object`, with `TagRef`, `TagObject`
and `TagKind`, are replaced by `tag_names` and `tag`, whose two requests are in the note in place.
Which adapter reads a repository is chosen by the host in its full name, in `adapters/code_hosts.py`,
one entry per host, and the GitHub reader's token is `GORA_GITHUB_TOKEN`, a `SecretStr` of
`Settings`. The longest tag path of the bounds is now that of `tag`, measured at 354 characters;
the cap is unchanged. The notes under *Defined adapters* and *Adapter registration* are in place;
the token decision of ADR-022 section 14 and ADR-006's "the token of the run" are as they were.

### 2026-11-15 — the Git adapter names the commit that added a file

Building `gora changes` (ADR-026 section 9), the first command to read through both adapters.
*Defined adapters*: the Git adapter gains `added_at`, with the walk's batches and its measurement,
and `NotFirstParentError`, a `GitError` whose message is unchanged. (The reader's token: the entry
of 2026-11-18.)

### 2026-11-18 — the GitHub core takes only an installation token

The maintainer decided on 2026-11-16 that GORA never receives a person's token, for a write or a
read (L22 keeps its text). *Defined adapters*: `HttpCore` refuses, before any client is built, a
token that is not the `b64token` of a Bearer token (RFC 6750 section 2.1) and a token that is not
an installation token of a GitHub App (`ghs_`), for the reader and the writer; the empty token
and a token of a user account are the second case, and a non-ASCII token, which httpx2 refused
with a text that held a character of it and its position, the first. The two messages hold no part
of the token. The notes in place say what this holds and what is left to the pipeline step, the
rejected alternative and the sources: "the token of the run" and "on the run's token" are `GORA_GITHUB_TOKEN`
and an installation token.

### 2026-11-24 — the boundary between GORA's App's token and another is left to a step not built

A correction. The note on the reader's token and the entry above placed the boundary between
GORA's App's token and another installation token in a workflow. Nothing holds it yet: the
pipeline step of `build.yml` that is to hold it is not built, and until it is, any installation
token passes, as ADR-022 section 14 decides. Both now say what this holds and what is left to
that step. Nothing the core refuses or accepts changes.

### 2026-11-24 — a host's text or header value that reflects a token is not shown

"A message holds no token" held for GORA's own text, not for what a host sends: a host that
reflected the request's token in the text of an error, or in a rate-limit header, put it on
standard error, and the executor of the round that built the installation-token rule reproduced
it with a fake host. *Defined adapters* gains the rule in the note in place: the host's text, and
each rate-limit header value, is shown only when it holds neither the credential the request sent
nor a string in the shape of that host's tokens, which each `Host` states (`token_shapes`) and
`egress` does not name. The error keeps the operation and the status. A fragment of a token with
no prefix and shorter than the whole is not detected, and that is accepted. ADR-015 takes the
same rule for Slack's `error` and `Retry-After`.

### 2026-11-26 — the pipeline step is the job `changes` of `candidate.yml`

A correction. The note on the reader's token, its correction of 2026-11-24 and the entries of
those dates left the boundary between GORA's App's token and another installation token to a step
of `build.yml` not yet built. The step is the job `changes` of `.github/workflows/candidate.yml`
(ADR-022 section 14, the note of this date), and it is built: it makes the App's token, narrowed
to read, and hands it to `gora changes`. Nothing the core refuses or accepts changes, and the
job's first live run is on `main` after the merge.

### 2026-11-27 — the registry adapter reads a digest and its bundles on GHCR

*Defined adapters* gains the Registry Adapter, built for its two reads: a manifest by digest and
the Sigstore bundles attached to a digest, on GHCR, for public packages, with every answer checked
against its digest. It reads the bundles from the referrers fallback tag, since GHCR answers `404`
to the referrers API (ADR-018, the note of 2026-11-27 under "Consequences"). The redirect bullet
gains its one exception, a blob's redirect to GHCR's storage host, without a token and never
shown; the egress bullet names the adapter's two hosts. Its writes, other registries and a
credential stay decided, not built.

# GORA

## Deterministic GitOps Release Engine

Manages the full release lifecycle from candidate creation through promotion, verification, and
rollback. Policy-driven. Git-backed. Platform-agnostic. Self-releasing.

## Problem

Modern delivery pipelines have build systems and deployment systems, but no authoritative release
contract between them.

Release state is fragmented across pipelines, scripts, and human communication. Teams lack clarity
on what artifact is eligible for promotion, how promotion is controlled, and how rollback is
performed safely.

## How GORA works

```txt
CI produces immutable artifacts
    -> GORA defines release state in Git (manifest)
        -> CD deploys from that state
```

GORA is the release control layer between CI and CD. It is not a build engine and not a cluster
reconciler.

## Core principles

- Git is the source of release state
- Artifacts are immutable (digest-based)
- Promotion moves the same artifact (no rebuild)
- A merge to `main` that changes an artifact's inputs is built once; once GORA registers the
  build, it is a candidate with its own version `X.Y.Z-rc`, which becomes `X.Y.Z` when it
  enters `prod`
- GORA offers every version the stage before verified to every stage a person approves, in
  order; only a person skips one there, and nothing waits for a newer one
- Release is a state, not an event
- Rollback restores a known prior state via Git

## Status

Under active development. The model is decided; what is built and what is not yet is listed in
[release-model section 13](docs/architecture/release-model.md#13-where-gora-is-today). The
milestones are in [ADR-009](docs/adr/0009-mvp-scope-and-milestones.md). What each of GORA's own
stages holds is in `release-manifest.yaml` (`gora status`). CI is the gate every change passes,
on native `linux/amd64` and `linux/arm64`; `make verify` runs the same checks locally
([TESTING.md](TESTING.md)).

## Configuration

GORA reads **only the process environment**. No file is read: not from the working directory,
not from a default location. A secret is set by whoever starts the process, never found by it.

| Variable | Meaning | Default |
| --- | --- | --- |
| `GORA_GITHUB_TOKEN` | GitHub API token; required by `gora promote --open-pr` | unset |
| `GORA_GITHUB_ORG` | GitHub organisation (declared; no command reads it yet) | `zuwarskejes-labs` |
| `GORA_SLACK_ENABLED` | Post release events to Slack; Slack is off unless `true` | `false` |
| `GORA_SLACK_BOT_TOKEN` | Slack bot token used to post notifications | unset |
| `GORA_SLACK_CHANNEL` | Channel id notifications are posted to | unset |
| `GORA_SLACK_APP_TOKEN` | Slack app-level token for incoming commands (declared; not read yet) | unset |
| `GORA_SLACK_SIGNING_SECRET` | Verifies Slack HTTP requests (declared; not read yet) | unset |
| `GORA_MANIFEST_PATH` | Manifest the API reads | `release-manifest.yaml` |
| `GORA_MANIFEST_REPO` | Manifest repository (declared; no command reads it yet) | unset |
| `GORA_LOG_LEVEL` | Log level (declared; no command reads it yet) | `INFO` |
| `GORA_AUDIT_LOG` | Absolute path of the audit journal; empty counts as unset, relative is a usage error | see below |
| `GORA_REVISION` | The source commit the image was built from; the release image sets it at build (`gora version`) | `unknown` |
| `GORA_VERSION` | The version GORA runs as; a deployment sets it, since the artifact does not carry it (L9) | `not set` |

Every mutating command (`cut-rc`, `promote`, `rollback`, `promote --open-pr`) appends one JSON
line per event to the audit journal, and opens it before it changes anything. Its location, in
order: `GORA_AUDIT_LOG`, then `$XDG_STATE_HOME/gora/audit.jsonl` (when set and absolute), then
`$HOME/.local/state/gora/audit.jsonl`. The directory is created `0700` and the file `0600`.
`gora version` and `gora status` never create it. A container with a read-only `HOME` needs
`GORA_AUDIT_LOG` for mutating commands.

Exit codes: `0` ok, `1` refused or failed, `2` usage, `3` completed with an incomplete audit
(the events the journal missed are printed on stderr, one JSON line each). Details:
[ADR-017](docs/adr/0017-audit-journal-and-config-sources.md).

## Documentation

The release model, with its diagrams and laws, is mapped in
[`docs/architecture/release-model.md`](docs/architecture/release-model.md). Architecture
Decision Records are in [`docs/adr/`](docs/adr/). A later ADR amends an earlier one in place,
with an "Amended" line in its header, a note where the text changed and an entry under
"Amendments":

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](docs/adr/0001-vision-and-positioning.md) | Vision and positioning | Accepted, amended |
| [0002](docs/adr/0002-architectural-principles.md) | Architectural principles | Accepted |
| [0003](docs/adr/0003-release-contract-model.md) | Release contract model: the manifest, identifier grammar, schema v0.1 | Accepted, amended |
| [0004](docs/adr/0004-promotion-and-rollback-model.md) | Promotion and rollback model | Accepted, amended |
| [0005](docs/adr/0005-technology-stack.md) | Technology stack | Accepted, amended |
| [0006](docs/adr/0006-tool-adapter-contracts.md) | Tool adapter contracts | Accepted, amended |
| [0007](docs/adr/0007-workflow-execution-model.md) | Workflow execution model | Accepted, amended |
| [0008](docs/adr/0008-repository-structure.md) | Repository structure | Proposed, amended |
| [0009](docs/adr/0009-mvp-scope-and-milestones.md) | MVP scope and milestones | Proposed, amended |
| [0010](docs/adr/0010-agent-architecture.md) | Agent architecture | Proposed |
| [0011](docs/adr/0011-self-release-pipeline.md) | Self-release pipeline: signed OCI image, SBOM, provenance | Proposed, amended |
| [0012](docs/adr/0012-positioning-vs-promotion-executors.md) | Positioning vs promotion executors (Kargo / Argo) | Proposed, amended |
| [0013](docs/adr/0013-promotion-pr-workflow.md) | Promotion PR workflow (GitHub adapter) | Proposed, amended |
| [0014](docs/adr/0014-api-slack-intent-routing.md) | API, Slack and intent routing (M5) | Proposed, amended |
| [0015](docs/adr/0015-slack-connector.md) | Slack connector (notifications and commands) | Proposed, amended |
| [0016](docs/adr/0016-a-gate-must-prove-it-can-fail.md) | A gate must prove it can fail | Accepted, amended |
| [0017](docs/adr/0017-audit-journal-and-config-sources.md) | Audit journal and configuration sources | Accepted, amended |
| [0018](docs/adr/0018-trust-and-promotion-policy.md) | Trust and promotion policy: verification, SBOM, provenance, VSA | Accepted, amended; implementation pending |
| [0019](docs/adr/0019-operating-model.md) | Operating model: GORA owns the release repository; projects connect by contract | Accepted, amended |
| 0020 | Reserved for Project Intelligence / ADR Sync (pinned by ADR-014) | — |
| [0021](docs/adr/0021-platforms-and-native-gates.md) | Platforms: one image index (amd64, arm64), every gate on native hardware | Accepted, implemented, amended |
| [0022](docs/adr/0022-lifecycle-on-trunk.md) | Lifecycle on trunk: a candidate per merge, promotion through gates, platform states | Accepted, amended; implementation pending |
| [0023](docs/adr/0023-command-model-and-stage-loop.md) | Command model and the stage loop: one grammar for CLI, API and Slack; no artifact stalls unseen | Accepted, amended; implementation pending |
| [0024](docs/adr/0024-versions-and-promotion.md) | Versions and promotion: one version per candidate, final in `prod`, eligible versions in order | Accepted; implementation pending |
| [0025](docs/adr/0025-dependencies-and-migrations.md) | No dependencies between artifacts: a migration is an artifact like any other | Accepted |

Workflow catalog: [`docs/catalog/workflow-catalog.md`](docs/catalog/workflow-catalog.md).
Background, never a decision:
[GORA and feature-based delivery](docs/reference/gora-vs-feature-based-delivery.md),
[prior art](docs/reference/prior-art-bom-poc.md).

## License

Private. Not yet licensed for public distribution.

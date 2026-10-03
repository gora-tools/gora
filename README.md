# GORA

## Deterministic GitOps Release Engine

Manages the full release lifecycle from candidate creation through promotion, verification, and rollback. Policy-driven. Git-backed. Platform-agnostic. Self-releasing.

## Problem

Modern delivery pipelines have build systems and deployment systems, but no authoritative release contract between them.

Release state is fragmented across pipelines, scripts, and human communication. Teams lack clarity on what artifact is eligible for promotion, how promotion is controlled, and how rollback is performed safely.

## How GORA works

```txt
CI produces immutable artifacts
    -> GORA defines release state in Git (manifest)
        -> CD deploys from that state
```

GORA is the release control layer between CI and CD. It is not a build engine and not a cluster reconciler.

## Core principles

- Git is the source of release state
- Artifacts are immutable (digest-based)
- Promotion moves the same artifact (no rebuild)
- Only one active candidate exists per release line
- Release is a state, not an event
- Rollback restores a known prior state via Git

## Status

Under active development. The milestone roadmap is [ADR-009](docs/adr/0009-mvp-scope-and-milestones.md); the current release is whatever the newest `v*` tag points at; `make verify` is the gate every change passes, and mirrors CI exactly.

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

Every mutating command (`cut-rc`, `promote`, `rollback`, `promote --open-pr`) appends one JSON
line per event to the audit journal, and opens it before it changes anything. Its location, in
order: `GORA_AUDIT_LOG`, then `$XDG_STATE_HOME/gora/audit.jsonl` (when set and absolute), then
`$HOME/.local/state/gora/audit.jsonl`. The directory is created `0700` and the file `0600`.
`gora version` and `gora status` never create it. A container with a read-only `HOME` needs
`GORA_AUDIT_LOG` for mutating commands.

Exit codes: `0` ok, `1` refused or failed, `2` usage, `3` completed with an incomplete audit
(the events the journal missed are printed on stderr, one JSON line each). Details:
[ADR-0017](docs/adr/0017-audit-journal-and-config-sources.md).

## Documentation

Architecture Decision Records are in [`docs/adr/`](docs/adr/):

| ADR | Decision |
| --- | --- |
| [0001](docs/adr/0001-vision-and-positioning.md) | Vision and positioning |
| [0002](docs/adr/0002-architectural-principles.md) | Architectural principles |
| [0003](docs/adr/0003-release-contract-model.md) | Release contract model (manifest + RC) |
| [0004](docs/adr/0004-promotion-and-rollback-model.md) | Promotion and rollback model |
| [0005](docs/adr/0005-technology-stack.md) | Technology stack |
| [0006](docs/adr/0006-tool-adapter-contracts.md) | Tool adapter contracts |
| [0007](docs/adr/0007-workflow-execution-model.md) | Workflow execution model |
| [0008](docs/adr/0008-repository-structure.md) | Repository structure |
| [0009](docs/adr/0009-mvp-scope-and-milestones.md) | MVP scope and milestones |
| [0010](docs/adr/0010-agent-architecture.md) | Agent architecture |
| [0011](docs/adr/0011-self-release-pipeline.md) | Self-release pipeline (signed OCI: image + SBOM + provenance) |
| [0012](docs/adr/0012-positioning-vs-promotion-executors.md) | Positioning vs promotion executors (Kargo / Argo) |
| [0013](docs/adr/0013-promotion-pr-workflow.md) | Promotion PR workflow (GitHub adapter) |
| [0014](docs/adr/0014-api-slack-intent-routing.md) | API, Slack & intent routing (M5) |
| [0015](docs/adr/0015-slack-connector.md) | Slack connector (notifications & commands) |
| [0016](docs/adr/0016-a-gate-must-prove-it-can-fail.md) | A gate must prove it can fail (falsifiable gates) |
| [0017](docs/adr/0017-audit-journal-and-config-sources.md) | Audit journal and configuration sources |
| [0018](docs/adr/0018-trust-and-promotion-policy.md) | Trust and promotion policy (verification, SBOM, provenance, VSA) |
| [0019](docs/adr/0019-operating-model.md) | Operating model: GORA owns the release repository; projects connect by contract |
| [0021](docs/adr/0021-platforms-and-native-gates.md) | Platforms: one image index (amd64, arm64), every gate on native hardware |

Workflow catalog: [`docs/catalog/workflow-catalog.md`](docs/catalog/workflow-catalog.md)

## License

Private. Not yet licensed for public distribution.

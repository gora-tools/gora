# ADR-014: API, Slack & Intent Routing (Milestone 5)

**Status:** Accepted; implementation pending
**Date:** 2026-07-07
**Amended:** 2026-10-04 — one grammar, resource-oriented API (ADR-023)
**Amended:** 2026-10-08 — what the UI paragraph decides (correction)
**Amended:** 2026-10-08 — accepted (status)
**Amended:** 2026-10-10 — the two services of v0.1 are removed (ADR-026)
**Amended:** 2026-10-16 — Slack's events and status after v0.1 (correction)

---

## Context

Milestone 5 (ADR-009) exposes the release engine beyond the CLI — through a FastAPI HTTP API,
a Slack app, an intent-routing layer, and a read-only visualization UI. Same services, new
transports (ADR-005 chose FastAPI + Slack Bolt; ADR-010 defines the agent/intent stance).
**GitHub and Slack are now production requirements**, so both must be prod-grade tested.

**ADR numbering:** this takes ADR-014. The Project Intelligence / ADR Sync ADR is pinned to
**ADR-020** (a deliberately high, fixed number so it stops floating as milestones claim ADRs).

## Decision

### Principle — transports are thin; services are the truth

CLI, API, and Slack all call the **same services** (`ReleaseService`, `PromotionPRService`).
No release logic lives in a transport. Slack is **interface only, never source of truth**
(ADR-002); Git stays authoritative.

*Amended 2026-10-10 (ADR-026 section 16):* `ReleaseService` and `PromotionPRService` are removed
with manifest v0.1; the services of ADR-023 replace them as they are built.

### FastAPI (HTTP API)

- **Read-only** (powers the UI and Slack status): `GET /status`, `GET /manifest`,
  `GET /history` (from `git log`), `GET /healthz`. Typed responses (pydantic), OpenAPI docs.
- **Mutating** (auth required): `POST /cut-rc`, `POST /promote` (`?open_pr=true`),
  `POST /rollback`.

*Amended 2026-10-04 (ADR-023 sections 1 to 4):* the API is resource-oriented under `/v1`; each
CLI command maps to the methods of one resource, for example:

- `GET /v1/projects/{p}/stages`
- `POST /v1/projects/{p}/stages/{s}/promotions`
- `POST /v1/projects/{p}/artifacts/{a}/versions/{v}:reject`

The read endpoints `/status`, `/manifest` and `/history` are replaced by the stage, artifact and
state reads of ADR-023 section 4, and the verb endpoints above by its writes; `GET /healthz` stays
outside `/v1` as the liveness probe.

### Slack (Bolt) — read/notify first, then commands

Per the ADR-009 build order (read-only before privileged mutations):

- **Phase A — notify + status:** post notifications to a channel on release events (cut-rc,
  promote, promotion-PR opened, rollback); `/gora status` renders manifest state.
- **Phase B — mutating commands behind an approval gate:** `/gora promote <stage>` → intent →
  service → approval → promotion PR. **GORA never merges.**
- **Socket Mode** for the MVP (app-level token; no public HTTP endpoint needed — simplest for
  self-hosted). Signing-secret + HTTP events is the alternative once publicly hosted.
- A **bounded Slack adapter** (ADR-006): narrow ops only (`post_message`, `respond`), audited.

*Amended 2026-10-04 (ADR-023 sections 1, 7 and 8):* `/gora` takes the same words as the CLI.
The service checks who may ask for an action; the approval of a promotion is a review on the
pull request, on the code host, never in Slack.

*Amended 2026-10-16 (correction, ADR-023 section 2, ADR-026 section 2):* `cut-rc` is removed with
manifest v0.1, and registration replaces it; the state that `/gora status` shows is the
manifest, the `state/` tree.

### Intent routing (ADR-010)

Requests (Slack text, API calls) → intent parsing → route to the right service. **Deterministic
code executes; the LLM only interprets intent and never holds privileged execution.** MVP is
structured commands (deterministic parsing, no LLM); LLM-assisted natural-language intent is an
additive layer later, behind the same routing seam.

### UI — read-only visualization

Thin Next.js, a **read-only projection** of Git/API state (ADR-002): renders the artifact
moving through gates/envs. Reads the API; no auth/DB weight. Design inspiration:
`motherduck.com` (colours TBD). Includes **auto release / patch notes** generated from the
manifest + git history. Dogfoods GORA's own release state. A rich/interactive UI is an
Enterprise concern (Tier 2, external POSITIONING note).

*Amended 2026-10-08 (correction):* this ADR decides that the UI is a read-only projection of Git
and the API. Its visual design and the split between editions are not decided here, so "colours
TBD" and the pointer to a note outside the repository carry no decision.

### Auth — done properly from the start

Read endpoints: open on localhost, read-token for remote. Mutating endpoints and Slack
commands: require a token; Slack requests are verified (signing secret / Socket-Mode auth).
Auth is per-adapter (ADR-006) — GitHub App/token, Slack app tokens. No secrets in Git.

### Testing policy — Debian-Policy-level (non-negotiable)

GitHub and Slack are prod requirements → prod-grade tested. This milestone codifies a
`TESTING.md` policy, **as authoritative as an ADR**, in the spirit of Debian Policy: CI parity,
gates reproduce prod, every bug becomes a test, adapters mocked (no network in unit tests),
integration exercised via the runner, the pipeline self-verifies, "release when it's ready."

## Consequences

- M5 exit (ADR-009): release operations are accessible via CLI, API, and Slack.
- The API is the stable seam for the UI and future integrations.
- Transports stay thin; the mechanism core is untouched. The Slack integration is a bounded
  adapter (ADR-006).
- **GitHub and Slack become supported production adapters.**
- A `TESTING.md` policy formalizes the verification standard for the whole project.
- Project Intelligence / ADR Sync is pinned to **ADR-020**.

## Amendments

### 2026-10-04 — one grammar, resource-oriented API

ADR-023 decides the command model: one grammar for the CLI, the API and Slack; a
resource-oriented API under `/v1` with custom methods for actions; approvals as reviews on the
code host. The mutating endpoints and the Slack approval step of this ADR are amended in place.

### 2026-10-08 — what the UI paragraph decides

A correction: the UI paragraph decides a read-only projection of Git and the API; its visual
design and the split between editions are not decisions of this ADR.

### 2026-10-08 — accepted

The decision is accepted: the release model and the ADRs after it build on it. Its status was
Proposed.

### 2026-10-10 — the two services of v0.1 are removed

The pull request that adds GORA's release files removes `ReleaseService` and `PromotionPRService`
with manifest v0.1 (ADR-026 section 16). For this ADR: the principle stands, and the services of
ADR-023 are the ones the transports call as they are built.

### 2026-10-16 — Slack's events and status after v0.1

A correction: #86 removed `cut-rc` and manifest v0.1, and phase A still named `cut-rc` among
Slack's events. A note in place names registration in its place and says that the manifest
`/gora status` shows is the `state/` tree.

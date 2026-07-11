# ADR-014: API, Slack & Intent Routing (Milestone 5)

**Status:** Proposed
**Date:** 2026-07-07

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

### FastAPI (HTTP API)

- **Read-only** (powers the UI and Slack status): `GET /status`, `GET /manifest`,
  `GET /history` (from `git log`), `GET /healthz`. Typed responses (pydantic), OpenAPI docs.
- **Mutating** (auth required): `POST /cut-rc`, `POST /promote` (`?open_pr=true`),
  `POST /rollback`.

### Slack (Bolt) — read/notify first, then commands

Per the ADR-009 build order (read-only before privileged mutations):

- **Phase A — notify + status:** post notifications to a channel on release events (cut-rc,
  promote, promotion-PR opened, rollback); `/gora status` renders manifest state.
- **Phase B — mutating commands behind an approval gate:** `/gora promote <stage>` → intent →
  service → approval → promotion PR. **GORA never merges.**
- **Socket Mode** for the MVP (app-level token; no public HTTP endpoint needed — simplest for
  self-hosted). Signing-secret + HTTP events is the alternative once publicly hosted.
- A **bounded Slack adapter** (ADR-006): narrow ops only (`post_message`, `respond`), audited.

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

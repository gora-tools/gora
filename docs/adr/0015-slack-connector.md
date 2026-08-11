# ADR-015: Slack Connector (notifications & commands)

**Status:** Proposed
**Date:** 2026-07-07

---

## Context

Slack is a production requirement (ADR-014). This ADR details the Slack connector's design and
its logic with GORA — direction, event→message mapping, config/env, and boundaries. It refines
ADR-014's Slack section into a concrete connector contract. See the flow in
`docs/diagrams/slack-flow.mermaid`.

## Decision

### Two directions, two mechanisms

- **Outgoing (notifications) — built first:** a bounded Slack adapter over `httpx`
  (`chat.postMessage`). No `slack-bolt` needed for outgoing.
- **Incoming (commands) — later (M5.2b):** `slack-bolt` in Socket Mode. `/gora status`,
  `/gora promote <stage>`.

### Bounded Slack adapter (ADR-006)

Narrow surface — `post_message(channel, text | blocks) -> ts`. Typed, token from settings,
**every post audited**, no arbitrary Slack API. Reuses the GitHub-adapter pattern (`httpx`,
`httpx.MockTransport` in unit tests, **no network** per the TESTING policy).

### Notifier — event → notification

A notifier posts on release events (**cut-rc, promote, promotion-PR opened, rollback**),
formatted messages. GORA emits the notification **after** the deterministic operation — Git is
the source of truth; Slack merely reflects it (ADR-002). Notifications are informational; Slack
never drives state.

### Config / env (settings, `GORA_` prefix)

`GORA_SLACK_BOT_TOKEN` (`xoxb-`), `GORA_SLACK_APP_TOKEN` (`xapp-`, Socket Mode),
`GORA_SLACK_SIGNING_SECRET`, `GORA_SLACK_CHANNEL` (id), `GORA_SLACK_ENABLED` (feature flag,
**off by default**). Secrets via env / `.env`, never committed. Slack is **optional**:
unconfigured → GORA is CLI-only and the notifier is a no-op (keeps the lightweight ethos).

### Boundaries (ADR-002 / ADR-010)

Slack is **interface only, never source of truth**. Incoming commands (later) go through intent
routing → deterministic services → approval gate. GORA never merges and never executes a
privileged operation directly from a Slack message.

### Connections registry ("gora default: GitHub + Slack")

Slack and GitHub register in the connections registry — configured? healthy (`auth.test` /
`GET /user`)? — surfaced via `gora connections` and `GET /connections`, with an audit journal of
connection events.

### Testing (TESTING policy — Debian-level)

Adapter and notifier are unit-tested with a mocked transport (no network); prod-grade. A real
end-to-end dogfood post uses the operator's tokens.

## Consequences

- Slack becomes a supported production connector (outgoing first, commands later).
- Reuses the bounded-adapter pattern; few dependencies (`httpx` for notify; `slack-bolt` only
  for incoming, later).
- Feature-flagged and optional — the lightweight, cluster-optional ethos holds.
- Diagrams for GORA's design live in `docs/diagrams/*.mermaid` (diagrams-as-code, free, render
  on GitHub).

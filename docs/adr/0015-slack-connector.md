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

### Outgoing messages: plain text, built with Block Kit

A notification is a plain engineering message: no emoji, no Slack markup, every value shown
literally. Release values (version, stage, digest, URL) come from manifests and arguments, so
they are untrusted text. In the earlier mrkdwn message a `<!here>` in a value pinged the
channel, `*x*` rendered bold, `:name:` became an emoji and U+202E reached the channel raw.

**Division of labour.** The notifier (service) is Slack-agnostic. It builds a `Notification`
(`summary`, `detail`, `link`) and passes every value through `gora.text.visible` first: the
rule the CLI uses since PR #40, so one definition covers every output. Invisible
characters (Unicode Cc, Cf, Zl, Zp) become `\xNN` / `\uNNNN` / `\UNNNNNNNN`, a backslash
becomes `\\`. The Slack adapter renders a `Notification` and owns every Slack-specific rule.

**The four summaries** (GORA's own words are ASCII and carry none of `& < > * _ ~` or a
backtick, and no `:name:` shape):

| Event | Summary |
| --- | --- |
| cut-rc | `Cut RC <version>` |
| promote | `Promoted <version> to <stage>` |
| rollback | `Rolled back <stage> to <version>` |
| promotion PR | `Promotion PR for <version> to <stage>` |

`detail` is `Digest <digest>`. `link` is the pull request URL, promotion PR only.

**The payload** (`chat.postMessage`), and nothing else:

```json
{"channel": "C...",
 "text": "<fallback>",
 "mrkdwn": false, "unfurl_links": false, "unfurl_media": false,
 "blocks": [
   {"type": "section", "text": {"type": "plain_text", "text": "<summary>", "emoji": false}},
   {"type": "context", "elements": [
      {"type": "plain_text", "text": "<detail>", "emoji": false},
      {"type": "mrkdwn", "text": "<<url>|Open pull request>", "verbatim": true}]}]}
```

No `parse`, no `link_names`, no header block, no other block types.

**Which field is escaped how, and why.**

| Field | Treatment | Why |
| --- | --- | --- |
| `plain_text` (summary, detail) | sent as is; no `& < >` escaping; `"emoji": false` always sent | markdown is not parsed in `plain_text`, so entities would show up literally; `emoji` is sent explicitly so `:name:` is never turned into an emoji |
| fallback `text` | `summary + ". " + detail` (+ `" " + link`), then `&` `<` `>` as entities, ampersand first | with `blocks`, `text` is the notification and screen-reader fallback; Slack treats `& < >` as control characters there |
| link element (`mrkdwn`) | URL with `& < >` as entities and `\|` as `%7C`; `verbatim: true` | `\|` would end the URL part of `<url\|label>`; the label is a GORA constant |
| non-`https://` link | not made clickable: appended to `detail` as `. Pull request <link>`, no `mrkdwn` element, not repeated in the fallback | only `https://` URLs become links |

Ampersand goes first, so a value that already reads `&lt;` is shown as typed in `plain_text`
and arrives in the fallback as `&amp;lt;`, never as a second-level entity. `unfurl_links` and
`unfurl_media` are off: a URL in a value must not cause Slack to fetch it.

**Limits**, checked before any request: a text object over 3000 characters, or a fallback
over 4000, raises `SlackError("notification exceeds Slack limits")` and no HTTP call is made.
The CLI already treats a notify failure as a best-effort warning. The audit event is
unchanged: `slack.post_message` with `channel` and `ts`.

**Residual.** The fallback `text` (notifications only) cannot disable `:name:` shortcodes, as
`emoji` exists only on `plain_text` objects. A value can contain `:rocket:` and that text can
be rendered as an emoji in the notification there. Values cannot form one once the
identifier grammar lands.

**Enforcement class** (ADR-016 rule 6): values pass through `visible`, plain_text blocks,
`emoji: false`, escape order, field set, limits, GORA constants: `test`
(`tests/unit/test_notifier.py`, `tests/unit/test_slack_adapter.py`,
`tests/integration/test_cli_notify.py`). That Slack renders these as described: `review-only`
(no test reaches Slack, TESTING rule 6).

**Slack sources** (read 2026-10-01):

- Text object: `plain_text` and `mrkdwn`; `emoji` is only usable with `plain_text`; text length
  1 to 3000 characters; `verbatim` for `mrkdwn`:
  <https://docs.slack.dev/reference/block-kit/composition-objects/text-object>
- Escaping: `&`, `<`, `>` are control characters and are converted to `&amp;`, `&lt;`, `&gt;`
  when not meant for parsing; link syntax `<url|text>`:
  <https://docs.slack.dev/messaging/formatting-message-text>
- `chat.postMessage`: with `blocks`, `text` is a fallback string for notifications; `mrkdwn`
  false disables markup parsing; keep `text` under 4000 characters; `unfurl_links` and
  `unfurl_media`: <https://docs.slack.dev/reference/methods/chat.postMessage>

The documentation does not state the default of `emoji` for `plain_text`. The adapter does not
rely on one: the key is always sent.

### Config / env (settings, `GORA_` prefix)

`GORA_SLACK_BOT_TOKEN` (`xoxb-`), `GORA_SLACK_APP_TOKEN` (`xapp-`, Socket Mode),
`GORA_SLACK_SIGNING_SECRET`, `GORA_SLACK_CHANNEL` (id), `GORA_SLACK_ENABLED` (feature flag,
**off by default**). Secrets via the process environment only, never committed. Slack is
**optional**: unconfigured → GORA is CLI-only and the notifier is a no-op (keeps the
lightweight ethos).

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

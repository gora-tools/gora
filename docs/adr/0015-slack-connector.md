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

### Outgoing messages: one rich_text block, styled by structure

A notification is a plain engineering message: no emoji, no Slack markup, every value shown
literally, and the values emphasised. Release values (version, stage, digest, URL) come from
manifests and arguments, so they are untrusted text. In the first mrkdwn message a `<!here>` in
a value pinged the channel, `*x*` rendered bold, `:name:` became an emoji and U+202E reached
the channel raw. Emphasis therefore comes from the `style` field of structured elements, never
from characters in a text: a value cannot turn into markup because nothing parses it.

**Division of labour.** The notifier (service) is Slack-agnostic. It builds a `Notification`
(`summary` and `detail`, each a tuple of `Span(text, kind)` with kind `word`, `value` or
`digest`, plus `link`) and passes every value and digest through `gora.text.visible` first:
the rule the CLI uses since PR #40, so one definition covers every output. Invisible
characters (Unicode Cc, Cf, Zl, Zp) become `\xNN` / `\uNNNN` / `\UNNNNNNNN`, a backslash
becomes `\\`. `Notification.plain()` joins the span texts and feeds the fallback. The Slack
adapter renders a `Notification` and owns every Slack-specific rule.

**The spans, exactly.** Words are GORA constants: ASCII, none of `& < > * _ ~` or a backtick,
and no `:name:` shape (a colon followed by a space is fine).

| Event | Summary spans | Detail spans |
| --- | --- | --- |
| cut-rc | word `Cut RC `, value version | word `Image: `, digest `<image>@<digest>` |
| promote | word `Promoted `, value version, word ` to `, value stage | same |
| rollback | word `Rolled back `, value stage, word ` to `, value version | same, the digest being the one rolled back to |
| promotion PR | word `Promotion PR for `, value version, word ` to `, value stage | same, plus the link |

*Amended 2026-10-02.* The detail is the image line whenever the manifest has an active
candidate: one image per manifest is now an ADR-003 invariant, so the candidate's image is the
image of every digest the manifest records, and `<image>@<digest>` is the reference a puller
would use. Image and digest each pass through `visible` and share one `digest` span (code
style). A manifest without an active candidate names no image, and the detail stays word
`Digest: `, digest digest. The fallback follows the spans: `<summary>. Image: <image>@<digest>`.

**The payload** (`chat.postMessage`), and nothing else:

```json
{"channel": "C...",
 "text": "<fallback>",
 "mrkdwn": false, "unfurl_links": false, "unfurl_media": false,
 "blocks": [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [
   {"type": "text", "text": "Promoted "},
   {"type": "text", "text": "1.2.0", "style": {"bold": true}},
   {"type": "text", "text": " to "},
   {"type": "text", "text": "prod", "style": {"bold": true}},
   {"type": "text", "text": "\n"},
   {"type": "text", "text": "Image: "},
   {"type": "text", "text": "ghcr.io/org/service@sha256:...", "style": {"code": true}}]}]}]}
```

A word has no style, a value `{"bold": true}`, a digest `{"code": true}`. A promotion PR with
an `https://` URL ends with `{"type": "text", "text": "\n"}` and
`{"type": "link", "url": URL, "text": "Open pull request"}`. A span with empty text is omitted:
an element with empty text is never sent. No `section`, `context`, `plain_text`, `mrkdwn`
block text, `emoji` key, `parse`, `link_names` or header block.

**Which field is escaped how, and why.**

| Field | Treatment | Why |
| --- | --- | --- |
| text elements (words, values, digests) | sent as is: no `& < >` entities | a `text` element is a structured field, not parsed as markup, so an entity would show up literally |
| link `url` | sent as is: no entities, no `%7C` | structured field again; the label is a GORA constant |
| fallback `text` | `summary + ". " + detail` (+ `" " + link`), then `&` `<` `>` as entities, ampersand first | with `blocks`, `text` is the notification fallback; Slack treats `& < >` as control characters there |
| non-`https://` link | not made clickable: appended to the detail as word `. Pull request: ` plus a bold value of the link, no `link` element, not repeated in the fallback | only `https://` URLs become links; an empty link counts as no link |

Ampersand goes first, so a value that already reads `&lt;` is sent as typed inside rich_text
and arrives in the fallback as `&amp;lt;`, never as a second-level entity. `mrkdwn` is false
for the fallback, and `unfurl_links` and `unfurl_media` are off: a URL in a value must not
cause Slack to fetch it.

**Limits**, checked before any request: a text element over 3000 characters, or a fallback
over 4000, raises `SlackError("notification exceeds Slack limits")` and no HTTP call is made.
The CLI treats a notify failure as a best-effort warning. The audit event is unchanged:
`slack.post_message` with `channel` and `ts`.

**Why this layout.**

- *Not `plain_text` in a section plus a context block* (the previous layout): nothing could be
  emphasised, and Slack renders a context block small and grey, so the detail line was hard to
  read (operator live check on the first version).
- *Not `mrkdwn`:* it parses values, which is exactly the defect above.
- *The image line came with the manifest invariants* (2026-10-02). Before them `StageState`
  carried no image and a manifest could hold several, so a rollback could not show a proven
  one; with one image per manifest (ADR-003 c) it can.

**Residual.** The fallback `text` (notifications only) cannot disable `:name:` shortcodes: a
value can contain `:rocket:` and that text can be rendered as an emoji in the notification
there. Since the identifier grammar (ADR-003, 2026-10-02) no version, stage, image or digest
can hold that shape (test, `tests/unit/test_identifiers.py`); the pull request URL is the one
value left, and it comes from GitHub's response, not from the operator. Whether Slack keeps `& < >`
and `:name:` literal inside rich_text text elements is `review-only`: no test reaches Slack
(TESTING rule 6). Checked live 2026-10-02 on 820adc7 by the operator in #gora-releases: the
values `probe&<b>*x*:rocket:` and `a&amp;b:+1:` rendered literally (no entity decoding, no
emoji, no markup), values bold, digest code-styled. The class stays `review-only`.

**Enforcement class** (ADR-016 rule 6): values pass through `visible`, the spans, the styles,
the payload keys, escape order, omission of empty text, limits, GORA's words and the image
line: `test` (`tests/unit/test_notifier.py`, `tests/unit/test_slack_adapter.py`,
`tests/integration/test_cli_notify.py`).

**Sources** (read 2026-10-01):

- Rich text block: "the output of the Slack client's WYSIWYG message composer", a
  `rich_text_section` inside `elements`, bold style text nested in a section:
  <https://docs.slack.dev/reference/block-kit/blocks/rich-text-block>
- python-slack-sdk, `RichTextElementParts` in `slack_sdk/models/blocks/block_elements.py`:
  `text` {`text`, `style`}, `link` {`url`, `text`, `style`}, `emoji` its own element type, and a
  style with `bold`, `italic`, `strike`, `code` (and `underline`):
  <https://github.com/slackapi/python-slack-sdk/blob/main/slack_sdk/models/blocks/block_elements.py>
- Escaping: `&`, `<`, `>` are control characters and are converted to `&amp;`, `&lt;`, `&gt;`
  when not meant for parsing: <https://docs.slack.dev/messaging/formatting-message-text>
- `chat.postMessage`: with `blocks`, `text` is a fallback string for notifications; `mrkdwn`
  false disables markup parsing; keep `text` under 4000 characters; `unfurl_links` and
  `unfurl_media`: <https://docs.slack.dev/reference/methods/chat.postMessage>

The Slack pages do not state the text length limit for rich_text text elements. The adapter
applies the 3000-character limit of Slack's text objects to them, as a conservative bound.

### Three states, so a notify that does not happen is visible

A notification that is skipped must not look like one that succeeded. `build_notifier` decides,
from settings, one of three states, and the CLI only prints what the notifier reports. **Not
enabled** (`GORA_SLACK_ENABLED` not true): inactive and silent, the CLI-only default.
**Enabled but incomplete** (`GORA_SLACK_BOT_TOKEN` or `GORA_SLACK_CHANNEL` empty): inactive, and
each mutating command (`cut-rc`, `promote`, `promote --open-pr`, `rollback`) prints once on
stderr `(slack notify skipped: GORA_SLACK_ENABLED is true but <NAMES> empty)`, where `<NAMES>`
lists only the missing variable names, token first, joined with ` and `, never a value.
**Complete**: the notifier posts, and a post that went out is reported on stdout as
`slack: posted to <channel>`, the channel shown through `visible`. A failed post keeps its
existing warning, `(slack notify failed: ...)`, and is not reported as posted. All three status
lines start at column 0. The exit code is unchanged in every state:
notification stays best-effort. Enforcement class (ADR-016 rule 6): `test`
(`tests/unit/test_notifier.py`, `tests/integration/test_cli_notify_states.py`, including a
sentinel check that no token value reaches stdout or stderr).

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
- Feature-flagged and optional — the lightweight ethos holds: the core needs no cluster
  (ADR-019).
- Diagrams for GORA's design live in `docs/diagrams/*.mermaid` (diagrams-as-code, free, render
  on GitHub).

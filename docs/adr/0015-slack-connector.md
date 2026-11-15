# ADR-015: Slack Connector (notifications & commands)

**Status:** Accepted; the notifier and the adapter are built, no command posts yet (ADR-026 section 16); commands pending
**Date:** 2026-07-07
**Amended:** 2026-10-02 — the detail names the image of the digest (ADR-003)
**Amended:** 2026-10-04 — commands take the CLI's words; approvals on the code host (ADR-023)
**Amended:** 2026-10-08 — events of the model; the connections endpoint (ADR-023, ADR-024)
**Amended:** 2026-10-08 — "M5.2b" is part of Milestone 5 (correction)
**Amended:** 2026-10-08 — accepted (status)
**Amended:** 2026-10-09 — the adapter's signature as built (correction)
**Amended:** 2026-10-10 — the commands that posted are removed with v0.1 (ADR-026)
**Amended:** 2026-11-15 — the adapter sends through the shared bounded request (ADR-006)
**Amended:** 2026-11-15 — the HTTP client is httpx2 (ADR-005)

---

## Context

Slack is a production requirement (ADR-014). This ADR details the Slack connector's design and
its logic with GORA — direction, event→message mapping, config/env, and boundaries. It refines
ADR-014's Slack section into a concrete connector contract. See the flow in
`docs/diagrams/slack-flow.mermaid`.

## Decision

### Two directions, two mechanisms

- **Outgoing (notifications) — built first:** a bounded Slack adapter over `httpx2`
  (`chat.postMessage`). No `slack-bolt` needed for outgoing.
- **Incoming (commands) — later (M5.2b):** `slack-bolt` in Socket Mode. `/gora status`,
  `/gora promote <stage>`. *Amended 2026-10-08 (correction):* "M5.2b" is part of Milestone 5
  (ADR-009); the commands take the words of ADR-023.

### Bounded Slack adapter (ADR-006)

Narrow surface — `post_message(channel, text | blocks) -> ts`. Typed, token from settings,
**every post audited**, no arbitrary Slack API. Reuses the GitHub-adapter pattern (`httpx2`,
`httpx2.MockTransport` in unit tests, **no network** per the TESTING policy).

*Amended 2026-10-09 (correction):* as built, the operation is
`post_message(channel, notification) -> SlackMessage`, the posted message's channel and `ts`:
the adapter renders a `Notification`, as "Division of labour" below says, and takes no text or
blocks.

*Amended 2026-11-15 (ADR-006, the bounds of every request):* "bounded" was a name and not a
mechanism: the adapter called `httpx` itself, with a timeout and nothing else. `post_message`
sends `POST /chat.postMessage` through the bounded request that the GitHub reader and the GitHub
writer share, written once in `gora.adapters.egress` (ADR-006): httpx2's timeout, a deadline of
60 s for the whole request, a cap of 64 MiB on the body, `Accept-Encoding: identity` with the raw
bytes counted, a 3xx refused and never followed, no retry, and a cap on a path. The failures are
`SlackError`, every one, and nothing else leaves `post_message` but `SlackError` and the
`ValueError` of a caller's error:

- a transport error, a refused answer (a 3xx, an encoded body, a body over the cap, the
  deadline) and a status of 400 or more are `SlackError`s whose message names the operation, the
  status or the refusal, and never `resp.text`. Slack's own text, the `error` field of a JSON
  body, is shown only when it is printable ASCII of at most 200 characters; for a 429, so is
  `Retry-After`, when it is printable ASCII of at most 64 characters. No other field, header or
  byte of an answer is shown;
- a 200 whose body is not a JSON object, whose `ok` is not a JSON boolean, or whose `channel` or
  `ts`, on `ok: true`, is not a string of printable ASCII of at most 64 characters, is a
  `SlackError` "unexpected response" that names no value of the body, raised before the audit
  event is recorded; `ok: false` is a `SlackError` that shows `error` by the rule above.

Measured on 2026-11-15, on the code before this note, through `SlackAdapter` over a mock
transport: a `301`, a body that is not JSON and a JSON list left the adapter as
`JSONDecodeError` or `AttributeError`; a body with no `ts` as `KeyError`; `{"ok": "no"}` was
returned as a posted message; a `500` whose body was 1,000,000 characters, an escape sequence
among them, was a `SlackError` of 1,000,025 characters, escape sequence included, and an
`ok: false` with an `error` of an escape sequence and 5,000 characters one of 5,035; a transport
error left as `httpx.ConnectError`; and 47.5 KiB of `gzip` that inflate to 50 MB were inflated,
read whole and returned as a posted message. Each is a test (`tests/unit/test_slack_adapter.py`);
the deadline and the cap are shown where they live, in `tests/unit/test_slack_bounds.py`. A unit
test hands the adapter `mock_transport` (`tests/audit_fakes.py`), a `MockTransport` whose answers
are unread streams, as a real transport's are: the request reads a stream, nothing else.

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

*Amended 2026-10-02 (ADR-003):* The detail is the image line whenever the manifest has an active
candidate: one image per manifest is now an ADR-003 invariant, so the candidate's image is the
image of every digest the manifest records, and `<image>@<digest>` is the reference a puller would
use. Image and digest each pass through `visible` and share one `digest` span (code style). A
manifest without an active candidate names no image, and the detail stays word `Digest: `, digest
digest. The fallback follows the spans: `<summary>. Image: <image>@<digest>`.

*Amended 2026-10-08 (ADR-023 section 2, ADR-024 sections 1 and 2):* the events above are those
of the v0.1 CLI. In the model the events are a registration, a promotion pull request opened and
merged, a rollback and a rejection, named by the commands of ADR-023 section 2 (`register`
replaces `cut-rc`); a version is `X.Y.Z-rc` until it enters `prod`, and the "active candidate"
belongs to manifest v0.1. The spans and the payload rules stand for every event.

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

*Amended 2026-10-10 (ADR-026 section 16):* the commands that posted are removed with v0.1, and
with them this integration test and the event `cut_rc`; the notifier keeps its unit tests, and a
command posts again, held by its own tests, from `gora register` on.

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

*Amended 2026-10-10 (ADR-026 section 16):* the commands this section names are removed with v0.1,
and with them this integration test; see the note of 2026-10-10 above.

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

*Amended 2026-10-04 (ADR-023 sections 1, 7 and 8):* `/gora` takes the same words as the CLI
(`/gora -p cally status prod`), and a button calls the HTTP API. The approval gate is a review
on the promotion pull request, on the code host: Slack shows the pull request and links to it,
and never approves.

### Connections registry ("gora default: GitHub + Slack")

Slack and GitHub register in the connections registry — configured? healthy (`auth.test` /
`GET /user`)? — surfaced via `gora connections` and `GET /connections`, with an audit journal of
connection events.

*Amended 2026-10-08 (correction, ADR-023 section 4):* the endpoint is
`GET /v1/projects/{p}/connections`.

### Testing (TESTING policy — Debian-level)

Adapter and notifier are unit-tested with a mocked transport (no network); prod-grade. A real
end-to-end dogfood post uses the operator's tokens.

## Consequences

- Slack becomes a supported production connector (outgoing first, commands later).
- Reuses the bounded-adapter pattern; few dependencies (`httpx2` for notify; `slack-bolt` only
  for incoming, later).
- Feature-flagged and optional — the lightweight ethos holds: the core needs no cluster
  (ADR-019).
- Diagrams for GORA's design live in `docs/diagrams/*.mermaid` (diagrams-as-code, free, render
  on GitHub).

## Amendments

### 2026-10-02 — the detail names the image of the digest

The detail of a notification is `<image>@<digest>` whenever the manifest has an active
candidate, since one image per manifest is an ADR-003 invariant.

### 2026-10-04 — commands take the CLI's words; approvals on the code host

ADR-023 decides one grammar for the CLI, the API and Slack. The incoming commands of this ADR
use it, and an approval is a review on the code host, never a Slack action.

### 2026-10-08 — events of the model; the connections endpoint

ADR-023 and ADR-024 decide the commands and the versions. For this ADR: the notification events
follow the model's commands (`register` replaces `cut-rc`), and the connections endpoint is the
one ADR-023 section 4 names. The spans and the payload rules stand.

### 2026-10-08 — "M5.2b" is part of Milestone 5

A correction: ADR-009 defines no milestone "M5.2b"; incoming commands belong to Milestone 5.

### 2026-10-08 — accepted

The decision is accepted: the release model and the ADRs after it build on it. Its status was
Proposed.

### 2026-10-09 — the adapter's signature as built

A correction: the bounded adapter takes a `Notification` and returns the posted message
(`channel`, `ts`), not `text | blocks`; the rest of the section already describes it.

### 2026-10-10 — the commands that posted are removed with v0.1

The pull request that adds GORA's release files removes the commands that posted, the event
`cut_rc` and the integration tests of the notifications (ADR-026 section 16). The notifier and the
adapter keep their unit tests, and a command posts again, held by its own tests, from `gora
register` on. The status line says that no command posts until `gora register`.

### 2026-11-15 — the adapter sends through the shared bounded request

The adapter called `httpx` with a timeout and nothing else, and a body that was not what Slack
sends left it as a `JSONDecodeError`, an `AttributeError` or a `KeyError`; `ok` was not checked
to be a boolean; `resp.text`, whole, was in a message. `post_message` now sends through the
bounded request every adapter shares (ADR-006, the entry "every request of the three adapters is
bounded"), and every failure is a `SlackError` that shows no text of an answer but Slack's own
`error`, when short and printable. The note under "Bounded Slack adapter" says how, and what was
measured.

### 2026-11-15 — the HTTP client is httpx2

The adapter's client library is `httpx2`, a maintained fork of `httpx` with the same API
(ADR-005, which holds the decision). The lines of the decision that name the library say
`httpx2`; what was measured on `httpx` and the entry above, which says the adapter called `httpx`
with a timeout and nothing else, stay as they were written.

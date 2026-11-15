"""Slack adapter — bounded outgoing notifications (ADR-015).

Exposes a single narrow operation, `post_message`, over the Slack Web API via
`httpx2`. Typed, token from settings, every post audited. No `slack-bolt` (that is
for incoming commands, later) and no arbitrary Slack API access (ADR-002, ADR-006).
The host is `SLACK_API`, a constant; the client is the one `gora.adapters.egress` builds,
which no variable of the environment reaches. The adapter takes no URL and no client, only,
for a test, a transport.

The request is the bounded request every adapter shares (`gora.adapters.egress`, ADR-006): a
deadline, a cap on the body, a cap on a path, `Accept-Encoding: identity` with the raw bytes
counted, a 3xx refused and never followed, no retry, and the host's text shown only when it is
printable ASCII and short. Everything that goes wrong in `post_message` is a `SlackError`: a
transport error, a refused answer, a body that is not what Slack sends, `ok` that is not a JSON
boolean, a `channel` or `ts` that is not safe text, and `ok: false`. A message holds no value
of the body, but Slack's `error` text when it is safe to show, and `Retry-After` for a 429.
The one other error is the caller's `ValueError`.

The adapter renders a `Notification` and owns every Slack-specific rule: one rich_text
block whose emphasis comes from the `style` field of structured elements, never from
characters in a text; entity escaping only where Slack parses `& < >` (the fallback text);
and Slack's size limits.
"""

from typing import Any

import httpx2
from pydantic import BaseModel

from gora.adapters import egress
from gora.audit.logger import AuditLogger
from gora.domain.notification import Notification, Span

SLACK_API = "https://slack.com/api"
POST_MESSAGE = "/chat.postMessage"

# Slack limits: a text element in a block, and the top-level text field.
TEXT_LIMIT = 3000
FALLBACK_LIMIT = 4000

# GORA's own words in a message: plain ASCII, no markup, no emoji-shortcode shape.
LINK_LABEL = "Open pull request"
PULL_REQUEST = ". Pull request: "
FALLBACK_SEPARATOR = ". "
NEWLINE = "\n"

# What a span kind looks like. The style is chosen here, from the kind, and from nothing else.
_STYLE: dict[str, dict[str, bool] | None] = {
    "word": None,
    "value": {"bold": True},
    "digest": {"code": True},
}

NOT_AN_OBJECT = "the body is not a JSON object"
NOT_A_BOOLEAN = "ok is not a JSON boolean"
NOT_SAFE_TEXT = "channel and ts are not printable ASCII of at most 64 characters"


class SlackMessage(BaseModel):
    """A posted Slack message (channel + timestamp id)."""

    channel: str
    ts: str


class SlackError(RuntimeError):
    """A Slack API call failed: a refused request or answer, or a 200 that is not ``ok: true``."""


_SLACK = egress.Host(
    api=SLACK_API,
    error=SlackError,
    text_field="error",
    rate_limits={429: ("retry-after",)},
)


def _entities(text: str) -> str:
    """Slack's entity escaping, ampersand first so the others are not escaped twice."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(span: Span) -> dict[str, Any]:
    element: dict[str, Any] = {"type": "text", "text": span.text}
    style = _STYLE[span.kind]
    if style:
        element["style"] = dict(style)
    return element


def _render(channel: str, notification: Notification) -> dict[str, Any]:
    """The chat.postMessage body for a notification; SlackError when over Slack's limits."""
    detail = notification.detail
    link = notification.link
    https_link = link if link and link.startswith("https://") else None
    if link and https_link is None:
        # Anything but https:// is not made clickable: it is shown as text, in the detail.
        detail = (*detail, Span(PULL_REQUEST, "word"), Span(link, "value"))

    # An empty text is never sent as an element: Slack rejects it.
    summary_spans = [span for span in notification.summary if span.text]
    detail_spans = [span for span in detail if span.text]
    elements = [_text(span) for span in summary_spans]
    elements.append({"type": "text", "text": NEWLINE})
    elements += [_text(span) for span in detail_spans]
    if https_link:
        elements.append({"type": "text", "text": NEWLINE})
        elements.append({"type": "link", "url": https_link, "text": LINK_LABEL})

    # The fallback is the one place Slack parses & < >, so it is the one place they are escaped.
    summary_text = "".join(span.text for span in summary_spans)
    detail_text = "".join(span.text for span in detail_spans)
    fallback = f"{summary_text}{FALLBACK_SEPARATOR}{detail_text}"
    if https_link:
        fallback += f" {https_link}"
    fallback = _entities(fallback)

    too_long = any(len(e["text"]) > TEXT_LIMIT for e in elements if e["type"] == "text")
    if too_long or len(fallback) > FALLBACK_LIMIT:
        raise SlackError("notification exceeds Slack limits")
    return {
        "channel": channel,
        "text": fallback,
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {
                "type": "rich_text",
                "elements": [{"type": "rich_text_section", "elements": elements}],
            }
        ],
    }


def _unexpected(operation: str, reason: str) -> SlackError:
    """An answer that is not what Slack sends, with a reason that holds no value of it."""
    return SlackError(f"{operation} failed: unexpected response ({reason})")


class SlackAdapter:
    """Narrow, audited Slack operation: post a notification message."""

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 10.0,
        audit: AuditLogger | None = None,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        if not token:
            raise ValueError("Slack bot token is required")
        self._audit = audit or AuditLogger()
        self._request = egress.BoundedRequest(
            _SLACK,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    def post_message(self, channel: str, notification: Notification) -> SlackMessage:
        """Post a notification to a channel. Raises SlackError on failure."""
        body = _render(channel, notification)  # limits are checked before any request
        operation = f"POST {POST_MESSAGE}"
        answer = self._request.send("POST", POST_MESSAGE, json=body)
        try:
            data = answer.json()
        except egress.UnexpectedError as exc:
            raise _unexpected(operation, exc.reason) from exc
        if not isinstance(data, dict):
            raise _unexpected(operation, NOT_AN_OBJECT)
        ok = data.get("ok")
        if ok is False:
            # Slack returns HTTP 200 even on logical failure, with {"ok": false, ...}.
            raise SlackError(f"{operation} failed{self._request.host_text(answer)}")
        if ok is not True:
            raise _unexpected(operation, NOT_A_BOOLEAN)
        posted, ts = data.get("channel"), data.get("ts")
        if not (
            isinstance(posted, str)
            and isinstance(ts, str)
            and egress.is_safe(posted, egress.HEADER_LIMIT)
            and egress.is_safe(ts, egress.HEADER_LIMIT)
        ):
            raise _unexpected(operation, NOT_SAFE_TEXT)
        self._audit.record("slack.post_message", channel=channel, ts=ts)
        return SlackMessage(channel=posted, ts=ts)

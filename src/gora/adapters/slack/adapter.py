"""Slack adapter — bounded outgoing notifications (ADR-015).

Exposes a single narrow operation, `post_message`, over the Slack Web API via
`httpx`. Typed, token from settings, every post audited. No `slack-bolt` (that is
for incoming commands, later) and no arbitrary Slack API access (ADR-002, ADR-006).

The adapter renders a `Notification` and owns every Slack-specific rule: one rich_text
block whose emphasis comes from the `style` field of structured elements, never from
characters in a text; entity escaping only where Slack parses `& < >` (the fallback text);
and Slack's size limits.
"""

from typing import Any

import httpx
from pydantic import BaseModel

from gora.audit.logger import AuditLogger
from gora.domain.notification import Notification, Span

SLACK_API = "https://slack.com/api"

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


class SlackMessage(BaseModel):
    """A posted Slack message (channel + timestamp id)."""

    channel: str
    ts: str


class SlackError(RuntimeError):
    """A Slack API call failed (HTTP error, or a 200 with ``ok: false``)."""


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


class SlackAdapter:
    """Narrow, audited Slack operation: post a notification message."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = SLACK_API,
        timeout: float = 10.0,
        audit: AuditLogger | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise ValueError("Slack bot token is required")
        self._base_url = base_url.rstrip("/")
        self._audit = audit or AuditLogger()
        self._client = client or httpx.Client(
            timeout=timeout, headers={"Authorization": f"Bearer {token}"}
        )

    def post_message(self, channel: str, notification: Notification) -> SlackMessage:
        """Post a notification to a channel. Raises SlackError on failure."""
        body = _render(channel, notification)  # limits are checked before any request
        resp = self._client.post(f"{self._base_url}/chat.postMessage", json=body)
        if resp.status_code >= 400:
            raise SlackError(f"chat.postMessage -> {resp.status_code}: {resp.text}")
        # Slack returns HTTP 200 even on logical failure, with {"ok": false, ...}.
        data: dict[str, Any] = resp.json()
        if not data.get("ok"):
            raise SlackError(f"chat.postMessage failed: {data.get('error', 'unknown')}")
        self._audit.record("slack.post_message", channel=channel, ts=data["ts"])
        return SlackMessage(channel=str(data["channel"]), ts=str(data["ts"]))

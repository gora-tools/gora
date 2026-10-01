"""Slack adapter — bounded outgoing notifications (ADR-015).

Exposes a single narrow operation, `post_message`, over the Slack Web API via
`httpx`. Typed, token from settings, every post audited. No `slack-bolt` (that is
for incoming commands, later) and no arbitrary Slack API access (ADR-002, ADR-006).

The adapter renders a `Notification` and owns every Slack-specific rule: plain_text
blocks (markdown is not parsed, emoji is switched off explicitly), entity escaping where
Slack parses `& < >` (the fallback text and the link), and Slack's size limits.
"""

from typing import Any

import httpx
from pydantic import BaseModel

from gora.audit.logger import AuditLogger
from gora.domain.notification import Notification

SLACK_API = "https://slack.com/api"

# Slack limits: a text object in a block, and the top-level text field.
TEXT_LIMIT = 3000
FALLBACK_LIMIT = 4000

# GORA's own words in a message: plain ASCII, no markup, no emoji-shortcode shape.
LINK_LABEL = "Open pull request"
PULL_REQUEST = "Pull request"
FALLBACK_SEPARATOR = ". "


class SlackMessage(BaseModel):
    """A posted Slack message (channel + timestamp id)."""

    channel: str
    ts: str


class SlackError(RuntimeError):
    """A Slack API call failed (HTTP error, or a 200 with ``ok: false``)."""


def _entities(text: str) -> str:
    """Slack's entity escaping, ampersand first so the others are not escaped twice."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plain_text(text: str) -> dict[str, Any]:
    # "emoji" defaults to true on Slack's side, so it is always sent.
    return {"type": "plain_text", "text": text, "emoji": False}


def _render(channel: str, notification: Notification) -> dict[str, Any]:
    """The chat.postMessage body for a notification; SlackError when over Slack's limits."""
    detail = notification.detail
    link = notification.link
    fallback_link = ""
    link_element: dict[str, Any] | None = None
    if link is not None and link.startswith("https://"):
        url = _entities(link).replace("|", "%7C")
        link_element = {"type": "mrkdwn", "text": f"<{url}|{LINK_LABEL}>", "verbatim": True}
        fallback_link = f" {link}"
    elif link is not None:
        # Anything but https:// is not made clickable: it is shown as text.
        detail = f"{detail}{FALLBACK_SEPARATOR}{PULL_REQUEST} {link}"

    fallback = _entities(f"{notification.summary}{FALLBACK_SEPARATOR}{detail}{fallback_link}")
    elements = [_plain_text(detail)] + ([link_element] if link_element else [])
    texts = [notification.summary, detail] + ([link_element["text"]] if link_element else [])
    if len(fallback) > FALLBACK_LIMIT or any(len(text) > TEXT_LIMIT for text in texts):
        raise SlackError("notification exceeds Slack limits")
    return {
        "channel": channel,
        "text": fallback,
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {"type": "section", "text": _plain_text(notification.summary)},
            {"type": "context", "elements": elements},
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

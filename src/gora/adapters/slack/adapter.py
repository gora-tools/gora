"""Slack adapter — bounded outgoing notifications (ADR-015).

Exposes a single narrow operation, `post_message`, over the Slack Web API via
`httpx`. Typed, token from settings, every post audited. No `slack-bolt` (that is
for incoming commands, later) and no arbitrary Slack API access (ADR-002, ADR-006).
"""

from typing import Any

import httpx
from pydantic import BaseModel

from gora.audit.logger import AuditLogger

SLACK_API = "https://slack.com/api"


class SlackMessage(BaseModel):
    """A posted Slack message (channel + timestamp id)."""

    channel: str
    ts: str


class SlackError(RuntimeError):
    """A Slack API call failed (HTTP error, or a 200 with ``ok: false``)."""


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

    def post_message(self, channel: str, text: str) -> SlackMessage:
        """Post a message to a channel. Raises SlackError on failure."""
        resp = self._client.post(
            f"{self._base_url}/chat.postMessage",
            json={"channel": channel, "text": text},
        )
        if resp.status_code >= 400:
            raise SlackError(f"chat.postMessage -> {resp.status_code}: {resp.text}")
        # Slack returns HTTP 200 even on logical failure, with {"ok": false, ...}.
        data: dict[str, Any] = resp.json()
        if not data.get("ok"):
            raise SlackError(f"chat.postMessage failed: {data.get('error', 'unknown')}")
        self._audit.record("slack.post_message", channel=channel, ts=data["ts"])
        return SlackMessage(channel=str(data["channel"]), ts=str(data["ts"]))

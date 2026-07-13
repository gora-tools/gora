"""Unit tests for the bounded Slack adapter (no network — httpx.MockTransport)."""

import json
from typing import Any

import httpx
import pytest

from gora.adapters.slack import SlackAdapter, SlackError, SlackMessage
from gora.audit.logger import AuditLogger


class _CapturingAudit(AuditLogger):
    def __init__(self) -> None:
        super().__init__(actor="test")
        self.events: list[dict[str, Any]] = []

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        event = super().record(action, **details)
        self.events.append(event)
        return event


def _adapter(handler: Any, audit: AuditLogger | None = None) -> SlackAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SlackAdapter("xoxb-test", audit=audit, client=client)


def test_token_required() -> None:
    with pytest.raises(ValueError, match="token is required"):
        SlackAdapter("")


def test_post_message_ok_and_audited() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat.postMessage"
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "channel": "C123", "ts": "1700000000.000100"})

    audit = _CapturingAudit()
    msg = _adapter(handler, audit).post_message("C123", "hello")

    assert isinstance(msg, SlackMessage)
    assert msg.channel == "C123"
    assert msg.ts == "1700000000.000100"
    assert captured == {"channel": "C123", "text": "hello"}
    assert audit.events[0]["action"] == "slack.post_message"
    assert audit.events[0]["details"]["ts"] == "1700000000.000100"


def test_post_message_logical_error_raises() -> None:
    # Slack returns HTTP 200 with {"ok": false} on logical failure.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    with pytest.raises(SlackError, match="channel_not_found"):
        _adapter(handler).post_message("C404", "x")


def test_post_message_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(SlackError, match="500"):
        _adapter(handler).post_message("C1", "x")

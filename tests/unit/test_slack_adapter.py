"""Unit tests for the bounded Slack adapter (no network — httpx.MockTransport)."""

import json
from typing import Any

import httpx
import pytest

from gora.adapters.slack import SlackAdapter, SlackError, SlackMessage
from gora.adapters.slack import adapter as adapter_module
from gora.audit.logger import AuditLogger
from gora.domain.notification import Notification


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


_OK = {"ok": True, "channel": "C123", "ts": "1700000000.000100"}
_NOTE = Notification(summary="Promoted 1.0.0 to dev", detail="Digest sha256:abc")


def _capture(posted: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat.postMessage"
        posted.append(json.loads(request.content))
        return httpx.Response(200, json=_OK)

    return handler


def test_post_message_ok_and_audited() -> None:
    posted: list[dict[str, Any]] = []
    audit = _CapturingAudit()
    msg = _adapter(_capture(posted), audit).post_message("C123", _NOTE)

    assert isinstance(msg, SlackMessage)
    assert msg.channel == "C123"
    assert msg.ts == "1700000000.000100"
    assert len(posted) == 1
    assert audit.events[0]["action"] == "slack.post_message"
    assert audit.events[0]["details"]["ts"] == "1700000000.000100"
    assert audit.events[0]["details"]["channel"] == "C123"


def test_payload_without_a_link_is_exactly_this() -> None:
    posted: list[dict[str, Any]] = []
    _adapter(_capture(posted)).post_message("C123", _NOTE)
    assert posted == [
        {
            "channel": "C123",
            "text": "Promoted 1.0.0 to dev. Digest sha256:abc",
            "mrkdwn": False,
            "unfurl_links": False,
            "unfurl_media": False,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "plain_text", "text": "Promoted 1.0.0 to dev", "emoji": False},
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "plain_text", "text": "Digest sha256:abc", "emoji": False}
                    ],
                },
            ],
        }
    ]


def test_payload_with_a_link_is_exactly_this() -> None:
    posted: list[dict[str, Any]] = []
    note = Notification(
        summary="Promotion PR for 1.0.0 to dev",
        detail="Digest sha256:abc",
        link="https://h/o/r/pull/7",
    )
    _adapter(_capture(posted)).post_message("C123", note)
    assert posted == [
        {
            "channel": "C123",
            "text": "Promotion PR for 1.0.0 to dev. Digest sha256:abc https://h/o/r/pull/7",
            "mrkdwn": False,
            "unfurl_links": False,
            "unfurl_media": False,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "text": "Promotion PR for 1.0.0 to dev",
                        "emoji": False,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "plain_text", "text": "Digest sha256:abc", "emoji": False},
                        {
                            "type": "mrkdwn",
                            "text": "<https://h/o/r/pull/7|Open pull request>",
                            "verbatim": True,
                        },
                    ],
                },
            ],
        }
    ]


def test_the_link_is_entity_escaped_and_the_pipe_is_percent_encoded() -> None:
    posted: list[dict[str, Any]] = []
    note = Notification(summary="s", detail="d", link="https://h/?a=1&b=<2>|x")
    _adapter(_capture(posted)).post_message("C123", note)
    element = posted[0]["blocks"][1]["elements"][1]
    assert element["text"] == "<https://h/?a=1&amp;b=&lt;2&gt;%7Cx|Open pull request>"
    assert posted[0]["text"] == "s. d https://h/?a=1&amp;b=&lt;2&gt;|x"


def test_plain_text_is_not_entity_escaped_but_the_fallback_is() -> None:
    posted: list[dict[str, Any]] = []
    note = Notification(summary="a<b>&c", detail="<!here>")
    _adapter(_capture(posted)).post_message("C123", note)
    section, context = posted[0]["blocks"]
    assert section["text"]["text"] == "a<b>&c"
    assert context["elements"][0]["text"] == "<!here>"
    assert posted[0]["text"] == "a&lt;b&gt;&amp;c. &lt;!here&gt;"


def test_fallback_escapes_the_ampersand_first() -> None:
    posted: list[dict[str, Any]] = []
    _adapter(_capture(posted)).post_message("C123", Notification(summary="&lt;", detail="d"))
    assert posted[0]["blocks"][0]["text"]["text"] == "&lt;"
    assert posted[0]["text"] == "&amp;lt;. d"


@pytest.mark.parametrize("link", ["http://h/p", "javascript:alert(1)", "<!here>", "ftp://h/p"])
def test_a_link_that_is_not_https_is_shown_as_plain_text_in_the_detail(link: str) -> None:
    posted: list[dict[str, Any]] = []
    note = Notification(summary="s", detail="Digest d", link=link)
    _adapter(_capture(posted)).post_message("C123", note)
    context = posted[0]["blocks"][1]
    assert context["elements"] == [
        {"type": "plain_text", "text": f"Digest d. Pull request {link}", "emoji": False}
    ]
    assert "mrkdwn" not in json.dumps(posted[0]["blocks"])


def test_no_parse_link_names_header_or_other_block_types() -> None:
    posted: list[dict[str, Any]] = []
    note = Notification(summary="s", detail="d", link="https://h/p")
    _adapter(_capture(posted)).post_message("C123", note)
    assert set(posted[0]) == {"channel", "text", "mrkdwn", "unfurl_links", "unfurl_media", "blocks"}
    assert [b["type"] for b in posted[0]["blocks"]] == ["section", "context"]


def test_ascii_notification_gives_an_ascii_body() -> None:
    posted: list[dict[str, Any]] = []
    note = Notification(summary="Cut RC 1.0.0", detail="Digest sha256:abc", link="https://h/p")
    _adapter(_capture(posted)).post_message("C123", note)
    assert json.dumps(posted[0], ensure_ascii=False).isascii()


@pytest.mark.parametrize(
    "constant",
    [adapter_module.LINK_LABEL, adapter_module.FALLBACK_SEPARATOR, adapter_module.PULL_REQUEST],
)
def test_adapter_constants_are_ascii_and_carry_no_markup(constant: str) -> None:
    import re

    assert constant.isascii()
    assert not set(constant) & set("&<>*_~`|")
    assert re.search(r":[a-z0-9_+-]+:", constant) is None


# --- limits: checked before any request ---------------------------------------------------


def _count_requests(calls: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_OK)

    return handler


@pytest.mark.parametrize(
    "note",
    [
        Notification(summary="a" * 3001, detail="d"),
        Notification(summary="s", detail="a" * 3001),
        # each part within 3000, the fallback one over 4000: 2000 + ". " + 1999
        Notification(summary="a" * 2000, detail="b" * 1999),
        # raw fallback is 1001 + 2 + 1 = 1004; escaped it is 4004 + 3
        Notification(summary="<" * 1001, detail="d"),
        # the link element, with | becoming %7C, is over 3000 while the fallback is not
        Notification(summary="s", detail="d", link="https://h/" + "|" * 1100),
        # the link element is exactly 3001: 11 + 990 * 3 + 1 + 19
        Notification(summary="s", detail="d", link="https://h/" + "|" * 990 + "a"),
    ],
    ids=["summary", "detail", "fallback", "fallback-after-escaping", "link-element", "link-3001"],
)
def test_over_the_limits_raises_and_sends_nothing(note: Notification) -> None:
    calls: list[httpx.Request] = []
    audit = _CapturingAudit()
    with pytest.raises(SlackError, match="notification exceeds Slack limits"):
        _adapter(_count_requests(calls), audit).post_message("C1", note)
    assert calls == []
    assert audit.events == []


@pytest.mark.parametrize(
    "note",
    [
        Notification(summary="a" * 3000, detail="d"),
        Notification(summary="s", detail="a" * 3000),
        # fallback exactly 4000: 1999 + 2 + 1999
        Notification(summary="a" * 1999, detail="b" * 1999),
        # the link element is exactly 3000: 11 + 990 * 3 + 19
        Notification(summary="s", detail="d", link="https://h/" + "|" * 990),
    ],
    ids=["summary", "detail", "fallback", "link-3000"],
)
def test_at_the_limits_is_sent(note: Notification) -> None:
    calls: list[httpx.Request] = []
    _adapter(_count_requests(calls)).post_message("C1", note)
    assert len(calls) == 1


def test_post_message_logical_error_raises() -> None:
    # Slack returns HTTP 200 with {"ok": false} on logical failure.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    with pytest.raises(SlackError, match="channel_not_found"):
        _adapter(handler).post_message("C404", _NOTE)


def test_post_message_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(SlackError, match="500"):
        _adapter(handler).post_message("C1", _NOTE)

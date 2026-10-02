"""Unit tests for the bounded Slack adapter (no network, httpx.MockTransport)."""

import json
import re
from typing import Any

import httpx
import pytest

from gora.adapters.slack import SlackAdapter, SlackError, SlackMessage
from gora.adapters.slack import adapter as adapter_module
from gora.audit.logger import AuditLogger
from gora.domain.notification import Notification, Span


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
    return SlackAdapter("fake-sentinel-test", audit=audit, client=client)


def test_token_required() -> None:
    with pytest.raises(ValueError, match="token is required"):
        SlackAdapter("")


def _w(text: str) -> Span:
    return Span(text, "word")


def _v(text: str) -> Span:
    return Span(text, "value")


def _d(text: str) -> Span:
    return Span(text, "digest")


_OK = {"ok": True, "channel": "C123", "ts": "1700000000.000100"}
_NOTE = Notification(
    summary=(_w("Promoted "), _v("1.0.0"), _w(" to "), _v("dev")),
    detail=(_w("Digest: "), _d("sha256:abc")),
)
_PR = Notification(
    summary=(_w("Promotion PR for "), _v("1.0.0"), _w(" to "), _v("dev")),
    detail=(_w("Digest: "), _d("sha256:abc")),
    link="https://h/o/r/pull/7",
)


def _capture(posted: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat.postMessage"
        posted.append(json.loads(request.content))
        return httpx.Response(200, json=_OK)

    return handler


def _post(note: Notification) -> dict[str, Any]:
    posted: list[dict[str, Any]] = []
    _adapter(_capture(posted)).post_message("C123", note)
    assert len(posted) == 1
    return posted[0]


def _elements(body: dict[str, Any]) -> list[dict[str, Any]]:
    (block,) = body["blocks"]
    (section,) = block["elements"]
    assert block["type"] == "rich_text"
    assert section["type"] == "rich_text_section"
    return section["elements"]  # type: ignore[no-any-return]


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
    assert _post(_NOTE) == {
        "channel": "C123",
        "text": "Promoted 1.0.0 to dev. Digest: sha256:abc",
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "text", "text": "Promoted "},
                            {"type": "text", "text": "1.0.0", "style": {"bold": True}},
                            {"type": "text", "text": " to "},
                            {"type": "text", "text": "dev", "style": {"bold": True}},
                            {"type": "text", "text": "\n"},
                            {"type": "text", "text": "Digest: "},
                            {"type": "text", "text": "sha256:abc", "style": {"code": True}},
                        ],
                    }
                ],
            }
        ],
    }


def test_payload_with_a_link_is_exactly_this() -> None:
    assert _post(_PR) == {
        "channel": "C123",
        "text": "Promotion PR for 1.0.0 to dev. Digest: sha256:abc https://h/o/r/pull/7",
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "text", "text": "Promotion PR for "},
                            {"type": "text", "text": "1.0.0", "style": {"bold": True}},
                            {"type": "text", "text": " to "},
                            {"type": "text", "text": "dev", "style": {"bold": True}},
                            {"type": "text", "text": "\n"},
                            {"type": "text", "text": "Digest: "},
                            {"type": "text", "text": "sha256:abc", "style": {"code": True}},
                            {"type": "text", "text": "\n"},
                            {
                                "type": "link",
                                "url": "https://h/o/r/pull/7",
                                "text": "Open pull request",
                            },
                        ],
                    }
                ],
            }
        ],
    }


def test_the_link_url_is_sent_as_is_and_only_the_fallback_is_entity_escaped() -> None:
    note = Notification(summary=(_w("s"),), detail=(_w("d"),), link="https://h/?a=1&b=<2>|x")
    body = _post(note)
    assert _elements(body)[-1] == {
        "type": "link",
        "url": "https://h/?a=1&b=<2>|x",  # structured field: no entities, no %7C
        "text": "Open pull request",
    }
    assert body["text"] == "s. d https://h/?a=1&amp;b=&lt;2&gt;|x"


def test_texts_inside_rich_text_are_not_entity_escaped_but_the_fallback_is() -> None:
    note = Notification(summary=(_w("a"), _v("<b>&c")), detail=(_d("<!here>"),))
    body = _post(note)
    assert [e["text"] for e in _elements(body)] == ["a", "<b>&c", "\n", "<!here>"]
    assert body["text"] == "a&lt;b&gt;&amp;c. &lt;!here&gt;"


def test_fallback_escapes_the_ampersand_first() -> None:
    body = _post(Notification(summary=(_v("&lt;"),), detail=(_w("d"),)))
    assert _elements(body)[0]["text"] == "&lt;"
    assert body["text"] == "&amp;lt;. d"


@pytest.mark.parametrize("link", ["http://h/p", "javascript:alert(1)", "<!here>", "ftp://h/p"])
def test_a_link_that_is_not_https_is_appended_to_the_detail_and_never_clickable(link: str) -> None:
    note = Notification(summary=(_w("s"),), detail=(_w("Digest: "), _d("d")), link=link)
    body = _post(note)
    assert _elements(body) == [
        {"type": "text", "text": "s"},
        {"type": "text", "text": "\n"},
        {"type": "text", "text": "Digest: "},
        {"type": "text", "text": "d", "style": {"code": True}},
        {"type": "text", "text": ". Pull request: "},
        {"type": "text", "text": link, "style": {"bold": True}},
    ]
    assert "link" not in {e["type"] for e in _elements(body)}
    # Shown once: in the detail, nothing appended after it.
    shown = link.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    assert body["text"] == f"s. Digest: d. Pull request: {shown}"


def test_an_empty_link_is_no_link() -> None:
    note = Notification(summary=(_w("s"),), detail=(_w("d"),), link="")
    assert (
        _post(note)["blocks"]
        == _post(Notification(summary=(_w("s"),), detail=(_w("d"),)))["blocks"]
    )


def test_a_span_with_empty_text_is_omitted() -> None:
    note = Notification(summary=(_w("Promoted "), _v("")), detail=(_w("Digest: "), _d("")))
    body = _post(note)
    assert _elements(body) == [
        {"type": "text", "text": "Promoted "},
        {"type": "text", "text": "\n"},
        {"type": "text", "text": "Digest: "},
    ]
    assert body["text"] == "Promoted . Digest: "


def test_styles_come_from_the_span_kind_only() -> None:
    body = _post(Notification(summary=(_w("w"), _v("*v*")), detail=(_d("`d`"),)))
    assert _elements(body) == [
        {"type": "text", "text": "w"},
        {"type": "text", "text": "*v*", "style": {"bold": True}},
        {"type": "text", "text": "\n"},
        {"type": "text", "text": "`d`", "style": {"code": True}},
    ]


def test_the_payload_has_these_keys_and_no_old_layout() -> None:
    body = _post(_PR)
    assert set(body) == {"channel", "text", "mrkdwn", "unfurl_links", "unfurl_media", "blocks"}
    assert [b["type"] for b in body["blocks"]] == ["rich_text"]
    dumped = json.dumps(body)
    for gone in ('"section"', '"context"', '"plain_text"', '"mrkdwn", "text"', '"emoji"'):
        assert gone not in dumped
    assert body["mrkdwn"] is False


def test_ascii_notification_gives_an_ascii_body() -> None:
    assert json.dumps(_post(_PR), ensure_ascii=False).isascii()


@pytest.mark.parametrize(
    "constant",
    [adapter_module.LINK_LABEL, adapter_module.FALLBACK_SEPARATOR, adapter_module.PULL_REQUEST],
)
def test_adapter_constants_are_ascii_and_carry_no_markup(constant: str) -> None:
    assert constant.isascii()
    assert not set(constant) & set("&<>*_~`|")
    assert re.search(r":[a-z0-9_+-]+:", constant) is None


# --- limits: checked before any request -----------------------------------------------------


def _count_requests(calls: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_OK)

    return handler


def _n(summary: Span, detail: Span, link: str | None = None) -> Notification:
    return Notification(summary=(summary,), detail=(detail,), link=link)


@pytest.mark.parametrize(
    "note",
    [
        _n(_v("a" * 3001), _w("d")),
        _n(_w("s"), _d("a" * 3001)),
        # each element within 3000, the fallback one over 4000: 2000 + ". " + 1999
        _n(_v("a" * 2000), _w("b" * 1999)),
        # raw fallback is about 1004; escaped it is 4004 + 3
        _n(_v("<" * 1001), _w("d")),
        # an https link is repeated in the fallback: "s. d " + 4001
        _n(_w("s"), _w("d"), "https://h/" + "a" * 3991),
        # a link that is not https becomes a value element of 3001
        _n(_w("s"), _w("d"), "x" * 3001),
    ],
    ids=["summary", "detail", "fallback", "fallback-after-escaping", "https-link", "other-link"],
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
        _n(_v("a" * 3000), _w("d")),
        _n(_w("s"), _d("a" * 3000)),
        # fallback exactly 4000: 1999 + 2 + 1999
        _n(_v("a" * 1999), _w("b" * 1999)),
        # a non-https link as a value element of exactly 3000
        _n(_w("s"), _w("d"), "x" * 3000),
    ],
    ids=["summary", "detail", "fallback", "other-link"],
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

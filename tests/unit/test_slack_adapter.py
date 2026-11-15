"""Unit tests for the bounded Slack adapter (no network, httpx2.MockTransport)."""

import functools
import gzip
import json
import re
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from gora.adapters.slack import SlackAdapter, SlackError, SlackMessage
from gora.adapters.slack import adapter as adapter_module
from gora.audit.logger import AuditLogger
from gora.domain.notification import Notification, Span
from tests.audit_fakes import Chunks, intercept_transport, mock_transport


class _CapturingAudit(AuditLogger):
    def __init__(self) -> None:
        super().__init__(actor="test")
        self.events: list[dict[str, Any]] = []

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        event = super().record(action, **details)
        self.events.append(event)
        return event


def _adapter(handler: Any, audit: AuditLogger | None = None) -> SlackAdapter:
    return SlackAdapter("fake-sentinel-test", audit=audit, transport=mock_transport(handler))


def test_the_client_the_adapter_builds_itself_sends_its_token_and_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transport is not replaced, so the request is the adapter's own, held at the
    transport: nothing leaves the process."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_OK)

    intercept_transport(monkeypatch, answer)

    SlackAdapter("fake-sentinel-test").post_message("C123", _NOTE)
    SlackAdapter("fake-sentinel-test", timeout=2.5).post_message("C123", _NOTE)

    default, given = seen
    assert str(default.url) == f"{adapter_module.SLACK_API}/chat.postMessage"
    assert default.headers["authorization"] == "Bearer fake-sentinel-test"
    assert given.headers["authorization"] == "Bearer fake-sentinel-test"
    assert default.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 10.0
    )
    assert given.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.5)


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
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/chat.postMessage"
        posted.append(json.loads(request.content))
        return httpx2.Response(200, json=_OK)

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


def _count_requests(calls: list[httpx2.Request]) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(200, json=_OK)

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
    calls: list[httpx2.Request] = []
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
    calls: list[httpx2.Request] = []
    _adapter(_count_requests(calls)).post_message("C1", note)
    assert len(calls) == 1


def test_post_message_logical_error_raises() -> None:
    # Slack returns HTTP 200 with {"ok": false} on logical failure.
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"ok": False, "error": "channel_not_found"})

    with pytest.raises(SlackError, match="channel_not_found"):
        _adapter(handler).post_message("C404", _NOTE)


def test_post_message_http_error_raises() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, text="boom")

    with pytest.raises(SlackError, match="500"):
        _adapter(handler).post_message("C1", _NOTE)


# --- the call is bounded: whatever answers, the adapter posts or raises SlackError -----------

OPERATION = "POST /chat.postMessage"
ESCAPE = chr(27)
BELL = chr(7)
NOT_SAFE_TEXT = "channel and ts are not printable ASCII of at most 64 characters"


@functools.cache
def _inflating_body() -> bytes:
    """47 KiB that inflate to 50 MB of an answer Slack could have sent, ``ok`` and all."""
    answer = b'{"ok": true, "channel": "C1", "ts": "1.1", "pad": "' + b"x" * 50_000_000 + b'"}'
    return gzip.compress(answer)


# Every answer that is not a posted message, as Slack, or whatever answers in its place, could
# send it, with the whole message of the SlackError it is: the text of a body is shown only
# where it is Slack's own ``error``, short and printable (below), and no other byte is.
REFUSED: dict[str, tuple[Callable[[], httpx2.Response], str]] = {
    "a-redirect": (
        lambda: httpx2.Response(301, headers={"Location": "https://elsewhere.example/x"}),
        f"{OPERATION} failed: the host answered a redirect (301); no redirect is followed",
    ),
    "a-body-that-is-not-json": (
        lambda: httpx2.Response(200, text="<html>not json</html>"),
        f"{OPERATION} failed: unexpected response (the body is not JSON)",
    ),
    "a-json-list": (
        lambda: httpx2.Response(200, json=["ok"]),
        f"{OPERATION} failed: unexpected response (the body is not a JSON object)",
    ),
    "no-ts": (
        lambda: httpx2.Response(200, json={"ok": True, "channel": "C1"}),
        f"{OPERATION} failed: unexpected response ({NOT_SAFE_TEXT})",
    ),
    "ok-is-a-string": (
        lambda: httpx2.Response(200, json={**_OK, "ok": "no"}),
        f"{OPERATION} failed: unexpected response (ok is not a JSON boolean)",
    ),
    "ok-is-a-number": (
        lambda: httpx2.Response(200, json={**_OK, "ok": 1}),
        f"{OPERATION} failed: unexpected response (ok is not a JSON boolean)",
    ),
    "ok-is-missing": (
        lambda: httpx2.Response(200, json={"channel": "C1", "ts": "1.1"}),
        f"{OPERATION} failed: unexpected response (ok is not a JSON boolean)",
    ),
    "a-500-of-a-million-characters-and-an-escape-sequence": (
        lambda: httpx2.Response(500, text=f"{ESCAPE}[2J" + "x" * 1_000_000),
        f"{OPERATION} -> 500",
    ),
    "ok-false-with-an-escape-sequence-and-5000-characters": (
        lambda: httpx2.Response(
            200, json={"ok": False, "error": f"{ESCAPE}]0;pwned{BELL}" + "x" * 5000}
        ),
        f"{OPERATION} failed",
    ),
    "ok-false-with-no-error": (
        lambda: httpx2.Response(200, json={"ok": False}),
        f"{OPERATION} failed",
    ),
    "a-gzip-body-of-47-kib-that-inflates-to-50-mb": (
        lambda: httpx2.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=Chunks([_inflating_body()])
        ),
        f"{OPERATION} failed: the body is encoded",
    ),
}


@pytest.mark.parametrize("case", list(REFUSED))
def test_an_answer_that_is_not_a_posted_message_is_a_slack_error_and_is_not_audited(
    case: str,
) -> None:
    make, message = REFUSED[case]
    audit = _CapturingAudit()

    with pytest.raises(SlackError) as info:
        _adapter(lambda request: make(), audit).post_message("C1", _NOTE)

    assert str(info.value) == message
    assert audit.events == []


@pytest.mark.parametrize(
    "error",
    [httpx2.ConnectError, httpx2.ReadTimeout, httpx2.RemoteProtocolError, httpx2.TooManyRedirects],
    ids=lambda e: e.__name__,
)
def test_a_transport_error_is_a_slack_error_that_names_its_type_only(
    error: type[httpx2.RequestError],
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error(f"{request.url} {request.headers['authorization']}", request=request)

    audit = _CapturingAudit()
    with pytest.raises(SlackError) as info:
        _adapter(handler, audit).post_message("C1", _NOTE)

    assert str(info.value) == f"{OPERATION} failed: {error.__name__}"
    assert isinstance(info.value.__cause__, httpx2.HTTPError)
    assert "fake-sentinel-test" in str(info.value.__cause__)  # calibration: the text had it
    assert audit.events == []


def test_a_response_read_before_the_adapter_sees_it_is_a_slack_error_not_a_body() -> None:
    """A transport returns an unread stream; a response built with ``json=`` has been read, and
    the request reads a stream, nothing else."""
    adapter = SlackAdapter(
        "fake-sentinel-test",
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200, json=_OK)),
    )

    with pytest.raises(SlackError) as info:
        adapter.post_message("C1", _NOTE)

    assert str(info.value) == f"{OPERATION} failed: StreamConsumed"
    assert isinstance(info.value.__cause__, httpx2.StreamError)


def test_the_request_asks_for_an_identity_body_and_the_token_goes_in_the_header() -> None:
    calls: list[httpx2.Request] = []

    _adapter(_count_requests(calls)).post_message("C1", _NOTE)

    (request,) = calls
    assert request.headers["accept-encoding"] == "identity"
    assert request.headers["authorization"] == "Bearer fake-sentinel-test"
    assert request.method == "POST"


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        ("channel_not_found", ": channel_not_found"),
        ("not in channel", ": not in channel"),
        ("x" * 200, f": {'x' * 200}"),  # calibration: the limit refuses one more, and only that
        ("x" * 201, ""),
        (f"{ESCAPE}]0;pwned{BELL}", ""),
        ("two\nlines", ""),
        ("a\ttab", ""),
        ("del" + chr(127), ""),
        ("caf" + chr(0xE9), ""),
        ("", ""),
        (["channel_not_found"], ""),
        (7, ""),
    ],
    ids=[
        "a-code",
        "words",
        "exactly-200",
        "201",
        "an-escape-sequence",
        "a-new-line",
        "a-tab",
        "a-delete",
        "not-ascii",
        "empty",
        "a-list",
        "a-number",
    ],
)
def test_slack_s_error_is_shown_when_it_is_short_and_printable_and_nothing_else_of_the_body_is(
    error: object, shown: str
) -> None:
    body = {"ok": False, "error": error, "response_metadata": {"messages": ["not shown"]}}

    with pytest.raises(SlackError) as info:
        _adapter(lambda request: httpx2.Response(200, json=body)).post_message("C1", _NOTE)

    assert str(info.value) == f"{OPERATION} failed{shown}"


def test_an_error_status_shows_slack_s_error_too() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, json={"ok": False, "error": "fatal_error"})

    with pytest.raises(SlackError) as info:
        _adapter(handler).post_message("C1", _NOTE)

    assert str(info.value) == f"{OPERATION} -> 500: fatal_error"


@pytest.mark.parametrize(
    ("headers", "shown"),
    [
        ({"Retry-After": "30"}, " (retry-after: 30)"),
        ({}, ""),
        ({"Retry-After": ESCAPE + "[2J"}, ""),
        ({"Retry-After": "9" * 65}, ""),
        ({"Retry-After": ""}, ""),
        ({"Retry-After": "9" * 64}, f" (retry-after: {'9' * 64})"),
    ],
    ids=["retry-after", "none", "an-escape-sequence", "65-characters", "empty", "64-characters"],
)
def test_a_429_names_retry_after_when_it_is_safe_to_show(
    headers: dict[str, str], shown: str
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, headers=headers, json={"ok": False, "error": "ratelimited"})

    with pytest.raises(SlackError) as info:
        _adapter(handler).post_message("C1", _NOTE)

    assert str(info.value) == f"{OPERATION} -> 429: ratelimited{shown}"


@pytest.mark.parametrize("status", [400, 403, 500, 503])
def test_another_error_status_shows_no_header(status: int) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        headers = {"Retry-After": "30", "X-RateLimit-Reset": "1794678624"}
        return httpx2.Response(status, headers=headers, json={"ok": False, "error": "boom"})

    with pytest.raises(SlackError) as info:
        _adapter(handler).post_message("C1", _NOTE)

    assert str(info.value) == f"{OPERATION} -> {status}: boom"


@pytest.mark.parametrize("status", [429, 500, 502])
def test_nothing_is_retried(status: int) -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(status, headers={"Retry-After": "1"})

    with pytest.raises(SlackError):
        _adapter(handler).post_message("C1", _NOTE)

    assert len(calls) == 1


def test_a_transport_error_is_not_retried_either() -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        raise httpx2.ConnectError("unreachable", request=request)

    with pytest.raises(SlackError):
        _adapter(handler).post_message("C1", _NOTE)

    assert len(calls) == 1


@pytest.mark.parametrize("field", ["channel", "ts"])
@pytest.mark.parametrize(
    "value",
    [
        f"C{ESCAPE}[2J",
        "C" * 65,
        "",
        "C\n1",
        "C" + chr(0xE9),
        7,
        None,
        ["C1"],
        True,
    ],
    ids=[
        "an-escape-sequence",
        "65-characters",
        "empty",
        "a-new-line",
        "not-ascii",
        "a-number",
        "null",
        "a-list",
        "a-boolean",
    ],
)
def test_a_channel_or_ts_that_is_not_safe_text_is_refused_and_not_audited(
    field: str, value: object
) -> None:
    audit = _CapturingAudit()

    with pytest.raises(SlackError) as info:
        _adapter(
            lambda request: httpx2.Response(200, json={**_OK, field: value}), audit
        ).post_message("C1", _NOTE)

    assert str(info.value) == f"{OPERATION} failed: unexpected response ({NOT_SAFE_TEXT})"
    assert audit.events == []


def test_a_channel_and_a_ts_of_exactly_64_characters_are_posted() -> None:
    """Calibration: the limit refuses one more character, and only that."""
    audit = _CapturingAudit()
    body = {"ok": True, "channel": "C" * 64, "ts": "1" * 64}

    msg = _adapter(lambda request: httpx2.Response(200, json=body), audit).post_message("C1", _NOTE)

    assert msg == SlackMessage(channel="C" * 64, ts="1" * 64)
    assert audit.events[0]["details"] == {"channel": "C1", "ts": "1" * 64}


def test_fields_of_the_answer_that_the_adapter_does_not_read_are_not_read() -> None:
    body = {**_OK, "message": {"text": "x" * 1000}, "warning": "missing_charset", "ok": True}

    msg = _adapter(lambda request: httpx2.Response(200, json=body)).post_message("C1", _NOTE)

    assert msg == SlackMessage(channel="C123", ts="1700000000.000100")

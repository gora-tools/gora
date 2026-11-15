"""The bounds of the Slack call, shown where they live: ``gora.adapters.egress`` (ADR-006,
ADR-015).

The deadline and the cap on a body are checked against a clock and a cap the tests replace in
``egress``, so none of them can be red on the code before the change, which has neither. Each is
proven with a defect injected in ``egress`` (ADR-016 rule 1), and the same bounds of the GitHub
adapters are in ``test_github_reader.py``, the one request they all go through. The answers are
the unread streams of a transport, as ``tests/audit_fakes.py`` makes them.
"""

from collections.abc import Callable

import httpx2
import pytest

from gora.adapters import egress
from gora.adapters.slack import SlackAdapter, SlackError
from gora.domain.notification import Notification, Span
from tests.audit_fakes import Chunks, mock_transport
from tests.unit.test_github_reader import EMPTY_DEFLATE_BLOCK, Clock, pulled_at_most

OPERATION = "POST /chat.postMessage"
NOTE = Notification(summary=(Span("Promoted ", "word"),), detail=(Span("Digest: ", "word"),))
OK = b'{"ok": true, "channel": "C1", "ts": "1.1"}'


def adapter_on(answer: Callable[[], httpx2.Response]) -> SlackAdapter:
    return SlackAdapter("fake-sentinel-test", transport=mock_transport(lambda request: answer()))


def test_a_body_over_the_cap_is_refused_never_read_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request stops at the chunk that crosses the cap: the one after it is never pulled,
    so one that reads the whole body first and counts it once afterwards is red."""
    monkeypatch.setattr(egress, "MAX_BODY", 1000)
    chunks = [b" " * 600, b" " * 600, b"x"]

    def answer() -> httpx2.Response:
        return httpx2.Response(200, stream=Chunks(chunks, pulled_at_most(2)))

    with pytest.raises(SlackError) as info:
        adapter_on(answer).post_message("C1", NOTE)

    assert str(info.value) == f"{OPERATION} failed: the body is over the cap of 1000 bytes"


def test_a_body_the_host_declares_over_the_cap_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress, "MAX_BODY", 1000)

    def never() -> None:
        raise AssertionError("the body was read")

    def answer() -> httpx2.Response:
        headers = {"Content-Length": "1001"}
        return httpx2.Response(200, headers=headers, stream=Chunks([OK], never))

    with pytest.raises(SlackError, match="over the cap of 1000 bytes"):
        adapter_on(answer).post_message("C1", NOTE)


def test_a_body_of_exactly_the_cap_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calibration: the cap refuses one byte more, and only that."""
    monkeypatch.setattr(egress, "MAX_BODY", 1000)
    body = OK + b" " * (1000 - len(OK))

    def answer() -> httpx2.Response:
        return httpx2.Response(200, stream=Chunks([body[:500], body[500:]]))

    assert adapter_on(answer).post_message("C1", NOTE).ts == "1.1"


def test_the_cap_counts_the_bytes_that_arrive_not_the_bytes_they_decode_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream of chunks that decode to nothing is refused by the cap on what is sent."""
    monkeypatch.setattr(egress, "MAX_BODY", 1000)
    chunks = [EMPTY_DEFLATE_BLOCK] * 201

    def answer() -> httpx2.Response:
        return httpx2.Response(200, stream=Chunks(chunks))

    with pytest.raises(SlackError, match="over the cap of 1000 bytes"):
        adapter_on(answer).post_message("C1", NOTE)


def test_an_answer_that_arrives_too_slowly_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request stops at the chunk at which the clock passes the deadline: the one after it
    is never pulled, so one that reads the whole body first and checks the clock once afterwards
    is red."""
    clock = Clock(monkeypatch)
    slowly = [b'{"ok": ', b"true, ", b'"channel": "C1"', b"x"]
    guard, move = pulled_at_most(3), clock.advance(30)

    def each() -> None:
        guard()
        move()

    def answer() -> httpx2.Response:
        return httpx2.Response(200, stream=Chunks(slowly, each))

    with pytest.raises(SlackError) as info:
        adapter_on(answer).post_message("C1", NOTE)

    assert str(info.value) == f"{OPERATION} failed: no complete answer within 60 s"


def test_an_answer_that_ends_at_the_deadline_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calibration: the deadline refuses what is past it, and only that."""
    clock = Clock(monkeypatch)

    def answer() -> httpx2.Response:
        return httpx2.Response(200, stream=Chunks([OK[:20], OK[20:]], clock.advance(30)))

    assert adapter_on(answer).post_message("C1", NOTE).ts == "1.1"


def test_headers_that_arrive_too_late_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(monkeypatch)

    def answer() -> httpx2.Response:
        clock.advance(61)()
        return httpx2.Response(200, stream=Chunks([OK]))

    with pytest.raises(SlackError, match="no complete answer within 60 s"):
        adapter_on(answer).post_message("C1", NOTE)

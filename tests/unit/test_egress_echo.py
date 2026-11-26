"""A host that reflects a token in its answer puts nothing of it on GORA's terminal (ADR-006,
ADR-015).

``BoundedRequest`` shows the host's text of an error answer (``message`` for GitHub, ``error``
for Slack) and the values of the rate-limit headers a ``Host`` names, each only when it holds
neither the credential of the ``Authorization`` header the request sent nor a string in the shape
of that host's tokens. What is left out is the value alone: the error still names the operation
and the status. An ordinary text ("Not Found", "invalid_auth") is shown as before.

The test tokens never match a rule of the secrets scan. A GitHub-shaped value has a ``-`` right
after its prefix and no run of 20 or more letters, digits or ``_``. A Slack-shaped value is built
here from short parts, never written whole, and once assembled it has fewer than 10 letters,
digits or ``-`` after its prefix; in a host's text it ends the text, so no output shows a value
the scan reads. The credential Slack's tests send has no known shape: the check of the
credential, not of the shapes, is what hides it.
"""

from collections.abc import Callable

import httpx2
import pytest

from gora.adapters import egress
from gora.adapters.github import GitHubError
from gora.adapters.github._http import HttpCore
from gora.adapters.slack import SlackAdapter, SlackError
from gora.domain.notification import Notification, Span
from tests.audit_fakes import mock_transport

Handler = Callable[[httpx2.Request], httpx2.Response]

NOTE = Notification(summary=(Span("Promoted", "word"),), detail=(Span("a digest", "word"),))

GITHUB_SENT = "ghs_-echo-1"  # the credential a GitHub request sends: an installation token
SLACK_SENT = "echo-cred-1"  # the one Slack's tests send: no prefix, no shape of Slack's

# Another token of each shape of GitHub's, and one in another case.
GITHUB_ELSE = {
    "the-sent-credential": GITHUB_SENT,
    "classic": "ghp_-echo-2",
    "fine-grained": "github_pat_-echo-3",
    "installation": "ghs_-echo-4",
    "oauth": "gho_-echo-5",
    "upper-case": "GHP_-echo-6",
}

# Slack's, built at run time from short parts: a bot's, a user's and an app-level token, and
# one in another case. Each has "e1" after its prefix, which the scan does not read.
SLACK_ELSE = {
    "bot": "xox" + "b" + "-" + "e1",
    "user": "xox" + "p" + "-" + "e1",
    "app-level": "xapp" + "-" + "e1",
    "upper-case": "XOX" + "B" + "-" + "e1",
    "app-level-upper-case": "XAPP" + "-" + "e1",
}


def _answer(
    status: int, body: dict[str, object] | None = None, headers: dict[str, str] | None = None
) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=body, headers=headers)

    return handler


def _github(handler: Handler) -> str:
    """The message of the error a GitHub request that ``handler`` answers raises."""
    core = HttpCore(GITHUB_SENT, timeout=5.0, transport=mock_transport(handler))
    with pytest.raises(GitHubError) as info:
        core.send("GET", "/x")
    return str(info.value)


def _slack(handler: Handler) -> str:
    """The message of the error posting a notification raises when ``handler`` answers."""
    adapter = SlackAdapter(SLACK_SENT, transport=mock_transport(handler))
    with pytest.raises(SlackError) as info:
        adapter.post_message("C123", NOTE)
    return str(info.value)


# --- GitHub's text ----------------------------------------------------------------------------


@pytest.mark.parametrize("token", GITHUB_ELSE.values(), ids=list(GITHUB_ELSE))
def test_a_github_text_that_holds_a_token_is_not_shown(token: str) -> None:
    for text in (token, f"Bad credentials: {token}", f"{token} is not valid"):
        message = _github(_answer(401, {"message": text}))
        assert message == "GET /x -> 401"  # the operation and the status, none of the text
        assert token not in message


@pytest.mark.parametrize("text", ["Not Found", "Bad credentials", "Resource not accessible"])
def test_an_ordinary_github_text_is_still_shown(text: str) -> None:
    """Calibration: the tests above are red only because of the token, not because a text is
    never shown."""
    assert _github(_answer(404, {"message": text})) == f"GET /x -> 404: {text}"


@pytest.mark.parametrize("token", GITHUB_ELSE.values(), ids=list(GITHUB_ELSE))
@pytest.mark.parametrize("name", ["x-ratelimit-reset", "retry-after"])
def test_a_github_rate_limit_value_that_holds_a_token_is_left_out(token: str, name: str) -> None:
    other = "retry-after" if name == "x-ratelimit-reset" else "x-ratelimit-reset"
    headers = {name: token, other: "30"}

    message = _github(_answer(429, {"message": "rate limited"}, headers))

    assert message == f"GET /x -> 429: rate limited ({other}: 30)"
    assert token not in message


def test_ordinary_github_rate_limit_values_are_still_shown() -> None:
    headers = {"x-ratelimit-reset": "1700000000", "retry-after": "30"}
    assert _github(_answer(403, {"message": "limit"}, headers)) == (
        "GET /x -> 403: limit (x-ratelimit-reset: 1700000000, retry-after: 30)"
    )


# --- Slack's text -----------------------------------------------------------------------------


def test_a_slack_error_that_holds_the_credential_sent_is_not_shown() -> None:
    """The credential has no shape Slack's: only the check of what the request sent hides it."""
    for text in (SLACK_SENT, f"invalid {SLACK_SENT}"):
        message = _slack(_answer(200, {"ok": False, "error": text}))
        assert message == "POST /chat.postMessage failed"
        assert SLACK_SENT not in message


@pytest.mark.parametrize("token", SLACK_ELSE.values(), ids=list(SLACK_ELSE))
def test_a_slack_error_that_holds_a_token_of_a_shape_is_not_shown(token: str) -> None:
    for text in (token, f"invalid {token}"):
        message = _slack(_answer(200, {"ok": False, "error": text}))
        assert message == "POST /chat.postMessage failed"
        assert token not in message
    status = _slack(_answer(500, {"error": f"invalid {token}"}))
    assert status == "POST /chat.postMessage -> 500"


@pytest.mark.parametrize("text", ["invalid_auth", "channel_not_found", "token_revoked"])
def test_an_ordinary_slack_error_is_still_shown(text: str) -> None:
    """Calibration. ``token_revoked`` names a token and holds none: it is shown."""
    assert _slack(_answer(200, {"ok": False, "error": text})) == (
        f"POST /chat.postMessage failed: {text}"
    )
    assert _slack(_answer(500, {"error": text})) == f"POST /chat.postMessage -> 500: {text}"


@pytest.mark.parametrize("value", [SLACK_SENT, *SLACK_ELSE.values()], ids=["sent", *SLACK_ELSE])
def test_a_slack_retry_after_that_holds_a_token_is_left_out(value: str) -> None:
    message = _slack(_answer(429, {"error": "ratelimited"}, {"retry-after": value}))
    assert message == "POST /chat.postMessage -> 429: ratelimited"
    assert value not in message


def test_an_ordinary_slack_retry_after_is_still_shown() -> None:
    message = _slack(_answer(429, {"error": "ratelimited"}, {"retry-after": "30"}))
    assert message == "POST /chat.postMessage -> 429: ratelimited (retry-after: 30)"


# --- the credential, whatever the host's shapes ----------------------------------------------


def _request(host: egress.Host, headers: dict[str, str], body: dict[str, object]) -> str:
    request = egress.BoundedRequest(
        host, headers=headers, timeout=5.0, transport=mock_transport(_answer(400, body))
    )
    with pytest.raises(RuntimeError) as info:
        request.send("GET", "/x")
    return str(info.value)


NO_SHAPES = egress.Host("https://host.example", RuntimeError, "error", {}, ())
SHAPED = egress.Host("https://host.example", RuntimeError, "error", {}, (r"tk[a-z]-",))


@pytest.mark.parametrize("name", ["Authorization", "authorization", "AUTHORIZATION"])
def test_the_credential_is_the_part_after_the_scheme_whatever_the_header_s_case(name: str) -> None:
    headers = {name: f"Bearer {SLACK_SENT}"}

    assert _request(NO_SHAPES, headers, {"error": f"it is {SLACK_SENT}"}) == "GET /x -> 400"
    # The scheme is not the credential: the word is not hidden, and a text with it is shown.
    assert _request(NO_SHAPES, headers, {"error": "Bearer of news"}) == (
        "GET /x -> 400: Bearer of news"
    )


def test_a_value_with_no_scheme_is_the_credential_whole() -> None:
    headers = {"Authorization": SLACK_SENT}
    assert _request(NO_SHAPES, headers, {"error": f"it is {SLACK_SENT}"}) == "GET /x -> 400"


@pytest.mark.parametrize("value", ["Bearer ", "Bearer", ""])
def test_an_empty_credential_hides_nothing(value: str) -> None:
    """An empty string is in every text: it must not be a credential."""
    assert _request(NO_SHAPES, {"Authorization": value}, {"error": "a text"}) == (
        "GET /x -> 400: a text"
    )


def test_a_request_with_no_authorization_header_has_no_credential_check_but_the_shapes_apply() -> (
    None
):
    headers = {"Accept": "application/json"}

    assert _request(NO_SHAPES, headers, {"error": SLACK_SENT}) == f"GET /x -> 400: {SLACK_SENT}"
    assert _request(SHAPED, headers, {"error": "it is tkb-1"}) == "GET /x -> 400"
    assert _request(SHAPED, headers, {"error": "it is TKB-1"}) == "GET /x -> 400"  # any case
    assert _request(SHAPED, headers, {"error": "it is tk-1"}) == "GET /x -> 400: it is tk-1"


def test_no_other_header_is_a_credential() -> None:
    headers = {"Authorization": "Bearer other-1", "X-Other": SLACK_SENT, "Accept": "x"}
    assert _request(NO_SHAPES, headers, {"error": SLACK_SENT}) == f"GET /x -> 400: {SLACK_SENT}"


def test_no_repr_shows_the_credential() -> None:
    request = egress.BoundedRequest(
        SHAPED, headers={"Authorization": f"Bearer {SLACK_SENT}"}, timeout=5.0
    )
    assert SLACK_SENT not in repr(request) and SLACK_SENT not in repr(SHAPED)
    assert SLACK_SENT not in str(request)

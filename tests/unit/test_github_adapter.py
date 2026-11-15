"""Unit tests for the bounded GitHub adapter (no network — httpx2.MockTransport)."""

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from gora.adapters.github import GitHubAdapter, GitHubError, PullRequest
from gora.adapters.github._http import GITHUB_API
from gora.audit.logger import AuditLogger
from gora.domain.release import identifiers
from tests.audit_fakes import intercept_transport, mock_transport
from tests.unit.test_github_reader import DEEP, replay
from tests.unit.test_identifiers import INVALID_REPOS

REPO = "owner/repo"

_PULL_REQUEST = {
    "number": 7,
    "html_url": "u",
    "state": "open",
    "head": {"ref": "feature"},
    "base": {"ref": "main"},
    "title": "t",
}


class _CapturingAudit(AuditLogger):
    def __init__(self) -> None:
        super().__init__(actor="test")
        self.events: list[dict[str, Any]] = []

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        event = super().record(action, **details)
        self.events.append(event)
        return event


def _adapter(handler: Any, audit: AuditLogger | None = None) -> GitHubAdapter:
    return GitHubAdapter(REPO, "test-token", audit=audit, transport=mock_transport(handler))


def test_token_required() -> None:
    with pytest.raises(ValueError, match="token is required"):
        GitHubAdapter(REPO, "")


@pytest.mark.parametrize("repo", [*INVALID_REPOS, "o/r?sentinel-7f3a"])
def test_a_repository_the_grammar_refuses_is_refused_at_construction(repo: str) -> None:
    """The repository is placed in every URL path unencoded; the adapter is where it is checked."""
    with pytest.raises(ValueError) as info:
        GitHubAdapter(repo, "test-token")

    # One of the grammar's constant rules, so it holds no value; the sentinel is in none.
    rules = {identifiers.REPO.rule, identifiers.REPO_DOT_NAME}
    assert str(info.value) in {f"GitHub repository {rule}" for rule in rules}
    assert "sentinel-7f3a" not in str(info.value)


def _seen_by_the_transport(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.Request]:
    """Every request any client sends, as the transport receives it, answered with a pull
    request."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_PULL_REQUEST)

    intercept_transport(monkeypatch, answer)
    return seen


def test_the_client_the_adapter_builds_itself_sends_its_headers_and_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transport is not replaced, so the request is the adapter's own, held at the
    transport: nothing leaves the process."""
    seen = _seen_by_the_transport(monkeypatch)

    assert GitHubAdapter(REPO, "test-token").get_pull_request_status(7).number == 7

    (request,) = seen
    assert str(request.url) == f"{GITHUB_API}/repos/{REPO}/pulls/7"
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert request.headers["accept-encoding"] == "identity"
    assert request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 10.0
    )


def test_the_timeout_the_adapter_is_given_is_the_one_its_client_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _seen_by_the_transport(monkeypatch)

    GitHubAdapter(REPO, "test-token", timeout=2.5).get_pull_request_status(7)

    (request,) = seen
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.5)


def test_create_branch_audited() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == f"/repos/{REPO}/git/refs"
        captured.update(json.loads(request.content))
        return httpx2.Response(201, json={"ref": "refs/heads/feature"})

    audit = _CapturingAudit()
    _adapter(handler, audit).create_branch("feature", "abc123")

    assert captured == {"ref": "refs/heads/feature", "sha": "abc123"}
    assert [e["action"] for e in audit.events] == ["github.create_branch"]


def test_commit_file_audited_and_returns_sha() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PUT"
        assert request.url.path == f"/repos/{REPO}/contents/release-manifest.yaml"
        captured.update(json.loads(request.content))
        return httpx2.Response(200, json={"commit": {"sha": "commit123"}})

    audit = _CapturingAudit()
    sha = _adapter(handler, audit).commit_file(
        "release-manifest.yaml", "hello: world\n", "feature", "update manifest", sha="old"
    )

    assert sha == "commit123"
    assert base64.b64decode(captured["content"]).decode() == "hello: world\n"
    assert captured["branch"] == "feature"
    assert captured["sha"] == "old"
    assert audit.events[0]["action"] == "github.commit_file"
    assert audit.events[0]["details"]["commit"] == "commit123"


def test_create_pull_request_audited() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == f"/repos/{REPO}/pulls"
        return httpx2.Response(
            201,
            json={
                "number": 7,
                "html_url": "https://github.com/owner/repo/pull/7",
                "state": "open",
                "head": {"ref": "feature"},
                "base": {"ref": "main"},
                "title": "Promote",
            },
        )

    audit = _CapturingAudit()
    pr = _adapter(handler, audit).create_pull_request("feature", "main", "Promote", "body")

    assert isinstance(pr, PullRequest)
    assert pr.number == 7
    assert pr.url == "https://github.com/owner/repo/pull/7"
    assert pr.state == "open"
    assert audit.events[0]["action"] == "github.create_pull_request"
    assert audit.events[0]["details"]["number"] == 7


def test_get_pull_request_status() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == f"/repos/{REPO}/pulls/7"
        return httpx2.Response(
            200,
            json={
                "number": 7,
                "html_url": "u",
                "state": "merged",
                "head": {"ref": "feature"},
                "base": {"ref": "main"},
                "title": "t",
            },
        )

    pr = _adapter(handler).get_pull_request_status(7)
    assert pr.state == "merged"


def test_error_status_raises() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(422, json={"message": "Validation failed"})

    with pytest.raises(GitHubError, match="422"):
        _adapter(handler).create_pull_request("feature", "main", "t", "b")


def test_find_open_pull_request() -> None:
    def found(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == f"/repos/{REPO}/pulls"
        assert request.url.params["head"] == "owner:feature"
        assert request.url.params["state"] == "open"
        return httpx2.Response(
            200,
            json=[
                {
                    "number": 5,
                    "html_url": "u",
                    "state": "open",
                    "head": {"ref": "feature"},
                    "base": {"ref": "main"},
                    "title": "t",
                }
            ],
        )

    def none(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=[])

    pr = _adapter(found).find_open_pull_request("feature")
    assert pr is not None and pr.number == 5
    assert _adapter(none).find_open_pull_request("feature") is None


# --- the boundary: nothing but GitHubError leaves the adapter -------------------------------

SENTINEL = "fake-sentinel-github-token"


def _sentinel_adapter(handler: Any) -> GitHubAdapter:
    """An adapter whose client carries a sentinel token, as the real one does."""
    return GitHubAdapter(REPO, SENTINEL, transport=mock_transport(handler))


def _raises(error: type[httpx2.RequestError], text: str) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error(text, request=request)

    return handler


# Every operation, called the way the promotion-PR workflow calls it.
OPERATIONS = {
    "create_branch": lambda a: a.create_branch("gora/promote-dev-1.2.0", "abc"),
    "commit_file": lambda a: a.commit_file("release-manifest.yaml", "x", "b", "m", sha="s"),
    "find_open_pull_request": lambda a: a.find_open_pull_request("b"),
    "create_pull_request": lambda a: a.create_pull_request("b", "main", "t", "body"),
    "get_pull_request_status": lambda a: a.get_pull_request_status(7),
}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize(
    "error",
    [httpx2.ConnectError, httpx2.ReadTimeout, httpx2.RemoteProtocolError, httpx2.TooManyRedirects],
    ids=lambda e: e.__name__,
)
def test_a_transport_error_becomes_github_error(
    operation: str, error: type[httpx2.RequestError]
) -> None:
    adapter = _sentinel_adapter(_raises(error, "[Errno 8] nodename nor servname provided"))

    with pytest.raises(GitHubError) as info:
        OPERATIONS[operation](adapter)

    message = str(info.value)
    assert f"/repos/{REPO}/" in message  # names the operation
    assert message.endswith(f" failed: {error.__name__}")  # and nothing httpx2 wrote
    assert SENTINEL not in message
    assert "?" not in message  # no query string
    assert isinstance(info.value.__cause__, httpx2.HTTPError)


def test_nothing_httpx_writes_reaches_the_message() -> None:
    """Even an exception text that quotes the request URL and its token stays out."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        quoted = f"{request.url} {request.headers['authorization']}"
        raise httpx2.ConnectError(quoted, request=request)

    with pytest.raises(GitHubError) as info:
        _sentinel_adapter(handler).find_open_pull_request("feature")

    assert SENTINEL not in str(info.value)
    assert "state=open" not in str(info.value)
    assert SENTINEL in str(info.value.__cause__)  # calibration: the text really carried both


# A value that goes into a URL and no longer fits the 65,536 characters httpx2 takes of one. The
# host is a constant now, so a control character in it can no longer be given. A path is
# refused by the cap on a path before that (below); the query is not a path, and a head of this
# length is the input that still reaches httpx2's InvalidURL.
TOO_LONG = "a" * 70_000


def test_a_url_httpx_refuses_becomes_github_error() -> None:
    """A URL over httpx2's limit made it raise InvalidURL to the operator."""
    called: list[httpx2.Request] = []
    adapter = _sentinel_adapter(lambda request: called.append(request))

    with pytest.raises(GitHubError, match="failed: invalid URL") as info:
        adapter.find_open_pull_request(TOO_LONG)

    assert called == []
    assert SENTINEL not in str(info.value)
    assert isinstance(info.value.__cause__, httpx2.InvalidURL)


@pytest.mark.parametrize("length", [3_000, 5_000, 70_000])
def test_a_content_path_over_the_cap_is_a_value_error_before_any_request(length: int) -> None:
    """The writer's one free-form value in a path is a content path. One of 70,000 characters
    made a URL httpx2 refuses and was a ``GitHubError`` naming the operation with the whole
    path; one of 3,000 was sent. Now the cap on a path refuses both, as a caller's error, and
    the message names the cap and never the path."""
    called: list[httpx2.Request] = []
    audit = _CapturingAudit()

    def handler(request: httpx2.Request) -> httpx2.Response:
        called.append(request)
        return httpx2.Response(200, json={"commit": {"sha": "c"}})

    adapter = GitHubAdapter(REPO, SENTINEL, audit=audit, transport=mock_transport(handler))

    with pytest.raises(ValueError, match="over the cap of") as info:
        adapter.commit_file("a" * length, "x", "b", "m")

    assert called == []
    assert audit.events == []
    assert len(str(info.value)) < 200
    assert "aaaa" not in str(info.value)
    assert SENTINEL not in str(info.value)


MALFORMED = {
    "not-json": httpx2.Response(200, text="<html>not json</html>"),
    "wrong-object": httpx2.Response(200, json={"unexpected": "shape"}),
    "a-list": httpx2.Response(200, json=["a", "list"]),
    "list-of-wrong-objects": httpx2.Response(200, json=[{"no": "number"}]),
    # about 400 KB, under the cap: json.loads raises RecursionError on it
    "nested-too-deep": httpx2.Response(200, content=DEEP),
}
# The operations that succeed on a malformed body, each because it reads nothing from it:
# create_branch reads no field of the ref GitHub answers with, and a JSON object, any object,
# is all it needs of the body. Every other pair is a GitHubError.
READS_NOTHING_FROM = {("create_branch", "wrong-object")}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize("body", list(MALFORMED))
def test_a_response_that_is_not_the_expected_json_becomes_github_error(
    operation: str, body: str
) -> None:
    response = MALFORMED[body]
    adapter = _sentinel_adapter(lambda request: response)

    if (operation, body) in READS_NOTHING_FROM:
        assert OPERATIONS[operation](adapter) is None
        return
    with pytest.raises(GitHubError, match="unexpected response") as info:
        OPERATIONS[operation](adapter)
    assert SENTINEL not in str(info.value)


def test_an_error_status_never_shows_the_query_string() -> None:
    adapter = _sentinel_adapter(lambda request: httpx2.Response(500, text="boom"))

    with pytest.raises(GitHubError) as info:
        adapter.find_open_pull_request("secret-branch")

    assert "secret-branch" not in str(info.value)
    assert "?" not in str(info.value)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_404_is_an_error_for_every_operation(operation: str) -> None:
    """GitHub answers 404 for a missing branch and for a repository the token cannot see, so
    no operation takes it for an answer."""
    adapter = _sentinel_adapter(lambda request: httpx2.Response(404, json={"message": "Not Found"}))

    with pytest.raises(GitHubError) as info:
        OPERATIONS[operation](adapter)

    assert str(info.value).endswith(" -> 404: Not Found")


# --- a redirect: refused, never followed ----------------------------------------------------


def test_a_redirect_is_refused_and_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real 301 of a renamed repository: the identity of a repository is its id, never a
    name a redirect leads to. The core says what it saw, a redirect, and not what it guesses
    the cause to be."""
    seen: list[str] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        if request.url.path == f"/repos/{REPO}/pulls/7":
            return replay("repo-renamed")
        return httpx2.Response(200, json={"followed": True})

    intercept_transport(monkeypatch, answer)

    with pytest.raises(GitHubError) as info:
        GitHubAdapter(REPO, SENTINEL).get_pull_request_status(7)

    assert str(info.value) == (
        f"GET /repos/{REPO}/pulls/7 failed: the host answered a redirect (301); "
        "no redirect is followed"
    )
    assert seen == [f"/repos/{REPO}/pulls/7"]


# --- values in URL paths --------------------------------------------------------------------


def _path_of(request: httpx2.Request) -> bytes:
    return request.url.raw_path.split(b"?")[0]


def test_a_content_path_is_percent_encoded_except_its_slashes() -> None:
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, _path_of(request)))
        return httpx2.Response(200, json={"commit": {"sha": "c"}})

    _sentinel_adapter(handler).commit_file("deploy/a b#.yaml", "x", "feat/x", "m")

    path = f"/repos/{REPO}/contents/deploy/a%20b%23.yaml".encode()
    assert seen == [("PUT", path)]


@pytest.mark.parametrize(
    "path",
    ["..", ".", "../../../user/x", "a/../b", "a/./b", "a//b", "/a", "a/", ""],
)
def test_a_content_path_that_would_leave_its_path_is_a_caller_s_error_and_sends_nothing(
    path: str,
) -> None:
    """``quote`` keeps ``.`` and httpx2 removes dot segments: ``commit_file("../../../user/x")``
    sent a PUT, with the token, to ``/repos/user/x``."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"sha": "blob", "commit": {"sha": "c"}})

    with pytest.raises(ValueError, match="a part of a path must not be empty"):
        _sentinel_adapter(handler).commit_file(path, "x", "feat/x", "m")

    assert seen == []


# --- the surface: what the adapter can do, and what a write sends ---------------------------


def test_the_adapter_has_exactly_these_public_methods() -> None:
    """L15: GORA's adapter has no merge operation, and none enables auto-merge.

    The list is written out. A method added, renamed or removed is a red test and a reviewed
    edit of this list.
    """
    public = [name for name in dir(GitHubAdapter) if not name.startswith("_")]

    assert public == [
        "commit_file",
        "create_branch",
        "create_pull_request",
        "find_open_pull_request",
        "get_pull_request_status",
    ]


# One body that every write can read its answer from.
_WRITE_RESPONSE = {
    "commit": {"sha": "c"},
    "number": 7,
    "html_url": "u",
    "state": "open",
    "head": {"ref": "feature"},
    "base": {"ref": "main"},
    "title": "t",
}


def _sent(call: Callable[[GitHubAdapter], object]) -> list[tuple[str, str, dict[str, Any]]]:
    """Each request ``call`` makes: its method, its path, its whole JSON body."""
    sent: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.method, request.url.path, json.loads(request.content)))
        return httpx2.Response(200, json=_WRITE_RESPONSE)

    call(_adapter(handler))
    return sent


def test_create_branch_sends_a_ref_and_a_sha() -> None:
    sent = _sent(lambda adapter: adapter.create_branch("feature", "abc123"))

    assert sent == [
        ("POST", f"/repos/{REPO}/git/refs", {"ref": "refs/heads/feature", "sha": "abc123"})
    ]


@pytest.mark.parametrize(
    ("sha", "extra"),
    [(None, {}), ("old", {"sha": "old"})],
    ids=["create", "update"],
)
def test_commit_file_sends_no_author_committer_or_signature(
    sha: str | None, extra: dict[str, str]
) -> None:
    """ADR-023: GitHub signs an App's commit only when the request names none of the three."""
    sent = _sent(
        lambda adapter: adapter.commit_file("release-manifest.yaml", "x", "feature", "m", sha=sha)
    )

    body = {"message": "m", "content": base64.b64encode(b"x").decode(), "branch": "feature"}
    assert sent == [("PUT", f"/repos/{REPO}/contents/release-manifest.yaml", body | extra)]


def test_create_pull_request_sends_a_head_a_base_a_title_and_a_body() -> None:
    sent = _sent(lambda adapter: adapter.create_pull_request("feature", "main", "t", "b"))

    body = {"head": "feature", "base": "main", "title": "t", "body": "b"}
    assert sent == [("POST", f"/repos/{REPO}/pulls", body)]

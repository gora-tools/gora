"""Unit tests for the bounded GitHub adapter (no network — httpx.MockTransport)."""

import base64
import json
from typing import Any

import httpx
import pytest

from gora.adapters.github import GitHubAdapter, GitHubError, PullRequest
from gora.audit.logger import AuditLogger

REPO = "owner/repo"


class _CapturingAudit(AuditLogger):
    def __init__(self) -> None:
        super().__init__(actor="test")
        self.events: list[dict[str, Any]] = []

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        event = super().record(action, **details)
        self.events.append(event)
        return event


def _adapter(handler: Any, audit: AuditLogger | None = None) -> GitHubAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubAdapter(REPO, "test-token", audit=audit, client=client)


def test_token_required() -> None:
    with pytest.raises(ValueError, match="token is required"):
        GitHubAdapter(REPO, "")


def test_get_ref_sha() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/repos/{REPO}/git/ref/heads/main"
        return httpx.Response(200, json={"object": {"sha": "abc123"}})

    assert _adapter(handler).get_ref_sha("main") == "abc123"


def test_create_branch_audited() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/repos/{REPO}/git/refs"
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"ref": "refs/heads/feature"})

    audit = _CapturingAudit()
    _adapter(handler, audit).create_branch("feature", "abc123")

    assert captured == {"ref": "refs/heads/feature", "sha": "abc123"}
    assert [e["action"] for e in audit.events] == ["github.create_branch"]


def test_commit_file_audited_and_returns_sha() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == f"/repos/{REPO}/contents/release-manifest.yaml"
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"commit": {"sha": "commit123"}})

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
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/repos/{REPO}/pulls"
        return httpx.Response(
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
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{REPO}/pulls/7"
        return httpx.Response(
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


def test_get_file_sha_present_and_absent() -> None:
    def present(request: httpx.Request) -> httpx.Response:
        assert request.url.params["ref"] == "main"
        return httpx.Response(200, json={"sha": "blob123"})

    def absent(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    assert _adapter(present).get_file_sha("release-manifest.yaml", "main") == "blob123"
    assert _adapter(absent).get_file_sha("release-manifest.yaml", "main") is None


def test_error_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation failed"})

    with pytest.raises(GitHubError, match="422"):
        _adapter(handler).create_pull_request("feature", "main", "t", "b")


def test_find_open_pull_request() -> None:
    def found(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{REPO}/pulls"
        assert request.url.params["head"] == "owner:feature"
        assert request.url.params["state"] == "open"
        return httpx.Response(
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

    def none(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    pr = _adapter(found).find_open_pull_request("feature")
    assert pr is not None and pr.number == 5
    assert _adapter(none).find_open_pull_request("feature") is None


# --- the boundary: nothing but GitHubError leaves the adapter -------------------------------

TOKEN = "fake-sentinel-github-token"


def _sentinel_adapter(handler: Any, repo: str = REPO) -> GitHubAdapter:
    """An adapter whose client carries a sentinel token, as the real one does."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    return GitHubAdapter(repo, TOKEN, client=client)


def _raises(error: type[Exception], text: str) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error(text, request=request)

    return handler


# Every operation, called the way the promotion-PR workflow calls it.
OPERATIONS = {
    "get_ref_sha": lambda a: a.get_ref_sha("main"),
    "create_branch": lambda a: a.create_branch("gora/promote-dev-1.2.0", "abc"),
    "get_file_sha": lambda a: a.get_file_sha("release-manifest.yaml", "main"),
    "commit_file": lambda a: a.commit_file("release-manifest.yaml", "x", "b", "m", sha="s"),
    "find_open_pull_request": lambda a: a.find_open_pull_request("b"),
    "create_pull_request": lambda a: a.create_pull_request("b", "main", "t", "body"),
    "get_pull_request_status": lambda a: a.get_pull_request_status(7),
}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.TooManyRedirects],
    ids=lambda e: e.__name__,
)
def test_a_transport_error_becomes_github_error(operation: str, error: type[Exception]) -> None:
    adapter = _sentinel_adapter(_raises(error, "[Errno 8] nodename nor servname provided"))

    with pytest.raises(GitHubError) as info:
        OPERATIONS[operation](adapter)

    message = str(info.value)
    assert f"/repos/{REPO}/" in message  # names the operation
    assert message.endswith(f" failed: {error.__name__}")  # and nothing httpx wrote
    assert TOKEN not in message
    assert "?" not in message  # no query string
    assert isinstance(info.value.__cause__, httpx.HTTPError)


def test_nothing_httpx_writes_reaches_the_message() -> None:
    """Even an exception text that quotes the request URL and its token stays out."""

    def handler(request: httpx.Request) -> httpx.Response:
        quoted = f"{request.url} {request.headers['authorization']}"
        raise httpx.ConnectError(quoted, request=request)

    with pytest.raises(GitHubError) as info:
        _sentinel_adapter(handler).get_file_sha("release-manifest.yaml", "main")

    assert TOKEN not in str(info.value)
    assert "ref=main" not in str(info.value)
    assert TOKEN in str(info.value.__cause__)  # calibration: the text really carried both


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_url_httpx_refuses_becomes_github_error(operation: str) -> None:
    """A control character in the repository made httpx raise InvalidURL to the operator."""
    called: list[httpx.Request] = []
    adapter = _sentinel_adapter(lambda request: called.append(request), repo="own\x01er/repo")

    with pytest.raises(GitHubError, match="failed: invalid URL") as info:
        OPERATIONS[operation](adapter)

    assert called == []
    assert TOKEN not in str(info.value)
    assert isinstance(info.value.__cause__, httpx.InvalidURL)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, json=["a", "list"]),
        httpx.Response(200, json=[{"no": "number"}]),
    ],
    ids=["not-json", "wrong-object", "a-list", "list-of-wrong-objects"],
)
def test_a_response_that_is_not_the_expected_json_becomes_github_error(
    operation: str, response: httpx.Response
) -> None:
    adapter = _sentinel_adapter(lambda request: response)

    try:
        OPERATIONS[operation](adapter)
    except GitHubError as exc:
        assert "unexpected response" in str(exc)
        assert TOKEN not in str(exc)
    # An operation that reads nothing from this body (create_branch) may succeed; none may
    # raise anything else, which pytest reports as an error here.


def test_an_error_status_never_shows_the_query_string() -> None:
    adapter = _sentinel_adapter(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(GitHubError) as info:
        adapter.get_file_sha("release-manifest.yaml", "secret-branch")

    assert "secret-branch" not in str(info.value)
    assert "?" not in str(info.value)


# --- values in URL paths --------------------------------------------------------------------


def _path_of(request: httpx.Request) -> bytes:
    return request.url.raw_path.split(b"?")[0]


def test_a_ref_name_is_percent_encoded_except_its_slashes() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_path_of(request))
        return httpx.Response(200, json={"object": {"sha": "abc"}})

    adapter = _sentinel_adapter(handler)
    adapter.get_ref_sha("gora/promote-dev-1.2.0-rc.1")
    adapter.get_ref_sha("a b/c#d?e%f\x01")

    assert seen == [
        f"/repos/{REPO}/git/ref/heads/gora/promote-dev-1.2.0-rc.1".encode(),
        f"/repos/{REPO}/git/ref/heads/a%20b/c%23d%3Fe%25f%01".encode(),
    ]


def test_a_content_path_is_percent_encoded_except_its_slashes() -> None:
    seen: list[tuple[str, bytes, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, _path_of(request), request.url.params.get("ref")))
        if request.method == "GET":
            return httpx.Response(200, json={"sha": "blob"})
        return httpx.Response(200, json={"commit": {"sha": "c"}})

    adapter = _sentinel_adapter(handler)
    adapter.get_file_sha("deploy/a b#.yaml", "feat/x")
    adapter.commit_file("deploy/a b#.yaml", "x", "feat/x", "m")

    path = f"/repos/{REPO}/contents/deploy/a%20b%23.yaml".encode()
    assert seen == [("GET", path, "feat/x"), ("PUT", path, None)]

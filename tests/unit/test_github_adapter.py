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

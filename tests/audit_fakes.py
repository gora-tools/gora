"""Fakes for the audit tests: GitHub and Slack over ``httpx.MockTransport``, no network.

The real adapters run, so every event they audit is the real one. Only the transport is
replaced, and each client carries the token it was given as a bearer header, so a secret
that reaches an adapter is really in the process the tests inspect.
"""

import errno
import hashlib
import json
from pathlib import Path

import httpx
import pytest

import gora.adapters.github as github
import gora.adapters.slack as slack
import gora.audit.sink as sink
from gora.adapters.github import GitHubAdapter
from gora.adapters.slack import SlackAdapter
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import ReleaseManifest

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIGEST_B = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
IMAGE = "ghcr.io/test/service"

# The adapters as they are before a test replaces them in their package.
_REAL_GITHUB = GitHubAdapter
_REAL_SLACK = SlackAdapter


def seed_manifest(path: Path) -> None:
    candidate = ReleaseCandidate(version="1.2.0-rc.1", image=IMAGE, digest=DIGEST_A)
    ReleaseManifest(release="1.2.0", candidate=candidate).to_yaml(path)


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def journal_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def stderr_events(stderr: str) -> list[dict[str, object]]:
    """The event lines a command printed on stderr when the journal missed them."""
    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def fail_writes(monkeypatch: pytest.MonkeyPatch, *, after: int = 0) -> None:
    """A test double: every journal write after the first ``after`` fails, as a full disk would.

    Not ``/dev/full``: the failure is injected at the one function the sink writes through,
    so it happens exactly when the test says, after the command has already mutated.
    """
    real = sink._write
    count = 0

    def write(fd: int, data: bytes | memoryview) -> int:
        nonlocal count
        count += 1
        if count > after:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fd, data)

    monkeypatch.setattr(sink, "_write", write)


def tear_first_write(monkeypatch: pytest.MonkeyPatch, *, nbytes: int = 10) -> None:
    """A test double: the first journal write lands ``nbytes`` and then fails (ENOSPC).

    Every later write is real, so a journal that kept writing after the tear would put the
    next event straight after the torn bytes.
    """
    real = sink._write
    calls = 0

    def write(fd: int, data: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            real(fd, bytes(data[:nbytes]))
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fd, data)

    monkeypatch.setattr(sink, "_write", write)


def complete_lines(raw: bytes) -> list[bytes]:
    """The newline-terminated lines of a journal; a tail without a newline is not one."""
    return raw.split(b"\n")[:-1]


class Remote:
    """A fake GitHub and a fake Slack that record every request they receive."""

    def __init__(self, *, fail_pull_request: bool = False) -> None:
        self.github_requests: list[httpx.Request] = []
        self.slack_requests: list[httpx.Request] = []
        self._fail_pull_request = fail_pull_request

    @property
    def requests(self) -> list[httpx.Request]:
        return [*self.github_requests, *self.slack_requests]

    def authorization_headers(self) -> set[str]:
        return {r.headers.get("authorization", "") for r in self.requests}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(github, "GitHubAdapter", self._github_adapter)
        monkeypatch.setattr(slack, "SlackAdapter", self._slack_adapter)

    def _github_adapter(self, repo: str, token: str) -> GitHubAdapter:
        client = httpx.Client(
            transport=httpx.MockTransport(self._github),
            headers={"Authorization": f"Bearer {token}"},
        )
        return _REAL_GITHUB(repo, token, client=client)

    def _slack_adapter(self, token: str) -> SlackAdapter:
        client = httpx.Client(
            transport=httpx.MockTransport(self._slack),
            headers={"Authorization": f"Bearer {token}"},
        )
        return _REAL_SLACK(token, client=client)

    def _github(self, request: httpx.Request) -> httpx.Response:
        self.github_requests.append(request)
        path, method = request.url.path, request.method
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if method == "GET" and "/git/ref/heads/gora/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "POST" and path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "PUT" and "/contents/" in path:
            return httpx.Response(200, json={"commit": {"sha": "commitsha"}})
        if method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if method == "POST" and path.endswith("/pulls"):
            if self._fail_pull_request:
                return httpx.Response(422, json={"message": "Validation Failed"})
            return httpx.Response(
                201,
                json={
                    "number": 7,
                    "html_url": "https://github.com/o/r/pull/7",
                    "state": "open",
                    "head": {"ref": "gora/promote-dev-1.2.0-rc.1"},
                    "base": {"ref": "main"},
                    "title": "Promote 1.2.0-rc.1 to dev",
                },
            )
        return httpx.Response(500, json={"message": f"unexpected {method} {path}"})

    def _slack(self, request: httpx.Request) -> httpx.Response:
        self.slack_requests.append(request)
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.1"})

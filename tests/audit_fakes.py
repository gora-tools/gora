"""Fakes for the audit tests: GitHub and Slack over ``httpx.MockTransport``, no network.

The real adapters run, so every event they audit is the real one. Only the transport is
replaced, and each client carries the token it was given as a bearer header, so a secret
that reaches an adapter is really in the process the tests inspect.
"""

import errno
import json
from pathlib import Path

import httpx
import pytest

import gora.audit.sink as sink
from gora.adapters.github import GitHubAdapter
from gora.adapters.slack import SlackAdapter


def journal_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def fail_writes(monkeypatch: pytest.MonkeyPatch, *, after: int = 0) -> None:
    """A test double: every journal write after the first ``after`` fails, as a full disk would.

    Not ``/dev/full``: the failure is injected at the one function the sink writes through,
    so it happens exactly when the test says.
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

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def authorization_headers(self) -> set[str]:
        return {r.headers.get("authorization", "") for r in self.requests}

    def github(self, repo: str, token: str) -> GitHubAdapter:
        client = httpx.Client(
            transport=httpx.MockTransport(self._github),
            headers={"Authorization": f"Bearer {token}"},
        )
        return GitHubAdapter(repo, token, client=client)

    def slack(self, token: str) -> SlackAdapter:
        client = httpx.Client(
            transport=httpx.MockTransport(self._slack),
            headers={"Authorization": f"Bearer {token}"},
        )
        return SlackAdapter(token, client=client)

    def _github(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if method == "POST" and path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if method == "PUT" and "/contents/" in path:
            return httpx.Response(200, json={"commit": {"sha": "commitsha"}})
        if method == "POST" and path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "number": 7,
                    "html_url": "https://github.com/o/r/pull/7",
                    "state": "open",
                    "head": {"ref": "gora/promote-dev"},
                    "base": {"ref": "main"},
                    "title": "Promote",
                },
            )
        return httpx.Response(500, json={"message": f"unexpected {method} {path}"})

    def _slack(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.1"})

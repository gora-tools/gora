"""Fakes for the audit tests: GitHub and Slack over ``httpx.MockTransport``, no network.

The real adapters run, so every event they audit is the real one, and each builds its own
client. Only the transport is replaced, so the token an adapter sends as a bearer header is
the one the fake receives.
"""

import errno
import json
from collections.abc import Callable
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


def intercept_transport(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[httpx.Request], httpx.Response]
) -> None:
    """A test double: every request any ``httpx.Client`` in the process sends is answered by
    ``answer`` at the transport, the last step before the network, and goes no further.

    Not a ``MockTransport`` handed to the adapter: a client an adapter builds itself keeps its
    own timeout and headers, and ``answer`` sees the request as the real transport would.
    """

    def handle_request(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        return answer(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)


class Remote:
    """A fake GitHub and a fake Slack that record every request they receive.

    Installed at the transport (``intercept_transport``): the adapters build their own clients,
    so what they send, ``Authorization`` included, is what the fakes receive.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.requests: list[httpx.Request] = []
        intercept_transport(monkeypatch, self._answer)

    def authorization_headers(self) -> set[str]:
        return {r.headers.get("authorization", "") for r in self.requests}

    def github(self, repo: str, token: str) -> GitHubAdapter:
        return GitHubAdapter(repo, token)

    def slack(self, token: str) -> SlackAdapter:
        return SlackAdapter(token)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.github.com":
            return self._github(request)
        if request.url.host == "slack.com":
            return self._slack(request)
        return httpx.Response(500, json={"message": f"unexpected host {request.url.host}"})

    def _github(self, request: httpx.Request) -> httpx.Response:
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
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.1"})

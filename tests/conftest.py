"""Shared pytest fixtures.

Gate-defining file (ADR-0016 rule 7). Every test, on every layer, is hermetic
(TESTING rule 6): settings never come from the machine running the suite, and any
attempt to reach the network fails the test — even when the code under test
swallows the error, as the best-effort Slack notify does.
"""

import os
import socket
from collections.abc import Iterator
from typing import Any

import pytest

from gora.config.settings import Settings, get_settings

pytest_plugins = ["pytester"]


class NetworkBlockedError(OSError):
    """A test tried to reach the network. An OSError, so code under test sees a
    connection failure — the guard records the attempt regardless of handling."""


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Read settings fresh in every test — get_settings is lru_cached."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `.env` from the cwd and no `GORA_*` from the shell: code defaults plus
    whatever the test itself passes. A developer's real Slack config stays out."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key in list(os.environ):
        if key.upper().startswith("GORA_"):
            monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def network_attempts(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Block DNS and every non-Unix socket connect; fail the test on any attempt.

    Yields the recorded attempts so the guard's own calibration test can see them.
    """
    attempts: list[str] = []

    def _blocked(what: str) -> NetworkBlockedError:
        attempts.append(what)
        return NetworkBlockedError(f"network blocked in tests: {what}")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        if self.family == socket.AF_UNIX:
            return real_connect(self, address)
        raise _blocked(f"connect {address!r}")

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family == socket.AF_UNIX:
            return real_connect_ex(self, address)
        raise _blocked(f"connect_ex {address!r}")

    def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        raise _blocked(f"getaddrinfo {host!r}:{port!r}")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield attempts
    if attempts:
        pytest.fail(f"test attempted network access (TESTING rule 6): {attempts}", pytrace=False)

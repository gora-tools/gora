"""Shared pytest fixtures and the session-wide hermetic guard.

Gate-defining file (ADR-0016 rule 7). TESTING rule 6, for the whole pytest session —
from configuration to the end, through collection, fixtures of every scope and other
threads: settings never come from the machine running the suite, and no code reaches
the network. An attempt in a test's own thread fails that test, even when the code
under test swallows the error, as the best-effort Slack notify does. An attempt no
single test can be charged with — a module import, a session- or module-scoped
fixture, another thread, even one running while a test does — fails the session.
Neither has an API to switch it off: the state lives in a plugin, not in fixtures.
"""

import os
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from gora.config.settings import Settings, get_settings

pytest_plugins = ["pytester"]

# Threads a test leaves behind get this long to finish before the session is judged.
THREAD_GRACE_SECONDS = 5.0

_PLUGIN = "gora-hermetic-guard"
_MARK = "__gora_network_guard__"


class NetworkBlockedError(OSError):
    """Code tried to reach the network. An OSError, so code under test sees a
    connection failure — the guard records the attempt regardless of handling."""


class _HermeticGuard:
    """Session-wide settings isolation and network block; a pytest plugin."""

    def __init__(self) -> None:
        self._main_thread = threading.get_ident()
        self._threads_at_start = set(threading.enumerate())
        self._settings = pytest.MonkeyPatch()
        self._network = pytest.MonkeyPatch()
        self._nested = False
        self._item: pytest.Item | None = None
        self._scoped_fixtures: list[str] = []
        self._item_attempts: dict[str, list[str]] = {}
        self.phase = "during collection"
        self.session_attempts: list[str] = []

    def install(self) -> None:
        self._settings.setitem(Settings.model_config, "env_file", None)
        for key in list(os.environ):
            if key.upper().startswith("GORA_"):
                self._settings.delenv(key)
        get_settings.cache_clear()

        # A pytester session inside this one installs its own guard on top of ours.
        self._nested = getattr(socket.getaddrinfo, _MARK, False)
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex

        def connect(sock: socket.socket, address: Any) -> None:
            if sock.family == socket.AF_UNIX:
                return real_connect(sock, address)
            raise self._blocked(f"connect {address!r}")

        def connect_ex(sock: socket.socket, address: Any) -> int:
            if sock.family == socket.AF_UNIX:
                return real_connect_ex(sock, address)
            raise self._blocked(f"connect_ex {address!r}")

        def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            raise self._blocked(f"getaddrinfo {host!r}:{port!r}")

        setattr(getaddrinfo, _MARK, True)
        self._network.setattr(socket.socket, "connect", connect)
        self._network.setattr(socket.socket, "connect_ex", connect_ex)
        self._network.setattr(socket, "getaddrinfo", getaddrinfo)

    def uninstall(self) -> None:
        self._settings.undo()
        # A nested session hands the outer guard back. The outermost one stays until
        # the process exits, so a thread outliving the session still cannot reach out.
        if self._nested:
            self._network.undo()

    def _blocked(self, what: str) -> NetworkBlockedError:
        if threading.get_ident() != self._main_thread:
            running = f"while {self._item.nodeid} ran" if self._item else self.phase
            name = threading.current_thread().name
            self.session_attempts.append(f"{what} [thread {name!r} {running}]")
        elif self._scoped_fixtures:
            self.session_attempts.append(f"{what} [{self._scoped_fixtures[-1]}]")
        elif self._item is not None:
            self._item_attempts.setdefault(self._item.nodeid, []).append(what)
        else:
            self.session_attempts.append(f"{what} [{self.phase}]")
        return NetworkBlockedError(f"network blocked in tests: {what}")

    # Hooks. Registered as a plugin, not left in this conftest: a conftest's hooks apply
    # only to nodes below its directory, so a session-scoped fixture (whose node is the
    # Session itself) would bypass a pytest_fixture_setup defined here.

    @pytest.hookimpl(wrapper=True)
    def pytest_runtestloop(self) -> Iterator[object]:
        self.phase = "between tests"
        try:
            return (yield)
        finally:
            self.phase = "after the last test"

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Iterator[object]:
        self._item = item
        try:
            return (yield)
        finally:
            self._item = None
            self.phase = "between tests"

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef: pytest.FixtureDef[Any]) -> Iterator[object]:
        if fixturedef.scope == "function":
            return (yield)
        self._scoped_fixtures.append(f"fixture {fixturedef.argname!r} ({fixturedef.scope}-scoped)")
        try:
            return (yield)
        finally:
            self._scoped_fixtures.pop()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Iterator[pytest.TestReport]:
        report = yield
        attempts = self._item_attempts.pop(item.nodeid, []) if call.when == "teardown" else []
        if attempts:
            msg = f"test attempted network access (TESTING rule 6): {attempts}"
            report.longrepr = f"{report.longrepr}\n\n{msg}" if report.failed else msg
            report.outcome = "failed"
        return report

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.phase = "after the last test"
        self._join_session_threads()
        if not self.session_attempts:
            return
        attempts = self.session_attempts
        msg = f"network access not charged to any test (TESTING rule 6): {attempts}"
        report = pytest.TestReport(
            nodeid="network-guard",
            location=("tests/conftest.py", None, "network-guard"),
            keywords={},
            outcome="failed",
            longrepr=msg,
            when="teardown",
        )
        session.config.hook.pytest_runtest_logreport(report=report)
        session.testsfailed += 1
        if session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def _join_session_threads(self) -> None:
        deadline = time.monotonic() + THREAD_GRACE_SECONDS
        for thread in threading.enumerate():
            if thread in self._threads_at_start or thread is threading.current_thread():
                continue
            thread.join(max(0.0, deadline - time.monotonic()))


def pytest_configure(config: pytest.Config) -> None:
    guard = _HermeticGuard()
    guard.install()
    if config.pluginmanager.register(guard, _PLUGIN) is None:  # `-p no:gora-hermetic-guard`
        raise pytest.UsageError(f"{_PLUGIN} cannot be disabled (TESTING rule 6)")


def pytest_unconfigure(config: pytest.Config) -> None:
    guard = config.pluginmanager.get_plugin(_PLUGIN)
    if isinstance(guard, _HermeticGuard):
        guard.uninstall()
        config.pluginmanager.unregister(guard)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Read settings fresh in every test — get_settings is lru_cached."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

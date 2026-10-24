"""Shared pytest fixtures and the session-wide hermetic guard.

Gate-defining file (ADR-0016 rule 7). TESTING rule 6, for the whole pytest session —
from configuration to the end, through collection, fixtures of every scope and other
threads: settings never come from the machine running the suite, and no code reaches
the network. An attempt in a test's own thread fails that test, even when the code
under test swallows the error, as the best-effort Slack notify does. An attempt no
single test can be charged with — a module import, a session- or module-scoped
fixture, another thread, even one running while a test does — fails the session.
A Python child process the session starts runs under the same block, and its attempt
is charged the same way, to the test that started it or else to the session.
Neither has an API to switch it off: the state lives in a plugin, not in fixtures.
The developer's terminal and colour settings are removed too, so no outcome depends on
the terminal the suite was started from. Every test also gets its own HOME, XDG
directories and audit journal under its tmp_path (``_isolated_home``): the CLI writes a
journal, and a test must never write one to the real home.

Two more guards, for this suite only (not for a pytester session that copies this file):
the suite refuses an interpreter whose minor
version is not the one ``.python-version`` pins, and a full run (``pytest`` with no path,
as ``make verify`` and CI run it) fails when a test module tracked in the git index was
not collected (ADR-0016 rule 3).

A gate ends (TESTING rule 8): the suite refuses to run unless pytest-timeout gives every
test a timeout, and no test may carry its own ``timeout`` marker. A child process is
started one at a time: a second ``Popen`` while one is being created raises
``ConcurrentSpawnError``, because concurrent process creation deadlocked the whole
interpreter under amd64 emulation on Apple silicon.
"""

import atexit
import inspect
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from gora.config.settings import Settings, get_settings

pytest_plugins = ["pytester"]

# Threads a test leaves behind get this long to finish before the session is judged.
THREAD_GRACE_SECONDS = 5.0

_PLUGIN = "gora-hermetic-guard"
_SUITE_PLUGIN = "gora-suite-size-guard"
_MARK = "__gora_network_guard__"
# Names the file a child process appends its attempts to; switched per test.
_CHILD_LOG = "PYTEST_GORA_CHILD_NETWORK_LOG"
# The developer's terminal, which Rich, Typer, Click and pytest read: colour, terminal
# type and size. A test that needs one sets it itself.
TERMINAL_ENV = (
    "FORCE_COLOR",  # Rich, pytest
    "NO_COLOR",  # Rich, pytest
    "CLICOLOR",
    "CLICOLOR_FORCE",
    "PY_COLORS",  # pytest, Typer
    "GITHUB_ACTIONS",  # Typer forces colour
    "TERM",
    "COLORTERM",  # Rich: truecolor
    "COLUMNS",  # Rich
    "LINES",  # Rich
    "TERMINAL_WIDTH",  # Typer
    "TTY_COMPATIBLE",  # Rich
    "TTY_INTERACTIVE",  # Rich
    "_TYPER_FORCE_DISABLE_TERMINAL",  # Typer
)


class NetworkBlockedError(OSError):
    """Code tried to reach the network. An OSError, so code under test sees a
    connection failure — the guard records the attempt regardless of handling."""


class ConcurrentSpawnError(RuntimeError):
    """A child process was started while another one was still being created
    (TESTING rule 8). Start them one at a time."""


def _child_guard(guard_dir: str, log_var: str) -> None:
    """The network block in a Python child process, run from its ``sitecustomize``.

    Shipped to the child as source (see ``_HermeticGuard._install_children``), so it
    imports nothing from this file. An attempt is appended to the log the parent named
    in the environment, one line each, then raised as an OSError, as in the parent.
    """
    import importlib.machinery
    import importlib.util
    import os
    import socket
    import sys

    log = os.environ.get(log_var)
    if log:
        real = {name: getattr(socket.socket, name) for name in ("connect", "connect_ex")}
        real_sendto, real_sendmsg = socket.socket.sendto, socket.socket.sendmsg

        def blocked(what: str) -> OSError:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{what} [child pid {os.getpid()} {sys.orig_argv!r}]\n")
            return OSError(f"network blocked in tests: {what}")

        def connecting(name: str) -> Any:
            def method(sock: socket.socket, address: Any) -> Any:
                if sock.family == socket.AF_UNIX:
                    return real[name](sock, address)
                raise blocked(f"{name} {address!r}")

            return method

        def sendto(sock: socket.socket, *args: Any) -> Any:
            if sock.family == socket.AF_UNIX:
                return real_sendto(sock, *args)
            raise blocked(f"sendto {args[-1]!r}")

        def sendmsg(sock: socket.socket, *args: Any, **kwargs: Any) -> Any:
            if sock.family == socket.AF_UNIX:
                return real_sendmsg(sock, *args, **kwargs)
            raise blocked(f"sendmsg {kwargs.get('address', args[3] if len(args) > 3 else None)!r}")

        def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            raise blocked(f"getaddrinfo {host!r}:{port!r}")

        methods = {name: connecting(name) for name in real} | {"sendto": sendto, "sendmsg": sendmsg}
        for name, method in methods.items():
            setattr(socket.socket, name, method)
        socket.getaddrinfo = getaddrinfo

    # A sitecustomize this one shadows on sys.path still runs.
    path = [p for p in sys.path if os.path.abspath(p or os.curdir) != guard_dir]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", path)
    if spec is not None and spec.loader is not None:
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


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
        self._guard_dir = Path(tempfile.mkdtemp(prefix="gora-guard-"))
        atexit.register(shutil.rmtree, self._guard_dir, ignore_errors=True)
        self._session_log = self._guard_dir / "logs" / "session.log"
        self._child_logs: dict[Path, int] = {}  # log -> lines already charged
        self._item_logs: dict[str, Path] = {}
        self.phase = "during collection"
        self.session_attempts: list[str] = []

    def install(self) -> None:
        self._settings.setitem(Settings.model_config, "env_file", None)
        for key in list(os.environ):
            if key.upper().startswith("GORA_") or key in TERMINAL_ENV:
                self._settings.delenv(key)
        get_settings.cache_clear()

        # A pytester session inside this one installs its own guard on top of ours.
        self._nested = getattr(socket.getaddrinfo, _MARK, False)
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        real_sendto = socket.socket.sendto
        real_sendmsg = socket.socket.sendmsg

        def connect(sock: socket.socket, address: Any) -> None:
            if sock.family == socket.AF_UNIX:
                return real_connect(sock, address)
            raise self._blocked(f"connect {address!r}")

        def connect_ex(sock: socket.socket, address: Any) -> int:
            if sock.family == socket.AF_UNIX:
                return real_connect_ex(sock, address)
            raise self._blocked(f"connect_ex {address!r}")

        # Connectionless sends reach the network without connect(): sendto(data[, flags],
        # address) and sendmsg(buffers[, ancdata[, flags[, address]]]).
        def sendto(sock: socket.socket, *args: Any) -> int:
            if sock.family == socket.AF_UNIX:
                return real_sendto(sock, *args)
            raise self._blocked(f"sendto {args[-1]!r}")

        def sendmsg(sock: socket.socket, *args: Any, **kwargs: Any) -> int:
            if sock.family == socket.AF_UNIX:
                return real_sendmsg(sock, *args, **kwargs)
            address = kwargs.get("address", args[3] if len(args) > 3 else None)
            raise self._blocked(f"sendmsg {address!r}")

        def getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            raise self._blocked(f"getaddrinfo {host!r}:{port!r}")

        setattr(getaddrinfo, _MARK, True)
        self._network.setattr(socket.socket, "connect", connect)
        self._network.setattr(socket.socket, "connect_ex", connect_ex)
        self._network.setattr(socket.socket, "sendto", sendto)
        self._network.setattr(socket.socket, "sendmsg", sendmsg)
        self._network.setattr(socket, "getaddrinfo", getaddrinfo)
        self._install_children()

    def _install_children(self) -> None:
        """Every Python child process starts under the block, via its ``sitecustomize``.

        The guard directory leads ``PYTHONPATH`` and the log variable names the file the
        child appends to. Both are in this process's environment, which a child inherits,
        and are added to an ``env`` passed to ``subprocess.Popen``, which it would not.
        """
        self._session_log.parent.mkdir()
        self._child_logs[self._session_log] = 0
        source = inspect.getsource(_child_guard)
        call = f"_child_guard({str(self._guard_dir)!r}, {_CHILD_LOG!r})"
        (self._guard_dir / "sitecustomize.py").write_text(
            f"from typing import Any\n\n\n{source}\n\n{call}\n", encoding="utf-8"
        )
        self._network.setenv("PYTHONPATH", str(self._guard_dir), prepend=os.pathsep)
        self._network.setenv(_CHILD_LOG, str(self._session_log))

        real_init = subprocess.Popen.__init__
        signature = inspect.signature(real_init)
        # Held while one child is being created. Never waited on: a second start refuses.
        creating = threading.Lock()

        def init(popen: subprocess.Popen[Any], *args: Any, **kwargs: Any) -> None:
            if not creating.acquire(blocking=False):
                raise ConcurrentSpawnError(
                    "a child process was started while another was being created "
                    "(TESTING rule 8): start them one at a time"
                )
            try:
                bound = signature.bind(popen, *args, **kwargs)
                env = bound.arguments.get("env")
                if env is not None:
                    bound.arguments["env"] = self._child_env(env)
                real_init(*bound.args, **bound.kwargs)
            finally:
                creating.release()

        self._network.setattr(subprocess.Popen, "__init__", init)

    def _child_env(self, env: Mapping[Any, Any]) -> dict[Any, Any]:
        """``env`` with the guard added, keyed as given: ``str`` or ``bytes``."""
        child = dict(env)
        as_bytes = any(isinstance(key, bytes) for key in child)

        def native(text: str) -> str | bytes:
            return os.fsencode(text) if as_bytes else text

        existing = child.get(native("PYTHONPATH"))
        path = [str(self._guard_dir)] + ([os.fsdecode(existing)] if existing else [])
        child[native("PYTHONPATH")] = native(os.pathsep.join(path))
        child[native(_CHILD_LOG)] = native(os.environ[_CHILD_LOG])
        return child

    @contextmanager
    def _children_log_to(self, log: Path) -> Iterator[None]:
        """Child processes started inside log to ``log``; the previous log after."""
        self._child_logs.setdefault(log, 0)
        previous = os.environ[_CHILD_LOG]
        os.environ[_CHILD_LOG] = str(log)
        try:
            yield
        finally:
            os.environ[_CHILD_LOG] = previous

    def _charge_child_log(self, log: Path) -> list[str]:
        """The lines a child appended to ``log`` since it was last read."""
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        new = lines[self._child_logs[log] :]
        self._child_logs[log] = len(lines)
        return new

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
    def pytest_runtestloop(self) -> Generator[None, object, object]:
        self.phase = "between tests"
        try:
            return (yield)
        finally:
            self.phase = "after the last test"

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Generator[None, object, object]:
        self._item = item
        log = self._item_logs[item.nodeid] = self._session_log.with_name(
            f"test-{len(self._item_logs)}.log"
        )
        try:
            with self._children_log_to(log):
                return (yield)
        finally:
            self._item = None
            self.phase = "between tests"

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(
        self, fixturedef: pytest.FixtureDef[Any]
    ) -> Generator[None, object, object]:
        if fixturedef.scope == "function":
            return (yield)
        self._scoped_fixtures.append(f"fixture {fixturedef.argname!r} ({fixturedef.scope}-scoped)")
        try:
            with self._children_log_to(self._session_log):
                return (yield)
        finally:
            self._scoped_fixtures.pop()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        report = yield
        attempts = []
        if call.when == "teardown":
            attempts = self._item_attempts.pop(item.nodeid, [])
            attempts += self._charge_child_log(self._item_logs[item.nodeid])
        if attempts:
            msg = f"test attempted network access (TESTING rule 6): {attempts}"
            report.longrepr = f"{report.longrepr}\n\n{msg}" if report.failed else msg
            report.outcome = "failed"
        return report

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.phase = "after the last test"
        self._join_session_threads()
        for log in self._child_logs:
            where = "not started by a test" if log == self._session_log else "after its test"
            self.session_attempts += [f"{a} [{where}]" for a in self._charge_child_log(log)]
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


# Tracked .py files under the test paths that are not test modules. Anything else tracked
# there must be collected. Keep it short: every entry is a file the suite-size guard skips.
NOT_TEST_MODULES = frozenset({"__init__.py", "conftest.py"})
NOT_TEST_FILES = frozenset({"tests/audit_fakes.py"})


def _require_pinned_python(root: Path) -> None:
    """The suite runs on the minor version ``.python-version`` pins, and on no other."""
    pin_file = root / ".python-version"
    try:
        pinned = pin_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise pytest.UsageError(f"cannot read the pinned Python version: {exc}") from exc
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if pinned.split(".")[:2] != running.split("."):
        raise pytest.UsageError(
            f"Python {running} found, {pinned} pinned in {pin_file.name}: the suite runs "
            f"on the pinned version only (ADR-0016 rule 5). Interpreter: {sys.executable}"
        )


class _SuiteSizeGuard:
    """Every test module tracked in the git index is collected (ADR-0016 rule 3).

    Compared with the index, not with a remembered number and not with a glob: renaming a
    module out of the ``test_*.py`` pattern drops it from the collection but not from the
    index. An untracked module is collected and not counted, so it cannot hide a missing
    one. Items count before deselection: ``-k``, ``-m`` and ``--deselect`` leave a module
    collected, and a deselection is a gate-defining edit that rule 7 flags.
    """

    def __init__(self, root: Path, testpaths: list[str]) -> None:
        self._root = root
        self._testpaths = testpaths
        self._collected: set[str] = set()

    def tracked(self) -> list[str]:
        """The .py files under the test paths in the git index; git's own configuration
        is left out, so the machine cannot change the answer."""
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        listed = subprocess.run(
            ["git", "-C", str(self._root), "ls-files", "-z", "--", *self._testpaths],
            capture_output=True,
            env=env,
            check=False,
        )
        if listed.returncode != 0:
            stderr = listed.stderr.decode(errors="replace").strip()
            raise pytest.UsageError(
                f"suite-size guard cannot read the git index at {self._root} "
                f"(ADR-0016 rule 3): {stderr}"
            )
        names = os.fsdecode(listed.stdout).split("\0")
        return sorted(n for n in names if n.endswith(".py"))

    def missing(self) -> list[str]:
        return [
            name
            for name in self.tracked()
            if name not in self._collected
            and name not in NOT_TEST_FILES
            and Path(name).name not in NOT_TEST_MODULES
        ]

    def pytest_itemcollected(self, item: pytest.Item) -> None:
        self._collected.add(item.path.relative_to(self._root).as_posix())

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        missing = self.missing()
        if not missing:
            return
        msg = (
            "test modules tracked in the git index but not collected (ADR-0016 rule 3): "
            f"{missing}. Collect them, or list a non-test module in NOT_TEST_FILES."
        )
        report = pytest.TestReport(
            nodeid="suite-size-guard",
            location=("tests/conftest.py", None, "suite-size-guard"),
            keywords={},
            outcome="failed",
            longrepr=msg,
            when="teardown",
        )
        session.config.hook.pytest_runtest_logreport(report=report)
        session.testsfailed += 1
        if session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


# pytest-timeout's method. signal raises in the test and the session goes on; it ended hangs
# under emulation where the thread method's timer never ran (TESTING rule 8).
TIMEOUT_METHOD = "signal"


def _require_test_timeout(config: pytest.Config) -> None:
    """Every test has a timeout, from setup to teardown, by the signal method."""
    plugin = config.pluginmanager.get_plugin("timeout")
    if plugin is None:
        raise pytest.UsageError(
            "pytest-timeout is not active: every test has a timeout (TESTING rule 8)"
        )
    settings = plugin.get_env_settings(config)
    if (
        not settings.timeout
        or settings.timeout <= 0
        or settings.method != TIMEOUT_METHOD
        or settings.func_only
    ):
        raise pytest.UsageError(
            f"every test has a timeout (TESTING rule 8); found timeout={settings.timeout}, "
            f"method={settings.method}, func_only={settings.func_only}. pyproject.toml sets "
            f'timeout and timeout_method = "{TIMEOUT_METHOD}"'
        )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """No test sets its own timeout: the one in pyproject.toml covers every test."""
    marked = [item.nodeid for item in items if item.get_closest_marker("timeout")]
    if marked:
        raise pytest.UsageError(f"a test may not set its own timeout (TESTING rule 8): {marked}")


def pytest_configure(config: pytest.Config) -> None:
    # This file at its tracked place, not a copy a pytester session runs elsewhere.
    is_suite = Path(__file__).resolve() == (config.rootpath / "tests" / "conftest.py").resolve()
    if is_suite:
        _require_pinned_python(config.rootpath)
        _require_test_timeout(config)
    guard = _HermeticGuard()
    # Registered before it installs anything: pytest_unconfigure cannot reach a refused
    # plugin to uninstall it, so a refused guard must leave nothing behind.
    if config.pluginmanager.register(guard, _PLUGIN) is None:  # `-p no:gora-hermetic-guard`
        raise pytest.UsageError(f"{_PLUGIN} cannot be disabled (TESTING rule 6)")
    guard.install()
    if is_suite and config.args_source == pytest.Config.ArgsSource.TESTPATHS:
        suite = _SuiteSizeGuard(config.rootpath, config.getini("testpaths"))
        config.pluginmanager.register(suite, _SUITE_PLUGIN)


def pytest_unconfigure(config: pytest.Config) -> None:
    guard = config.pluginmanager.get_plugin(_PLUGIN)
    if isinstance(guard, _HermeticGuard):
        guard.uninstall()
        config.pluginmanager.unregister(guard)
    suite = config.pluginmanager.get_plugin(_SUITE_PLUGIN)
    if suite is not None:
        config.pluginmanager.unregister(suite)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Read settings fresh in every test — get_settings is lru_cached."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HOME, the XDG directories and the audit journal of this test, all under tmp_path.

    Set in ``os.environ`` through monkeypatch, so the test's own code and every child
    process it starts see them. A test that needs another location sets it itself.
    """
    root = tmp_path / "isolated"
    home = root / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("GORA_AUDIT_LOG", str(root / "audit" / "audit.jsonl"))
    return root

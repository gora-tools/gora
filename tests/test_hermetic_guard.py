"""Calibration of the conftest guards (ADR-0016 rules 1-2): each one is shown to bite.

A guard that asserts an absence — no network, no `.env` — first proves it registers
the presence, otherwise its silence is not evidence.
"""

import os
import socket
import subprocess
from pathlib import Path

import pytest
import typer.rich_utils

from gora.config.settings import Settings
from tests import conftest

CONFTEST = Path(__file__).with_name("conftest.py")


def _run_isolated(pytester: pytest.Pytester, test_source: str) -> pytest.RunResult:
    """Run one test file in a separate pytester session under this conftest.

    The guard has no API a test could use to read or clear it, so it is calibrated
    from outside: the inner session's outcome is what shows it registered an attempt.
    """
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(test_source)
    return pytester.runpytest("-p", "no:cacheprovider")


# A plugin of an inner session that tells a test's thread when the run loop has ended, so the
# thread reaches out in the phase the test names, fixed by synchronization and not by a sleep.
# Outermost (tryfirst), its end comes after the guard's, which sets the phase.
LOOP_PLUGIN = """
import threading

import pytest

LOOP_ENDED = threading.Event()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtestloop():
    try:
        return (yield)
    finally:
        LOOP_ENDED.set()
"""


def _assert_session_red(result: pytest.RunResult, passed: int, where: str) -> None:
    """The attempt fails the session, names where it happened, and blames no test."""
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(passed=passed, errors=1)
    result.stdout.fnmatch_lines(
        [f"*network access not charged to any test (TESTING rule 6)*{where}*"]
    )
    result.stdout.no_fnmatch_line("*test attempted network access*")


def test_guard_registers_a_connection_attempt(pytester: pytest.Pytester) -> None:
    result = _run_isolated(
        pytester,
        """
        import socket

        def test_connects():
            socket.create_connection(("127.0.0.1", 9), timeout=1)
        """,
    )
    result.assert_outcomes(failed=1, errors=1)
    result.stdout.fnmatch_lines(["*NetworkBlockedError: network blocked in tests: getaddrinfo*"])


def test_guard_fails_a_test_whose_code_swallowed_the_attempt(pytester: pytest.Pytester) -> None:
    result = _run_isolated(
        pytester,
        """
        import contextlib

        import httpx

        def test_swallows_network_error():
            with contextlib.suppress(Exception):
                httpx.post("https://slack.com/api/chat.postMessage", json={})
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*attempted network access (TESTING rule 6)*"])


def test_guard_survives_monkeypatch_undo(pytester: pytest.Pytester) -> None:
    result = _run_isolated(
        pytester,
        """
        import contextlib
        import socket

        def test_undoes_then_connects(monkeypatch):
            monkeypatch.undo()
            with contextlib.suppress(OSError), socket.socket() as s:
                s.connect(("127.0.0.1", 9))
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*attempted network access (TESTING rule 6)*127.0.0.1*"])


@pytest.mark.parametrize(
    ("call", "shown"),
    [
        ('s.sendto(b"x", ("127.0.0.1", 9))', "sendto ('127.0.0.1', 9)"),
        ('s.sendmsg([b"x"], [], 0, ("127.0.0.1", 9))', "sendmsg ('127.0.0.1', 9)"),
    ],
)
def test_guard_fails_a_test_that_sends_a_datagram(
    pytester: pytest.Pytester, call: str, shown: str
) -> None:
    result = _run_isolated(
        pytester,
        f"""
        import contextlib
        import socket

        def test_sends_udp():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            with contextlib.suppress(OSError), s:
                {call}
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines([f"*test attempted network access (TESTING rule 6)*{shown}*"])


# The resolver entry points besides getaddrinfo, each with a call that is answered from the
# hosts file when unblocked (loopback only: the defect must not reach a name server), and
# how the guard names the attempt.
LOOKUPS = {
    "gethostbyname": ('socket.gethostbyname("localhost")', "gethostbyname 'localhost'"),
    "gethostbyname_ex": (
        'socket.gethostbyname_ex("localhost")',
        "gethostbyname_ex 'localhost'",
    ),
    "gethostbyaddr": ('socket.gethostbyaddr("127.0.0.1")', "gethostbyaddr '127.0.0.1'"),
    "getnameinfo": ('socket.getnameinfo(("127.0.0.1", 80), 0)', "getnameinfo ('127.0.0.1', 80)"),
    "getfqdn": ('socket.getfqdn("localhost")', "getfqdn 'localhost'"),
}


def test_every_name_lookup_the_guard_blocks_has_a_case() -> None:
    assert set(LOOKUPS) == set(conftest.NAME_LOOKUPS)


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_guard_fails_a_test_that_looks_a_name_up(pytester: pytest.Pytester, name: str) -> None:
    call, shown = LOOKUPS[name]
    result = _run_isolated(
        pytester,
        f"""
        import contextlib
        import socket

        def test_looks_up():
            with contextlib.suppress(OSError):
                {call}
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines([f"*test attempted network access (TESTING rule 6)*{shown}*"])


@pytest.mark.parametrize("name", sorted(LOOKUPS))
def test_guard_fails_a_test_whose_child_looked_a_name_up(
    pytester: pytest.Pytester, name: str
) -> None:
    call, shown = LOOKUPS[name]
    code = f"import contextlib, socket\nwith contextlib.suppress(OSError):\n    {call}\n"
    result = _run_isolated(
        pytester,
        f"""
        import subprocess
        import sys

        def test_child_looks_up():
            subprocess.run([sys.executable, "-c", {code!r}], check=True)
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    # The list the report prints escapes the quotes of an entry that has both kinds.
    argument = shown.split(" ", 1)[1].replace("'", "*")
    result.stdout.fnmatch_lines(
        [f"*test attempted network access (TESTING rule 6)*{name} {argument}*child pid*"]
    )


def test_guard_fails_the_session_on_an_attempt_at_import(pytester: pytest.Pytester) -> None:
    result = _run_isolated(
        pytester,
        """
        import contextlib
        import socket

        with contextlib.suppress(OSError), socket.socket() as s:
            s.connect(("127.0.0.1", 9))

        def test_ok():
            pass
        """,
    )
    _assert_session_red(result, passed=1, where="[during collection]")


@pytest.mark.parametrize("scope", ["session", "module"])
def test_guard_fails_the_session_on_an_attempt_in_a_scoped_fixture(
    pytester: pytest.Pytester, scope: str
) -> None:
    result = _run_isolated(
        pytester,
        f"""
        import contextlib
        import socket

        import pytest

        @pytest.fixture(scope="{scope}")
        def leaky():
            with contextlib.suppress(OSError), socket.socket() as s:
                s.connect(("127.0.0.1", 9))

        def test_uses_leaky(leaky):
            pass
        """,
    )
    _assert_session_red(result, passed=1, where=f"fixture 'leaky' ({scope}-scoped)")


def test_guard_fails_the_session_on_a_thread_after_the_last_test(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(loop_plugin=LOOP_PLUGIN)
    pytester.syspathinsert()
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        """
        import contextlib
        import socket
        import threading

        from loop_plugin import LOOP_ENDED

        def _late():
            LOOP_ENDED.wait()
            with contextlib.suppress(OSError), socket.socket() as s:
                s.connect(("127.0.0.1", 9))

        def test_spawns_thread():
            threading.Thread(target=_late).start()
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-p", "loop_plugin")
    _assert_session_red(result, passed=1, where="after the last test")


def test_guard_blames_the_session_not_the_neighbour_for_a_thread(
    pytester: pytest.Pytester,
) -> None:
    result = _run_isolated(
        pytester,
        """
        import contextlib
        import socket
        import threading

        NEIGHBOUR_RUNS, REACHED_OUT = threading.Event(), threading.Event()

        def _late():
            NEIGHBOUR_RUNS.wait()
            with contextlib.suppress(OSError), socket.socket() as s:
                s.connect(("127.0.0.1", 9))
            REACHED_OUT.set()

        def test_a_spawns_thread():
            threading.Thread(target=_late).start()

        def test_b_neighbour():
            NEIGHBOUR_RUNS.set()
            REACHED_OUT.wait()
        """,
    )
    _assert_session_red(result, passed=2, where="while *test_b_neighbour ran")


def installed() -> dict[str, object]:
    """Everything the guard's ``install()`` changes, as it stands: each patch, the settings
    file and every variable it sets or removes. Equal before and after an inner session
    when that session left nothing of its guard behind."""
    removed = [
        name
        for name in os.environ
        if name.upper().startswith("GORA_") or name in conftest.TERMINAL_ENV
    ]
    return {
        "socket.socket.connect": socket.socket.connect,
        "socket.socket.connect_ex": socket.socket.connect_ex,
        "socket.socket.sendto": socket.socket.sendto,
        "socket.socket.sendmsg": socket.socket.sendmsg,
        "socket.getaddrinfo": socket.getaddrinfo,
        **{f"socket.{name}": getattr(socket, name) for name in conftest.NAME_LOOKUPS},
        "subprocess.Popen.__init__": subprocess.Popen.__init__,
        "Settings.model_config['env_file']": Settings.model_config.get("env_file"),
        **{name: os.environ.get(name) for name in ("PYTHONPATH", conftest._CHILD_LOG)},
        **{name: os.environ[name] for name in sorted(removed)},
    }


def test_nested_session_leaves_the_outer_guard_in_place(pytester: pytest.Pytester) -> None:
    before = installed()
    assert getattr(socket.getaddrinfo, "__gora_network_guard__", False)
    _run_isolated(pytester, "def test_ok():\n    pass\n").assert_outcomes(passed=1)
    assert installed() == before


def test_guard_plugin_cannot_be_disabled_from_the_command_line(pytester: pytest.Pytester) -> None:
    before = installed()
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile("def test_ok():\n    pass\n")
    result = pytester.runpytest("-p", "no:cacheprovider", "-p", "no:gora-hermetic-guard")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*gora-hermetic-guard cannot be disabled*"])
    assert installed() == before  # a refused guard leaves nothing installed


def test_the_session_still_charges_a_test_after_a_refused_inner_session(
    pytester: pytest.Pytester,
) -> None:
    """A refused inner session in this process, then a later test of the same session that
    reaches out itself and through a child, each swallowing the error: the session the
    later test runs in charges both to it."""
    result = _run_isolated(
        pytester,
        f"""
        import contextlib
        import socket
        import subprocess
        import sys
        from pathlib import Path

        import pytest

        def test_a_refused_inner_session(pytester):
            pytester.makeconftest(Path({str(CONFTEST)!r}).read_text())
            pytester.makepyfile("def test_ok():\\n    pass\\n")
            result = pytester.runpytest("-p", "no:cacheprovider", "-p", "no:gora-hermetic-guard")
            assert result.ret == pytest.ExitCode.USAGE_ERROR

        def test_b_reaches_out_itself():
            with contextlib.suppress(OSError), socket.socket() as s:
                s.connect(("127.0.0.1", 9))

        def test_c_reaches_out_through_a_child():
            subprocess.run([sys.executable, "-c", {_CHILD_CONNECTS!r}], check=True)
        """,
    )
    result.assert_outcomes(passed=3, errors=2)
    result.stdout.fnmatch_lines(
        [
            "*ERROR at teardown of test_b_reaches_out_itself*",
            "*test attempted network access (TESTING rule 6)*connect ('127.0.0.1', 9)*",
            "*ERROR at teardown of test_c_reaches_out_through_a_child*",
            "*test attempted network access (TESTING rule 6)*connect*127.0.0.1*9*child pid*",
        ]
    )
    result.stdout.no_fnmatch_line("*network access not charged to any test*")


def test_settings_are_isolated_in_a_session_fixture(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A subprocess: in-process, this session's own isolation would mask the inner one.
    (pytester.path / ".env").write_text("GORA_SLACK_ENABLED=true\nGORA_SLACK_CHANNEL=CDOTENV\n")
    monkeypatch.setenv("GORA_SLACK_CHANNEL", "CSHELL")
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        """
        import pytest

        from gora.config.settings import Settings

        @pytest.fixture(scope="session")
        def settings():
            return Settings()

        def test_session_fixture_sees_code_defaults(settings):
            assert settings.slack_enabled is False
            assert settings.slack_channel == ""
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)


# A product change that gives Settings a settings file, loaded before the guard installs.
DECLARES_ENV_FILE = """
from gora.config.settings import Settings

Settings.model_config["env_file"] = ".env"
"""


def test_the_env_file_belt_holds_when_settings_declares_a_file(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Settings`` declares no ``env_file`` today, so no test shows the belt (``install`` sets
    it to None) while nothing reads one. An inner session in which it does declare one, with a
    ``.env`` in its directory, sees code defaults only while the belt is on."""
    monkeypatch.delenv("GORA_SLACK_CHANNEL", raising=False)
    (pytester.path / ".env").write_text("GORA_SLACK_ENABLED=true\nGORA_SLACK_CHANNEL=CDOTENV\n")
    pytester.makepyfile(declares_env_file=DECLARES_ENV_FILE)
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        """
        from gora.config.settings import Settings

        def test_dotenv_is_not_read():
            assert Settings.model_config["env_file"] is None
            settings = Settings()
            assert settings.slack_enabled is False
            assert settings.slack_channel == ""
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "declares_env_file")
    result.assert_outcomes(passed=1)


def test_settings_ignore_dotenv_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("GORA_SLACK_ENABLED=true\nGORA_SLACK_CHANNEL=CDOTENV\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.slack_enabled is False
    assert settings.slack_channel == ""


def test_no_gora_env_reaches_tests() -> None:
    # GORA_AUDIT_LOG is the one the per-test isolation sets (conftest `_isolated_home`),
    # so a test never writes a journal to the real home; nothing else gets through.
    assert [k for k in os.environ if k.upper().startswith("GORA_")] == ["GORA_AUDIT_LOG"]


# A child that connects and swallows the error, then exits 0 as if nothing happened.
_CHILD_CONNECTS = (
    "import contextlib, socket\n"
    "with contextlib.suppress(OSError), socket.socket() as s:\n"
    "    s.connect(('127.0.0.1', 9))\n"
)


@pytest.mark.parametrize(
    "env",
    ["None", "{'PATH': os.environ.get('PATH', '')}"],
    ids=["inherited-env", "explicit-env"],
)
def test_guard_fails_a_test_whose_child_process_swallowed_the_attempt(
    pytester: pytest.Pytester, env: str
) -> None:
    result = _run_isolated(
        pytester,
        f"""
        import os
        import subprocess
        import sys

        def test_child_connects():
            subprocess.run([sys.executable, "-c", {_CHILD_CONNECTS!r}], env={env}, check=True)
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        ["*test attempted network access (TESTING rule 6)*connect*127.0.0.1*9*child pid*"]
    )


def test_guard_fails_the_session_on_a_child_process_at_import(pytester: pytest.Pytester) -> None:
    result = _run_isolated(
        pytester,
        f"""
        import subprocess
        import sys

        subprocess.run([sys.executable, "-c", {_CHILD_CONNECTS!r}], check=True)

        def test_ok():
            pass
        """,
    )
    _assert_session_red(result, passed=1, where="child pid*not started by a test")


def test_child_guard_still_runs_a_sitecustomize_it_shadows(pytester: pytest.Pytester) -> None:
    shadowed = pytester.mkdir("shadowed")
    (shadowed / "sitecustomize.py").write_text("import os\nos.environ['SHADOWED_RAN'] = '1'\n")
    result = _run_isolated(
        pytester,
        f"""
        import os
        import subprocess
        import sys

        def test_child_runs_both():
            env = {{**os.environ, "PYTHONPATH": {str(shadowed)!r}}}
            code = {_CHILD_CONNECTS!r} + "print(os.environ.get('SHADOWED_RAN'))"
            out = subprocess.run([sys.executable, "-c", "import os\\n" + code], env=env,
                                 capture_output=True, text=True, check=True).stdout
            assert out == "1\\n"
        """,
    )
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*test attempted network access (TESTING rule 6)*child pid*"])


def test_no_terminal_env_reaches_tests() -> None:
    # The conftest's own tuple, every entry: a second list here would drift from it.
    assert conftest.TERMINAL_ENV
    assert [k for k in conftest.TERMINAL_ENV if k in os.environ] == []


def test_typer_did_not_read_a_terminal_variable_at_import() -> None:
    # Typer decides at import whether to force colour; the guard runs before that import.
    assert typer.rich_utils.FORCE_TERMINAL is None


def test_terminal_env_is_removed_for_the_session_and_its_children(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A subprocess: the inner session starts from an environment that has every variable.
    for name in conftest.TERMINAL_ENV:
        monkeypatch.setenv(name, "dumb" if name == "TERM" else "1")
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        f"""
        import os
        import subprocess
        import sys

        NAMES = {list(conftest.TERMINAL_ENV)!r}
        CHILD = f"import os; print([n for n in {{NAMES!r}} if n in os.environ])"

        def test_session_has_none():
            assert [n for n in NAMES if n in os.environ] == []

        def test_child_inherits_none():
            run = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True)
            assert run.stdout == "[]\\n"
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=2)


def test_a_child_started_while_another_is_being_created_is_refused(
    pytester: pytest.Pytester,
) -> None:
    """TESTING rule 8: one child at a time. The guard reads a child's ``env`` while it creates
    that child, so an ``env`` that starts a second child when it is read holds the first
    creation open there, by the test's own code: no thread, no sleep, no ``preexec_fn``. The
    second start is refused; then the first is created and ends, and the next one starts. In
    a session of its own, so the wrapper it meets is the one this conftest installs."""
    result = _run_isolated(
        pytester,
        """
        import os
        import subprocess
        import sys
        from collections.abc import Mapping

        import pytest

        from conftest import ConcurrentSpawnError

        ARGV = [sys.executable, "-c", "pass"]

        class StartsAChildWhileRead(Mapping):
            def __init__(self):
                self.env, self.refused = dict(os.environ), []

            def __getitem__(self, key):
                return self.env[key]

            def __len__(self):
                return len(self.env)

            def __iter__(self):
                if not self.refused:
                    with pytest.raises(ConcurrentSpawnError, match="TESTING rule 8") as second:
                        subprocess.Popen(ARGV)
                    self.refused.append(second.value)
                return iter(self.env)

        def test_one_child_at_a_time():
            env = StartsAChildWhileRead()
            first = subprocess.Popen(ARGV, env=env)
            assert first.wait(timeout=60) == 0
            assert len(env.refused) == 1
            assert first.poll() == 0  # nothing of it left running
            assert subprocess.run(ARGV, check=False, timeout=60).returncode == 0
        """,
    )
    result.assert_outcomes(passed=1)

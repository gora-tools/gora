"""Calibration of the conftest guards (ADR-0016 rules 1-2): each one is shown to bite.

A guard that asserts an absence — no network, no `.env` — first proves it registers
the presence, otherwise its silence is not evidence.
"""

import os
import socket
from pathlib import Path

import pytest

from gora.config.settings import Settings

CONFTEST = Path(__file__).with_name("conftest.py")


def _run_isolated(pytester: pytest.Pytester, test_source: str) -> pytest.RunResult:
    """Run one test file in a separate pytester session under this conftest.

    The guard has no API a test could use to read or clear it, so it is calibrated
    from outside: the inner session's outcome is what shows it registered an attempt.
    """
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(test_source)
    return pytester.runpytest("-p", "no:cacheprovider")


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
    result = _run_isolated(
        pytester,
        """
        import contextlib
        import socket
        import threading
        import time

        def _late():
            time.sleep(0.1)
            with contextlib.suppress(OSError), socket.socket() as s:
                s.connect(("127.0.0.1", 9))

        def test_spawns_thread():
            threading.Thread(target=_late).start()
        """,
    )
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
        import time

        def _late():
            time.sleep(0.1)
            with contextlib.suppress(OSError), socket.socket() as s:
                s.connect(("127.0.0.1", 9))

        def test_a_spawns_thread():
            threading.Thread(target=_late).start()

        def test_b_neighbour():
            time.sleep(0.3)
        """,
    )
    _assert_session_red(result, passed=2, where="while *test_b_neighbour ran")


def test_nested_session_leaves_the_outer_guard_in_place(pytester: pytest.Pytester) -> None:
    def installed() -> tuple[object, ...]:
        return (
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.getaddrinfo,
            Settings.model_config.get("env_file"),
        )

    before = installed()
    assert getattr(socket.getaddrinfo, "__gora_network_guard__", False)
    _run_isolated(pytester, "def test_ok():\n    pass\n").assert_outcomes(passed=1)
    assert installed() == before


def test_guard_plugin_cannot_be_disabled_from_the_command_line(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile("def test_ok():\n    pass\n")
    result = pytester.runpytest("-p", "no:cacheprovider", "-p", "no:gora-hermetic-guard")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*gora-hermetic-guard cannot be disabled*"])


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


def test_settings_ignore_dotenv_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("GORA_SLACK_ENABLED=true\nGORA_SLACK_CHANNEL=CDOTENV\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.slack_enabled is False
    assert settings.slack_channel == ""


def test_no_gora_env_reaches_tests() -> None:
    assert [k for k in os.environ if k.upper().startswith("GORA_")] == []


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

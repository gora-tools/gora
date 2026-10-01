"""Calibration of the conftest guards (ADR-0016 rules 1-2): each one is shown to bite.

A guard that asserts an absence — no network, no `.env` — first proves it registers
the presence, otherwise its silence is not evidence.
"""

import os
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


def test_settings_ignore_dotenv_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("GORA_SLACK_ENABLED=true\nGORA_SLACK_CHANNEL=CDOTENV\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.slack_enabled is False
    assert settings.slack_channel == ""


def test_no_gora_env_reaches_tests() -> None:
    assert [k for k in os.environ if k.upper().startswith("GORA_")] == []

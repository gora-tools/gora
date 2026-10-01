"""Calibration of the conftest guards (ADR-0016 rules 1-2): each one is shown to bite.

A guard that asserts an absence — no network, no `.env` — first proves it registers
the presence, otherwise its silence is not evidence.
"""

import contextlib
import os
from pathlib import Path

import httpx
import pytest

from gora.config.settings import Settings

CONFTEST = Path(__file__).with_name("conftest.py")


def test_guard_registers_swallowed_connection_attempt(network_attempts: list[str]) -> None:
    with contextlib.suppress(Exception):  # swallowed, as the best-effort Slack notify does
        httpx.post("https://slack.com/api/chat.postMessage", json={})
    assert network_attempts, "guard did not register the connection attempt"
    network_attempts.clear()  # this test is the one that is allowed to try


def test_guard_fails_a_test_whose_code_swallowed_the_attempt(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        """
        import httpx

        def test_swallows_network_error():
            try:
                httpx.post("https://slack.com/api/chat.postMessage", json={})
            except Exception:
                pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*attempted network access (TESTING rule 6)*"])


def test_settings_ignore_dotenv_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("GORA_SLACK_ENABLED=true\nGORA_SLACK_CHANNEL=CDOTENV\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.slack_enabled is False
    assert settings.slack_channel == ""


def test_no_gora_env_reaches_tests() -> None:
    assert [k for k in os.environ if k.upper().startswith("GORA_")] == []

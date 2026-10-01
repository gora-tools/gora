"""A notify that does not happen is visible (ADR-0015).

Three states, for every mutating command, through the real notifier and adapter over
``httpx.MockTransport``:

- Slack not enabled: silent, the CLI-only default;
- enabled, but the bot token or the channel empty: one line on stderr naming what is empty;
- a post that went out: one dim line on stdout naming the channel.

No token value appears on either stream, in any state.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

import gora.transport.cli.commands as commands
from gora.config.settings import get_settings
from gora.services.notifier import Notifier
from gora.transport.cli.commands import app
from tests.audit_fakes import DIGEST_B, IMAGE, Remote, seed_manifest
from tests.integration.test_audit_secrets import leaks, secret_variables

runner = CliRunner()

TOKEN_VAR, CHANNEL_VAR = "GORA_SLACK_BOT_TOKEN", "GORA_SLACK_CHANNEL"
SKIPPED = "(slack notify skipped: GORA_SLACK_ENABLED is true but {names} empty)"


def _args(command: str, manifest: str) -> list[str]:
    return {
        "cut-rc": ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.2", "-d", DIGEST_B, "-m", manifest],
        "promote": ["promote", "dev", "-m", manifest],
        "promote-open-pr": ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", manifest],
        "rollback": [
            "rollback", "dev", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", manifest
        ],
    }[command]  # fmt: skip


COMMANDS = ["cut-rc", "promote", "promote-open-pr", "rollback"]


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Remote:
    fake = Remote()
    fake.install(monkeypatch)
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "fake-github-token")
    return fake


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote: Remote) -> Callable[..., Result]:
    """Run one command with the given Slack environment; the setup runs with Slack off."""

    def go(command: str, env: dict[str, str]) -> Result:
        manifest = tmp_path / "release-manifest.yaml"
        seed_manifest(manifest)
        if command == "rollback":  # there must be a stage to roll back
            assert runner.invoke(app, ["promote", "dev", "-m", str(manifest)]).exit_code == 0
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()  # the setup command may have cached Settings with Slack off
        before = len(remote.slack_requests)
        result = runner.invoke(app, _args(command, str(manifest)))
        assert result.exit_code == 0, result.output
        go.requests = len(remote.slack_requests) - before  # type: ignore[attr-defined]
        return result

    return go


def _slack_lines(stream: str) -> list[str]:
    return [line for line in stream.splitlines() if "slack" in line.lower()]


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(
    "env",
    [
        {},
        {"GORA_SLACK_ENABLED": "false", TOKEN_VAR: "xoxb-x", CHANNEL_VAR: "C1"},
        {TOKEN_VAR: "xoxb-x", CHANNEL_VAR: "C1"},
        {"GORA_SLACK_ENABLED": "false"},
    ],
    ids=["unset", "false-and-configured", "unset-and-configured", "false-and-empty"],
)
def test_not_enabled_is_silent(
    run: Callable[..., Result], command: str, env: dict[str, str]
) -> None:
    result = run(command, env)
    assert _slack_lines(result.stdout) == []
    assert _slack_lines(result.stderr) == []
    assert run.requests == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(
    ("env", "names"),
    [
        ({TOKEN_VAR: "", CHANNEL_VAR: "C1"}, TOKEN_VAR),
        ({TOKEN_VAR: "xoxb-x", CHANNEL_VAR: ""}, CHANNEL_VAR),
        ({TOKEN_VAR: "", CHANNEL_VAR: ""}, f"{TOKEN_VAR} and {CHANNEL_VAR}"),
        ({}, f"{TOKEN_VAR} and {CHANNEL_VAR}"),
    ],
    ids=["token-missing", "channel-missing", "both-empty", "both-unset"],
)
def test_enabled_but_incomplete_says_so_once_on_stderr(
    run: Callable[..., Result], command: str, env: dict[str, str], names: str
) -> None:
    result = run(command, {"GORA_SLACK_ENABLED": "true", **env})
    assert result.stderr.splitlines().count(SKIPPED.format(names=names)) == 1
    assert _slack_lines(result.stderr) == [SKIPPED.format(names=names)]
    assert _slack_lines(result.stdout) == []
    assert run.requests == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize("command", COMMANDS)
def test_a_post_is_reported_once_on_stdout(run: Callable[..., Result], command: str) -> None:
    result = run(command, {"GORA_SLACK_ENABLED": "true", TOKEN_VAR: "xoxb-x", CHANNEL_VAR: "C1"})
    assert result.stdout.splitlines().count("slack: posted to C1") == 1
    assert _slack_lines(result.stdout) == ["slack: posted to C1"]
    assert _slack_lines(result.stderr) == []
    assert run.requests == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize("command", COMMANDS)
def test_a_hostile_channel_id_is_shown_escaped(run: Callable[..., Result], command: str) -> None:
    hostile = "C1\x1b[2J\nposted\N{RIGHT-TO-LEFT OVERRIDE}"
    result = run(command, {"GORA_SLACK_ENABLED": "true", TOKEN_VAR: "xoxb-x", CHANNEL_VAR: hostile})
    assert "slack: posted to C1\\x1b[2J\\x0aposted\\u202e" in result.stdout.splitlines()
    assert "\x1b" not in result.stdout
    assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in result.stdout


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("state", ["off", "skipped", "posted"])
def test_no_token_value_reaches_either_stream(
    run: Callable[..., Result],
    remote: Remote,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    state: str,
) -> None:
    sentinels = secret_variables()
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    env = {
        "off": {},
        "skipped": {"GORA_SLACK_ENABLED": "true"},  # token set, channel missing
        "posted": {"GORA_SLACK_ENABLED": "true", CHANNEL_VAR: "C1"},
    }[state]
    result = run(command, env)
    assert leaks(sentinels, result.stdout.encode(), result.stderr.encode()) == []
    if state == "posted":
        # Calibration: the token really is in the process, so an absence above means something.
        assert f"Bearer {sentinels[TOKEN_VAR]}" in remote.authorization_headers()


def test_a_failed_post_is_not_reported_as_posted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Down:
        def post_message(self, channel: str, notification: object) -> object:
            raise RuntimeError("slack down")

    monkeypatch.setattr(commands, "build_notifier", lambda _s: Notifier(_Down(), "C1"))  # type: ignore[arg-type]
    manifest = tmp_path / "release-manifest.yaml"
    seed_manifest(manifest)
    result = runner.invoke(app, ["promote", "dev", "-m", str(manifest)])
    assert result.exit_code == 0
    assert _slack_lines(result.stdout) == []
    assert "  (slack notify failed: slack down)" in result.stderr.splitlines()

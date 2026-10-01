"""Integration tests for the `gora promote` CLI command."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import gora.transport.cli.commands as commands
from gora.adapters.slack import SlackMessage
from gora.domain.notification import Notification
from gora.domain.release.manifest import ReleaseManifest
from gora.services.notifier import Notifier
from gora.transport.cli.commands import app

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
IMAGE = "ghcr.io/test/service"

runner = CliRunner()


def _cut(path: Path, version: str = "1.2.0-rc.1", digest: str = DIGEST_A) -> None:
    runner.invoke(
        app,
        ["cut-rc", "-i", IMAGE, "-v", version, "-d", digest, "-m", str(path)],
    )


def test_promote_after_cut_rc(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)

    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert "Promoted to" in result.output

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.stages["dev"].digest == DIGEST_A
    assert loaded.stages["dev"].version == "1.2.0-rc.1"


def test_promote_to_new_stage(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)

    runner.invoke(app, ["promote", "dev", "-m", str(path)])
    result = runner.invoke(app, ["promote", "staging", "-m", str(path)])

    assert result.exit_code == 0, result.output
    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.stages["staging"].digest == DIGEST_A


def test_promote_without_manifest_fails(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])

    assert result.exit_code == 1
    assert "No manifest" in result.output


def test_promote_notifies_slack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful promote posts a formatted event to the notifier."""
    path = tmp_path / "release-manifest.yaml"
    _cut(path)

    posted: list[tuple[str, Notification]] = []

    class _FakePoster:
        def post_message(self, channel: str, notification: Notification) -> SlackMessage:
            posted.append((channel, notification))
            return SlackMessage(channel=channel, ts="1.1")

    monkeypatch.setattr(
        commands,
        "build_notifier",
        lambda _s: Notifier(_FakePoster(), "C1", enabled=True),
    )

    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert posted, "notifier should have posted a message"
    channel, notification = posted[0]
    assert channel == "C1"
    assert "Promoted" in notification.summary and "dev" in notification.summary


def test_promote_notify_failure_does_not_fail_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notifications are best-effort: a Slack failure never fails the release."""
    path = tmp_path / "release-manifest.yaml"
    _cut(path)

    class _BoomPoster:
        def post_message(self, channel: str, text: str) -> SlackMessage:
            raise RuntimeError("slack down")

    monkeypatch.setattr(
        commands,
        "build_notifier",
        lambda _s: Notifier(_BoomPoster(), "C1", enabled=True),
    )

    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert "Promoted to" in result.output
    assert "slack notify failed" in result.output

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.stages["dev"].digest == DIGEST_A


def test_promote_notify_build_failure_does_not_fail_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure *building* the notifier (e.g. malformed GORA_* env) is swallowed too.

    Guards the whole notify path, not just the emit — the release is already
    persisted before _notify runs, so nothing here may fail the command.
    """
    path = tmp_path / "release-manifest.yaml"
    _cut(path)

    def _boom(_settings: object) -> Notifier:
        raise RuntimeError("bad GORA_ env")

    monkeypatch.setattr(commands, "build_notifier", _boom)

    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert "Promoted to" in result.output
    assert "slack notify failed" in result.output

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.stages["dev"].digest == DIGEST_A

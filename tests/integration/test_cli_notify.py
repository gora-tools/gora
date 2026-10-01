"""The CLI's notifications reach Slack as plain, literal messages (ADR-015).

Real notifier and real Slack adapter, over ``httpx.MockTransport``: what a release
command sends is the JSON body asserted here, never a live call.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import gora.adapters.github as github
import gora.transport.cli.commands as commands
from gora.adapters.slack import SlackAdapter
from gora.services.notifier import Notifier
from gora.transport.cli.commands import app
from tests.integration.test_cli_literal_output import FakeGitHub

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIGEST_B = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
IMAGE = "ghcr.io/test/service"

runner = CliRunner()


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every chat.postMessage body the CLI sends, with a notifier that is switched on."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = Notifier(SlackAdapter("xoxb-fake-0000", client=client), "C1")
    monkeypatch.setattr(commands, "build_notifier", lambda _settings: notifier)
    return bodies


def _cut(path: Path) -> None:
    result = runner.invoke(
        app, ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-m", str(path)]
    )
    assert result.exit_code == 0, result.output


def _texts(body: dict[str, Any]) -> tuple[str, str]:
    section, context = body["blocks"]
    return section["text"]["text"], context["elements"][0]["text"]


def test_cut_rc_posts_plain_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    _cut(tmp_path / "release-manifest.yaml")
    assert len(posted) == 1
    assert _texts(posted[0]) == ("Cut RC 1.2.0-rc.1", f"Digest {DIGEST_A}")
    assert posted[0]["text"] == f"Cut RC 1.2.0-rc.1. Digest {DIGEST_A}"


def test_promote_posts_plain_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])
    assert result.exit_code == 0, result.output
    assert [_texts(b) for b in posted] == [("Promoted 1.2.0-rc.1 to dev", f"Digest {DIGEST_A}")]


def test_rollback_posts_plain_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    runner.invoke(app, ["promote", "prod", "-m", str(path)])
    posted.clear()
    args = ["rollback", "prod", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert [_texts(b) for b in posted] == [("Rolled back prod to 1.1.9", f"Digest {DIGEST_B}")]


def test_promotion_pr_posts_the_digest_and_a_link(
    tmp_path: Path, posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github, "GitHubAdapter", FakeGitHub)
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "fake-token")
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    args = ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", str(path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert len(posted) == 1
    assert _texts(posted[0]) == ("Promotion PR for 1.2.0-rc.1 to dev", f"Digest {DIGEST_A}")
    link = posted[0]["blocks"][1]["elements"][1]
    assert link["text"] == "<https://github.com/o/r/pull/42|Open pull request>"


def test_a_hostile_stage_cannot_ping_or_format(
    tmp_path: Path, posted: list[dict[str, Any]]
) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    result = runner.invoke(app, ["promote", "<!here> *x* :rocket:", "-m", str(path)])
    assert result.exit_code == 0, result.output
    assert len(posted) == 1
    summary, _ = _texts(posted[0])
    assert summary == "Promoted 1.2.0-rc.1 to <!here> *x* :rocket:"  # literal in plain_text
    assert posted[0]["text"].startswith("Promoted 1.2.0-rc.1 to &lt;!here&gt; *x* :rocket:.")
    assert posted[0]["mrkdwn"] is False
    assert posted[0]["blocks"][0]["text"] == {"type": "plain_text", "text": summary, "emoji": False}

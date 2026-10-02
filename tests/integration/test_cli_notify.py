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
    notifier = Notifier(SlackAdapter("fake-sentinel-0000", client=client), "C1")
    monkeypatch.setattr(commands, "build_notifier", lambda _settings: notifier)
    return bodies


def _cut(path: Path) -> None:
    result = runner.invoke(
        app, ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-m", str(path)]
    )
    assert result.exit_code == 0, result.output


def _elements(body: dict[str, Any]) -> list[dict[str, Any]]:
    return body["blocks"][0]["elements"][0]["elements"]  # type: ignore[no-any-return]


def _message(body: dict[str, Any]) -> str:
    """The text elements of the one rich_text section, joined as Slack shows them."""
    return "".join(e["text"] for e in _elements(body) if e["type"] == "text")


def test_cut_rc_posts_rich_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    _cut(tmp_path / "release-manifest.yaml")
    assert len(posted) == 1
    assert _message(posted[0]) == f"Cut RC 1.2.0-rc.1\nDigest: {DIGEST_A}"
    assert posted[0]["text"] == f"Cut RC 1.2.0-rc.1. Digest: {DIGEST_A}"
    assert _elements(posted[0])[1] == {
        "type": "text",
        "text": "1.2.0-rc.1",
        "style": {"bold": True},
    }
    assert _elements(posted[0])[-1] == {"type": "text", "text": DIGEST_A, "style": {"code": True}}


def test_promote_posts_rich_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])
    assert result.exit_code == 0, result.output
    assert [_message(b) for b in posted] == [f"Promoted 1.2.0-rc.1 to dev\nDigest: {DIGEST_A}"]


def test_rollback_posts_rich_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    runner.invoke(app, ["promote", "prod", "-m", str(path)])
    posted.clear()
    args = ["rollback", "prod", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert [_message(b) for b in posted] == [f"Rolled back prod to 1.1.9\nDigest: {DIGEST_B}"]


def test_promotion_pr_posts_the_digest_and_a_link(
    tmp_path: Path, posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github, "GitHubAdapter", FakeGitHub)
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "fake-sentinel-github")
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    args = ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", str(path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert len(posted) == 1
    assert _message(posted[0]) == f"Promotion PR for 1.2.0-rc.1 to dev\nDigest: {DIGEST_A}\n"
    assert _elements(posted[0])[-1] == {
        "type": "link",
        "url": "https://github.com/o/r/pull/42",
        "text": "Open pull request",
    }


def test_a_hostile_stage_cannot_ping_or_format(
    tmp_path: Path, posted: list[dict[str, Any]]
) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    result = runner.invoke(app, ["promote", "<!here> *x* :rocket:", "-m", str(path)])
    assert result.exit_code == 0, result.output
    assert len(posted) == 1
    assert {"type": "text", "text": "<!here> *x* :rocket:", "style": {"bold": True}} in _elements(
        posted[0]
    )  # literal inside rich_text, styled by its kind only
    assert posted[0]["text"].startswith("Promoted 1.2.0-rc.1 to &lt;!here&gt; *x* :rocket:.")
    assert posted[0]["mrkdwn"] is False
    assert "plain_text" not in json.dumps(posted[0])

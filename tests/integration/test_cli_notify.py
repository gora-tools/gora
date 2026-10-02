"""The CLI's notifications reach Slack as plain, literal messages (ADR-015).

Real notifier and real Slack adapter, over ``httpx.MockTransport``: what a release
command sends is the JSON body asserted here, never a live call. With an active candidate
the line under the summary is ``Image: <image>@<digest>``; without one, ``Digest: <digest>``.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import gora.adapters.github as github
import gora.transport.cli.commands as commands
from gora.adapters.github import PullRequest
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
    assert _message(posted[0]) == f"Cut RC 1.2.0-rc.1\nImage: {IMAGE}@{DIGEST_A}"
    assert posted[0]["text"] == f"Cut RC 1.2.0-rc.1. Image: {IMAGE}@{DIGEST_A}"
    assert _elements(posted[0])[1] == {
        "type": "text",
        "text": "1.2.0-rc.1",
        "style": {"bold": True},
    }
    assert _elements(posted[0])[-1] == {
        "type": "text",
        "text": f"{IMAGE}@{DIGEST_A}",
        "style": {"code": True},
    }


def test_promote_posts_rich_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])
    assert result.exit_code == 0, result.output
    assert [_message(b) for b in posted] == [
        f"Promoted 1.2.0-rc.1 to dev\nImage: {IMAGE}@{DIGEST_A}"
    ]


def test_rollback_posts_rich_text(tmp_path: Path, posted: list[dict[str, Any]]) -> None:
    """The stage goes back to another digest of the manifest's one image."""
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    runner.invoke(app, ["promote", "prod", "-m", str(path)])
    posted.clear()
    args = ["rollback", "prod", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert [_message(b) for b in posted] == [
        f"Rolled back prod to 1.1.9\nImage: {IMAGE}@{DIGEST_B}"
    ]


def test_without_an_active_candidate_the_digest_line_stays(
    tmp_path: Path, posted: list[dict[str, Any]]
) -> None:
    path = tmp_path / "release-manifest.yaml"
    path.write_text(
        f"release: 1.2.0\nstages:\n  prod:\n    version: 1.2.0\n    digest: {DIGEST_A}\n"
    )
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
    assert _message(posted[0]) == f"Promotion PR for 1.2.0-rc.1 to dev\nImage: {IMAGE}@{DIGEST_A}\n"
    assert _elements(posted[0])[-1] == {
        "type": "link",
        "url": "https://github.com/o/r/pull/42",
        "text": "Open pull request",
    }


def test_a_hostile_stage_is_refused_and_nothing_is_posted(
    tmp_path: Path, posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stage is a DNS label now (ADR-003), so this one never reaches Slack.

    How Slack shows a hostile value is held where one can still arrive: the pull request
    URL below, and every value slot in tests/unit/test_notifier.py.
    """
    monkeypatch.setenv("COLUMNS", "1000")
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()
    result = runner.invoke(app, ["promote", "<!here> *x* :rocket:", "-m", str(path)])
    assert result.exit_code == 2, result.output
    assert posted == []


def test_a_hostile_pull_request_url_cannot_ping_or_format(
    tmp_path: Path, posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The URL comes from GitHub's response: a value the CLI does not choose."""
    hostile = "https://github.com/o/r/pull/42?<!here>*x*:rocket:"

    class _HostileGitHub(FakeGitHub):
        def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
            return PullRequest(
                number=42, url=hostile, state="open", head=head, base=base, title=title
            )

    monkeypatch.setattr(github, "GitHubAdapter", _HostileGitHub)
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "fake-sentinel-github")
    path = tmp_path / "release-manifest.yaml"
    _cut(path)
    posted.clear()

    result = runner.invoke(app, ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert len(posted) == 1
    link = {"type": "link", "url": hostile, "text": "Open pull request"}
    assert _elements(posted[0])[-1] == link  # a structured field: sent as is, never parsed
    assert posted[0]["text"].endswith(" https://github.com/o/r/pull/42?&lt;!here&gt;*x*:rocket:")
    assert posted[0]["mrkdwn"] is False
    assert "plain_text" not in json.dumps(posted[0])

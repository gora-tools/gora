"""Integration tests for the `gora rollback` CLI command."""

from pathlib import Path

from typer.testing import CliRunner

from gora.domain.release.manifest import ReleaseManifest
from gora.transport.cli.commands import app

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIGEST_B = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
IMAGE = "ghcr.io/test/service"

runner = CliRunner()


def _cut_and_promote(path: Path, stage: str = "prod") -> None:
    runner.invoke(app, ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-m", str(path)])
    runner.invoke(app, ["promote", stage, "-m", str(path)])


def test_rollback_restores_prior_state(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut_and_promote(path)

    result = runner.invoke(
        app,
        ["rollback", "prod", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(path)],
    )

    assert result.exit_code == 0, result.output
    assert "Rolled back" in result.output

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.stages["prod"].version == "1.1.9"
    assert loaded.stages["prod"].digest == DIGEST_B


def test_rollback_without_manifest_fails(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    result = runner.invoke(
        app,
        ["rollback", "prod", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(path)],
    )

    assert result.exit_code == 1
    assert "No manifest" in result.output


def test_rollback_invalid_digest_fails(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _cut_and_promote(path)

    result = runner.invoke(
        app,
        [
            "rollback",
            "prod",
            "--to-version",
            "1.1.9",
            "--to-digest",
            "not-a-digest",
            "-m",
            str(path),
        ],
    )

    assert result.exit_code == 2
    assert "'not-a-digest' is not a valid digest" in result.output

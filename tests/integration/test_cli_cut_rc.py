"""Integration tests for the `gora cut-rc` CLI command."""

from pathlib import Path

from typer.testing import CliRunner

from gora.domain.release.manifest import ReleaseManifest
from gora.transport.cli.commands import app

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIGEST_B = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
IMAGE = "ghcr.io/test/service"

runner = CliRunner()


def test_cut_rc_creates_manifest(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    result = runner.invoke(
        app,
        [
            "cut-rc",
            "--image",
            IMAGE,
            "--version",
            "1.2.0-rc.1",
            "--digest",
            DIGEST_A,
            "--manifest",
            str(path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Cut release candidate" in result.output
    assert path.exists()

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.candidate is not None
    assert loaded.candidate.version == "1.2.0-rc.1"


def test_cut_rc_supersedes_prior(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    base_args = ["cut-rc", "--image", IMAGE, "--manifest", str(path)]

    runner.invoke(app, [*base_args, "--version", "1.2.0-rc.1", "--digest", DIGEST_A])
    result = runner.invoke(app, [*base_args, "--version", "1.2.0-rc.2", "--digest", DIGEST_B])

    assert result.exit_code == 0, result.output
    assert "superseded prior candidate: 1.2.0-rc.1" in result.output

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.candidate is not None
    assert loaded.candidate.version == "1.2.0-rc.2"


def test_cut_rc_invalid_digest_fails(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    result = runner.invoke(
        app,
        [
            "cut-rc",
            "--image",
            IMAGE,
            "--version",
            "1.2.0-rc.1",
            "--digest",
            "not-a-digest",
            "--manifest",
            str(path),
        ],
    )

    assert result.exit_code == 1
    assert "Invalid release candidate" in result.output
    assert not path.exists()


def test_cut_rc_missing_required_option_fails(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    result = runner.invoke(
        app,
        ["cut-rc", "--image", IMAGE, "--manifest", str(path)],
    )

    assert result.exit_code != 0
    assert not path.exists()

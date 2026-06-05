"""Integration tests for the `gora promote` CLI command."""

from pathlib import Path

from typer.testing import CliRunner

from gora.domain.release.manifest import ReleaseManifest
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

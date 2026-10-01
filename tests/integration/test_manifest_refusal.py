"""CLI and API on a manifest this engine cannot load or cannot represent (ADR-003).

Every command exits 1 with a one-line reason on stderr and no traceback, and leaves
the file byte-identical; `/status` answers 500 with the reason in `detail`. No API
detail names the manifest's path.
"""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from typer.testing import CliRunner

from gora.config.settings import Settings, get_settings
from gora.main import app as api
from gora.transport.cli.commands import app

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIGEST_B = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

V01 = f"""\
version: v0.1
release: 1.2.0
candidate:
  version: 1.2.0-rc.1
  image: ghcr.io/test/service
  digest: {DIGEST_A}
stages:
  dev:
    version: 1.2.0-rc.1
    digest: {DIGEST_A}
"""

# The v0.2 Release BOM, in the shape ADR-003 specifies.
V02_BOM = f"""\
version: v0.2
system: cally-platform
release: 2026.Q2
artifacts:
  cally-api:
    image: ghcr.io/test/cally-api
    candidate:
      version: 1.3.0-rc.2
      digest: {DIGEST_A}
    stages:
      dev:
        digest: {DIGEST_A}
"""

# document -> (content, the refusal's first line must start with, and name)
DOCUMENTS = {
    "v0.2-bom": (V02_BOM, "Unsupported manifest", "schema version 'v0.2'"),
    "v9": (V01.replace("version: v0.1", "version: v9"), "Unsupported manifest", "'v9'"),
    "extra-field": (V01 + "owner: team-x\n", "Unsupported manifest", "owner"),
    "empty": ("", "Cannot load manifest", "file is empty"),
    "non-mapping": ("- version: v0.1\n", "Cannot load manifest", "got a sequence"),
    "invalid-yaml": ("version: v0.1\nrelease: [1.2.0\n", "Cannot load manifest", "invalid YAML"),
    "missing-release": (
        V01.replace("release: 1.2.0\n", ""),
        "Cannot load manifest",
        "release: Field required",
    ),
}

COMMANDS = {
    "status": ["status"],
    "cut-rc": [
        "cut-rc",
        "--image",
        "ghcr.io/test/service",
        "--version",
        "1.2.0-rc.2",
        "--digest",
        DIGEST_B,
    ],
    "promote": ["promote", "staging"],
    "rollback": ["rollback", "dev", "--to-version", "1.1.0", "--to-digest", DIGEST_B],
}

runner = CliRunner()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("command", sorted(COMMANDS))
@pytest.mark.parametrize("document", sorted(DOCUMENTS))
def test_cli_refuses_and_leaves_file_untouched(tmp_path: Path, document: str, command: str) -> None:
    content, kind, named = DOCUMENTS[document]
    path = tmp_path / "release-manifest.yaml"
    path.write_text(content)
    before = _sha256(path)

    result = runner.invoke(app, [*COMMANDS[command], "--manifest", str(path)])

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)  # typer.Exit, not an uncaught error
    assert "Traceback" not in result.output
    first = result.stderr.splitlines()[0]
    assert first.startswith(f"{kind} {path}: ")
    assert named in first
    assert _sha256(path) == before


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_cli_accepts_v01_control(tmp_path: Path, command: str) -> None:
    """The refused documents differ from this one only in the defect under test."""
    path = tmp_path / "release-manifest.yaml"
    path.write_text(V01)

    result = runner.invoke(app, [*COMMANDS[command], "--manifest", str(path)])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("document", sorted(DOCUMENTS))
def test_api_status_refuses_with_detail(tmp_path: Path, document: str) -> None:
    content, kind, named = DOCUMENTS[document]
    path = tmp_path / "release-manifest.yaml"
    path.write_text(content)
    before = _sha256(path)

    r = _get_status(path)

    assert r.status_code == 500
    assert r.headers["content-type"] == "application/json"
    detail = r.json()["detail"]
    assert detail.startswith(f"{kind}: ")
    assert named in detail
    assert str(tmp_path) not in detail
    assert _sha256(path) == before


def test_api_status_404_names_no_path(tmp_path: Path) -> None:
    r = _get_status(tmp_path / "release-manifest.yaml")

    assert r.status_code == 404
    assert str(tmp_path) not in r.json()["detail"]


def _get_status(path: Path) -> Response:
    api.dependency_overrides[get_settings] = lambda: Settings(manifest_path=path)
    try:
        return TestClient(api, raise_server_exceptions=False).get("/status")
    finally:
        api.dependency_overrides.clear()

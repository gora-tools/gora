"""Integration tests for the read-only FastAPI surface (no network — TestClient)."""

from pathlib import Path

from fastapi.testclient import TestClient

from gora.config.settings import Settings, get_settings
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import ReleaseManifest
from gora.main import app

DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _client_for(manifest_path: Path) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(manifest_path=manifest_path)
    return TestClient(app)


def test_healthz() -> None:
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_status_returns_manifest(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    ReleaseManifest(
        release="1.2.0",
        candidate=ReleaseCandidate(version="1.2.0-rc.1", image="ghcr.io/x/y", digest=DIGEST),
    ).to_yaml(path)
    try:
        r = _client_for(path).get("/status")
        assert r.status_code == 200
        body = r.json()
        assert body["release"] == "1.2.0"
        assert body["candidate"]["digest"] == DIGEST
    finally:
        app.dependency_overrides.clear()


def test_status_404_when_no_manifest(tmp_path: Path) -> None:
    try:
        r = _client_for(tmp_path / "nope.yaml").get("/status")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()

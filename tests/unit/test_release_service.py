"""Tests for the release service (cut-rc sequencing)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gora.audit.logger import AuditLogger
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import ReleaseManifest
from gora.services.release import ReleaseService

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIGEST_B = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
IMAGE = "ghcr.io/test/service"


class _CapturingAudit(AuditLogger):
    """Audit logger that records events in memory for assertions."""

    def __init__(self) -> None:
        super().__init__(actor="test")
        self.events: list[dict[str, Any]] = []

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        event = super().record(action, **details)
        self.events.append(event)
        return event


def test_cut_rc_creates_manifest_and_derives_release(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    service = ReleaseService(manifest_path=path)

    result = service.cut_rc(image=IMAGE, version="1.2.0-rc.1", digest=DIGEST_A)

    assert result.created is True
    assert result.release == "1.2.0"  # derived from version
    assert result.superseded is None
    assert path.exists()

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.candidate is not None
    assert loaded.candidate.version == "1.2.0-rc.1"
    assert loaded.candidate.digest == DIGEST_A


def test_cut_rc_explicit_release_overrides_derivation(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    service = ReleaseService(manifest_path=path)

    result = service.cut_rc(image=IMAGE, version="1.2.0-rc.1", digest=DIGEST_A, release="1.2.0")

    assert result.release == "1.2.0"


def test_cut_rc_supersedes_prior_candidate(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    service = ReleaseService(manifest_path=path)

    service.cut_rc(image=IMAGE, version="1.2.0-rc.1", digest=DIGEST_A)
    result = service.cut_rc(image=IMAGE, version="1.2.0-rc.2", digest=DIGEST_B)

    assert result.created is False
    assert result.superseded == "1.2.0-rc.1"

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.candidate is not None
    assert loaded.candidate.version == "1.2.0-rc.2"
    assert loaded.candidate.digest == DIGEST_B


def test_cut_rc_release_line_mismatch_raises(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    # Seed a manifest on release line 1.2.0
    ReleaseManifest(
        release="1.2.0",
        candidate=ReleaseCandidate(version="1.2.0-rc.1", image=IMAGE, digest=DIGEST_A),
    ).to_yaml(path)

    service = ReleaseService(manifest_path=path)
    with pytest.raises(ValueError, match="Release line mismatch"):
        service.cut_rc(image=IMAGE, version="1.3.0-rc.1", digest=DIGEST_B, release="1.3.0")


def test_cut_rc_invalid_digest_raises(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    service = ReleaseService(manifest_path=path)

    with pytest.raises(ValidationError):
        service.cut_rc(image=IMAGE, version="1.2.0-rc.1", digest="not-a-digest")

    # Failed cut must not create a manifest.
    assert not path.exists()


def test_cut_rc_emits_audit_event(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    audit = _CapturingAudit()
    service = ReleaseService(manifest_path=path, audit=audit)

    service.cut_rc(image=IMAGE, version="1.2.0-rc.1", digest=DIGEST_A)

    assert len(audit.events) == 1
    event = audit.events[0]
    assert event["action"] == "cut-rc"
    assert event["details"]["digest"] == DIGEST_A
    assert event["details"]["superseded"] is None


def test_promote_moves_candidate_digest_to_stage(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    audit = _CapturingAudit()
    service = ReleaseService(manifest_path=path, audit=audit)
    service.cut_rc(image=IMAGE, version="1.2.0-rc.1", digest=DIGEST_A)

    result = service.promote("dev")

    assert result.success
    assert result.stage == "dev"
    assert result.digest == DIGEST_A

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.stages["dev"].digest == DIGEST_A
    assert loaded.stages["dev"].version == "1.2.0-rc.1"

    # cut-rc + promote both audited
    assert [e["action"] for e in audit.events] == ["cut-rc", "promote"]


def test_promote_without_manifest_fails(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    service = ReleaseService(manifest_path=path)

    result = service.promote("dev")

    assert not result.success
    assert result.error is not None
    assert not path.exists()


def test_promote_without_candidate_fails(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    ReleaseManifest(release="1.2.0").to_yaml(path)
    audit = _CapturingAudit()
    service = ReleaseService(manifest_path=path, audit=audit)

    result = service.promote("dev")

    assert not result.success
    assert result.error is not None
    assert audit.events == []  # no mutation, no audit

"""Tests for release manifest model."""

import tempfile
from pathlib import Path

from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import ManifestVersion, ReleaseManifest, StageState

SAMPLE_DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ROLLBACK_DIGEST = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


def _make_manifest() -> ReleaseManifest:
    return ReleaseManifest(
        release="1.0.0",
        candidate=ReleaseCandidate(
            version="1.0.0-rc.1",
            image="ghcr.io/test/service",
            digest=SAMPLE_DIGEST,
        ),
        stages={
            "dev": StageState(version="0.9.0", digest=ROLLBACK_DIGEST),
        },
    )


class TestReleaseManifest:
    def test_create_manifest(self) -> None:
        m = _make_manifest()
        assert m.version == ManifestVersion.V01
        assert m.release == "1.0.0"
        assert m.candidate is not None
        assert m.candidate.version == "1.0.0-rc.1"

    def test_promote_candidate_to_stage(self) -> None:
        m = _make_manifest()
        result = m.promote("dev")
        assert result.success
        assert result.digest == SAMPLE_DIGEST
        assert m.stages["dev"].version == "1.0.0-rc.1"

    def test_promote_to_new_stage(self) -> None:
        m = _make_manifest()
        result = m.promote("staging")
        assert result.success
        assert "staging" in m.stages
        assert m.stages["staging"].digest == SAMPLE_DIGEST

    def test_promote_without_candidate_fails(self) -> None:
        m = ReleaseManifest(release="1.0.0")
        result = m.promote("dev")
        assert not result.success
        assert result.error is not None

    def test_rollback_stage(self) -> None:
        m = _make_manifest()
        m.promote("dev")  # promote RC first
        result = m.rollback("dev", "0.9.0", ROLLBACK_DIGEST)
        assert result.success
        assert m.stages["dev"].version == "0.9.0"
        assert m.stages["dev"].digest == ROLLBACK_DIGEST

    def test_cut_new_candidate_supersedes(self) -> None:
        m = _make_manifest()
        new_rc = ReleaseCandidate(
            version="1.0.0-rc.2",
            image="ghcr.io/test/service",
            digest=ROLLBACK_DIGEST,
        )
        m.cut_candidate(new_rc)
        assert m.candidate is not None
        assert m.candidate.version == "1.0.0-rc.2"
        assert m.candidate.digest == ROLLBACK_DIGEST

    def test_yaml_roundtrip(self) -> None:
        m = _make_manifest()
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = Path(f.name)
        m.to_yaml(path)
        loaded = ReleaseManifest.from_yaml(path)
        assert loaded.release == m.release
        assert loaded.candidate is not None
        assert loaded.candidate.digest == m.candidate.digest  # type: ignore[union-attr]
        assert loaded.stages["dev"].version == m.stages["dev"].version
        path.unlink()

    def test_candidate_image_ref(self) -> None:
        m = _make_manifest()
        assert m.candidate is not None
        assert m.candidate.image_ref == f"ghcr.io/test/service@{SAMPLE_DIGEST}"
        assert m.candidate.tagged_ref == "ghcr.io/test/service:1.0.0-rc.1"

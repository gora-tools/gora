"""The manifest parser checks the schema version first and refuses what it cannot hold.

ADR-003: "`version` field in manifest root identifies schema version — parsers must
check this first". A document the v0.1 model would load with loss — another schema
version, or a field it does not define — is refused with a typed error, before any
command could write the model back over it. So is a file that cannot be loaded at all,
with the base of the same error family.
"""

from pathlib import Path
from typing import Any

import pytest

from gora.audit.logger import AuditLogger
from gora.domain.release.manifest import (
    ManifestLoadError,
    ManifestVersion,
    ReleaseManifest,
    UnsupportedManifestError,
)
from gora.services.release import ReleaseService

REPO_ROOT = Path(__file__).resolve().parents[2]
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

# (document, text the refusal must name)
REFUSED = {
    "v0.2-bom": (V02_BOM, "'v0.2'"),
    "v0.2-header": (V01.replace("version: v0.1", "version: v0.2"), "'v0.2'"),
    "v9": (V01.replace("version: v0.1", "version: v9"), "'v9'"),
    "version-not-a-string": (V01.replace("version: v0.1", "version: 0.1"), "version 0.1 is"),
    "extra-root-field": (V01 + "owner: team-x\n", "owner"),
    "extra-candidate-field": (
        V01.replace("  image:", "  signed: true\n  image:"),
        "candidate.signed",
    ),
    "extra-stage-field": (V01 + "    note: hand-edited\n", "stages.dev.note"),
}


# (file content, text the refusal must name) — refused as ManifestLoadError itself
UNLOADABLE = {
    "empty": (b"", "file is empty"),
    "comments-only": (b"# nothing here\n", "file is empty"),
    "sequence": (b"- version: v0.1\n- release: 1.2.0\n", "got a sequence"),
    "scalar": (b"just a string\n", "got a scalar (str)"),
    "invalid-yaml": (b"version: v0.1\nrelease: [1.2.0\n", "invalid YAML"),
    "tab-indent": (b"version: v0.1\n\trelease: 1.2.0\n", "(line 2, column 1)"),
    "not-utf8": (b"version: v0.1\nrelease: \xff\xfe\n", "not UTF-8"),
    "missing-release": (V01.replace("release: 1.2.0\n", "").encode(), "release: Field required"),
    "bad-stage-digest": (
        V01.encode() + b"  prod:\n    version: 1.0.0\n    digest: sha256:nothex\n",
        "stages.prod.digest",
    ),
}


@pytest.fixture(params=sorted(REFUSED))
def refused(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, str]:
    document, named = REFUSED[request.param]
    path = tmp_path / "release-manifest.yaml"
    path.write_text(document)
    return path, named


class _CapturingAudit(AuditLogger):
    def __init__(self) -> None:
        super().__init__(actor="test")
        self.actions: list[str] = []

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        self.actions.append(action)
        return super().record(action, **details)


def test_from_yaml_refuses(refused: tuple[Path, str]) -> None:
    path, named = refused
    with pytest.raises(UnsupportedManifestError) as info:
        ReleaseManifest.from_yaml(path)
    assert info.value.path == path
    assert named in info.value.reason
    assert str(path) in str(info.value)


@pytest.fixture(params=sorted(UNLOADABLE))
def unloadable(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, str]:
    content, named = UNLOADABLE[request.param]
    path = tmp_path / "release-manifest.yaml"
    path.write_bytes(content)
    return path, named


def test_from_yaml_refuses_unloadable(unloadable: tuple[Path, str]) -> None:
    path, named = unloadable
    with pytest.raises(ManifestLoadError) as info:
        ReleaseManifest.from_yaml(path)
    assert type(info.value) is ManifestLoadError
    assert info.value.path == path
    assert named in info.value.reason
    assert str(path) in str(info.value)


def test_from_yaml_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ManifestLoadError, match="cannot read file"):
        ReleaseManifest.from_yaml(tmp_path)


def test_unsupported_version_is_reported_before_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    path.write_text(V02_BOM)
    with pytest.raises(UnsupportedManifestError, match=r"schema version 'v0\.2'"):
        ReleaseManifest.from_yaml(path)


def test_missing_version_is_v01(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    path.write_text(V01.replace("version: v0.1\n", ""))
    assert ReleaseManifest.from_yaml(path).version == ManifestVersion.V01


@pytest.mark.parametrize(
    "relative", ["release-manifest.yaml", "contracts/examples/release-manifest.yaml"]
)
def test_repo_manifests_load(relative: str) -> None:
    loaded = ReleaseManifest.from_yaml(REPO_ROOT / relative)
    assert loaded.version == ManifestVersion.V01
    assert loaded.candidate is not None


@pytest.mark.parametrize("operation", ["cut-rc", "promote", "rollback"])
def test_service_never_writes_over_refused_manifest(
    refused: tuple[Path, str], operation: str
) -> None:
    path, _ = refused
    before = path.read_bytes()
    audit = _CapturingAudit()
    service = ReleaseService(manifest_path=path, audit=audit)

    with pytest.raises(UnsupportedManifestError):
        if operation == "cut-rc":
            service.cut_rc(image="ghcr.io/test/service", version="1.2.0-rc.2", digest=DIGEST_B)
        elif operation == "promote":
            service.promote("staging")
        else:
            service.rollback("dev", to_version="1.1.0", to_digest=DIGEST_B)

    assert path.read_bytes() == before
    assert audit.actions == []


@pytest.mark.parametrize("operation", ["cut-rc", "promote", "rollback"])
def test_service_never_writes_over_unloadable_manifest(
    unloadable: tuple[Path, str], operation: str
) -> None:
    path, _ = unloadable
    before = path.read_bytes()
    audit = _CapturingAudit()
    service = ReleaseService(manifest_path=path, audit=audit)

    with pytest.raises(ManifestLoadError):
        if operation == "cut-rc":
            service.cut_rc(image="ghcr.io/test/service", version="1.2.0-rc.2", digest=DIGEST_B)
        elif operation == "promote":
            service.promote("staging")
        else:
            service.rollback("dev", to_version="1.1.0", to_digest=DIGEST_B)

    assert path.read_bytes() == before
    assert audit.actions == []

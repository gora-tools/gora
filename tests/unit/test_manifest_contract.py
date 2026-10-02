"""The release contract's invariants, held by the manifest itself (ADR-003).

Grammar on every field; within a manifest a version maps to one digest; the candidate's
MAJOR.MINOR.PATCH is the release line; one image per manifest; a rollback targets an
existing stage. An operation that would break one raises ``ContractViolationError`` and
leaves the model as it was; a file that already breaks one is refused on load. Loading
invents nothing: what the file does not say, the model does not say either.
"""

import hashlib
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

import gora
from gora.audit.logger import AuditLogger
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import (
    NO_SUCH_STAGE,
    OTHER_IMAGE,
    OTHER_RELEASE_LINE,
    ContractViolationError,
    ManifestLoadError,
    ReleaseManifest,
    StageState,
)
from gora.services.release import ReleaseService

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = Path(gora.__file__).parent.parent
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
IMAGE = "ghcr.io/test/service"


def _rc(version: str, digest: str, image: str = IMAGE) -> ReleaseCandidate:
    return ReleaseCandidate(version=version, image=image, digest=digest)


def _manifest() -> ReleaseManifest:
    """Line 1.2.0, rc.3 active on digest A, dev on rc.3, prod on the old line 1.1.9 (B)."""
    return ReleaseManifest(
        release="1.2.0",
        candidate=_rc("1.2.0-rc.3", DIGEST_A),
        stages={
            "dev": StageState(version="1.2.0-rc.3", digest=DIGEST_A),
            "prod": StageState(version="1.1.9", digest=DIGEST_B),
        },
    )


def _violation(call: Any) -> ContractViolationError:
    with pytest.raises(ContractViolationError) as info:
        call()
    return info.value


# --- a) a version maps to one digest --------------------------------------------------------


def test_recutting_the_active_version_with_another_digest_is_refused() -> None:
    """E1/E6: a re-cut of rc.3 on another digest rewrote the release identity."""
    m = _manifest()
    before = m.model_dump()

    error = _violation(lambda: m.cut_candidate(_rc("1.2.0-rc.3", DIGEST_C)))

    assert "already recorded in the active candidate with another digest" in error.reason
    assert m.model_dump() == before


def test_cutting_a_version_a_stage_holds_with_another_digest_is_refused() -> None:
    m = _manifest()
    m.cut_candidate(_rc("1.2.0-rc.4", DIGEST_C))
    m.promote("staging")
    m.cut_candidate(_rc("1.2.0-rc.5", DIGEST_B))
    before = m.model_dump()

    error = _violation(lambda: m.cut_candidate(_rc("1.2.0-rc.4", DIGEST_A)))

    assert "already recorded in a stage with another digest" in error.reason
    assert m.model_dump() == before


def test_rolling_back_to_a_version_recorded_with_another_digest_is_refused() -> None:
    m = _manifest()
    before = m.model_dump()

    _violation(lambda: m.rollback("dev", "1.1.9", DIGEST_C))  # prod holds 1.1.9 on B
    _violation(lambda: m.rollback("prod", "1.1.9", DIGEST_C))  # its own stage, too
    _violation(lambda: m.rollback("prod", "1.2.0-rc.3", DIGEST_C))  # the candidate's

    assert m.model_dump() == before


def test_a_digest_may_carry_several_versions() -> None:
    """The final release is a retag of the validated candidate: 1.2.0-rc.3 and 1.2.0."""
    m = _manifest()

    assert m.cut_candidate(_rc("1.2.0", DIGEST_A)) is True
    m.promote("prod")

    assert m.stages["prod"] == StageState(version="1.2.0", digest=DIGEST_A)
    assert m.stages["dev"] == StageState(version="1.2.0-rc.3", digest=DIGEST_A)


def test_rolling_back_to_the_version_and_digest_the_manifest_records_is_allowed() -> None:
    m = _manifest()
    m.cut_candidate(_rc("1.2.0-rc.4", DIGEST_C))
    m.promote("prod")

    result = m.rollback("prod", "1.1.9", DIGEST_B)

    assert result.success
    assert m.stages["prod"] == StageState(version="1.1.9", digest=DIGEST_B)


# --- the active candidate, cut again -------------------------------------------------------


def test_recutting_the_active_candidate_changes_nothing() -> None:
    m = _manifest()
    before = m.model_dump()

    assert m.cut_candidate(_rc("1.2.0-rc.3", DIGEST_A)) is False
    assert m.model_dump() == before


def test_the_same_version_and_digest_under_another_image_is_not_a_no_op() -> None:
    m = _manifest()
    error = _violation(lambda: m.cut_candidate(_rc("1.2.0-rc.3", DIGEST_A, "ghcr.io/x/other")))
    assert error.reason == OTHER_IMAGE


# --- b) the candidate is on the release line ------------------------------------------------


@pytest.mark.parametrize("version", ["1.3.0-rc.1", "1.1.9-rc.1", "2.0.0", "1.2.1"])
def test_a_candidate_off_the_release_line_is_refused(version: str) -> None:
    m = _manifest()
    before = m.model_dump()

    error = _violation(lambda: m.cut_candidate(_rc(version, DIGEST_C)))

    assert error.reason == OTHER_RELEASE_LINE
    assert m.model_dump() == before


def test_stages_may_hold_older_release_lines() -> None:
    assert _manifest().stages["prod"].version == "1.1.9"


# --- c) one image per manifest --------------------------------------------------------------


def test_a_candidate_with_another_image_is_refused() -> None:
    m = _manifest()
    before = m.model_dump()

    error = _violation(lambda: m.cut_candidate(_rc("1.2.0-rc.4", DIGEST_C, "ghcr.io/x/other")))

    assert error.reason == OTHER_IMAGE
    assert m.model_dump() == before


def test_the_first_candidate_may_carry_any_image() -> None:
    m = ReleaseManifest(release="1.2.0")
    assert m.cut_candidate(_rc("1.2.0-rc.1", DIGEST_A, "localhost:5000/x")) is True


# --- d) a rollback targets an existing stage ------------------------------------------------


def test_rolling_back_a_stage_that_does_not_exist_is_refused() -> None:
    """F7: the rollback created the stage."""
    m = _manifest()
    before = m.model_dump()

    error = _violation(lambda: m.rollback("qa", "1.1.9", DIGEST_B))

    assert error.reason == NO_SUCH_STAGE
    assert "qa" not in m.stages
    assert m.model_dump() == before


# --- every reason is value-free -------------------------------------------------------------


def test_no_reason_carries_a_value() -> None:
    m = _manifest()
    reasons = [
        _violation(lambda: m.cut_candidate(_rc("1.2.0-rc.3", DIGEST_C))).reason,
        _violation(lambda: m.cut_candidate(_rc("1.3.0-rc.1", DIGEST_C))).reason,
        _violation(lambda: m.cut_candidate(_rc("1.2.0-rc.9", DIGEST_C, "ghcr.io/x/y"))).reason,
        _violation(lambda: m.rollback("qa", "1.1.9", DIGEST_B)).reason,
    ]
    for value in ("1.2.0-rc.3", "1.3.0", "rc.9", "ghcr.io", "qa", "1.1.9", "a" * 64, "c" * 64):
        assert [r for r in reasons if value in r] == [], value


# --- the model cannot hold a broken contract ------------------------------------------------


def test_a_model_with_two_digests_for_one_version_cannot_be_built() -> None:
    with pytest.raises(ValidationError, match="a version maps to one digest"):
        ReleaseManifest(
            release="1.2.0",
            candidate=_rc("1.2.0-rc.3", DIGEST_A),
            stages={"dev": StageState(version="1.2.0-rc.3", digest=DIGEST_B)},
        )


def test_a_model_with_a_candidate_off_its_line_cannot_be_built() -> None:
    with pytest.raises(ValidationError, match="must equal release"):
        ReleaseManifest(release="1.2.0", candidate=_rc("1.3.0-rc.1", DIGEST_A))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "1.0.0+b1"),
        ("image", "service"),
        ("digest", "sha256:" + "a" * 63),
    ],
)
def test_a_candidate_with_a_bad_identifier_cannot_be_built(field: str, value: str) -> None:
    values = {"version": "1.2.0-rc.1", "image": IMAGE, "digest": DIGEST_A, field: value}
    with pytest.raises(ValidationError):
        ReleaseCandidate(**values)


def test_promote_refuses_a_stage_name_the_grammar_refuses() -> None:
    m = _manifest()
    before = m.model_dump()

    _violation(lambda: m.promote("dev[eu]"))

    assert m.model_dump() == before


# --- e) a file that breaks the contract is refused on load ----------------------------------

V01 = f"""\
version: v0.1
release: 1.2.0
candidate:
  version: 1.2.0-rc.3
  image: {IMAGE}
  digest: {DIGEST_A}
stages:
  dev:
    version: 1.2.0-rc.3
    digest: {DIGEST_A}
  prod:
    version: 1.1.9
    digest: {DIGEST_B}
"""

# (document, what the refusal must name, a value it must not show)
BROKEN = {
    "version-two-digests": (
        V01.replace(
            f"    version: 1.2.0-rc.3\n    digest: {DIGEST_A}",
            f"    version: 1.2.0-rc.3\n    digest: {DIGEST_C}",
        ),
        "stages.dev.version: the same version is recorded at candidate.version with another digest",
        "c" * 64,
    ),
    "two-stages-one-version": (
        V01.replace(
            f"    version: 1.1.9\n    digest: {DIGEST_B}",
            f"    version: 1.2.0-rc.3\n    digest: {DIGEST_B}",
        ),
        "stages.prod.version: the same version is recorded at candidate.version",
        "b" * 64,
    ),
    "candidate-off-the-line": (
        V01.replace("release: 1.2.0", "release: 1.3.0"),
        "candidate.version: its MAJOR.MINOR.PATCH must equal release",
        "1.3.0",
    ),
    "bad-version": (V01.replace("1.1.9", "1.1.9+b1"), "stages.prod.version: must", "1.1.9+b1"),
    "bad-release": (V01.replace("release: 1.2.0", "release: '1.2'"), "release: must", "'1.2'"),
    "bad-image": (V01.replace(IMAGE, "service"), "candidate.image: must", "service"),
    "image-with-tag": (V01.replace(IMAGE, IMAGE + ":1.2.0"), "candidate.image: must", ":1.2.0"),
    "bad-digest-65": (V01.replace(DIGEST_B, DIGEST_B + "b"), "stages.prod.digest: must", "b" * 65),
    "bad-digest-upper": (
        V01.replace(DIGEST_B, DIGEST_B.upper().replace("SHA256", "sha256")),
        "stages.prod.digest: must",
        "B" * 64,
    ),
    "bad-stage-name": (
        V01.replace("  prod:", "  Prod_EU:"),
        "stages.Prod_EU: must be a lowercase DNS label",
        "1.1.9",
    ),
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_a_manifest_that_breaks_the_contract_is_refused_on_load(name: str, tmp_path: Path) -> None:
    document, named, unshown = BROKEN[name]
    path = tmp_path / "release-manifest.yaml"
    path.write_text(document)
    before = path.read_bytes()

    with pytest.raises(ManifestLoadError) as info:
        ReleaseManifest.from_yaml(path)

    assert type(info.value) is ManifestLoadError
    assert named in info.value.reason
    assert unshown not in info.value.reason
    assert path.read_bytes() == before


def test_the_control_document_loads(tmp_path: Path) -> None:
    """The broken documents differ from this one only in the defect under test."""
    path = tmp_path / "release-manifest.yaml"
    path.write_text(V01)
    assert ReleaseManifest.from_yaml(path).release == "1.2.0"


# --- loading invents nothing ----------------------------------------------------------------


def test_created_at_absent_stays_absent_on_load_and_write(tmp_path: Path) -> None:
    """F6: every load of a manifest without created_at wrote a new one."""
    path = tmp_path / "release-manifest.yaml"
    path.write_text(V01)

    loaded = ReleaseManifest.from_yaml(path)
    assert loaded.candidate is not None
    assert loaded.candidate.created_at is None
    loaded.promote("staging")
    loaded.to_yaml(path)

    assert "created_at" not in path.read_text()
    assert "created_at" not in yaml.safe_load(path.read_text())["candidate"]


def test_cut_rc_sets_created_at(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    ReleaseService(manifest_path=path).cut_rc(image=IMAGE, version="1.2.0-rc.1", digest=DIGEST_A)
    candidate = ReleaseManifest.from_yaml(path).candidate
    assert candidate is not None and candidate.created_at is not None
    assert candidate.created_at.utcoffset() == timedelta(0)


ROUND_TRIP = (
    "import sys, pathlib; "
    "from gora.domain.release.manifest import ReleaseManifest; "
    "sys.stdout.write(ReleaseManifest.from_yaml(pathlib.Path(sys.argv[1])).to_yaml_str())"
)


def _round_trip_in_a_new_process(path: Path) -> bytes:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, "-c", ROUND_TRIP, str(path)], env=env, capture_output=True, check=True
    ).stdout


@pytest.mark.parametrize(
    "relative", ["contracts/examples/release-manifest.yaml", "release-manifest.yaml"]
)
def test_load_and_write_is_byte_stable_across_processes(relative: str) -> None:
    first = _round_trip_in_a_new_process(REPO_ROOT / relative)
    second = _round_trip_in_a_new_process(REPO_ROOT / relative)
    assert first  # calibration: a process that wrote nothing would compare equal too
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_the_repo_root_manifest_round_trips_to_its_own_bytes() -> None:
    """GORA releases itself with this file; loading and writing it changes nothing."""
    path = REPO_ROOT / "release-manifest.yaml"
    assert _round_trip_in_a_new_process(path) == path.read_bytes()


# --- the service writes, audits and notifies nothing on a violation -------------------------


class _CapturingAudit(AuditLogger):
    def __init__(self) -> None:
        super().__init__(actor="test")
        self.actions: list[str] = []

    def record(self, action: str, **details: Any) -> dict[str, Any]:
        self.actions.append(action)
        return super().record(action, **details)


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    path = tmp_path / "release-manifest.yaml"
    path.write_text(V01)
    return path


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.cut_rc(image=IMAGE, version="1.2.0-rc.3", digest=DIGEST_C),
        lambda s: s.cut_rc(image=IMAGE, version="1.3.0-rc.1", digest=DIGEST_C),
        lambda s: s.cut_rc(image="ghcr.io/x/other", version="1.2.0-rc.4", digest=DIGEST_C),
        lambda s: s.rollback("qa", to_version="1.1.9", to_digest=DIGEST_B),
        lambda s: s.rollback("dev", to_version="1.1.9", to_digest=DIGEST_C),
    ],
    ids=["version-taken", "off-the-line", "other-image", "no-such-stage", "rollback-taken"],
)
def test_a_refused_operation_writes_and_audits_nothing(seeded: Path, operation: Any) -> None:
    before = seeded.read_bytes()
    audit = _CapturingAudit()

    with pytest.raises(ContractViolationError):
        operation(ReleaseService(manifest_path=seeded, audit=audit))

    assert seeded.read_bytes() == before
    assert audit.actions == []


def test_recutting_the_active_candidate_writes_and_audits_nothing(seeded: Path) -> None:
    before = seeded.read_bytes()
    audit = _CapturingAudit()

    result = ReleaseService(manifest_path=seeded, audit=audit).cut_rc(
        image=IMAGE, version="1.2.0-rc.3", digest=DIGEST_A
    )

    assert result.changed is False
    assert result.candidate.version == "1.2.0-rc.3"
    assert result.superseded is None
    assert seeded.read_bytes() == before
    assert audit.actions == []


def test_a_fresh_manifest_takes_the_release_line_from_the_version(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    result = ReleaseService(manifest_path=path).cut_rc(
        image=IMAGE, version="1.2.0-x-y.1", digest=DIGEST_A
    )
    assert result.release == "1.2.0"


def test_a_fresh_manifest_refuses_a_release_line_the_version_is_not_on(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    with pytest.raises(ContractViolationError):
        ReleaseService(manifest_path=path).cut_rc(
            image=IMAGE, version="1.2.0-rc.1", digest=DIGEST_A, release="1.3.0"
        )
    assert not path.exists()

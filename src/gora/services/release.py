"""Release service — deterministic sequencing for release operations.

The service loads the Git-native manifest, applies a domain operation,
persists the result, and emits an audit event. All mutating release
operations (cut-rc, promote, rollback) flow through here so that
sequencing, persistence, and audit are uniform and transport-agnostic.

See ADR-002 (deterministic code owns sequencing), ADR-003 (release
contract / RC rules), ADR-004 (promotion and rollback model).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gora.audit.logger import AuditLogger
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.identifiers import release_line_of
from gora.domain.release.manifest import PromotionResult, ReleaseManifest

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class CutResult:
    """Outcome of a cut-rc operation.

    ``changed`` is False when the candidate already was the active one: nothing was
    written or audited, and ``candidate`` is the one the manifest holds.
    """

    candidate: ReleaseCandidate
    release: str
    superseded: str | None
    manifest_path: Path
    created: bool
    changed: bool = True


class ReleaseService:
    """Sequences mutating release operations over a single manifest file."""

    def __init__(self, manifest_path: Path, audit: AuditLogger | None = None) -> None:
        self._manifest_path = manifest_path
        self._audit = audit or AuditLogger()

    def cut_rc(
        self,
        *,
        image: str,
        version: str,
        digest: str,
        release: str | None = None,
    ) -> CutResult:
        """Cut a new release candidate from an immutable artifact digest.

        Creates the manifest if it does not exist (its release line is the version's
        MAJOR.MINOR.PATCH unless ``release`` is given). A new candidate replaces any
        prior active candidate on the release line (ADR-003). Every value is checked
        against the identifier grammar by the model; the invariants by the manifest,
        which raises ``ContractViolationError`` before anything is written. Re-cutting the
        active candidate (same version, digest and image) writes and audits nothing.
        """
        created = not self._manifest_path.exists()
        manifest = None if created else ReleaseManifest.from_yaml(self._manifest_path)
        if manifest is not None and release is not None and release != manifest.release:
            raise ValueError(
                f"Release line mismatch: manifest is '{manifest.release}', requested '{release}'"
            )

        candidate = ReleaseCandidate(
            version=version, image=image, digest=digest, created_at=datetime.now(UTC)
        )
        if manifest is None:
            manifest = ReleaseManifest(release=release or release_line_of(candidate.version))

        prior = manifest.candidate
        if not manifest.cut_candidate(candidate):
            assert prior is not None  # only the active candidate is unchanged
            return CutResult(
                candidate=prior,
                release=manifest.release,
                superseded=None,
                manifest_path=self._manifest_path,
                created=False,
                changed=False,
            )
        superseded = prior.version if prior is not None else None
        manifest.to_yaml(self._manifest_path)

        self._audit.record(
            "cut-rc",
            release=manifest.release,
            version=candidate.version,
            image=candidate.image,
            digest=candidate.digest,
            superseded=superseded,
            manifest=str(self._manifest_path),
        )

        return CutResult(
            candidate=candidate,
            release=manifest.release,
            superseded=superseded,
            manifest_path=self._manifest_path,
            created=created,
        )

    def promote(self, target_stage: str) -> PromotionResult:
        """Promote the active candidate to ``target_stage``.

        Moves the same digest into the stage (no rebuild, ADR-004). Fails
        cleanly if no manifest exists or there is no active candidate; on
        success the manifest is persisted and the promotion audited.
        """
        if not self._manifest_path.exists():
            return PromotionResult(
                success=False,
                error=f"No manifest at {self._manifest_path}; run cut-rc first",
            )

        manifest = ReleaseManifest.from_yaml(self._manifest_path)
        result = manifest.promote(target_stage)
        if not result.success:
            return result

        manifest.to_yaml(self._manifest_path)
        self._audit.record(
            "promote",
            release=manifest.release,
            stage=result.stage,
            version=result.version,
            digest=result.digest,
            manifest=str(self._manifest_path),
        )
        return result

    def rollback(
        self,
        target_stage: str,
        *,
        to_version: str,
        to_digest: str,
    ) -> PromotionResult:
        """Roll ``target_stage`` back to a known prior version/digest.

        Rollback points a stage at a previously validated digest (ADR-004);
        it is not a rebuild. The operator supplies the known-good target, and
        the Git history of the manifest is the audit trail. The values are
        validated by the domain model; a stage that does not exist, or a version
        the manifest records with another digest, raises ``ContractViolationError``
        and nothing is written. Fails cleanly if no manifest exists.
        """
        if not self._manifest_path.exists():
            return PromotionResult(
                success=False,
                error=f"No manifest at {self._manifest_path}; nothing to roll back",
            )

        manifest = ReleaseManifest.from_yaml(self._manifest_path)
        result = manifest.rollback(target_stage, to_version, to_digest)

        manifest.to_yaml(self._manifest_path)
        self._audit.record(
            "rollback",
            release=manifest.release,
            stage=result.stage,
            version=result.version,
            digest=result.digest,
            manifest=str(self._manifest_path),
        )
        return result

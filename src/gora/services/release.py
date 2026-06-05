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
from typing import TYPE_CHECKING

from gora.audit.logger import AuditLogger
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import PromotionResult, ReleaseManifest

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class CutResult:
    """Outcome of a cut-rc operation."""

    candidate: ReleaseCandidate
    release: str
    superseded: str | None
    manifest_path: Path
    created: bool


def _derive_release(version: str) -> str:
    """Derive the release line from an RC version.

    ``1.2.0-rc.3`` -> ``1.2.0``. A version without a pre-release suffix is
    itself the release line. Used only when a manifest is created fresh and
    no explicit ``release`` is supplied.
    """
    base, _, _ = version.partition("-")
    return base


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

        Creates the manifest if it does not exist (deriving the release line
        from ``version`` unless ``release`` is given). A new candidate
        supersedes any prior active candidate on the release line (ADR-003).
        The candidate's digest is validated by the domain model.
        """
        created = not self._manifest_path.exists()
        if created:
            manifest = ReleaseManifest(release=release or _derive_release(version))
        else:
            manifest = ReleaseManifest.from_yaml(self._manifest_path)
            if release is not None and release != manifest.release:
                raise ValueError(
                    f"Release line mismatch: manifest is '{manifest.release}', "
                    f"requested '{release}'"
                )

        prior = manifest.candidate
        superseded = prior.version if prior is not None else None

        candidate = ReleaseCandidate(version=version, image=image, digest=digest)
        manifest.cut_candidate(candidate)
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

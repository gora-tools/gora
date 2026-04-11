"""Release Manifest model.

The manifest is the authoritative contract between artifact production
and deployment reconciliation. It lives in Git.
See ADR-003 for the full specification.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from enum import Enum

from pydantic import BaseModel, Field

from gora.domain.release.candidate import ReleaseCandidate


class ManifestVersion(str, Enum):
    """Manifest schema version. See ADR-003 for evolution path."""

    V01 = "v0.1"  # Single artifact
    V02 = "v0.2"  # Release BOM (multi-artifact)


class StageState(BaseModel):
    """State of a single deployment stage.

    Represents what is currently deployed in a specific environment.
    Stages represent deployed state, not intended future state (ADR-003).
    """

    version: str = Field(..., description="Version deployed to this stage")
    digest: str = Field(
        ...,
        description="Immutable artifact digest",
        pattern=r"^sha256:[a-f0-9]{64}$",
    )


class ReleaseManifest(BaseModel):
    """Git-native release contract (v0.1 — single artifact).

    Rules (from ADR-003):
    - Candidate MUST map 1:1 to an immutable artifact digest
    - Same digest MUST be promoted across stages
    - Final promotion MUST reuse the validated candidate digest
    - Stages represent deployed state, not intended future state
    """

    version: ManifestVersion = Field(
        default=ManifestVersion.V01,
        description="Manifest schema version",
    )
    release: str = Field(
        ...,
        description="Current release line, e.g. '1.2.0'",
        examples=["1.2.0"],
    )
    candidate: ReleaseCandidate | None = Field(
        default=None,
        description="Current active release candidate (None if no active RC)",
    )
    stages: dict[str, StageState] = Field(
        default_factory=dict,
        description="Deployment state per environment/stage",
    )

    def promote(self, target_stage: str) -> PromotionResult:
        """Promote current candidate to target stage.

        Promotion moves the same digest — no rebuild, no mutation (ADR-004).
        """
        if self.candidate is None:
            return PromotionResult(
                success=False,
                error="No active release candidate to promote",
            )

        self.stages[target_stage] = StageState(
            version=self.candidate.version,
            digest=self.candidate.digest,
        )

        return PromotionResult(
            success=True,
            version=self.candidate.version,
            digest=self.candidate.digest,
            stage=target_stage,
        )

    def rollback(self, target_stage: str, to_version: str, to_digest: str) -> PromotionResult:
        """Rollback stage to a known prior state.

        Rollback restores a prior known release state by pointing
        to a previously validated digest (ADR-004).
        """
        self.stages[target_stage] = StageState(
            version=to_version,
            digest=to_digest,
        )

        return PromotionResult(
            success=True,
            version=to_version,
            digest=to_digest,
            stage=target_stage,
        )

    def cut_candidate(self, candidate: ReleaseCandidate) -> None:
        """Set a new active release candidate.

        A new RC supersedes the previous active RC (ADR-003).
        """
        self.candidate = candidate

    @classmethod
    def from_yaml(cls, path: Path) -> ReleaseManifest:
        """Load manifest from YAML file."""
        content = path.read_text()
        data = yaml.safe_load(content)
        return cls.model_validate(data)

    def to_yaml(self, path: Path) -> None:
        """Write manifest to YAML file."""
        data = self.model_dump(mode="json", exclude_none=True)
        content = yaml.dump(data, default_flow_style=False, sort_keys=False)
        path.write_text(content)


class PromotionResult(BaseModel):
    """Result of a promotion or rollback operation."""

    success: bool
    version: str | None = None
    digest: str | None = None
    stage: str | None = None
    error: str | None = None

"""Release Manifest model.

The manifest is the authoritative contract between artifact production
and deployment reconciliation. It lives in Git.
See ADR-003 for the full specification.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, Field

from gora.domain.release.candidate import ReleaseCandidate


class ManifestVersion(StrEnum):
    """Manifest schema versions this engine can represent. See ADR-003 for evolution path.

    v0.2 (Release BOM, multi-artifact) is specified in ADR-003 but not implemented:
    it joins this enum together with the model that can hold it, not before.
    """

    V01 = "v0.1"  # Single artifact


class UnsupportedManifestError(ValueError):
    """The manifest is a document this engine cannot represent without loss.

    Raised before validation, so nothing is ever written back over such a document:
    an unknown schema version, or a field schema v0.1 does not define.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Unsupported manifest {path}: {reason}")


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
        """Load manifest from YAML file.

        The root ``version`` is checked first (ADR-003), then every field against the
        schema; a document that fails either raises ``UnsupportedManifestError``.
        """
        content = path.read_text()
        data = yaml.safe_load(content)
        _check_schema(data, path)
        return cls.model_validate(data)

    def to_yaml_str(self) -> str:
        """Serialize the manifest to a YAML string."""
        data = self.model_dump(mode="json", exclude_none=True)
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    def to_yaml(self, path: Path) -> None:
        """Write manifest to YAML file."""
        path.write_text(self.to_yaml_str())


def _check_schema(data: object, path: Path) -> None:
    """Refuse a document the v0.1 model would load with loss.

    A missing ``version`` is the model default, v0.1. Shape errors that lose nothing
    (a wrong type, a missing required field) are left to validation.
    """
    if not isinstance(data, dict):
        return
    version = data.get("version", ManifestVersion.V01.value)
    supported = [v.value for v in ManifestVersion]
    if not isinstance(version, str) or version not in supported:
        raise UnsupportedManifestError(
            path,
            f"schema version {version!r} is not supported (supported: {', '.join(supported)})",
        )
    unknown = _unknown_fields(data, ReleaseManifest, "")
    if unknown:
        raise UnsupportedManifestError(
            path,
            f"fields not defined by schema {version}: {', '.join(unknown)}",
        )


def _unknown_fields(data: object, model: type[BaseModel], where: str) -> list[str]:
    """Dotted paths of keys in ``data`` that ``model`` (recursively) does not define."""
    if not isinstance(data, dict):
        return []
    found = [f"{where}{key}" for key in data if key not in model.model_fields]
    for name, field in model.model_fields.items():
        if name in data:
            found += _unknown_in(data[name], field.annotation, f"{where}{name}.")
    return found


def _unknown_in(value: object, annotation: Any, where: str) -> list[str]:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        models = [a for a in get_args(annotation) if _is_model(a)]
        return _unknown_fields(value, models[0], where) if len(models) == 1 else []
    if origin is dict and isinstance(value, dict):
        _, item = get_args(annotation)
        return [f for key, v in value.items() for f in _unknown_in(v, item, f"{where}{key}.")]
    if _is_model(annotation):
        return _unknown_fields(value, annotation, where)
    return []


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


class PromotionResult(BaseModel):
    """Result of a promotion or rollback operation."""

    success: bool
    version: str | None = None
    digest: str | None = None
    stage: str | None = None
    error: str | None = None

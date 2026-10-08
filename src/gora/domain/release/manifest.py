"""Release Manifest model.

The manifest is the authoritative contract between artifact production
and deployment reconciliation. It lives in Git.
See ADR-003 for the full specification.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from types import UnionType
from typing import Any, Self, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.identifiers import (
    STAGE,
    Digest,
    ReleaseLine,
    StageName,
    Version,
    release_line_of,
)


class ManifestVersion(StrEnum):
    """Manifest schema versions this engine can represent. See ADR-003 for evolution path.

    v0.2 (Release BOM, multi-artifact) is specified in ADR-003 but not implemented:
    it joins this enum together with the model that can hold it, not before.
    """

    V01 = "v0.1"  # Single artifact


class ManifestLoadError(ValueError):
    """The manifest file cannot be loaded into the model.

    Raised by ``ReleaseManifest.from_yaml`` before any model exists, so no command
    can write back over the file. Used as is for a file that cannot be read, is not
    YAML, is not a mapping, or fails validation.
    """

    kind = "Cannot load manifest"

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{self.kind} {path}: {reason}")

    @property
    def summary(self) -> str:
        """The refusal without the file path, for callers that must not disclose it."""
        return f"{self.kind}: {self.reason}"


class UnsupportedManifestError(ManifestLoadError):
    """The manifest is a document this engine cannot represent without loss:
    an unknown schema version, or a field schema v0.1 does not define."""

    kind = "Unsupported manifest"


class ContractViolationError(ValueError):
    """An operation would break an invariant of the release contract (ADR-003).

    Raised before anything changes: the manifest object, and so the file, stays as it
    was. The reason names the rule, never a value.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# The reasons a ContractViolationError carries. Value-free on purpose: the operator typed the
# values and the manifest holds them; the reason says which rule they break.
VERSION_TAKEN = (
    "the version is already recorded in {where} with another digest; "
    "a version never changes content (ADR-003)"
)
OTHER_RELEASE_LINE = "the version's MAJOR.MINOR.PATCH is not the manifest's release line (ADR-003)"
OTHER_IMAGE = (
    "the image is not the active candidate's image; a v0.1 manifest holds one image (ADR-003)"
)
NO_SUCH_STAGE = "the stage does not exist; a rollback targets an existing stage only (ADR-004)"


class StageState(BaseModel):
    """State of a single deployment stage.

    The version and digest the stage holds, not an intended future state (ADR-003).
    """

    model_config = ConfigDict(extra="forbid")

    version: Version = Field(..., description="Version the stage holds")
    digest: Digest = Field(..., description="Immutable artifact digest")


class ReleaseManifest(BaseModel):
    """Git-native release contract (v0.1 — single artifact).

    Rules (from ADR-003):
    - Candidate MUST map 1:1 to an immutable artifact digest
    - Same digest MUST be promoted across stages
    - Final promotion MUST reuse the validated candidate digest
    - A stage records the version and digest it holds, not an intended future state

    Enforced here (ADR-003, "Invariants"): every identifier against its grammar; within
    the manifest a version maps to one digest; the candidate's MAJOR.MINOR.PATCH is the
    release line; one image per manifest; a rollback targets an existing stage. A model
    that breaks the first two cannot be built or loaded. The operations check the rest
    and raise ``ContractViolationError`` before they change anything.
    """

    model_config = ConfigDict(extra="forbid")

    version: ManifestVersion = Field(
        default=ManifestVersion.V01,
        description="Manifest schema version",
    )
    release: ReleaseLine = Field(
        ...,
        description="Current release line, e.g. '1.2.0'",
        examples=["1.2.0"],
    )
    candidate: ReleaseCandidate | None = Field(
        default=None,
        description="Current active release candidate (absent or null if there is none)",
    )
    stages: dict[StageName, StageState] = Field(
        default_factory=dict,
        description="Deployment state per environment/stage",
    )

    @model_validator(mode="after")
    def _holds_the_contract(self) -> Self:
        problems = _contract_problems(self)
        if problems:
            raise PydanticCustomError("release_contract", "; ".join(problems))
        return self

    def promote(self, target_stage: str) -> PromotionResult:
        """Promote current candidate to target stage.

        Promotion moves the same digest — no rebuild, no mutation (ADR-004).
        """
        if self.candidate is None:
            return PromotionResult(
                success=False,
                error="No active release candidate to promote",
            )

        state = StageState(version=self.candidate.version, digest=self.candidate.digest)
        self.stages[_stage(target_stage)] = state

        return PromotionResult(
            success=True,
            version=self.candidate.version,
            digest=self.candidate.digest,
            stage=target_stage,
            image=self.candidate.image,
        )

    def rollback(self, target_stage: str, to_version: str, to_digest: str) -> PromotionResult:
        """Rollback stage to a known prior state.

        Rollback restores a prior known release state by pointing
        to a previously validated digest (ADR-004). The stage must exist, and the version
        must not be recorded anywhere in the manifest with another digest.
        """
        state = StageState(version=to_version, digest=to_digest)
        if target_stage not in self.stages:
            raise ContractViolationError(NO_SUCH_STAGE)
        self._check_version_free(state.version, state.digest)

        self.stages[target_stage] = state

        return PromotionResult(
            success=True,
            version=to_version,
            digest=to_digest,
            stage=target_stage,
            image=self.candidate.image if self.candidate is not None else None,
        )

    def cut_candidate(self, candidate: ReleaseCandidate) -> bool:
        """Set a new active release candidate; False when it already is the active one.

        A new RC replaces the previous active RC (ADR-003). The same version, digest and
        image as the active candidate change nothing. Anything else must keep the version's
        digest, be on the release line, and carry the manifest's one image.
        """
        active = self.candidate
        if active is not None and _identity(active) == _identity(candidate):
            return False
        if release_line_of(candidate.version) != self.release:
            raise ContractViolationError(OTHER_RELEASE_LINE)
        self._check_version_free(candidate.version, candidate.digest)
        if active is not None and candidate.image != active.image:
            raise ContractViolationError(OTHER_IMAGE)
        self.candidate = candidate
        return True

    def _check_version_free(self, version: str, digest: str) -> None:
        """Refuse a version the manifest already records with another digest."""
        for where, entry in _entries(self):
            if entry.version == version and entry.digest != digest:
                kind = "the active candidate" if where == "candidate" else "a stage"
                raise ContractViolationError(VERSION_TAKEN.format(where=kind))

    @classmethod
    def from_yaml(cls, path: Path) -> ReleaseManifest:
        """Load manifest from YAML file.

        The root ``version`` is checked first (ADR-003), then every field against the
        schema; a document that fails either raises ``UnsupportedManifestError``. Any
        other file that cannot be loaded raises its base, ``ManifestLoadError``.
        """
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ManifestLoadError(path, f"cannot read file: {exc.strerror}") from exc
        except UnicodeDecodeError as exc:
            raise ManifestLoadError(path, "file is not UTF-8 text") from exc
        except yaml.YAMLError as exc:
            raise ManifestLoadError(path, f"invalid YAML: {_yaml_problem(exc)}") from exc
        if data is None:
            raise ManifestLoadError(path, "file is empty")
        if not isinstance(data, dict):
            raise ManifestLoadError(
                path, f"expected a mapping at the top level, got {_yaml_kind(data)}"
            )
        _check_schema(data, path)
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ManifestLoadError(path, _validation_problem(exc)) from exc

    def to_yaml_str(self) -> str:
        """Serialize the manifest to a YAML string."""
        data = self.model_dump(mode="json", exclude_none=True)
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    def to_yaml(self, path: Path) -> None:
        """Write manifest to YAML file."""
        path.write_text(self.to_yaml_str())


def _yaml_problem(exc: yaml.YAMLError) -> str:
    """The parser's own description and position, without the stream name."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        return f"{exc.problem} (line {mark.line + 1}, column {mark.column + 1})"
    return str(exc)


def _yaml_kind(data: object) -> str:
    return "a sequence" if isinstance(data, list) else f"a scalar ({type(data).__name__})"


def _validation_problem(exc: ValidationError) -> str:
    """Field path and message per error; input values are left out.

    A broken invariant names its own field paths. A stage name that breaks the grammar is
    reported at its own path, without pydantic's ``[key]`` marker.
    """
    problems = []
    for err in exc.errors():
        if err["type"] == "release_contract":
            problems.append(err["msg"])
            continue
        loc = err["loc"][:-1] if err["loc"][-1:] == ("[key]",) else err["loc"]
        problems.append(f"{'.'.join(str(part) for part in loc) or '(root)'}: {err['msg']}")
    return "; ".join(problems)


def _identity(candidate: ReleaseCandidate) -> tuple[str, str, str]:
    return (candidate.version, candidate.digest, candidate.image)


def _entries(manifest: ReleaseManifest) -> list[tuple[str, ReleaseCandidate | StageState]]:
    """Every (path, entry) that records a version and a digest: candidate first, then stages."""
    entries: list[tuple[str, ReleaseCandidate | StageState]] = []
    if manifest.candidate is not None:
        entries.append(("candidate", manifest.candidate))
    entries += [(f"stages.{name}", state) for name, state in manifest.stages.items()]
    return entries


def _contract_problems(manifest: ReleaseManifest) -> list[str]:
    """The invariants a manifest must already hold, each named by its field paths."""
    problems = []
    candidate = manifest.candidate
    if candidate is not None and release_line_of(candidate.version) != manifest.release:
        problems.append(
            "candidate.version: its MAJOR.MINOR.PATCH must equal release, the release line"
        )
    first: dict[str, tuple[str, str]] = {}  # version -> (path, digest)
    for where, entry in _entries(manifest):
        seen = first.setdefault(entry.version, (where, entry.digest))
        if seen[1] != entry.digest:
            problems.append(
                f"{where}.version: the same version is recorded at {seen[0]}.version with "
                "another digest; a version maps to one digest"
            )
    return problems


def _stage(name: str) -> str:
    """A stage name the grammar accepts; ``ContractViolationError`` otherwise."""
    problem = STAGE.problem(name)
    if problem is not None:
        raise ContractViolationError(f"stage name {problem}")
    return name


def _check_schema(data: dict[Any, Any], path: Path) -> None:
    """Refuse a document the v0.1 model would load with loss.

    A missing ``version`` is the model default, v0.1. Shape errors that lose nothing
    (a wrong type, a missing required field) are left to validation.
    """
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
    """Result of a promotion or rollback operation.

    ``image`` is the manifest's one image, when it has an active candidate to name it.
    """

    success: bool
    version: str | None = None
    digest: str | None = None
    stage: str | None = None
    image: str | None = None
    error: str | None = None

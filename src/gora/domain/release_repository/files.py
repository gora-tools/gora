"""The layout of a release repository and the parsing of one release file (ADR-026 sections 2, 3).

Pure: a path and bytes in, a model or problems out. ``locate`` says what a path is in the
layout; ``parse`` reads one file through the strict loader and its model, and reports what is
wrong as ``InvalidReleaseFileError``: the file, the field path and a constant rule, never a value
from the document. The rules between files are not here.
"""

import itertools
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from gora.domain.release.identifiers import FINAL_VERSION, NAME, STAGE, VERSION
from gora.domain.release_repository import v1alpha1, yaml12

POLICY_PATH = "release-policy.yaml"
LAYOUT_ROOTS = ("artifacts", "state", "rejections")
TOP_LEVEL = ("apiVersion", "kind", "metadata", "spec", "writtenBy")
SUPPORTED = (v1alpha1.API_VERSION,)
# What names a schema version, whether this GORA supports it or not (ADR-026 section 12).
_API_VERSION = re.compile(r"^gora\.tools/v[1-9][0-9]{0,2}(?:(?:alpha|beta)[1-9][0-9]{0,2})?$")

RULE_LAYOUT = "not a file of the release layout"
RULE_REQUIRED = "required"
RULE_MAPPING = "must be a mapping"
RULE_ORDER = "top-level fields must be in the order " + ", ".join(TOP_LEVEL)
RULE_API_VERSION = "is not a schema version this GORA supports (it supports {supported})"
RULE_SCHEMA_VERSION = (
    "schema version {version} is not supported by this GORA (it supports {supported})"
)
RULE_WRITTEN_BY = "; written by GORA {written_by}"
RULE_KIND = "must be {kind}, as the path says"
RULE_METADATA = "must be the {part} in the path, {layout}"


@dataclass(frozen=True)
class Location:
    """What a path is in the layout: its kind, and the names the path gives."""

    kind: str
    artifact: str | None = None
    stage: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a release file: where, and by which rule."""

    source: str
    path: str
    line: int | None
    column: int | None
    rule: str

    def __str__(self) -> str:
        where = self.source if self.line is None else f"{self.source}:{self.line}:{self.column}"
        return f"{where}: {self.path}: {self.rule}"


class InvalidReleaseFileError(ValueError):
    """A release file refused, with every problem of the step that refused it."""

    def __init__(self, problems: tuple[Problem, ...]) -> None:
        self.problems = problems
        super().__init__("\n".join(str(problem) for problem in problems))


@dataclass(frozen=True)
class ReleaseFile:
    """A release file that holds: where it is, the document as loaded, and its model.

    ``document`` is what the loader returned, no default filled in, for the policy digest
    (ADR-026 section 15).
    """

    location: Location
    document: dict[str, object]
    model: v1alpha1.Policy | v1alpha1.Artifact | v1alpha1.StageState | v1alpha1.Rejection


def shown(path: str) -> str:
    """A path as it may be written to a terminal: every character outside printable ASCII
    escaped."""
    out = []
    for char in path:
        code = ord(char)
        if 0x20 <= code <= 0x7E and char != "\\":
            out.append(char)
        elif char == "\\":
            out.append("\\\\")
        elif code <= 0xFF:
            out.append(f"\\x{code:02x}")
        elif code <= 0xFFFF:
            out.append(f"\\u{code:04x}")
        else:
            out.append(f"\\U{code:08x}")
    return "".join(out)


def locate(path: str) -> Location | None:
    """What ``path``, POSIX and relative to the repository's root, is in the layout.

    None for a path that is not a release file; ``InvalidReleaseFileError`` for a path under
    ``artifacts/``, ``state/`` or ``rejections/`` that is not one of the layout's files.
    """
    if path == POLICY_PATH:
        return Location("Policy")
    parts = path.split("/")
    if len(parts) < 2 or parts[0] not in LAYOUT_ROOTS:
        return None
    location = _in_layout(parts)
    if location is None:
        raise InvalidReleaseFileError((Problem(shown(path), yaml12.ROOT, None, None, RULE_LAYOUT),))
    return location


def _in_layout(parts: list[str]) -> Location | None:
    if len(parts) != 3:
        return None
    root, directory, file = parts
    stem = file.removesuffix(".yaml") if file.endswith(".yaml") else None
    if root == "artifacts" and file == "artifact.yaml" and NAME.is_valid(directory):
        return Location("Artifact", artifact=directory)
    if root == "state" and stem and STAGE.is_valid(directory) and NAME.is_valid(stem):
        return Location("StageState", stage=directory, artifact=stem)
    if root == "rejections" and stem and NAME.is_valid(directory) and FINAL_VERSION.is_valid(stem):
        return Location("Rejection", artifact=directory, version=stem)
    return None


def parse(path: str, data: bytes) -> ReleaseFile:
    """The release file at ``path`` holding ``data``, or ``InvalidReleaseFileError``.

    In order, stopping at the first step that fails: the layout, the loader, a mapping, the
    schema version, the top-level order, the kind, then the model's fields and, once they all
    hold, the model's rules and the metadata against the path, reported together.
    """
    location = locate(path)
    if location is None:
        raise ValueError("not a release file")
    try:
        document = yaml12.load(data, source=path)
    except yaml12.ReleaseFileError as exc:
        raise InvalidReleaseFileError(
            (Problem(exc.source, exc.path, exc.line, exc.column, exc.rule),)
        ) from None
    if not isinstance(document, dict):
        _refuse(path, yaml12.ROOT, RULE_MAPPING)
    assert isinstance(document, dict)
    _check_api_version(path, location, document)
    _check_order(path, document)
    _check_kind(path, location, document)
    model_type = v1alpha1.MODELS[location.kind]
    try:
        model = model_type.model_validate(document)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        rules = [e for e in errors if e["type"] == "release_rules"]
        if len(rules) != len(errors):
            raise InvalidReleaseFileError(_field_problems(path, document, errors)) from None
        problems = _metadata_problems(location, document)
        for error in rules:
            problems += error["ctx"]["problems"]
        raise InvalidReleaseFileError(_problems(path, problems)) from None
    metadata = _metadata_problems(location, document)
    if metadata:
        raise InvalidReleaseFileError(_problems(path, metadata))
    assert isinstance(
        model, v1alpha1.Policy | v1alpha1.Artifact | v1alpha1.StageState | v1alpha1.Rejection
    )
    return ReleaseFile(location, document, model)


def _refuse(source: str, path: str, rule: str) -> None:
    raise InvalidReleaseFileError((Problem(source, path, None, None, rule),))


def _problems(source: str, pairs: list[tuple[str, str]]) -> tuple[Problem, ...]:
    return tuple(Problem(source, path, None, None, rule) for path, rule in pairs)


def _check_api_version(source: str, location: Location, document: dict[str, Any]) -> None:
    """Step 4: the schema version, before anything else of the document."""
    supported = ", ".join(SUPPORTED)
    if "apiVersion" not in document:
        _refuse(source, "apiVersion", RULE_REQUIRED)
    value = document["apiVersion"]
    if isinstance(value, str) and value in SUPPORTED:
        return
    if not (isinstance(value, str) and _API_VERSION.fullmatch(value)):
        _refuse(source, "apiVersion", RULE_API_VERSION.format(supported=supported))
    rule = RULE_SCHEMA_VERSION.format(version=value, supported=supported)
    written_by = document.get("writtenBy")
    if (
        location.kind in ("StageState", "Rejection")
        and isinstance(written_by, str)
        and VERSION.is_valid(written_by)
    ):
        rule += RULE_WRITTEN_BY.format(written_by=written_by)
    _refuse(source, "apiVersion", rule)


def _check_order(source: str, document: dict[str, Any]) -> None:
    """Step 5: the known top-level fields appear in the order of ADR-026 section 3."""
    known = [name for name in document if name in TOP_LEVEL]
    for earlier, later in itertools.pairwise(known):
        if TOP_LEVEL.index(later) < TOP_LEVEL.index(earlier):
            _refuse(source, later, RULE_ORDER)


def _check_kind(source: str, location: Location, document: dict[str, Any]) -> None:
    """Step 6: the kind is the one the path says."""
    if "kind" not in document:
        _refuse(source, "kind", RULE_REQUIRED)
    if document["kind"] != location.kind:
        _refuse(source, "kind", RULE_KIND.format(kind=location.kind))


_LAYOUTS = {
    "Artifact": "artifacts/<name>/artifact.yaml",
    "StageState": "state/<stage>/<artifact>.yaml",
    "Rejection": "rejections/<artifact>/<version>.yaml",
}
_METADATA = {
    "Artifact": (("name", "artifact", "name"),),
    "StageState": (("stage", "stage", "stage"), ("artifact", "artifact", "artifact")),
    "Rejection": (("artifact", "artifact", "artifact"), ("version", "version", "version")),
}


def _metadata_problems(location: Location, document: dict[str, Any]) -> list[tuple[str, str]]:
    """The metadata names what the path names; read once every field is valid."""
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return []
    layout = _LAYOUTS.get(location.kind, "")
    problems = []
    for field, attribute, part in _METADATA.get(location.kind, ()):
        if metadata.get(field) != getattr(location, attribute):
            problems.append((f"metadata.{field}", RULE_METADATA.format(part=part, layout=layout)))
    return problems


# --- rendering pydantic's errors -----------------------------------------------------------------


def _field_names(models: tuple[type[BaseModel], ...]) -> frozenset[str]:
    """Every field name of every model, as the file spells it: the only names a path shows
    that are not keys of the document."""
    return frozenset(
        field.alias or name for model in models for name, field in model.model_fields.items()
    )


FIELD_NAMES = _field_names(
    (
        v1alpha1.Policy,
        v1alpha1.PolicySpec,
        v1alpha1.Stage,
        v1alpha1.IdentitySigner,
        v1alpha1.KeySigner,
        v1alpha1.Requests,
        v1alpha1.Evidence,
        v1alpha1.Sbom,
        v1alpha1.Artifact,
        v1alpha1.ArtifactMetadata,
        v1alpha1.ArtifactSpec,
        v1alpha1.Source,
        v1alpha1.StageAddition,
        v1alpha1.StageState,
        v1alpha1.StageStateMetadata,
        v1alpha1.StageStateSpec,
        v1alpha1.StageEvidence,
        v1alpha1.Rejection,
        v1alpha1.RejectionMetadata,
        v1alpha1.RejectionSpec,
    )
)


def _field_problems(
    source: str, document: dict[str, Any], errors: list[Any]
) -> tuple[Problem, ...]:
    return tuple(
        Problem(source, render_path(document, error["loc"]), None, None, rule(error))
        for error in errors
    )


def render_path(document: object, loc: tuple[int | str, ...]) -> str:
    """A pydantic location as the loader writes a field path, walking the document beside it.

    A key of the document is shown when it matches the loader's key pattern, else as
    ``<key N>``; a field name a model has and the document lacks is shown as the file spells
    it; pydantic's own markers (``[key]``, a union tag) never appear.
    """
    path = yaml12.ROOT
    node: object = document
    for item in loc:
        if isinstance(item, int):
            path = f"{path}[{item}]"
            node = node[item] if isinstance(node, list) and 0 <= item < len(node) else None
        elif isinstance(node, dict) and item in node:
            position = list(node).index(item) + 1
            path = _child(path, v1alpha1.key("", item, position)[1:])
            node = node[item]
        elif item in FIELD_NAMES:
            path = _child(path, item)
            node = None
    return path


def _child(path: str, name: str) -> str:
    return name if path == yaml12.ROOT else f"{path}.{name}"


def _literal_rule(ctx: dict[str, Any]) -> str:
    values = re.findall(r"'([^']*)'", str(ctx.get("expected", "")))
    return "must be one of: " + ", ".join(values)


_RULES: dict[str, Any] = {
    "missing": lambda ctx: RULE_REQUIRED,
    "extra_forbidden": lambda ctx: "unknown field",
    "literal_error": _literal_rule,
    "string_type": lambda ctx: "must be a string",
    "int_type": lambda ctx: "must be an integer",
    "bool_type": lambda ctx: "must be true or false",
    "dict_type": lambda ctx: RULE_MAPPING,
    "model_type": lambda ctx: RULE_MAPPING,
    "list_type": lambda ctx: "must be a list",
    "too_short": lambda ctx: "must not be empty",
    "greater_than_equal": lambda ctx: f"must be at least {ctx['ge']}",
    "less_than_equal": lambda ctx: f"must be at most {ctx['le']}",
}


def rule(error: Any) -> str:
    """GORA's constant text for one pydantic error, never pydantic's message."""
    kind = error["type"]
    if kind == "release_identifier":
        return str(error["msg"])
    render = _RULES.get(kind)
    if render is None:
        return f"invalid ({kind})"
    return str(render(error.get("ctx") or {}))

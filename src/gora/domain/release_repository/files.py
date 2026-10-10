"""The layout of a release repository and the parsing of one release file (ADR-026 sections 2, 3).

Pure: a path and bytes in, a model or problems out. ``locate`` says what a path is in the
layout; ``parse`` reads one file through the strict loader and its model, and reports what is
wrong as ``InvalidReleaseFileError``: the file, the field path and a constant rule, never a value
from the document. The rules between files are not here.
"""

import itertools
import re
import types
from dataclasses import dataclass
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel, Tag, ValidationError

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
    hold, the model's rules and the metadata against the path, reported together in the order
    the document holds their paths.
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
            raise InvalidReleaseFileError(
                _field_problems(path, model_type, document, errors)
            ) from None
        problems = _metadata_problems(location, document)
        for error in rules:
            problems += error["ctx"]["problems"]
        raise InvalidReleaseFileError(_problems(path, _in_order(document, problems))) from None
    metadata = _metadata_problems(location, document)
    if metadata:
        raise InvalidReleaseFileError(_problems(path, _in_order(document, metadata)))
    assert isinstance(
        model, v1alpha1.Policy | v1alpha1.Artifact | v1alpha1.StageState | v1alpha1.Rejection
    )
    return ReleaseFile(location, document, model)


def _refuse(source: str, path: str, rule: str) -> None:
    raise InvalidReleaseFileError((Problem(source, path, None, None, rule),))


def _problems(source: str, pairs: list[tuple[str, str]]) -> tuple[Problem, ...]:
    return tuple(Problem(source, path, None, None, rule) for path, rule in pairs)


# One step of a field path as the loader writes it: an item [N], a key <key N>, or a name.
_PATH_STEP = re.compile(r"\[(\d+)\]|<key (\d+)>|([^.\[<]+)")


def _place(document: object, path: str) -> tuple[int, ...]:
    """Where a path stands in the document: each step's position among its parent's entries.

    A field the document lacks, which a rule may name (prod's approvers), stands after its
    parent's entries.
    """
    place: list[int] = []
    node: object = document
    for item, key, name in _PATH_STEP.findall(path):
        if isinstance(node, list) and item:
            position = int(item)
            node = node[position] if position < len(node) else None
        elif isinstance(node, dict):
            entries = list(node)
            if key:
                position = int(key) - 1
            else:
                position = entries.index(name) if name in node else len(entries)
            node = node[entries[position]] if position < len(entries) else None
        else:
            break
        place.append(position)
    return tuple(place)


def _in_order(document: object, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Rule problems in the order the document holds their paths, as a reader meets them."""
    return sorted(pairs, key=lambda pair: _place(document, pair[0]))


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


# pydantic's marker after a mapping key whose own validation failed.
KEY_MARKER = "[key]"
# Where ``loc`` holds an element the models' shape does not expect, the path ends with this.
UNEXPECTED = "<unexpected>"


def _field_problems(
    source: str, model: type[BaseModel], document: dict[str, Any], errors: list[Any]
) -> tuple[Problem, ...]:
    return tuple(
        Problem(
            source,
            render_path(model, document, error["loc"], error["type"]),
            None,
            None,
            rule(error),
        )
        for error in errors
    )


def _shape(annotation: object) -> tuple[str, Any]:
    """The place an annotation makes, as far as pydantic's ``loc`` goes into it.

    ``("model", the model)``, ``("mapping", the value's annotation)``, ``("list", the item's
    annotation)``, ``("union", the model of each tag)`` or ``("scalar", None)``.
    """
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "model", annotation
    origin = get_origin(annotation)
    if origin is dict:
        return "mapping", get_args(annotation)[1]
    if origin is list:
        return "list", get_args(annotation)[0]
    if origin is Union or origin is types.UnionType:
        tags: dict[str, Any] = {}
        for member in get_args(annotation):
            inner, *metadata = get_args(member) or (member,)
            tags |= {item.tag: inner for item in metadata if isinstance(item, Tag)}
        return "union", tags
    return "scalar", None


def render_path(
    model: type[BaseModel], document: object, loc: tuple[int | str, ...], error_type: str
) -> str:
    """A pydantic location as the loader writes a field path, read by the models' shape.

    Each element of ``loc`` is read by its place in the shape, from ``model`` down, never by
    what the document holds. Under a model it is a field, shown as the file spells it, or, for
    an unknown field, the document's key; under a mapping, a document key; under a list, an
    index; under the signer union, its tag, dropped. A key is shown when it matches the
    loader's key pattern, else as ``<key N>``: the document is read only for that position.

    A ``[key]`` as the last element, right after a mapping's key, is pydantic's marker of an
    error of that key, dropped; but for an unknown field of the model the mapping holds, whose
    ``loc`` ends the same way, the error's type tells them apart. A mapping whose value is a
    mapping would make the marker ambiguous (an error of the outer key and an error under an
    inner key spelled ``[key]`` have one ``loc``), so the models hold none: ``nested_mappings``
    finds them, and a test holds it at none. An element the shape does not expect, which only a
    change of pydantic could produce, ends the path with ``<unexpected>``; this never raises.
    """
    path = yaml12.ROOT
    kind: str = "model"
    inner: Any = model
    node: object = document
    last = len(loc) - 1
    for at, item in enumerate(loc):
        if kind == "model" and isinstance(item, str):
            fields = {field.alias or name: field for name, field in inner.model_fields.items()}
            if item in fields:
                path = _child(path, item)
                kind, inner = _shape(fields[item].annotation)
                node = node.get(item) if isinstance(node, dict) else None
                continue
            if error_type == "extra_forbidden" and at == last:
                return _child(path, _shown_key(node, item))
        elif kind == "mapping" and isinstance(item, str):
            shown = _shown_key(node, item)
            path = _child(path, shown)
            marker = at == last - 1 and loc[last] == KEY_MARKER and error_type != "extra_forbidden"
            if marker or shown == UNEXPECTED:
                return path
            kind, inner = _shape(inner)
            node = node.get(item) if isinstance(node, dict) else None
            continue
        elif kind == "list" and isinstance(item, int):
            path = f"{path}[{item}]"
            kind, inner = _shape(inner)
            node = node[item] if isinstance(node, list) and 0 <= item < len(node) else None
            continue
        elif kind == "union" and isinstance(item, str) and item in inner:
            kind, inner = _shape(inner[item])
            continue
        return _child(path, UNEXPECTED)
    return path


def _shown_key(mapping: object, key: str) -> str:
    """A document key as the loader shows it: the key, or ``<key N>`` by its position."""
    if yaml12.PATH_KEY.fullmatch(key):
        return key
    if isinstance(mapping, dict) and key in mapping:
        return f"<key {list(mapping).index(key) + 1}>"
    return UNEXPECTED


def nested_mappings(model: type[BaseModel]) -> list[str]:
    """Each field reachable from ``model``, as ``Model.field``, that holds a mapping whose value
    is a mapping, where ``render_path`` could not tell a ``[key]`` marker from a key.

    A mapping whose value is a model is not one: an unknown field of that model has its own
    error type. Nor is a mapping whose value is a list or the signer union: an index or a tag
    stands between the two keys.
    """
    found: list[str] = []
    seen: set[type[BaseModel]] = set()

    def walk(owner: str, annotation: object) -> None:
        kind, inner = _shape(annotation)
        if kind == "model" and inner not in seen:
            seen.add(inner)
            for name, field in inner.model_fields.items():
                walk(f"{inner.__name__}.{field.alias or name}", field.annotation)
        elif kind == "mapping":
            if _shape(inner)[0] == "mapping":
                found.append(owner)
            walk(owner, inner)
        elif kind == "list":
            walk(owner, inner)
        elif kind == "union":
            for member in inner.values():
                walk(owner, member)

    walk(model.__name__, model)
    return found


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

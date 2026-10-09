"""The strict YAML 1.2 loader of the release files (ADR-026 section 10).

Pure: callers pass bytes. Only ruamel.yaml's parser is used, its event stream; every value is
built here from the events, under the YAML 1.2.2 core schema, so no ruamel constructor or
resolver can apply (timestamps, ``0b``, ``_`` in numbers, merge, value). A file that YAML 1.2
reads one way and YAML 1.1 another, or that an RFC 8785 serialisation cannot carry, is refused.

An error names the file, the field path and the rule, never a value or a refused key from the
document (ADR-003, "Identifier grammar").
"""

import math
import re
from collections.abc import Iterator

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.events import (
    AliasEvent,
    DocumentEndEvent,
    DocumentStartEvent,
    Event,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
    StreamStartEvent,
)

MAX_BYTES = 262144
MAX_SAFE_INTEGER = 2**53 - 1
ROOT = "$"

RULE_TOO_LARGE = "larger than 256 KiB"
RULE_NOT_UTF8 = "not UTF-8 text"
RULE_BOM = "starts with a byte order mark"
RULE_DIRECTIVE = "a directive (%YAML, %TAG) is not allowed"
RULE_NO_DOCUMENT = "holds no YAML document"
RULE_MANY_DOCUMENTS = "holds more than one YAML document"
RULE_ANCHOR = "an anchor is not allowed"
RULE_ALIAS = "an alias is not allowed"
RULE_TAG = "an explicit tag is not allowed"
RULE_KEY_NOT_SCALAR = "a mapping key must be a scalar"
RULE_KEY_NOT_STRING = "a mapping key must be a string"
RULE_MERGE_KEY = "the merge key << is not allowed"
RULE_DUPLICATE_KEY = "duplicate key"
RULE_INTEGER_RANGE = "an integer must be within -(2^53 - 1) and 2^53 - 1"
RULE_FLOAT_NOT_FINITE = "a float must be finite"
RULE_LONE_SURROGATE = "a string must not hold a lone surrogate"
RULE_NOT_YAML = "not valid YAML"

# The YAML 1.2.2 core schema, plain scalars only (section 10.3.2).
_NULL = re.compile(r"~|null|Null|NULL")
_TRUE = re.compile(r"true|True|TRUE")
_FALSE = re.compile(r"false|False|FALSE")
_INT10 = re.compile(r"[-+]?[0-9]+")
_INT8 = re.compile(r"0o[0-7]+")
_INT16 = re.compile(r"0x[0-9a-fA-F]+")
_FLOAT = re.compile(r"[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?")
_INF = re.compile(r"[-+]?(\.inf|\.Inf|\.INF)")
_NAN = re.compile(r"\.nan|\.NaN|\.NAN")
_SURROGATE = re.compile("[\ud800-\udfff]")
# A key is written into a field path only when it reads as a field name; any other key is
# named by its position, so no text of the document reaches a message.
_PATH_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,62}")


class ReleaseFileError(ValueError):
    """A release file refused: where, and by which rule; never what it holds.

    ``line`` and ``column`` are 1-based, None when the refusal comes before parsing.
    """

    def __init__(
        self, source: str, path: str, line: int | None, column: int | None, rule: str
    ) -> None:
        self.source = source
        self.path = path
        self.line = line
        self.column = column
        self.rule = rule
        where = source if line is None else f"{source}:{line}:{column}"
        super().__init__(f"{where}: {path}: {rule}")


def load(data: bytes, *, source: str) -> object:
    """The one document ``data`` holds, as dict, list, str, int, float, bool and None."""
    if len(data) > MAX_BYTES:
        raise ReleaseFileError(source, ROOT, None, None, RULE_TOO_LARGE)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ReleaseFileError(source, ROOT, None, None, RULE_NOT_UTF8) from None
    if text.startswith("\ufeff"):
        raise ReleaseFileError(source, ROOT, None, None, RULE_BOM)
    return _Loader(text, source).document()


def resolve_plain(value: str) -> object:
    """A plain scalar under the YAML 1.2.2 core schema; non-finite floats included."""
    if value == "" or _NULL.fullmatch(value):
        return None
    if _TRUE.fullmatch(value):
        return True
    if _FALSE.fullmatch(value):
        return False
    if _INT10.fullmatch(value):
        return int(value, 10)
    if _INT8.fullmatch(value):
        return int(value[2:], 8)
    if _INT16.fullmatch(value):
        return int(value[2:], 16)
    if _FLOAT.fullmatch(value):
        return float(value)
    if _INF.fullmatch(value):
        return -math.inf if value.startswith("-") else math.inf
    if _NAN.fullmatch(value):
        return math.nan
    return value


def _child(path: str, key: str) -> str:
    return key if path == ROOT else f"{path}.{key}"


class _Loader:
    """One parse: a fresh ruamel YAML object, whose state an exception would leave behind."""

    def __init__(self, text: str, source: str) -> None:
        self._text = text
        self._source = source
        self._events: Iterator[Event] = iter(YAML(typ="safe", pure=True).parse(text))

    def _refuse(self, event: Event, path: str, rule: str) -> ReleaseFileError:
        mark = event.start_mark
        return ReleaseFileError(self._source, path, mark.line + 1, mark.column + 1, rule)

    def _next(self) -> Event:
        try:
            return next(self._events)
        except MarkedYAMLError as exc:
            mark = exc.problem_mark or exc.context_mark
            line, column = (None, None) if mark is None else (mark.line + 1, mark.column + 1)
            raise ReleaseFileError(self._source, ROOT, line, column, RULE_NOT_YAML) from None
        except YAMLError as exc:
            # A reader error carries the offset of the character it refused, not a mark.
            line, column = self._position(getattr(exc, "position", None))
            raise ReleaseFileError(self._source, ROOT, line, column, RULE_NOT_YAML) from None

    def _position(self, offset: object) -> tuple[int | None, int | None]:
        if not isinstance(offset, int):
            return None, None
        line_start = self._text.rfind("\n", 0, offset) + 1
        return self._text.count("\n", 0, offset) + 1, offset - line_start + 1

    def document(self) -> object:
        start = self._next()
        assert isinstance(start, StreamStartEvent)
        event = self._next()
        if isinstance(event, StreamEndEvent):
            raise self._refuse(event, ROOT, RULE_NO_DOCUMENT)
        assert isinstance(event, DocumentStartEvent)
        if event.version is not None or event.tags:
            raise self._refuse(event, ROOT, RULE_DIRECTIVE)
        value = self._node(self._next(), ROOT)
        end = self._next()
        assert isinstance(end, DocumentEndEvent)
        event = self._next()
        if not isinstance(event, StreamEndEvent):
            raise self._refuse(event, ROOT, RULE_MANY_DOCUMENTS)
        return value

    def _check_node(self, event: Event, path: str) -> None:
        if isinstance(event, AliasEvent):
            raise self._refuse(event, path, RULE_ALIAS)
        if getattr(event, "anchor", None) is not None:
            raise self._refuse(event, path, RULE_ANCHOR)
        if getattr(event, "tag", None) is not None:
            raise self._refuse(event, path, RULE_TAG)

    def _node(self, event: Event, path: str) -> object:
        self._check_node(event, path)
        if isinstance(event, ScalarEvent):
            return self._scalar(event, path)
        if isinstance(event, SequenceStartEvent):
            return self._sequence(path)
        if isinstance(event, MappingStartEvent):
            return self._mapping(path)
        raise self._refuse(event, path, RULE_NOT_YAML)

    def _scalar(self, event: ScalarEvent, path: str) -> object:
        value: object = event.value if event.style is not None else resolve_plain(event.value)
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
            raise self._refuse(event, path, RULE_INTEGER_RANGE)
        if isinstance(value, float) and not math.isfinite(value):
            raise self._refuse(event, path, RULE_FLOAT_NOT_FINITE)
        if isinstance(value, str) and _SURROGATE.search(value):
            raise self._refuse(event, path, RULE_LONE_SURROGATE)
        return value

    def _sequence(self, path: str) -> list[object]:
        items: list[object] = []
        while not isinstance(event := self._next(), SequenceEndEvent):
            items.append(self._node(event, f"{path}[{len(items)}]"))
        return items

    def _mapping(self, path: str) -> dict[str, object]:
        mapping: dict[str, object] = {}
        position = 0
        while not isinstance(event := self._next(), MappingEndEvent):
            position += 1
            key_path = _child(path, f"<key {position}>")
            self._check_node(event, key_path)
            if not isinstance(event, ScalarEvent):
                raise self._refuse(event, key_path, RULE_KEY_NOT_SCALAR)
            if event.style is None and event.value == "<<":
                raise self._refuse(event, key_path, RULE_MERGE_KEY)
            key = event.value if event.style is not None else resolve_plain(event.value)
            if not isinstance(key, str):
                raise self._refuse(event, key_path, RULE_KEY_NOT_STRING)
            if _SURROGATE.search(key):
                raise self._refuse(event, key_path, RULE_LONE_SURROGATE)
            if key in mapping:
                raise self._refuse(event, key_path, RULE_DUPLICATE_KEY)
            name = key if _PATH_KEY.fullmatch(key) else f"<key {position}>"
            mapping[key] = self._node(self._next(), _child(path, name))
        return mapping

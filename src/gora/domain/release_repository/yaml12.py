"""The strict YAML 1.2 loader of the release files (ADR-026 section 10).

Pure: callers pass bytes. Only ruamel.yaml's scanner and parser are used, its token and event
streams; every value is built here from the events, under the YAML 1.2.2 core schema, so no
ruamel constructor or resolver can apply (timestamps, ``0b``, ``_`` in numbers, merge, value).
Refused: what ruamel.yaml would read otherwise than YAML 1.2 (a line break other than LF or CR),
what it drops without a trace (a reserved directive, a byte order mark), what an RFC 8785
serialisation cannot carry, and nesting deeper than any release file goes. No exception but
``ReleaseFileError`` leaves ``load``.

An error names the file, the field path and the rule, never a value or a refused key from the
document (ADR-003, "Identifier grammar").
"""

import math
import re
from collections.abc import Iterator
from typing import TypeVar

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
from ruamel.yaml.tokens import DirectiveToken, DocumentEndToken, StreamEndToken, StreamStartToken

MAX_BYTES = 262144
MAX_SAFE_INTEGER = 2**53 - 1
# 2^53 - 1 has 16 digits: a decimal integer with more is out of range, and int() never runs on it.
MAX_DECIMAL_DIGITS = 16
MAX_DEPTH = 32
ROOT = "$"

RULE_TOO_LARGE = "larger than 256 KiB"
RULE_NOT_UTF8 = "not UTF-8 text"
RULE_BOM = "a byte order mark is not allowed"
RULE_LINE_BREAK = "a line break other than LF or CR"
RULE_DIRECTIVE = "a directive is not allowed"
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
RULE_DEPTH = f"nested deeper than {MAX_DEPTH} levels"
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
# ruamel.yaml reads U+0085, U+2028 and U+2029 as YAML 1.1 line breaks, where YAML 1.2 reads
# content, and drops a byte order mark wherever it stands.
_REFUSED_CHARACTER = re.compile("[\ufeff\x85\u2028\u2029]")
# The line breaks ruamel.yaml counts in its marks: CR LF, a lone CR, LF.
_LINE_BREAK = re.compile("\r\n|\r|\n")
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
    refused = _REFUSED_CHARACTER.search(text)
    if refused is not None:
        offset = refused.start()
        if refused.group() == "\ufeff":
            line, column = (None, None) if offset == 0 else _position(text, offset)
            raise ReleaseFileError(source, ROOT, line, column, RULE_BOM)
        raise ReleaseFileError(source, ROOT, *_position(text, offset), RULE_LINE_BREAK)
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
        if len(value.lstrip("+-")) > MAX_DECIMAL_DIGITS:
            return -(MAX_SAFE_INTEGER + 1) if value.startswith("-") else MAX_SAFE_INTEGER + 1
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


def _position(text: str, offset: int) -> tuple[int, int]:
    """1-based line and column of ``offset``, counting line breaks as ruamel.yaml's marks do."""
    line, line_start = 1, 0
    for line_break in _LINE_BREAK.finditer(text, 0, offset):
        line, line_start = line + 1, line_break.end()
    return line, offset - line_start + 1


_T = TypeVar("_T")


class _Loader:
    """One load: a fresh ruamel YAML object per pass, whose state an exception would leave."""

    def __init__(self, text: str, source: str) -> None:
        self._text = text
        self._source = source
        self._last_mark: object = None
        self._events: Iterator[Event] = iter(YAML(typ="safe", pure=True).parse(text))

    def _refuse(self, event: object, path: str, rule: str) -> ReleaseFileError:
        mark = event.start_mark  # type: ignore[attr-defined]
        return ReleaseFileError(self._source, path, mark.line + 1, mark.column + 1, rule)

    def _not_yaml(self, mark: object) -> ReleaseFileError:
        line = getattr(mark, "line", None)
        column = getattr(mark, "column", None)
        if isinstance(line, int) and isinstance(column, int):
            return ReleaseFileError(self._source, ROOT, line + 1, column + 1, RULE_NOT_YAML)
        return ReleaseFileError(self._source, ROOT, None, None, RULE_NOT_YAML)

    def _pull(self, items: Iterator[_T]) -> _T:
        """The next token or event; whatever ruamel.yaml raises becomes ``not valid YAML``."""
        try:
            item = next(items)
        except MarkedYAMLError as exc:
            raise self._not_yaml(exc.problem_mark or exc.context_mark) from None
        except YAMLError as exc:
            # A reader error carries the offset of the character it refused, not a mark.
            offset = getattr(exc, "position", None)
            if not isinstance(offset, int):
                raise self._not_yaml(None) from None
            line, column = _position(self._text, offset)
            raise ReleaseFileError(self._source, ROOT, line, column, RULE_NOT_YAML) from None
        except (ValueError, OverflowError):
            # Raised from inside the scanner, by an escape beyond Unicode.
            raise self._not_yaml(self._last_mark) from None
        self._last_mark = getattr(item, "start_mark", None)
        return item

    def _next(self) -> Event:
        return self._pull(self._events)

    def _scan(self) -> None:
        """Refuse any directive, and a stream with no document, from the tokens."""
        tokens = iter(YAML(typ="safe", pure=True).scan(self._text))
        content = False
        while not isinstance(token := self._pull(tokens), StreamEndToken):
            if isinstance(token, DirectiveToken):
                raise self._refuse(token, ROOT, RULE_DIRECTIVE)
            content = content or not isinstance(token, (StreamStartToken, DocumentEndToken))
        if not content:
            raise self._refuse(token, ROOT, RULE_NO_DOCUMENT)

    def document(self) -> object:
        self._scan()
        self._last_mark = None
        start = self._next()
        assert isinstance(start, StreamStartEvent)
        event = self._next()
        if isinstance(event, StreamEndEvent):
            raise self._refuse(event, ROOT, RULE_NO_DOCUMENT)
        assert isinstance(event, DocumentStartEvent)
        value = self._node(self._next(), ROOT, 0)
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

    def _node(self, event: Event, path: str, depth: int) -> object:
        """``depth`` is the number of collections ``event`` stands in."""
        self._check_node(event, path)
        if isinstance(event, ScalarEvent):
            return self._scalar(event, path)
        if depth == MAX_DEPTH:
            raise self._refuse(event, path, RULE_DEPTH)
        if isinstance(event, SequenceStartEvent):
            return self._sequence(path, depth + 1)
        if isinstance(event, MappingStartEvent):
            return self._mapping(path, depth + 1)
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

    def _sequence(self, path: str, depth: int) -> list[object]:
        items: list[object] = []
        while not isinstance(event := self._next(), SequenceEndEvent):
            items.append(self._node(event, f"{path}[{len(items)}]", depth))
        return items

    def _mapping(self, path: str, depth: int) -> dict[str, object]:
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
            mapping[key] = self._node(self._next(), _child(path, name), depth)
        return mapping

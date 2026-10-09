"""Canonical writing of the release files, RFC 8785 and the policy digest (ADR-026 sections 10, 15).

Pure: values in, text or bytes out. ``dump`` writes the files GORA owns, the same bytes for the
same content every time, and ``yaml12.load`` reads back exactly what it was given. ``jcs`` is the
RFC 8785 serialisation the policy digest is taken over. No release file field is a float, so
neither writes one.
"""

import hashlib
import json
import re

from gora.domain.release_repository.yaml12 import (
    MAX_BYTES,
    MAX_DEPTH,
    MAX_SAFE_INTEGER,
    resolve_plain,
)

INDENT = "  "

_PLAIN = re.compile(r"[A-Za-z0-9_./+][A-Za-z0-9_./@+:-]*")
# The YAML 1.1 implicit types (yaml.org/type, as PyYAML resolves them, y and n included) that
# a plain string could read as. A file GORA writes is also read by YAML 1.1 tools, so a string
# one of them would read as a boolean, a number, null or a timestamp is quoted (`no`, `1:30`,
# `1_000`, `0b11`, `2026-10-09T12:00:00Z`). The merge and value keys cannot start plain.
_YAML11_NOT_STRING = re.compile(
    r"y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF"
    r"|~|null|Null|NULL"
    r"|[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+"
    r"|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+"
    r"|[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9_]+(?:[eE][-+][0-9]+)?"
    r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
    r"|[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
    r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?"
)
# What ruamel.yaml's default safe loader reads as a number beyond the core schema: its YAML 1.2
# resolver allows `_` in numbers and an optional `o` (`08_`, `0_9`, `0o1_`, `1_e5`).
_RUAMEL_NOT_STRING = re.compile(
    r"[-+]?0o?[0-7_]+|[-+]?[0-9_]+|[-+]?[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.[0-9_]+(?:[eE][-+][0-9]+)?"
)
# Outside the YAML 1.2 printable set, beyond what JSON escapes already: DEL, C1, U+FFFE and
# U+FFFF. U+0085, U+2028 and U+2029 are printable in YAML 1.2, but ruamel.yaml reads them as
# line breaks inside a double-quoted scalar and folds or trims around them, so they are escaped
# too.
_UNPRINTABLE = re.compile("[\x7f-\x9f\u2028\u2029\ufffe\uffff]")
# The modeline is one comment line: printable ASCII, no space.
_MODELINE = re.compile("[!-~]+")
_SURROGATE = re.compile("[\ud800-\udfff]")


def dump(document: dict[str, object], *, modeline: str | None = None) -> str:
    """``document`` as canonical YAML: block style, two-space indent, one trailing newline."""
    if not isinstance(document, dict):
        raise TypeError("a release file is a mapping")
    if modeline is not None and not _MODELINE.fullmatch(modeline):
        raise ValueError("a modeline is printable ASCII without a space")
    _check_depth(document)
    lines = [] if modeline is None else [f"# yaml-language-server: $schema={modeline}"]
    if document:
        lines.extend(_mapping(document, 0))
    else:
        lines.append("{}")
    text = "\n".join(lines) + "\n"
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("a release file is at most 256 KiB")
    return text


def _check_depth(document: dict[str, object]) -> None:
    """Refuse nesting the loader would refuse, before any recursion."""
    pending: list[tuple[object, int]] = [(document, 1)]
    while pending:
        value, level = pending.pop()
        if level > MAX_DEPTH:
            raise ValueError(f"a release file is nested at most {MAX_DEPTH} levels deep")
        if isinstance(value, dict):
            value = list(value.values())
        if isinstance(value, list):
            pending.extend((child, level + 1) for child in value if isinstance(child, (dict, list)))


def _mapping(mapping: dict[str, object], depth: int) -> list[str]:
    indent = INDENT * depth
    lines: list[str] = []
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise TypeError("a mapping key must be a string")
        head = f"{indent}{_string(key)}:"
        if isinstance(value, dict) and value:
            lines.append(head)
            lines.extend(_mapping(value, depth + 1))
        elif isinstance(value, list) and value:
            lines.append(head)
            lines.extend(_sequence(value, depth + 1))
        else:
            lines.append(f"{head} {_flat(value)}")
    return lines


def _sequence(items: list[object], depth: int) -> list[str]:
    indent = INDENT * depth
    lines: list[str] = []
    for item in items:
        if isinstance(item, list):
            raise TypeError("a list directly inside a list is not written")
        if isinstance(item, dict) and item:
            # The mapping is laid out one level deeper; its first line takes the dash.
            nested = _mapping(item, depth + 1)
            nested[0] = f"{indent}- {nested[0][len(indent) + len(INDENT) :]}"
            lines.extend(nested)
        else:
            lines.append(f"{indent}- {_flat(item)}")
    return lines


def _flat(value: object) -> str:
    """A value written on one line: a scalar, or an empty mapping or list."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        _check_integer(value)
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, dict) and not value:
        return "{}"
    if isinstance(value, list) and not value:
        return "[]"
    raise TypeError(f"a release file holds no {type(value).__name__}")


def _string(value: str) -> str:
    """Plain when YAML 1.2, YAML 1.1 and ruamel.yaml all read it as this string; else quoted."""
    _check_string(value)
    if (
        _PLAIN.fullmatch(value)
        and not value.endswith(":")
        and isinstance(resolve_plain(value), str)
        and not _YAML11_NOT_STRING.fullmatch(value)
        and not _RUAMEL_NOT_STRING.fullmatch(value)
    ):
        return value
    quoted = json.dumps(value, ensure_ascii=False)
    return _UNPRINTABLE.sub(lambda match: f"\\u{ord(match.group()):04x}", quoted)


def _check_integer(value: int) -> None:
    if abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("an integer must be within -(2^53 - 1) and 2^53 - 1")


def _check_string(value: str) -> None:
    if _SURROGATE.search(value):
        raise ValueError("a string must not hold a lone surrogate")


def jcs(value: object) -> bytes:
    """The RFC 8785 (JSON Canonicalization Scheme) serialisation of ``value``, UTF-8."""
    return _jcs(value).encode("utf-8")


def _jcs(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        _check_integer(value)
        return str(value)
    if isinstance(value, str):
        _check_string(value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_jcs(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError("an object member name must be a string")
        names = sorted(value, key=lambda name: name.encode("utf-16-be"))
        return "{" + ",".join(f"{_jcs(name)}:{_jcs(value[name])}" for name in names) + "}"
    raise TypeError(f"RFC 8785 serialisation here holds no {type(value).__name__}")


def policy_digest(policy: object, artifact: object) -> str:
    """The policy digest of ADR-026 section 15: SHA-256 over ``jcs([policy, artifact])``.

    The two documents as ``yaml12.load`` returned them and their models then validated (step
    1b), with no default filled in. The models refuse a float, and ``jcs`` raises ``TypeError``
    on one.
    """
    return "sha256:" + hashlib.sha256(jcs([policy, artifact])).hexdigest()

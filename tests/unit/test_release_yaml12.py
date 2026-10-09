"""The strict YAML 1.2 loader of the release files (ADR-026 section 10).

Every refusal is asserted by its rule, its field path, its line and column, and by what its
message must not hold: a value or a refused key from the document, here the sentinel. The core
schema is asserted row by row. Expectations are written out literally.
"""

import pytest

from gora.domain.release_repository import yaml12
from gora.domain.release_repository.yaml12 import ReleaseFileError

SOURCE = "state/qa/api.yaml"
# Placed in every refused document where a value or a refused key goes; no part of it may
# reach a message.
SENTINEL = "QXZJ9K7W"


def refusal(text: str | bytes) -> ReleaseFileError:
    data = text.encode("utf-8") if isinstance(text, str) else text
    with pytest.raises(ReleaseFileError) as caught:
        yaml12.load(data, source=SOURCE)
    return caught.value


def assert_refused(
    text: str | bytes,
    rule: str,
    path: str,
    line: int | None,
    column: int | None,
    *absent: str,
) -> None:
    error = refusal(text)
    assert (error.rule, error.path, error.line, error.column) == (rule, path, line, column)
    assert error.source == SOURCE
    where = SOURCE if line is None else f"{SOURCE}:{line}:{column}"
    assert str(error) == f"{where}: {path}: {rule}"
    for part in [SENTINEL[i : i + 4] for i in range(len(SENTINEL) - 3)] + list(absent):
        assert part not in str(error)


def load(text: str) -> object:
    return yaml12.load(text.encode("utf-8"), source=SOURCE)


def typed(value: object) -> object:
    """``value`` with its types and its order made part of equality: True is not 1 here."""
    if isinstance(value, dict):
        return ("dict", [(key, typed(item)) for key, item in value.items()])
    if isinstance(value, list):
        return ("list", [typed(item) for item in value])
    return (type(value).__name__, value)


def nested(levels: int, *, flow: bool) -> bytes:
    """A document whose innermost collection is ``levels`` collections deep."""
    if flow:
        return b"[" * levels + b"]" * levels
    return b"- " * (levels - 1) + b"[]"


# Rule 1: size, before decoding.


def test_refuses_a_file_over_256_kib() -> None:
    data = f"a: {SENTINEL}\n".encode() + b"#" * 262144
    assert_refused(data, "larger than 256 KiB", "$", None, None)


def test_accepts_a_file_of_exactly_256_kib() -> None:
    data = b"a: 1\n#"
    data += b"x" * (262144 - len(data))
    assert yaml12.load(data, source=SOURCE) == {"a": 1}


# Rule 2: UTF-8, no byte order mark.


def test_refuses_a_file_that_is_not_utf8() -> None:
    assert_refused(f"a: {SENTINEL}\n".encode() + b"\xff\n", "not UTF-8 text", "$", None, None)


def test_refuses_an_encoded_surrogate() -> None:
    data = f"a: {SENTINEL}".encode() + b"\xed\xa0\x80\n"
    assert_refused(data, "not UTF-8 text", "$", None, None)


def test_refuses_a_byte_order_mark() -> None:
    data = b"\xef\xbb\xbf" + f"a: {SENTINEL}\n".encode()
    assert_refused(data, "a byte order mark is not allowed", "$", None, None)


@pytest.mark.parametrize(
    ("data", "line", "column"),
    [
        (b"a: 1\n\xef\xbb\xbfb: 2\n", 2, 1),
        (f'a: "{SENTINEL}'.encode() + b'\xef\xbb\xbf"\n', 1, 13),
        (f"a: {SENTINEL}\r".encode() + b"\xef\xbb\xbf", 2, 1),
    ],
)
def test_refuses_a_byte_order_mark_anywhere(data: bytes, line: int, column: int) -> None:
    assert_refused(data, "a byte order mark is not allowed", "$", line, column)


# Line breaks: ruamel.yaml reads U+0085, U+2028 and U+2029 as YAML 1.1 line breaks.


@pytest.mark.parametrize(
    ("text", "line", "column"),
    [
        ("# c\u2028a: 1\n", 1, 4),
        (f"a: b\x85{SENTINEL}", 1, 5),
        (f'a: 1\nb: "{SENTINEL}\u2029"\n', 2, 13),
        (f"a: 1\r\nb: 2\rc: {SENTINEL}\u2028", 3, 12),
    ],
)
def test_refuses_a_line_break_other_than_lf_or_cr(text: str, line: int, column: int) -> None:
    assert_refused(text, "a line break other than LF or CR", "$", line, column)


def test_accepts_crlf_and_cr_line_breaks() -> None:
    assert typed(load("a: 1\r\nb: 2\rc: 3\n")) == typed({"a": 1, "b": 2, "c": 3})


# Rule 3: directives.


@pytest.mark.parametrize(
    "text",
    [
        f"%YAML 1.2\n---\na: {SENTINEL}\n",
        f"%TAG !q! tag:{SENTINEL}.example,2026:\n---\na: 1\n",
        f"%FOO {SENTINEL}\n---\na: 1\n",
    ],
)
def test_refuses_a_directive(text: str) -> None:
    assert_refused(text, "a directive is not allowed", "$", 1, 1)


def test_refuses_a_reserved_directive_after_a_comment() -> None:
    assert_refused(f"# {SENTINEL}\n%FOO bar\n---\na: 1\n", "a directive is not allowed", "$", 2, 1)


# Rule 4: exactly one document.


@pytest.mark.parametrize(
    ("text", "line"),
    [("", 1), (f"# {SENTINEL}\n", 2), ("...\n", 2), (f"# {SENTINEL}\n...\n...\n", 4)],
)
def test_refuses_no_document(text: str, line: int) -> None:
    assert_refused(text, "holds no YAML document", "$", line, 1)


def test_refuses_a_second_document() -> None:
    text = f"a: {SENTINEL}\n---\nb: {SENTINEL}\n"
    assert_refused(text, "holds more than one YAML document", "$", 2, 1)


def test_accepts_explicit_document_markers() -> None:
    assert typed(load("---\na: 1\n...\n")) == typed({"a": 1})
    assert load("---\n") is None


# Rule 5: anchors and aliases.


def test_refuses_an_anchor() -> None:
    text = f"spec:\n  a: &{SENTINEL} x\n"
    assert_refused(text, "an anchor is not allowed", "spec.a", 2, 6)


def test_refuses_an_anchor_on_a_collection() -> None:
    text = f"spec: &{SENTINEL}\n  a: 1\n"
    assert_refused(text, "an anchor is not allowed", "spec", 1, 7)


def test_refuses_an_alias() -> None:
    text = f"spec:\n  a: 1\n  b: *{SENTINEL}\n"
    assert_refused(text, "an alias is not allowed", "spec.b", 3, 6)


# Rule 6: explicit tags.


@pytest.mark.parametrize(
    "tagged",
    [f"!!str {SENTINEL}", f"! {SENTINEL}", f"!{SENTINEL} x", "!!map {}", f"!!seq [{SENTINEL}]"],
)
def test_refuses_an_explicit_tag(tagged: str) -> None:
    text = f"spec:\n  a: {tagged}\n"
    assert_refused(text, "an explicit tag is not allowed", "spec.a", 2, 6, "!!", "str")


def test_refuses_a_tagged_key() -> None:
    text = f"spec:\n  !!str {SENTINEL}: 1\n"
    assert_refused(text, "an explicit tag is not allowed", "spec.<key 1>", 2, 3)


# Rule 7: keys are strings.


@pytest.mark.parametrize("key", [f"[{SENTINEL}]", f"{{{SENTINEL}: 1}}"])
def test_refuses_a_key_that_is_not_a_scalar(key: str) -> None:
    text = f"spec:\n  a: {SENTINEL}\n  ? {key}\n  : 2\n"
    assert_refused(text, "a mapping key must be a scalar", "spec.<key 2>", 3, 5)


@pytest.mark.parametrize("key", ["true", "1", "~", "1.5", "0x10", "null"])
def test_refuses_a_key_that_resolves_to_no_string(key: str) -> None:
    text = f"spec:\n  a: {SENTINEL}\n  {key}: {SENTINEL}\n"
    assert_refused(text, "a mapping key must be a string", "spec.<key 2>", 3, 3)


def test_refuses_an_empty_plain_key() -> None:
    text = f"spec:\n  ? \n  : {SENTINEL}\n"
    error = refusal(text)
    assert (error.rule, error.path) == ("a mapping key must be a string", "spec.<key 1>")


def test_accepts_quoted_keys_that_read_like_other_types() -> None:
    assert typed(load("'true': 1\n\"1\": 2\n'~': 3\n")) == typed({"true": 1, "1": 2, "~": 3})


# Rule 8: the merge key.


def test_refuses_the_merge_key() -> None:
    text = f"spec:\n  b: 1\n  <<: {{{SENTINEL}: 1}}\n"
    assert_refused(text, "the merge key << is not allowed", "spec.<key 2>", 3, 3)


def test_accepts_a_quoted_merge_key_as_a_string() -> None:
    assert load(f'"<<": {SENTINEL}\n') == {"<<": SENTINEL}


# Rule 9: duplicate keys.


def test_refuses_a_duplicate_key() -> None:
    text = f"spec:\n  {SENTINEL}: 1\n  {SENTINEL}: 2\n"
    assert_refused(text, "duplicate key", "spec.<key 2>", 3, 3)


def test_refuses_a_duplicate_key_written_in_another_style() -> None:
    text = f"spec:\n  {SENTINEL}: 1\n  b: 2\n  '{SENTINEL}': 3\n"
    assert_refused(text, "duplicate key", "spec.<key 3>", 4, 3)


# Rule 10: numbers RFC 8785 can carry.


@pytest.mark.parametrize("number", ["9007199254740992", "-9007199254740992", "0x20000000000000"])
def test_refuses_an_integer_outside_the_safe_range(number: str) -> None:
    text = f"spec:\n  a: {number}\n"
    assert_refused(
        text, "an integer must be within -(2^53 - 1) and 2^53 - 1", "spec.a", 2, 6, number[-6:]
    )


def test_accepts_the_bounds_of_the_safe_range() -> None:
    assert typed(load("a: 9007199254740991\nb: -9007199254740991\n")) == typed(
        {"a": 9007199254740991, "b": -9007199254740991}
    )


@pytest.mark.parametrize(
    "number",
    ["1" * 5000, "-" + "9" * 5000, "+" + "1" * 17, "00000000000000001", "0o" + "7" * 5000],
    ids=["5000 digits", "-5000 digits", "+17 digits", "17 digits, leading zeros", "octal 5000"],
)
def test_refuses_a_long_integer_before_converting_it(number: str) -> None:
    # Python's int() refuses a decimal string of more than 4300 digits with ValueError.
    text = f"spec:\n  a: {number}\n"
    assert_refused(text, "an integer must be within -(2^53 - 1) and 2^53 - 1", "spec.a", 2, 6)


@pytest.mark.parametrize("number", [".inf", "-.Inf", "+.INF", ".nan", ".NaN", ".NAN"])
def test_refuses_a_float_that_is_not_finite(number: str) -> None:
    text = f"spec:\n  a: {number}\n"
    assert_refused(text, "a float must be finite", "spec.a", 2, 6, number)


# Rule 11: lone surrogates.


def test_refuses_a_lone_surrogate_in_a_value() -> None:
    text = f'spec:\n  a: "{SENTINEL}\\uD800"\n'
    assert_refused(text, "a string must not hold a lone surrogate", "spec.a", 2, 6, "D800")


def test_refuses_a_lone_surrogate_in_a_key() -> None:
    text = f'spec:\n  "{SENTINEL}\\uDC00": 1\n'
    assert_refused(text, "a string must not hold a lone surrogate", "spec.<key 1>", 2, 3)


def test_refuses_an_escaped_surrogate_pair() -> None:
    # YAML's \u escapes one 16-bit character and ruamel.yaml joins no pair: two lone surrogates.
    error = refusal('a: "\\uD83D\\uDE00"\n')
    assert error.rule == "a string must not hold a lone surrogate"


def test_accepts_a_character_beyond_the_bmp() -> None:
    assert load('a: "\\U0001F600"\nb: \U0001f600\n') == {"a": "\U0001f600", "b": "\U0001f600"}


# Not YAML at all: ruamel's own problem text never reaches the message.


def test_a_parser_error_names_only_its_position() -> None:
    error = refusal(f"spec:\n  a: [{SENTINEL}\n")
    assert (error.rule, error.path, error.line) == ("not valid YAML", "$", 3)
    assert str(error) == f"{SOURCE}:3:{error.column}: $: not valid YAML"


def test_a_reader_error_names_only_its_position() -> None:
    assert_refused(f"a: {SENTINEL}\x01\n", "not valid YAML", "$", 1, 12)


@pytest.mark.parametrize(
    ("data", "line", "column"),
    [(b"a: 1\rb: \x01", 2, 4), (b"a: 1\r\nb: \x01", 2, 4), (b"a: 1\n\rb: \x01", 3, 4)],
)
def test_a_reader_error_counts_a_lone_cr_as_a_line_break(
    data: bytes, line: int, column: int
) -> None:
    assert_refused(data, "not valid YAML", "$", line, column)


@pytest.mark.parametrize("escape", ["\\UFFFFFFFF", "\\U00110000"])
def test_an_escape_beyond_unicode_is_not_valid_yaml(escape: str) -> None:
    # ruamel.yaml's scanner raises OverflowError and ValueError here, not a YAMLError.
    error = refusal(f'a: "{SENTINEL}{escape}"\n')
    assert (error.rule, error.path) == ("not valid YAML", "$")
    assert SENTINEL not in str(error)


@pytest.mark.parametrize(
    "refused",
    [
        "a: [b\n",  # ruamel.yaml's own error
        "x: [1, 2, {c: &d e}]\nz: 9\n",  # a refusal that abandons the parse mid-stream
        "x: 1\nx: 2\nz: 9\n",
    ],
)
def test_a_failed_parse_leaves_no_state_for_the_next(refused: str) -> None:
    # A ruamel YAML object whose parse is abandoned resumes it on its next parse.
    refusal(refused)
    assert typed(load("a: 1\nb: [x, y]\n")) == typed({"a": 1, "b": ["x", "y"]})


# Paths.


def test_a_path_names_fields_and_indexes() -> None:
    text = "spec:\n  stages:\n    - name: dev\n    - name: qa\n    - name: !!str x\n"
    assert_refused(text, "an explicit tag is not allowed", "spec.stages[2].name", 5, 13)


def test_a_key_that_is_not_a_field_name_is_named_by_its_position() -> None:
    text = f'spec:\n  "{SENTINEL} \\e[31m":\n    a: !!str x\n'
    assert_refused(text, "an explicit tag is not allowed", "spec.<key 1>.a", 3, 8, "\x1b")


def test_an_index_at_the_root() -> None:
    assert_refused("- 1\n- &a 2\n", "an anchor is not allowed", "$[1]", 2, 3)


# Depth: one counter for block and flow style.


@pytest.mark.parametrize("flow", [True, False])
def test_accepts_32_levels(flow: bool) -> None:
    loaded = yaml12.load(nested(32, flow=flow), source=SOURCE)
    for _ in range(31):
        assert isinstance(loaded, list)
        loaded = loaded[0]
    assert loaded == []


@pytest.mark.parametrize(("flow", "column"), [(True, 33), (False, 65)])
def test_refuses_a_33rd_level(flow: bool, column: int) -> None:
    assert_refused(
        nested(33, flow=flow), "nested deeper than 32 levels", "$" + "[0]" * 32, 1, column
    )


def test_refuses_depth_counted_across_mappings_and_lists() -> None:
    text = "a:\n" + "".join(f"{'  ' * (i + 1)}b{i}:\n" for i in range(30)) + "  " * 31 + "c: [x]\n"
    path = "a." + ".".join(f"b{i}" for i in range(30)) + ".c"
    # The root is level 1 and the mapping under b29 level 32: the list under c is the 33rd.
    assert_refused(text, "nested deeper than 32 levels", path, 32, 66)


@pytest.mark.parametrize(
    "data",
    [b"a: " + b"[" * 5000 + b"]" * 5000, b"- " * 500 + b"x"],
    ids=["flow 5000", "block 500"],
)
def test_deep_nesting_is_refused_not_a_crash(data: bytes) -> None:
    assert refusal(data).rule == "nested deeper than 32 levels"


# The YAML 1.2.2 core schema, plain scalars only.


@pytest.mark.parametrize(
    ("plain", "value"),
    [
        ("", None),
        ("~", None),
        ("null", None),
        ("Null", None),
        ("NULL", None),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", 0),
        ("42", 42),
        ("-42", -42),
        ("+42", 42),
        ("010", 10),
        ("0o17", 15),
        ("0o0", 0),
        ("0x1F", 31),
        ("0xff", 255),
        ("1.5", 1.5),
        ("-1.5", -1.5),
        ("+.5", 0.5),
        (".5", 0.5),
        ("1.", 1.0),
        ("1e3", 1000.0),
        ("1.5E-2", 0.015),
        ("-.5e+1", -5.0),
    ],
)
def test_core_schema(plain: str, value: object) -> None:
    assert typed(load(f"a: {plain}\n")) == typed({"a": value})


@pytest.mark.parametrize(
    "plain",
    [
        "no",
        "on",
        "yes",
        "off",
        "y",
        "1:30",
        "1_000",
        "0b11",
        "2026-10-09T12:00:00Z",
        "2026-10-09",
        "nULL",
        "tRUE",
        "0O17",
        "0X1F",
        "0o8",
        "1e",
        "e3",
        "1.2.3",
        "=",
        ".",
        "inf",
        "nan",
    ],
)
def test_core_schema_leaves_everything_else_a_string(plain: str) -> None:
    assert load(f"a: {plain}\n") == {"a": plain}


@pytest.mark.parametrize(
    ("scalar", "value"),
    [
        ("'true'", "true"),
        ('"1"', "1"),
        ("'~'", "~"),
        ('""', ""),
        ("'.inf'", ".inf"),
        ('"010"', "010"),
        ("|\n  010\n", "010\n"),
        ("|-\n  null\n", "null"),
        (">\n  true\n", "true\n"),
        (">-\n  1.5\n", "1.5"),
    ],
)
def test_quoted_literal_and_folded_scalars_are_strings(scalar: str, value: str) -> None:
    assert load(f"a: {scalar}") == {"a": value}


def test_insertion_order_is_kept() -> None:
    loaded = load("zeta: 1\nalpha: 2\nmid:\n  b: 1\n  a: 2\n")
    assert isinstance(loaded, dict)
    assert list(loaded) == ["zeta", "alpha", "mid"]
    assert list(loaded["mid"]) == ["b", "a"]


def test_collections_and_flow_style() -> None:
    text = "a: [1, x, {b: null}]\nc: {}\nd: []\ne:\n  - f: 1\n    g: [true]\n"
    assert typed(load(text)) == typed(
        {"a": [1, "x", {"b": None}], "c": {}, "d": [], "e": [{"f": 1, "g": [True]}]}
    )

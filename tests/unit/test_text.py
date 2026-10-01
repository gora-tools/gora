"""``gora.text`` is a leaf: one escape rule for every output, importing nothing from gora."""

import ast
from pathlib import Path

import pytest

from gora import text

E_ACUTE = "\N{LATIN SMALL LETTER E WITH ACUTE}"


def test_the_module_imports_nothing_from_gora() -> None:
    tree = ast.parse(Path(text.__file__).read_text())
    absolute = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ] + [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    ]
    relative = [
        node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert absolute  # calibration: the walk does see imports
    assert [name for name in absolute if name.split(".")[0] == "gora"] == []
    assert relative == []


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("plain text", "plain text"),
        ("a\nb", "a\\x0ab"),
        ("a\x7fb", "a\\x7fb"),
        ("a\N{RIGHT-TO-LEFT OVERRIDE}b", "a\\u202eb"),
        ("a\N{LINE SEPARATOR}b", "a\\u2028b"),
        ("a\U000e0041b", "a\\U000e0041b"),
        ("a\\b", "a\\\\b"),
        ("\N{GRINNING FACE}", "\N{GRINNING FACE}"),
        (E_ACUTE, E_ACUTE),
    ],
)
def test_visible_shows_invisible_characters_as_escapes(raw: str, shown: str) -> None:
    assert text.visible(raw) == shown

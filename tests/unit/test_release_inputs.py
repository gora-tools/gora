"""The input glob matcher (ADR-026 section 9): every rule of its meaning, written out literally.

The meaning is git's ``glob`` pathspec magic; ``tests/integration/test_input_globs_against_git``
compares the matcher with the real git binary. Here each rule has its own rows.
"""

import pytest

from gora.domain.release_repository.inputs import fixed_components, matches

TABLE = [
    # A glob matches, literally, the path it spells and every path under it.
    ("src", "src", True),
    ("src", "src/a.py", True),
    ("src", "src/a/b.py", True),
    ("src", "srcx", False),
    ("src", "srcx/a.py", False),
    ("src/a.py", "src/a.py", True),
    ("src/a.py", "src/a.pyc", False),
    ("pom.xml", "pom.xml", True),
    ("pom.xml", "a/pom.xml", False),
    # ... a glob with a wildcard too: git compares literally before it reads any wildcard.
    ("a*", "a*", True),
    ("a*", "a*/x", True),
    ("a?b", "a?b/c", True),
    ("a[b]", "a[b]/y", True),
    ("**", "**/x", True),
    # A glob with *, ? or [ also matches every whole path its pattern matches.
    ("a*", "abc", True),
    ("a*", "a", True),
    ("a*", "abc/x", False),  # a pattern never matches a leading directory
    ("src/*", "src/a", True),
    ("src/*", "src/a/b", False),
    ("*", "a", True),
    ("*", "a/b", False),
    ("*.py", "a.py", True),
    ("*.py", "src/a.py", False),  # * never matches /
    ("a?b", "axb", True),
    ("a?b", "ab", False),  # ? is one character
    ("a?b", "axxb", False),
    ("a?b", "a/b", False),  # ? never matches /
    ("[ab]", "a", True),
    ("[ab]", "c", False),
    ("[a-c]x", "bx", True),
    ("[a-c]x", "dx", False),
    ("[A-Z]*", "Dockerfile", True),
    ("[A-Z]*", "dockerfile", False),
    ("[0-9]*", "9z", True),
    ("[.]*", ".hidden", True),
    ("a[b]", "ab", True),
    # A leading **/ matches zero or more leading components.
    ("**/foo", "foo", True),
    ("**/foo", "a/foo", True),
    ("**/foo", "a/b/foo", True),
    ("**/foo", "foo/bar", False),
    ("**/foo", "afoo", False),
    # /**/ matches zero or more components between two.
    ("a/**/b", "a/b", True),
    ("a/**/b", "a/x/b", True),
    ("a/**/b", "a/x/y/b", True),
    ("a/**/b", "ab", False),
    ("a/**/b", "a/xb", False),
    # A trailing /** matches one or more components below.
    ("src/**", "src/a", True),
    ("src/**", "src/a/b/c", True),
    ("src/**", "src", False),
    ("src/**", "srcx/a", False),
    # ** alone matches every path.
    ("**", "a", True),
    ("**", "a/b/c", True),
    ("**", ".hidden", True),
    # Matching is case-sensitive, and * matches a leading dot.
    ("src/**", "Src/a", False),
    ("*.PY", "a.py", False),
    ("*", ".hidden", True),
    ("*.py", ".a.py", True),
    ("**/*.py", ".github/.x.py", True),
    # The other characters are literal.
    ("a.b", "axb", False),
    ("a+b", "a+b", True),
    ("a+b", "aab", False),
    ("v1.2/*", "v1.2/x", True),
    ("%#~@=,", "%#~@=,", True),
]


@pytest.mark.parametrize(("glob", "path", "expected"), TABLE)
def test_matches(glob: str, path: str, expected: bool) -> None:
    assert matches(glob, path) is expected


@pytest.mark.parametrize(
    ("glob", "fixed"),
    [
        ("src", ("src",)),
        ("src/main.py", ("src", "main.py")),
        ("src/**", ("src",)),
        ("src/a/*.py", ("src", "a")),
        ("**/x", ()),
        ("*", ()),
        ("state/?a/x", ("state",)),
        ("artifacts/[ab]/x", ("artifacts",)),
        ("a/b*/c", ("a",)),
    ],
)
def test_fixed_components(glob: str, fixed: tuple[str, ...]) -> None:
    assert fixed_components(glob) == fixed


@pytest.mark.parametrize("glob", ["", "/src", "a//b", "a/**/**/b", "[!a]", "a b", "../x"])
def test_a_glob_the_grammar_refuses_raises(glob: str) -> None:
    with pytest.raises(ValueError, match="not an input glob"):
        matches(glob, "x")
    with pytest.raises(ValueError, match="not an input glob"):
        fixed_components(glob)

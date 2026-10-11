"""The input glob matcher (ADR-026 section 9): every rule of its meaning, written out literally.

The meaning is git's ``glob`` pathspec magic; ``tests/integration/test_input_globs_against_git``
compares the matcher with the real git binary. Here each rule has its own rows.
"""

import pytest

from gora.domain.release_repository.inputs import (
    fixed_components,
    input_paths,
    is_release_path,
    matches,
)

TABLE = [
    # A glob matches, literally, the path it spells and every path under it.
    ("src", b"src", True),
    ("src", b"src/a.py", True),
    ("src", b"src/a/b.py", True),
    ("src", b"srcx", False),
    ("src", b"srcx/a.py", False),
    ("src/a.py", b"src/a.py", True),
    ("src/a.py", b"src/a.pyc", False),
    ("pom.xml", b"pom.xml", True),
    ("pom.xml", b"a/pom.xml", False),
    # ... a glob with a wildcard too: git compares literally before it reads any wildcard.
    ("a*", b"a*", True),
    ("a*", b"a*/x", True),
    ("a?b", b"a?b/c", True),
    ("a[b]", b"a[b]/y", True),
    ("**", b"**/x", True),
    # A glob with *, ? or [ also matches every whole path its pattern matches.
    ("a*", b"abc", True),
    ("a*", b"a", True),
    ("a*", b"abc/x", False),  # a pattern never matches a leading directory
    ("src/*", b"src/a", True),
    ("src/*", b"src/a/b", False),
    ("*", b"a", True),
    ("*", b"a/b", False),
    ("*.py", b"a.py", True),
    ("*.py", b"src/a.py", False),  # * never matches /
    ("a?b", b"axb", True),
    ("a?b", b"ab", False),  # ? is one byte
    ("a?b", b"axxb", False),
    ("a?b", b"a/b", False),  # ? never matches /
    ("[ab]", b"a", True),
    ("[ab]", b"c", False),
    ("[a-c]x", b"bx", True),
    ("[a-c]x", b"dx", False),
    ("[A-Z]*", b"Dockerfile", True),
    ("[A-Z]*", b"dockerfile", False),
    ("[0-9]*", b"9z", True),
    ("[.]*", b".hidden", True),
    ("a[b]", b"ab", True),
    # A leading **/ matches zero or more leading components.
    ("**/foo", b"foo", True),
    ("**/foo", b"a/foo", True),
    ("**/foo", b"a/b/foo", True),
    ("**/foo", b"foo/bar", False),
    ("**/foo", b"afoo", False),
    # /**/ matches zero or more components between two.
    ("a/**/b", b"a/b", True),
    ("a/**/b", b"a/x/b", True),
    ("a/**/b", b"a/x/y/b", True),
    ("a/**/b", b"ab", False),
    ("a/**/b", b"a/xb", False),
    # A trailing /** matches one or more components below.
    ("src/**", b"src/a", True),
    ("src/**", b"src/a/b/c", True),
    ("src/**", b"src", False),
    ("src/**", b"srcx/a", False),
    # ** alone matches every path.
    ("**", b"a", True),
    ("**", b"a/b/c", True),
    ("**", b".hidden", True),
    # Matching is case-sensitive, and * matches a leading dot.
    ("src/**", b"Src/a", False),
    ("*.PY", b"a.py", False),
    ("*", b".hidden", True),
    ("*.py", b".a.py", True),
    ("**/*.py", b".github/.x.py", True),
    # The other characters are literal.
    ("a.b", b"axb", False),
    ("a+b", b"a+b", True),
    ("a+b", b"aab", False),
    ("v1.2/*", b"v1.2/x", True),
    ("%#~@=,", b"%#~@=,", True),
    # Matching is on the path's bytes, as git's is: ? is one byte, a class matches one byte.
    ("a/?", "a/\u00e9".encode(), False),  # U+00E9 is two bytes in UTF-8
    ("a/??", "a/\u00e9".encode(), True),
    ("b/???", "b/\u65e5".encode(), True),  # U+65E5 is three
    ("a/*", "a/\u00e9".encode(), True),
    ("a/*", b"a/\xff", True),  # a path that is not UTF-8 matches too
    ("[a-z]", "\u00e9".encode(), False),
    ("?", b"\xff", True),
    ("a/**", b"a/\xff/x", True),
]


@pytest.mark.parametrize(("glob", "path", "expected"), TABLE)
def test_matches(glob: str, path: bytes, expected: bool) -> None:
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
        matches(glob, b"x")
    with pytest.raises(ValueError, match="not an input glob"):
        fixed_components(glob)


# --- the release paths are removed from every input set (section 9) -------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (b"release-policy.yaml", True),
        (b".github/CODEOWNERS", True),
        (b"artifacts/api/artifact.yaml", True),
        (b"state/qa/api.yaml", True),
        (b"rejections/api/1.1.0.yaml", True),
        (b"artifacts/api/notes.md", True),  # anything under a release directory
        (b"state/\xff", True),  # a path that is not UTF-8
        (b"state", True),  # a release directory's own name
        (b"artifacts", True),
        (b"release-policy.yaml/x", True),  # under a release file's name
        (b"release-policy.yml", False),
        (b"release-policy.yaml.bak", False),
        (b"Release-Policy.yaml", False),  # case-sensitive
        (b".github", False),  # the directory, not the code-owner file
        (b".github/workflows/ci.yml", False),
        (b"CODEOWNERS", False),
        (b"docs/CODEOWNERS", False),
        (b"artifactsx/a", False),  # whole components, not a prefix of the text
        (b"src/state/x", False),  # from the root only
        (b"State/qa/api.yaml", False),
    ],
)
def test_is_release_path(path: bytes, expected: bool) -> None:
    assert is_release_path(path) is expected


EVERY_KIND = [
    b"release-policy.yaml",
    b".github/CODEOWNERS",
    b"artifacts/api/artifact.yaml",
    b"state/qa/api.yaml",
    b"rejections/api/1.1.0.yaml",
    b"src/a.py",
    b".github/workflows/ci.yml",
]


@pytest.mark.parametrize(
    ("globs", "in_release_repository", "expected"),
    [
        pytest.param(["**"], True, {b"src/a.py", b".github/workflows/ci.yml"}, id="everything"),
        pytest.param(["**"], False, set(EVERY_KIND), id="everything-elsewhere"),
        pytest.param(["*.yaml"], True, set(), id="yaml"),
        pytest.param(["*.yaml"], False, {b"release-policy.yaml"}, id="yaml-elsewhere"),
        pytest.param([".github/**"], True, {b".github/workflows/ci.yml"}, id="github"),
        pytest.param(
            [".github/**"],
            False,
            {b".github/CODEOWNERS", b".github/workflows/ci.yml"},
            id="github-elsewhere",
        ),
        pytest.param(
            ["src/**", "state/**"],
            False,
            {b"state/qa/api.yaml", b"src/a.py"},
            id="two-globs-elsewhere",
        ),
        pytest.param(["docs/**"], True, set(), id="no-match"),
        pytest.param([], True, set(), id="no-glob"),
    ],
)
def test_input_paths(globs: list[str], in_release_repository: bool, expected: set[bytes]) -> None:
    """Both the globs and the paths are passed as iterators, which are read once: a release
    path is removed first in the release repository, whatever a glob says, and is a path like
    any other elsewhere."""
    got = input_paths(iter(globs), iter(EVERY_KIND), in_release_repository=in_release_repository)
    assert got == frozenset(expected)


@pytest.mark.parametrize("in_release_repository", [True, False], ids=["release", "elsewhere"])
@pytest.mark.parametrize(
    "paths", [[], [b"src/a.py"]], ids=["no-path", "a-path-the-first-glob-matches"]
)
def test_input_paths_checks_every_glob_whatever_the_paths(
    in_release_repository: bool, paths: list[bytes]
) -> None:
    with pytest.raises(ValueError, match="not an input glob"):
        input_paths(["src/**", "/x"], paths, in_release_repository=in_release_repository)

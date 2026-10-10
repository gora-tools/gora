"""The input glob matcher against the real git binary (ADR-026 section 9).

An input glob means what git's ``glob`` pathspec magic means, so a pipeline that asks git sees
the paths GORA matches: for every glob ``INPUT_GLOB`` accepts, ``inputs.matches`` accepts exactly
the paths ``git ls-files ':(glob)<glob>'`` lists. git matches the bytes of a path, so the paths
are bytes throughout, some of them not ASCII and some not UTF-8: they are written into the index
with ``git update-index -z --index-info`` and read back from ``git ls-files -z``, never decoded.
They live only in the index of a temporary repository, with the empty blob, never on disk, so no
file system folds or normalises them. git runs with no user or system configuration and one
process at a time (TESTING rule 8), and is calibrated first: a git that lists nothing cannot pass.

git is on every machine that runs this suite and in CI; a missing git fails the module rather
than skipping it, because a differential test that cannot run proves nothing.
"""

import random
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from gora.domain.release.identifiers import INPUT_GLOB
from gora.domain.release_repository import inputs
from tests.integration.test_identifiers_against_git import GIT, hermetic_git_env

EMPTY_BLOB = b"e69de29bb2d1d6280b6e9b6a1ae7dfb3d9fda78b"  # SHA-1 of the empty blob

# Components of the corpus: case variants, dotfiles, names that hold *, ?, [ and ], so the
# literal comparison is tested where it differs from the pattern, and names that are not ASCII,
# where ? is one byte: U+00E9 is two bytes in UTF-8, U+65E5 U+672C six, and \xff is not UTF-8.
NOT_ASCII = ["\u00e9".encode(), "a\u00e9".encode(), "\u65e5\u672c".encode(), b"\xff", b"a\xff"]
DIRECTORIES = [
    b"src", b"Src", b"SRC", b"lib", b"a", b"b", b"ab", b"aB", b"x", b"db", b"docs", b".github",
    b".cfg", b"a*", b"a?b", b"a[b]", b"[ab]", b"**", b"ab]", b"a-b", b"a_b", b"v1.2", *NOT_ASCII,
]  # fmt: skip
FILES = [
    b"main.py", b"Main.py", b"MAIN.PY", b"x.py", b"a", b"b", b"ab", b"aB", b".hidden", b".a.py",
    b"README.md", b"Dockerfile", b"pom.xml", b"V1__init.sql", b"test_a.py", b"a*", b"a?b",
    b"a[b]", b"[ab]", b"**", b"a.b.c", b"a-1", b"a_1", b"z9", *NOT_ASCII,
]  # fmt: skip
# One directory holding each name that is not ASCII, for the calibration.
UNDER_U = [b"u/" + name for name in NOT_ASCII]


def _corpus() -> list[bytes]:
    """About 200 paths, nested up to four levels; no path is also a directory of another."""
    rng = random.Random(20261009)
    paths: set[bytes] = {b"pom.xml", b"Dockerfile", b"src/main.py", b"src/a/b/x.py", b"a*/x.py"}
    paths |= {b"a?b/x.py", b"a[b]/x.py", b"ab/x.py", b"Src/main.py", b".github/x.py"}
    paths |= {b"x/a/x/a/x.py", *UNDER_U}
    while len(paths) < 200:
        depth = rng.randint(0, 3)
        parts = [rng.choice(DIRECTORIES) for _ in range(depth)] + [rng.choice(FILES)]
        paths.add(b"/".join(parts))
    # The index cannot hold a file and a directory of one name: the file goes.
    directories = {p.rsplit(b"/", 1)[0] for p in paths if b"/" in p}
    directories |= {d.rsplit(b"/", i)[0] for d in directories for i in range(1, d.count(b"/") + 1)}
    return sorted(p for p in paths if p not in directories)


def _random_component(rng: random.Random, corpus_names: list[bytes]) -> str:
    name = rng.choice(corpus_names)
    choice = rng.randint(0, 9)
    if not name.isascii():
        # A glob is ASCII, so a name that is not is reached by wildcards: as many ? as it has
        # bytes, one fewer, one more, or one per character.
        size = len(name)
        return rng.choice(["*", "?" * size, "?" * max(size - 1, 1), "?" * (size + 1), "a*", "?*"])
    text = name.decode("ascii")
    if choice == 0:
        return "*"
    if choice == 1:
        return "**"
    if choice == 2 and len(text) > 1:
        at = rng.randrange(len(text))
        return text[:at] + "?" + text[at + 1 :]
    if choice == 3:
        return rng.choice(
            ["*.py", "a*", "*a", "*.*", "M*", "m*", ".*", "*b*", "V*__*.sql", "??", "???"]
        )
    if choice == 4:
        return rng.choice(
            ["[ab]", "[a-c]", "[A-Z]*", "[a-z]*.py", "[0-9]*", "[.]*", "[x_]*", "a[bB]"]
        )
    if choice == 5:
        return rng.choice(["a*", "a?b", "a[b]", "[ab]", "ab"])  # names the corpus holds literally
    return text


def _random_glob(rng: random.Random, corpus: list[bytes], corpus_names: list[bytes]) -> str:
    """Random components, or a path of the corpus with some components made wildcards."""
    if rng.random() < 0.5:
        return "/".join(_random_component(rng, corpus_names) for _ in range(rng.randint(1, 4)))
    parts = rng.choice(corpus).split(b"/")
    if rng.random() < 0.3:
        parts = parts[: rng.randint(1, len(parts))]
    return "/".join(
        part.decode("ascii")
        if part.isascii() and rng.random() >= 0.5
        else _random_component(rng, [part])
        for part in parts
    )


FIXED_GLOBS = [
    "src", "src/**", "**", "**/x.py", "a/**/x.py", "*", "*/*", "src/*", "a*", "a?b", "a[b]",
    "[ab]", "**/a*", "**/.hidden", "*.xml", "pom.xml", "Src", "SRC/**", "?", "??", "*/main.py",
    "**/[A-Z]*", "a*/x.py", "a[b]/x.py", "x/a/x/a/x.py", "x/**/x.py", ".github", ".*", "???",
    "u/?", "u/??", "u/???", "u/????", "u/??????", "u/*", "u/a?", "u/a??", "**/??", "**/a?",
    "[a-z]", "u/[a-z]*",
]  # fmt: skip


def _globs(corpus: list[bytes]) -> list[str]:
    rng = random.Random(20261009)
    names = sorted({part for path in corpus for part in path.split(b"/")})
    globs = set(FIXED_GLOBS)
    globs |= {p.decode("ascii") for p in corpus[::7] if p.isascii()}
    globs = {glob for glob in globs if INPUT_GLOB.is_valid(glob)}
    while len(globs) < 560:
        glob = _random_glob(rng, corpus, names)
        if INPUT_GLOB.is_valid(glob):
            globs.add(glob)
    return sorted(globs)


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[dict[str, str], Path]]:
    env = hermetic_git_env(tmp_path_factory.mktemp("git-home"))
    root = tmp_path_factory.mktemp("globs")
    _git(env, root, "-c", "init.defaultBranch=main", "init", "-q")
    index = b"".join(b"100644 " + EMPTY_BLOB + b"\t" + path + b"\0" for path in _corpus())
    _git(env, root, "update-index", "-z", "--index-info", stdin=index)
    yield env, root


def _git(env: dict[str, str], root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """One git process, waited for before the next starts (TESTING rule 8)."""
    run = subprocess.run(
        [str(GIT), *args], env=env, cwd=root, input=stdin, capture_output=True, check=True
    )
    return run.stdout


def git_lists(env: dict[str, str], root: Path, glob: str) -> list[bytes]:
    out = _git(env, root, "-c", "core.ignorecase=false", "ls-files", "-z", "--", f":(glob){glob}")
    return sorted(path for path in out.split(b"\0") if path)


def test_the_index_holds_the_corpus_and_nothing_is_on_disk(
    repository: tuple[dict[str, str], Path],
) -> None:
    env, root = repository
    assert git_lists(env, root, "**") == _corpus()
    assert len(_corpus()) > 150
    assert sum(not path.isascii() for path in _corpus()) > 20  # names that are not ASCII
    assert set(UNDER_U) <= set(_corpus())
    assert sorted(p.name for p in root.iterdir()) == [".git"]


def test_the_oracle_is_calibrated(repository: tuple[dict[str, str], Path]) -> None:
    """For fixed globs git lists exactly these paths, so a git that lists nothing fails."""
    env, root = repository
    assert git_lists(env, root, "src/**") == [p for p in _corpus() if p.startswith(b"src/")]
    assert git_lists(env, root, "pom.xml") == [b"pom.xml"]
    listed = set(git_lists(env, root, "a*/x.py"))
    assert {b"a*/x.py", b"a?b/x.py", b"a[b]/x.py", b"ab/x.py"} <= listed
    assert git_lists(env, root, "a[b]/x.py") == [b"a[b]/x.py", b"ab/x.py"]
    assert b"Src/main.py" not in git_lists(env, root, "src/**")  # case-sensitive


def test_git_matches_bytes(repository: tuple[dict[str, str], Path]) -> None:
    """Calibration of the decision: in git, ? is one byte, so a name's length is in bytes."""
    env, root = repository
    e_acute, a_e_acute, nihon, ff, a_ff = UNDER_U
    assert git_lists(env, root, "u/?") == [ff]
    assert git_lists(env, root, "u/??") == sorted([e_acute, a_ff])
    assert git_lists(env, root, "u/???") == [a_e_acute]
    assert git_lists(env, root, "u/??????") == [nihon]
    assert git_lists(env, root, "u/*") == sorted(UNDER_U)


def test_gora_matches_every_glob_as_git_does(repository: tuple[dict[str, str], Path]) -> None:
    env, root = repository
    corpus = _corpus()
    globs = _globs(corpus)
    assert len(globs) >= 500
    assert sum(any(c in g for c in "*?[") for g in globs) > 400  # calibration: wildcards
    disagree = []
    nonempty = 0
    not_ascii = 0
    for glob in globs:
        listed = git_lists(env, root, glob)
        nonempty += bool(listed)
        not_ascii += any(not path.isascii() for path in listed)
        matched = [path for path in corpus if inputs.matches(glob, path)]
        if matched != listed:
            disagree.append((glob, sorted(set(matched) ^ set(listed))[:5]))
    assert disagree == []
    assert nonempty > 200  # calibration: many globs matched something
    assert not_ascii > 50  # calibration: many reached a path that is not ASCII

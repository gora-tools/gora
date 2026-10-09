"""The input glob matcher against the real git binary (ADR-026 section 9).

An input glob means what git's ``glob`` pathspec magic means, so a pipeline that asks git sees
the paths GORA matches: for every glob ``INPUT_GLOB`` accepts, ``inputs.matches`` accepts exactly
the paths ``git ls-files ':(glob)<glob>'`` lists. The paths live only in the index of a temporary
repository (``git update-index --index-info`` with the empty blob), never on disk, so no file
system folds their case. git runs with no user or system configuration and one process at a time
(TESTING rule 8), and is calibrated first: a git that lists nothing cannot pass.

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

EMPTY_BLOB = "e69de29bb2d1d6280b6e9b6a1ae7dfb3d9fda78b"  # SHA-1 of the empty blob

# Components of the corpus: case variants, dotfiles, and names that hold *, ?, [ and ], so the
# literal comparison is tested where it differs from the pattern.
DIRECTORIES = [
    "src", "Src", "SRC", "lib", "a", "b", "ab", "aB", "x", "db", "docs", ".github", ".cfg",
    "a*", "a?b", "a[b]", "[ab]", "**", "ab]", "a-b", "a_b", "v1.2",
]  # fmt: skip
FILES = [
    "main.py", "Main.py", "MAIN.PY", "x.py", "a", "b", "ab", "aB", ".hidden", ".a.py", "README.md",
    "Dockerfile", "pom.xml", "V1__init.sql", "test_a.py", "a*", "a?b", "a[b]", "[ab]", "**",
    "a.b.c", "a-1", "a_1", "z9",
]  # fmt: skip


def _corpus() -> list[str]:
    """About 200 paths, nested up to four levels; no path is also a directory of another."""
    rng = random.Random(20261009)
    paths: set[str] = {"pom.xml", "Dockerfile", "src/main.py", "src/a/b/x.py", "a*/x.py"}
    paths |= {"a?b/x.py", "a[b]/x.py", "ab/x.py", "Src/main.py", ".github/x.py", "x/a/x/a/x.py"}
    while len(paths) < 200:
        depth = rng.randint(0, 3)
        parts = [rng.choice(DIRECTORIES) for _ in range(depth)] + [rng.choice(FILES)]
        paths.add("/".join(parts))
    # The index cannot hold a file and a directory of one name: the file goes.
    directories = {p.rsplit("/", 1)[0] for p in paths if "/" in p}
    directories |= {d.rsplit("/", i)[0] for d in directories for i in range(1, d.count("/") + 1)}
    return sorted(p for p in paths if p not in directories)


def _random_component(rng: random.Random, corpus_names: list[str]) -> str:
    name = rng.choice(corpus_names)
    choice = rng.randint(0, 9)
    if choice == 0:
        return "*"
    if choice == 1:
        return "**"
    if choice == 2 and len(name) > 1:
        at = rng.randrange(len(name))
        return name[:at] + "?" + name[at + 1 :]
    if choice == 3:
        return rng.choice(["*.py", "a*", "*a", "*.*", "M*", "m*", ".*", "*b*", "V*__*.sql"])
    if choice == 4:
        return rng.choice(
            ["[ab]", "[a-c]", "[A-Z]*", "[a-z]*.py", "[0-9]*", "[.]*", "[x_]*", "a[bB]"]
        )
    if choice == 5:
        return rng.choice(["a*", "a?b", "a[b]", "[ab]", "ab"])  # names the corpus holds literally
    return name


def _random_glob(rng: random.Random, corpus: list[str], corpus_names: list[str]) -> str:
    """Random components, or a path of the corpus with some components made wildcards."""
    if rng.random() < 0.5:
        return "/".join(_random_component(rng, corpus_names) for _ in range(rng.randint(1, 4)))
    parts = rng.choice(corpus).split("/")
    if rng.random() < 0.3:
        parts = parts[: rng.randint(1, len(parts))]
    return "/".join(
        _random_component(rng, [part]) if rng.random() < 0.5 else part for part in parts
    )


FIXED_GLOBS = [
    "src", "src/**", "**", "**/x.py", "a/**/x.py", "*", "*/*", "src/*", "a*", "a?b", "a[b]",
    "[ab]", "**/a*", "**/.hidden", "*.xml", "pom.xml", "Src", "SRC/**", "?", "??", "*/main.py",
    "**/[A-Z]*", "a*/x.py", "a[b]/x.py", "x/a/x/a/x.py", "x/**/x.py", ".github", ".*",
]  # fmt: skip


def _globs(corpus: list[str]) -> list[str]:
    rng = random.Random(20261009)
    names = sorted({part for path in corpus for part in path.split("/")})
    globs = set(FIXED_GLOBS) | {path for path in corpus[::7] if INPUT_GLOB.is_valid(path)}
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
    index = "".join(f"100644 {EMPTY_BLOB}\t{path}\n" for path in _corpus())
    _git(env, root, "update-index", "--index-info", stdin=index)
    yield env, root


def _git(env: dict[str, str], root: Path, *args: str, stdin: str | None = None) -> str:
    """One git process, waited for before the next starts (TESTING rule 8)."""
    run = subprocess.run(
        [str(GIT), *args],
        env=env,
        cwd=root,
        input=stdin.encode("utf-8") if stdin is not None else None,
        capture_output=True,
        check=True,
    )
    return run.stdout.decode("utf-8")


def git_lists(env: dict[str, str], root: Path, glob: str) -> list[str]:
    out = _git(env, root, "-c", "core.ignorecase=false", "ls-files", "-z", "--", f":(glob){glob}")
    return sorted(path for path in out.split("\0") if path)


def test_the_index_holds_the_corpus_and_nothing_is_on_disk(
    repository: tuple[dict[str, str], Path],
) -> None:
    env, root = repository
    assert git_lists(env, root, "**") == _corpus()
    assert len(_corpus()) > 150
    assert sorted(p.name for p in root.iterdir()) == [".git"]


def test_the_oracle_is_calibrated(repository: tuple[dict[str, str], Path]) -> None:
    """For fixed globs git lists exactly these paths, so a git that lists nothing fails."""
    env, root = repository
    assert git_lists(env, root, "src/**") == [p for p in _corpus() if p.startswith("src/")]
    assert git_lists(env, root, "pom.xml") == ["pom.xml"]
    assert {"a*/x.py", "a?b/x.py", "a[b]/x.py", "ab/x.py"} <= set(git_lists(env, root, "a*/x.py"))
    assert git_lists(env, root, "a[b]/x.py") == ["a[b]/x.py", "ab/x.py"]
    assert "Src/main.py" not in git_lists(env, root, "src/**")  # case-sensitive


def test_gora_matches_every_glob_as_git_does(repository: tuple[dict[str, str], Path]) -> None:
    env, root = repository
    corpus = _corpus()
    globs = _globs(corpus)
    assert len(globs) >= 500
    assert sum(any(c in g for c in "*?[") for g in globs) > 400  # calibration: wildcards
    disagree = []
    nonempty = 0
    for glob in globs:
        listed = git_lists(env, root, glob)
        nonempty += bool(listed)
        matched = [path for path in corpus if inputs.matches(glob, path)]
        if matched != listed:
            disagree.append((glob, sorted(set(matched) ^ set(listed))[:5]))
    assert disagree == []
    assert nonempty > 200  # calibration: many globs matched something

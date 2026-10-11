"""Which commit is an artifact's candidate, and its version (ADR-022 section 4, ADR-024 section 1,
ADR-026 section 9, ADR-027 section 4; L8, L10, L11).

What is held: a commit is refused when it is not one of the commits merged after the declaration
(L8), or is the registered candidate or older than it (L10); a later candidate is judged by the
paths in which its tree differs from the registered candidate's, not by its own change, so a
commit of release paths only carries a change of inputs whose build was never registered, and a
change made and changed back is none (L8); its version is the registered candidate's raised by the
highest bump among the commits since it that changed an input, so a build that failed is counted
by the next (ADR-024 section 1); the first candidate is judged by its own change alone, and takes
the initial version whatever its message says (ADR-026 section 9, ADR-027 section 4), unless the
declaration is stable below ``1.0.0-rc`` (L11). A caller's error is a ``ValueError``, never a
refusal, and comes first, in a fixed order.

Expectations are written out literally, never derived from the code under test. A refusal is
asserted as ``(law, reason)``, the reason equal to the module's constant, and a candidate as
``(commit sha, version, [sha of each change])``. The ids name the cases, so a failure says which
rule broke. Every message is value-free.
"""

import pickle
import re
from collections.abc import Iterable, Sequence

import pytest

from gora.domain.release.candidates import (
    DIFFER_WITHOUT_CANDIDATE,
    INITIAL_FINAL,
    L8_NOT_AFTER_DECLARATION,
    L8_RELEASE_PATHS,
    L8_SAME_INPUTS,
    L10_OLDER,
    L10_REGISTERED,
    L11_STABLE_BELOW_ONE,
    NO_INPUT,
    REGISTERED_ELSEWHERE,
    REGISTERED_FINAL,
    TWICE,
    UNCHANGED_DIFFER,
    Commit,
    Declaration,
    RefusedError,
    Registered,
    candidate,
)
from gora.domain.release.identifiers import SOURCE_COMMIT
from gora.domain.release.versions import Version

# What a case asserts: (law, reason) of a refusal, or (commit sha, version, [sha of each change]).
Outcome = tuple[str, str] | tuple[str, str, list[str]]

DEFAULT_INPUTS = ("src/**", "pyproject.toml", "uv.lock")
RELEASE_FILES = (
    "release-policy.yaml",
    ".github/CODEOWNERS",
    "artifacts/gora/artifact.yaml",
    "state/dev/gora.yaml",
    "rejections/gora/0.2.1.yaml",
)
GLOB_PREFIX = "not an input glob: "
SOURCE_COMMIT_ERROR = f"source commit {SOURCE_COMMIT.rule}"


def c(n: int) -> str:
    """The object name of the n-th commit."""
    return f"{n:040x}"


def commit(n: int, message: str, *changed: str) -> Commit:
    return Commit(c(n), message, frozenset(path.encode() for path in changed))


def declared(
    inputs: Sequence[str] = DEFAULT_INPUTS,
    initial: str = "0.2.1-rc",
    *,
    stable: bool = False,
    release: bool = True,
) -> Declaration:
    return Declaration(tuple(inputs), Version.parse(initial), stable, release)


def registered(n: int, version: str) -> Registered:
    return Registered(c(n), Version.parse(version))


def paths(*names: str) -> list[bytes]:
    return [name.encode() for name in names]


def outcome(
    declaration: Declaration,
    history: Sequence[Commit],
    requested: str,
    *,
    known: Registered | None = None,
    differ: Iterable[bytes] | None = None,
) -> Outcome:
    """What ``candidate`` answers, as a case asserts it; the str of a refusal is held here."""
    try:
        answer = candidate(declaration, history, requested, registered=known, differ=differ)
    except RefusedError as refusal:
        assert str(refusal) == f"{refusal.law}: {refusal.reason}"
        return (refusal.law, refusal.reason)
    return (answer.commit.sha, str(answer.version), [each.sha for each in answer.changes])


# --- 3.1 the history: L8 and L10 --------------------------------------------------------------

TWO_FIXES = [commit(1, "fix: a", "src/a.py"), commit(2, "fix: b", "src/b.py")]

HISTORY = [
    pytest.param(
        [commit(1, "fix: a", "src/a.py"), commit(2, "feat: b", "src/b.py")],
        c(9),
        None,
        None,
        ("L8", L8_NOT_AFTER_DECLARATION),
        id="not-in-history",
    ),
    pytest.param([], c(1), None, None, ("L8", L8_NOT_AFTER_DECLARATION), id="empty-history"),
    pytest.param(
        TWO_FIXES,
        c(9),
        registered(1, "1.0.1-rc"),
        [],
        ("L8", L8_NOT_AFTER_DECLARATION),
        id="not-in-history-registered",
    ),
    pytest.param(
        TWO_FIXES,
        c(2),
        registered(2, "1.0.1-rc"),
        [],
        ("L10", L10_REGISTERED),
        id="is-the-registered",
    ),
    pytest.param(
        TWO_FIXES,
        c(1),
        registered(2, "1.0.1-rc"),
        [],
        ("L10", L10_OLDER),
        id="older-inputs",
    ),
    pytest.param(
        [commit(1, "fix: a", "docs/a.md"), commit(2, "fix: b", "src/b.py")],
        c(1),
        registered(2, "1.0.1-rc"),
        [],
        ("L10", L10_OLDER),
        id="older-no-inputs",
    ),
]


@pytest.mark.parametrize(("history", "requested", "known", "differ", "expected"), HISTORY)
def test_the_history_refuses_what_is_not_after_the_declaration_or_not_after_the_registered(
    history: list[Commit],
    requested: str,
    known: Registered | None,
    differ: list[bytes] | None,
    expected: Outcome,
) -> None:
    assert outcome(declared(), history, requested, known=known, differ=differ) == expected


# --- 3.2 the first candidate ------------------------------------------------------------------

ONE_INPUT = [commit(1, "feat: a", "src/a.py")]

FIRST = [
    pytest.param(
        [commit(1, "fix: a", "src/a.py")],
        c(1),
        declared(),
        (c(1), "0.2.1-rc", [c(1)]),
        id="first-an-input",
    ),
    pytest.param(
        [commit(1, "docs: a", "docs/a.md")],
        c(1),
        declared(),
        (c(1), "0.2.1-rc", [c(1)]),
        id="first-no-input",
    ),
    pytest.param(
        [commit(1, "chore: a", *RELEASE_FILES, "README.md")],
        c(1),
        declared(),
        (c(1), "0.2.1-rc", [c(1)]),
        id="first-release-files-and-more",
    ),
    pytest.param(
        [commit(1, "feat!: a", "src/a.py")],
        c(1),
        declared(),
        (c(1), "0.2.1-rc", [c(1)]),
        id="first-breaking",
    ),
    pytest.param(
        [commit(1, "Update a", "src/a.py")],
        c(1),
        declared(),
        (c(1), "0.2.1-rc", [c(1)]),
        id="first-not-conventional",
    ),
    pytest.param(
        [
            commit(1, "feat: a", "src/a.py"),
            commit(2, "feat!: b", "src/b.py"),
            commit(3, "fix: c", "src/c.py"),
            commit(4, "feat: d", "src/d.py"),
        ],
        c(3),
        declared(),
        (c(3), "0.2.1-rc", [c(3)]),
        id="first-after-unregistered",
    ),
    pytest.param(
        [commit(1, "chore: a", *RELEASE_FILES)],
        c(1),
        declared(),
        ("L8", L8_RELEASE_PATHS),
        id="first-release-files",
    ),
    pytest.param(
        [commit(1, "chore: a", "state/dev/gora.yaml")],
        c(1),
        declared(),
        ("L8", L8_RELEASE_PATHS),
        id="first-a-promotion",
    ),
    pytest.param(
        [commit(1, "chore: a")], c(1), declared(), ("L8", L8_RELEASE_PATHS), id="first-nothing"
    ),
    pytest.param(
        [commit(1, "chore: a", "state/dev/gora.yaml")],
        c(1),
        declared(release=False),
        (c(1), "0.2.1-rc", [c(1)]),
        id="first-elsewhere",
    ),
    pytest.param(
        [commit(1, "chore: a")],
        c(1),
        declared(release=False),
        ("L8", L8_RELEASE_PATHS),
        id="first-elsewhere-nothing",
    ),
    pytest.param(
        ONE_INPUT,
        c(1),
        declared(initial="0.2.1-rc", stable=False),
        (c(1), "0.2.1-rc", [c(1)]),
        id="initial-unstable",
    ),
    pytest.param(
        ONE_INPUT,
        c(1),
        declared(initial="1.0.0-rc", stable=True),
        (c(1), "1.0.0-rc", [c(1)]),
        id="initial-stable-one",
    ),
    pytest.param(
        ONE_INPUT,
        c(1),
        declared(initial="1.4.0-rc", stable=True),
        (c(1), "1.4.0-rc", [c(1)]),
        id="initial-stable-later",
    ),
    pytest.param(
        ONE_INPUT,
        c(1),
        declared(initial="0.0.0-rc", stable=False),
        (c(1), "0.0.0-rc", [c(1)]),
        id="initial-zero",
    ),
    pytest.param(
        ONE_INPUT,
        c(1),
        declared(initial="0.2.1-rc", stable=True),
        ("L11", L11_STABLE_BELOW_ONE),
        id="stable-below-one",
    ),
    pytest.param(
        ONE_INPUT,
        c(1),
        declared(initial="0.99.99-rc", stable=True),
        ("L11", L11_STABLE_BELOW_ONE),
        id="stable-below-one-high",
    ),
    pytest.param(
        ONE_INPUT,
        c(1),
        declared(initial="0.0.0-rc", stable=True),
        ("L11", L11_STABLE_BELOW_ONE),
        id="stable-below-one-zero",
    ),
    pytest.param(
        [commit(1, "chore: a", "release-policy.yaml")],
        c(1),
        declared(initial="0.2.1-rc", stable=True),
        ("L8", L8_RELEASE_PATHS),
        id="l8-before-l11",
    ),
]


@pytest.mark.parametrize(("history", "requested", "declaration", "expected"), FIRST)
def test_the_first_candidate_takes_the_initial_version_unless_it_changes_release_files_only(
    history: list[Commit], requested: str, declaration: Declaration, expected: Outcome
) -> None:
    assert outcome(declaration, history, requested) == expected


@pytest.mark.parametrize(
    ("history", "requested", "expected"),
    [
        pytest.param(
            [
                commit(1, "feat: a", "src/a.py"),
                commit(2, "chore: b", "state/dev/gora.yaml"),
            ],
            c(2),
            ("L8", L8_RELEASE_PATHS),
            id="first-judged-by-its-own-change",
        )
    ],
)
def test_the_first_candidate_is_judged_by_its_own_change(
    history: list[Commit], requested: str, expected: Outcome
) -> None:
    """With no registered candidate L8 has no tree to compare with, so the first candidate is
    judged by its own change, not by the commits before it (ADR-026 section 9): ``c1`` changed
    an input, and ``c2``, a promotion that changes release files only, is still refused."""
    assert outcome(declared(), history, requested) == expected


# --- 3.3 a later candidate --------------------------------------------------------------------

MAP = [
    commit(10, "fix: refuse an empty stage policy", "src/policy.py"),
    commit(11, "fix: refuse an empty stage", "src/stage.py"),
    commit(12, "feat(cli): add the rollback command", "src/cli.py"),
    commit(13, "docs: explain the stages", "docs/stages.md"),
    commit(14, "fix: refuse a rejected digest", "src/reject.py"),
]


@pytest.mark.parametrize(
    ("requested", "known", "differ", "expected"),
    [
        pytest.param(
            c(11),
            registered(10, "1.0.1-rc"),
            paths("src/stage.py"),
            (c(11), "1.0.2-rc", [c(11)]),
            id="map-c11",
        ),
        pytest.param(
            c(12),
            registered(11, "1.0.2-rc"),
            paths("src/cli.py"),
            (c(12), "1.1.0-rc", [c(12)]),
            id="map-c12",
        ),
        pytest.param(
            c(13),
            registered(12, "1.1.0-rc"),
            paths("docs/stages.md"),
            ("L8", L8_SAME_INPUTS),
            id="map-c13",
        ),
        pytest.param(
            c(14),
            registered(12, "1.1.0-rc"),
            paths("docs/stages.md", "src/reject.py"),
            (c(14), "1.1.1-rc", [c(14)]),
            id="map-c14",
        ),
    ],
)
def test_the_map_of_release_model_sections_3_and_4(
    requested: str, known: Registered, differ: list[bytes], expected: Outcome
) -> None:
    """``c13`` changes no input, so it is no candidate; ``c14`` is computed after ``1.1.0-rc``,
    which was rejected in a stage and keeps its number, so it is ``1.1.1-rc``. The paths that
    differ are passed as an iterator, which is read once."""
    assert outcome(declared(), MAP, requested, known=known, differ=iter(differ)) == expected


@pytest.mark.parametrize(
    ("history", "requested", "known", "differ", "expected"),
    [
        pytest.param(
            [
                commit(1, "fix: a", "src/a.py"),
                commit(2, "feat!: drop the old flag", "src/flag.py"),
                commit(3, "docs: b", "docs/b.md"),
            ],
            c(3),
            registered(1, "1.0.1-rc"),
            paths("src/flag.py", "docs/b.md"),
            (c(3), "2.0.0-rc", [c(2)]),
            id="build-failed",
        )
    ],
)
def test_a_build_that_failed_is_counted_by_the_next_candidate(
    history: list[Commit],
    requested: str,
    known: Registered,
    differ: list[bytes],
    expected: Outcome,
) -> None:
    """``c2`` was never registered, its build failed; ``c3`` is the candidate and its version
    counts ``c2``'s breaking change, not ``c3``'s own message (ADR-022 section 4, ADR-024
    section 1)."""
    assert outcome(declared(), history, requested, known=known, differ=differ) == expected


@pytest.mark.parametrize(
    ("history", "requested", "known", "differ", "expected"),
    [
        pytest.param(
            [
                commit(0, "fix: a", "src/a.py"),
                commit(1, "feat: b", "src/b.py"),
                commit(2, "promote(dev): gora 1.0.1-rc", "state/dev/gora.yaml"),
            ],
            c(2),
            registered(0, "1.0.1-rc"),
            paths("src/b.py", "state/dev/gora.yaml"),
            (c(2), "1.1.0-rc", [c(1)]),
            id="release-only-carries",
        )
    ],
)
def test_a_commit_of_release_paths_only_carries_the_inputs_changed_before_it(
    history: list[Commit],
    requested: str,
    known: Registered,
    differ: list[bytes],
    expected: Outcome,
) -> None:
    """``c1`` changed an input and was never registered; ``c2`` changes release files only. It
    is a candidate because the trees differ in an input (ADR-026 section 9 and its note of
    2026-10-11), and its version counts ``c1``, not itself."""
    assert outcome(declared(), history, requested, known=known, differ=differ) == expected


CHANGED_BACK = [
    commit(0, "fix: a", "src/a.py"),
    commit(1, "feat: b", "src/a.py"),
    commit(2, 'revert: "feat: b"', "src/a.py"),
]


@pytest.mark.parametrize(
    ("history", "requested", "known", "differ", "expected"),
    [
        pytest.param(
            CHANGED_BACK,
            c(2),
            registered(0, "1.0.1-rc"),
            [],
            ("L8", L8_SAME_INPUTS),
            id="changed-back",
        )
    ],
)
def test_an_input_changed_and_changed_back_is_no_candidate(
    history: list[Commit],
    requested: str,
    known: Registered,
    differ: list[bytes],
    expected: Outcome,
) -> None:
    """L8 compares the trees: ``c1`` and ``c2`` each changed ``src/a.py``, and the tree at ``c2``
    is the registered candidate's, so no path differs and nothing is an input that differs."""
    assert outcome(declared(), history, requested, known=known, differ=differ) == expected


@pytest.mark.parametrize(
    ("history", "requested", "known", "differ", "expected"),
    [
        pytest.param(
            [*CHANGED_BACK, commit(3, "fix: c", "src/c.py")],
            c(3),
            registered(0, "1.0.1-rc"),
            paths("src/c.py"),
            (c(3), "1.1.0-rc", [c(1), c(2), c(3)]),
            id="every-commit-counts",
        )
    ],
)
def test_every_commit_since_that_changed_an_input_counts_even_when_the_change_was_reverted(
    history: list[Commit],
    requested: str,
    known: Registered,
    differ: list[bytes],
    expected: Outcome,
) -> None:
    """The tree differs only in ``src/c.py``, yet ``c1`` and ``c2`` each changed an input and
    count: a revert bumps by its own message, ``revert`` is a patch, and ``feat: b`` is still
    minor (ADR-024 section 1)."""
    assert outcome(declared(), history, requested, known=known, differ=differ) == expected


@pytest.mark.parametrize(
    ("declaration", "history", "requested", "known", "differ", "expected"),
    [
        pytest.param(
            declared(),
            [
                commit(0, "fix: a", "src/a.py"),
                commit(1, "fix: b", "src/b.py"),
                commit(2, "docs: c", "docs/c.md"),
                commit(3, "feat: d", "uv.lock"),
                commit(4, "feat!: e", "src/e.py"),
            ],
            c(3),
            registered(0, "1.4.2-rc"),
            paths("src/b.py", "docs/c.md", "uv.lock"),
            (c(3), "1.5.0-rc", [c(1), c(3)]),
            id="oldest-first",
        ),
        pytest.param(
            declared(("**",)),
            [
                commit(0, "fix: a", "src/a.py"),
                commit(1, "feat(dev): promote gora", "state/dev/gora.yaml"),
                commit(2, "fix: b", "src/b.py"),
            ],
            c(1),
            registered(0, "1.0.1-rc"),
            paths("state/dev/gora.yaml"),
            ("L8", L8_SAME_INPUTS),
            id="release-paths-promotion",
        ),
        pytest.param(
            declared(("**",)),
            [
                commit(0, "fix: a", "src/a.py"),
                commit(1, "feat(dev): promote gora", "state/dev/gora.yaml"),
                commit(2, "fix: b", "src/b.py"),
            ],
            c(2),
            registered(0, "1.0.1-rc"),
            paths("state/dev/gora.yaml", "src/b.py"),
            (c(2), "1.0.2-rc", [c(2)]),
            id="release-paths-next",
        ),
        pytest.param(
            declared(("**",), release=False),
            [
                commit(0, "fix: a", "src/a.py"),
                commit(1, "feat: b", "state/dev/gora.yaml"),
            ],
            c(1),
            registered(0, "1.0.1-rc"),
            paths("state/dev/gora.yaml"),
            (c(1), "1.1.0-rc", [c(1)]),
            id="release-paths-elsewhere",
        ),
        pytest.param(
            declared(),
            [
                commit(0, "fix: a", "src/a.py"),
                commit(1, "fix: b", "src/b.py"),
                commit(2, "feat!: c", "src/c.py"),
            ],
            c(1),
            registered(0, "1.0.1-rc"),
            paths("src/b.py"),
            (c(1), "1.0.2-rc", [c(1)]),
            id="after-requested",
        ),
        pytest.param(
            declared(stable=True),
            [commit(0, "fix: a", "src/a.py"), commit(1, "feat: b", "src/b.py")],
            c(1),
            registered(0, "0.3.0-rc"),
            paths("src/b.py"),
            (c(1), "1.0.0-rc", [c(1)]),
            id="leaves-zero-stable",
        ),
        pytest.param(
            declared(stable=False),
            [commit(0, "fix: a", "src/a.py"), commit(1, "feat: b", "src/b.py")],
            c(1),
            registered(0, "0.3.0-rc"),
            paths("src/b.py"),
            (c(1), "0.4.0-rc", [c(1)]),
            id="leaves-zero-unstable",
        ),
    ],
)
def test_a_later_candidate_is_judged_by_the_trees_and_counted_by_the_commits_since(
    declaration: Declaration,
    history: list[Commit],
    requested: str,
    known: Registered,
    differ: list[bytes],
    expected: Outcome,
) -> None:
    """The commits since the registered candidate, the requested one included and the registered
    one not, oldest first; a commit after the requested one is not counted; a path under a
    release directory is no input in the release repository, and is one anywhere else; the
    major version leaves ``0`` by ``stable``, once, and a later candidate is never refused for
    the initial version."""
    assert outcome(declaration, history, requested, known=known, differ=differ) == expected


# --- 3.4 a caller's errors --------------------------------------------------------------------

ONE = [commit(1, "fix: a", "src/a.py"), commit(2, "fix: b", "src/b.py")]
BAD = Commit("A" * 40, "fix: z", frozenset())


def exactly(message: str) -> str:
    return f"^{re.escape(message)}\\Z"


@pytest.mark.parametrize(
    ("declaration", "history", "requested", "known", "differ", "message"),
    [
        pytest.param(declared(()), ONE, c(1), None, None, NO_INPUT, id="no-input"),
        pytest.param(
            Declaration(DEFAULT_INPUTS, Version(1, 0, 0, final=True), False, True),
            ONE,
            c(1),
            None,
            None,
            INITIAL_FINAL,
            id="initial-final",
        ),
        pytest.param(declared(), [*ONE, ONE[0]], c(1), None, None, TWICE, id="twice"),
        pytest.param(
            declared(),
            ONE,
            c(2),
            registered(1, "1.0.1-rc"),
            None,
            DIFFER_WITHOUT_CANDIDATE,
            id="registered-without-differ",
        ),
        pytest.param(
            declared(),
            ONE,
            c(2),
            None,
            paths("src/b.py"),
            DIFFER_WITHOUT_CANDIDATE,
            id="differ-without-registered",
        ),
        pytest.param(
            declared(),
            ONE,
            c(2),
            registered(1, "1.0.1"),
            paths("src/b.py"),
            REGISTERED_FINAL,
            id="registered-final",
        ),
        pytest.param(
            declared(),
            ONE,
            c(2),
            registered(7, "1.0.1-rc"),
            paths("src/b.py"),
            REGISTERED_ELSEWHERE,
            id="registered-elsewhere",
        ),
        pytest.param(
            declared(),
            ONE,
            c(2),
            registered(1, "1.0.1-rc"),
            paths("src/a.py"),
            UNCHANGED_DIFFER,
            id="differ-not-changed-since",
        ),
        pytest.param(
            declared(),
            ONE,
            c(2),
            registered(1, "1.0.1-rc"),
            paths("docs/x.md"),
            UNCHANGED_DIFFER,
            id="differ-not-changed-before-l8",
        ),
        pytest.param(
            declared(), [ONE[0]], "A" * 40, None, None, SOURCE_COMMIT_ERROR, id="sha-requested"
        ),
        pytest.param(
            declared(),
            [Commit("A" * 40, "fix: a", frozenset({b"src/a.py"}))],
            c(1),
            None,
            None,
            SOURCE_COMMIT_ERROR,
            id="sha-history",
        ),
    ],
)
def test_a_caller_s_error_is_a_value_error_with_one_fixed_text(
    declaration: Declaration,
    history: list[Commit],
    requested: str,
    known: Registered | None,
    differ: list[bytes] | None,
    message: str,
) -> None:
    """``differ-not-changed-before-l8``: ``docs/x.md`` is no input, so L8 would refuse it as the
    same inputs; the caller's error comes first, since no history can give that path."""
    with pytest.raises(ValueError, match=exactly(message)) as raised:
        candidate(declaration, history, requested, registered=known, differ=differ)
    assert not isinstance(raised.value, RefusedError)


@pytest.mark.parametrize(
    "glob", ["/src", "a b", "src/../x"], ids=["glob-root", "glob-space", "glob-dots"]
)
def test_a_glob_the_grammar_refuses_is_a_caller_s_error_and_never_named(glob: str) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(GLOB_PREFIX)}") as raised:
        candidate(declared(("src/**", glob)), ONE, c(1))
    assert not isinstance(raised.value, RefusedError)
    assert glob not in str(raised.value)


@pytest.mark.parametrize(
    ("declaration", "known", "differ", "pattern"),
    [
        pytest.param(
            declared(("/src",)),
            None,
            None,
            f"^{re.escape(GLOB_PREFIX)}",
            id="caller-before-refusal-glob",
        ),
        pytest.param(
            declared(()), None, None, exactly(NO_INPUT), id="caller-before-refusal-no-input"
        ),
        pytest.param(
            declared(),
            registered(7, "1.0.1-rc"),
            [],
            exactly(REGISTERED_ELSEWHERE),
            id="caller-before-refusal-registered-elsewhere",
        ),
    ],
)
def test_a_caller_s_error_comes_before_a_refusal(
    declaration: Declaration, known: Registered | None, differ: list[bytes] | None, pattern: str
) -> None:
    """The requested commit ``c(9)`` is not in ``ONE``, which L8 would refuse; each fault of the
    caller is reported instead."""
    with pytest.raises(ValueError, match=pattern) as raised:
        candidate(declaration, ONE, c(9), registered=known, differ=differ)
    assert not isinstance(raised.value, RefusedError)


@pytest.mark.parametrize(
    ("declaration", "history", "known", "differ", "pattern"),
    [
        pytest.param(
            Declaration((), Version(1, 0, 0, final=True), False, True),
            [BAD, ONE[0], ONE[0]],
            registered(7, "1.0.1"),
            None,
            exactly(NO_INPUT),
            id="caller-order-1",
        ),
        pytest.param(
            Declaration(("/x",), Version(1, 0, 0, final=True), False, True),
            [BAD, ONE[0], ONE[0]],
            registered(7, "1.0.1"),
            None,
            f"^{re.escape(GLOB_PREFIX)}",
            id="caller-order-2",
        ),
        pytest.param(
            Declaration(DEFAULT_INPUTS, Version(1, 0, 0, final=True), False, True),
            [BAD, ONE[0], ONE[0]],
            registered(7, "1.0.1"),
            None,
            exactly(INITIAL_FINAL),
            id="caller-order-3",
        ),
        pytest.param(
            declared(),
            [BAD, ONE[0], ONE[0]],
            registered(7, "1.0.1"),
            None,
            exactly(SOURCE_COMMIT_ERROR),
            id="caller-order-4",
        ),
        pytest.param(
            declared(),
            [ONE[0], ONE[0]],
            registered(7, "1.0.1"),
            None,
            exactly(TWICE),
            id="caller-order-5",
        ),
        pytest.param(
            declared(),
            ONE,
            registered(7, "1.0.1"),
            None,
            exactly(DIFFER_WITHOUT_CANDIDATE),
            id="caller-order-6",
        ),
        pytest.param(
            declared(),
            ONE,
            registered(7, "1.0.1"),
            [],
            exactly(REGISTERED_FINAL),
            id="caller-order-7",
        ),
        pytest.param(
            declared(),
            ONE,
            registered(7, "1.0.1-rc"),
            [],
            exactly(REGISTERED_ELSEWHERE),
            id="caller-order-8",
        ),
    ],
)
def test_the_caller_s_errors_are_checked_in_a_fixed_order(
    declaration: Declaration,
    history: list[Commit],
    known: Registered,
    differ: list[bytes] | None,
    pattern: str,
) -> None:
    """One fault fewer at each step, the requested commit ``c(9)`` in none of the histories: the
    inputs, then the globs, the initial version, the object names, a commit twice, the paths that
    differ against the registered candidate, its version and, last, its place in the history."""
    with pytest.raises(ValueError, match=pattern) as raised:
        candidate(declaration, history, c(9), registered=known, differ=differ)
    assert not isinstance(raised.value, RefusedError)


# --- the refusal ------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["refusal-type-is-not-a-value-error"])
def test_a_refusal_is_not_a_value_error(case: str) -> None:
    """A caller's error and a refusal never meet in one ``except``: a caller that catches a
    ``ValueError`` does not swallow a law."""
    assert not issubclass(RefusedError, ValueError)
    assert issubclass(RefusedError, Exception)


@pytest.mark.parametrize("case", ["refusal-type-pickles"])
def test_a_refusal_survives_pickle_with_its_law_and_its_reason(case: str) -> None:
    refusal = RefusedError("L8", L8_SAME_INPUTS)
    again = pickle.loads(pickle.dumps(refusal))
    assert isinstance(again, RefusedError)
    assert (again.law, again.reason) == ("L8", L8_SAME_INPUTS)
    assert str(again) == f"L8: {L8_SAME_INPUTS}"

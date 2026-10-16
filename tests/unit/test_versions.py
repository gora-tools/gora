"""Versions of candidates from Conventional Commits (ADR-024 section 1, release-model section 5).

What is held: a version is ``X.Y.Z-rc`` or ``X.Y.Z`` and orders as SemVer 2.0 orders those two
forms; a commit message is read by Conventional Commits 1.0.0 into a bump (breaking is major,
``feat`` is minor, anything else is a patch), and a subject that is not a Conventional Commit
counts as a patch; a candidate's version is the previous candidate's raised by the highest bump,
and the major version stays ``0`` until ``stable`` says it leaves.

Expectations are written out literally, never derived from the code under test. The ids name
the cases, so a failure says which rule broke. Every refusal is value-free: its message is a
fixed text of the rules, the same whatever the input was.
"""

import functools
import itertools
import random
from collections.abc import Iterator
from typing import Any

import pytest

from gora.domain.release.identifiers import FINAL_VERSION, RC_VERSION
from gora.domain.release.versions import (
    Bump,
    Change,
    Subject,
    Version,
    bump_of,
    change,
    next_version,
    subject,
)
from tests.unit.test_pr_title import (
    CONFORMING,
    DEPENDABOT,
    DEPENDABOT_TITLES,
    NOT_CONFORMING,
    check_title,
)

# --- 1. parse and print -----------------------------------------------------------------------


def rc_of_length(length: int) -> str:
    """An rc version of exactly ``length`` characters: ``0.0.`` and digits, then ``-rc``."""
    return "0.0." + "1" * (length - len("0.0.-rc")) + "-rc"


ROUND_TRIP = {
    "zero": "0.0.0-rc",
    "initial": "0.2.1-rc",
    "final": "1.0.0",
    "two-digits": "10.20.30-rc",
    "128-characters": rc_of_length(128),
}

PARSE_REFUSED = {
    "empty": "",
    "two-numbers": "1.0",
    "four-numbers": "1.0.0.0",
    "leading-zero-major": "01.0.0",
    "leading-zero-minor": "1.00.0-rc",
    "v-prefix": "v1.0.0",
    "rc-counter": "1.0.0-rc.1",
    "rc-upper-case": "1.0.0-RC",
    "other-prerelease": "1.0.0-beta",
    "build-metadata": "1.0.0+build",
    "rc-build-metadata": "1.0.0-rc+build",
    "leading-space": " 1.0.0",
    "trailing-space": "1.0.0 ",
    "trailing-newline": "1.0.0\n",
    "full-width-digit": "\N{FULLWIDTH DIGIT ONE}.0.0",
    "129-characters": rc_of_length(129),
}


@pytest.mark.parametrize("text", list(ROUND_TRIP.values()), ids=list(ROUND_TRIP))
def test_a_parsed_version_prints_as_it_was_written(text: str) -> None:
    assert str(Version.parse(text)) == text


def test_parse_reads_the_form_and_the_numbers() -> None:
    assert Version.parse("1.2.3-rc") == Version(1, 2, 3, False)
    assert Version.parse("1.2.3") == Version(1, 2, 3, True)


def refusal(text: str) -> str:
    with pytest.raises(ValueError) as refused:
        Version.parse(text)
    return str(refused.value)


@pytest.mark.parametrize("text", list(PARSE_REFUSED.values()), ids=list(PARSE_REFUSED))
def test_parse_refuses_with_one_fixed_text_that_gives_both_rules(text: str) -> None:
    message = refusal(text)
    assert RC_VERSION.rule in message
    assert FINAL_VERSION.rule in message
    assert message == refusal("")


# --- 2. construct -----------------------------------------------------------------------------

# Arguments of Version(...), and the message the refusal carries.
CONSTRUCT_REFUSED: dict[str, tuple[tuple[Any, ...], str]] = {
    "negative": ((-1, 0, 0, False), "a version number is not negative"),
    "bool-number": ((True, 0, 0, False), "a version number is an int"),
    "float-number": ((1.0, 0, 0, False), "a version number is an int"),
    "final-not-a-bool": ((1, 0, 0, 1), "final is a bool"),
    "129-characters": ((0, 0, int("1" * 122), False), RC_VERSION.rule),
    "129-characters-final": ((0, 0, int("1" * 125), True), FINAL_VERSION.rule),
    "10**5000": ((0, 0, 10**5000, False), RC_VERSION.rule),
    "10**5000-final": ((0, 0, 10**5000, True), FINAL_VERSION.rule),
    # The order of the checks: the type of a number, its sign, the type of final, then the form.
    "type-before-sign": ((True, -1, 0, 1), "a version number is an int"),
    "sign-before-final": ((0, -1, 0, 1), "a version number is not negative"),
    "final-before-form": ((0, 0, 10**5000, 1), "final is a bool"),
}


@pytest.mark.parametrize(
    ("arguments", "message"), list(CONSTRUCT_REFUSED.values()), ids=list(CONSTRUCT_REFUSED)
)
def test_a_version_that_is_not_one_is_refused_with_the_rule(
    arguments: tuple[Any, ...], message: str
) -> None:
    with pytest.raises(ValueError) as refused:
        Version(*arguments)
    assert str(refused.value) == message


def test_a_version_of_128_characters_is_one() -> None:
    longest = Version(0, 0, 10**121 - 1, False)
    assert len(str(longest)) == 128


# --- 3. order ---------------------------------------------------------------------------------

ORDERED = [
    "0.9.9-rc",
    "0.9.9",
    "0.10.0-rc",
    "1.0.2-rc",
    "1.0.2",
    "1.1.0-rc",
    "1.1.1-rc",
    "1.1.1",
    "1.9.0",
    "1.10.0-rc",
    "1.10.0",
    "2.0.0-rc",
]


def test_versions_sort_by_semver_precedence_with_numbers_as_numbers() -> None:
    """Release-model section 5: ``1.0.2-rc < 1.0.2 < 1.1.0-rc < 1.1.1-rc < 1.1.1``."""
    versions = [Version.parse(text) for text in ORDERED]
    shuffled = list(versions)
    random.Random(20261011).shuffle(shuffled)
    assert shuffled != versions
    assert sorted(shuffled) == versions
    assert all(a < b for a, b in itertools.pairwise(versions))


# --- 4. a message -----------------------------------------------------------------------------

# id, message, then what the message says: conforming, commit type, breaking, bump.
MESSAGES: list[tuple[str, str, bool, str | None, bool, Bump]] = [
    ("fix", "fix: a", True, "fix", False, Bump.PATCH),
    ("feat", "feat: a", True, "feat", False, Bump.MINOR),
    ("feat-scope", "feat(cli): a", True, "feat", False, Bump.MINOR),
    ("type-case", "Feat: a", True, "feat", False, Bump.MINOR),
    ("type-case-upper", "FEAT: a", True, "feat", False, Bump.MINOR),
    ("bang-feat", "feat!: a", True, "feat", True, Bump.MAJOR),
    ("bang-scope", "fix(api)!: a", True, "fix", True, Bump.MAJOR),
    ("revert", "revert: feat(cli): a", True, "revert", False, Bump.PATCH),
    ("revert-bang", "revert!: a", True, "revert", True, Bump.MAJOR),
    ("chore", "chore: a", True, "chore", False, Bump.PATCH),
    ("deps", "build(deps): bump x from 1 to 2", True, "build", False, Bump.PATCH),
    ("footer", "fix: a\n\nBREAKING CHANGE: b", True, "fix", True, Bump.MAJOR),
    ("footer-dash", "fix: a\n\nBREAKING-CHANGE: b", True, "fix", True, Bump.MAJOR),
    ("after-body", "fix: a\n\ntext\n\nBREAKING CHANGE: b", True, "fix", True, Bump.MAJOR),
    ("after-token", "fix: a\n\nRefs: 1\nBREAKING CHANGE: b", True, "fix", True, Bump.MAJOR),
    ("after-hash", "fix: a\n\nFixes #12\nBREAKING CHANGE: b", True, "fix", True, Bump.MAJOR),
    (
        "value-lines",
        "fix: a\n\nRefs: 1\nmore\n\nBREAKING CHANGE: b",
        True,
        "fix",
        True,
        Bump.MAJOR,
    ),
    (
        "trailers-after",
        "feat: a\n\nBREAKING CHANGE: b\n\nCo-authored-by: c <c@example.com>",
        True,
        "feat",
        True,
        Bump.MAJOR,
    ),
    ("blank-spaces", "fix: a\n \t\nBREAKING CHANGE: b", True, "fix", True, Bump.MAJOR),
    ("crlf", "feat: a\r\n\r\nBREAKING CHANGE: b\r\n", True, "feat", True, Bump.MAJOR),
    ("crlf-subject", "feat: a\r\n", True, "feat", False, Bump.MINOR),
    ("bang-and-footer", "feat!: a\n\nBREAKING CHANGE: b", True, "feat", True, Bump.MAJOR),
    ("to-the-end", "fix: a\n\nRefs: 1\nmore\nBREAKING CHANGE: b", True, "fix", True, Bump.MAJOR),
    (
        "earlier-token",
        "fix: a\n\nNote: x\n\ntext\nBREAKING CHANGE: b",
        True,
        "fix",
        True,
        Bump.MAJOR,
    ),
    ("dash-list", "fix: a\n\n- #12 done\nBREAKING CHANGE: b", True, "fix", False, Bump.PATCH),
    ("in-paragraph", "fix: a\n\ntext\nBREAKING CHANGE: b", True, "fix", False, Bump.PATCH),
    ("no-blank", "fix: a\nBREAKING CHANGE: b", True, "fix", False, Bump.PATCH),
    ("bang-no-blank", "feat!: a\nBREAKING CHANGE: b", True, "feat", True, Bump.MAJOR),
    ("bare", "fix: a\n\nBREAKING CHANGE:", True, "fix", False, Bump.PATCH),
    ("bare-space", "fix: a\n\nBREAKING CHANGE: ", True, "fix", True, Bump.MAJOR),
    ("next-line", "fix: a\n\nBREAKING CHANGE: \n  b", True, "fix", True, Bump.MAJOR),
    ("plural", "fix: a\n\nBREAKING CHANGES: b", True, "fix", False, Bump.PATCH),
    ("indented", "fix: a\n\n  BREAKING CHANGE: b", True, "fix", False, Bump.PATCH),
    ("listed", "fix: a\n\n- BREAKING CHANGE: b", True, "fix", False, Bump.PATCH),
    ("lower", "fix: a\n\nbreaking change: b", True, "fix", False, Bump.PATCH),
    ("in-subject", "fix: BREAKING CHANGE: a", True, "fix", False, Bump.PATCH),
    ("not-cc", "Update README.md", False, None, False, Bump.PATCH),
    ("not-cc-marker", "Update README.md\n\nBREAKING CHANGE: b", False, None, False, Bump.PATCH),
    ("not-cc-feat-body", "Update README.md\n\nfeat: a", False, None, False, Bump.PATCH),
    ("empty", "", False, None, False, Bump.PATCH),
    ("blank-first", "\n\nfeat: a", False, None, False, Bump.PATCH),
    ("mid-cr", "feat: a\rb", False, None, False, Bump.PATCH),
    ("cr-no-lf", "feat: a\r", False, None, False, Bump.PATCH),
]


@pytest.mark.parametrize(
    ("message", "conforming", "commit_type", "breaking", "bump"),
    [row[1:] for row in MESSAGES],
    ids=[row[0] for row in MESSAGES],
)
def test_a_message_is_read_by_conventional_commits(
    message: str, conforming: bool, commit_type: str | None, breaking: bool, bump: Bump
) -> None:
    """What a commit message asks of the version.

    ``in-paragraph`` and ``earlier-token`` differ only in the paragraph before the marker: a
    paragraph that begins with ``Note: `` begins the footers, so everything after it is read
    as footers (items 8 to 10); a paragraph of plain text does not, and the marker in it is
    no footer.
    """
    assert change(message) == Change(conforming, commit_type, breaking, bump)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("feat(api): add retry", Subject(True, "api", "add retry"), id="subject-scope"),
        pytest.param("fix: a\n\nbody", Subject(True, None, "a"), id="subject-no-scope"),
        pytest.param("feat!: b", Subject(True, None, "b"), id="subject-bang"),
        pytest.param("feat(x)!: c  ", Subject(True, "x", "c  "), id="subject-scope-bang-spaces"),
        pytest.param("FIX(Cli): d", Subject(True, "Cli", "d"), id="subject-as-written"),
        pytest.param("fix: e\r\nbody", Subject(True, None, "e"), id="subject-crlf"),
        pytest.param("fix: f\r", Subject(False, None, "fix: f\r"), id="subject-lone-cr"),
        pytest.param("fix:g", Subject(False, None, "fix:g"), id="subject-no-space"),
        pytest.param(
            "Update deps\nbody", Subject(False, None, "Update deps"), id="subject-not-conforming"
        ),
        pytest.param(
            "Update deps\r\nbody",
            Subject(False, None, "Update deps"),
            id="subject-not-conforming-crlf",
        ),
        pytest.param("", Subject(False, None, ""), id="subject-empty"),
    ],
)
def test_the_subject_is_its_scope_and_description(message: str, expected: Subject) -> None:
    """A conforming subject gives its scope and what follows ``: ``; any other, itself."""
    assert subject(message) == expected


@pytest.mark.parametrize(
    ("message", "conforming"),
    [(row[1], row[2]) for row in MESSAGES],
    ids=[row[0] for row in MESSAGES],
)
def test_subject_and_change_read_one_grammar(message: str, conforming: bool) -> None:
    """Every message of section 4: ``subject`` conforms exactly when ``change`` does."""
    assert subject(message).conforming is conforming


# --- 5. the title check and GORA read a subject alike ------------------------------------------

# A title that holds a line break is a message with a body, which the script does not take.
TITLES = [
    title for title in [*CONFORMING, *DEPENDABOT_TITLES, *NOT_CONFORMING] if "\n" not in title
]


@functools.cache
def script_passes(title: str) -> bool:
    """The script's grammar alone: Dependabot's titles are exempt from its length limit, which
    is this repository's rule for subjects on main and no part of their grammar."""
    return check_title(title, author=DEPENDABOT)[0] == 0


@pytest.mark.parametrize("title", TITLES)
def test_gora_reads_a_subject_as_the_title_check_does(title: str) -> None:
    assert change(title).conforming is script_passes(title)


def test_the_corpus_the_comparison_runs_over_keeps_both_outcomes() -> None:
    assert {script_passes(title) for title in TITLES} == {True, False}


# --- 6. highest bump --------------------------------------------------------------------------

PATCH = change("fix: a")
MINOR = change("feat: a")
MAJOR = change("feat!: a")


@pytest.mark.parametrize("changes", [[], iter([])], ids=["empty-list", "empty-iterator"])
def test_a_candidate_follows_at_least_one_change(changes: Any) -> None:
    with pytest.raises(ValueError) as refused:
        bump_of(changes)
    assert str(refused.value) == "a candidate follows at least one change"


def generated(*changes: Change) -> Iterator[Change]:
    yield from changes


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ([PATCH], Bump.PATCH),
        ([PATCH, MINOR], Bump.MINOR),
        ([MINOR, PATCH], Bump.MINOR),
        ([MAJOR, PATCH], Bump.MAJOR),
        ([PATCH, MINOR, MAJOR], Bump.MAJOR),
        (generated(PATCH, MINOR), Bump.MINOR),
    ],
    ids=["patch", "patch-minor", "minor-patch", "major-patch", "all-three", "generator"],
)
def test_the_highest_bump_wins(changes: Any, expected: Bump) -> None:
    assert bump_of(changes) is expected


# --- 7. the next version ----------------------------------------------------------------------

NEXT: list[tuple[str, Bump, bool, str]] = [
    ("1.0.1-rc", Bump.PATCH, False, "1.0.2-rc"),
    ("1.0.1-rc", Bump.MINOR, False, "1.1.0-rc"),
    ("1.0.1-rc", Bump.MAJOR, False, "2.0.0-rc"),
    ("1.2.3-rc", Bump.MINOR, False, "1.3.0-rc"),
    ("1.2.3-rc", Bump.MAJOR, False, "2.0.0-rc"),
    ("0.2.1-rc", Bump.PATCH, False, "0.2.2-rc"),
    ("0.2.1-rc", Bump.MINOR, False, "0.3.0-rc"),
    ("0.2.1-rc", Bump.MAJOR, False, "0.3.0-rc"),
    ("0.2.1-rc", Bump.PATCH, True, "1.0.0-rc"),
    ("0.2.1-rc", Bump.MAJOR, True, "1.0.0-rc"),
    ("1.4.0-rc", Bump.PATCH, True, "1.4.1-rc"),
    ("1.4.0-rc", Bump.MAJOR, True, "2.0.0-rc"),
]
NEXT_IDS = [
    f"{previous}-{bump.name.lower()}-{'stable' if stable else 'unstable'}"
    for previous, bump, stable, _ in NEXT
]
PREVIOUS = sorted({row[0] for row in NEXT})


@pytest.mark.parametrize(("previous", "bump", "stable", "expected"), NEXT, ids=NEXT_IDS)
def test_the_version_of_the_next_candidate(
    previous: str, bump: Bump, stable: bool, expected: str
) -> None:
    assert str(next_version(Version.parse(previous), bump, stable=stable)) == expected


@pytest.mark.parametrize("stable", [False, True], ids=["unstable", "stable"])
@pytest.mark.parametrize("bump", list(Bump), ids=[bump.name.lower() for bump in Bump])
@pytest.mark.parametrize("previous", PREVIOUS)
def test_the_next_version_is_an_rc_version_and_newer(
    previous: str, bump: Bump, stable: bool
) -> None:
    before = Version.parse(previous)
    after = next_version(before, bump, stable=stable)
    assert after.final is False
    assert after > before


@pytest.mark.parametrize("bump", list(Bump), ids=[bump.name.lower() for bump in Bump])
def test_the_previous_candidate_is_an_rc_version(bump: Bump) -> None:
    with pytest.raises(ValueError) as refused:
        next_version(Version.parse("1.0.2"), bump, stable=False)
    assert str(refused.value) == "the previous candidate's version is an rc version"


def test_a_next_version_of_129_characters_is_refused() -> None:
    previous = Version(0, 0, 10**121 - 1, False)
    assert len(str(previous)) == 128
    with pytest.raises(ValueError) as refused:
        next_version(previous, Bump.PATCH, stable=False)
    assert str(refused.value) == RC_VERSION.rule


# --- 8. the map's history ---------------------------------------------------------------------


def test_the_history_of_release_model_section_3_gives_the_versions_of_section_4() -> None:
    """From ``1.0.1-rc``: c11 (fix), c12 (feat), and after the rejected ``1.1.0-rc``, c14 (fix).

    c13 (docs) changed no input, so it is no candidate and is not read here. The rejected
    candidate keeps its number: the next one is computed after it.
    """
    version = Version.parse("1.0.1-rc")
    for message, expected in [
        ("fix: refuse an empty stage", "1.0.2-rc"),
        ("feat(cli): add the rollback command", "1.1.0-rc"),
        ("fix: refuse a rejected digest", "1.1.1-rc"),
    ]:
        version = next_version(version, bump_of([change(message)]), stable=False)
        assert str(version) == expected

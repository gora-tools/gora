"""The change log of a range of an artifact's versions (ADR-027 section 4).

What is held: the commits are grouped as breaking changes, features, fixes and other changes, in
that order, each group in the order the commits are given; a breaking commit is listed only
among the breaking changes, whatever its type; a subject that is not a Conventional Commit is
listed among the other changes as it is; a group with no commit is left out. Each line is
``- ``, the description, the scope in parentheses when there is one, and the first 12
hexadecimal digits of the commit. No line begins with ``#`` and none ends with white space, so
the text is the same after git's cleanup of a message (``test_tag_message_against_git``). A
NUL or a lone surrogate, which no message of a tag can hold, is written as U+FFFD.

Expectations are written out literally, never derived from the code under test. ``c(n)`` is
the object name whose 40 digits are all the hexadecimal digit ``n``.
"""

import pytest

from gora.domain.release.candidates import Commit
from gora.domain.release.changelog import change_log
from gora.domain.release.identifiers import SOURCE_COMMIT


def c(n: int) -> str:
    return f"{n:x}" * 40


def commit(n: int, message: str) -> Commit:
    return Commit(c(n), message, frozenset({b"src/a.py"}))


@pytest.mark.parametrize(
    ("commits", "expected"),
    [
        pytest.param(
            [
                commit(1, "feat(api): add retry\n\nThe body is not listed."),
                commit(2, "fix: handle empty input"),
                commit(3, "refactor!: drop v1 endpoints"),
                commit(4, "Update dependencies"),
                commit(5, "docs: describe the cache"),
            ],
            "Breaking changes:\n"
            "- drop v1 endpoints 333333333333\n"
            "\n"
            "Features:\n"
            "- add retry (api) 111111111111\n"
            "\n"
            "Fixes:\n"
            "- handle empty input 222222222222\n"
            "\n"
            "Other changes:\n"
            "- Update dependencies 444444444444\n"
            "- describe the cache 555555555555\n",
            id="every-group",
        ),
        pytest.param(
            [commit(1, "fix: a"), commit(2, "fix(cli): b")],
            "Fixes:\n- a 111111111111\n- b (cli) 222222222222\n",
            id="empty-groups-left-out",
        ),
        pytest.param(
            [commit(1, "feat!: replace the config format"), commit(2, "feat: add a flag")],
            "Breaking changes:\n"
            "- replace the config format 111111111111\n"
            "\n"
            "Features:\n"
            "- add a flag 222222222222\n",
            id="breaking-feat-only-breaking",
        ),
        pytest.param(
            [commit(1, "fix: rename a field\n\nBREAKING CHANGE: clients must update")],
            "Breaking changes:\n- rename a field 111111111111\n",
            id="breaking-footer",
        ),
        pytest.param(
            [commit(1, "FIX: upper case type"), commit(2, "Feat(Api): mixed case")],
            "Features:\n"
            "- mixed case (Api) 222222222222\n"
            "\n"
            "Fixes:\n"
            "- upper case type 111111111111\n",
            id="type-in-any-case",
        ),
        pytest.param(
            [commit(3, "chore: c"), commit(1, "Merge pull request #7"), commit(2, "build: b")],
            "Other changes:\n"
            "- c 333333333333\n"
            "- Merge pull request #7 111111111111\n"
            "- b 222222222222\n",
            id="order-given",
        ),
        pytest.param(
            [
                commit(1, "fix:no space"),
                commit(2, "fix: control\x1b[0m"),
                commit(3, "Fix the build (#12)\nbody"),
            ],
            "Other changes:\n"
            "- fix:no space 111111111111\n"
            "- fix: control\x1b[0m 222222222222\n"
            "- Fix the build (#12) 333333333333\n",
            id="not-conventional-as-is",
        ),
        pytest.param(
            [commit(1, "fix: #12 crash"), commit(2, "# not a heading")],
            "Fixes:\n- #12 crash 111111111111\n\nOther changes:\n- # not a heading 222222222222\n",
            id="hash-not-at-line-start",
        ),
        pytest.param(
            [commit(1, "fix: two  spaces  "), commit(2, "fix: crlf\r\nbody"), commit(3, "x\r")],
            "Fixes:\n"
            "- two  spaces   111111111111\n"
            "- crlf 222222222222\n"
            "\n"
            "Other changes:\n"
            "- x\r 333333333333\n",
            id="description-as-written",
        ),
        pytest.param(
            [commit(1, ""), commit(2, "\nbody only"), commit(3, "   ")],
            "Other changes:\n- 111111111111\n- 222222222222\n-     333333333333\n",
            id="empty-subject",
        ),
        pytest.param(
            [Commit("ab" * 32, "fix: sha-256 object name", frozenset())],
            "Fixes:\n- sha-256 object name abababababab\n",
            id="sha-256",
        ),
        pytest.param(
            [commit(1, "x\x00y"), commit(2, "fix(\udcff): z\udcff\nbody\x00")],
            "Fixes:\n- z\ufffd (\ufffd) 222222222222\n\nOther changes:\n- x\ufffdy 111111111111\n",
            id="not-text",
        ),
        pytest.param([], "", id="no-commit"),
    ],
)
def test_the_change_log_groups_its_commits(commits: list[Commit], expected: str) -> None:
    assert change_log(commits) == expected


@pytest.mark.parametrize("bad", ["A" * 40, "1" * 39, "1" * 41, "", f"{'1' * 40}\n"])
@pytest.mark.parametrize("at", [0, 1])
def test_a_commit_outside_the_source_commit_grammar_is_a_caller_s_error(bad: str, at: int) -> None:
    commits = [commit(1, "fix: a")]
    commits.insert(at, Commit(bad, "fix: b", frozenset()))
    with pytest.raises(ValueError) as raised:
        change_log(commits)
    assert str(raised.value) == f"source commit {SOURCE_COMMIT.rule}"

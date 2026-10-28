"""Every pull request title is a Conventional Commit (L11, ADR-022 sections 2 and 5), and the
subject it makes on ``main`` is at most 72 bytes.

A squash commit takes the pull request title as its subject and appends `` (#<number>)``, and
GORA computes versions from the subjects on ``main``. ``scripts/gate/pr-title.sh`` checks the
grammar, ``<type>[(<scope>)][!]: <subject>`` with no list of types, and the length of the
subject, counted as bytes. Dependabot's generated titles are exempt from the length by actor,
its login and account type, never by their text. ``.github/workflows/pr-title.yml`` runs it on
every pull request.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate" / "pr-title.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-title.yml"


PERSON = ("zuwarskej", "User")
DEPENDABOT = ("dependabot[bot]", "Bot")


def check(*args: str) -> tuple[int, str]:
    """The validator, run as CI runs it: its exit code and its output."""
    done = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode, done.stdout + done.stderr


def check_title(title: str, number: int = 94, author: tuple[str, str] = PERSON) -> tuple[int, str]:
    return check(title, str(number), *author)


CONFORMING = [
    "fix: refuse an empty stage",
    "feat(cli): add the rollback command",
    "feat!: drop manifest schema v0.1",
    "feat(api)!: rename the status route",
    "build: conform to L9, L11 and the guard",
    "docs(adr): decide the lifecycle (#54)",
    "refactor(domain/release): split the grammar",
    "fix(cli,api): one message for a refusal",
    "Fix: the type is not case sensitive",
    "revert: feat(cli): add the rollback command",
    "fix: non-ASCII text, déjà vu \U0001f600",
]

# Dependabot, as this repository's configuration titles its pull requests: longer than the limit.
DEPENDABOT_TITLES = [
    "build(deps): bump astral-sh/uv from 0.12.22-python3.12-trixie to 0.12.23-python3.12-trixie"
    " in /deployments/docker in the uv group",
    "build(deps): bump the python-packages group across 1 directory with 12 updates",
    "build(deps): bump the python-packages group with 2 updates",
]

NOT_CONFORMING = [
    "Update README.md",
    "Merge pull request #1 from zrjes/feature",
    'Revert "feat(cli): add the rollback command"',
    "fix:no space after the colon",
    "fix:  two spaces after the colon",
    "fix : a space before the colon",
    "fix: ",
    "fix:",
    "fix",
    "",
    ": no type",
    "(cli): no type",
    " fix: a leading space",
    "fi x: a space in the type",
    "f1x: a digit in the type",
    "fix(): an empty scope",
    "fix(cli: an unclosed scope",
    "fix(cli)): a second parenthesis",
    "fix(a b): a space in the scope",
    "fix((cli)): a nested scope",
    "feat!!: twice breaking",
    "feat!(cli): breaking before the scope",
    "fix: a line\nbreak",
    "fix: a carriage\rreturn",
    "fix: a\ttab",
    "fix: an escape \x1b[31m",
]


@pytest.mark.parametrize("title", CONFORMING)
def test_a_conventional_commit_title_passes(title: str) -> None:
    code, output = check_title(title)
    assert code == 0, output


@pytest.mark.parametrize("author", [PERSON, DEPENDABOT], ids=["person", "dependabot"])
@pytest.mark.parametrize("title", NOT_CONFORMING)
def test_any_other_title_fails(title: str, author: tuple[str, str]) -> None:
    code, output = check_title(title, author=author)
    assert code == 1, output
    assert "Conventional Commit" in output


@pytest.mark.parametrize(
    ("title", "number", "length"),
    [
        ("fix: " + "x" * 61, 94, 72),
        ("fix: " + "x" * 62, 94, 73),
        ("fix: " + "x" * 60, 123, 72),
        ("fix: " + "x" * 61, 123, 73),
        ("fix: " + "x" * 62, 9, 72),
        ("fix: " + "é" * 30 + "x", 94, 72),
        ("fix: " + "é" * 30 + "xx", 94, 73),
        ("fix: " + "x" * 40, 94, 51),
    ],
)
def test_the_subject_on_main_is_at_most_72_bytes(title: str, number: int, length: int) -> None:
    """The title with `` (#<number>)``, counted as bytes: a character outside ASCII counts as
    its UTF-8 bytes."""
    code, output = check_title(title, number)
    assert code == (0 if length <= 72 else 1), output
    assert f" {length} bytes" in output


@pytest.mark.parametrize("title", DEPENDABOT_TITLES)
def test_dependabot_s_titles_are_exempt_from_the_length_by_actor(title: str) -> None:
    code, output = check_title(title, 80, DEPENDABOT)
    assert code == 0, output


@pytest.mark.parametrize(
    "author",
    [PERSON, ("dependabot[bot]", "User"), ("dependabot", "Bot"), ("gora-release[bot]", "Bot")],
    ids=["person", "dependabot-login-as-user", "dependabot-without-bot", "another-bot"],
)
@pytest.mark.parametrize(
    "title", [t for t in DEPENDABOT_TITLES if len(t.encode()) + len(" (#80)") > 72]
)
def test_a_title_like_dependabot_s_from_anyone_else_is_held_to_the_limit(
    title: str, author: tuple[str, str]
) -> None:
    code, output = check_title(title, 80, author)
    assert code == 1, output
    assert "over 72" in output


def test_a_short_title_in_dependabot_s_words_passes_by_length_not_by_exemption() -> None:
    title = DEPENDABOT_TITLES[2]
    code, output = check_title(title, 80, PERSON)
    assert code == 0, output
    length = len(title) + len(" (#80)")
    assert f" {length} bytes" in output  # the length check passed it, and said so
    assert "no length limit" not in output  # not the exemption
    code, output = check_title(title, 80, DEPENDABOT)
    assert code == 0, output
    assert "no length limit" in output  # calibration: the exemption has its own words
    assert " bytes" not in output


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("fix: a",),
        ("fix: a", "94", "zuwarskej"),
        ("fix: a", "94", "zuwarskej", "User", "x"),
        ("fix: a", "", "zuwarskej", "User"),
        ("fix: a", "0", "zuwarskej", "User"),
        ("fix: a", "094", "zuwarskej", "User"),
        ("fix: a", "-1", "zuwarskej", "User"),
        ("fix: a", "9x", "zuwarskej", "User"),
    ],
    ids=[
        "none",
        "title-only",
        "three",
        "five",
        "empty",
        "zero",
        "leading-zero",
        "negative",
        "text",
    ],
)
def test_a_wrong_call_is_refused(args: tuple[str, ...]) -> None:
    assert check(*args)[0] == 64


@pytest.mark.parametrize("author", [PERSON, DEPENDABOT], ids=["person", "dependabot"])
@pytest.mark.parametrize(
    "title",
    [
        "::error::forged",
        "fix: ::add-mask::x",
        "fix: x\n::warning::forged",
        "fix: ::error::" + "x" * 60,
    ],
)
def test_the_title_is_never_printed(title: str, author: tuple[str, str]) -> None:
    """A line of output that starts with ``::`` is a workflow command to the runner."""
    _, output = check_title(title, author=author)
    assert "::" not in output


# --- the workflow runs it on every pull request, with the title as data --------------------


def _workflow() -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document


def test_the_workflow_runs_on_every_title_and_head_change() -> None:
    document = _workflow()
    # YAML 1.1 reads the key `on` as true.
    on = document.get("on", document.get(True))
    assert on == {"pull_request": {"types": ["opened", "edited", "reopened", "synchronize"]}}
    assert document["permissions"] == {"contents": "read"}


def test_the_workflow_hands_the_title_to_the_script_as_data() -> None:
    """The title, the number and the author reach the script through the environment, never
    in a script's text; the author is the event's, which a title cannot change."""
    (job,) = _workflow()["jobs"].values()
    assert job["name"] == "pr-title"
    runs = [step for step in job["steps"] if "run" in step]
    assert [step["run"] for step in runs] == [
        'scripts/gate/pr-title.sh "$TITLE" "$NUMBER" "$AUTHOR" "$AUTHOR_TYPE"'
    ]
    assert runs[0]["env"] == {
        "TITLE": "${{ github.event.pull_request.title }}",
        "NUMBER": "${{ github.event.pull_request.number }}",
        "AUTHOR": "${{ github.event.pull_request.user.login }}",
        "AUTHOR_TYPE": "${{ github.event.pull_request.user.type }}",
    }
    assert [step["run"] for step in runs if "${{" in step["run"]] == []

"""Every pull request title is a Conventional Commit (L11, ADR-022 sections 2 and 5).

A squash commit takes the pull request title as its subject, and GORA computes versions from
the subjects on ``main``. ``scripts/gate/pr-title.sh`` checks the grammar and nothing else:
``<type>[(<scope>)][!]: <subject>``. No list of types and no length limit: a Dependabot
title is long and valid. ``.github/workflows/pr-title.yml`` runs it on every pull request.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate" / "pr-title.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-title.yml"


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


CONFORMING = [
    "fix: refuse an empty stage",
    "feat(cli): add the rollback command",
    "feat!: drop manifest schema v0.1",
    "feat(api)!: rename the status route",
    "build: conform to L9, L11 and the release guard",
    "docs(adr): decide the lifecycle on trunk (#54)",
    "refactor(domain/release): split the identifier grammar",
    "fix(cli,api): one message for a refused stage",
    "Fix: the type is not case sensitive",
    "revert: feat(cli): add the rollback command",
    "fix: a subject with non-ASCII text, déjà vu \U0001f600",
    # Dependabot, as this repository's configuration titles its pull requests.
    "build(deps): bump astral-sh/uv from 0.12.22-python3.12-trixie to 0.12.23-python3.12-trixie"
    " in /deployments/docker in the uv group",
    "build(deps): bump the python-packages group across 1 directory with 12 updates",
    "build(deps-dev): bump ruff from 0.16.8 to 0.16.9",
    # No length limit.
    "chore: " + "x" * 500,
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
    code, output = check(title)
    assert code == 0, output


@pytest.mark.parametrize("title", NOT_CONFORMING)
def test_any_other_title_fails(title: str) -> None:
    code, output = check(title)
    assert code == 1, output
    assert "Conventional Commit" in output


@pytest.mark.parametrize("args", [(), ("fix: a", "fix: b")], ids=["none", "two"])
def test_one_title_exactly(args: tuple[str, ...]) -> None:
    assert check(*args)[0] == 64


@pytest.mark.parametrize(
    "title", ["::error::forged", "fix: ::add-mask::x", "fix: x\n::warning::forged"]
)
def test_the_title_is_never_printed(title: str) -> None:
    """A line of output that starts with ``::`` is a workflow command to the runner."""
    _, output = check(title)
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
    """The title reaches the script through the environment, never in a script's text."""
    (job,) = _workflow()["jobs"].values()
    assert job["name"] == "pr-title"
    runs = [step for step in job["steps"] if "run" in step]
    assert [step["run"] for step in runs] == ['scripts/gate/pr-title.sh "$TITLE"']
    assert runs[0]["env"] == {"TITLE": "${{ github.event.pull_request.title }}"}
    assert [step["run"] for step in runs if "${{" in step["run"]] == []

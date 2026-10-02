"""The gate-change check (ADR-0016 rule 7), tested from the workflow file it runs in.

The classifier is the ``classify`` step of ``.github/workflows/gate-change.yml``, a
``shell: python`` step. These tests load that step's source from the file and run it
against path lists and patches, so what is tested is what the base branch runs.
"""

import json
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "gate-change.yml"
LABEL = "gate-change-reviewed"
# Spelled in two parts so this file's own lines never carry a suppression.
NOQA = "# " + "noqa"
TYPE_IGNORE = "type" + ": ignore"


def _workflow() -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _workflow()["jobs"]["gate-change"]["steps"]
    return steps


@pytest.fixture(scope="module")
def check() -> types.ModuleType:
    (step,) = [s for s in _steps() if s.get("id") == "classify"]
    assert step["shell"] == "python"
    module = types.ModuleType("gate_change")
    exec(compile(step["run"], str(WORKFLOW), "exec"), module.__dict__)
    return module


def _file(name: str, patch: str | None = "@@ -1 +1 @@\n-a\n+b", **extra: Any) -> dict[str, Any]:
    return {"filename": name, "patch": patch, "additions": 1, **extra}


GATE_PATHS = [
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/gate-change.yml",
    "Makefile",
    "GNUmakefile",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "tests/conftest.py",
    "tests/unit/conftest.py",
    "deployments/docker/Dockerfile",
    "pytest.ini",
    "src/gora/ruff.toml",
    "setup.cfg",
    "mypy.ini",
]

ORDINARY_PATHS = [
    "src/gora/domain/release/manifest.py",
    "tests/unit/test_manifest.py",
    "tests/audit_fakes.py",
    "docs/adr/0016-a-gate-must-prove-it-can-fail.md",
    "README.md",
    "TESTING.md",
    ".github/CODEOWNERS",
    "deployments/docker/README.md",
    "docs/Makefile.md",
]


@pytest.mark.parametrize("path", GATE_PATHS)
def test_a_gate_defining_path_is_a_gate_change(check: types.ModuleType, path: str) -> None:
    assert check.gate_paths([_file(path)]) == [path]
    passed, summary = check.verdict([_file(path)], set(), LABEL)
    assert passed is False
    assert f"`{path}`" in summary


@pytest.mark.parametrize("path", ORDINARY_PATHS)
def test_an_ordinary_path_is_not(check: types.ModuleType, path: str) -> None:
    assert check.gate_paths([_file(path)]) == []
    assert check.verdict([_file(path)], set(), LABEL) == (
        True,
        "Not a gate change: no gate-defining path, no added suppression.",
    )


def test_a_rename_counts_under_its_old_name(check: types.ModuleType) -> None:
    moved = _file("docs/old-makefile.txt", previous_filename="Makefile", status="renamed")
    assert check.gate_paths([moved]) == ["Makefile"]


def test_the_label_turns_a_gate_change_green_and_says_so(check: types.ModuleType) -> None:
    files = [_file("Makefile"), _file("src/gora/main.py")]
    passed, summary = check.verdict(files, {LABEL, "other"}, LABEL)
    assert passed is True
    assert "`Makefile`" in summary and f"Label `{LABEL}` present" in summary
    assert check.verdict(files, {"other"}, LABEL)[0] is False


@pytest.mark.parametrize("marker", [NOQA, f"{NOQA}: E501", TYPE_IGNORE, f"{TYPE_IGNORE}[arg-type]"])
def test_an_added_suppression_in_any_file_is_a_gate_change(
    check: types.ModuleType, marker: str
) -> None:
    patch = "@@ -10,3 +10,4 @@ def f():\n x = 1\n-y = 2\n+y = 2  #\n+z = f()  " + marker + "\n w"
    files = [_file("src/gora/services/notifier.py", patch)]
    found = check.suppressions(files)
    assert len(found) == 1
    assert found[0].startswith("src/gora/services/notifier.py:12: z = f()")
    assert check.verdict(files, set(), LABEL)[0] is False


def test_a_removed_or_unchanged_suppression_is_not(check: types.ModuleType) -> None:
    patch = f"@@ -1,3 +1,2 @@\n-a = 1  {NOQA}\n b = 2  {TYPE_IGNORE}\n c = 3"
    assert check.suppressions([_file("src/gora/main.py", patch)]) == []


def test_line_numbers_follow_the_hunks(check: types.ModuleType) -> None:
    patch = (
        "@@ -1,2 +1,2 @@\n-a\n+b\n c\n"
        "\\ No newline at end of file\n"
        f"@@ -40,2 +41,3 @@\n d\n-e\n+f\n+g  {NOQA}"
    )
    assert check.suppressions([_file("src/x.py", patch)]) == [f"src/x.py:43: g  {NOQA}"]


def test_added_lines_the_api_did_not_send_are_not_assumed_clean(check: types.ModuleType) -> None:
    big = _file("src/gora/big.py", patch=None, additions=5000)
    binary = _file("docs/diagram.png", patch=None, additions=0)
    assert check.suppressions([big, binary]) == [
        "src/gora/big.py: no patch from the API, added lines unread"
    ]


def test_main_reads_the_files_and_labels_and_sets_the_exit_code(
    check: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files, labels, summary = tmp_path / "f.jsonl", tmp_path / "l.txt", tmp_path / "summary.md"
    files.write_text(json.dumps(_file("uv.lock")) + "\n" + json.dumps(_file("README.md")) + "\n")
    labels.write_text("bug\n")
    for name, value in {"FILES": files, "LABELS": labels, "GITHUB_STEP_SUMMARY": summary}.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("LABEL", LABEL)

    assert check.main() == 1
    assert "`uv.lock`" in summary.read_text() and "README" not in summary.read_text()

    labels.write_text(f"bug\n{LABEL}\n")
    assert check.main() == 0


def test_the_workflow_is_defined_on_the_base_and_runs_nothing_from_the_pr() -> None:
    workflow = _workflow()
    trigger: dict[str, Any] = workflow[True]  # YAML 1.1 reads the bare key `on` as true
    assert list(trigger) == ["pull_request_target"]
    assert trigger["pull_request_target"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "labeled",
        "unlabeled",
    ]
    assert workflow["permissions"] == {"pull-requests": "write", "contents": "read"}
    steps = _steps()
    assert [s for s in steps if "uses" in s] == []  # no checkout, no action at all
    assert "${{" not in "".join(s["run"] for s in steps)  # PR data reaches run via env only


def test_a_push_removes_the_review_label_before_the_check(check: types.ModuleType) -> None:
    steps = _steps()
    first = steps[0]
    assert first["if"] == "github.event.action == 'synchronize'"
    assert "-X DELETE" in first["run"] and "$LABEL" in first["run"]
    assert steps.index(first) < [s.get("id") for s in steps].index("classify")
    assert _workflow()["jobs"]["gate-change"]["env"]["LABEL"] == LABEL

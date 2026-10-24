"""The gate-change check (ADR-0016 rule 7), tested from the workflow file it runs in.

The classifier is the ``classify`` step of ``.github/workflows/gate-change.yml``, a
``shell: python`` step. These tests load that step's source from the file and run it
against path lists, patches and event snapshots, so what is tested is what the base branch
runs. ``gh`` is replaced by a fake: no test reaches GitHub. What the check assumes of pytest, which
files it collects and what each spelling of a skip does, is measured here on the pinned pytest
in a nested session, not recalled.
"""

import json
import tomllib
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "gate-change.yml"
LABEL = "gate-change-reviewed"
# Spelled in two parts so this file's own lines never carry a suppression.
NOQA = "# " + "noqa"
TYPE_IGNORE = "type" + ": ignore"
SHELLCHECK_DISABLE = "# shellcheck" + " disable=SC2086"
HEAD = "a" * 40
NEWER = "b" * 40

NOT_GATE = "no gate files changed"
REVIEWED = f"gate files changed, reviewed (label {LABEL})"
UNREVIEWED = f"gate files changed: review, then add label {LABEL}"
UNCLASSIFIED = "gate-change could not classify the files"


def _workflow() -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _workflow()["jobs"]["classifier"]["steps"]
    return steps


def _classify_step() -> dict[str, Any]:
    (step,) = [s for s in _steps() if s.get("id") == "classify"]
    return step


@pytest.fixture(scope="module")
def check() -> types.ModuleType:
    step = _classify_step()
    assert step["shell"] == "python"
    module = types.ModuleType("gate_change")
    exec(compile(step["run"], str(WORKFLOW), "exec"), module.__dict__)
    return module


def _file(name: str, patch: str | None = "@@ -1 +1 @@\n-a\n+b", **extra: Any) -> dict[str, Any]:
    return {"filename": name, "patch": patch, "additions": 1, **extra}


def _decide(
    check: types.ModuleType,
    files: list[dict[str, Any]] | None,
    *,
    action: str = "opened",
    label: str = "",
    sha: str = HEAD,
    live: str | None = HEAD,
) -> tuple[bool, str, str]:
    decision = check.decide(action, label, sha, live, files)
    return (decision.post, decision.state, decision.description)


# --- which paths and lines make a gate change ---------------------------------------------

GATE_PATHS = [
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/gate-change.yml",
    ".github/actions/build-image/action.yml",
    "Makefile",
    "GNUmakefile",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "tests/conftest.py",
    "tests/unit/conftest.py",
    "deployments/docker/Dockerfile",
    "deployments/docker/gate-images.Dockerfile",
    "deployments/docker/debian.sources",
    "deployments/docker/debian.lock",
    "scripts/gate/run-container.sh",
    "scripts/gate/bounded.sh",
    "pytest.ini",
    "src/gora/ruff.toml",
    "setup.cfg",
    "mypy.ini",
    "pytest.toml",
    ".pytest.toml",
    ".pytest.ini",
    "tests/unit/pytest.toml",
    "uv.toml",
    "deployments/docker/uv.toml",
    ".gitignore",
    "src/gora/.gitignore",
    ".gitattributes",
    "deployments/docker/.gitattributes",
    ".ignore",
    "tests/unit/.ignore",
    ".dockerignore",
    "deployments/docker/Dockerfile.dockerignore",
    "deployments/docker/gate-images.Dockerfile.dockerignore",
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
    "scripts/gate.sh",
    "docs/pytest.toml.md",
    "docs/uv.toml.md",
    "docs/gitignore.md",
    "docs/gitattributes.md",
    "docs/ignore.md",
    "deployments/docker/dockerignore.md",
    "deployments/docker/README.dockerignore.md",
]


@pytest.mark.parametrize("path", GATE_PATHS)
def test_a_gate_defining_path_is_a_gate_change(check: types.ModuleType, path: str) -> None:
    assert check.gate_paths([_file(path)]) == [path]
    assert _decide(check, [_file(path)]) == (True, "failure", UNREVIEWED)
    assert f"`{path}`" in "\n".join(check.details([_file(path)]))


@pytest.mark.parametrize("path", ORDINARY_PATHS)
def test_an_ordinary_path_is_not(check: types.ModuleType, path: str) -> None:
    assert check.gate_paths([_file(path)]) == []
    assert _decide(check, [_file(path)]) == (True, "success", NOT_GATE)


def test_a_rename_counts_under_its_old_name(check: types.ModuleType) -> None:
    moved = _file("docs/old-makefile.txt", previous_filename="Makefile", status="renamed")
    assert check.gate_paths([moved]) == ["Makefile"]


# Spellings each tool accepts as a suppression, measured with ruff 0.17.1 (the version uv.lock
# pins), mypy 2.4.0 and shellcheck 0.11.0; each is split, or a template, so that no line
# here carries one.
# ruff's range and line comments take the same forms for each of their three keywords.
RUFF_COMMENT_FORMS = [
    "# ruff: {}[F401]",
    "#ruff:{}[F401]",
    "# ruff:{}[F401]",
    "#  ruff  :  {}[F401]",
    "#\truff:\t{}[F401]",
    "# ruff: {} [F401]",
    "# ruff: {}[ F401 ]",
    "# ruff: {}[F401] # reason",
    "# ruff: {}[F401] reason",
]
SPELLINGS = [
    "#" + "noqa",
    "# " + "NOQA",
    "#  " + "NoQa: E501",
    "# " + "noqa : F401",
    "# " + "ruff: noqa",
    "#" + "ruff:noqa",
    "# " + "flake8: noqa",
    "# " + "ruff: noqa: F401",
    "#" + "type:ignore",
    "# " + "type:ignore[assignment]",
    "#type" + ": ignore",
    "# " + "mypy: ignore-errors",
    "# " + "mypy:ignore-errors",
    "# " + 'mypy: disable-error-code="assignment"',
    "#shellcheck" + " disable=SC2086",
    "# shellcheck" + "  disable=SC2086",
    "# shellcheck" + "\tdisable=SC2086",
    "# shellcheck" + " disable=all",
    "# shellcheck" + " source=/dev/null disable=SC2086",
    "# " + "fmt: off",
    "# " + "fmt:off",
    "# " + "fmt: skip",
    "# " + "fmt:skip",
    "# " + "yapf: disable",
    *[form.format(word) for form in RUFF_COMMENT_FORMS for word in ("ignore", "disable", "enable")],
    *[f"# ruff: {word}[F401, E501]" for word in ("ignore", "disable")],
    "# " + "isort: skip_file",
    "# " + "isort:skip_file",
    "# " + "ruff: isort: skip_file",
    "# " + "isort: skip",
    "#" + "isort:skip",
    "# " + "isort:skip",
    "#  " + "isort: skip",
    "# " + "isort: skip # reason",
    "# " + "ruff: isort: skip",
    "# " + "isort: off",
    "# " + "ruff: isort: off",
]
# What carries none: a comment that names a tool or the word, and code.
NOT_SUPPRESSIONS = [
    "# the noqa marker is reviewed",
    "# type annotations: ignored by nobody",
    "# shellcheck runs with --norc",
    "# fmt is a module",
    "x = noqa  # a name",
    'disable = "SC2086"',
    "# ruff: ignored by nobody",
    "# ruff: enabled in CI",
    "# ruff: disabler",
    "# ruff format is a command",
    "# isort sorts the imports",
    "# isort: skipped",
    "# isort: skip_files are listed elsewhere",
    "# isort: offline",
]


@pytest.mark.parametrize(
    "marker",
    [NOQA, f"{NOQA}: E501", f"# {TYPE_IGNORE}[arg-type]", SHELLCHECK_DISABLE, *SPELLINGS],
)
def test_an_added_suppression_in_any_file_is_a_gate_change(
    check: types.ModuleType, marker: str
) -> None:
    patch = "@@ -10,3 +10,4 @@ def f():\n x = 1\n-y = 2\n+y = 2  #\n+z = f()  " + marker + "\n w"
    files = [_file("src/gora/services/notifier.py", patch)]
    found = check.suppressions(files)
    assert len(found) == 1
    assert found[0].startswith("src/gora/services/notifier.py:12: z = f()")
    assert _decide(check, files) == (True, "failure", UNREVIEWED)


@pytest.mark.parametrize("text", NOT_SUPPRESSIONS)
def test_a_line_without_a_suppression_is_not(check: types.ModuleType, text: str) -> None:
    patch = "@@ -1 +1,2 @@\n a\n+" + text
    assert check.suppressions([_file("src/gora/main.py", patch)]) == []


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


# --- tests removed or switched off (ADR-0016 rule 7) ---------------------------------------


def _adds(source: str) -> str:
    """The patch of a file that gains ``source``."""
    lines = source.splitlines()
    return f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + line for line in lines)


def _removes(*lines: str) -> str:
    """The patch of an edit that drops ``lines`` starting at old line 10, keeping one."""
    return f"@@ -10,{len(lines) + 1} +10 @@\n" + "\n".join("-" + line for line in lines) + "\n kept"


def _replaces(old: list[str], new: list[str], kept: str = "kept") -> str:
    """The patch of an edit that swaps ``old`` for ``new`` at line 10, keeping one line."""
    return (
        f"@@ -10,{len(old) + 1} +10,{len(new) + 1} @@\n"
        + "\n".join(["-" + line for line in old] + ["+" + line for line in new])
        + f"\n {kept}"
    )


def _gone(path: str) -> dict[str, Any]:
    return _file(path, "@@ -1,2 +0,0 @@\n-import x\n-y = 1", status="removed", additions=0)


def _moved(old: str, new: str) -> dict[str, Any]:
    return _file(new, None, previous_filename=old, status="renamed", additions=0)


def _weakened(check: types.ModuleType, files: list[dict[str, Any]]) -> list[str]:
    found: list[str] = check.weakened_tests(files)
    return found


# The names pytest 9.1.1 collects by default (pyproject.toml configures none): python_files
# `test_*.py` and `*_test.py`, case-sensitive, in every directory but those norecursedirs
# names. Distinct stems, so that a file system that folds case holds them all.
COLLECTED_NAMES = ["test_a.py", "b_test.py", "test_.py", "_test.py"]
NOT_COLLECTED_NAMES = [
    "Test_c.py",
    "d_Test.py",
    "TEST_e.py",
    "test.py",
    "tests.py",
    "testf.py",
    "g_tests.py",
    "test-h.py",
    "i-test.py",
    "test_j.pyi",
    "test_k.PY",
    "test_l.txt",
    "m.test.py",
    "helpers.py",
]
COLLECTED_DIRS = ["unit", "deep/er", "_private", "site-packages", "builds", "distro", "venv2"]
NOT_ENTERED_DIRS = [
    "build",
    "dist",
    "node_modules",
    "venv",
    ".hidden",
    "x.egg",
    "CVS",
    "_darcs",
    "{arch}",
    "unit/build",
]
RENAMED_OUT = [
    ("tests/unit/test_a.py", "docs/test_a.py"),
    ("tests/unit/test_a.py", "src/gora/test_a.py"),
    ("tests/unit/test_a.py", "test_a.py"),
    ("tests/unit/test_a.py", "tests/unit/helpers.py"),
    ("tests/unit/test_a.py", "tests/unit/Test_z.py"),
    ("tests/unit/a_test.py", "tests/unit/a_tests.py"),
    ("tests/unit/test_a.py", "tests/unit/test_a.pyi"),
    ("tests/unit/test_a.py", "tests/unit/test_a.txt"),
    ("tests/unit/test_a.py", "tests/build/test_a.py"),
    ("tests/unit/test_a.py", "tests/unit/node_modules/test_a.py"),
    ("tests/unit/test_a.py", "tests/.hidden/test_a.py"),
    ("tests/unit/test_a.py", "tests/unit/x.egg/test_a.py"),
]
RENAMED_WITHIN = [
    ("tests/unit/test_a.py", "tests/unit/test_b.py"),
    ("tests/unit/test_a.py", "tests/integration/test_a.py"),
    ("tests/unit/test_a.py", "tests/unit/a_test.py"),
    ("tests/unit/test_a.py", "tests/unit/deep/er/test_a.py"),
    ("tests/audit_fakes.py", "tests/fakes.py"),
    ("tests/unit/helpers.py", "tests/unit/utils.py"),
    ("tests/unit/test_a.py", "tests/unit/builds/test_a.py"),
    ("docs/test_a.py", "docs/test_b.py"),
    ("docs/test_a.py", "tests/unit/test_a.py"),
]
# A line that defines what pytest collects, as a patch removes it: a function or method whose
# name starts with `test`, a class whose name starts with `Test` (measured: `test`, `testx`,
# `Testing` are collected, `Test_x`, `x_test`, `_test_x` and `test_lower` are not).
REMOVED_DEFINITIONS = [
    "def test_x():",
    "def test_x(a: int) -> None:",
    "    def test_method(self) -> None:",
    "async def test_y():",
    "    async def test_async_method(self):",
    "class TestFoo:",
    "class TestFoo(Base):",
    "class Testing:",
    "def test():",
    "def testx():",
    "\tdef test_tab():",
    "def   test_spaces():",
]
REMOVED_BUT_NOT_DEFINITIONS = [
    "def helper():",
    "def latest_test():",
    "def attest(x):",
    "async def helper():",
    "class Helper:",
    "class ATest:",
    "    assert x == 1",
    "# def test_x():",
    "x = 'def test_x'",
    "test_x = 1",
]


@pytest.mark.parametrize("path", [f"tests/unit/{n}" for n in COLLECTED_NAMES])
def test_a_removed_test_module_is_a_gate_change(check: types.ModuleType, path: str) -> None:
    assert _weakened(check, [_gone(path)]) == [f"{path}: test module removed"]
    assert _decide(check, [_gone(path)]) == (True, "failure", UNREVIEWED)


@pytest.mark.parametrize(
    "path",
    [f"tests/unit/{n}" for n in NOT_COLLECTED_NAMES]
    + [f"tests/{d}/test_a.py" for d in NOT_ENTERED_DIRS]
    + ["docs/test_manifest.py", "src/gora/test_helpers.py", "tests/audit_fakes.py"],
)
def test_a_removed_file_pytest_does_not_collect_is_not(check: types.ModuleType, path: str) -> None:
    assert _decide(check, [_gone(path)]) == (True, "success", NOT_GATE)


@pytest.mark.parametrize(("old", "new"), RENAMED_OUT, ids=[f"{o}>{n}" for o, n in RENAMED_OUT])
def test_a_test_module_renamed_out_of_collection_is_a_gate_change(
    check: types.ModuleType, old: str, new: str
) -> None:
    moved = _moved(old, new)
    assert _weakened(check, [moved]) == [
        f"{old} -> {new}: test module no longer collected by pytest"
    ]
    assert _decide(check, [moved]) == (True, "failure", UNREVIEWED)


@pytest.mark.parametrize(
    ("old", "new"), RENAMED_WITHIN, ids=[f"{o}>{n}" for o, n in RENAMED_WITHIN]
)
def test_a_test_module_renamed_within_collection_is_not(
    check: types.ModuleType, old: str, new: str
) -> None:
    assert _decide(check, [_moved(old, new)]) == (True, "success", NOT_GATE)


@pytest.mark.parametrize("line", REMOVED_DEFINITIONS)
def test_a_removed_line_that_defines_a_test_is_a_gate_change(
    check: types.ModuleType, line: str
) -> None:
    files = [_file("tests/unit/test_x.py", _removes("    assert 1", line))]
    assert _weakened(check, files) == [
        f"tests/unit/test_x.py:11: test definition removed: {line.strip()}"
    ]
    assert _decide(check, files) == (True, "failure", UNREVIEWED)


@pytest.mark.parametrize("line", REMOVED_BUT_NOT_DEFINITIONS)
def test_a_removed_line_that_defines_no_test_is_not(check: types.ModuleType, line: str) -> None:
    files = [_file("tests/unit/test_x.py", _removes("    assert 1", line))]
    assert _decide(check, files) == (True, "success", NOT_GATE)


# A definition is removed only when its name is gone: the same file gains a line defining it
# again. A test is collected by its name, so a signature edit leaves the collected tests as
# they were (test_pytest_collects_the_same_tests_after_a_signature_edit measures it).
SIGNATURE_EDITS = {
    "parameter added": (["def test_x():"], ["def test_x(tmp_path):"]),
    "reformatted over lines": (["def test_x(a, b):"], ["def test_x(", "    a,", "    b,", "):"]),
    "base class added": (["class TestX:"], ["class TestX(Base):"]),
    "async added": (["def test_x():"], ["async def test_x():"]),
    "async dropped": (["    async def test_x(self):"], ["    def test_x(self):"]),
    "annotated": (["def test_x(a):"], ["def test_x(a: int) -> None:"]),
    "two edited": (
        ["def test_x(a):", "def test_x(b):"],
        ["def test_x(a, c):", "def test_x(b, c):"],
    ),
}
# (removed lines, added lines, the lines flagged: the line numbers and the text of each)
NAME_GONE = {
    "renamed": (["def test_a():"], ["def test_b():"], [(10, "def test_a():")]),
    "class removed, a method of its name added": (
        ["class TestX:"],
        ["    def TestX(self):"],
        [(10, "class TestX:")],
    ),
    "one of two not re-added": (
        ["def test_x():", "def test_x(a):"],
        ["def test_x(b):"],
        [(11, "def test_x(a):")],
    ),
    "both renamed": (
        ["def test_a():", "def test_b():"],
        ["def test_c():"],
        [(10, "def test_a():"), (11, "def test_b():")],
    ),
    "a helper added in its place": (["def test_x():"], ["def helper():"], [(10, "def test_x():")]),
}


@pytest.mark.parametrize(("old", "new"), SIGNATURE_EDITS.values(), ids=list(SIGNATURE_EDITS))
def test_a_test_whose_signature_is_edited_keeps_its_name_and_is_not_a_gate_change(
    check: types.ModuleType, old: list[str], new: list[str]
) -> None:
    files = [_file("tests/unit/test_x.py", _replaces(old, new))]
    assert _weakened(check, files) == []
    assert _decide(check, files) == (True, "success", NOT_GATE)


# What a signature edit does to pytest, before and after, as the module a PR leaves.
SIGNATURE_MODULES = {
    "parameter added": (
        "def test_x():\n    pass\n",
        "def test_x(tmp_path):\n    pass\n",
    ),
    "reformatted over lines": (
        "def test_x(a=1, b=2):\n    pass\n",
        "def test_x(\n    a=1,\n    b=2,\n):\n    pass\n",
    ),
    "base class added": (
        "class Base:\n    pass\n\n\nclass TestX:\n    def test_m(self):\n        pass\n",
        "class Base:\n    pass\n\n\nclass TestX(Base):\n    def test_m(self):\n        pass\n",
    ),
    "async added": (
        "def test_x():\n    pass\n",
        "async def test_x():\n    pass\n",
    ),
}


@pytest.mark.parametrize(
    ("before", "after"), SIGNATURE_MODULES.values(), ids=list(SIGNATURE_MODULES)
)
def test_pytest_collects_the_same_tests_after_a_signature_edit(
    pytester: pytest.Pytester, before: str, after: str
) -> None:
    pytester.makepyfile(test_before=before, test_after=after)
    result = pytester.runpytest("--collect-only", "-q", "-p", "no:cacheprovider")
    ids = [line for line in result.outlines if "::" in line]
    names = {
        module: [i.split("::", 1)[1] for i in ids if i.startswith(module)]
        for module in ("test_before.py", "test_after.py")
    }
    assert names["test_before.py"]
    assert names["test_before.py"] == names["test_after.py"]


def test_a_signature_edit_in_a_renamed_file_is_not_a_gate_change(check: types.ModuleType) -> None:
    patch = _replaces(["def test_x():"], ["def test_x(tmp_path):"])
    moved = _file(
        "tests/unit/test_new.py",
        patch,
        previous_filename="tests/unit/test_old.py",
        status="renamed",
    )
    assert _decide(check, [moved]) == (True, "success", NOT_GATE)


@pytest.mark.parametrize(("old", "new", "flagged"), NAME_GONE.values(), ids=list(NAME_GONE))
def test_a_test_whose_name_is_gone_is_a_gate_change(
    check: types.ModuleType, old: list[str], new: list[str], flagged: list[tuple[int, str]]
) -> None:
    files = [_file("tests/unit/test_x.py", _replaces(old, new))]
    assert _weakened(check, files) == [
        f"tests/unit/test_x.py:{n}: test definition removed: {text}" for n, text in flagged
    ]
    assert _decide(check, files) == (True, "failure", UNREVIEWED)


def test_a_name_defined_elsewhere_in_the_file_is_not_a_line_gained(
    check: types.ModuleType,
) -> None:
    # The other definition is unchanged, a context line of the patch: nothing was gained.
    patch = _replaces(["    def test_x(self):"], [], kept="    def test_x(self, a):")
    files = [_file("tests/unit/test_x.py", patch)]
    assert _weakened(check, files) == [
        "tests/unit/test_x.py:10: test definition removed: def test_x(self):"
    ]


@pytest.mark.parametrize("moved_first", [True, False], ids=["added first", "removed first"])
def test_a_test_moved_to_another_file_is_a_gate_change(
    check: types.ModuleType, moved_first: bool
) -> None:
    out = _file("tests/unit/test_a.py", _removes("    assert 1", "def test_x():"))
    into = _file("tests/unit/test_b.py", _adds("def test_x():\n    assert 1"), status="added")
    files = [into, out] if moved_first else [out, into]
    assert _weakened(check, files) == [
        "tests/unit/test_a.py:11: test definition removed: def test_x():"
    ]
    assert _decide(check, files) == (True, "failure", UNREVIEWED)


@pytest.mark.parametrize("path", ["src/gora/main.py", "tests/fixtures/notes.txt", "docs/guide.md"])
def test_a_removed_definition_outside_the_python_tests_is_not(
    check: types.ModuleType, path: str
) -> None:
    assert _decide(check, [_file(path, _removes("def test_x():"))]) == (True, "success", NOT_GATE)


def test_a_test_added_or_a_body_edited_is_not_a_gate_change(check: types.ModuleType) -> None:
    patch = (
        "@@ -5,4 +5,7 @@ def test_old():\n"
        "     value = compute()\n"
        "-    assert value == 1\n"
        "+    assert value == 2\n"
        "+\n"
        "+\n"
        "+def test_new(tmp_path) -> None:\n"
        "+    assert tmp_path\n"
        " \n"
        " done"
    )
    added = _file("tests/unit/test_new.py", _adds("def test_new():\n    assert 1"), status="added")
    edited = _file("tests/unit/test_x.py", patch)
    assert _decide(check, [added]) == (True, "success", NOT_GATE)
    assert _decide(check, [edited]) == (True, "success", NOT_GATE)


def test_removed_lines_the_api_did_not_send_are_not_assumed_clean(check: types.ModuleType) -> None:
    big = _file("tests/unit/test_big.py", patch=None, additions=0, deletions=800)
    assert _weakened(check, [big]) == [
        "tests/unit/test_big.py: no patch from the API, removed lines unread"
    ]
    assert _decide(check, [big]) == (True, "failure", UNREVIEWED)
    # Not under tests/, nothing removed, or a binary file: nothing was left unread.
    for path, deletions in (("docs/big.md", 800), ("tests/unit/test_big.py", 0)):
        unread = _file(path, patch=None, additions=0, deletions=deletions)
        assert _decide(check, [unread]) == (True, "success", NOT_GATE)


class Spelling(NamedTuple):
    label: str  # the id of its cases
    line: str  # the added line the check must flag
    source: str  # a test module around it, whose test body fails
    outcome: str  # what the pinned pytest does with that module


FAILS = "def test_case():\n    assert False\n"
PYTEST = "import pytest\n"
UNITTEST = "import unittest\n"
TESTCASE = "class T(unittest.TestCase):\n"


def _above(line: str, outcome: str, *, before: str = PYTEST, label: str = "") -> Spelling:
    """``line`` above a failing test: a decorator or a statement of the module."""
    return Spelling(label or line.strip(), line, f"{before}{line}\n{FAILS}", outcome)


def _inside(line: str, outcome: str, *, before: str = PYTEST, label: str = "") -> Spelling:
    """``line`` first in the body of a failing test."""
    source = f"{before}def test_case():\n    {line}\n    assert False\n"
    return Spelling(label or line, line, source, outcome)


def _on_method(line: str, outcome: str) -> Spelling:
    """``line`` above a failing unittest method."""
    source = f"{UNITTEST}{TESTCASE}    {line}\n    def test_case(self):\n        self.fail()\n"
    return Spelling(f"TestCase: {line}", line, source, outcome)


def _in_method(line: str, outcome: str) -> Spelling:
    """``line`` first in the body of a failing unittest method."""
    source = f"{UNITTEST}{TESTCASE}    def test_case(self):\n        {line}\n        self.fail()\n"
    return Spelling(f"TestCase: {line}", line, source, outcome)


# Every spelling the pinned pytest (9.1.1) honours for a test it does not run, runs without
# counting, does not collect, or a run it ends: the line the check must flag, and what pytest
# does with a failing test that carries it ("dropped": no test is collected; "ended": the run
# stops with no report). The last group is wider than the tool, on purpose: a false match asks
# for a review, a miss would not.
PARAM = "@pytest.mark.parametrize('x', [pytest.param(1, marks={})])"
PARAM_TEST = "def test_case(x):\n    assert False\n"
CLASS_TEST = "class TestC:\n{}    def test_case(self):\n        assert False\n"
SWITCH_OFFS = [
    # pytest's markers
    _above("@pytest.mark.skip", "skipped"),
    _above("@pytest.mark.skip(reason='r')", "skipped"),
    _above("@pytest.mark.skipif(True, reason='r')", "skipped"),
    _above("@pytest.mark.skipif('True', reason='r')", "skipped"),
    _above("@pytest.mark.xfail", "xfailed"),
    _above("@pytest.mark.xfail(reason='r')", "xfailed"),
    _above("@pytest.mark.xfail(strict=True)", "xfailed"),
    _above("@pytest.mark.xfail(run=False)", "xfailed"),
    Spelling(
        "param marks=skip",
        PARAM.format("pytest.mark.skip"),
        PYTEST + PARAM.format("pytest.mark.skip") + "\n" + PARAM_TEST,
        "skipped",
    ),
    Spelling(
        "param marks=xfail",
        PARAM.format("[pytest.mark.xfail]"),
        PYTEST + PARAM.format("[pytest.mark.xfail]") + "\n" + PARAM_TEST,
        "xfailed",
    ),
    Spelling(
        "request.applymarker",
        "request.applymarker(pytest.mark.xfail)",
        PYTEST + "def test_case(request):\n    request.applymarker(pytest.mark.xfail)\n"
        "    assert False\n",
        "xfailed",
    ),
    # a module's or a class's pytestmark
    _above("pytestmark = pytest.mark.skip", "skipped"),
    _above("pytestmark = [pytest.mark.skip]", "skipped"),
    _above("pytestmark = pytest.mark.skipif(True, reason='r')", "skipped"),
    _above("pytestmark = pytest.mark.xfail", "xfailed"),
    _above("pytestmark = SKIP", "skipped", before=PYTEST + "SKIP = pytest.mark.skip\n"),
    Spelling(
        "class pytestmark",
        "    pytestmark = pytest.mark.skip",
        PYTEST + CLASS_TEST.format("    pytestmark = pytest.mark.skip\n"),
        "skipped",
    ),
    # pytest's calls
    _inside("pytest.skip('r')", "skipped"),
    _inside("pytest.skip()", "skipped"),
    _inside("raise pytest.skip.Exception('r')", "skipped"),
    _inside("pytest.xfail('r')", "xfailed"),
    _inside("pytest.importorskip('no_such_module')", "skipped"),
    _inside("pytest.exit('r', returncode=0)", "ended"),
    _above("pytest.skip('r', allow_module_level=True)", "skipped", label="skip at module level"),
    _above(
        "pytest.importorskip('no_such_module')", "skipped", label="importorskip at module level"
    ),
    Spelling(
        "skip in a fixture",
        "    pytest.skip('r')",
        PYTEST
        + "@pytest.fixture\ndef f():\n    pytest.skip('r')\n\n\ndef test_case(f):\n    pass\n",
        "skipped",
    ),
    # pytest imported another way
    _above("@mark.skip", "skipped", before="from pytest import mark\n"),
    Spelling(
        "from pytest import mark as m",
        "from pytest import mark as m",
        "from pytest import mark as m\n@m.skip\n" + FAILS,
        "skipped",
    ),
    Spelling(
        "from pytest import skip",
        "from pytest import skip",
        "from pytest import skip\ndef test_case():\n    skip('r')\n    assert False\n",
        "skipped",
    ),
    Spelling(
        "from pytest import xfail",
        "from pytest import xfail",
        "from pytest import xfail\ndef test_case():\n    xfail('r')\n    assert False\n",
        "xfailed",
    ),
    Spelling(
        "from pytest import importorskip",
        "from pytest import importorskip",
        "from pytest import importorskip\ndef test_case():\n"
        "    importorskip('no_such_module')\n    assert False\n",
        "skipped",
    ),
    Spelling(
        "from pytest import exit",
        "from pytest import exit",
        "from pytest import exit\ndef test_case():\n    exit('r', returncode=0)\n"
        "    assert False\n",
        "ended",
    ),
    Spelling(
        "import pytest as pt",
        "import pytest as pt",
        "import pytest as pt\ndef test_case():\n    pt.skip('r')\n    assert False\n",
        "skipped",
    ),
    # a test pytest does not collect
    Spelling("__test__ in a module", "__test__ = False", "__test__ = False\n" + FAILS, "dropped"),
    Spelling(
        "__test__ in a class",
        "    __test__ = False",
        CLASS_TEST.format("    __test__ = False\n"),
        "dropped",
    ),
    Spelling(
        "__test__ on a function",
        "test_case.__test__ = False",
        FAILS + "test_case.__test__ = False\n",
        "dropped",
    ),
    # unittest's, which pytest honours in a TestCase and in a plain function
    _above("@unittest.skip('r')", "skipped", before=UNITTEST, label="function: unittest.skip"),
    _above("@unittest.skipIf(True, 'r')", "skipped", before=UNITTEST, label="function: skipIf"),
    _above(
        "@unittest.skipUnless(False, 'r')", "skipped", before=UNITTEST, label="function: skipUnless"
    ),
    _inside("raise unittest.SkipTest('r')", "skipped", before=UNITTEST, label="function: SkipTest"),
    _above("raise unittest.SkipTest('r')", "skipped", before=UNITTEST, label="module: SkipTest"),
    _on_method("@unittest.skip('r')", "skipped"),
    _on_method("@unittest.skipIf(True, 'r')", "skipped"),
    _on_method("@unittest.skipUnless(False, 'r')", "skipped"),
    _on_method("@unittest.expectedFailure", "xfailed"),
    _in_method("self.skipTest('r')", "skipped"),
    _in_method("raise unittest.SkipTest('r')", "skipped"),
    Spelling(
        "TestCase class: unittest.skip",
        "@unittest.skip('r')",
        UNITTEST
        + "@unittest.skip('r')\n"
        + TESTCASE
        + "    def test_case(self):\n        self.fail()\n",
        "skipped",
    ),
    Spelling(
        "from unittest import skip",
        "from unittest import skip",
        UNITTEST
        + "from unittest import skip\n"
        + TESTCASE
        + "    @skip('r')\n    def test_case(self):\n        self.fail()\n",
        "skipped",
    ),
    Spelling(
        "from unittest import SkipTest",
        "from unittest import SkipTest",
        "from unittest import SkipTest\ndef test_case():\n    raise SkipTest('r')\n"
        "    assert False\n",
        "skipped",
    ),
    # wider than the tool: pytest still runs and fails the test
    _above("@pytest.mark.skipif(False, reason='r')", "failed"),
    Spelling(
        "pytestmark = usefixtures",
        "pytestmark = pytest.mark.usefixtures('f')",
        PYTEST + "@pytest.fixture\ndef f():\n    pass\n\n\n"
        "pytestmark = pytest.mark.usefixtures('f')\n" + FAILS,
        "failed",
    ),
    Spelling(
        "add_marker in a body, too late",
        "request.node.add_marker(pytest.mark.skip)",
        PYTEST + "def test_case(request):\n    request.node.add_marker(pytest.mark.skip)\n"
        "    assert False\n",
        "failed",
    ),
    _above(
        "@unittest.expectedFailure", "failed", before=UNITTEST, label="function: expectedFailure"
    ),
    Spelling(
        "from pytest import fixture as fx",
        "from pytest import fixture as fx",
        "from pytest import fixture as fx\n" + FAILS,
        "failed",
    ),
]
# What a PR adds to a test and that switches nothing off.
NOT_SWITCHED_OFF = [
    "def test_new():",
    "    assert x == 1",
    "@pytest.mark.parametrize('x', [1, 2])",
    "@pytest.fixture",
    "with pytest.raises(ValueError):",
    "pytest.fail('r')",
    "import pytest",
    "import unittest",
    "from unittest import mock",
    "from pytest import fixture, raises",
    "unittest.mock.patch('x')",
    "x = skip_rows(3)",
    "# skip the first row",
    "@pytest.mark.skipped_by_ci",
    "@pytest.mark.Skip",
    "pytestmarks = []",
    "my_pytestmark = 1",
    "if __test__ == 1:",
    "skipping = True",
]


def _outcome(recorded: pytest.HookRecorder) -> str:
    """What the pinned pytest did with the one test module of a run."""
    seen = set()
    for report in recorded.getreports("pytest_collectreport pytest_runtest_logreport"):
        if hasattr(report, "wasxfail"):
            seen.add("xfailed" if report.skipped else "xpassed")
        elif report.outcome != "passed" or getattr(report, "when", None) == "call":
            seen.add(report.outcome)
    for outcome in ("failed", "xfailed", "xpassed", "skipped", "passed"):
        if outcome in seen:
            return outcome
    return "dropped" if recorded.ret == pytest.ExitCode.NO_TESTS_COLLECTED else "ended"


@pytest.mark.parametrize("spelling", SWITCH_OFFS, ids=[s.label for s in SWITCH_OFFS])
def test_an_added_line_with_each_spelling_is_a_gate_change(
    check: types.ModuleType, spelling: Spelling
) -> None:
    files = [_file("tests/unit/test_x.py", _adds(spelling.source))]
    flagged = [entry for entry in _weakened(check, files) if spelling.line.strip() in entry]
    assert flagged
    assert all("test skipped or switched off:" in entry for entry in flagged)
    assert _decide(check, files) == (True, "failure", UNREVIEWED)


@pytest.mark.parametrize("spelling", SWITCH_OFFS, ids=[s.label for s in SWITCH_OFFS])
def test_pytest_does_with_each_spelling_what_the_check_assumes(
    pytester: pytest.Pytester, spelling: Spelling
) -> None:
    pytester.makepyfile(test_case=spelling.source)
    assert _outcome(pytester.inline_run("-p", "no:cacheprovider")) == spelling.outcome


def test_a_run_pytest_ends_exits_green(pytester: pytest.Pytester) -> None:
    exits = next(s for s in SWITCH_OFFS if s.line.startswith("pytest.exit"))
    pytester.makepyfile(test_case=exits.source)
    result = pytester.runpytest("-p", "no:cacheprovider")
    assert result.ret == 0
    assert "failed" not in result.parseoutcomes()


@pytest.mark.parametrize("line", NOT_SWITCHED_OFF)
def test_an_added_line_that_switches_nothing_off_is_not(check: types.ModuleType, line: str) -> None:
    assert _decide(check, [_file("tests/unit/test_x.py", _adds(line))]) == (
        True,
        "success",
        NOT_GATE,
    )


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        ("tests/audit_fakes.py", False),
        ("tests/unit/helpers.py", False),
        ("tests/unit/test_x.py", False),
        ("src/gora/main.py", True),
        ("tests/fixtures/notes.txt", True),
        ("docs/testing.md", True),
    ],
)
def test_a_skip_counts_in_any_python_file_under_tests_and_nowhere_else(
    check: types.ModuleType, path: str, kept: bool
) -> None:
    decision = _decide(check, [_file(path, _adds("@pytest.mark.skip"))])
    assert decision == ((True, "success", NOT_GATE) if kept else (True, "failure", UNREVIEWED))


def test_the_modules_the_check_calls_collected_are_the_ones_pytest_collects(
    check: types.ModuleType, pytester: pytest.Pytester
) -> None:
    paths = {f"tests/unit/{name}" for name in COLLECTED_NAMES + NOT_COLLECTED_NAMES}
    paths |= {f"tests/{d}/test_in.py" for d in COLLECTED_DIRS + NOT_ENTERED_DIRS}
    for pair in RENAMED_OUT + RENAMED_WITHIN:
        paths |= {p for p in pair if p.startswith("tests/")}
    # Distinct names on a file system that folds case too, or the run depends on write order.
    assert len({p.lower() for p in paths}) == len(paths)
    for path in sorted(paths):
        target = pytester.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_case():\n    pass\n", encoding="utf-8")
    result = pytester.runpytest(
        "--collect-only", "-q", "--import-mode=importlib", "-p", "no:cacheprovider", "tests"
    )
    collected = {line.split("::")[0] for line in result.outlines if "::" in line}
    assert collected == {p for p in paths if check.is_test_module(p)}
    # Calibration: the run took some modules and left some, so the comparison can fail.
    assert {"tests/unit/test_a.py", "tests/unit/deep/er/test_a.py"} <= collected
    assert (
        not {"tests/unit/helpers.py", "tests/unit/Test_z.py", "tests/build/test_in.py"} & collected
    )


def test_pytest_is_configured_with_none_of_the_names_the_check_assumes() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = document["tool"]["pytest"]["ini_options"]
    assert options["testpaths"] == ["tests"]
    assert {"python_files", "python_classes", "python_functions", "norecursedirs"}.isdisjoint(
        options
    )


def test_each_reason_is_named_in_the_summary(check: types.ModuleType) -> None:
    files = [
        _gone("tests/unit/test_gone.py"),
        _moved("tests/unit/test_a.py", "docs/test_a.py"),
        _file("tests/unit/test_x.py", _removes("def test_x():")),
        _file("tests/unit/test_y.py", _adds("@pytest.mark.skip")),
        _file("tests/unit/test_big.py", patch=None, additions=0, deletions=800),
    ]
    text = "\n".join(check.details(files))
    assert "Tests removed, skipped or switched off:" in text
    for reason in (
        "tests/unit/test_gone.py: test module removed",
        "tests/unit/test_a.py -> docs/test_a.py: test module no longer collected by pytest",
        "tests/unit/test_x.py:10: test definition removed: def test_x():",
        "tests/unit/test_y.py:1: test skipped or switched off: @pytest.mark.skip",
        "tests/unit/test_big.py: no patch from the API, removed lines unread",
    ):
        assert reason in text


# --- the decision: event x label x gate change -> what is posted ----------------------------

GATE = [_file("Makefile"), _file("src/gora/main.py")]
PLAIN = [_file("README.md")]
SKIPPED = (False, "skipped", "not a gate-change event")

# Every event the workflow is triggered by, with the label the event itself names.
DECISIONS = [
    # (action, event label, files, expected)
    ("opened", "", GATE, (True, "failure", UNREVIEWED)),
    ("reopened", "", GATE, (True, "failure", UNREVIEWED)),
    ("synchronize", "", GATE, (True, "failure", UNREVIEWED)),
    ("labeled", LABEL, GATE, (True, "success", REVIEWED)),
    ("unlabeled", LABEL, GATE, (True, "failure", UNREVIEWED)),
    ("labeled", "bug", GATE, SKIPPED),
    ("unlabeled", "bug", GATE, SKIPPED),
    ("opened", "", PLAIN, (True, "success", NOT_GATE)),
    ("reopened", "", PLAIN, (True, "success", NOT_GATE)),
    ("synchronize", "", PLAIN, (True, "success", NOT_GATE)),
    ("labeled", LABEL, PLAIN, (True, "success", NOT_GATE)),
    ("unlabeled", LABEL, PLAIN, (True, "success", NOT_GATE)),
    ("labeled", "bug", PLAIN, SKIPPED),
    ("unlabeled", "bug", PLAIN, SKIPPED),
]


@pytest.mark.parametrize(
    ("action", "label", "files", "expected"),
    DECISIONS,
    ids=[f"{a}-{lb or 'none'}-{'gate' if f is GATE else 'plain'}" for a, lb, f, _ in DECISIONS],
)
def test_the_decision_table(
    check: types.ModuleType,
    action: str,
    label: str,
    files: list[dict[str, Any]],
    expected: tuple[bool, str, str],
) -> None:
    assert _decide(check, files, action=action, label=label) == expected


def test_only_the_event_that_adds_the_label_approves(check: types.ModuleType) -> None:
    for action in ("opened", "reopened", "synchronize", "unlabeled"):
        # The label named by an event that does not add it approves nothing.
        assert _decide(check, GATE, action=action, label=LABEL)[1] == "failure"
    assert _decide(check, GATE, action="labeled", label="")[1:] == SKIPPED[1:]


def test_another_label_event_needs_no_snapshot_or_files(check: types.ModuleType) -> None:
    assert _decide(check, None, action="labeled", label="bug", sha="", live=None) == SKIPPED


def test_a_stale_snapshot_posts_nothing(check: types.ModuleType) -> None:
    for files in (GATE, PLAIN, None):
        assert _decide(check, files, action="labeled", label=LABEL, live=NEWER) == (
            False,
            "superseded",
            f"superseded by {NEWER}",
        )


@pytest.mark.parametrize(
    "files",
    [None, [{"patch": "@@ -1 +1 @@\n+x"}], [{"filename": None}], ["Makefile"]],
    ids=["unread", "no-filename", "null-filename", "not-an-object"],
)
def test_files_it_cannot_classify_are_an_error(check: types.ModuleType, files: Any) -> None:
    assert _decide(check, files, action="labeled", label=LABEL) == (True, "error", UNCLASSIFIED)


def test_an_unreadable_live_head_is_an_error(check: types.ModuleType) -> None:
    assert _decide(check, PLAIN, live=None) == (True, "error", UNCLASSIFIED)
    assert _decide(check, PLAIN, live="not-a-sha") == (True, "error", UNCLASSIFIED)


@pytest.mark.parametrize("sha", ["", "a" * 39, "A" * 40, HEAD + "\n", "g" * 40, f"{HEAD} x"])
def test_a_payload_head_that_is_not_a_sha_posts_nothing(check: types.ModuleType, sha: str) -> None:
    post, state, _ = _decide(check, PLAIN, sha=sha, live=sha)
    assert (post, state) == (False, "error")


# --- main: the API calls, in order, through a fake gh -----------------------------------------


class FakeGh:
    """Answers gh api calls from a table and records every call."""

    def __init__(
        self,
        *,
        live: str = HEAD,
        files: list[dict[str, Any]] | None = None,
        live_labels: tuple[str, ...] = (),
        fail: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.live, self.live_labels, self.fail = live, live_labels, fail
        self.files = PLAIN if files is None else files

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        endpoint = next(a for a in args if a.startswith("repos/"))
        if self.fail and self.fail in endpoint:
            raise RuntimeError(f"gh api {endpoint} failed: HTTP 502")
        if endpoint.endswith("/labels"):
            return "".join(f"{name}\n" for name in self.live_labels)
        if "/files" in endpoint:
            return "".join(json.dumps(f) + "\n" for f in self.files)
        if "/statuses/" in endpoint or "-X" in args:
            return "{}"
        return self.live + "\n"

    def statuses(self) -> list[dict[str, str]]:
        posted = []
        for args in self.calls:
            if any("/statuses/" in a for a in args):
                fields = [a for a in args if "=" in a and not a.startswith("repos/")]
                posted.append(dict(f.split("=", 1) for f in fields) | {"sha": args[0][-40:]})
        return posted

    def removed_label(self) -> bool:
        return ("-X", "DELETE", f"repos/owner/repo/issues/46/labels/{LABEL}") in self.calls


@pytest.fixture
def run(
    check: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[int, FakeGh, str]]:
    summary = tmp_path / "summary.md"

    def go(fake: FakeGh, *, action: str = "opened", label: str = "") -> tuple[int, FakeGh, str]:
        env = {
            "REPO": "owner/repo",
            "PR": "46",
            "ACTION": action,
            "HEAD_SHA": HEAD,
            "EVENT_LABEL": label,
            "RUN_URL": "https://github.com/owner/repo/actions/runs/1",
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(check, "gh", fake)
        status = check.main()
        return status, fake, summary.read_text(encoding="utf-8")

    return go


def test_main_approves_on_the_label_event_pending_first(
    run: Callable[..., tuple[int, FakeGh, str]],
) -> None:
    fake = FakeGh(files=GATE, live_labels=(LABEL,))
    status, fake, summary = run(fake, action="labeled", label=LABEL)
    assert status == 0
    assert [(s["state"], s["description"], s["sha"]) for s in fake.statuses()] == [
        ("pending", "checking gate files", HEAD),
        ("success", REVIEWED, HEAD),
    ]
    assert all(s["context"] == "gate-change" for s in fake.statuses())
    assert all(s["target_url"].endswith("/actions/runs/1") for s in fake.statuses())
    assert not fake.removed_label()
    assert "`Makefile`" in summary


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
def test_main_removes_a_carried_label_and_fails_a_gate_change(
    run: Callable[..., tuple[int, FakeGh, str]], action: str
) -> None:
    status, fake, summary = run(FakeGh(files=GATE, live_labels=("bug", LABEL)), action=action)
    assert status == 1
    assert fake.removed_label()
    assert fake.statuses()[-1]["state"] == "failure"
    assert f"Removed `{LABEL}`" in summary


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
def test_main_removes_a_carried_label_from_a_plain_change_too(
    run: Callable[..., tuple[int, FakeGh, str]], action: str
) -> None:
    status, fake, _ = run(FakeGh(live_labels=(LABEL,)), action=action)
    assert status == 0
    assert fake.removed_label()
    assert fake.statuses()[-1]["state"] == "success"


def test_main_fails_when_the_label_is_removed(run: Callable[..., tuple[int, FakeGh, str]]) -> None:
    status, fake, _ = run(FakeGh(files=GATE), action="unlabeled", label=LABEL)
    assert status == 1
    assert not fake.removed_label()
    assert fake.statuses()[-1]["state"] == "failure"


@pytest.mark.parametrize("action", ["labeled", "unlabeled"])
def test_main_ignores_another_label_event_entirely(
    run: Callable[..., tuple[int, FakeGh, str]], action: str
) -> None:
    status, fake, summary = run(
        FakeGh(files=GATE, live_labels=(LABEL,)), action=action, label="bug"
    )
    assert status == 0
    assert fake.calls == []  # no API call, no status: the head keeps what it has
    assert "not a gate-change event" in summary


def test_main_posts_nothing_for_a_superseded_head(
    run: Callable[..., tuple[int, FakeGh, str]],
) -> None:
    status, fake, summary = run(FakeGh(live=NEWER, files=GATE), action="labeled", label=LABEL)
    assert status == 0
    assert fake.statuses() == []
    assert f"superseded by {NEWER}" in summary


def test_main_posts_error_when_the_files_cannot_be_read(
    run: Callable[..., tuple[int, FakeGh, str]],
) -> None:
    status, fake, summary = run(FakeGh(fail="/files"))
    assert status == 1
    assert [s["state"] for s in fake.statuses()] == ["pending", "error"]
    assert fake.statuses()[-1]["description"] == UNCLASSIFIED
    assert "HTTP 502" in summary


# --- the workflow itself ---------------------------------------------------------------------


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
    assert workflow["permissions"] == {
        "pull-requests": "write",
        "statuses": "write",
        "contents": "read",
    }
    assert workflow["concurrency"] == {
        "group": "gate-change-${{ github.event.pull_request.number }}"
    }
    steps = _steps()
    assert [s for s in steps if "uses" in s] == []  # no checkout, no action at all
    assert "${{" not in "".join(s["run"] for s in steps)  # event data reaches run via env only


def test_the_snapshot_comes_from_the_event_payload_not_the_label_set() -> None:
    env = _classify_step()["env"]
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert env["EVENT_LABEL"] == "${{ github.event.label.name }}"
    assert env["ACTION"] == "${{ github.event.action }}"
    assert not any("labels" in value for value in env.values())


def _names(document: dict[Any, Any]) -> list[tuple[str, str]]:
    """Every name a workflow gives: its own, each job's check run, each step's."""
    names = [("workflow", str(document.get("name", "")))]
    for job_id, job in document.get("jobs", {}).items():
        # A job's check run is called by its name, or by its id when it has none.
        names += [(f"job {job_id}", str(job_id)), (f"job {job_id}", str(job.get("name", "")))]
        names += [(f"step of {job_id}", str(step.get("name", ""))) for step in job.get("steps", [])]
    return names


def _named_gate_change(document: dict[Any, Any]) -> list[str]:
    return [where for where, name in _names(document) if name.strip().lower() == "gate-change"]


def test_only_the_status_is_called_gate_change() -> None:
    """The required context is the commit status `gate-change` (ADR-0016 rule 7). A check
    run of that name could be green on a head whose status is failure: an event for another
    label posts no status and the job still succeeds."""
    found = {
        path.name: _named_gate_change(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }
    assert "gate-change.yml" in found
    assert {name: where for name, where in found.items() if where} == {}


@pytest.mark.parametrize(
    ("document", "found"),
    [
        ({"name": "gate-change", "jobs": {}}, "workflow"),
        ({"jobs": {"gate-change": {"steps": []}}}, "job gate-change"),
        ({"jobs": {"classifier": {"name": "gate-change", "steps": []}}}, "job classifier"),
        (
            {"jobs": {"classifier": {"name": "x", "steps": [{"name": "gate-change"}]}}},
            "step of classifier",
        ),
    ],
    ids=["document0", "document1", "document2", "document3"],
)
def test_a_context_called_gate_change_is_found(document: dict[Any, Any], found: str) -> None:
    assert _named_gate_change(document) == [found]

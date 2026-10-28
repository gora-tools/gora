"""Calibration of the suite's own guards (ADR-0016 rules 1, 3 and 5; TESTING rule 8).

The guards of the pinned Python, of every test's timeout and of the suite's size run only when
the conftest is this suite's own, ``tests/conftest.py`` under the rootdir, so no pytester
session that copies the conftest elsewhere ever shows them. Each is shown to bite here in a
pytester session laid out as this repository is: the conftest at ``tests/conftest.py``, the
pytest settings of ``pyproject.toml``, ``.python-version`` and, for the suite-size guard, a git
index. A layout that meets every guard runs green, so a red outcome is the guard's.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import git_env

CONFTEST = Path(__file__).with_name("conftest.py")
MINOR = f"{sys.version_info.major}.{sys.version_info.minor}"
OTHER_MINOR = "3.11" if MINOR != "3.11" else "3.13"
SETTINGS = (
    '[tool.pytest.ini_options]\ntestpaths = ["tests"]\ntimeout = 120\ntimeout_method = "signal"\n'
)
PASSING = "def test_passes():\n    assert True\n"


def _suite(
    pytester: pytest.Pytester,
    modules: dict[str, str],
    *,
    settings: str = SETTINGS,
    python: str = MINOR,
    tracked: list[str] | None = None,
) -> None:
    """The layout of this repository: settings, the pinned Python, ``tests/`` with this
    conftest and ``modules``, and a git index holding ``tracked`` (default: every module)."""
    pytester.makepyprojecttoml(settings)
    (pytester.path / ".python-version").write_text(f"{python}\n", encoding="utf-8")
    tests = pytester.path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    for name, source in modules.items():
        (tests / name).write_text(source, encoding="utf-8")
    names = ["tests/conftest.py", *(f"tests/{n}" for n in (tracked or modules))]
    for command in (["git", "init", "-q"], ["git", "add", "--", *names]):
        subprocess.run(command, cwd=pytester.path, env=git_env(), check=True, capture_output=True)


def _run(pytester: pytest.Pytester) -> pytest.RunResult:
    return pytester.runpytest("-p", "no:cacheprovider")


def _decoy_repository(tmp_path: Path) -> Path:
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], env=git_env(), check=True)
    return decoy


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _as_a_git_hook_runs(monkeypatch: pytest.MonkeyPatch, decoy: Path) -> None:
    """The variables a git hook leaves in the environment, pointing at ``decoy``: the ones that
    redirect ``git init``, ``add`` and ``ls-files`` on the git this suite runs (measured: each
    one written to, or read from, the decoy instead of the repository in the working directory)."""
    git_dir = decoy / ".git"
    monkeypatch.setenv("GIT_DIR", str(git_dir))
    monkeypatch.setenv("GIT_COMMON_DIR", str(git_dir))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(git_dir / "hook-index"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(git_dir / "objects"))


def test_a_layout_that_meets_every_guard_runs_green(pytester: pytest.Pytester) -> None:
    _suite(pytester, {"test_a.py": PASSING})
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)


# --- the suite's size (ADR-0016 rule 3) ----------------------------------------------------


def test_a_tracked_module_that_is_not_collected_fails_the_session(
    pytester: pytest.Pytester,
) -> None:
    _suite(pytester, {"test_a.py": PASSING, "check_b.py": PASSING})
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(passed=1, errors=1)
    # fnmatch reads [ and ] as a set of characters: the module's name is matched without them.
    result.stdout.fnmatch_lines(["*not collected (ADR-0016 rule 3): *'tests/check_b.py'*"])


def test_an_untracked_module_is_not_counted(pytester: pytest.Pytester) -> None:
    _suite(pytester, {"test_a.py": PASSING, "check_b.py": PASSING}, tracked=["test_a.py"])
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)


def test_a_deselected_module_still_counts_as_collected(pytester: pytest.Pytester) -> None:
    _suite(pytester, {"test_a.py": PASSING, "test_b.py": PASSING})
    result = pytester.runpytest("-p", "no:cacheprovider", "--deselect", "tests/test_b.py")
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, deselected=1)


def test_a_layout_is_built_in_its_own_repository_under_a_git_hook(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy = _decoy_repository(tmp_path)
    before = _files(decoy)
    _as_a_git_hook_runs(monkeypatch, decoy)
    _suite(pytester, {"test_a.py": PASSING})
    assert _files(decoy) == before
    listed = subprocess.run(
        ["git", "ls-files"], cwd=pytester.path, env=git_env(), capture_output=True, check=True
    )
    assert listed.stdout.decode().split() == ["tests/conftest.py", "tests/test_a.py"]


def test_the_suite_size_guard_reads_its_own_index_under_a_git_hook(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _suite(pytester, {"test_a.py": PASSING, "check_b.py": PASSING})
    decoy = _decoy_repository(tmp_path)
    before = _files(decoy)
    _as_a_git_hook_runs(monkeypatch, decoy)
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*not collected (ADR-0016 rule 3): *'tests/check_b.py'*"])
    assert _files(decoy) == before


# --- the pinned Python (ADR-0016 rule 5) ---------------------------------------------------


def test_another_minor_version_is_refused(pytester: pytest.Pytester) -> None:
    _suite(pytester, {"test_a.py": PASSING}, python=OTHER_MINOR)
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        [f"*Python {MINOR} found, {OTHER_MINOR} pinned in .python-version*"]
    )


# --- every test has a timeout (TESTING rule 8) ---------------------------------------------


@pytest.mark.parametrize(
    "settings",
    [
        SETTINGS.replace("timeout = 120\n", ""),
        SETTINGS.replace("timeout = 120\n", "timeout = 0\n"),
        SETTINGS.replace("timeout = 120\n", "timeout = -1\n"),
        SETTINGS.replace('"signal"', '"thread"'),
        SETTINGS + "timeout_func_only = true\n",
    ],
    ids=["no-timeout", "zero", "negative", "thread-method", "func-only"],
)
def test_a_suite_without_a_timeout_for_every_test_is_refused(
    pytester: pytest.Pytester, settings: str
) -> None:
    _suite(pytester, {"test_a.py": PASSING}, settings=settings)
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*every test has a timeout (TESTING rule 8)*"])


def test_a_test_with_its_own_timeout_is_refused(pytester: pytest.Pytester) -> None:
    marked = "import pytest\n\n\n@pytest.mark.timeout(5)\ndef test_marked():\n    pass\n"
    _suite(pytester, {"test_a.py": marked})
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*a test may not set its own timeout (TESTING rule 8)*"])

"""Calibration of the suite's own guards (ADR-0016 rules 1, 3 and 5; TESTING rule 8).

The guards of the pinned Python, of every test's timeout and of the suite's size run only when
the conftest is this suite's own, ``tests/conftest.py`` under the rootdir, so no pytester
session that copies the conftest elsewhere ever shows them. Each is shown to bite here in a
pytester session laid out as this repository is: the conftest at ``tests/conftest.py``, the
pytest settings of ``pyproject.toml``, ``.python-version`` and, for the suite-size guard, a git
index. A layout that meets every guard runs green, so a red outcome is the guard's.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    names = ["tests/conftest.py", *(f"tests/{n}" for n in (tracked or modules))]
    for command in (["git", "init", "-q"], ["git", "add", "--", *names]):
        subprocess.run(command, cwd=pytester.path, env=env, check=True, capture_output=True)


def _run(pytester: pytest.Pytester) -> pytest.RunResult:
    return pytester.runpytest("-p", "no:cacheprovider")


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

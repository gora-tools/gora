"""``make red-first`` proves a test red the way ADR-016 rule 1 asks, on an exported tree.

``scripts/gate/red-first.sh`` runs named tests on committed HEAD, exported, first as HEAD has
them, then with named files taken from a base commit or a defect patch applied, and passes
only when they are green first and fail after (ADR-016 rule 2). Each test builds a small git
repository of its own and runs the script in it. ``uv`` is replaced by a shim on ``PATH`` that
records its arguments and the state of the export it is called in, and runs the interpreter of
this suite, so no test reaches a network (TESTING rule 6); the real ``uv`` is exercised by the
proof of the pull request that added the script. Every run is held to the same four things,
whatever it decides: the working tree is as it was, nothing of the script's temporary
directory is left, the output names no path of this machine, and its last line is the verdict.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate" / "red-first.sh"

# A path starts at the beginning of a line, after white space, a quote, a parenthesis or `=`.
# A path anywhere else, the script removes only when it knows the path.
ABSOLUTE_PATH = re.compile(r"""(^|[\s"'(=])/\S""", re.MULTILINE)

# Where a failed sync of the fake uv prints a path, each a place the pattern does not look.
IN_A_URL, AFTER_A_COLON, AFTER_A_COMMA = "  file://", "  error:", "  a,"

# Each call is logged, and so is the export it ran in: the content of thing.py and uv.lock,
# and the bytecode files there. A failed sync prints the paths of this machine the way uv
# does, in a URL, after a colon and after a comma, and records each in UV_PRINTED: the
# directories the test names in FAKE_UV_PATHS, and the script's temporary directory as
# mktemp gave it, as pwd writes it and as pwd -P writes it, and the export in it.
UV_SHIM = """\
#!/bin/sh
echo "$*" >> "$UV_LOG"
state="$1 thing=$(sed -n 's/^    return //p' thing.py) $(sed 's/^/lock=/' uv.lock)"
echo "$state pyc=$(find . -name '*.pyc' | wc -l | tr -d ' ')" >> "$UV_STATE"
show() {
  echo "$1" >> "$UV_PRINTED"
  echo "  file://$1"
  echo "  error:$1"
  echo "  a,$1"
}
case "$1" in
  sync)
    if [ "$(grep -c '^sync' "$UV_LOG")" -ge "${FAKE_UV_SYNC_FROM:-1}" ]; then
      rc="${FAKE_UV_SYNC_EXIT:-0}"
      if [ "$rc" -ne 0 ]; then
        echo "${FAKE_UV_PATHS:-}" | while IFS= read -r path; do
          if [ -n "$path" ]; then show "$path"; fi
        done
        for work in "$TMPDIR"/gora-red-first.*; do
          show "$work"
          show "$(cd "$work" && pwd)"
          show "$(cd "$work" && pwd -P)"
          show "$(cd "$work" && pwd -P)/export"
        done
        echo "  built: file:///opt/elsewhere/gora"
      fi
      exit "$rc"
    fi
    exit 0
    ;;
  run)
    if [ -n "${FAKE_UV_RUN_SLEEP:-}" ]; then exec sleep "$FAKE_UV_RUN_SLEEP"; fi
    shift 3
    exec "$PYTHON" -m pytest "$@"
    ;;
esac
exit 64
"""

CONFIG = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
THING_AT_BASE = "def f():\n    return 1\n"
THING_AT_HEAD = "def f():\n    return 2\n"
LOCK_AT_BASE = "base\n"
LOCK_AT_HEAD = "head\n"
HELPER = "VALUE = 3\n"
# Green at HEAD, red with thing.py as at base, with the absolute path of the file it runs from
# in its message.
TEST_THING = "def test_f():\n    from thing import f\n\n    assert f() == 2, __file__\n"
TEST_HELPER = "def test_h():\n    import helper\n\n    assert helper.VALUE == 3\n"
# Green at HEAD, a collection error without helper.py.
TEST_IMPORT = "import helper\n\n\ndef test_i():\n    assert helper.VALUE == 3\n"
# Red at HEAD, with the absolute path of the file it runs from in its message.
TEST_PATH = "def test_p():\n    assert False, __file__\n"
# Red at HEAD, and red with thing.py as at base.
TEST_BOTH = "def test_b():\n    from thing import f\n\n    assert f() == 3\n"
TEST_BROKEN = "import no_such_module_anywhere\n"
TEST_OTHER = "def test_o():\n    assert True\n"


@dataclass
class Repo:
    path: Path
    tmp: Path
    bin: Path
    log: Path
    state: Path
    printed: Path
    link: Path  # a link to tmp, kept outside this test's directory
    home: Path
    home_link: Path  # a link to home, kept outside this test's directory


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": "/usr/bin:/bin",
        **(extra or {}),
    }


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return done.stdout


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "--", ".")
    _git(root, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Repo:
    """BASE (HEAD~1) has the defect; HEAD has the fix, and tests that bite without it."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root, {"pyproject.toml": CONFIG, "thing.py": THING_AT_BASE, "uv.lock": LOCK_AT_BASE})
    _write(root, {"tests/test_other.py": TEST_OTHER})
    _commit(root, "base")
    _write(
        root,
        {
            "thing.py": THING_AT_HEAD,
            "uv.lock": LOCK_AT_HEAD,
            "helper.py": HELPER,
            "tests/test_thing.py": TEST_THING,
            "tests/test_helper.py": TEST_HELPER,
            "tests/test_import.py": TEST_IMPORT,
            "tests/test_path.py": TEST_PATH,
            "tests/test_both.py": TEST_BOTH,
            "tests/test_broken.py": TEST_BROKEN,
        },
    )
    _commit(root, "head")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "uv"
    shim.write_text(UV_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    links = tmp_path_factory.mktemp("links")
    (links / "tmp").symlink_to(tmp)
    (links / "home").symlink_to(home)
    return Repo(
        root,
        tmp,
        bin_dir,
        tmp_path / "uv.log",
        tmp_path / "uv.state",
        tmp_path / "uv.printed",
        links / "tmp",
        home,
        links / "home",
    )


def _script_env(repo: Repo, extra: dict[str, str] | None = None) -> dict[str, str]:
    git = shutil.which("git")
    assert git is not None
    return _env(
        {
            "PATH": f"{repo.bin}:{Path(git).parent}:/usr/bin:/bin",
            "HOME": str(repo.tmp),
            "TMPDIR": str(repo.tmp),
            "PYTHON": sys.executable,
            "UV_LOG": str(repo.log),
            "UV_STATE": str(repo.state),
            "UV_PRINTED": str(repo.printed),
            **(extra or {}),
        }
    )


def _status(repo: Repo) -> str:
    return _git(repo.path, "status", "--short")


def _check_run(repo: Repo, output: str, status_before: str) -> None:
    """What every run holds to, whatever it decides."""
    assert _status(repo) == status_before, "the working tree changed"
    assert list(repo.tmp.iterdir()) == [], "the script's temporary directory was left"
    for private in (
        repo.path.parent,
        repo.tmp,
        repo.link,
        repo.home_link,
        Path(sys.executable).parent,
        ROOT,
    ):
        assert str(private) not in output
    assert "file:///" not in output
    assert ABSOLUTE_PATH.search(output) is None, output


def run(
    repo: Repo, *args: str, env: dict[str, str] | None = None, tests: str = "tests/test_thing.py"
) -> tuple[int, list[str]]:
    """The script, run as ``make red-first`` runs it: its exit code and its output lines."""
    before = _status(repo)
    done = subprocess.run(
        ["bash", str(SCRIPT), *(["--tests", tests] if tests else []), *args],
        cwd=repo.path,
        env=_script_env(repo, env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=90,
    )
    _check_run(repo, done.stdout, before)
    return done.returncode, done.stdout.splitlines()


def _uv_calls(repo: Repo) -> list[str]:
    return repo.log.read_text(encoding="utf-8").splitlines() if repo.log.exists() else []


def _uv_states(repo: Repo) -> list[str]:
    """The export as each uv call found it, one line per call, in order."""
    return repo.state.read_text(encoding="utf-8").splitlines() if repo.state.exists() else []


def _revert(*paths: str) -> list[str]:
    return ["--base", "HEAD~1", "--files", " ".join(paths)]


def _patch(repo: Repo, *paths: str) -> Path:
    """A diff from HEAD to the base commit, kept outside the repository: its tree stays clean."""
    target = repo.path.parent / "defect.patch"
    target.write_text(_git(repo.path, "diff", "HEAD", "HEAD~1", "--", *paths), encoding="utf-8")
    return target


# --- red: the proof ------------------------------------------------------------------------


def test_a_test_red_on_the_base_files_is_a_proof(repo: Repo) -> None:
    code, lines = run(repo, *_revert("thing.py"))
    assert code == 0
    assert lines[-2:] == ["tests/test_thing.py::test_f", "red-first: 1 failed"]
    assert "red-first: baseline green on HEAD" in lines


def test_a_path_the_base_lacks_is_removed(repo: Repo) -> None:
    code, lines = run(repo, *_revert("helper.py"), tests="tests/test_helper.py")
    assert code == 0
    assert "red-first: helper.py removed, absent at HEAD~1" in lines
    assert lines[-2:] == ["tests/test_helper.py::test_h", "red-first: 1 failed"]


def test_every_failed_test_is_printed_one_per_line(repo: Repo) -> None:
    code, lines = run(
        repo,
        *_revert("thing.py", "helper.py"),
        tests="tests/test_thing.py tests/test_helper.py",
    )
    assert code == 0
    assert sorted(lines[-3:-1]) == ["tests/test_helper.py::test_h", "tests/test_thing.py::test_f"]
    assert lines[-1] == "red-first: 2 failed"


def test_a_defect_patch_is_a_proof(repo: Repo) -> None:
    patch = _patch(repo, "thing.py")
    code, lines = run(repo, "--patch", str(patch))
    assert code == 0
    assert lines[-2:] == ["tests/test_thing.py::test_f", "red-first: 1 failed"]
    assert "red-first: patch applied" in lines


def test_a_relative_patch_is_read_from_the_repository_root(repo: Repo) -> None:
    _patch(repo, "thing.py")
    code, lines = run(repo, "--patch", "../defect.patch")
    assert code == 0
    assert lines[-1] == "red-first: 1 failed"


def test_files_and_a_patch_combine(repo: Repo) -> None:
    patch = _patch(repo, "helper.py")
    code, lines = run(
        repo,
        *_revert("thing.py"),
        "--patch",
        str(patch),
        tests="tests/test_thing.py tests/test_helper.py",
    )
    assert code == 0
    assert lines[-1] == "red-first: 2 failed"


def test_pytests_messages_are_not_shown(repo: Repo) -> None:
    """A failing test's message holds the path of the export; only its id is shown."""
    code, lines = run(repo, *_revert("thing.py"))
    assert code == 0
    assert lines[-2:] == ["tests/test_thing.py::test_f", "red-first: 1 failed"]
    assert not any("AssertionError" in line or "gora-red-first" in line for line in lines)


def test_the_uv_calls_are_the_gates(repo: Repo) -> None:
    """A baseline, then the same sync and the same tests with the change applied."""
    run(repo, *_revert("thing.py"))
    sync, baseline, sync_again, test = _uv_calls(repo)
    want = "sync --locked --all-extras --group test --group build"
    assert sync == sync_again == f"{want} --no-build-isolation-package gora"
    assert baseline == test
    assert test.startswith("run --no-sync pytest -q --no-header -p no:cacheprovider ")
    assert test.endswith(" tests/test_thing.py")


# --- the baseline: green on HEAD before anything is applied --------------------------------


def test_the_baseline_runs_on_head_before_any_change_is_applied(repo: Repo) -> None:
    patch = _patch(repo, "helper.py")
    code, lines = run(
        repo,
        *_revert("thing.py"),
        "--patch",
        str(patch),
        tests="tests/test_thing.py tests/test_helper.py",
    )
    assert code == 0
    baseline = lines.index("red-first: baseline green on HEAD")
    applied = [
        i for i, line in enumerate(lines) if "as at HEAD~1" in line or "patch applied" in line
    ]
    assert len(applied) == 2
    assert baseline < min(applied)
    assert [state.split()[0] for state in _uv_states(repo)] == ["sync", "run", "sync", "run"]
    assert _uv_states(repo)[:2] == [
        "sync thing=2 lock=head pyc=0",
        "run thing=2 lock=head pyc=0",
    ]


def test_a_change_that_touches_the_lock_is_synced_before_the_second_run(repo: Repo) -> None:
    code, lines = run(repo, *_revert("thing.py", "uv.lock"))
    assert code == 0
    assert lines[-1] == "red-first: 1 failed"
    assert _uv_states(repo) == [
        "sync thing=2 lock=head pyc=0",
        "run thing=2 lock=head pyc=0",
        "sync thing=1 lock=base pyc=0",
        "run thing=1 lock=base pyc=0",
    ]


def test_the_baseline_leaves_no_bytecode_for_the_second_run(repo: Repo) -> None:
    """A rewritten source of the same size in the same second would keep its bytecode."""
    run(repo, *_revert("thing.py"))
    assert [state.rsplit(" ", 1)[1] for state in _uv_states(repo)] == ["pyc=0"] * 4


# --- green, and every other outcome that is not a proof ------------------------------------


def test_a_test_that_passes_is_not_a_proof(repo: Repo) -> None:
    code, lines = run(repo, "--base", "HEAD~1", "--files", "")
    assert code == 1
    assert lines[-1] == "red-first: GREEN — nothing failed"
    assert not any(line.startswith(("tests/", "baseline: ")) for line in lines)


def test_a_test_red_on_head_is_not_a_proof_whatever_the_base_files_do(repo: Repo) -> None:
    """It fails with the files of the base too, so it would "fail" for any change."""
    code, lines = run(repo, *_revert("thing.py"), tests="tests/test_both.py")
    assert code == 2
    assert lines[-2:] == [
        "baseline: tests/test_both.py::test_b",
        "red-first: ERROR (baseline is not green on HEAD)",
    ]
    assert not any("as at" in line or "patch applied" in line for line in lines)
    assert [state.split()[0] for state in _uv_states(repo)] == ["sync", "run"]
    assert _uv_states(repo) == ["sync thing=2 lock=head pyc=0", "run thing=2 lock=head pyc=0"]


def test_a_test_red_on_head_is_not_a_proof_whatever_the_patch_does(repo: Repo) -> None:
    patch = _patch(repo, "thing.py")
    code, lines = run(repo, "--patch", str(patch), tests="tests/test_both.py")
    assert code == 2
    assert lines[-1] == "red-first: ERROR (baseline is not green on HEAD)"
    assert "red-first: patch applied" not in lines
    assert len(_uv_calls(repo)) == 2


def test_every_test_red_on_head_is_printed_one_per_line(repo: Repo) -> None:
    code, lines = run(
        repo,
        *_revert("thing.py"),
        tests="tests/test_both.py tests/test_path.py tests/test_thing.py",
    )
    assert code == 2
    assert sorted(lines[-3:-1]) == [
        "baseline: tests/test_both.py::test_b",
        "baseline: tests/test_path.py::test_p",
    ]
    assert lines[-1] == "red-first: ERROR (baseline is not green on HEAD)"


def test_a_baseline_collection_error_is_an_error(repo: Repo) -> None:
    code, lines = run(repo, *_revert("thing.py"), tests="tests/test_broken.py")
    assert code == 2
    assert lines[-2:] == [
        "baseline: tests/test_broken.py",
        "red-first: ERROR (baseline is not green on HEAD)",
    ]
    assert len(_uv_calls(repo)) == 2


def test_a_baseline_that_finds_no_test_is_an_error(repo: Repo) -> None:
    code, lines = run(repo, "--base", "HEAD~1", "--files", "", tests="tests/test_nope.py")
    assert code == 2
    assert lines[-1] == "red-first: ERROR (baseline is not green on HEAD)"
    assert not any(line.startswith("baseline: ") for line in lines)


def test_a_baseline_message_is_not_shown(repo: Repo) -> None:
    """The failed test's message holds the path of the export; only its id is shown."""
    code, lines = run(repo, "--base", "HEAD~1", "--files", "", tests="tests/test_path.py")
    assert code == 2
    assert lines[-2:] == [
        "baseline: tests/test_path.py::test_p",
        "red-first: ERROR (baseline is not green on HEAD)",
    ]
    assert not any("AssertionError" in line or "gora-red-first" in line for line in lines)


def test_a_collection_error_with_the_change_is_not_a_kill(repo: Repo) -> None:
    """Green on HEAD, it cannot be collected without helper.py: an error, not a failure."""
    code, lines = run(repo, *_revert("helper.py"), tests="tests/test_import.py")
    assert code == 2
    assert lines[-2:] == ["error: tests/test_import.py", "red-first: ERROR (pytest exit 2)"]
    assert "red-first: baseline green on HEAD" in lines


def test_a_test_that_the_change_removes_is_not_a_kill(repo: Repo) -> None:
    code, lines = run(repo, *_revert("tests/test_thing.py"))
    assert code == 2
    assert lines[-1] == "red-first: ERROR (pytest exit 4)"
    assert "red-first: baseline green on HEAD" in lines


def test_a_patch_that_does_not_apply_is_an_error(repo: Repo) -> None:
    patch = repo.path.parent / "bad.patch"
    patch.write_text("--- a/nothing.py\n+++ b/nothing.py\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
    code, lines = run(repo, "--patch", str(patch))
    assert code == 2
    assert lines[-1].startswith("red-first: ERROR (the patch does not apply")
    assert len(_uv_calls(repo)) == 2, "the baseline ran, and nothing after it"


def test_a_failed_sync_is_an_error(repo: Repo) -> None:
    code, lines = run(repo, *_revert("thing.py"), env={"FAKE_UV_SYNC_EXIT": "3"})
    assert code == 2
    assert lines[-1] == "red-first: ERROR (uv sync exit 3)"
    assert len(_uv_calls(repo)) == 1


def test_a_failed_second_sync_is_an_error(repo: Repo) -> None:
    code, lines = run(
        repo,
        *_revert("thing.py", "uv.lock"),
        env={"FAKE_UV_SYNC_EXIT": "3", "FAKE_UV_SYNC_FROM": "2"},
    )
    assert code == 2
    assert lines[-1] == "red-first: ERROR (uv sync exit 3)"
    assert [call.split()[0] for call in _uv_calls(repo)] == ["sync", "run", "sync"]


def _spellings(*dirs: Path) -> str:
    """Each directory as written and as resolved, one per line: the paths a failed uv prints."""
    return "\n".join(dict.fromkeys(p for d in dirs for p in (str(d), str(d.resolve()))))


def _assert_no_path_shown(repo: Repo, lines: list[str]) -> None:
    """Each path the fake uv printed is gone, replaced in each of the three places it was in."""
    printed = repo.printed.read_text(encoding="utf-8").splitlines()
    assert len(printed) >= 6, "the fake uv printed too few paths to prove anything"
    text = "\n".join(lines)
    for path in printed:
        assert path not in text
    for lead in (IN_A_URL, AFTER_A_COLON, AFTER_A_COMMA):
        shown = [line for line in lines if line.startswith(lead)]
        # The export is inside the temporary directory, which goes first: only the rest stays.
        want = [lead + ("<path>/export" if p.endswith("/export") else "<path>") for p in printed]
        assert sorted(shown) == sorted(want)


def test_a_failed_sync_names_no_path_of_this_machine_in_any_spelling(repo: Repo) -> None:
    """TMPDIR is a link and ends in a slash, as on macOS: logical, physical and as given differ."""
    assert repo.link.resolve() == repo.tmp != repo.link
    assert repo.home_link.resolve() == repo.home != repo.home_link
    env = {
        "TMPDIR": f"{repo.link}/",
        "HOME": str(repo.home_link),
        "FAKE_UV_SYNC_EXIT": "3",
        "FAKE_UV_PATHS": _spellings(repo.link, repo.home_link, repo.path),
    }
    code, lines = run(repo, *_revert("thing.py"), env=env)
    assert code == 2
    assert lines[-1] == "red-first: ERROR (uv sync exit 3)"
    _assert_no_path_shown(repo, lines)


def test_a_directory_name_is_a_string_and_not_a_pattern(repo: Repo) -> None:
    """A dot, a plus, a star and a bracket in TMPDIR mean themselves."""
    odd = repo.path.parent / "t.+*[x"
    odd.mkdir()
    repo.tmp = odd
    env = {"FAKE_UV_SYNC_EXIT": "3", "FAKE_UV_PATHS": _spellings(odd, repo.path)}
    code, lines = run(repo, *_revert("thing.py"), env=env)
    assert code == 2
    assert lines[-1] == "red-first: ERROR (uv sync exit 3)"
    _assert_no_path_shown(repo, lines)


def test_a_url_to_a_path_the_script_does_not_know_is_replaced(repo: Repo) -> None:
    """The pattern takes a path right after file://, as it takes one after a space."""
    code, lines = run(repo, *_revert("thing.py"), env={"FAKE_UV_SYNC_EXIT": "3"})
    assert code == 2
    assert "  built: file://<path>" in lines


# --- the tree is read, never touched --------------------------------------------------------


@pytest.mark.parametrize("dirt", ["untracked.txt", "thing.py"])
def test_a_dirty_tree_is_refused(repo: Repo, dirt: str) -> None:
    (repo.path / dirt).write_text("dirt\n", encoding="utf-8")
    code, lines = run(repo, *_revert("thing.py"))
    assert code == 2
    assert lines[-1] == "red-first: ERROR (working tree is dirty)"
    assert _uv_calls(repo) == []


# --- a wrong call ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "tests"),
    [
        ([], "tests/test_thing.py"),
        (["--files", "thing.py"], "tests/test_thing.py"),
        (["--base", "HEAD~1"], ""),
        (["--base", "HEAD~1"], "   "),
        (["--base", "no-such-revision", "--files", "thing.py"], "tests/test_thing.py"),
        (["--base", "HEAD~1", "--files", "no_such_file.py"], "tests/test_thing.py"),
        (["--base", "HEAD~1", "--files", "../thing.py"], "tests/test_thing.py"),
        (["--base", "HEAD~1", "--files", "/etc/hosts"], "tests/test_thing.py"),
        (["--base", "HEAD~1", "--files", "tests"], "tests/test_thing.py"),
        (["--patch", "no-such.patch"], "tests/test_thing.py"),
        (["--no-such-flag", "x"], "tests/test_thing.py"),
    ],
    ids=[
        "no-change",
        "files-without-base",
        "no-tests",
        "blank-tests",
        "unknown-base",
        "path-in-neither",
        "path-outside",
        "path-absolute",
        "path-a-directory",
        "patch-missing",
        "unknown-flag",
    ],
)
def test_a_wrong_call_is_refused(repo: Repo, args: list[str], tests: str) -> None:
    code, lines = run(repo, *args, tests=tests)
    assert code == 64
    assert lines[-1] != "red-first: 0 failed"
    assert _uv_calls(repo) == []


# --- it ends -------------------------------------------------------------------------------


def test_a_term_ends_the_run_and_leaves_nothing(repo: Repo) -> None:
    """The temporary directory is removed on TERM too, with the tests still running."""
    before = _status(repo)
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), "--tests", "tests/test_thing.py", *_revert("thing.py")],
        cwd=repo.path,
        env=_script_env(repo, {"FAKE_UV_RUN_SLEEP": "60"}),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not any(call.startswith("run ") for call in _uv_calls(repo)):
            assert time.monotonic() < deadline, "the tests never started"
            time.sleep(0.05)
        assert list(repo.tmp.iterdir()) != []
        proc.send_signal(signal.SIGTERM)
        output, _ = proc.communicate(timeout=30)
    finally:
        proc.kill()
        proc.wait()
    assert proc.returncode == 143
    _check_run(repo, output, before)

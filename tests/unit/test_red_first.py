"""``make red-first`` proves a test red the way ADR-016 rule 1 asks, on an exported tree.

``scripts/gate/red-first.sh`` runs named tests on committed HEAD, exported, first as HEAD has
them, then with named files taken from a base commit or a defect patch applied, and passes
only when they are green first and fail after (ADR-016 rule 2). Each test builds a small git
repository of its own and runs the script in it. ``uv`` is replaced by a shim on ``PATH`` that
records its arguments and the state of the export it is called in, and runs the interpreter of
this suite, so no test reaches a network (TESTING rule 6); the real ``uv`` is exercised by the
proof of the pull request that added the script. Every run is held to the same four things,
whatever it decides: the working tree is as it was, nothing of the script's temporary
directory is left, the output holds none of the directories the test knows and no absolute
path its pattern can see, and its last line is the verdict.

The second mode, ``--check docker-smoke``, runs the export's own ``make docker-smoke``. The
fixture's Makefile has a fake of that target: ``docker`` is a shim on ``PATH`` that records its
calls and keeps an image as a file, the target "builds" the image it is given by tag, records
what it was given and the export it ran in, prints ``smoke.out`` and exits with ``smoke.exit``,
two files of the export that the change under proof rewrites. No test reaches Docker.
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

# docker: each call is logged. An image is a file of DOCKER_FAKE_DIR, made by the fake build.
DOCKER_SHIM = """\
#!/bin/sh
echo "$*" >> "$DOCKER_FAKE_DIR/docker.log"
case "$1 $2" in
  "fake-build "*)
    if [ -z "${DOCKER_FAKE_NO_BUILD:-}" ]; then : > "$DOCKER_FAKE_DIR/image.$2"; fi
    exit 0
    ;;
  "image inspect")
    test -e "$DOCKER_FAKE_DIR/image.$3"
    exit $?
    ;;
  "image rm")
    if [ -e "$DOCKER_FAKE_DIR/image.$3" ]; then
      rm "$DOCKER_FAKE_DIR/image.$3"
      echo "Untagged: $3"
      exit 0
    fi
    echo "Error: No such image: $3" >&2
    exit 1
    ;;
esac
exit 64
"""

# What `make docker-smoke` is in the fixture: it builds the image it is given, records the
# arguments it got and the export it ran in (call.N), its environment (env.N), and prints
# smoke.out, where @PWD@ is the export's directory.
SMOKE_MAKEFILE = (
    "IMAGE ?= unset\n\n"
    "docker-smoke:\n"
    "\t@docker fake-build $(IMAGE)\n"
    '\t@exec sh ./smoke.sh "$(IMAGE)" "$(PLATFORM)" "$(REVISION)" "$(LINUX_IMAGE)" "$(MAKEFLAGS)"\n'
)
SMOKE_SH = """\
n=$(( $(ls "$DOCKER_FAKE_DIR" | grep -c '^call[.]') + 1 ))
{
  echo "image=$1"
  echo "platform=$2"
  echo "revision=$3"
  echo "linux=$4"
  echo "flags=$5"
  echo "tree=$(git rev-parse 'HEAD^{tree}')"
  echo "commits=$(git rev-list --count HEAD)"
  echo "author=$(git log -1 --format='%an <%ae>')"
  echo "committer=$(git log -1 --format='%cn <%ce>')"
  echo "dirty=$(git status --porcelain | wc -l | tr -d ' ')"
} > "$DOCKER_FAKE_DIR/call.$n"
env > "$DOCKER_FAKE_DIR/env.$n"
if [ -n "${DOCKER_FAKE_SLEEP:-}" ]; then
  sleep "$DOCKER_FAKE_SLEEP" &
  pid=$!
  trap 'kill "$pid"; exit 143' TERM
  wait "$pid"
fi
sed "s#@PWD@#$(pwd)#g" smoke.out
exit "$(cat smoke.exit)"
"""
# Red at the base (exit 3, the defect), green at HEAD. Only the red output holds KILL.
KILL = "licenses=MIT, not Apache-2.0"
SMOKE_OUT_AT_BASE = (
    "image-smoke: label org.opencontainers.image.licenses=MIT, expecting Apache-2.0\n"
    f"image-smoke: the image is labelled {KILL} (built in @PWD@/context)\n"
)
SMOKE_OUT_AT_HEAD = (
    "image-smoke: label org.opencontainers.image.licenses=Apache-2.0, expecting Apache-2.0\n"
    "image-smoke: cwd=@PWD@\n"
)
FIXED_IDENTITY = "red-first <red-first@example.invalid>"

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
# A conftest whose fixture fails every test's setup: an error, and no test fails.
FIXTURE_ERROR_PATCH = """\
diff --git a/tests/conftest.py b/tests/conftest.py
new file mode 100644
--- /dev/null
+++ b/tests/conftest.py
@@ -0,0 +1,6 @@
+import pytest
+
+
+@pytest.fixture(autouse=True)
+def broken():
+    raise RuntimeError("a fixture that fails")
"""


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
    fake: Path  # where the docker shim and the fake docker-smoke keep their state


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
    _write(
        root,
        {
            "Makefile": SMOKE_MAKEFILE,
            "smoke.sh": SMOKE_SH,
            "smoke.exit": "3\n",
            "smoke.out": SMOKE_OUT_AT_BASE,
        },
    )
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
            "smoke.exit": "0\n",
            "smoke.out": SMOKE_OUT_AT_HEAD,
        },
    )
    _commit(root, "head")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "uv"
    shim.write_text(UV_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_SHIM, encoding="utf-8")
    docker.chmod(0o755)
    fake = tmp_path / "fake"
    fake.mkdir()
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
        fake,
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
            "DOCKER_FAKE_DIR": str(repo.fake),
            **(extra or {}),
        }
    )


def _status(repo: Repo) -> str:
    return _git(repo.path, "status", "--short")


def _check_run(repo: Repo, output: str, status_before: str) -> None:
    """What every run holds to, whatever it decides."""
    assert _status(repo) == status_before, "the working tree changed"
    assert list(repo.tmp.iterdir()) == [], "the script's temporary directory was left"
    assert sorted(p.name for p in repo.fake.glob("image.*")) == [], "the run's image was left"
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


def test_an_error_and_no_failed_test_is_not_a_kill(repo: Repo) -> None:
    """Green on HEAD, its fixture fails with the change: pytest exits 1 with an error and no
    failed test, and an error is no kill."""
    patch = repo.path.parent / "fixture.patch"
    patch.write_text(FIXTURE_ERROR_PATCH, encoding="utf-8")
    code, lines = run(repo, "--patch", str(patch))
    assert code == 2
    assert lines[-2:] == [
        "error: tests/test_thing.py::test_f",
        "red-first: ERROR (pytest exit 1, an error and no failed test)",
    ]
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


# --- the check: the export's own docker-smoke ----------------------------------------------

DEFECT = ["--base", "HEAD~1", "--files", "smoke.exit smoke.out"]
SMOKE_RED = f"image-smoke: the image is labelled {KILL} (built in <path>/export/context)"


def check(
    repo: Repo, *args: str, expect: str = KILL, env: dict[str, str] | None = None
) -> tuple[int, list[str]]:
    """``--check docker-smoke`` with the text it must print, run as ``run`` runs the tests."""
    return run(repo, "--check", "docker-smoke", "--expect", expect, *args, env=env, tests="")


def _calls(repo: Repo) -> list[dict[str, str]]:
    """What the fake docker-smoke was given and the export it ran in, one entry per run."""
    found = sorted(repo.fake.glob("call.*"), key=lambda p: int(p.name.split(".")[1]))
    return [
        {k: v for k, _, v in (line.partition("=") for line in p.read_text("utf-8").splitlines())}
        for p in found
    ]


def _call_env(repo: Repo, n: int) -> dict[str, str]:
    """The environment of the n-th run of the fake docker-smoke."""
    text = (repo.fake / f"env.{n}").read_text(encoding="utf-8")
    return {k: v for k, _, v in (line.partition("=") for line in text.splitlines() if "=" in line)}


def _docker(repo: Repo) -> list[str]:
    """Each call the script made to docker, and the fake build's."""
    log = repo.fake / "docker.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _smoke(repo: Repo, exit_code: str, out: str) -> None:
    """HEAD itself runs the check as given: a commit that rewrites the fake's two files."""
    _write(repo.path, {"smoke.exit": exit_code, "smoke.out": out})
    _commit(repo.path, "smoke")


def _smoke_patch(repo: Repo, exit_code: str, out: str) -> Path:
    """A defect patch that makes the fake exit with exit_code and print out."""
    _write(repo.path, {"smoke.exit": exit_code, "smoke.out": out})
    target = repo.path.parent / "smoke.patch"
    target.write_text(_git(repo.path, "diff", "--", "smoke.exit", "smoke.out"), encoding="utf-8")
    _git(repo.path, "checkout", "-q", "--", "smoke.exit", "smoke.out")
    return target


def test_a_failing_check_that_prints_the_text_is_a_proof(repo: Repo) -> None:
    code, lines = check(repo, *DEFECT)
    assert code == 0
    assert lines[-2:] == [SMOKE_RED, "red-first: docker-smoke failed, as EXPECT says"]
    assert lines[0].startswith("red-first: running docker-smoke on commit ")
    assert "red-first: baseline green on HEAD" in lines
    assert "red-first: smoke.exit as at HEAD~1" in lines
    assert [call["commits"] for call in _calls(repo)] == ["1", "2"]
    assert _uv_calls(repo) == [], "this mode syncs no uv environment"


def test_a_defect_patch_is_a_proof_of_the_check(repo: Repo) -> None:
    patch = _smoke_patch(repo, "3\n", SMOKE_OUT_AT_BASE)
    code, lines = check(repo, "--patch", str(patch))
    assert code == 0
    assert lines[-2:] == [SMOKE_RED, "red-first: docker-smoke failed, as EXPECT says"]
    assert "red-first: patch applied" in lines


def test_no_verdict_prints_an_exit_code_whatever_the_recipe_exited_with(repo: Repo) -> None:
    """Make ends every failed recipe with its own exit 2, so a code in the verdict says nothing."""
    patch = _smoke_patch(repo, "7\n", SMOKE_OUT_AT_BASE)
    code, lines = check(repo, "--patch", str(patch))
    assert code == 0
    assert lines[-1] == "red-first: docker-smoke failed, as EXPECT says"
    for text in (lines[-1], check(repo, *DEFECT, expect="no such text")[1][-1]):
        assert "exit" not in text


def test_each_shown_line_holding_the_text_is_printed_and_no_other(repo: Repo) -> None:
    out = f"first {KILL}\nan unrelated line\nsecond {KILL}\nlast line\n"
    code, lines = check(repo, "--patch", str(_smoke_patch(repo, "1\n", out)))
    assert code == 0
    assert lines[-3:] == [f"first {KILL}", f"second {KILL}", lines[-1]]
    assert lines[-1] == "red-first: docker-smoke failed, as EXPECT says"
    assert "an unrelated line" not in lines


def test_the_text_is_a_fixed_string_and_not_a_pattern(repo: Repo) -> None:
    """A dot, a star and a bracket in the text mean themselves."""
    text = "a.c [x]* (y)"
    out = f"abc xxx\n{text}\n"
    code, lines = check(repo, "--patch", str(_smoke_patch(repo, "1\n", out)), expect=text)
    assert code == 0
    assert lines[-2:] == [text, "red-first: docker-smoke failed, as EXPECT says"]


def test_a_text_that_looks_like_an_option_is_a_text(repo: Repo) -> None:
    code, lines = check(
        repo, "--patch", str(_smoke_patch(repo, "1\n", "-v is here\n")), expect="-v is"
    )
    assert code == 0
    assert lines[-2] == "-v is here"


def test_a_non_zero_exit_without_the_text_is_not_a_kill(repo: Repo) -> None:
    """A failed pull or a refused network exits non-zero as well: it is not the defect."""
    code, lines = check(repo, *DEFECT, expect="no such text")
    assert code == 2
    assert lines[-1] == "red-first: ERROR (docker-smoke failed without EXPECT)"
    assert SMOKE_RED in lines
    assert [call["commits"] for call in _calls(repo)] == ["1", "2"]


def test_an_error_shows_the_last_twenty_lines(repo: Repo) -> None:
    out = "".join(f"line {i:02d}\n" for i in range(1, 41))
    patch = _smoke_patch(repo, "3\n", out)
    code, lines = check(repo, "--patch", str(patch), expect="no such text")
    assert code == 2
    shown = lines[lines.index("red-first: patch applied") + 1 : -1]
    assert len(shown) == 20
    assert "line 40" in shown
    assert "line 01" not in lines


def test_a_check_that_passes_is_not_a_proof(repo: Repo) -> None:
    code, lines = check(repo, "--base", "HEAD~1", "--files", "")
    assert code == 1
    assert lines[-1] == "red-first: GREEN — docker-smoke passed"
    assert not any(line.startswith("image-smoke") for line in lines)
    assert [call["commits"] for call in _calls(repo)] == ["1", "2"]


def test_a_change_the_check_does_not_read_is_green(repo: Repo) -> None:
    code, lines = check(repo, *_revert("helper.py"))
    assert code == 1
    assert lines[-1] == "red-first: GREEN — docker-smoke passed"


def test_a_red_baseline_is_an_error_and_nothing_is_applied(repo: Repo) -> None:
    _smoke(repo, "3\n", SMOKE_OUT_AT_BASE)
    code, lines = check(repo, *DEFECT)
    assert code == 2
    assert lines[-1] == "red-first: ERROR (baseline docker-smoke failed)"
    assert SMOKE_RED in lines
    assert not any("as at" in line or "patch applied" in line for line in lines)
    assert len(_calls(repo)) == 1


def test_a_red_baseline_shows_its_last_twenty_lines(repo: Repo) -> None:
    _smoke(repo, "3\n", "".join(f"line {i:02d}\n" for i in range(1, 41)))
    code, lines = check(repo, *DEFECT)
    assert code == 2
    assert len(lines[1:-1]) == 20
    assert lines[-1] == "red-first: ERROR (baseline docker-smoke failed)"


def test_the_text_in_a_green_baseline_is_an_error_and_nothing_is_applied(repo: Repo) -> None:
    """A text that a green run prints would make a failure that prints it prove nothing."""
    code, lines = check(repo, *DEFECT, expect="expecting Apache-2.0")
    assert code == 2
    assert lines[-1] == "red-first: ERROR (EXPECT is in the green baseline's output)"
    assert any("expecting Apache-2.0" in line for line in lines[:-1]), "the lines are shown"
    assert not any("as at" in line or "patch applied" in line for line in lines)
    assert len(_calls(repo)) == 1


def test_a_patch_that_does_not_apply_is_an_error_of_the_check(repo: Repo) -> None:
    patch = repo.path.parent / "bad.patch"
    patch.write_text("--- a/nothing.py\n+++ b/nothing.py\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
    code, lines = check(repo, "--patch", str(patch))
    assert code == 2
    assert lines[-1].startswith("red-first: ERROR (the patch does not apply")
    assert len(_calls(repo)) == 1, "the baseline ran, and nothing after it"


def test_the_export_is_committed_by_a_fixed_author_and_is_heads_tree(repo: Repo) -> None:
    """The caller's git settings do not reach the export: a signing key that is no key, a hook
    that refuses, a name, and an ignore file that would leave the fake's two files out."""
    hooks = repo.path.parent / "hooks"
    hooks.mkdir()
    for name in ("pre-commit", "commit-msg", "post-commit"):
        (hooks / name).write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (hooks / name).chmod(0o755)
    config = repo.path.parent / "gitconfig"
    config.write_text(
        f"[user]\n\tname = Mallory\n\temail = m@example.invalid\n"
        f"[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /usr/bin/false\n"
        f"[core]\n\thooksPath = {hooks}\n",
        encoding="utf-8",
    )
    ignore = repo.path.parent / "xdg" / "git" / "ignore"
    ignore.parent.mkdir(parents=True)
    ignore.write_text("smoke.exit\nsmoke.out\n", encoding="utf-8")
    env = {"GIT_CONFIG_GLOBAL": str(config), "XDG_CONFIG_HOME": str(ignore.parents[1])}
    code, lines = check(repo, *DEFECT, env=env)
    assert code == 0, lines
    first, second = _calls(repo)
    head_tree = _git(repo.path, "rev-parse", "HEAD^{tree}").strip()
    assert first["tree"] == head_tree
    assert second["tree"] != head_tree
    for call, commits in ((first, "1"), (second, "2")):
        assert call["commits"] == commits
        assert call["dirty"] == "0"
        assert call["author"] == call["committer"] == FIXED_IDENTITY


def test_an_export_that_is_not_heads_tree_is_an_error(repo: Repo) -> None:
    """A tracked file that an ignore rule of the repository leaves out of the export's index."""
    _write(repo.path, {".gitignore": "ignored.txt\n", "ignored.txt": "tracked\n"})
    _git(repo.path, "add", "-f", "--", ".gitignore", "ignored.txt")
    _git(repo.path, "commit", "-q", "-m", "an ignored file, tracked")
    code, lines = check(repo, *DEFECT)
    assert code == 2
    assert lines[-1] == "red-first: ERROR (the export is not HEAD's tree)"
    assert _calls(repo) == []
    assert _docker(repo) == []


# --- the check runs alone: nothing of the caller's reaches it -------------------------------


def test_nothing_of_the_callers_environment_reaches_the_check(repo: Repo) -> None:
    """The variables `make red-first X=y` hands the script: the make variables, MAKEFLAGS, and
    the others a build reads, none of which may point the build at another platform."""
    env = {
        "MAKEFLAGS": "-j9 PLATFORM=linux/amd64 IMAGE=poison:tag",
        "MFLAGS": "-j9",
        "PLATFORM": "linux/amd64",
        "IMAGE": "poison:tag",
        "REVISION": "deadbeef",
        "LINUX_IMAGE": "poison:linux",
        "PULL_TIMEOUT": "1",
        "FOO": "bar",
        "DOCKER_HOST": "unix:///elsewhere.sock",
        "DOCKER_CONTEXT": "colima",
    }
    code, lines = check(repo, *DEFECT, env=env)
    assert code == 0, lines
    for n, call in enumerate(_calls(repo), start=1):
        assert call["platform"] == call["revision"] == call["linux"] == ""
        assert call["image"].startswith("gora-red-first:")
        assert "poison" not in call["flags"] and "j9" not in call["flags"], call["flags"]
        seen = _call_env(repo, n)
        for name in ("PLATFORM", "REVISION", "LINUX_IMAGE", "PULL_TIMEOUT", "FOO"):
            assert name not in seen, name
        for name in ("GIT_AUTHOR_NAME", "GIT_CONFIG_GLOBAL", "PYTHON", "UV_LOG", "PYTHONPATH"):
            assert name not in seen, name
        assert seen["IMAGE"] == call["image"], "make's own command-line variable, not the caller's"
        assert "poison" not in seen.get("MAKEFLAGS", "")
        assert "j9" not in seen.get("MAKEFLAGS", "") + seen.get("MFLAGS", "")
        assert (seen["HOME"], seen["TMPDIR"], seen["LC_ALL"]) == (str(repo.tmp), str(repo.tmp), "C")
        assert seen["PATH"].split(":")[0] == str(repo.bin)
        assert seen["DOCKER_HOST"] == "unix:///elsewhere.sock"
        assert seen["DOCKER_CONTEXT"] == "colima"
        assert seen["DOCKER_FAKE_DIR"] == str(repo.fake)


def test_the_callers_xdg_state_home_reaches_the_check_when_it_is_set(repo: Repo) -> None:
    """The bounded runs of the export are recorded in the caller's log (limit-report.sh)."""
    state = repo.path.parent / "state"
    code, lines = check(repo, *DEFECT, env={"XDG_STATE_HOME": str(state)})
    assert code == 0, lines
    assert len(_calls(repo)) == 2
    for n in (1, 2):
        assert _call_env(repo, n)["XDG_STATE_HOME"] == str(state)


@pytest.mark.parametrize("state", [None, ""])
def test_no_xdg_state_home_reaches_the_check_when_the_caller_has_none(
    repo: Repo, state: str | None
) -> None:
    code, lines = check(repo, *DEFECT, env=None if state is None else {"XDG_STATE_HOME": state})
    assert code == 0, lines
    assert len(_calls(repo)) == 2
    for n in (1, 2):
        assert "XDG_STATE_HOME" not in _call_env(repo, n)


def test_each_run_has_the_same_tag_of_its_own(repo: Repo) -> None:
    code, _ = check(repo, *DEFECT)
    assert code == 0
    first, second = _calls(repo)
    assert first["image"] == second["image"]
    assert re.fullmatch(r"gora-red-first:\d+-\d+", first["image"])


# --- the run's image: removed on every exit, when it is present ------------------------------


def _removed(repo: Repo) -> list[str]:
    return [line for line in _docker(repo) if line.startswith("image rm ")]


def test_the_image_is_removed_after_each_run_on_a_kill(repo: Repo) -> None:
    code, _ = check(repo, *DEFECT)
    assert code == 0
    tag = _calls(repo)[0]["image"]
    assert _removed(repo) == [f"image rm {tag}"] * 2, "after the baseline and after the second"


def test_the_image_is_removed_on_green(repo: Repo) -> None:
    code, _ = check(repo, "--base", "HEAD~1", "--files", "")
    assert code == 1
    assert len(_removed(repo)) == 2


@pytest.mark.parametrize("case", ["baseline-red", "baseline-text", "no-text", "bad-patch"])
def test_the_image_is_removed_on_an_error(repo: Repo, case: str) -> None:
    args = list(DEFECT)
    expect = KILL
    if case == "baseline-red":
        _smoke(repo, "3\n", SMOKE_OUT_AT_BASE)
    elif case == "baseline-text":
        expect = "expecting Apache-2.0"
    elif case == "no-text":
        expect = "no such text"
    else:
        bad = repo.path.parent / "bad.patch"
        bad.write_text("--- a/nothing.py\n+++ b/nothing.py\n@@ -1 +1 @@\n-a\n+b\n", "utf-8")
        args = ["--patch", str(bad)]
    code, _ = check(repo, *args, expect=expect)
    assert code == 2
    assert len(_calls(repo)) >= 1
    assert len(_removed(repo)) >= 1


def test_an_image_that_is_not_there_is_not_removed(repo: Repo) -> None:
    """Check, then act: a build that left no image leaves nothing to remove."""
    code, _ = check(repo, *DEFECT, env={"DOCKER_FAKE_NO_BUILD": "1"})
    assert code == 0
    docker = _docker(repo)
    assert any(line.startswith("image inspect ") for line in docker)
    assert _removed(repo) == []


def test_a_term_ends_the_check_and_removes_the_image(repo: Repo) -> None:
    before = _status(repo)
    proc = subprocess.Popen(
        [
            "bash",
            str(SCRIPT),
            "--check",
            "docker-smoke",
            "--expect",
            KILL,
            *DEFECT,
        ],
        cwd=repo.path,
        env=_script_env(repo, {"DOCKER_FAKE_SLEEP": "60"}),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not list(repo.fake.glob("image.*")):
            assert time.monotonic() < deadline, "the check never built its image"
            time.sleep(0.05)
        while not (repo.fake / "env.1").exists():
            assert time.monotonic() < deadline, "the check never started"
            time.sleep(0.05)
        assert list(repo.tmp.iterdir()) != []
        proc.send_signal(signal.SIGTERM)
        output, _ = proc.communicate(timeout=30)
    finally:
        proc.kill()
        proc.wait()
    assert proc.returncode == 143
    assert len(_removed(repo)) == 1
    _check_run(repo, output, before)


# --- the shown lines go through scrub -------------------------------------------------------


def test_the_checks_output_names_no_path_of_this_machine(repo: Repo) -> None:
    out = (
        f"cwd=@PWD@ {KILL}\n"
        f"in @PWD@/context: {KILL}\n"
        f"file://@PWD@/a {KILL}\n"
        f"elsewhere /opt/elsewhere/ctx {KILL}\n"
    )
    code, lines = check(repo, "--patch", str(_smoke_patch(repo, "1\n", out)))
    assert code == 0
    assert lines[-5:-1] == [
        f"cwd=<path>/export {KILL}",
        f"in <path>/export/context: {KILL}",
        f"file://<path>/export/a {KILL}",
        f"elsewhere <path> {KILL}",
    ]


def test_an_expect_written_with_the_path_marker_matches_a_line_whose_path_was_replaced(
    repo: Repo,
) -> None:
    """EXPECT is matched against the output as shown: where that says <path>, EXPECT does."""
    code, lines = check(repo, *DEFECT, expect="(built in <path>/export/context)")
    assert code == 0
    assert lines[-2:] == [SMOKE_RED, "red-first: docker-smoke failed, as EXPECT says"]


@pytest.mark.parametrize("where", ["a-path-the-pattern-takes", "the-temporary-directory"])
def test_an_expect_with_an_absolute_path_is_refused_before_anything_is_exported(
    repo: Repo, where: str
) -> None:
    """Scrub would show it as <path>, so it could never match and two builds would be wasted."""
    expect = {
        "a-path-the-pattern-takes": "built in /opt/elsewhere/context",
        "the-temporary-directory": f"cwd={repo.tmp}/export",
    }[where]
    code, lines = check(repo, *DEFECT, expect=expect)
    assert code == 64
    # Nothing but the refusal, which does not repeat the path: not even the line that says
    # the commit is being run, which comes before the export.
    assert lines == [
        "red-first: EXPECT holds an absolute path, which the output shows as <path>: "
        "write it as <path>"
    ]
    assert _docker(repo) == []
    assert _calls(repo) == []
    assert _uv_calls(repo) == []


# --- a wrong call, and a tree that is only read ----------------------------------------------


@pytest.mark.parametrize(
    ("args", "tests"),
    [
        (["--check", "docker-smoke", "--expect", KILL], ""),
        (["--check", "docker-smoke", "--base", "HEAD~1", "--files", "smoke.exit"], ""),
        (["--check", "docker-smoke", "--expect", "", "--base", "HEAD~1"], ""),
        (["--check", "verify", "--expect", KILL, "--base", "HEAD~1"], ""),
        (["--check", "", "--expect", KILL, "--base", "HEAD~1"], ""),
        (["--check", "docker-smoke", "--expect", "a\nb", "--base", "HEAD~1"], ""),
        (["--check", "docker-smoke", "--expect", KILL, "--base", "HEAD~1"], "tests/test_thing.py"),
        (["--expect", KILL, "--base", "HEAD~1", "--files", "thing.py"], "tests/test_thing.py"),
        (["--check", "docker-smoke", "--expect", KILL, "--files", "smoke.exit"], ""),
        (["--check", "docker-smoke"], ""),
    ],
    ids=[
        "no-change",
        "no-text",
        "blank-text",
        "another-check",
        "blank-check",
        "text-of-two-lines",
        "check-and-tests",
        "text-without-check",
        "files-without-base",
        "nothing-else",
    ],
)
def test_a_wrong_call_of_the_check_is_refused(repo: Repo, args: list[str], tests: str) -> None:
    code, lines = run(repo, *args, tests=tests)
    assert code == 64
    assert _uv_calls(repo) == []
    assert _docker(repo) == []
    assert lines[-1] != "red-first: docker-smoke failed, as EXPECT says"


@pytest.mark.parametrize("dirt", ["untracked.txt", "smoke.exit"])
def test_a_dirty_tree_is_refused_by_the_check(repo: Repo, dirt: str) -> None:
    (repo.path / dirt).write_text("dirt\n", encoding="utf-8")
    code, lines = check(repo, *DEFECT)
    assert code == 2
    assert lines[-1] == "red-first: ERROR (working tree is dirty)"
    assert _docker(repo) == []


def test_make_passes_the_check_and_its_text_as_it_passes_the_others() -> None:
    """The recipe hands the script each variable of the call: a check is `make red-first
    CHECK=docker-smoke EXPECT=…`, with the same BASE, FILES and PATCH as the tests have."""
    done = subprocess.run(
        ["make", "-n", "-C", str(ROOT), "red-first", "CHECK=docker-smoke", "EXPECT=x"],
        env=_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    recipe = " ".join(done.stdout.replace("\\\n", " ").split())
    assert (
        '--tests "$TESTS" --check "$CHECK" --expect "$EXPECT" --base "$BASE" --files "$FILES"'
        ' --patch "$PATCH"'
    ) in recipe

"""The report of a bounded run (TESTING rule 8): bounded.sh, run-container.sh and pull.sh.

Every gate run ends with one line on standard error that says how much of its limit it used:
the limit's name, the seconds, the limit, the share, the load average when the run started and
the CPUs, and a sentence when the run took more than a tenth of its limit: that rule 8 does not
hold, for a run that started at rest, or that it did not start at rest. The run is also appended,
with its exit code, to this machine's log as one record. Neither changes the exit code.
``scripts/gate/limit-report.sh`` is their one implementation. The seconds are two readings of
the real clock, ``date +%s``, at the start and the end of the run; a clock that went back, or a
reading that is no number, makes them ``unknown``, and an unknown run is not recorded.

Each test runs the real scripts. The machine's own load and CPU count never reach a verdict
(TESTING rule 6): ``getconf`` and ``sysctl`` are shims on ``PATH``, and the file a Linux host
reads its load from is named by ``LIMIT_REPORT_LOADAVG``. ``docker`` is a shim as well, so
``run-container.sh`` and ``pull.sh`` run without Docker.

Time is a shim too, and no test sleeps to make a duration. ``date`` reads a fake clock from a
file in the box and answers ``+%s`` and ``-u +%Y-%m-%dT%H:%M:%SZ`` from it; ``tick N`` moves it
by N seconds, back too. The scripts know nothing of it: they read ``date`` as they would the
real one, and the shim only changes what it says. A duration is therefore exact, whatever the
machine's load or clock: the command ticks (``tick 1``), the docker shim's pull and run tick
``FAKE_PULL_SECONDS`` and ``FAKE_RUN_SECONDS``. A test of a limit (exit 124) keeps a real
``timeout(1)`` and ticks before its real sleep. One test removes the ``date`` shim and runs on
the real clock, and checks the form of the line only.

The suite never writes the machine's own log: every test has its own ``HOME`` and
``XDG_STATE_HOME`` (``_isolated_home`` in ``tests/conftest.py``), and the box passes its own,
under the test's directory, to the scripts it runs.
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "gate"
BOUNDED = GATE / "bounded.sh"
REPORT = GATE / "limit-report.sh"
RUN_CONTAINER = GATE / "run-container.sh"
PULL = GATE / "pull.sh"

SENTENCE = "; above a tenth of the limit, TESTING rule 8 does not hold"
NOT_AT_REST = "; above a tenth of the limit, not at rest"
NOT_RECORDED = "limit-report.sh: this run was not recorded in the machine's log"
FINISHED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
LINE = re.compile(
    r"^(?P<who>[\w.-]+): (?P<name>[A-Z][A-Z0-9_]*): (?P<took>\d+) s of (?P<limit>\d+) s "
    r"\((?P<share>\d+)%\), load (?P<load>\S+) on (?P<cpus>\S+) CPUs(?P<sentence>.*)$"
)

# The fake clock starts here (2027-01-15 08:00:00 UTC) and moves only by `tick`.
CLOCK_START = 1_800_000_000

# getconf and sysctl as each platform prints them; any other question is unanswered.
GETCONF_SHIM = """\
#!/bin/sh
if [ "$*" = "_NPROCESSORS_ONLN" ] && [ -n "${FAKE_CPUS+x}" ]; then
  echo "$FAKE_CPUS"
  exit 0
fi
exit 1
"""
SYSCTL_SHIM = """\
#!/bin/sh
if [ "$*" = "-n vm.loadavg" ] && [ -n "${FAKE_SYSCTL+x}" ]; then
  echo "$FAKE_SYSCTL"
  exit 0
fi
exit 1
"""
# date: the fake clock, the file `clock` in FAKE_DIR. `+%s` prints it as it is, a number or not;
# the UTC time of the report's record is made from it; any other question exits 1.
DATE_SHIM = f"""\
#!{sys.executable}
import os
import sys
from datetime import UTC, datetime

now = open(os.path.join(os.environ["FAKE_DIR"], "clock"), encoding="utf-8").read().strip()
if sys.argv[1:] == ["+%s"]:
    print(now)
elif sys.argv[1:] == ["-u", "+%Y-%m-%dT%H:%M:%SZ"] and now.isdigit():
    print(datetime.fromtimestamp(int(now), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
else:
    sys.exit(1)
"""
# tick N: the fake clock moves N seconds, N negative for a clock that goes back. Builtins only,
# so that a command which ticks first reaches its tick before any limit of a second.
TICK_SHIM = """\
#!/bin/sh
read -r now < "$FAKE_DIR/clock"
echo $((now + $1)) > "$FAKE_DIR/clock"
"""
# docker: an image is present once the file `pulled` exists. A pull ticks FAKE_PULL_SECONDS; a
# run ticks FAKE_RUN_SECONDS and exits FAKE_RUN_EXIT. Each call is logged.
DOCKER_SHIM = """\
#!/bin/sh
echo "$*" >> "$FAKE_DIR/docker.log"
case "$1" in
  ps) exit 0 ;;
  rm) exit 0 ;;
  image)
    if [ -e "$FAKE_DIR/pulled" ]; then echo linux/arm64; exit 0; fi
    exit 1
    ;;
  pull)
    tick "${FAKE_PULL_SECONDS:-0}"
    : > "$FAKE_DIR/pulled"
    exit "${FAKE_PULL_EXIT:-0}"
    ;;
  run)
    tick "${FAKE_RUN_SECONDS:-0}"
    exit "${FAKE_RUN_EXIT:-0}"
    ;;
esac
exit 64
"""


@dataclass
class Box:
    path: Path
    bin: Path
    loadavg: Path
    fake: Path

    def env(self, **extra: str) -> dict[str, str]:
        base = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "LIMIT_REPORT_LOADAVG": str(self.loadavg),
            "FAKE_DIR": str(self.fake),
            "FAKE_CPUS": "8",
            "HOME": str(self.path),
            "XDG_STATE_HOME": str(self.path / "state"),
        }
        return {**base, **extra}

    @property
    def log(self) -> Path:
        """The log of bounded runs, as the box's XDG_STATE_HOME places it."""
        return self.path / "state" / "gora" / "limits.log"

    @property
    def clock(self) -> Path:
        """The file the fake clock reads its time from."""
        return self.fake / "clock"


@pytest.fixture
def box(tmp_path: Path) -> Box:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (
        ("getconf", GETCONF_SHIM),
        ("sysctl", SYSCTL_SHIM),
        ("docker", DOCKER_SHIM),
        ("date", DATE_SHIM),
        ("tick", TICK_SHIM),
    ):
        shim = bin_dir / name
        shim.write_text(source, encoding="utf-8")
        shim.chmod(0o755)
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "clock").write_text(f"{CLOCK_START}\n", encoding="utf-8")
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("1.50 1.20 1.00 2/300 4242\n", encoding="utf-8")
    return Box(path=tmp_path, bin=bin_dir, loadavg=loadavg, fake=fake)


def at(seconds: int) -> str:
    """The UTC time the fake clock prints, `seconds` after it started."""
    return datetime.fromtimestamp(CLOCK_START + seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Run:
    code: int
    out: str
    err: str

    @property
    def lines(self) -> list[str]:
        return self.err.splitlines()

    @property
    def report(self) -> re.Match[str]:
        match = LINE.match(self.lines[-1])
        assert match, self.err
        return match

    def unmeasured(self, who: str, name: str, limit: int, load: str, cpus: str) -> None:
        """The run was reported as unmeasured: the line, then that it was not recorded."""
        assert self.lines[-2:] == [
            f"{who}: {name}: unknown s of {limit} s (unknown%), load {load} on {cpus} CPUs",
            NOT_RECORDED,
        ], self.err


def _run(
    argv: list[str | int | Path],
    env: dict[str, str],
    *,
    stderr_closed: bool = False,
    cwd: Path | None = None,
) -> Run:
    command = [str(part) for part in argv]
    if stderr_closed:
        # sh closes the descriptor, then becomes the script: the script sees no standard error.
        command = ["sh", "-c", 'exec "$0" "$@" 2>&-', *command]
    done = subprocess.run(
        command, env=env, cwd=cwd, capture_output=True, text=True, timeout=60, check=False
    )
    return Run(done.returncode, done.stdout, done.stderr)


def bounded(box: Box, name: str, seconds: str | int, *command: str, **env: str) -> Run:
    return _run([BOUNDED, name, str(seconds), *command], box.env(**env))


# --- the report ----------------------------------------------------------------------------


def test_a_green_run_ends_with_its_report(box: Box) -> None:
    run = bounded(box, "VERIFY_TIMEOUT", 100, "true")
    assert run.code == 0
    assert run.out == ""
    report = run.report
    assert report["who"] == "bounded.sh"
    assert report["name"] == "VERIFY_TIMEOUT"
    assert report["limit"] == "100"
    assert (report["load"], report["cpus"]) == ("1.50", "8")
    assert report["sentence"] == ""
    assert (report["took"], report["share"]) == ("0", "0")
    assert len(run.lines) == 1, "a green run says nothing else"


def test_another_exit_code_is_the_commands_and_gets_its_report(box: Box) -> None:
    run = bounded(box, "CHECK_TIMEOUT", 100, "sh", "-c", "echo out; echo err >&2; exit 7")
    assert run.code == 7
    assert run.out == "out\n"
    assert run.lines[0] == "err"
    assert (run.report["name"], run.report["took"]) == ("CHECK_TIMEOUT", "0")
    assert len(run.lines) == 2


def test_at_the_limit_the_old_line_comes_first_and_the_report_after_it(box: Box) -> None:
    run = bounded(box, "BEHAVIOUR_TIMEOUT", 1, "sh", "-c", "tick 1; exec sleep 30")
    assert run.code == 124
    assert run.lines[0] == (
        "bounded.sh: the limit of 1 s was reached (exit 124): sh -c tick 1; exec sleep 30"
    )
    report = run.report
    assert (report["name"], report["limit"]) == ("BEHAVIOUR_TIMEOUT", "1")
    assert (report["took"], report["share"]) == ("1", "100")
    assert report["sentence"] == SENTENCE, "1 s of 1 s is far above a tenth"
    assert len(run.lines) == 2


def test_above_a_tenth_of_the_limit_the_report_says_rule_8_does_not_hold(box: Box) -> None:
    run = bounded(box, "VERIFY_TIMEOUT", 9, "tick", "1")
    assert run.code == 0, "the sentence is a remark, never a failure"
    assert (run.report["took"], run.report["share"]) == ("1", "12")
    assert run.report["sentence"] == SENTENCE


def test_at_or_below_a_tenth_of_the_limit_it_does_not(box: Box) -> None:
    run = bounded(box, "VERIFY_TIMEOUT", 10, "tick", "1")
    assert run.code == 0
    assert (run.report["took"], run.report["share"]) == ("1", "10")
    assert run.report["sentence"] == ""


@pytest.mark.parametrize(
    ("took", "limit", "share", "sentence"),
    [
        (6, 60, "10", False),  # exactly a tenth: rule 8 holds
        (7, 60, "12", True),  # a second over: it does not, and the share is rounded up
        (60, 600, "10", False),
        (61, 600, "11", True),
        (0, 600, "0", False),
        (600, 600, "100", True),
        (1, 1000, "1", False),
    ],
)
def test_the_share_and_the_tenth_are_integer_arithmetic(
    box: Box, took: int, limit: int, share: str, sentence: bool
) -> None:
    run = _run([REPORT, "line", "bounded.sh", "X_TIMEOUT", took, limit, "1.0", "4", 0], box.env())
    assert run.code == 0
    report = run.report
    assert (report["took"], report["limit"], report["share"]) == (str(took), str(limit), share)
    assert (report["sentence"] == SENTENCE) is sentence
    assert (sentence and int(share) > 10) or (not sentence and int(share) <= 10)


# --- the pull -------------------------------------------------------------------------------


def test_a_pull_is_not_held_to_a_tenth(box: Box) -> None:
    run = _run([REPORT, "line", "bounded.sh", "PULL_TIMEOUT", 200, 300, "1.0", "4", 0], box.env())
    assert run.report["share"] == "67"
    assert run.report["sentence"] == "", "a pull is bounded by the network, not by a run"


def test_a_pull_gets_no_sentence_at_any_load(box: Box) -> None:
    for load, cpus in (("1.0", "4"), ("9.0", "4"), ("unknown", "4")):
        run = _run(
            [REPORT, "line", "bounded.sh", "PULL_TIMEOUT", 200, 300, load, cpus, 0], box.env()
        )
        assert run.report["sentence"] == ""


def test_above_a_tenth_a_run_that_did_not_start_at_rest_says_so(box: Box) -> None:
    box.loadavg.write_text("4.00 4.00 4.00 2/300 4242\n", encoding="utf-8")
    run = bounded(box, "VERIFY_TIMEOUT", 9, "tick", "1")
    assert run.code == 0
    assert (run.report["load"], run.report["cpus"]) == ("4.00", "8")
    assert run.report["sentence"] == NOT_AT_REST, "half of 8 CPUs is 4: not under it"
    [record] = _records(box.log)
    assert record[3:] == ["4.00", "8", "0"]


def test_pull_sh_runs_its_pull_under_that_name(box: Box) -> None:
    run = _run([PULL, "gate:image", "linux/arm64", 9], box.env(FAKE_PULL_SECONDS="1"))
    assert run.code == 0
    assert run.lines[0] == "pull.sh: pulling gate:image for linux/arm64, limit 9 s"
    report = run.report
    assert (report["who"], report["name"], report["limit"]) == ("bounded.sh", "PULL_TIMEOUT", "9")
    assert report["took"] == "1", "above a tenth of 9 s, and a pull is not held to one"
    assert report["sentence"] == ""


def test_an_image_already_present_is_not_pulled_and_reports_nothing(box: Box) -> None:
    (box.fake / "pulled").write_text("", encoding="utf-8")
    run = _run([PULL, "gate:image", "linux/arm64", 30], box.env())
    assert (run.code, run.err) == (0, "")


def test_a_failed_pull_keeps_its_exit_code_and_its_report(box: Box) -> None:
    run = _run([PULL, "gate:image", "linux/arm64", 30], box.env(FAKE_PULL_EXIT="5"))
    assert run.code == 5
    assert run.lines[-2].startswith("bounded.sh: PULL_TIMEOUT: ")
    assert run.lines[-1] == (
        "pull.sh: gate:image for linux/arm64 could not be pulled (exit 5); no container started"
    )


# --- a call that names no limit -------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["100", "touch", "ran"],  # the old call: SECONDS COMMAND
        ["verify_timeout", "100", "touch", "ran"],
        ["VERIFY-TIMEOUT", "100", "touch", "ran"],
        ["VERIFY_TIMEOUT", "1.5", "touch", "ran"],
        ["VERIFY_TIMEOUT", "0", "touch", "ran"],
        ["VERIFY_TIMEOUT", "-1", "touch", "ran"],
        ["VERIFY_TIMEOUT", "100"],  # no command
        ["VERIFY_TIMEOUT"],
        [],
    ],
)
def test_bounded_refuses_a_call_without_a_name_and_runs_nothing(box: Box, argv: list[str]) -> None:
    run = _run([BOUNDED, *argv], box.env(), cwd=box.path)
    assert run.code == 64, run.err
    assert not (box.path / "ran").exists()


def test_run_container_refuses_a_call_without_a_name_and_calls_no_docker(box: Box) -> None:
    for argv in (
        ["smoke", "60", "--platform", "linux/arm64", "--", "img", "true"],
        ["smoke", "smoke_timeout", "60", "--platform", "linux/arm64", "--", "img", "true"],
        ["smoke", "SMOKE_TIMEOUT", "x", "--platform", "linux/arm64", "--", "img", "true"],
        ["smoke", "SMOKE_TIMEOUT"],
        [],
    ):
        run = _run([RUN_CONTAINER, *argv], box.env())
        assert run.code == 64, (argv, run.err)
    assert not (box.fake / "docker.log").exists()


# --- the exit code is never the report's ---------------------------------------------------


@pytest.mark.parametrize(
    ("command", "limit", "code"),
    [(["true"], 100, 0), (["sh", "-c", "exit 7"], 100, 7), (["sleep", "30"], 1, 124)],
)
def test_with_standard_error_closed_the_exit_code_is_still_the_commands(
    box: Box, command: list[str], limit: int, code: int
) -> None:
    run = _run([BOUNDED, "VERIFY_TIMEOUT", limit, *command], box.env(), stderr_closed=True)
    assert run.code == code
    assert run.err == ""


@pytest.mark.parametrize("failing", ["line", "elapsed"])
def test_a_helper_that_fails_does_not_change_the_exit_code(box: Box, failing: str) -> None:
    """The report is allowed to fail on every print; only `check` and `sample` may stop a run."""
    scripts = box.path / "scripts"
    scripts.mkdir()
    shutil.copy(BOUNDED, scripts / "bounded.sh")
    helper = scripts / "limit-report.sh"
    helper.write_text(
        f'#!/bin/sh\nif [ "$1" = {failing} ]; then exit 1; fi\n'
        'if [ "$1" = sample ]; then echo "1.0 4"; fi\nexit 0\n',
        encoding="utf-8",
    )
    helper.chmod(0o755)
    for command, code in ((["true"], 0), (["sh", "-c", "exit 9"], 9)):
        run = _run([scripts / "bounded.sh", "VERIFY_TIMEOUT", 100, *command], box.env())
        assert run.code == code, run.err


def test_above_a_tenth_the_exit_code_is_still_the_commands(box: Box) -> None:
    for command, code in ((["sh", "-c", "tick 1; exit 3"], 3),):
        run = bounded(box, "VERIFY_TIMEOUT", 9, *command)
        assert run.code == code, run.err
        assert run.report["took"] == "1"
        assert run.report["sentence"] == SENTENCE


# --- a duration that was not measured ------------------------------------------------------


@pytest.mark.parametrize("code", [0, 7])
def test_a_clock_that_went_back_reports_unknown_and_records_nothing(box: Box, code: int) -> None:
    run = bounded(box, "VERIFY_TIMEOUT", 100, "sh", "-c", f"tick -3; exit {code}")
    assert run.code == code, "the exit code is the command's"
    run.unmeasured("bounded.sh", "VERIFY_TIMEOUT", 100, "1.50", "8")
    assert not box.log.exists()


@pytest.mark.parametrize(
    ("before", "during"),
    [("x", None), ("", None), ("1.5", None), (None, "x"), (None, "")],
    ids=["start x", "start empty", "start 1.5", "end x", "end empty"],
)
def test_a_reading_that_is_no_number_reports_unknown_and_records_nothing(
    box: Box, before: str | None, during: str | None
) -> None:
    if before is not None:
        box.clock.write_text(f"{before}\n", encoding="utf-8")
    command = ["true"] if during is None else ["sh", "-c", f"echo '{during}' > '{box.clock}'"]
    run = bounded(box, "VERIFY_TIMEOUT", 100, *command)
    assert run.code == 0
    run.unmeasured("bounded.sh", "VERIFY_TIMEOUT", 100, "1.50", "8")
    assert not box.log.exists()


def test_an_unmeasured_run_at_its_limit_keeps_124_and_says_so_first(box: Box) -> None:
    run = bounded(box, "BEHAVIOUR_TIMEOUT", 1, "sh", "-c", "tick -1; exec sleep 30")
    assert run.code == 124
    assert run.lines[0].startswith("bounded.sh: the limit of 1 s was reached (exit 124)")
    run.unmeasured("bounded.sh", "BEHAVIOUR_TIMEOUT", 1, "1.50", "8")
    assert not box.log.exists()


def test_a_container_whose_clock_went_back_reports_unknown_and_records_nothing(box: Box) -> None:
    (box.fake / "pulled").write_text("", encoding="utf-8")
    run = _container(box, "PROBE_TIMEOUT", 60, FAKE_RUN_SECONDS="-3", FAKE_RUN_EXIT="3")
    assert run.code == 3
    run.unmeasured("run-container.sh", "PROBE_TIMEOUT", 60, "1.50", "8")
    assert not box.log.exists()


@pytest.mark.parametrize("reading", ["x", ""])
def test_a_container_reading_that_is_no_number_reports_unknown_and_records_nothing(
    box: Box, reading: str
) -> None:
    (box.fake / "pulled").write_text("", encoding="utf-8")
    box.clock.write_text(f"{reading}\n", encoding="utf-8")
    run = _container(box, "PROBE_TIMEOUT", 60)
    assert run.code == 0
    run.unmeasured("run-container.sh", "PROBE_TIMEOUT", 60, "1.50", "8")
    assert not box.log.exists()


@pytest.mark.parametrize(
    ("start", "end", "answer"),
    [
        ("100", "100", "0"),
        ("100", "107", "7"),
        ("007", "010", "3"),
        ("0", "0", "0"),
        ("1800000000", "1800000002", "2"),
        ("100", "99", "unknown"),
        ("100", "", "unknown"),
        ("", "100", "unknown"),
        ("x", "100", "unknown"),
        ("100", "1x", "unknown"),
        ("100", "-1", "unknown"),
        ("1.5", "2.5", "unknown"),
        ("0x1", "0x2", "unknown"),
        ("1", "1" + "0" * 30, "unknown"),
    ],
)
def test_elapsed_is_the_difference_of_two_whole_readings_else_unknown(
    box: Box, start: str, end: str, answer: str
) -> None:
    run = _run([REPORT, "elapsed", start, end], box.env())
    assert (run.code, run.out, run.err) == (0, f"{answer}\n", "")


def test_a_line_whose_elapsed_is_unknown_has_no_sentence_and_no_record(box: Box) -> None:
    run = _run([REPORT, "line", "bounded.sh", "X_TIMEOUT", "unknown", 1, "9.0", "4", 5], box.env())
    assert run.code == 0
    run.unmeasured("bounded.sh", "X_TIMEOUT", 1, "9.0", "4")
    assert not box.log.exists()


# --- load and CPUs, from each source, as each platform prints it ---------------------------


def _load_and_cpus(box: Box, **env: str) -> tuple[str, str]:
    run = bounded(box, "CHECK_TIMEOUT", 100, "true", **env)
    assert run.code == 0, run.err
    return run.report["load"], run.report["cpus"]


def test_linux_load_is_the_first_field_of_proc_loadavg(box: Box) -> None:
    box.loadavg.write_text("0.52 0.40 0.30 3/157 8\n", encoding="utf-8")
    assert _load_and_cpus(box, FAKE_CPUS="4") == ("0.52", "4")


def test_macos_load_is_the_first_number_of_sysctl_vm_loadavg(box: Box) -> None:
    box.loadavg.unlink()
    assert _load_and_cpus(box, FAKE_SYSCTL="{ 3.96 3.36 3.22 }", FAKE_CPUS="10") == ("3.96", "10")


def test_the_file_is_read_before_sysctl(box: Box) -> None:
    assert _load_and_cpus(box, FAKE_SYSCTL="{ 9.99 9.99 9.99 }") == ("1.50", "8")


def test_a_file_that_is_not_a_load_falls_back_to_sysctl(box: Box) -> None:
    box.loadavg.write_text("not a load\n", encoding="utf-8")
    assert _load_and_cpus(box, FAKE_SYSCTL="{ 2.25 2.00 1.75 }")[0] == "2.25"


@pytest.mark.parametrize(
    "printed", ["", "{ }", "{ abc 1 2 }", "{ 3,96 3,36 3,22 }", "{ 1.2.3 1 1 }"]
)
def test_a_load_nobody_can_read_is_unknown_and_never_a_guess(box: Box, printed: str) -> None:
    box.loadavg.unlink()
    assert _load_and_cpus(box, FAKE_SYSCTL=printed) == ("unknown", "8")


def test_with_no_source_at_all_both_are_unknown_and_the_run_goes_on(box: Box) -> None:
    box.loadavg.unlink()
    run = bounded(box, "CHECK_TIMEOUT", 100, "sh", "-c", "exit 4", FAKE_CPUS="")
    assert run.code == 4
    assert (run.report["load"], run.report["cpus"]) == ("unknown", "unknown")


@pytest.mark.parametrize("printed", ["x", "0", "-1", "4.5"])
def test_a_cpu_count_that_is_not_a_count_is_unknown(box: Box, printed: str) -> None:
    assert _load_and_cpus(box, FAKE_CPUS=printed)[1] == "unknown"


def test_the_load_is_the_one_sampled_when_the_run_started(box: Box) -> None:
    run = bounded(
        box,
        "CHECK_TIMEOUT",
        100,
        "sh",
        "-c",
        f"echo '7.77 7.00 7.00 1/1 1' > {box.loadavg}",
    )
    assert run.code == 0
    assert run.report["load"] == "1.50", "a gate loads the machine itself"


# --- run-container.sh, without Docker ------------------------------------------------------


def _container(box: Box, name: str = "SMOKE_TIMEOUT", seconds: int = 60, **env: str) -> Run:
    return _run(
        [
            RUN_CONTAINER,
            "image-smoke",
            name,
            seconds,
            "--platform",
            "linux/arm64",
            "--",
            "img",
            "x",
        ],
        box.env(**env),
    )


def test_a_container_run_ends_with_the_same_report(box: Box) -> None:
    (box.fake / "pulled").write_text("", encoding="utf-8")
    run = _container(box)
    assert run.code == 0
    report = run.report
    assert report["who"] == "run-container.sh"
    assert (report["name"], report["limit"]) == ("SMOKE_TIMEOUT", "60")
    assert (report["load"], report["cpus"]) == ("1.50", "8"), "the host's, which runs the script"
    assert (report["took"], report["share"]) == ("0", "0")
    assert report["sentence"] == ""
    assert run.lines == [run.lines[-1]]


@pytest.mark.parametrize("code", [1, 3, 124, 137])
def test_the_container_exit_code_is_passed_on_with_its_report(box: Box, code: int) -> None:
    (box.fake / "pulled").write_text("", encoding="utf-8")
    run = _container(box, "PROBE_TIMEOUT", 60, FAKE_RUN_EXIT=str(code))
    assert run.code == code
    assert (run.report["name"], run.report["took"]) == ("PROBE_TIMEOUT", "0")


def test_a_slow_container_run_says_rule_8_does_not_hold(box: Box) -> None:
    (box.fake / "pulled").write_text("", encoding="utf-8")
    run = _container(box, "VERIFY_LINUX_TIMEOUT", 9, FAKE_RUN_SECONDS="1")
    assert run.code == 0
    assert (run.report["took"], run.report["share"]) == ("1", "12")
    assert run.report["sentence"] == SENTENCE


def test_the_report_of_a_container_leaves_out_the_pull_before_it(box: Box) -> None:
    run = _container(box, "SMOKE_TIMEOUT", 10, FAKE_PULL_SECONDS="2", PULL_TIMEOUT="30")
    assert run.code == 0
    pull = next(line for line in run.lines if line.startswith("bounded.sh: PULL_TIMEOUT: "))
    match = LINE.match(pull)
    assert match and (match["took"], match["limit"]) == ("2", "30")
    report = run.report
    assert report["who"] == "run-container.sh"
    assert (report["took"], report["share"]) == ("0", "0"), "the 2 s of the pull are not the run's"
    assert report["sentence"] == ""


def test_a_container_on_a_leftover_is_refused_before_it_runs_and_reports_nothing(box: Box) -> None:
    ps = box.bin / "docker"
    ps.write_text(DOCKER_SHIM.replace("ps) exit 0 ;;", "ps) echo gora-gate-left; exit 0 ;;"))
    run = _container(box)
    assert run.code == 2
    assert "refusing to start" in run.err
    assert not any(line.startswith("run-container.sh:") for line in run.lines)


# --- the record in the machine's log -------------------------------------------------------


def _records(log: Path) -> list[list[str]]:
    """The records of a log, each split into its tab-separated fields."""
    return [line.split("\t") for line in log.read_text(encoding="utf-8").splitlines()]


def _assert_record(record: list[str], report: re.Match[str], code: int, finished: str) -> None:
    """The six fields of a run's record, as its report line printed them, and the UTC time on
    the fake clock when the line was printed."""
    assert len(record) == 6, record
    assert FINISHED.match(record[0]), record[0]
    assert record == [
        finished,
        report["name"],
        report["took"],
        report["load"],
        report["cpus"],
        str(code),
    ]


@pytest.mark.parametrize(
    ("command", "limit", "code", "took"),
    [
        (["true"], 100, 0, 0),
        (["sh", "-c", "tick 2; exit 7"], 100, 7, 2),
        (["sh", "-c", "tick 1; exec sleep 30"], 1, 124, 1),
    ],
)
def test_a_run_is_recorded_with_its_exit_code(
    box: Box, command: list[str], limit: int, code: int, took: int
) -> None:
    run = bounded(box, "VERIFY_TIMEOUT", limit, *command)
    assert run.code == code
    records = _records(box.log)
    assert len(records) == 1
    assert run.report["took"] == str(took)
    _assert_record(records[0], run.report, code, at(took))
    assert NOT_RECORDED not in run.err


def test_a_container_run_is_recorded_with_the_containers_exit_code(box: Box) -> None:
    (box.fake / "pulled").write_text("", encoding="utf-8")
    run = _container(box, "PROBE_TIMEOUT", 60, FAKE_RUN_SECONDS="4", FAKE_RUN_EXIT="3")
    assert run.code == 3
    [record] = _records(box.log)
    assert run.report["took"] == "4"
    _assert_record(record, run.report, 3, at(4))


def test_the_pull_is_recorded_as_pull_timeout_before_the_container(box: Box) -> None:
    run = _container(box, "SMOKE_TIMEOUT", 60, FAKE_PULL_SECONDS="2", FAKE_RUN_SECONDS="1")
    assert run.code == 0
    assert [record[:3] for record in _records(box.log)] == [
        [at(2), "PULL_TIMEOUT", "2"],
        [at(3), "SMOKE_TIMEOUT", "1"],
    ]
    assert [record[5] for record in _records(box.log)] == ["0", "0"]


def test_records_are_appended_one_line_each(box: Box) -> None:
    for code in (0, 5):
        bounded(box, "CHECK_TIMEOUT", 100, "sh", "-c", f"exit {code}")
    assert [record[5] for record in _records(box.log)] == ["0", "5"]


def test_the_log_is_under_xdg_state_home_when_it_is_set(box: Box) -> None:
    other = box.path / "elsewhere"
    run = bounded(box, "CHECK_TIMEOUT", 100, "true", XDG_STATE_HOME=str(other))
    assert run.code == 0
    assert len(_records(other / "gora" / "limits.log")) == 1
    assert not box.log.exists()


@pytest.mark.parametrize("state", [None, "", "relative/state"])
def test_the_log_is_under_home_when_xdg_state_home_is_unset_empty_or_relative(
    box: Box, state: str | None
) -> None:
    env = box.env()
    if state is None:
        del env["XDG_STATE_HOME"]
    else:
        env["XDG_STATE_HOME"] = state
    run = _run([BOUNDED, "CHECK_TIMEOUT", 100, "true"], env, cwd=box.path)
    assert run.code == 0
    assert len(_records(box.path / ".local" / "state" / "gora" / "limits.log")) == 1
    assert not (box.path / "relative").exists()


def _unwritable(box: Box, case: str) -> dict[str, str]:
    """An environment in which the record cannot be written, whoever runs it (root too)."""
    env = box.env()
    if case == "no home":
        del env["HOME"], env["XDG_STATE_HOME"]
    elif case == "a file where the directory goes":
        (box.path / "state").write_text("", encoding="utf-8")
    elif case == "the log is a directory":
        box.log.mkdir(parents=True)
    return env


@pytest.mark.parametrize(
    "case", ["no home", "a file where the directory goes", "the log is a directory"]
)
@pytest.mark.parametrize(("command", "code"), [(["true"], 0), (["sh", "-c", "exit 6"], 6)])
def test_a_record_that_cannot_be_written_is_said_and_changes_nothing_else(
    box: Box, case: str, command: list[str], code: int
) -> None:
    env = _unwritable(box, case)
    run = _run([BOUNDED, "VERIFY_TIMEOUT", 100, *command], env, cwd=box.path)
    assert run.code == code
    assert run.lines[-1] == NOT_RECORDED
    assert str(box.path) not in run.err, "the sentence names no path"
    match = LINE.match(run.lines[-2])
    assert match and (match["name"], match["sentence"]) == ("VERIFY_TIMEOUT", "")
    assert len(run.lines) == 2


def test_the_helper_alone_says_a_record_it_cannot_write_and_exits_0(box: Box) -> None:
    env = _unwritable(box, "a file where the directory goes")
    run = _run([REPORT, "line", "bounded.sh", "X_TIMEOUT", 1, 100, "1.0", "4", 3], env)
    assert run.code == 0
    assert run.lines == [run.lines[0], NOT_RECORDED]
    assert LINE.match(run.lines[0])


@pytest.mark.parametrize("code", ["", "x", "1.5", "-1", "0x1"])
def test_a_line_whose_code_is_no_whole_number_is_refused_and_records_nothing(
    box: Box, code: str
) -> None:
    run = _run([REPORT, "line", "bounded.sh", "X_TIMEOUT", 1, 100, "1.0", "4", code], box.env())
    assert run.code == 64
    assert not box.log.exists()


def test_a_run_with_the_suites_environment_records_under_the_tests_directory(
    box: Box, tmp_path: Path
) -> None:
    """No box: the environment every test has (conftest ``_isolated_home``) keeps the record in
    the test's own directory, never in the machine's log."""
    state = Path(os.environ["XDG_STATE_HOME"])
    assert state.is_relative_to(tmp_path)
    boxed = box.env()
    env = {
        **os.environ,
        **{key: boxed[key] for key in ("PATH", "LIMIT_REPORT_LOADAVG", "FAKE_DIR")},
    }
    run = _run([BOUNDED, "CHECK_TIMEOUT", 100, "true"], env)
    assert run.code == 0
    assert len(_records(state / "gora" / "limits.log")) == 1


def test_a_run_on_the_real_clock_prints_the_line_in_its_form(box: Box) -> None:
    """No date shim: the line and the record have their form, and no duration is asserted."""
    (box.bin / "date").unlink()
    run = bounded(box, "CHECK_TIMEOUT", 100, "true")
    assert run.code == 0
    assert run.report["name"] == "CHECK_TIMEOUT"
    [record] = _records(box.log)
    assert FINISHED.match(record[0]), record
    assert record[1:] == ["CHECK_TIMEOUT", run.report["took"], "1.50", "8", "0"]


# --- the one helper -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nothing"],
        ["check", "X"],
        ["sample", "x"],
        ["elapsed"],
        ["elapsed", "1"],
        ["elapsed", "1", "2", "3"],
        ["line", "a", "B", "1", "2", "3"],
        ["line", "a", "B", "1", "2", "3", "4"],  # no CODE
        ["line", "a", "B", "1", "2", "3", "4", "0", "extra"],
    ],
)
def test_the_helper_refuses_a_call_it_does_not_know(box: Box, argv: list[str]) -> None:
    assert _run([REPORT, *argv], box.env()).code == 64


def test_the_helper_prints_unknown_for_a_load_or_cpus_it_was_given_badly(box: Box) -> None:
    run = _run([REPORT, "line", "bounded.sh", "X", 1, 100, "oops", "-3", 0], box.env())
    assert run.code == 0
    assert (run.report["load"], run.report["cpus"]) == ("unknown", "unknown")


def test_every_script_is_executable_and_parses() -> None:
    for script in (BOUNDED, REPORT, RUN_CONTAINER, PULL):
        assert os.access(script, os.X_OK), script
        done = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, timeout=30, check=False
        )
        assert done.returncode == 0, done.stderr

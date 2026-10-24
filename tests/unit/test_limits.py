"""Every limit of TESTING rule 8 is in its table, and an outer limit is above its inner ones.

Read from the files themselves, offline (TESTING rule 6): the Makefile's ``*_TIMEOUT`` limits,
every job's ``timeout-minutes`` in ``.github/workflows/`` and the table of TESTING rule 8. The
table is held to its sources, so a limit changed in one place and not in the other is red; a
limit that is not at least 10x the slowest of its measured runs, or was measured on a machine
that was not at rest, is red as well. Each check is a function of the parsed text, and each is
shown red here on a defect of its class (ADR-0016 rule 1) before it is trusted on the real files.
"""

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
TESTING = ROOT / "TESTING.md"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# A pull is bounded by the network, not by a run: it is no row of the table (TESTING rule 8).
NOT_HELD_TO_10X = frozenset({"PULL_TIMEOUT"})
# The outer limit, then the limits it contains: the inner one ends a hung run first.
NESTED = [
    ("VERIFY_EXPORT_TIMEOUT", "VERIFY_TIMEOUT"),
    ("VERIFY_LINUX_TIMEOUT", "BEHAVIOUR_TIMEOUT"),
    ("RED_FIRST_TIMEOUT", "DOCKER_BUILD_TIMEOUT"),
    ("ci.yml:check", "CHECK_TIMEOUT"),
    ("ci.yml:test", "BEHAVIOUR_TIMEOUT"),
]
# The rows measured on this machine carry the load and the CPUs the report printed; a job of the
# code host carries none. The release workflow runs on a tag: its rows are its last run, dated,
# and a job added since has no run to measure (TESTING says so in its row).
LAST_RUN_ONLY = frozenset({"release.yml"})
MINIMUM_RUNS = 3

HEADER = "| Limit | Value | Date | Runs | Slowest | Load | CPUs | Run |"
ROW = re.compile(
    r"^\| `(?P<limit>[^`]+)` \| (?P<value>\d+) (?P<unit>s|min) \| (?P<date>\d{4}-\d{2}-\d{2}) "
    r"\| (?P<runs>\d+) \| (?P<slowest>\d+) s \| (?P<load>\d+(?:\.\d+)?|-) \| (?P<cpus>\d+|-) "
    r"\| (?P<run>[^|]+) \|$"
)
LIMIT = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*_TIMEOUT) \?= (?P<seconds>\d+)\s*$", re.MULTILINE)
# A use of the two macros, and any other use of the two scripts' variables.
CALL = re.compile(r"\$\(call (?P<macro>bounded|gate_run),(?P<args>[^)]*)\)")
SCRIPT_VARIABLE = re.compile(r"\$\((?:BOUNDED|GATE_RUN)\)")
MACRO_DEFINITION = re.compile(r"^(?:bounded|gate_run) = .*$", re.MULTILINE)


@dataclass(frozen=True)
class Row:
    limit: str
    seconds: int
    measured: str
    runs: int
    slowest: int
    load: str
    cpus: str


# --- reading -------------------------------------------------------------------------------


def makefile_limits(text: str) -> dict[str, int]:
    """Every ``NAME_TIMEOUT ?= seconds`` of the Makefile, in seconds."""
    return {m["name"]: int(m["seconds"]) for m in LIMIT.finditer(text)}


def workflow_limits(workflows: dict[str, dict[Any, Any]]) -> tuple[dict[str, int], list[str]]:
    """Each job's ``timeout-minutes`` as ``file:job`` in seconds, and the jobs that have none."""
    limits: dict[str, int] = {}
    missing: list[str] = []
    for name, document in workflows.items():
        for job, body in (document.get("jobs") or {}).items():
            key = f"{name}:{job}"
            minutes = body.get("timeout-minutes")
            if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
                limits[key] = minutes * 60
            else:
                missing.append(key)
    return limits, missing


def table_rows(text: str) -> tuple[list[Row], list[str]]:
    """The rows of the table that starts at HEADER, and the lines of it that are not rows."""
    lines = [line.strip() for line in text.splitlines()]
    if HEADER not in lines:
        return [], [f"no table with the header {HEADER}"]
    rows: list[Row] = []
    bad: list[str] = []
    for line in lines[lines.index(HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        match = ROW.match(line)
        if not match:
            bad.append(line)
            continue
        seconds = int(match["value"]) * (60 if match["unit"] == "min" else 1)
        rows.append(
            Row(
                match["limit"],
                seconds,
                match["date"],
                int(match["runs"]),
                int(match["slowest"]),
                match["load"],
                match["cpus"],
            )
        )
    return rows, bad


# --- the checks ----------------------------------------------------------------------------


def table_problems(rows: list[Row], bad: list[str], limits: dict[str, int]) -> list[str]:
    """The table against its sources: Makefile and workflows (``limits``), one row for each."""
    problems = [f"not a row of the table: {line}" for line in bad]
    held = {name: seconds for name, seconds in limits.items() if name not in NOT_HELD_TO_10X}
    seen: set[str] = set()
    for row in rows:
        if row.limit in seen:
            problems.append(f"{row.limit}: two rows")
        seen.add(row.limit)
        if row.limit not in held:
            problems.append(f"{row.limit}: the table names a limit that has no source")
            continue
        if row.seconds != held[row.limit]:
            problems.append(
                f"{row.limit}: the table says {row.seconds} s, its source {held[row.limit]} s"
            )
        problems += _measurement_problems(row)
    problems += [f"{name}: a limit with no row in the table" for name in held if name not in seen]
    return problems


def _measurement_problems(row: Row) -> list[str]:
    problems: list[str] = []
    needed = math.ceil(row.slowest * 10 / 60) * 60
    if row.seconds < needed:
        problems.append(
            f"{row.limit}: {row.seconds} s is below 10x its slowest run of {row.slowest} s,"
            f" which is {needed} s rounded up to a whole minute"
        )
    file = row.limit.partition(":")[0]
    if row.runs < (0 if file in LAST_RUN_ONLY else MINIMUM_RUNS):
        problems.append(f"{row.limit}: {row.runs} runs measured")
    try:
        date.fromisoformat(row.measured)
    except ValueError:
        problems.append(f"{row.limit}: {row.measured} is no date")
    if ":" in row.limit:
        if (row.load, row.cpus) != ("-", "-"):
            problems.append(f"{row.limit}: a job of the code host has no load and CPUs of ours")
    elif "-" in (row.load, row.cpus):
        problems.append(f"{row.limit}: measured here, so with the load and CPUs of its line")
    elif float(row.load) >= int(row.cpus):
        problems.append(
            f"{row.limit}: measured at a load of {row.load} on {row.cpus} CPUs, not at rest"
        )
    return problems


def nesting_problems(limits: dict[str, int]) -> list[str]:
    """An outer limit is above the limits it contains."""
    problems: list[str] = []
    for outer, inner in NESTED:
        if outer not in limits or inner not in limits:
            problems.append(f"{outer} and {inner}: a limit of the nesting has no source")
        elif limits[outer] <= limits[inner]:
            problems.append(
                f"{outer} ({limits[outer]} s) is not above {inner} ({limits[inner]} s):"
                " the outer limit would end a hung run before the inner one"
            )
    return problems


def macro_problems(makefile: str, limits: dict[str, int]) -> list[str]:
    """Every bounded run of the Makefile is named by a limit of it, through the two macros."""
    problems: list[str] = []
    names = set(makefile_limits(makefile)) - NOT_HELD_TO_10X
    code = "\n".join(line for line in makefile.splitlines() if not line.lstrip().startswith("#"))
    if SCRIPT_VARIABLE.search(MACRO_DEFINITION.sub("", code)):
        problems.append(
            "a run bounded by $(BOUNDED) or $(GATE_RUN), not by the macros that name it"
        )
    used: set[str] = set()
    for call in CALL.finditer(code):
        name = call["args"].split(",")[-1].strip()
        used.add(name)
        if name not in limits:
            problems.append(
                f"$(call {call['macro']},{call['args']}): {name} is no limit of the Makefile"
            )
    problems += [f"{name}: a limit that no run is bounded by" for name in sorted(names - used)]
    return problems


# --- the real files -------------------------------------------------------------------------


def _real_limits() -> dict[str, int]:
    makefile = makefile_limits(MAKEFILE.read_text(encoding="utf-8"))
    documents = {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) for path in WORKFLOWS}
    jobs, _ = workflow_limits(documents)
    return {**makefile, **jobs}


def test_the_table_holds_every_limit_of_the_makefile_and_the_workflows_and_no_other() -> None:
    rows, bad = table_rows(TESTING.read_text(encoding="utf-8"))
    assert table_problems(rows, bad, _real_limits()) == []


def test_every_job_of_every_workflow_has_a_timeout() -> None:
    documents = {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) for path in WORKFLOWS}
    limits, missing = workflow_limits(documents)
    assert missing == []
    assert limits, "no job found: the workflows were not read"


def test_an_outer_limit_is_above_the_limits_it_contains() -> None:
    assert nesting_problems(_real_limits()) == []


def test_every_bounded_run_is_named_by_its_limit_through_the_macros() -> None:
    assert macro_problems(MAKEFILE.read_text(encoding="utf-8"), _real_limits()) == []


def test_the_makefile_is_read_whole() -> None:
    limits = makefile_limits(MAKEFILE.read_text(encoding="utf-8"))
    assert {"VERIFY_TIMEOUT", "VERIFY_LINUX_TIMEOUT", "SMOKE_TIMEOUT", "PULL_TIMEOUT"} <= set(
        limits
    )
    assert all(seconds > 0 for seconds in limits.values())


# --- each check, red on a defect of its class ----------------------------------------------

LIMITS = {
    "VERIFY_TIMEOUT": 600,
    "VERIFY_EXPORT_TIMEOUT": 720,
    "PULL_TIMEOUT": 300,
    "ci.yml:check": 600,
}


def _row(
    limit: str,
    seconds: int,
    *,
    measured: str = "2026-10-24",
    runs: int = 3,
    slowest: int = 40,
    load: str = "2.0",
    cpus: str = "10",
) -> Row:
    if ":" in limit and (load, cpus) == ("2.0", "10"):
        load = cpus = "-"
    return Row(limit, seconds, measured, runs, slowest, load, cpus)


def _table(*changes: Row) -> list[Row]:
    """The rows that agree with LIMITS, each replaced by the change that names its limit."""
    rows = {
        "VERIFY_TIMEOUT": _row("VERIFY_TIMEOUT", 600),
        "VERIFY_EXPORT_TIMEOUT": _row("VERIFY_EXPORT_TIMEOUT", 720),
        "ci.yml:check": _row("ci.yml:check", 600),
    }
    rows.update({row.limit: row for row in changes})
    return list(rows.values())


def test_a_table_that_agrees_with_its_sources_is_green() -> None:
    assert table_problems(_table(), [], LIMITS) == []


def test_a_limit_changed_in_the_makefile_and_not_in_the_table_is_red() -> None:
    problems = table_problems(_table(), [], {**LIMITS, "VERIFY_TIMEOUT": 660})
    assert problems == ["VERIFY_TIMEOUT: the table says 600 s, its source 660 s"]


def test_a_timeout_changed_in_a_workflow_and_not_in_the_table_is_red() -> None:
    problems = table_problems(_table(), [], {**LIMITS, "ci.yml:check": 900})
    assert problems == ["ci.yml:check: the table says 600 s, its source 900 s"]


def test_a_limit_with_no_row_is_red() -> None:
    problems = table_problems(_table(), [], {**LIMITS, "RED_FIRST_TIMEOUT": 1500})
    assert problems == ["RED_FIRST_TIMEOUT: a limit with no row in the table"]


def test_a_row_with_no_source_is_red_and_so_is_the_pull() -> None:
    rows = _table(_row("GONE_TIMEOUT", 60), _row("PULL_TIMEOUT", 300))
    assert table_problems(rows, [], LIMITS) == [
        "GONE_TIMEOUT: the table names a limit that has no source",
        "PULL_TIMEOUT: the table names a limit that has no source",
    ]


def test_two_rows_for_one_limit_are_red() -> None:
    rows = [*_table(), _row("VERIFY_TIMEOUT", 600)]
    assert "VERIFY_TIMEOUT: two rows" in table_problems(rows, [], LIMITS)


def test_a_line_that_is_not_a_row_is_red() -> None:
    assert table_problems(_table(), ["| `X` | 600 | soon |"], LIMITS) == [
        "not a row of the table: | `X` | 600 | soon |"
    ]


def test_a_limit_below_10x_its_slowest_run_rounded_up_to_a_minute_is_red() -> None:
    assert table_problems(_table(_row("VERIFY_TIMEOUT", 600, slowest=60)), [], LIMITS) == []
    slow = _row("VERIFY_TIMEOUT", 600, slowest=61)
    problems = table_problems(_table(slow), [], LIMITS)
    assert len(problems) == 1
    assert "below 10x its slowest run of 61 s, which is 660 s" in problems[0]


def test_a_run_measured_with_the_load_at_or_above_the_cpus_is_red() -> None:
    for load in ("10", "10.2", "17.5"):
        problems = table_problems(_table(_row("VERIFY_TIMEOUT", 600, load=load)), [], LIMITS)
        assert problems == [f"VERIFY_TIMEOUT: measured at a load of {load} on 10 CPUs, not at rest"]
    assert table_problems(_table(_row("VERIFY_TIMEOUT", 600, load="9.99")), [], LIMITS) == []


def test_fewer_than_three_runs_are_red_except_for_a_release_job() -> None:
    few = _table(_row("VERIFY_TIMEOUT", 600, runs=2))
    assert table_problems(few, [], LIMITS) == ["VERIFY_TIMEOUT: 2 runs measured"]
    limits = {**LIMITS, "release.yml:build": 1800}
    last = _table(_row("release.yml:build", 1800, runs=1))
    assert table_problems(last, [], limits) == []
    never = _table(_row("release.yml:build", 1800, runs=0, slowest=0))
    assert table_problems(never, [], limits) == []


def test_a_row_without_a_date_or_with_a_load_missing_is_red() -> None:
    assert (
        "no date"
        in table_problems(_table(_row("VERIFY_TIMEOUT", 600, measured="2026-13-40")), [], LIMITS)[0]
    )
    no_load = _table(_row("VERIFY_TIMEOUT", 600, load="-"))
    assert "with the load and CPUs of its line" in table_problems(no_load, [], LIMITS)[0]
    host_load = _table(_row("ci.yml:check", 600, load="1.0", cpus="4"))
    assert "no load and CPUs of ours" in table_problems(host_load, [], LIMITS)[0]


def test_the_rows_are_read_from_the_table_and_only_from_it() -> None:
    text = (
        "prose | with a pipe\n"
        f"     {HEADER}\n"
        "     | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "     | `CHECK_TIMEOUT` | 3 min | 2026-10-24 | 3 | 17 s | 1.5 | 10 | `make check` |\n"
        "     | `ci.yml:check` | 10 min | 2026-10-24 | 5 | 17 s | - | - | job |\n"
        "\n"
        "     | `NOT_TIMEOUT` | 1 s | 2026-10-24 | 3 | 1 s | 1.0 | 4 | after the table |\n"
    )
    rows, bad = table_rows(text)
    assert bad == []
    assert [(row.limit, row.seconds) for row in rows] == [
        ("CHECK_TIMEOUT", 180),
        ("ci.yml:check", 600),
    ]
    assert table_rows("no table here")[1] == [f"no table with the header {HEADER}"]


def test_an_equal_or_lower_outer_limit_is_red() -> None:
    limits = {
        "VERIFY_EXPORT_TIMEOUT": 720,
        "VERIFY_TIMEOUT": 600,
        "VERIFY_LINUX_TIMEOUT": 600,
        "BEHAVIOUR_TIMEOUT": 600,
        "RED_FIRST_TIMEOUT": 1500,
        "DOCKER_BUILD_TIMEOUT": 300,
        "ci.yml:check": 600,
        "CHECK_TIMEOUT": 180,
        "ci.yml:test": 900,
    }
    assert nesting_problems(limits) == [
        "VERIFY_LINUX_TIMEOUT (600 s) is not above BEHAVIOUR_TIMEOUT (600 s):"
        " the outer limit would end a hung run before the inner one"
    ]
    assert nesting_problems({**limits, "VERIFY_LINUX_TIMEOUT": 601}) == []
    lower = nesting_problems({**limits, "VERIFY_LINUX_TIMEOUT": 601, "ci.yml:test": 500})
    assert len(lower) == 1 and lower[0].startswith("ci.yml:test (500 s) is not above")
    gone = {name: seconds for name, seconds in limits.items() if name != "CHECK_TIMEOUT"}
    assert any("has no source" in problem for problem in nesting_problems(gone))


def test_a_job_without_a_timeout_is_found() -> None:
    documents: dict[str, dict[Any, Any]] = {
        "a.yml": {"jobs": {"one": {"timeout-minutes": 5}, "two": {"runs-on": "x"}}},
        "b.yml": {"jobs": {"three": {"timeout-minutes": 0}, "four": {"timeout-minutes": "5"}}},
    }
    limits, missing = workflow_limits(documents)
    assert limits == {"a.yml:one": 300}
    assert missing == ["a.yml:two", "b.yml:three", "b.yml:four"]


MAKEFILE_TEXT = """\
BOUNDED := scripts/gate/bounded.sh
GATE_RUN := scripts/gate/run-container.sh
bounded = $(BOUNDED) $(1) $($(1))
gate_run = $(GATE_RUN) $(1) $(2) $($(2))
VERIFY_TIMEOUT ?= 600
SMOKE_TIMEOUT ?= 60
PULL_TIMEOUT ?= 300
verify:
\t@exec $(call bounded,VERIFY_TIMEOUT) $(MAKE) verify-steps
smoke:
\t@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only
"""


def test_a_makefile_that_names_every_run_by_its_limit_is_green() -> None:
    limits = makefile_limits(MAKEFILE_TEXT)
    assert limits == {"VERIFY_TIMEOUT": 600, "SMOKE_TIMEOUT": 60, "PULL_TIMEOUT": 300}
    assert macro_problems(MAKEFILE_TEXT, limits) == []


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        (
            "$(call bounded,VERIFY_TIMEOUT)",
            "$(BOUNDED) $(VERIFY_TIMEOUT)",
            "a run bounded by $(BOUNDED) or $(GATE_RUN), not by the macros that name it",
        ),
        (
            "$(call gate_run,image-smoke,SMOKE_TIMEOUT)",
            "$(GATE_RUN) image-smoke $(SMOKE_TIMEOUT)",
            "a run bounded by $(BOUNDED) or $(GATE_RUN), not by the macros that name it",
        ),
        (
            "$(call bounded,VERIFY_TIMEOUT)",
            "$(call bounded,VERIFY_TIMEOUTS)",
            "$(call bounded,VERIFY_TIMEOUTS): VERIFY_TIMEOUTS is no limit of the Makefile",
        ),
        (
            "$(call gate_run,image-smoke,SMOKE_TIMEOUT)",
            "$(call gate_run,image-smoke,SMOKE)",
            "$(call gate_run,image-smoke,SMOKE): SMOKE is no limit of the Makefile",
        ),
    ],
)
def test_a_run_not_named_by_its_limit_is_red(old: str, new: str, problem: str) -> None:
    text = MAKEFILE_TEXT.replace(old, new)
    assert problem in macro_problems(text, makefile_limits(text))


def test_a_limit_that_no_run_is_bounded_by_is_red() -> None:
    text = MAKEFILE_TEXT.replace(
        "\t@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only\n", ""
    )
    assert macro_problems(text, makefile_limits(text)) == [
        "SMOKE_TIMEOUT: a limit that no run is bounded by"
    ]

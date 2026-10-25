"""Rule 8's data is held to its sources (TESTING rule 8), with the code ``make limits`` runs.

``scripts/gate/limits.py`` owns every function on the data: reading the Makefile's
``*_TIMEOUT`` limits and every job's ``timeout-minutes``, reading the data and the machine's log,
the checks, the collection, the selection and the writing. This test imports it by path, so the
target and the test are one implementation, and holds ``scripts/gate/limits.tsv`` to the
Makefile and the workflows offline (TESTING rule 6): each limit at least 10x its slowest run,
local and CI together; enough runs of each; a run on this machine at rest; the nesting; the
tool's two tables against the files. Each check is shown red here on a defect of its class
(ADR-0016 rule 1) before it is trusted on the real files. The tool itself runs end to end in a
tmp root, with ``gh`` a shim on ``PATH`` and the log under the test's own ``HOME``: no network.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "gate" / "limits.py"


def _import() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gate_limits", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


limits = _import()
Record = limits.Record
Run = limits.Run
Sources = limits.Sources
StopError = limits.StopError


# --- the real files ------------------------------------------------------------------------


def _sources() -> Any:
    return limits.read_sources(ROOT)


def _data() -> tuple[list[Any], list[str]]:
    records, bad = limits.parse_data((ROOT / limits.DATA).read_text(encoding="utf-8"))
    return list(records), list(bad)


def test_the_data_is_records_in_their_form() -> None:
    records, bad = _data()
    assert bad == []
    assert records, "no record read: the data was not read"


def test_the_data_is_sorted_and_holds_no_line_twice() -> None:
    records, _ = _data()
    assert limits.order_problems(records) == []


def test_the_data_holds_every_limit_of_the_sources_to_its_runs() -> None:
    records, _ = _data()
    assert limits.data_problems(records, _sources()) == []


def test_every_job_of_every_workflow_has_a_timeout() -> None:
    found, missing = limits.workflow_limits(_sources().workflows)
    assert missing == []
    assert found, "no job found: the workflows were not read"


def test_an_outer_limit_is_above_the_limits_it_contains() -> None:
    assert limits.nesting_problems(_sources().limits) == []


def test_every_bounded_run_is_named_by_its_limit_through_the_macros() -> None:
    sources = _sources()
    assert limits.macro_problems(sources.makefile, sources.limits) == []


def test_the_tools_tables_are_those_of_the_workflows() -> None:
    assert limits.table_problems(_sources()) == []


def test_the_sources_are_read_whole() -> None:
    sources = _sources()
    assert {"VERIFY_TIMEOUT", "VERIFY_LINUX_TIMEOUT", "SMOKE_TIMEOUT", "PULL_TIMEOUT"} <= set(
        sources.makefile_limits
    )
    assert all(seconds > 0 for seconds in sources.limits.values())
    assert set(limits.derived_ci_limits(sources)) == {
        "BEHAVIOUR_TIMEOUT",
        "CHECK_TIMEOUT",
        "SMOKE_TIMEOUT",
    }, "the make targets of the workflows and their action were not read"
    assert "./.github/actions/build-image" in sources.actions


# --- each check, red on a defect of its class ----------------------------------------------

MAKEFILE_TEXT = """\
BOUNDED := scripts/gate/bounded.sh
GATE_RUN := scripts/gate/run-container.sh
bounded = $(BOUNDED) $(1) $($(1))
gate_run = $(GATE_RUN) $(1) $(2) $($(2))
VERIFY_TIMEOUT ?= 600
CHECK_TIMEOUT ?= 600
BEHAVIOUR_TIMEOUT ?= 600
SMOKE_TIMEOUT ?= 60
PULL_TIMEOUT ?= 300
verify:
\t@exec $(call bounded,VERIFY_TIMEOUT) $(MAKE) verify-steps
check: ## source checks
\t@exec $(call bounded,CHECK_TIMEOUT) $(MAKE) check-steps
behaviour:
\t@exec $(call bounded,BEHAVIOUR_TIMEOUT) $(MAKE) behaviour-steps
image-smoke:
\t@scripts/gate/pull.sh $(IMAGE) "$(PLATFORM)" $(PULL_TIMEOUT)
\t@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only
"""


def _workflows() -> dict[str, dict[str, Any]]:
    return {
        "ci.yml": {
            "jobs": {
                "check": {"name": "check", "timeout-minutes": 10, "steps": [{"run": "make check"}]},
                "test": {
                    "name": "test (${{ matrix.platform }})",
                    "timeout-minutes": 30,
                    "steps": [{"uses": "actions/checkout@x"}, {"run": "make behaviour"}],
                },
                "image": {
                    "name": "image (${{ matrix.platform }})",
                    "timeout-minutes": 20,
                    "steps": [{"uses": "./.github/actions/build-image"}],
                },
            }
        },
        "release.yml": {
            "jobs": {
                "guard": {"timeout-minutes": 5, "steps": [{"run": "echo guard"}]},
                "build": {
                    "name": "build (${{ matrix.platform }})",
                    "timeout-minutes": 30,
                    "steps": [{"uses": "./.github/actions/build-image/"}],
                },
            }
        },
    }


ACTIONS = {
    "./.github/actions/build-image": {
        "runs": {"steps": [{"run": 'make image-smoke IMAGE="$IMAGE@$DIGEST"'}]}
    }
}


def _sample(**changes: Any) -> Any:
    return Sources(
        changes.get("makefile", MAKEFILE_TEXT),
        changes.get("workflows", _workflows()),
        changes.get("actions", ACTIONS),
    )


def _at(n: int) -> str:
    return f"2026-10-{10 + n:02d}T10:00:00Z"


def _local(limit: str, seconds: int = 40, n: int = 0, load: str = "2.00", cpus: str = "10") -> Any:
    return Record(limit, _at(n), seconds, load, cpus, "-")


def _ci(limit: str, job: str, seconds: int = 20, n: int = 0, **printed: str) -> Any:
    if ":" in limit:
        load, cpus = printed.get("load", "-"), printed.get("cpus", "-")
    else:
        load, cpus = printed.get("load", "0.50"), printed.get("cpus", "4")
    return Record(limit, _at(n), seconds, load, cpus, job)


def _green() -> list[Any]:
    """Records that hold rule 8 against _sample(): three of every limit, five at most."""
    records = []
    for n in range(3):
        records += [_local(name, n=n) for name in ("VERIFY_TIMEOUT", "CHECK_TIMEOUT")]
        records += [_local("BEHAVIOUR_TIMEOUT", n=n), _local("SMOKE_TIMEOUT", 1, n=n)]
        records += [
            _ci("CHECK_TIMEOUT", "ci.yml:check", n=n),
            _ci("BEHAVIOUR_TIMEOUT", "ci.yml:test (linux/amd64)", n=n),
            _ci("SMOKE_TIMEOUT", "ci.yml:image (linux/arm64)", 1, n=n),
            _ci("ci.yml:check", "ci.yml:check", n=n),
            _ci("ci.yml:test", "ci.yml:test (linux/arm64)", n=n),
            _ci("ci.yml:image", "ci.yml:image (linux/amd64)", n=n),
        ]
    records.append(_ci("release.yml:build", "release.yml:build (linux/amd64)"))
    return sorted(records, key=Record.key)


def _without(records: list[Any], limit: str, job: str | None = None) -> list[Any]:
    return [r for r in records if not (r.limit == limit and (job is None or r.job == job))]


def test_records_that_hold_rule_8_are_green() -> None:
    assert limits.data_problems(_green(), _sample()) == []
    assert limits.order_problems(_green()) == []


def test_a_record_of_no_limit_of_the_sources_is_red_and_so_is_the_pull() -> None:
    records = [*_green(), _local("GONE_TIMEOUT", n=4), _local("PULL_TIMEOUT", n=4)]
    assert limits.data_problems(records, _sample()) == [
        f"GONE_TIMEOUT (-, {_at(4)}): a record of no limit of the sources",
        f"PULL_TIMEOUT (-, {_at(4)}): a record of no limit of the sources",
    ]


def test_a_limit_with_fewer_than_three_records_is_red() -> None:
    records = [r for r in _green() if not (r.limit == "VERIFY_TIMEOUT" and r.finished == _at(2))]
    assert limits.data_problems(records, _sample()) == ["VERIFY_TIMEOUT: 2 records, fewer than 3"]
    jobs = _without(_green(), "ci.yml:image")
    assert limits.data_problems(jobs, _sample()) == ["ci.yml:image: 0 records, fewer than 3"]


def test_a_job_of_the_release_workflow_may_have_one_record_or_none() -> None:
    assert limits.data_problems(_without(_green(), "release.yml:build"), _sample()) == []


def test_a_limit_ci_runs_needs_three_runs_here_and_three_in_each_workflow() -> None:
    records = _without(_green(), "BEHAVIOUR_TIMEOUT", "ci.yml:test (linux/amd64)")
    assert limits.data_problems(records, _sample()) == [
        "BEHAVIOUR_TIMEOUT: 0 runs in ci.yml, fewer than 3"
    ]
    records = _without(_green(), "CHECK_TIMEOUT", "-")
    assert limits.data_problems(records, _sample()) == [
        "CHECK_TIMEOUT: 0 runs on this machine, fewer than 3"
    ]


def test_a_limit_ci_runs_needs_no_runs_from_the_release_workflow() -> None:
    records = [*_green(), _ci("SMOKE_TIMEOUT", "release.yml:build (linux/amd64)", 1)]
    assert limits.data_problems(sorted(records, key=Record.key), _sample()) == []


def test_more_records_of_a_limit_and_job_than_the_selection_keeps_are_red() -> None:
    six = [*_green(), *(_local("VERIFY_TIMEOUT", n=n) for n in range(3, 6))]
    assert limits.data_problems(six, _sample()) == [
        "VERIFY_TIMEOUT (-): 6 records, more than the 5 kept"
    ]
    two = [*_green(), _ci("release.yml:build", "release.yml:build (linux/amd64)", n=1)]
    assert limits.data_problems(two, _sample()) == [
        "release.yml:build (release.yml:build (linux/amd64)): 2 records, more than the 1 kept"
    ]


@pytest.mark.parametrize(
    ("load", "cpus"), [("5.00", "10"), ("5", "10"), ("10.2", "10"), ("unknown", "10")]
)
def test_a_run_on_this_machine_not_at_rest_is_red(load: str, cpus: str) -> None:
    records = [*_green(), _local("VERIFY_TIMEOUT", n=4, load=load, cpus=cpus)]
    assert limits.data_problems(sorted(records, key=Record.key), _sample()) == [
        f"VERIFY_TIMEOUT (-, {_at(4)}): a run on this machine at load {load} on {cpus} CPUs,"
        " not at rest"
    ]


@pytest.mark.parametrize(("load", "cpus"), [("7.50", "4"), ("unknown", "unknown")])
def test_a_report_line_in_ci_has_no_rest_requirement(load: str, cpus: str) -> None:
    line = _ci("CHECK_TIMEOUT", "ci.yml:check", n=4, load=load, cpus=cpus)
    records = sorted([*_green(), line], key=Record.key)
    assert limits.data_problems(_without_oldest(records, line), _sample()) == []


def _without_oldest(records: list[Any], like: Any) -> list[Any]:
    """The records with the oldest of like's limit and job left out: one in, one out."""
    group = [r for r in records if (r.limit, r.job) == (like.limit, like.job)]
    return [r for r in records if r != min(group, key=lambda r: r.finished)]


def test_a_report_line_in_ci_without_its_load_or_of_a_job_that_does_not_run_it_is_red() -> None:
    dashed = _ci("CHECK_TIMEOUT", "ci.yml:check", n=4, load="-", cpus="-")
    problems = limits.data_problems([*_green(), dashed], _sample())
    assert (
        f"CHECK_TIMEOUT (ci.yml:check, {_at(4)}):"
        " a report line in CI has its load and CPUs as printed"
    ) in problems
    other = _ci("CHECK_TIMEOUT", "ci.yml:test (linux/amd64)", n=4)
    problems = limits.data_problems([*_green(), other], _sample())
    assert problems == [
        f"CHECK_TIMEOUT (ci.yml:test (linux/amd64), {_at(4)}): no job that runs the limit"
    ]


def test_a_jobs_own_record_with_a_load_or_of_another_job_is_red() -> None:
    loaded = _ci("ci.yml:check", "ci.yml:check", n=4, load="1.0", cpus="4")
    problems = limits.data_problems([*_green(), loaded], _sample())
    assert (
        f"ci.yml:check (ci.yml:check, {_at(4)}): a job's own record has no load and CPUs"
        in problems
    )
    other = _ci("ci.yml:check", "ci.yml:test (linux/amd64)", n=4)
    assert (
        f"ci.yml:check (ci.yml:test (linux/amd64), {_at(4)}): the job is not the one its"
        " limit names"
    ) in limits.data_problems([*_green(), other], _sample())


def test_a_limit_below_10x_its_slowest_run_rounded_up_to_a_minute_is_red() -> None:
    at = [*_green(), _local("VERIFY_TIMEOUT", 60, n=4)]
    assert limits.data_problems(sorted(at, key=Record.key), _sample()) == []
    slow = [*_green(), _local("VERIFY_TIMEOUT", 61, n=4)]
    assert limits.data_problems(sorted(slow, key=Record.key), _sample()) == [
        "VERIFY_TIMEOUT: 600 s is below 10x its slowest run of 61 s,"
        " 660 s rounded up to a whole minute"
    ]


def test_the_10x_is_of_the_slowest_run_not_the_fastest() -> None:
    records = [*_green(), _local("VERIFY_TIMEOUT", 61, n=4), _local("VERIFY_TIMEOUT", 10, n=5)]
    problems = limits.data_problems(sorted(records, key=Record.key), _sample())
    assert any("below 10x its slowest run of 61 s" in problem for problem in problems)


def test_a_limit_ci_runs_is_held_to_its_ci_runs_too() -> None:
    slow = _ci("BEHAVIOUR_TIMEOUT", "ci.yml:test (linux/amd64)", 61, n=4)
    records = _without_oldest(sorted([*_green(), slow], key=Record.key), slow)
    assert limits.data_problems(records, _sample()) == [
        "BEHAVIOUR_TIMEOUT: 600 s is below 10x its slowest run of 61 s,"
        " 660 s rounded up to a whole minute"
    ]


def test_unsorted_records_or_a_line_twice_are_red() -> None:
    records = _green()
    assert limits.order_problems(records[::-1]) == [
        f"{limits.DATA}: the records are not sorted by limit, job and finished"
    ]
    assert limits.order_problems([records[0], *records]) == [
        f"{limits.DATA}: a record is there twice"
    ]


# --- the data's form ------------------------------------------------------------------------


def _text(*lines: str) -> str:
    return "".join(f"{line}\n" for line in (limits.HEADER, *lines))


def test_the_data_is_read_as_written() -> None:
    records = _green()
    text = "".join(f"{line}\n" for line in [limits.HEADER, *(r.line() for r in records)])
    assert limits.parse_data(text) == (records, [])


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("VERIFY_TIMEOUT\t2026-10-24T10:00:00Z\t40\t2.00\t10", "5 fields, not 6"),
        ("verify\t2026-10-24T10:00:00Z\t40\t2.00\t10\t-", "'verify' is no limit's name"),
        ("ci.yml\t2026-10-24T10:00:00Z\t40\t-\t-\tci.yml:x", "'ci.yml' is no limit's name"),
        ("VERIFY_TIMEOUT\t2026-10-24 10:00:00\t40\t2.00\t10\t-", "is no UTC time to the second"),
        ("VERIFY_TIMEOUT\t2026-10-24T10:00:00.5Z\t40\t2.00\t10\t-", "is no UTC time"),
        ("VERIFY_TIMEOUT\t2026-10-24T10:00:00Z\t40.5\t2.00\t10\t-", "is no whole number"),
        ("VERIFY_TIMEOUT\t2026-10-24T10:00:00Z\t40\t2,00\t10\t-", "'2,00' is no load"),
        ("VERIFY_TIMEOUT\t2026-10-24T10:00:00Z\t40\t2.00\t0\t-", "'0' is no CPU count"),
        ("VERIFY_TIMEOUT\t2026-10-24T10:00:00Z\t40\t2.00\t10\ttest", "'test' is no job"),
    ],
)
def test_a_line_that_is_not_a_record_is_named_by_its_number(line: str, why: str) -> None:
    good = "VERIFY_TIMEOUT\t2026-10-24T09:00:00Z\t40\t2.00\t10\t-"
    records, bad = limits.parse_data(_text(good, line))
    assert len(records) == 1
    assert len(bad) == 1 and bad[0].startswith("line 3: ") and why in bad[0], bad


def test_data_without_its_header_is_red() -> None:
    _, bad = limits.parse_data("VERIFY_TIMEOUT\t2026-10-24T09:00:00Z\t40\t2.00\t10\t-\n")
    assert bad == ["the first line is not the header limit finished seconds load cpus job"]


# --- the machine's log and its runs ---------------------------------------------------------


def test_the_log_is_where_limit_report_sh_writes_it() -> None:
    assert limits.log_path({"XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path("/s/gora/limits.log")
    for state in ("", "relative"):
        found = limits.log_path({"XDG_STATE_HOME": state, "HOME": "/h"})
        assert found == Path("/h/.local/state/gora/limits.log")
    assert limits.log_path({"HOME": "/h"}) == Path("/h/.local/state/gora/limits.log")
    assert limits.log_path({"HOME": "h"}) is None
    assert limits.log_path({}) is None


def test_a_log_line_that_is_not_a_record_stops_the_tool_with_its_number() -> None:
    good = "2026-10-24T09:00:00Z\tVERIFY_TIMEOUT\t40\t2.00\t10\t0"
    assert limits.parse_log(good + "\n") == [
        Run("2026-10-24T09:00:00Z", "VERIFY_TIMEOUT", 40, "2.00", "10", 0)
    ]
    for bad in (good + "\textra", good.replace("\t0", "\tx"), good.replace("2.00", "high")):
        with pytest.raises(StopError, match="line 2 of this machine's log is not a record"):
            limits.parse_log(f"{good}\n{bad}\n")


def test_only_green_runs_at_rest_of_a_makefile_limit_are_collected() -> None:
    def run(name: str, code: int = 0, load: str = "2.00", cpus: str = "10") -> Any:
        return Run(_at(0), name, 30, load, cpus, code)

    runs = [
        run("VERIFY_TIMEOUT"),
        run("CHECK_TIMEOUT", code=1),
        run("CHECK_TIMEOUT", code=124),
        run("PULL_TIMEOUT"),
        run("GONE_TIMEOUT"),
        run("SMOKE_TIMEOUT", load="unknown"),
        run("SMOKE_TIMEOUT", cpus="unknown"),
        run("BEHAVIOUR_TIMEOUT", load="5.00"),
        run("BEHAVIOUR_TIMEOUT", load="10.00"),
    ]
    collected = limits.local_records(runs, _sample().makefile_limits)
    assert collected == [Record("VERIFY_TIMEOUT", _at(0), 30, "2.00", "10", "-")]


# --- at rest: one table of cases for both implementations ----------------------------------

REPORT = ROOT / "scripts" / "gate" / "limit-report.sh"
RULE_8 = "; above a tenth of the limit, TESTING rule 8 does not hold"
NOT_AT_REST = "; above a tenth of the limit, not at rest"


@pytest.mark.parametrize(
    ("load", "cpus", "rest"),
    [
        ("4.99", "10", True),
        ("5.00", "10", False),
        ("1.99", "4", True),
        ("2.00", "4", False),
        ("4.6", "10", True),
        ("0", "10", True),
        ("5", "10", False),
        ("unknown", "10", False),
        ("4.6", "unknown", False),
        ("4.999", "10", True),
        ("5.001", "10", False),
    ],
)
def test_at_rest_is_a_load_under_half_the_cpus_in_both_implementations(
    tmp_path: Path, load: str, cpus: str, rest: bool
) -> None:
    """limit-report.sh says which sentence; limits.py's at_rest selects and checks the runs."""
    assert limits.at_rest(load, cpus) is rest
    done = subprocess.run(
        [str(REPORT), "line", "bounded.sh", "X_TIMEOUT", "2", "10", load, cpus, "0"],
        env={"PATH": os.environ["PATH"], "XDG_STATE_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    line = done.stderr.splitlines()[0]
    assert line.endswith(RULE_8 if rest else NOT_AT_REST), line
    [record] = (tmp_path / "gora" / "limits.log").read_text(encoding="utf-8").splitlines()
    assert record.split("\t")[1:] == ["X_TIMEOUT", "2", load, cpus, "0"], "the record is unchanged"


# --- selection ------------------------------------------------------------------------------


def test_the_newest_five_of_each_limit_and_job_are_kept() -> None:
    seven = [_local("VERIFY_TIMEOUT", n=n) for n in range(7)]
    chosen = limits.select(seven, [], _sample())
    assert [r.finished for r in chosen] == [_at(n) for n in range(2, 7)]


def test_a_job_of_the_release_workflow_keeps_its_last_run() -> None:
    runs = [_ci("release.yml:build", "release.yml:build (linux/amd64)", n=n) for n in range(3)]
    assert limits.select(runs, [], _sample()) == [runs[-1]]


def test_the_file_keeps_what_no_one_ran_again_and_a_fresh_run_displaces_the_oldest() -> None:
    kept = [_local("VERIFY_TIMEOUT", n=n) for n in range(5)] + [_local("CHECK_TIMEOUT", n=0)]
    fresh = [_local("VERIFY_TIMEOUT", n=5), kept[3]]
    chosen = limits.select(fresh, kept, _sample())
    assert [(r.limit, r.finished) for r in chosen] == [
        ("CHECK_TIMEOUT", _at(0)),
        *(("VERIFY_TIMEOUT", _at(n)) for n in range(1, 6)),
    ]


def test_the_file_loses_what_the_sources_no_longer_measure() -> None:
    kept = [
        _local("GONE_TIMEOUT"),
        _local("PULL_TIMEOUT"),
        _local("VERIFY_TIMEOUT", load="12.0"),
        _ci("ci.yml:check", "ci.yml:renamed"),
        _ci("CHECK_TIMEOUT", "ci.yml:test (linux/amd64)"),
        _local("CHECK_TIMEOUT"),
    ]
    assert limits.select([], kept, _sample()) == [_local("CHECK_TIMEOUT")]


def test_selected_records_are_sorted_with_no_duplicate() -> None:
    records = _green()
    chosen = limits.select(records[::-1], records, _sample())
    assert chosen == records


# --- job names, as the code host shows them -------------------------------------------------


def test_a_job_name_matches_its_id_with_each_expression_any_text() -> None:
    names = _sample().names["ci.yml"]
    assert limits.match_job("ci.yml", "test (linux/amd64)", names) == "test"
    assert limits.match_job("ci.yml", "check", names) == "check"
    assert limits.match_job("ci.yml", "check (linux/amd64)", names) is None, "the whole name"
    assert limits.match_job("ci.yml", "lint", names) is None
    assert limits.match_job("release.yml", "guard", _sample().names["release.yml"]) == "guard"


def test_a_job_name_that_matches_two_job_ids_stops_the_tool() -> None:
    names = {"amd": "build (${{ matrix.a }})", "arm": "build (${{ matrix.b }})"}
    with pytest.raises(StopError, match='"build \\(x\\)" matches the jobs amd, arm'):
        limits.match_job("release.yml", "build (x)", names)


# --- the tool's two tables, against the files ----------------------------------------------


def test_the_ci_limits_are_derived_from_each_make_target_a_job_runs() -> None:
    assert limits.derived_ci_limits(_sample()) == {
        "BEHAVIOUR_TIMEOUT": ("ci.yml:test",),
        "CHECK_TIMEOUT": ("ci.yml:check",),
        "SMOKE_TIMEOUT": ("ci.yml:image", "release.yml:build"),
    }


def test_a_workflow_that_starts_running_another_bounded_target_is_red() -> None:
    line = "VERIFY_TIMEOUT: CI runs it in ci.yml:check, and the table of limits.py says none"
    assert line not in limits.table_problems(_sample())
    workflows = _workflows()
    workflows["ci.yml"]["jobs"]["check"]["steps"].append({"run": "make verify"})
    assert line in limits.table_problems(_sample(workflows=workflows))
    workflows["ci.yml"]["jobs"]["image"]["steps"] = []
    assert (
        "SMOKE_TIMEOUT: CI runs it in release.yml:build,"
        " and the table of limits.py says ci.yml:image, release.yml:build"
    ) in limits.table_problems(_sample(workflows=workflows))


def test_a_workflow_without_an_entry_in_the_run_table_is_red() -> None:
    workflows = {
        name: yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        for name in limits.RUNS
    }
    sources = _sample(makefile=_sources().makefile, workflows=workflows, actions=_sources().actions)
    assert limits.table_problems(sources) == []
    workflows["new.yml"] = {"jobs": {"x": {"timeout-minutes": 5}}}
    assert limits.table_problems(sources) == [
        "new.yml: a workflow with no entry in the run table of limits.py"
    ]
    del workflows["new.yml"], workflows["pr-title.yml"]
    assert limits.table_problems(sources) == [
        "pr-title.yml: an entry of the run table of limits.py with no workflow"
    ]


def test_a_make_target_named_in_prose_or_another_tool_is_no_run() -> None:
    workflows = _workflows()
    workflows["ci.yml"]["jobs"]["check"]["steps"] = [
        {"run": "cmake check; gmake verify; echo $(MAKE) verify; ./make verify"},
        {"run": "make reads GNUmakefile and makefile before Makefile"},
    ]
    derived = limits.derived_ci_limits(_sample(workflows=workflows))
    assert "CHECK_TIMEOUT" not in derived
    assert "VERIFY_TIMEOUT" not in derived
    assert set(derived) == {"BEHAVIOUR_TIMEOUT", "SMOKE_TIMEOUT"}


# --- the nesting and the macros, as before --------------------------------------------------


def test_an_equal_or_lower_outer_limit_is_red() -> None:
    nested = {
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
    assert limits.nesting_problems(nested) == [
        "VERIFY_LINUX_TIMEOUT (600 s) is not above BEHAVIOUR_TIMEOUT (600 s):"
        " the outer limit would end a hung run before the inner one"
    ]
    assert limits.nesting_problems({**nested, "VERIFY_LINUX_TIMEOUT": 601}) == []
    lower = limits.nesting_problems({**nested, "VERIFY_LINUX_TIMEOUT": 601, "ci.yml:test": 500})
    assert len(lower) == 1 and lower[0].startswith("ci.yml:test (500 s) is not above")
    gone = {name: seconds for name, seconds in nested.items() if name != "CHECK_TIMEOUT"}
    assert any("has no source" in problem for problem in limits.nesting_problems(gone))


def test_a_job_without_a_timeout_is_found() -> None:
    documents: dict[str, dict[Any, Any]] = {
        "a.yml": {"jobs": {"one": {"timeout-minutes": 5}, "two": {"runs-on": "x"}}},
        "b.yml": {"jobs": {"three": {"timeout-minutes": 0}, "four": {"timeout-minutes": "5"}}},
    }
    found, missing = limits.workflow_limits(documents)
    assert found == {"a.yml:one": 300}
    assert missing == ["a.yml:two", "b.yml:three", "b.yml:four"]


def test_a_makefile_that_names_every_run_by_its_limit_is_green() -> None:
    found = limits.makefile_limits(MAKEFILE_TEXT)
    assert found == {
        "VERIFY_TIMEOUT": 600,
        "CHECK_TIMEOUT": 600,
        "BEHAVIOUR_TIMEOUT": 600,
        "SMOKE_TIMEOUT": 60,
        "PULL_TIMEOUT": 300,
    }
    assert limits.macro_problems(MAKEFILE_TEXT, found) == []


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
    assert problem in limits.macro_problems(text, limits.makefile_limits(text))


def test_a_limit_that_no_run_is_bounded_by_is_red() -> None:
    text = MAKEFILE_TEXT.replace(
        "\t@exec $(call gate_run,image-smoke,SMOKE_TIMEOUT) --read-only\n", ""
    )
    assert limits.macro_problems(text, limits.makefile_limits(text)) == [
        "SMOKE_TIMEOUT: a limit that no run is bounded by"
    ]


# --- CI's report lines ----------------------------------------------------------------------


def test_report_lines_are_read_from_a_job_log_as_gh_prints_it() -> None:
    log = (
        "check\tSet up job\t2026-10-24T10:00:00.1Z Current runner version: '2.330.0'\n"
        "check\tSource checks\t2026-10-24T10:00:08.2Z bounded.sh: CHECK_TIMEOUT: 8 s of 180 s"
        " (5%), load 0.54 on 4 CPUs\n"
        "image\tSmoke\t2026-10-24T10:00:09.0Z run-container.sh: SMOKE_TIMEOUT: 1 s of 60 s (2%),"
        " load unknown on unknown CPUs\n"
        "check\tSource checks\t2026-10-24T10:00:08.3Z bounded.sh: PULL_TIMEOUT: 3 s of 300 s"
        " (1%), load 1.35 on 4 CPUs\n"
        "check\tx\t2026-10-24T10:00:09.0Z bounded.sh: CHECK_TIMEOUT: 30 s of 180 s (17%), load"
        " 4.00 on 4 CPUs; above a tenth of the limit, TESTING rule 8 does not hold\n"
    )
    assert limits.report_lines(log, "CHECK_TIMEOUT", "job") == [(8, "0.54", "4"), (30, "4.00", "4")]
    assert limits.report_lines(log, "SMOKE_TIMEOUT", "job") == [(1, "unknown", "unknown")]
    assert limits.report_lines(log, "BEHAVIOUR_TIMEOUT", "job") == []
    broken = "x\ty\t2026 bounded.sh: CHECK_TIMEOUT: 8 s of 180 s (5%), load 0,54 on 4 CPUs\n"
    with pytest.raises(StopError, match="line 1 is a report line of CHECK_TIMEOUT and no record"):
        limits.report_lines(broken, "CHECK_TIMEOUT", "job")


# --- gh, bounded ----------------------------------------------------------------------------


def _shim(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    gh.chmod(0o755)


def test_a_gh_that_fails_or_does_not_answer_stops_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    _shim(tmp_path / "bin", 'echo "HTTP 502: Bad Gateway" >&2\nexit 1\n')
    with pytest.raises(StopError, match="gh run list: exit 1: HTTP 502: Bad Gateway"):
        limits.run_gh(["run", "list"], cwd=tmp_path)
    _shim(tmp_path / "bin", "exec sleep 30\n")
    with pytest.raises(StopError, match="gh run list: no answer in 1 s"):
        limits.run_gh(["run", "list"], cwd=tmp_path, seconds=1)
    assert limits.GH_SECONDS == 60


# --- the tool, end to end in a tmp root ----------------------------------------------------

GH_SHIM = """\
echo "$*" >> "$GH_FAKE/calls"
if [ -n "${GH_FAKE_EXIT:-}" ]; then echo "HTTP 502" >&2; exit "$GH_FAKE_EXIT"; fi
case "$1 $2" in
  "run list")
    while [ $# -gt 0 ]; do
      if [ "$1" = --workflow ]; then file=$2; fi
      shift
    done
    cat "$GH_FAKE/list-$file.json"
    ;;
  "run view")
    run=$3 job= log=
    shift 3
    while [ $# -gt 0 ]; do
      case $1 in
        --job) job=$2 ;;
        --log) log=1 ;;
      esac
      shift
    done
    if [ -n "$log" ]; then cat "$GH_FAKE/log-$job.txt"; else cat "$GH_FAKE/jobs-$run.json"; fi
    ;;
  *) exit 64 ;;
esac
"""


class Tree:
    """A copy of the repository's sources and the tool, a fake CI and a log of this machine."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.fake = tmp_path / "gh"
        self.home = tmp_path / "home"
        self.bin = tmp_path / "bin"
        (self.root / "scripts" / "gate").mkdir(parents=True)
        shutil.copy(ROOT / "Makefile", self.root / "Makefile")
        shutil.copytree(ROOT / ".github", self.root / ".github")
        shutil.copy(TOOL, self.root / "scripts" / "gate" / "limits.py")
        self.fake.mkdir()
        self.home.mkdir()
        _shim(self.bin, GH_SHIM)
        self.sources = limits.read_sources(self.root)

    @property
    def data(self) -> Path:
        return self.root / Path(limits.DATA)

    @property
    def log(self) -> Path:
        return self.home / "state" / "gora" / "limits.log"

    def ci(self, seconds: int = 10, line: int = 1, *, quiet: frozenset[str] = frozenset()) -> None:
        """Green runs of each workflow: three, one for the release; each job lasts seconds, and
        each job that runs a Makefile limit prints its report line, unless its limit is quiet."""
        run_id, job_id = 100, 500
        for file, (_, count) in limits.RUNS.items():
            runs = []
            for n in range(min(count, 3)):
                run_id += 1
                runs.append({"databaseId": run_id})
                jobs = []
                for job, name in self.sources.names[file].items():
                    job_id += 1
                    shown = limits.EXPRESSION.sub("linux/amd64", name)
                    start = datetime(2026, 10, 20 + n, 10, tzinfo=UTC)
                    end = start + timedelta(seconds=seconds)
                    stamp = end.strftime("%Y-%m-%dT%H:%M:%SZ")
                    jobs.append(
                        {
                            "databaseId": job_id,
                            "name": shown,
                            "conclusion": "success",
                            "startedAt": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "completedAt": stamp,
                        }
                    )
                    printed = [
                        f"{shown}\tstep\t{stamp} bounded.sh: {limit}: {line} s of 60 s (2%),"
                        " load 0.50 on 4 CPUs"
                        for limit, where in limits.CI_LIMITS.items()
                        if f"{file}:{job}" in where and limit not in quiet
                    ]
                    (self.fake / f"log-{job_id}.txt").write_text(
                        "".join(f"{p}\n" for p in ["noise", *printed]), encoding="utf-8"
                    )
                (self.fake / f"jobs-{run_id}.json").write_text(
                    json.dumps({"jobs": jobs}), encoding="utf-8"
                )
            (self.fake / f"list-{file}.json").write_text(json.dumps(runs), encoding="utf-8")

    def runs(self, *lines: str, seconds: int = 1) -> None:
        """Three green runs at rest of each Makefile limit, then the lines given."""
        records = [
            f"2026-10-2{n}T09:00:00Z\t{name}\t{seconds}\t2.00\t10\t0"
            for name in self.sources.makefile_limits
            for n in range(3)
        ]
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_text("".join(f"{r}\n" for r in [*records, *lines]), encoding="utf-8")

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.root / "scripts" / "gate" / "limits.py")],
            cwd=self.root,
            env={
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "HOME": str(self.home),
                "XDG_STATE_HOME": str(self.home / "state"),
                "GH_FAKE": str(self.fake),
                **env,
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def records(self) -> list[Any]:
        records, bad = limits.parse_data(self.data.read_text(encoding="utf-8"))
        assert bad == []
        return list(records)


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


def test_the_tool_collects_selects_writes_and_holds_rule_8(tree: Tree) -> None:
    tree.ci()
    tree.runs()
    done = tree.run()
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[-1] == "limits: rule 8 holds"
    assert "limits: VERIFY_TIMEOUT: slowest 1 s of 3 runs; limit " in done.stdout
    assert "10x 60 s, 15x 60 s" in done.stdout
    records = tree.records()
    assert limits.order_problems(records) == []
    assert limits.data_problems(records, tree.sources) == []
    behaviour = [r for r in records if r.limit == "BEHAVIOUR_TIMEOUT"]
    assert sorted({r.job for r in behaviour}) == ["-", "ci.yml:test (linux/amd64)"]
    assert {r.job for r in records if r.limit == "release.yml:guard"} == {"release.yml:guard"}
    calls = (tree.fake / "calls").read_text(encoding="utf-8").splitlines()
    for file, (options, count) in limits.RUNS.items():
        want = f"run list --workflow {file} --status success --limit {count} --json databaseId"
        assert " ".join([want, *options]) in calls


def test_the_tool_keeps_from_the_file_what_no_one_ran_again(tree: Tree) -> None:
    tree.ci()
    tree.runs()
    old = [f"VERIFY_TIMEOUT\t2026-10-0{n}T09:00:00Z\t2\t1.00\t10\t-" for n in range(1, 5)]
    tree.data.write_text(_text(*old), encoding="utf-8")
    done = tree.run()
    assert done.returncode == 0, done.stdout + done.stderr
    kept = [r.finished for r in tree.records() if r.limit == "VERIFY_TIMEOUT"]
    assert kept == [
        "2026-10-03T09:00:00Z",
        "2026-10-04T09:00:00Z",
        "2026-10-20T09:00:00Z",
        "2026-10-21T09:00:00Z",
        "2026-10-22T09:00:00Z",
    ], "the three fresh runs displace the two oldest"


def test_a_limit_below_its_10x_is_exit_1_and_the_file_is_written(tree: Tree) -> None:
    tree.ci()
    tree.runs("2026-10-23T09:00:00Z\tCHECK_TIMEOUT\t19\t1.00\t10\t0")
    done = tree.run()
    assert done.returncode == 1, done.stdout + done.stderr
    assert "limits: CHECK_TIMEOUT: slowest 19 s of " in done.stdout
    assert "10x 240 s, 15x 300 s; below 10x" in done.stdout
    assert done.stdout.splitlines()[-1] == "limits: rule 8 does not hold"
    assert any(r.limit == "CHECK_TIMEOUT" and r.seconds == 19 for r in tree.records())


def test_what_ci_does_not_say_is_skipped_and_named(tree: Tree) -> None:
    tree.ci(quiet=frozenset({"SMOKE_TIMEOUT"}))
    jobs = json.loads((tree.fake / "jobs-101.json").read_text(encoding="utf-8"))
    jobs["jobs"].append({**jobs["jobs"][0], "name": "lint", "databaseId": 999})
    jobs["jobs"].append({**jobs["jobs"][0], "conclusion": "failure", "databaseId": 998})
    (tree.fake / "jobs-101.json").write_text(json.dumps(jobs), encoding="utf-8")
    tree.runs()
    done = tree.run()
    assert 'limits: ci.yml: "lint" is no job of the workflow today, skipped' in done.stdout
    assert (
        'limits: ci.yml: "image (linux/amd64)" printed no report line of SMOKE_TIMEOUT, skipped'
    ) in done.stdout
    assert "limits: SMOKE_TIMEOUT: 0 runs in ci.yml, fewer than 3" in done.stdout
    assert done.returncode == 1


@pytest.mark.parametrize("before", [None, "unsorted"])
@pytest.mark.parametrize("cause", ["gh fails", "gh answers no JSON", "a log line"])
def test_on_exit_2_nothing_is_written(tree: Tree, before: str | None, cause: str) -> None:
    tree.ci()
    tree.runs()
    env = {}
    if cause == "gh fails":
        env["GH_FAKE_EXIT"] = "1"
    elif cause == "gh answers no JSON":
        (tree.fake / "list-pr-title.yml.json").write_text("<html>", encoding="utf-8")
    else:
        tree.runs("not a record")
    if before:
        lines = [f"VERIFY_TIMEOUT\t2026-10-0{n}T09:00:00Z\t2\t1.00\t10\t-" for n in (2, 1)]
        tree.data.write_text(_text(*lines), encoding="utf-8")
        written = tree.data.read_bytes()
    done = tree.run(**env)
    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stderr.splitlines()[-1] == "limits: nothing written"
    if before:
        assert tree.data.read_bytes() == written
    else:
        assert not tree.data.exists()
    assert not list(tree.data.parent.glob("*.partial"))
    said = {
        "gh fails": "exit 1: HTTP 502",
        "gh answers no JSON": "the answer is no JSON",
        "a log line": f"line {3 * len(tree.sources.makefile_limits) + 1} of this machine's log",
    }
    assert said[cause] in done.stderr

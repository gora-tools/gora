"""TESTING rule 8's data: every bounded run, collected, selected, written and checked.

Gate-defining file (ADR-0016 rule 7). ``make limits`` runs it on the machine whose local runs
it measures; ``tests/unit/test_limits.py`` imports it by path, so the target and the test hold
the data to the sources with one implementation.

The runs are the records of ``scripts/gate/limits.tsv``, written here and never by hand::

    limit	finished	seconds	load	cpus	job

``limit`` is a Makefile variable (``VERIFY_TIMEOUT``) or a job of a workflow, ``file:job id``
(``ci.yml:test``); ``finished`` is UTC, to the second; ``seconds`` are whole; ``job`` is ``-``
for a run on this machine, else the workflow file and the job's name as the code host shows it
(``ci.yml:test (linux/amd64)``): a name alone is ambiguous. ``load`` and ``cpus`` are those the
run's report line printed (``unknown`` allowed in CI), and ``-`` for the record of a job's own
time, which has no line. Sorted by limit, job and finished, with no duplicate line. The file
holds no limit value: the value lives only in the Makefile or the workflow.

Collected from this machine's log (``limit-report.sh`` appends every bounded run to it): each
green run of a Makefile limit at rest. And from CI, with ``gh``: each green job of the runs
``RUNS`` names, as a record of its job's limit, and, in the log of each job that runs a
Makefile limit (``CI_LIMITS``), every report line of that limit, as CI ran it: the gate of
record has no rest filter. Selected: those with the records the file holds, the newest
``KEEP`` of each limit and job, and for a job of a workflow the count ``RUNS`` gives it.

Then each limit of the sources is printed against its 10x and its 15x, and every problem the
test reports. Exit 0: rule 8 holds. Exit 1: it does not, and the file is written all the
same, so its diff shows why. Exit 2: a source, ``gh``, the file or the log could not be read
as it must be, and nothing is written.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = Path("scripts") / "gate" / "limits.tsv"
HEADER = "limit\tfinished\tseconds\tload\tcpus\tjob"

# A pull is bounded by the network, not by a run: it has no records and no 10x.
NOT_HELD_TO_10X = frozenset({"PULL_TIMEOUT"})
# The outer limit, then the limit it contains: the inner one ends a hung run first.
NESTED = (
    ("VERIFY_EXPORT_TIMEOUT", "VERIFY_TIMEOUT"),
    ("VERIFY_LINUX_TIMEOUT", "BEHAVIOUR_TIMEOUT"),
    ("RED_FIRST_TIMEOUT", "DOCKER_BUILD_TIMEOUT"),
    ("ci.yml:check", "CHECK_TIMEOUT"),
    ("ci.yml:test", "BEHAVIOUR_TIMEOUT"),
)
# The green runs read of each workflow: the options of `gh run list`, and how many. The count
# is also how many records of each of its jobs are kept: release.yml runs on a tag, so its
# records are its last green run.
RUNS: dict[str, tuple[tuple[str, ...], int]] = {
    "ci.yml": (("--branch", "main", "--event", "push"), 5),
    "pr-title.yml": (("--event", "pull_request"), 5),
    "gate-change.yml": (("--event", "pull_request_target"), 5),
    "release.yml": ((), 1),
}
# The Makefile limits CI runs, and the jobs that run them. The test derives the same table
# from the workflows, the actions and the Makefile (derived_ci_limits).
CI_LIMITS: dict[str, tuple[str, ...]] = {
    "BEHAVIOUR_TIMEOUT": ("ci.yml:test",),
    "CHECK_TIMEOUT": ("ci.yml:check",),
    "SMOKE_TIMEOUT": ("ci.yml:image", "release.yml:build"),
}
# A workflow whose jobs may have no record: a job added since its last run has no run.
LAST_RUN_ONLY = frozenset(file for file, (_, count) in RUNS.items() if count == 1)
KEEP = 5
MINIMUM_RUNS = 3
GH_SECONDS = 60

LIMIT = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*_TIMEOUT) \?= (?P<seconds>\d+)\s*$", re.MULTILINE)
# A use of the two macros, and any other use of the two scripts' variables.
CALL = re.compile(r"\$\(call (?P<macro>bounded|gate_run),(?P<args>[^)]*)\)")
SCRIPT_VARIABLE = re.compile(r"\$\((?:BOUNDED|GATE_RUN)\)")
MACRO_DEFINITION = re.compile(r"^(?:bounded|gate_run) = .*$", re.MULTILINE)
TARGET = re.compile(r"^(?P<target>[A-Za-z0-9_.-]+):(?!=)")
MAKE = re.compile(r"(?<![\w./$-])make((?:[ \t]+[^\s;&|()]+)*)")
EXPRESSION = re.compile(r"\$\{\{.*?\}\}")
# A report line of bounded.sh or run-container.sh, wherever a log puts it on its line.
REPORT = re.compile(
    r"(?:^|\s)(?:bounded|run-container)\.sh: (?P<name>[A-Z][A-Z0-9_]*): (?P<seconds>\d+) s of"
    r" \d+ s \(\d+%\), load (?P<load>\S+) on (?P<cpus>\S+) CPUs(?:;.*)?$"
)
FINISHED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
JOB_LIMIT = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml:[A-Za-z0-9_-]+$")
JOB = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml:\S.*$")
WHOLE = re.compile(r"^[0-9]+$")
LOAD = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
COUNT = re.compile(r"^[1-9][0-9]*$")


class StopError(Exception):
    """What makes the tool stop with exit 2, before anything is written."""


@dataclass(frozen=True)
class Record:
    limit: str
    finished: str
    seconds: int
    load: str
    cpus: str
    job: str

    @property
    def local(self) -> bool:
        return self.job == "-"

    @property
    def file(self) -> str:
        """The workflow file of a CI record; empty for a run on this machine."""
        return "" if self.local else self.job.partition(":")[0]

    def key(self) -> tuple[str, str, str, int, str, str]:
        return (self.limit, self.job, self.finished, self.seconds, self.load, self.cpus)

    def line(self) -> str:
        fields = (self.limit, self.finished, str(self.seconds), self.load, self.cpus, self.job)
        return "\t".join(fields)


@dataclass(frozen=True)
class Run:
    """One record of this machine's log."""

    finished: str
    name: str
    seconds: int
    load: str
    cpus: str
    code: int


@dataclass
class Sources:
    makefile: str
    workflows: dict[str, dict[Any, Any]]
    actions: dict[str, dict[Any, Any]] = field(default_factory=dict)

    @property
    def makefile_limits(self) -> dict[str, int]:
        return makefile_limits(self.makefile)

    @property
    def limits(self) -> dict[str, int]:
        """Every limit, the Makefile's then the workflows', in seconds."""
        return {**self.makefile_limits, **workflow_limits(self.workflows)[0]}

    @property
    def held(self) -> dict[str, int]:
        """The limits held to their runs: every one but the pull."""
        return {name: value for name, value in self.limits.items() if name not in NOT_HELD_TO_10X}

    @property
    def names(self) -> dict[str, dict[str, str]]:
        """Each job's name, as its workflow writes it (its id when it has none), by file."""
        return {
            file: {
                str(job): str(body.get("name") or job)
                for job, body in (document.get("jobs") or {}).items()
            }
            for file, document in self.workflows.items()
        }


# --- reading the sources ------------------------------------------------------------------


def makefile_limits(text: str) -> dict[str, int]:
    """Every ``NAME_TIMEOUT ?= seconds`` of the Makefile, in seconds."""
    return {m["name"]: int(m["seconds"]) for m in LIMIT.finditer(text)}


def workflow_limits(workflows: Mapping[str, Mapping[Any, Any]]) -> tuple[dict[str, int], list[str]]:
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


def _document(path: Path, root: Path) -> dict[Any, Any]:
    name = path.relative_to(root).as_posix()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise StopError(f"limits: {name} cannot be read ({type(error).__name__})") from error
    if not isinstance(document, dict):
        raise StopError(f"limits: {name} is no mapping")
    return document


def read_sources(root: Path) -> Sources:
    """The Makefile, every workflow and every action of the repository at root."""
    try:
        makefile = (root / "Makefile").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise StopError(f"limits: the Makefile cannot be read ({type(error).__name__})") from error
    github = root / ".github"
    workflows = {p.name: _document(p, root) for p in sorted((github / "workflows").glob("*.yml"))}
    actions = {
        "./" + p.parent.relative_to(root).as_posix(): _document(p, root)
        for p in sorted((github / "actions").glob("*/action.yml"))
    }
    return Sources(makefile, workflows, actions)


# --- which Makefile limits CI runs ---------------------------------------------------------


def recipe_limits(makefile: str) -> dict[str, set[str]]:
    """Each target of the Makefile, and the limits its own recipe bounds through the macros."""
    recipes: dict[str, set[str]] = {}
    current: str | None = None
    for line in makefile.splitlines():
        if line.startswith("\t"):
            if current is not None:
                for call in CALL.finditer(line):
                    recipes[current].add(call["args"].split(",")[-1].strip())
            continue
        match = TARGET.match(line)
        current = match["target"] if match else None
        if current is not None:
            recipes.setdefault(current, set())
    return recipes


def _run_texts(body: Mapping[Any, Any], actions: Mapping[str, Mapping[Any, Any]]) -> Iterator[str]:
    """Every ``run:`` of a job, and of each action of this repository a step of it uses."""
    for step in body.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("run"), str):
            yield step["run"]
        uses = step.get("uses")
        if isinstance(uses, str) and uses.rstrip("/") in actions:
            yield from _run_texts(actions[uses.rstrip("/")].get("runs") or {}, {})


def derived_ci_limits(sources: Sources) -> dict[str, tuple[str, ...]]:
    """The Makefile limits each ``make <target>`` of a workflow job runs, and those jobs."""
    recipes = recipe_limits(sources.makefile)
    found: dict[str, set[str]] = {}
    for file, document in sources.workflows.items():
        for job, body in (document.get("jobs") or {}).items():
            for text in _run_texts(body, sources.actions):
                for call in MAKE.finditer(text):
                    for target in call.group(1).split():
                        for limit in recipes.get(target, set()) - NOT_HELD_TO_10X:
                            found.setdefault(limit, set()).add(f"{file}:{job}")
    return {limit: tuple(sorted(jobs)) for limit, jobs in sorted(found.items())}


# --- job names to job ids -----------------------------------------------------------------


def job_ids(shown: str, names: Mapping[str, str]) -> list[str]:
    """The job ids whose name matches the name the code host shows: each ``${{ … }}`` matches
    any text, the rest itself, the whole name."""
    matched = []
    for job, name in names.items():
        parts = [re.escape(part) for part in EXPRESSION.split(name)]
        if re.fullmatch(".*".join(parts), shown, re.DOTALL):
            matched.append(job)
    return matched


def match_job(file: str, shown: str, names: Mapping[str, str]) -> str | None:
    """The one job id of ``file`` that ``shown`` names, None for none; two stop the tool."""
    matched = job_ids(shown, names)
    if len(matched) > 1:
        raise StopError(f'limits: {file}: "{shown}" matches the jobs {", ".join(matched)}')
    return matched[0] if matched else None


# --- the data and the log -----------------------------------------------------------------


def _form(fields: list[str]) -> str | None:
    """Why the fields of a line of the data are not a record, or None."""
    if len(fields) != 6:
        return f"{len(fields)} fields, not 6"
    limit, finished, seconds, load, cpus, job = fields
    if not (NAME.match(limit) or JOB_LIMIT.match(limit)):
        return f"{limit!r} is no limit's name"
    if not FINISHED.match(finished):
        return f"{finished!r} is no UTC time to the second"
    if not WHOLE.match(seconds):
        return f"{seconds!r} is no whole number of seconds"
    if not (LOAD.match(load) or load in ("unknown", "-")):
        return f"{load!r} is no load"
    if not (COUNT.match(cpus) or cpus in ("unknown", "-")):
        return f"{cpus!r} is no CPU count"
    if not (job == "-" or JOB.match(job)):
        return f"{job!r} is no job"
    return None


def parse_data(text: str) -> tuple[list[Record], list[str]]:
    """The records of the data, in its order, and why each line that is not one is not."""
    lines = text.splitlines()
    if not lines or lines[0] != HEADER:
        return [], ["the first line is not the header " + HEADER.replace("\t", " ")]
    records: list[Record] = []
    bad: list[str] = []
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split("\t")
        why = _form(fields)
        if why:
            bad.append(f"line {number}: {why}")
            continue
        limit, finished, seconds, load, cpus, job = fields
        records.append(Record(limit, finished, int(seconds), load, cpus, job))
    return records, bad


def read_data(path: Path) -> list[Record]:
    """The records the data holds; none when there is no file yet."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise StopError(f"limits: {DATA} cannot be read ({type(error).__name__})") from error
    records, bad = parse_data(text)
    if bad:
        raise StopError(f"limits: {DATA}: {bad[0]}")
    return records


def log_path(environ: Mapping[str, str]) -> Path | None:
    """The log limit-report.sh writes: an absolute XDG_STATE_HOME, else an absolute HOME."""
    state = environ.get("XDG_STATE_HOME", "")
    if not state.startswith("/"):
        home = environ.get("HOME", "")
        if not home.startswith("/"):
            return None
        state = home + "/.local/state"
    return Path(state) / "gora" / "limits.log"


def parse_log(text: str) -> list[Run]:
    """The runs of this machine's log; a line that is not one stops the tool."""
    runs: list[Run] = []
    for number, line in enumerate(text.splitlines(), start=1):
        fields = line.split("\t")
        if (
            len(fields) != 6
            or not FINISHED.match(fields[0])
            or not NAME.match(fields[1])
            or not WHOLE.match(fields[2])
            or not (LOAD.match(fields[3]) or fields[3] == "unknown")
            or not (COUNT.match(fields[4]) or fields[4] == "unknown")
            or not WHOLE.match(fields[5])
        ):
            raise StopError(f"limits: line {number} of this machine's log is not a record")
        finished, name, seconds, load, cpus, code = fields
        runs.append(Run(finished, name, int(seconds), load, cpus, int(code)))
    return runs


def read_log(path: Path | None) -> list[Run]:
    if path is None or not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise StopError(
            f"limits: this machine's log cannot be read ({type(error).__name__})"
        ) from error
    return parse_log(text)


# --- collection and selection -------------------------------------------------------------


def at_rest(load: str, cpus: str) -> bool:
    """A run started at rest: the load its line printed under the CPU count it printed."""
    if not LOAD.match(load) or not COUNT.match(cpus):
        return False
    return Decimal(load) < Decimal(cpus)


def local_records(runs: Iterable[Run], makefile: Mapping[str, int]) -> list[Record]:
    """The green runs at rest of a Makefile limit (the pull's are not held to one)."""
    return [
        Record(run.name, run.finished, run.seconds, run.load, run.cpus, "-")
        for run in runs
        if run.code == 0
        and run.name in makefile
        and run.name not in NOT_HELD_TO_10X
        and at_rest(run.load, run.cpus)
    ]


Gh = Callable[[list[str]], str]


def run_gh(args: list[str], *, cwd: Path = ROOT, seconds: int = GH_SECONDS) -> str:
    """What ``gh`` prints, bounded; a failure, a timeout or no answer stops the tool."""
    shown = " ".join(["gh", *args])
    try:
        done = subprocess.run(
            ["gh", *args], cwd=cwd, capture_output=True, text=True, timeout=seconds, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise StopError(f"limits: {shown}: no answer in {seconds} s") from error
    except OSError as error:
        raise StopError(f"limits: {shown}: cannot run ({type(error).__name__})") from error
    if done.returncode != 0:
        said = (done.stderr.strip().splitlines() or [""])[0]
        raise StopError(f"limits: {shown}: exit {done.returncode}" + (f": {said}" if said else ""))
    return done.stdout


def _answer(text: str, shown: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as error:
        raise StopError(f"limits: {shown}: the answer is no JSON") from error


def _time(value: object, shown: str) -> datetime:
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if when.tzinfo is not None:
                return when.astimezone(UTC).replace(microsecond=0)
    raise StopError(f"limits: {shown}: {value!r} is no time")


def _runs(gh: Gh, file: str, options: tuple[str, ...], count: int) -> list[str]:
    args = ["run", "list", "--workflow", file, "--status", "success", "--limit", str(count)]
    args += ["--json", "databaseId", *options]
    shown = "gh " + " ".join(args)
    listed = _answer(gh(args), shown)
    if not isinstance(listed, list) or not all(
        isinstance(run, dict) and type(run.get("databaseId")) is int for run in listed
    ):
        raise StopError(f"limits: {shown}: the answer is not a list of runs")
    return [str(run["databaseId"]) for run in listed]


def _jobs(gh: Gh, run: str) -> list[dict[str, Any]]:
    args = ["run", "view", run, "--json", "jobs"]
    shown = "gh " + " ".join(args)
    answer = _answer(gh(args), shown)
    jobs = answer.get("jobs") if isinstance(answer, dict) else None
    if not isinstance(jobs, list) or not all(
        isinstance(job, dict)
        and isinstance(job.get("name"), str)
        and isinstance(job.get("conclusion"), str)
        and type(job.get("databaseId")) is int
        for job in jobs
    ):
        raise StopError(f"limits: {shown}: the answer is not a list of jobs")
    return jobs


def report_lines(log: str, name: str, shown: str) -> list[tuple[int, str, str]]:
    """The seconds, load and CPUs of every report line of the limit ``name`` in a job's log."""
    found = []
    for number, line in enumerate(log.splitlines(), start=1):
        match = REPORT.search(line)
        if not match or match["name"] != name:
            continue
        load, cpus = match["load"], match["cpus"]
        if not (LOAD.match(load) or load == "unknown") or not (
            COUNT.match(cpus) or cpus == "unknown"
        ):
            raise StopError(
                f"limits: {shown}: line {number} is a report line of {name} and no record"
            )
        found.append((int(match["seconds"]), load, cpus))
    return found


def ci_records(sources: Sources, gh: Gh, say: Callable[[str], None]) -> list[Record]:
    """The green jobs of the runs RUNS names, and the report lines of the CI_LIMITS jobs."""
    names = sources.names
    records: list[Record] = []
    skipped: set[tuple[str, str]] = set()
    for file, (options, count) in RUNS.items():
        if file not in sources.workflows:
            continue
        for run in _runs(gh, file, options, count):
            for job in _jobs(gh, run):
                if job["conclusion"] != "success":
                    continue
                shown = job["name"]
                job_id = match_job(file, shown, names[file])
                if job_id is None:
                    if (file, shown) not in skipped:
                        skipped.add((file, shown))
                        say(f'limits: {file}: "{shown}" is no job of the workflow today, skipped')
                    continue
                where = f"gh run view {run}: job {shown!r}"
                started = _time(job.get("startedAt"), where)
                completed = _time(job.get("completedAt"), where)
                if completed < started:
                    raise StopError(f"limits: {where}: it completed before it started")
                finished = completed.strftime("%Y-%m-%dT%H:%M:%SZ")
                label = f"{file}:{shown}"
                seconds = int((completed - started).total_seconds())
                records.append(Record(f"{file}:{job_id}", finished, seconds, "-", "-", label))
                bounded = sorted(n for n, jobs in CI_LIMITS.items() if f"{file}:{job_id}" in jobs)
                if not bounded:
                    continue
                args = ["run", "view", run, "--job", str(job["databaseId"]), "--log"]
                log = gh(args)
                for name in bounded:
                    lines = report_lines(log, name, "gh " + " ".join(args))
                    if not lines:
                        say(f'limits: {file}: "{shown}" printed no report line of {name}, skipped')
                    records += [Record(name, finished, s, ld, c, label) for s, ld, c in lines]
    return records


def keep(job: str) -> int:
    """How many records of one limit and job are kept: KEEP, or the count of its workflow."""
    if job == "-":
        return KEEP
    return RUNS.get(job.partition(":")[0], ((), KEEP))[1]


def job_problem(record: Record, names: Mapping[str, Mapping[str, str]]) -> str | None:
    """Why the job of a CI record is not one its limit is measured by, or None."""
    file, _, shown = record.job.partition(":")
    ids = [f"{file}:{job}" for job in job_ids(shown, names.get(file, {}))]
    if ":" in record.limit:
        return None if ids == [record.limit] else "the job is not the one its limit names"
    if len(ids) == 1 and ids[0] in CI_LIMITS.get(record.limit, ()):
        return None
    return "no job that runs the limit"


def _still_held(record: Record, sources: Sources) -> bool:
    """A record the file holds that the sources still measure, the way it was collected."""
    if record.limit not in sources.held:
        return False
    if record.local:
        return at_rest(record.load, record.cpus)
    return job_problem(record, sources.names) is None


def select(collected: Iterable[Record], kept: Iterable[Record], sources: Sources) -> list[Record]:
    """The collected records and those the file holds, without duplicates, the newest of each
    limit and job as many as ``keep`` says, sorted. A limit no one has run again keeps its
    records; a fresh run displaces the oldest."""
    pool = set(collected) | {record for record in kept if _still_held(record, sources)}
    groups: dict[tuple[str, str], list[Record]] = {}
    for record in pool:
        groups.setdefault((record.limit, record.job), []).append(record)
    chosen: list[Record] = []
    for (_, job), group in groups.items():
        group.sort(key=lambda record: (record.finished, record.seconds, record.load, record.cpus))
        chosen += group[-keep(job) :]
    return sorted(chosen, key=Record.key)


def write_data(path: Path, records: Iterable[Record]) -> None:
    text = "".join(f"{line}\n" for line in [HEADER, *(record.line() for record in records)])
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


# --- the checks ---------------------------------------------------------------------------


def minute_up(seconds: int) -> int:
    """Seconds rounded up to a whole minute."""
    return -(-seconds // 60) * 60


def slowest(records: Iterable[Record], limit: str) -> int | None:
    """The slowest run of a limit, local and CI together; None when it has none."""
    runs = [record.seconds for record in records if record.limit == limit]
    return max(runs) if runs else None


def data_problems(records: list[Record], sources: Sources) -> list[str]:
    """The records against the sources and rule 8."""
    problems: list[str] = []
    held = sources.held
    names = sources.names
    for record in records:
        where = f"{record.limit} ({record.job}, {record.finished})"
        if record.limit not in held:
            problems.append(f"{where}: a record of no limit of the sources")
            continue
        if record.local:
            if not at_rest(record.load, record.cpus):
                problems.append(
                    f"{where}: a run on this machine at load {record.load} on {record.cpus}"
                    " CPUs, not at rest"
                )
            continue
        if ":" in record.limit and (record.load, record.cpus) != ("-", "-"):
            problems.append(f"{where}: a job's own record has no load and CPUs")
        if ":" not in record.limit and "-" in (record.load, record.cpus):
            problems.append(f"{where}: a report line in CI has its load and CPUs as printed")
        why = job_problem(record, names)
        if why:
            problems.append(f"{where}: {why}")
    counts: dict[tuple[str, str], int] = {}
    for record in records:
        counts[record.limit, record.job] = counts.get((record.limit, record.job), 0) + 1
    for (limit, job), count in sorted(counts.items()):
        if count > keep(job):
            problems.append(f"{limit} ({job}): {count} records, more than the {keep(job)} kept")
    for limit, seconds in held.items():
        mine = [record for record in records if record.limit == limit]
        if limit.partition(":")[0] not in LAST_RUN_ONLY and len(mine) < MINIMUM_RUNS:
            problems.append(f"{limit}: {len(mine)} records, fewer than {MINIMUM_RUNS}")
        if limit in CI_LIMITS:
            local = [record for record in mine if record.local]
            if len(local) < MINIMUM_RUNS:
                problems.append(
                    f"{limit}: {len(local)} runs on this machine, fewer than {MINIMUM_RUNS}"
                )
            for file in sorted({job.partition(":")[0] for job in CI_LIMITS[limit]}):
                if file in LAST_RUN_ONLY:
                    continue
                ci = [record for record in mine if record.file == file]
                if len(ci) < MINIMUM_RUNS:
                    problems.append(f"{limit}: {len(ci)} runs in {file}, fewer than {MINIMUM_RUNS}")
        top = slowest(records, limit)
        if top is not None and seconds < minute_up(top * 10):
            problems.append(
                f"{limit}: {seconds} s is below 10x its slowest run of {top} s,"
                f" {minute_up(top * 10)} s rounded up to a whole minute"
            )
    return problems


def order_problems(records: list[Record]) -> list[str]:
    """The records as the file holds them: sorted, and no line twice."""
    problems = []
    if len(set(records)) != len(records):
        problems.append(f"{DATA}: a record is there twice")
    if records != sorted(records, key=Record.key):
        problems.append(f"{DATA}: the records are not sorted by limit, job and finished")
    return problems


def nesting_problems(limits: Mapping[str, int]) -> list[str]:
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


def macro_problems(makefile: str, limits: Mapping[str, int]) -> list[str]:
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


def table_problems(sources: Sources) -> list[str]:
    """The tool's two tables against the files: RUNS and CI_LIMITS."""
    problems = [
        f"{file}: a workflow with no entry in the run table of limits.py"
        for file in sources.workflows
        if file not in RUNS
    ]
    problems += [
        f"{file}: an entry of the run table of limits.py with no workflow"
        for file in RUNS
        if file not in sources.workflows
    ]
    derived = derived_ci_limits(sources)
    for limit in sorted(set(derived) | set(CI_LIMITS)):
        if derived.get(limit) != CI_LIMITS.get(limit):
            problems.append(
                f"{limit}: CI runs it in {', '.join(derived.get(limit, ())) or 'no job'},"
                f" and the table of limits.py says {', '.join(CI_LIMITS.get(limit, ())) or 'none'}"
            )
    return problems


def problems(records: list[Record], sources: Sources) -> list[str]:
    """Every problem the test reports, for the records as selected."""
    _, missing = workflow_limits(sources.workflows)
    return [
        *data_problems(records, sources),
        *order_problems(records),
        *nesting_problems(sources.limits),
        *macro_problems(sources.makefile, sources.limits),
        *[f"{job}: a job with no timeout-minutes" for job in missing],
        *table_problems(sources),
    ]


def summary(records: list[Record], sources: Sources) -> list[str]:
    """One line per limit of the sources (the pull excepted), against its 10x and 15x."""
    lines = []
    for limit, seconds in sources.held.items():
        count = sum(1 for record in records if record.limit == limit)
        top = slowest(records, limit)
        if top is None:
            lines.append(f"limits: {limit}: no runs; limit {seconds} s")
            continue
        line = (
            f"limits: {limit}: slowest {top} s of {count} runs; limit {seconds} s,"
            f" 10x {minute_up(top * 10)} s, 15x {minute_up(top * 15)} s"
        )
        if seconds < minute_up(top * 10):
            line += "; below 10x"
        lines.append(line)
    return lines


def main(root: Path = ROOT, environ: Mapping[str, str] = os.environ, gh: Gh | None = None) -> int:
    def ask(args: list[str]) -> str:
        return run_gh(args, cwd=root)

    try:
        sources = read_sources(root)
        kept = read_data(root / DATA)
        runs = read_log(log_path(environ))
        collected = local_records(runs, sources.makefile_limits)
        collected += ci_records(sources, gh or ask, print)
    except StopError as stop:
        print(stop, file=sys.stderr)
        print("limits: nothing written", file=sys.stderr)
        return 2
    records = select(collected, kept, sources)
    write_data(root / DATA, records)
    for line in summary(records, sources):
        print(line)
    found = problems(records, sources)
    for problem in found:
        print(f"limits: {problem}")
    print("limits: rule 8 does not hold" if found else "limits: rule 8 holds")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

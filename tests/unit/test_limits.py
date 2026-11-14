"""Rule 8's limits are held to their sources (TESTING rule 8).

Every limit is a constant: ``NAME_TIMEOUT ?= N`` in the Makefile, ``timeout-minutes`` of a job in
a workflow. Three things about them are read from those files alone, offline (TESTING rule 6),
and held here:

- every job of every workflow has a ``timeout-minutes``;
- an outer limit is above every limit it contains, so that the inner one ends a hung run first
  (``NESTED``);
- every bounded run of the Makefile is named by its limit through the two macros ``bounded`` and
  ``gate_run``, and every limit of the Makefile bounds a run.

Each check is shown red here on a defect of its class (ADR-0016 rule 1) before it is trusted on
the real files. There is no data: how long a run takes is a measurement the pull request that
sets a limit carries, and a run that ends at its limit is a red gate.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

# A pull is bounded by the network, not by a run through the macros.
NO_MACRO = frozenset({"PULL_TIMEOUT"})
# The outer limit, then the limit it contains: the inner one ends a hung run first.
NESTED = (
    ("VERIFY_EXPORT_TIMEOUT", "VERIFY_TIMEOUT"),
    ("VERIFY_LINUX_TIMEOUT", "BEHAVIOUR_TIMEOUT"),
    ("RED_FIRST_TIMEOUT", "DOCKER_BUILD_TIMEOUT"),
    ("ci.yml:check", "CHECK_TIMEOUT"),
    ("ci.yml:test", "BEHAVIOUR_TIMEOUT"),
)

LIMIT = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*_TIMEOUT) \?= (?P<seconds>\d+)\s*$", re.MULTILINE)
# A use of the two macros, and any other use of the two scripts' variables.
CALL = re.compile(r"\$\(call (?P<macro>bounded|gate_run),(?P<args>[^)]*)\)")
SCRIPT_VARIABLE = re.compile(r"\$\((?:BOUNDED|GATE_RUN)\)")
MACRO_DEFINITION = re.compile(r"^(?:bounded|gate_run) = .*$", re.MULTILINE)


# --- the checks, over the text of the sources ----------------------------------------------


def makefile_limits(text: str) -> dict[str, int]:
    """Every ``NAME_TIMEOUT ?= seconds`` of the Makefile, in seconds."""
    return {m["name"]: int(m["seconds"]) for m in LIMIT.finditer(text)}


def workflow_limits(workflows: Mapping[str, Mapping[Any, Any]]) -> tuple[dict[str, int], list[str]]:
    """Each job's ``timeout-minutes`` as ``file:job`` in seconds, and the jobs that have none.
    A job that calls a reusable workflow (``uses``) has none by GitHub's rules: the called
    workflow's jobs carry the limits."""
    limits: dict[str, int] = {}
    missing: list[str] = []
    for name, document in workflows.items():
        for job, body in (document.get("jobs") or {}).items():
            if "uses" in body:
                continue
            key = f"{name}:{job}"
            minutes = body.get("timeout-minutes")
            if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
                limits[key] = minutes * 60
            else:
                missing.append(key)
    return limits, missing


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
    """Every bounded run of the Makefile is named by a limit of it, through the two macros, and
    every limit of the Makefile bounds a run."""
    problems: list[str] = []
    names = set(makefile_limits(makefile)) - NO_MACRO
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


# --- the real files ------------------------------------------------------------------------


def _makefile() -> str:
    return (ROOT / "Makefile").read_text(encoding="utf-8")


def _workflows() -> dict[str, dict[Any, Any]]:
    found = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), f"{path.name} is no mapping"
        found[path.name] = document
    return found


def _limits() -> dict[str, int]:
    """Every limit, the Makefile's then the workflows', in seconds."""
    return {**makefile_limits(_makefile()), **workflow_limits(_workflows())[0]}


def test_every_job_of_every_workflow_has_a_timeout() -> None:
    found, missing = workflow_limits(_workflows())
    assert missing == []
    assert found, "no job found: the workflows were not read"


def test_an_outer_limit_is_above_the_limits_it_contains() -> None:
    assert nesting_problems(_limits()) == []


def test_every_bounded_run_is_named_by_its_limit_through_the_macros() -> None:
    assert macro_problems(_makefile(), _limits()) == []


def test_the_sources_are_read_whole() -> None:
    from_makefile = makefile_limits(_makefile())
    assert {"VERIFY_TIMEOUT", "VERIFY_LINUX_TIMEOUT", "SMOKE_TIMEOUT", "PULL_TIMEOUT"} <= set(
        from_makefile
    )
    assert all(seconds > 0 for seconds in _limits().values())
    assert {"ci.yml:check", "ci.yml:test", "ci.yml:image", "release.yml:build"} <= set(_limits())


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
    assert nesting_problems(nested) == [
        "VERIFY_LINUX_TIMEOUT (600 s) is not above BEHAVIOUR_TIMEOUT (600 s):"
        " the outer limit would end a hung run before the inner one"
    ]
    assert nesting_problems({**nested, "VERIFY_LINUX_TIMEOUT": 601}) == []
    lower = nesting_problems({**nested, "VERIFY_LINUX_TIMEOUT": 601, "ci.yml:test": 500})
    assert len(lower) == 1 and lower[0].startswith("ci.yml:test (500 s) is not above")
    gone = {name: seconds for name, seconds in nested.items() if name != "CHECK_TIMEOUT"}
    assert any("has no source" in problem for problem in nesting_problems(gone))


def test_a_job_without_a_timeout_is_found() -> None:
    documents: dict[str, dict[Any, Any]] = {
        "a.yml": {"jobs": {"one": {"timeout-minutes": 5}, "two": {"runs-on": "x"}}},
        "b.yml": {"jobs": {"three": {"timeout-minutes": 0}, "four": {"timeout-minutes": "5"}}},
    }
    found, missing = workflow_limits(documents)
    assert found == {"a.yml:one": 300}
    assert missing == ["a.yml:two", "b.yml:three", "b.yml:four"]


def test_a_job_that_calls_a_reusable_workflow_has_none_of_its_own_and_is_not_found() -> None:
    """GitHub allows no timeout-minutes on a job with `uses`: the called jobs carry the limits."""
    documents: dict[str, dict[Any, Any]] = {
        "base.yml": {"jobs": {"image": {"uses": "./.github/workflows/own-image.yml"}}},
        "own-image.yml": {
            "jobs": {"build": {"timeout-minutes": 30}, "publish": {"runs-on": "x"}},
        },
    }
    found, missing = workflow_limits(documents)
    assert found == {"own-image.yml:build": 1800}
    assert missing == ["own-image.yml:publish"]


def test_a_makefile_that_names_every_run_by_its_limit_is_green() -> None:
    found = makefile_limits(MAKEFILE_TEXT)
    assert found == {
        "VERIFY_TIMEOUT": 600,
        "CHECK_TIMEOUT": 600,
        "BEHAVIOUR_TIMEOUT": 600,
        "SMOKE_TIMEOUT": 60,
        "PULL_TIMEOUT": 300,
    }
    assert macro_problems(MAKEFILE_TEXT, found) == []


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

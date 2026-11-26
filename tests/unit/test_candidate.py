"""The workflow that asks GORA which artifacts a commit is a candidate of holds its shape
(ADR-022 sections 4 and 14, ADR-006, the note on the reader's token).

``.github/workflows/candidate.yml`` mints GORA's App token, narrowed to read, in one job of the
environment ``gora``, and hands it to ``gora changes`` as ``GORA_GITHUB_TOKEN``. The core passes
any installation token, so what the token is, is decided where it is made: this file. Read from
the file itself, offline (TESTING rule 6): each check is a function of the workflow's text, and
each statement is shown red here on a defect of its class (ADR-0016 rule 1), made as a change of
the real file's text, before it is trusted on the real file.

What the test holds is the token's source in this file. The core still passes any installation
token, and a first live run on ``main`` is the only proof that the job runs. Comments of the
workflow are free: only the parsed file is read, its keys, its values and its scripts.
"""

import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "candidate.yml"

TOKEN_ACTION = "actions/create-github-app-token@"
SETTING = "GORA_GITHUB_TOKEN"
EXPECTED_TRIGGERS = {"push": {"branches": ["main"]}, "workflow_dispatch": None}
EXPECTED_INPUTS = {
    "app-id": "${{ vars.GORA_APP_ID }}",
    "private-key": "${{ secrets.GORA_APP_PRIVATE_KEY }}",
    "owner": "gora-tools",
    "repositories": "gora",
    "permission-contents": "read",
    "permission-metadata": "read",
}
PROJECT = "github.com/gora-tools/gora"
COMMIT = "${{ github.sha }}"
# The refs the guard is run with: only the first passes.
MAIN = "refs/heads/main"
OTHER_REFS = [
    "refs/heads/feature",
    "refs/heads/main2",
    "refs/heads/Main",
    "refs/tags/v0.2.1-rc",
    "refs/pull/1/merge",
    "main",
    "",
]

# A setting of the token: a key of that name, or a script that assigns it.
ASSIGNS = re.compile(rf"{SETTING}\s*=")
# The token of Actions, in any spelling a workflow has: `github.token`, `secrets.GITHUB_TOKEN`
# and a GITHUB_TOKEN that is not GORA's variable.
ACTIONS_TOKEN = re.compile(r"github\s*\.\s*token|github\s*\[\s*['\"]token['\"]\s*\]", re.I)
ANY_GITHUB_TOKEN = re.compile(r"(?<!GORA_)GITHUB_TOKEN")
EXPRESSION = re.compile(r"\$\{\{")
TRACE = re.compile(r"(?m)^\s*set\s+(?:-\w*\s+)*[-+]\w*x|xtrace")
ECHOES_TOKEN = re.compile(r"(?mi)^\s*(?:echo|printf)\b.*token")
DUMPS_ENV = re.compile(r"(?m)^\s*(?:env|printenv|export\s+-p)\s*$")
BUILDS = re.compile(r"docker|crane|cosign|syft|packages", re.I)

Document = dict[Any, Any]


# --- reading the workflow ------------------------------------------------------------------


def _load(text: str) -> Document:
    document: Document = yaml.safe_load(text)
    return document


def _job(document: Document, name: str) -> dict[str, Any]:
    job = (document.get("jobs") or {}).get(name)
    return job if isinstance(job, dict) else {}


def _steps(document: Document, name: str) -> list[dict[str, Any]]:
    return [step for step in _job(document, name).get("steps") or [] if isinstance(step, dict)]


def _strings(node: Any) -> Iterator[str]:
    """Every key and every value of the parsed file that is text: keys, values and scripts."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, str):
        yield node


def _scripts(document: Document) -> Iterator[str]:
    for name in document.get("jobs") or {}:
        for step in _steps(document, name):
            if isinstance(step.get("run"), str):
                yield step["run"]


def _places(node: Any, path: str = "") -> Iterator[str]:
    """Where the token is set: the mapping that holds a key of its name, or the script that
    assigns it."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == SETTING:
                yield path
            yield from _places(value, f"{path}/{key}")
    elif isinstance(node, list):
        for number, item in enumerate(node):
            yield from _places(item, f"{path}/{number}")
    elif isinstance(node, str) and ASSIGNS.search(node):
        yield path


def _indexes(steps: list[dict[str, Any]], match: Callable[[dict[str, Any]], bool]) -> list[int]:
    return [number for number, step in enumerate(steps) if match(step)]


def _uses(prefix: str) -> Callable[[dict[str, Any]], bool]:
    return lambda step: str(step.get("uses", "")).startswith(prefix)


def _runs(part: str) -> Callable[[dict[str, Any]], bool]:
    return lambda step: part in str(step.get("run", ""))


def _the_one(steps: list[dict[str, Any]], match: Callable[[dict[str, Any]], bool]) -> int | None:
    found = _indexes(steps, match)
    return found[0] if len(found) == 1 else None


# --- the checks: each returns its problems, each problem starts with the name of its check -


def trigger_problems(document: Document) -> list[str]:
    """Only a push to main and a person's run; YAML 1.1 reads the key `on` as true."""
    found = document.get("on", document.get(True))
    if found != EXPECTED_TRIGGERS:
        return [f"triggers: {found!r}, not a push to main and workflow_dispatch alone"]
    return []


def permission_problems(document: Document) -> list[str]:
    """Nothing by default, `contents: read` in `changes` alone: no write, no id-token (this
    workflow neither builds nor signs), none in the guard."""
    bad = []
    if document.get("permissions") != {}:
        bad.append(f"permissions: the workflow grants {document.get('permissions')!r}, not nothing")
    if "permissions" in _job(document, "guard"):
        bad.append(f"permissions: guard holds {_job(document, 'guard')['permissions']!r}")
    if _job(document, "changes").get("permissions") != {"contents": "read"}:
        bad.append(
            f"permissions: changes holds {_job(document, 'changes').get('permissions')!r},"
            " not contents read alone"
        )
    return bad


def _guard_exit(script: str, ref: str) -> int:
    """The guard's script as GitHub runs a `shell: bash` step, with nothing but the ref set."""
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env={"PATH": "/usr/bin:/bin", "GITHUB_REF": ref},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode


def guard_problems(document: Document) -> list[str]:
    """The guard checks out nothing, refuses any ref but main, and `changes` waits for it."""
    guard = _job(document, "guard")
    if not guard:
        return ["guard: the workflow has no job guard"]
    bad = []
    needs = _job(document, "changes").get("needs", [])
    if "guard" not in ([needs] if isinstance(needs, str) else needs):
        bad.append("guard: changes does not need guard")
    if any("uses" in step for step in _steps(document, "guard")):
        bad.append("guard: the guard checks out or uses an action: it reads the ref alone")
    asked = [step for step in _steps(document, "guard") if step.get("id") == "guard"]
    if (
        len(asked) != 1
        or asked[0].get("shell") != "bash"
        or not isinstance(asked[0].get("run"), str)
    ):
        return [*bad, "guard: no one bash step with the id guard"]
    wrong = [ref for ref in OTHER_REFS if _guard_exit(asked[0]["run"], ref) != 1]
    if _guard_exit(asked[0]["run"], MAIN) != 0:
        wrong.append(MAIN)
    if wrong:
        bad.append(f"guard: the script answers wrongly for {wrong}")
    return bad


def timeout_problems(document: Document) -> list[str]:
    """A limit on both jobs (TESTING rule 8; the generic test holds it for every workflow)."""
    bad = []
    for name in ("guard", "changes"):
        minutes = _job(document, name).get("timeout-minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
            bad.append(f"timeout: {name} has no timeout-minutes")
    return bad


def environment_problems(document: Document) -> list[str]:
    """`changes` is the only job with an environment, and it is `gora`."""
    found = {
        name: job["environment"]
        for name, job in (document.get("jobs") or {}).items()
        if isinstance(job, dict) and "environment" in job
    }
    if found != {"changes": "gora"}:
        return [f"environment: {found!r}, not gora on changes alone"]
    return []


def token_step_problems(document: Document) -> list[str]:
    """One step makes the token, with exactly the six inputs and no other."""
    steps = _steps(document, "changes")
    found = [step for step in steps if _uses(TOKEN_ACTION)(step)]
    if len(found) != 1:
        return [f"token-step: {len(found)} steps of {TOKEN_ACTION}, not one"]
    given = found[0].get("with") or {}
    if given != EXPECTED_INPUTS:
        differ = sorted(
            key for key in {*given, *EXPECTED_INPUTS} if given.get(key) != EXPECTED_INPUTS.get(key)
        )
        return [f"inputs: the token step differs from the six inputs in {differ}"]
    return []


def _token_step_id(document: Document) -> str:
    steps = _steps(document, "changes")
    found = [step for step in steps if _uses(TOKEN_ACTION)(step)]
    return str(found[0].get("id")) if len(found) == 1 else "(no token step)"


def boundary_problems(document: Document) -> list[str]:
    """The token is set once, in the env of the step that runs `gora changes`, from the output
    of the step that makes it; the token of Actions appears nowhere in the parsed file."""
    bad = []
    steps = _steps(document, "changes")
    asking = _the_one(steps, _runs("gora changes"))
    want = [] if asking is None else [f"/jobs/changes/steps/{asking}/env"]
    places = sorted(_places(document))
    if asking is None or places != want:
        bad.append(f"place: {SETTING} is set at {places}, not only in the env of the step asking")
    else:
        value = (steps[asking].get("env") or {}).get(SETTING)
        source = "${{ steps." + _token_step_id(document) + ".outputs.token }}"
        if value != source:
            bad.append(f"value: {SETTING} is {value!r}, not {source}")
    for text in _strings(document):
        if ACTIONS_TOKEN.search(text) or ANY_GITHUB_TOKEN.search(text):
            bad.append(f"forbidden: the token of Actions is named in {text!r}")
    return bad


def order_problems(document: Document) -> list[str]:
    """The checkout, the versions, setup-uv and the sync come before the token step, and the
    step that asks comes right after it."""
    steps = _steps(document, "changes")
    wanted = {
        "checkout": _the_one(steps, _uses("actions/checkout@")),
        "versions": _the_one(steps, _runs("scripts/gate/versions.sh")),
        "setup-uv": _the_one(steps, _uses("astral-sh/setup-uv@")),
        "sync": _the_one(steps, lambda step: str(step.get("run", "")).strip() == "make sync-test"),
        "token": _the_one(steps, _uses(TOKEN_ACTION)),
        "ask": _the_one(steps, _runs("gora changes")),
    }
    at = {name: found for name, found in wanted.items() if found is not None}
    missing = [name for name in wanted if name not in at]
    if missing:
        return [f"order: no one step for {missing}"]
    late = [name for name in ("checkout", "versions", "setup-uv", "sync") if at[name] > at["token"]]
    bad = []
    if late:
        bad.append(f"order: {late} come after the token step")
    if at["ask"] != at["token"] + 1:
        bad.append("order: the step that asks is not immediately after the token step")
    return bad


def clone_problems(document: Document) -> list[str]:
    """The clone is full (the Git adapter reads a full clone, ADR-006) and keeps no credential."""
    steps = _steps(document, "changes")
    checkout = _the_one(steps, _uses("actions/checkout@"))
    if checkout is None:
        return ["clone: no one checkout step"]
    given = steps[checkout].get("with") or {}
    bad = []
    depth = given.get("fetch-depth")
    if type(depth) is not int or depth != 0:
        bad.append(f"fetch-depth is {depth!r}, not 0")
    if given.get("persist-credentials") is not False:
        bad.append(f"persist-credentials is {given.get('persist-credentials')!r}, not false")
    bad += [
        f"{key} is set: the clone is partial"
        for key in ("filter", "sparse-checkout")
        if key in given
    ]
    return [f"clone: {problem}" for problem in bad]


def ask_env_problems(document: Document) -> list[str]:
    """The project is the literal (README) and the commit is the pushed one."""
    steps = _steps(document, "changes")
    asking = _the_one(steps, _runs("gora changes"))
    if asking is None:
        return ["ask-env: no one step runs gora changes"]
    env = steps[asking].get("env") or {}
    return [
        f"ask-env: {key} is {env.get(key)!r}, not {want}"
        for key, want in (("GORA_PROJECT", PROJECT), ("COMMIT", COMMIT))
        if env.get(key) != want
    ]


def script_problems(document: Document) -> list[str]:
    """No expression in a script (every value reaches it through env), no trace, and no
    echo of the token."""
    bad = []
    for script in _scripts(document):
        first = script.splitlines()[0] if script.strip() else "(empty)"
        if EXPRESSION.search(script):
            bad.append(f"script: an expression in the script that begins {first!r}")
        if TRACE.search(script):
            bad.append(f"script: a trace in the script that begins {first!r}")
        if ECHOES_TOKEN.search(script) or DUMPS_ENV.search(script):
            bad.append(f"script: the token may be printed by the script that begins {first!r}")
    return bad


def builds_problems(document: Document) -> list[str]:
    """Nothing here builds, signs or pushes."""
    return [f"builds: {text!r}" for text in _strings(document) if BUILDS.search(text)]


CHECKS = (
    trigger_problems,
    permission_problems,
    guard_problems,
    timeout_problems,
    environment_problems,
    token_step_problems,
    boundary_problems,
    order_problems,
    clone_problems,
    ask_env_problems,
    script_problems,
    builds_problems,
)


def problems(text: str) -> list[str]:
    document = _load(text)
    return [problem for check in CHECKS for problem in check(document)]


def names(found: list[str]) -> list[str]:
    """The check each problem came from, in order."""
    return [problem.split(":")[0] for problem in found]


# --- the real file -------------------------------------------------------------------------


def _real() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_candidate_workflow_holds_its_shape() -> None:
    assert problems(_real()) == []


def test_each_thing_the_checks_hold_is_found_at_all() -> None:
    """A check that finds nothing is a gate that cannot fail: each thing is there, and the
    reading of it reads it."""
    document = _load(_real())
    assert document["name"] == "Candidate"
    assert set(document["jobs"]) == {"guard", "changes"}
    steps = _steps(document, "changes")
    for match in (
        _uses("actions/checkout@"),
        _runs("scripts/gate/versions.sh"),
        _uses("astral-sh/setup-uv@"),
        _runs("make sync-test"),
        _uses(TOKEN_ACTION),
        _runs("gora changes"),
    ):
        assert len(_indexes(steps, match)) == 1
    asking = _the_one(steps, _runs("gora changes"))
    assert asking is not None
    assert list(_places(document)) == [f"/jobs/changes/steps/{asking}/env"]
    assert len(list(_strings(document))) > 30
    assert len(list(_scripts(document))) == 4  # the guard, versions, the sync, the question
    assert _guard_exit(next(_scripts(document)), MAIN) == 0
    assert _guard_exit(next(_scripts(document)), "refs/heads/other") == 1
    assert len(CHECKS) == len({check.__name__ for check in CHECKS})


# --- each statement is shown red on a defect of its class ----------------------------------


def _replace(old: str, new: str) -> Callable[[str], str]:
    def defect(text: str) -> str:
        assert text.count(old) == 1, f"{old!r} is not in the workflow exactly once"
        return text.replace(old, new)

    return defect


def _append(step: str) -> Callable[[str], str]:
    return lambda text: text + step


TOKEN_SOURCE = "${{ steps.token.outputs.token }}"
TOKEN_LINE = f"          {SETTING}: {TOKEN_SOURCE}\n"
SYNC_STEP = "      - name: Install the locked environment\n        run: make sync-test\n"
ASK_SCRIPT = '          body=$(uv run --no-sync gora changes --output json "$COMMIT")\n'
JOB_ENV = f"    env:\n      {SETTING}: {TOKEN_SOURCE}\n    outputs:\n"


def _move_the_token_to_the_job(text: str) -> str:
    return _replace("    outputs:\n", JOB_ENV)(_replace(TOKEN_LINE, "")(text))


def _sync_after_the_question(text: str) -> str:
    return _replace(SYNC_STEP, "")(text) + "\n" + SYNC_STEP


def _a_step_between(text: str) -> str:
    return _replace(
        "      # GORA_PROJECT is a literal",
        "      - name: Pause\n        run: echo pause\n\n      # GORA_PROJECT is a literal",
    )(text)


def _a_checkout_in_the_guard(text: str) -> str:
    step = "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    return _replace("    steps:\n      - name: Refuse", f"    steps:\n{step}      - name: Refuse")(
        text
    )


# (id, the defect as a change of the real text, the checks that find it, in order)
DEFECTS: list[tuple[str, Callable[[str], str], list[str]]] = [
    # the source of the token
    (
        "token-from-github-token",
        _replace(TOKEN_SOURCE, "${{ github.token }}"),
        ["value", "forbidden"],
    ),
    (
        "token-from-secrets-github-token",
        _replace(TOKEN_SOURCE, "${{ secrets.GITHUB_TOKEN }}"),
        ["value", "forbidden"],
    ),
    (
        "token-from-another-step",
        _replace(TOKEN_SOURCE, "${{ steps.versions.outputs.python }}"),
        ["value"],
    ),
    ("token-in-the-job-env", _move_the_token_to_the_job, ["place"]),
    (
        "token-set-twice",
        _replace("    outputs:\n", JOB_ENV),
        ["place"],
    ),
    (
        "token-assigned-in-a-script",
        _replace(ASK_SCRIPT, f'          {SETTING}="$COMMIT"\n{ASK_SCRIPT}'),
        ["place"],
    ),
    # the environment
    ("environment-removed", _replace("    environment: gora\n", ""), ["environment"]),
    (
        "environment-renamed",
        _replace("    environment: gora\n", "    environment: prod\n"),
        ["environment"],
    ),
    (
        "environment-on-the-guard",
        _replace("    timeout-minutes: 5\n", "    timeout-minutes: 5\n    environment: gora\n"),
        ["environment"],
    ),
    # the triggers
    (
        "pull-request",
        _replace(
            "  workflow_dispatch:\n",
            "  workflow_dispatch:\n  pull_request:\n    branches: [main]\n",
        ),
        ["triggers"],
    ),
    (
        "pull-request-target",
        _replace("  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request_target:\n"),
        ["triggers"],
    ),
    (
        "schedule",
        _replace(
            "  workflow_dispatch:\n", "  workflow_dispatch:\n  schedule:\n    - cron: '0 3 * * *'\n"
        ),
        ["triggers"],
    ),
    ("second-branch", _replace("branches: [main]", "branches: [main, dev]"), ["triggers"]),
    ("tags", _replace("branches: [main]", "branches: [main]\n    tags: ['v*']"), ["triggers"]),
    # the permissions of the workflow
    (
        "write-in-changes",
        _replace("      contents: read\n    outputs:", "      contents: write\n    outputs:"),
        ["permissions"],
    ),
    (
        "id-token-in-changes",
        _replace(
            "      contents: read\n    outputs:",
            "      contents: read\n      id-token: write\n    outputs:",
        ),
        ["permissions"],
    ),
    (
        "permissions-in-the-guard",
        _replace(
            "    timeout-minutes: 5\n",
            "    timeout-minutes: 5\n    permissions:\n      contents: read\n",
        ),
        ["permissions"],
    ),
    (
        "workflow-grants-read",
        _replace("permissions: {}\n", "permissions:\n  contents: read\n"),
        ["permissions"],
    ),
    # the token step
    (
        "permission-contents-write",
        _replace("permission-contents: read", "permission-contents: write"),
        ["inputs"],
    ),
    (
        "another-permission",
        _replace(
            "permission-metadata: read\n",
            "permission-metadata: read\n          permission-issues: read\n",
        ),
        ["inputs"],
    ),
    (
        "a-permission-less",
        _replace("          permission-metadata: read\n", ""),
        ["inputs"],
    ),
    (
        "a-second-repository",
        _replace("repositories: gora\n", "repositories: gora,gora-infra\n"),
        ["inputs"],
    ),
    ("another-owner", _replace("owner: gora-tools", "owner: other"), ["inputs"]),
    (
        "skip-token-revoke",
        _replace(
            "permission-metadata: read\n",
            "permission-metadata: read\n          skip-token-revoke: true\n",
        ),
        ["inputs"],
    ),
    (
        "the-key-from-elsewhere",
        _replace("secrets.GORA_APP_PRIVATE_KEY", "secrets.OTHER_KEY"),
        ["inputs"],
    ),
    (
        "packages-permission",
        _replace(
            "permission-metadata: read\n",
            "permission-metadata: read\n          permission-packages: read\n",
        ),
        ["inputs", "builds"],
    ),
    # the order
    ("a-step-between-the-token-and-the-question", _a_step_between, ["order"]),
    ("the-sync-after-the-question", _sync_after_the_question, ["order"]),
    # the clone
    ("fetch-depth-removed", _replace("          fetch-depth: 0\n", ""), ["clone"]),
    ("shallow-clone", _replace("fetch-depth: 0", "fetch-depth: 1"), ["clone"]),
    (
        "partial-clone",
        _replace(
            "          fetch-depth: 0\n", "          fetch-depth: 0\n          filter: blob:none\n"
        ),
        ["clone"],
    ),
    (
        "credentials-persist",
        _replace("persist-credentials: false", "persist-credentials: true"),
        ["clone"],
    ),
    # the project and the commit
    (
        "project-from-the-repository",
        _replace(f"GORA_PROJECT: {PROJECT}", "GORA_PROJECT: ${{ github.repository }}"),
        ["ask-env"],
    ),
    (
        "commit-from-elsewhere",
        _replace(f"COMMIT: {COMMIT}", "COMMIT: ${{ github.event.after }}"),
        ["ask-env"],
    ),
    # the guard
    ("guard-not-needed", _replace("    needs: guard\n", ""), ["guard"]),
    ("guard-checks-out", _a_checkout_in_the_guard, ["guard"]),
    ("guard-refuses-main", _replace('!= "refs/heads/main"', '== "refs/heads/main"'), ["guard"]),
    ("guard-never-refuses", _replace("            exit 1\n", "            exit 0\n"), ["guard"]),
    # the limits
    ("changes-without-a-timeout", _replace("    timeout-minutes: 10\n", ""), ["timeout"]),
    ("guard-without-a-timeout", _replace("    timeout-minutes: 5\n", ""), ["timeout"]),
    # the scripts
    (
        "expression-in-a-script",
        _replace(ASK_SCRIPT, ASK_SCRIPT.replace('"$COMMIT"', '"${{ github.sha }}"')),
        ["script"],
    ),
    (
        "trace",
        _replace(ASK_SCRIPT, f"          set -x\n{ASK_SCRIPT}"),
        ["script"],
    ),
    (
        "echo-of-the-token",
        _replace(ASK_SCRIPT, f'{ASK_SCRIPT}          echo "$GORA_GITHUB_TOKEN"\n'),
        ["script"],
    ),
    (
        "printf-of-the-token",
        _replace(ASK_SCRIPT, f'{ASK_SCRIPT}          printf "%s" "$token"\n'),
        ["script"],
    ),
    (
        "dump-of-the-environment",
        _replace(ASK_SCRIPT, f"{ASK_SCRIPT}          printenv\n"),
        ["script"],
    ),
    # nothing builds or pushes
    *[
        (
            f"a-step-that-runs-{word}",
            _append(f"\n      - name: Run it\n        run: {word} version\n"),
            ["builds"],
        )
        for word in ("docker", "crane", "cosign", "syft")
    ],
]


@pytest.mark.parametrize(
    ("text", "found"), [(d[1], d[2]) for d in DEFECTS], ids=[d[0] for d in DEFECTS]
)
def test_a_defect_is_found_by_the_check_it_is_for(
    text: Callable[[str], str], found: list[str]
) -> None:
    """Each defect is a change of the real file's text, made around the values it holds, and it
    is found by its own check and, but for the ones named, by no other: with that check deleted
    the case would pass."""
    real = _real()
    broken = text(real)
    assert broken != real
    assert set(names(problems(broken))) == set(found)


def test_the_defects_cover_every_check() -> None:
    """Every check of the file is the one a defect names, and the defects are not made twice."""
    covered = {name for _, _, found in DEFECTS for name in found}
    assert covered >= {
        "triggers",
        "permissions",
        "guard",
        "timeout",
        "environment",
        "inputs",
        "place",
        "value",
        "forbidden",
        "order",
        "clone",
        "ask-env",
        "script",
        "builds",
    }
    assert len({d[0] for d in DEFECTS}) == len(DEFECTS)


def test_a_token_step_that_is_not_the_apps_is_found() -> None:
    """A workflow with no step of the App's action has no token step: found, not skipped."""
    broken = _replace(
        "actions/create-github-app-token@f418cff399f7aef4c2f0ad47e25ca7a6abbdd6fc # v3.4.0",
        "actions/some-other-action@f418cff399f7aef4c2f0ad47e25ca7a6abbdd6fc # v3.4.0",
    )(_real())
    assert "token-step" in names(problems(broken))

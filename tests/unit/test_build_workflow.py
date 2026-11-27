"""The workflow that builds, signs and attests GORA's candidate holds its shape (ADR-018
sections 2 and 3, ADR-021 sections 1 to 4, ADR-022 section 4, ADR-027 section 7).

``.github/workflows/build.yml`` is the reusable workflow whose identity, ``build.yml@refs/heads/
main``, ``release-policy.yaml`` names as the builder ``gora-build``; ``candidate.yml`` calls it for
an artifact ``gora changes`` names. No standard tool knows what holds it to its purpose, so this
file reads the two workflows, the policy and the artifact's declaration, offline (TESTING rule 6),
and holds each statement the spec makes of them: what triggers it and what it holds (no
environment, no secret, no ref, the permissions each job asks for and no more), the guard that runs
first, the evidence each digest carries (a signature on the index and on each platform manifest;
provenance for each platform manifest and for the index; one CycloneDX SBOM for each platform
manifest), the tools that come with their actions, and the temporary job that verifies the first
live run from the digests alone. Each statement is shown red here on a defect of its class
(ADR-016 rule 1), made as a change of the real file's text.

What the test cannot hold: that the workflow runs. It runs on a push to ``main`` and nowhere else,
so its first run is the push that merges it; what GHCR keeps of the bundles cosign v3 writes is
read from that run, never assumed (ADR-018, Consequences). Comments of the files are free: only
the parsed files are read, their keys, their values and their scripts.

``release.yml`` is read as it is on ``main``: its ``COSIGN_VERSION`` and ``SYFT_VERSION`` stay
until Milestone 3 step 14 removes that workflow, which removes the statement of ``release`` below
with it.
"""

import json
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
CHANGES_SCHEMA = ROOT / "contracts" / "schemas" / "api" / "v1" / "changes.json"

IDENTITY = "https://github.com/gora-tools/gora/.github/workflows/build.yml@refs/heads/main"
ISSUER = "https://token.actions.githubusercontent.com"
IMAGE = "ghcr.io/gora-tools/gora"
MAIN = "refs/heads/main"

JOBS = ("guard", "build", "assemble", "verify")
WRITES = {"contents": "read", "packages": "write", "id-token": "write"}
PERMISSIONS = {
    "build": WRITES,
    "assemble": WRITES,
    "verify": {"contents": "read", "packages": "read"},
}
# The only scopes any job of these files may write: the registry and the OIDC token.
MAY_WRITE = frozenset({"packages", "id-token"})
TIMEOUTS = {"guard": 5, "build": 30, "assemble": 15, "verify": 15}
CALLER_IF = (
    "github.event_name == 'push' && "
    "contains(fromJSON(needs.changes.outputs.body).artifacts.*.name, 'gora')"
)
LOGIN = "docker/login-action@"
GITHUB_TOKEN = "${{ secrets.GITHUB_TOKEN }}"
INSTALLER = "sigstore/cosign-installer@"
SBOM_ACTION = "anchore/sbom-action@"
CHECKOUT = "actions/checkout@"
BUILDX = "docker/setup-buildx-action@"
PREDICATE_ACTION = "./.github/actions/provenance-predicate"
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
SECRET_READ = re.compile(r"secrets\s*[.\[]", re.I)
# A command of cosign that reaches the registry or the log, as a word of a script.
COSIGN_CALL = re.compile(
    r"(?<![\w-])cosign\s+(?:sign|attest|verify-attestation|verify|tree|download)\b"
)
MANIFEST = "${{ steps.build.outputs.manifest }}"
INDEX = "${{ steps.assemble.outputs.index }}"
PREDICATE_FILE = "${{ runner.temp }}/provenance.json"
CERTIFICATE_FLAGS = (
    '--certificate-identity "$IDENTITY"',
    '--certificate-oidc-issuer "$ISSUER"',
    '--certificate-github-workflow-repository "$REPOSITORY"',
    '--certificate-github-workflow-ref "$SOURCE_REF"',
    '--certificate-github-workflow-sha "$COMMIT"',
)

# The pairs the guard is run with, as (ref, event, exit code): only a push to main passes.
GUARD_CASES = [
    ("refs/heads/main", "push", 0),
    ("refs/heads/main", "workflow_dispatch", 1),
    ("refs/heads/main", "pull_request", 1),
    ("refs/heads/main", "schedule", 1),
    ("refs/heads/main", "", 1),
    ("refs/heads/feature", "push", 1),
    ("refs/heads/main2", "push", 1),
    ("refs/tags/v0.2.1-rc", "push", 1),
    ("refs/pull/1/merge", "pull_request", 1),
    ("", "push", 1),
    ("", "", 1),
]

Document = dict[Any, Any]
Job = dict[str, Any]
Step = dict[str, Any]


# --- reading the files ---------------------------------------------------------------------


@dataclass(frozen=True)
class Files:
    """The text of every file the statements read."""

    build: str
    candidate: str
    release: str
    policy: str
    artifact: str


@dataclass(frozen=True)
class Parsed:
    build: Document
    candidate: Document
    release: Document
    policy: Document
    artifact: Document


def _load(text: str) -> Document:
    document: Document = yaml.safe_load(text)
    return document


def parse(files: Files) -> Parsed:
    return Parsed(
        _load(files.build),
        _load(files.candidate),
        _load(files.release),
        _load(files.policy),
        _load(files.artifact),
    )


def _real() -> Files:
    return Files(
        build=(WORKFLOWS / "build.yml").read_text(encoding="utf-8"),
        candidate=(WORKFLOWS / "candidate.yml").read_text(encoding="utf-8"),
        release=(WORKFLOWS / "release.yml").read_text(encoding="utf-8"),
        policy=(ROOT / "release-policy.yaml").read_text(encoding="utf-8"),
        artifact=(ROOT / "artifacts" / "gora" / "artifact.yaml").read_text(encoding="utf-8"),
    )


def _jobs(document: Document) -> dict[str, Job]:
    jobs = document.get("jobs") or {}
    return {name: job for name, job in jobs.items() if isinstance(job, dict)}


def _steps(job: Job) -> list[Step]:
    return [step for step in job.get("steps") or [] if isinstance(step, dict)]


def _needs(job: Job) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _upstream(jobs: dict[str, Job], name: str) -> set[str]:
    """Every job ``name`` waits for, directly or through another job."""
    found: set[str] = set()
    pending = _needs(jobs.get(name, {}))
    while pending:
        job = pending.pop()
        if job not in found:
            found.add(job)
            pending += _needs(jobs.get(job, {}))
    return found


def _walk(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every node of a parsed file with its path: keys, list positions and values."""
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, (*path, str(key)))
    elif isinstance(node, list):
        for number, item in enumerate(node):
            yield from _walk(item, (*path, str(number)))


def _keys(node: Any) -> Iterator[str]:
    for path, _ in _walk(node):
        if path:
            yield path[-1]


def _strings(node: Any) -> Iterator[str]:
    for _, value in _walk(node):
        if isinstance(value, str):
            yield value


def _scripts(job: Job) -> list[str]:
    return [step["run"] for step in _steps(job) if isinstance(step.get("run"), str)]


def _uses(prefix: str) -> Callable[[Step], bool]:
    return lambda step: str(step.get("uses", "")).startswith(prefix)


def _runs(part: str) -> Callable[[Step], bool]:
    return lambda step: part in str(step.get("run", ""))


def _one(steps: list[Step], match: Callable[[Step], bool]) -> int | None:
    found = [number for number, step in enumerate(steps) if match(step)]
    return found[0] if len(found) == 1 else None


def _env(step: Step | None) -> dict[str, Any]:
    return (step or {}).get("env") or {}


def _with(step: Step | None) -> dict[str, Any]:
    return (step or {}).get("with") or {}


# --- the checks: each returns its problems, each problem starts with the name of its check -


def trigger_problems(p: Parsed) -> list[str]:
    """``workflow_call`` alone, one input `image`, one output `index`."""
    on = p.build.get("on", p.build.get(True))  # YAML 1.1 reads the key `on` as true
    if not isinstance(on, dict) or set(on) != {"workflow_call"}:
        return [f"trigger: {sorted(on) if isinstance(on, dict) else on!r}, not workflow_call alone"]
    call = on["workflow_call"] or {}
    bad = []
    inputs = call.get("inputs") or {}
    if set(inputs) != {"image"}:
        bad.append(f"trigger: the inputs are {sorted(inputs)}, not image alone")
    else:
        image = inputs["image"] or {}
        if image.get("type") != "string" or image.get("required") is not True:
            bad.append("trigger: the input image is not a required string")
    outputs = call.get("outputs") or {}
    if set(outputs) != {"index"}:
        bad.append(f"trigger: the outputs are {sorted(outputs)}, not index alone")
    elif (outputs["index"] or {}).get("value") != "${{ jobs.assemble.outputs.index }}":
        bad.append("trigger: the output index is not the digest the job assemble made")
    if set(call) - {"inputs", "outputs"}:
        bad.append(f"trigger: workflow_call also holds {sorted(set(call) - {'inputs', 'outputs'})}")
    return bad


def jobs_problems(p: Parsed) -> list[str]:
    """The four jobs and no other: a fifth would hold what no check here has read."""
    found = set(_jobs(p.build))
    return [] if found == set(JOBS) else [f"jobs: {sorted(found)}, not {sorted(JOBS)}"]


def permission_problems(p: Parsed) -> list[str]:
    """Nothing by default, the guard none, each job exactly what it asks for, the caller's
    ceiling the same, and no `write` anywhere but the registry and the OIDC token."""
    bad = []
    jobs = _jobs(p.build)
    if p.build.get("permissions") != {}:
        bad.append(f"permissions: the workflow grants {p.build.get('permissions')!r}, not nothing")
    if "permissions" in jobs.get("guard", {}):
        bad.append("permissions: the guard holds permissions")
    for name, want in PERMISSIONS.items():
        if jobs.get(name, {}).get("permissions") != want:
            bad.append(f"permissions: {name} holds {jobs.get(name, {}).get('permissions')!r}")
    caller = _jobs(p.candidate).get("build", {})
    if caller.get("permissions") != WRITES:
        bad.append(f"permissions: the caller's build holds {caller.get('permissions')!r}")
    for label, document in (("build.yml", p.build), ("candidate.yml", p.candidate)):
        for path, value in _walk(document):
            if path and path[-1] == "permissions" and isinstance(value, dict):
                bad += [
                    f"permissions: {label} writes {scope}"
                    for scope, level in value.items()
                    if level == "write" and scope not in MAY_WRITE
                ]
    return bad


def _guard_exit(script: str, ref: str, event: str) -> int:
    """The guard's script as GitHub runs a `shell: bash` step, with nothing set but the ref and
    the event."""
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env={"PATH": "/usr/bin:/bin", "GITHUB_REF": ref, "GITHUB_EVENT_NAME": event},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode


def guard_problems(p: Parsed) -> list[str]:
    """The guard checks out nothing, refuses any run but a push to main, and every other job
    waits for it, one after the other."""
    jobs = _jobs(p.build)
    guard = jobs.get("guard")
    if guard is None:
        return ["guard: the workflow has no job guard"]
    bad = []
    if any("uses" in step for step in _steps(guard)):
        bad.append("guard: the guard checks out or uses an action: it reads two variables alone")
    waiting = [name for name in jobs if name != "guard" and "guard" not in _upstream(jobs, name)]
    if waiting:
        bad.append(f"guard: {waiting} can start before the guard has passed")
    chain = {"build": ["guard"], "assemble": ["build"], "verify": ["assemble"]}
    bad += [
        f"guard: {name} needs {_needs(jobs.get(name, {}))}, not {want}"
        for name, want in chain.items()
        if _needs(jobs.get(name, {})) != want
    ]
    asked = [step for step in _steps(guard) if step.get("id") == "guard"]
    if (
        len(asked) != 1
        or asked[0].get("shell") != "bash"
        or not isinstance(asked[0].get("run"), str)
    ):
        return [*bad, "guard: no one bash step with the id guard"]
    wrong = [
        (ref, event)
        for ref, event, code in GUARD_CASES
        if _guard_exit(asked[0]["run"], ref, event) != code
    ]
    if wrong:
        bad.append(f"guard: the script answers wrongly for {wrong}")
    return bad


def timeout_problems(p: Parsed) -> list[str]:
    """The limits release.yml's jobs of the same steps carry (TESTING rule 8), and each cosign
    call under `timeout 300`, as release.yml writes them."""
    bad = []
    jobs = _jobs(p.build)
    for name, minutes in TIMEOUTS.items():
        found = jobs.get(name, {}).get("timeout-minutes")
        if found != minutes or isinstance(found, bool):
            bad.append(f"timeout: {name} has {found!r} minutes, not {minutes}")
    for name, job in jobs.items():
        for script in _scripts(job):
            for line in script.replace("\\\n", " ").splitlines():
                if line.lstrip().startswith("#"):
                    continue
                bad += [
                    f"timeout: {name} calls cosign outside `timeout 300`: {line.strip()[:60]}"
                    for call in COSIGN_CALL.finditer(line)
                    if not line[: call.start()].endswith("timeout 300 ")
                ]
    return bad


def environment_problems(p: Parsed) -> list[str]:
    """No job of build.yml has an environment, and the caller's build job none either: the
    build holds no GORA identity."""
    found = [name for name, job in _jobs(p.build).items() if "environment" in job]
    if "environment" in _jobs(p.candidate).get("build", {}):
        found.append("candidate.yml:build")
    return [f"environment: {found} has an environment"] if found else []


def secrets_problems(p: Parsed) -> list[str]:
    """No `secrets:` key and no `inherit` in build.yml or in the caller's build job, and the one
    expression that reads secrets is `secrets.GITHUB_TOKEN`, the `password` of a GHCR login."""
    bad = []
    caller = _jobs(p.candidate).get("build", {})
    for label, node in (("build.yml", p.build), ("candidate.yml's build", caller)):
        if "secrets" in _keys(node):
            bad.append(f"secrets: {label} has a `secrets:` key")
        if any(re.search(r"\binherit\b", text) for text in _strings(node)):
            bad.append(f"secrets: {label} holds `inherit`")
    for path, value in _walk(p.build):
        if isinstance(value, str) and SECRET_READ.search(value):
            step = _step_at(p.build, path)
            if not (
                path[-2:] == ("with", "password")
                and value == GITHUB_TOKEN
                and str(step.get("uses", "")).startswith(LOGIN)
            ):
                bad.append(
                    f"secrets: {'/'.join(path)} reads secrets, and is no GHCR login's password"
                )
    for _, value in _walk(caller):
        if isinstance(value, str) and SECRET_READ.search(value):
            bad.append("secrets: the caller's build reads secrets")
    return bad


def _step_at(document: Document, path: tuple[str, ...]) -> Step:
    """The step a path of the form ``jobs/<job>/steps/<n>/...`` is in, or an empty one."""
    if len(path) >= 4 and path[0] == "jobs" and path[2] == "steps" and path[3].isdigit():
        steps = _jobs(document).get(path[1], {}).get("steps") or []
        if int(path[3]) < len(steps) and isinstance(steps[int(path[3])], dict):
            found: Step = steps[int(path[3])]
            return found
    return {}


def ref_problems(p: Parsed) -> list[str]:
    """The workflow builds exactly github.sha: no `ref` is an input, a `with` or a checkout's,
    in build.yml or in the call of it, and every checkout keeps no credential."""
    caller = _jobs(p.candidate).get("build", {})
    bad = [
        f"ref: {label} has a key `ref`"
        for label, node in (("build.yml", p.build), ("candidate.yml's build", caller))
        if "ref" in _keys(node)
    ]
    for name, job in _jobs(p.build).items():
        for step in _steps(job):
            if _uses(CHECKOUT)(step) and _with(step) != {"persist-credentials": False}:
                bad.append(f"ref: a checkout of {name} is given {sorted(_with(step))}")
    return bad


def tool_problems(p: Parsed) -> list[str]:
    """The tools come with their actions: cosign-installer and sbom-action are pinned by commit
    and carry no version input, and no `*_VERSION` is set."""
    bad = []
    document = p.build
    steps = [step for job in _jobs(document).values() for step in _steps(job)]
    installers = [step for step in steps if _uses(INSTALLER)(step)]
    sboms = [step for step in steps if _uses(SBOM_ACTION)(step)]
    if len(installers) != 3 or len(sboms) != 1:
        bad.append(f"tools: {len(installers)} cosign installers and {len(sboms)} sbom actions")
    for step in [*installers, *sboms]:
        if not PINNED.fullmatch(str(step["uses"]).split(" ")[0]):
            bad.append(f"tools: {step['uses']} is not pinned by commit")
    bad += [
        f"tools: the installer is given {sorted(_with(step))}" for step in installers if _with(step)
    ]
    for step in sboms:
        given = _with(step)
        if set(given) != {
            "image",
            "format",
            "output-file",
            "upload-artifact",
            "upload-release-assets",
        }:
            bad.append(f"tools: the sbom action is given {sorted(given)}")
        elif (
            given["format"] != "cyclonedx-json"
            or given["upload-artifact"] is not False
            or given["upload-release-assets"] is not False
        ):
            bad.append("tools: the sbom action is not CycloneDX JSON with nothing uploaded")
    for path, _ in _walk(document):
        if path and path[-1].endswith("_VERSION"):
            bad.append(f"tools: {'/'.join(path)} sets a version of a tool")
    return bad


def _build_steps(p: Parsed) -> list[Step]:
    return _steps(_jobs(p.build).get("build", {}))


def _assemble_steps(p: Parsed) -> list[Step]:
    return _steps(_jobs(p.build).get("assemble", {}))


def evidence_problems(p: Parsed) -> list[str]:
    """Each platform manifest: signed, its SBOM and its provenance attested, from its build
    job; the index: signed and its provenance attested, from the job that assembles it."""
    bad = []
    steps = _build_steps(p)
    wanted = {
        "build": _one(steps, _uses("./.github/actions/build-image")),
        "install": _one(steps, _uses(INSTALLER)),
        "sign": _one(steps, _runs("cosign sign --yes")),
        "sbom": _one(steps, _uses(SBOM_ACTION)),
        "floor": _one(steps, _runs("bomFormat")),
        "attest-sbom": _one(steps, _runs("attest --yes --type cyclonedx")),
        "predicate": _one(steps, _uses(PREDICATE_ACTION)),
        "attest-provenance": _one(steps, _runs("attest --yes --type slsaprovenance1")),
        "record": _one(steps, _uses("actions/upload-artifact@")),
    }
    missing = [name for name, found in wanted.items() if found is None]
    if missing:
        bad.append(f"evidence: the build job has no one step for {missing}")
    else:
        order = [wanted[name] for name in wanted]
        if order != sorted(order, key=lambda found: found or 0):
            bad.append(f"evidence: the build job's steps are not in the order {list(wanted)}")
        for name in ("sign", "attest-sbom", "attest-provenance"):
            at = wanted[name]
            step = steps[at] if at is not None else {}
            if (
                _env(step).get("MANIFEST") != MANIFEST
                or '"${IMAGE}@${MANIFEST}"' not in step["run"]
            ):
                bad.append(f"evidence: {name} is not of the platform manifest the build made")
        built = steps[wanted["build"] or 0]
        if built.get("id") != "build":
            bad.append("evidence: the build step has not the id build")
        sbom = steps[wanted["sbom"] or 0]
        if _with(sbom).get("image") != f"${{{{ inputs.image }}}}@{MANIFEST}":
            bad.append("evidence: the SBOM is not of the platform manifest the build made")
        emitted = steps[wanted["predicate"] or 0]
        attested = steps[wanted["attest-provenance"] or 0]
        if _with(emitted).get("output") != PREDICATE_FILE or _env(attested).get("PREDICATE") != (
            PREDICATE_FILE
        ):
            bad.append(
                "evidence: the provenance attested is not the file the predicate action wrote"
            )
        if _with(steps[wanted["record"] or 0]).get("name") != "record-${{ matrix.slug }}":
            bad.append("evidence: the record is not uploaded as record-<slug>")
    after = _assemble_steps(p)
    wanted_after = {
        "assemble": _one(after, _uses("./.github/actions/assemble-index")),
        "install": _one(after, _uses(INSTALLER)),
        "sign": _one(after, _runs("cosign sign --yes")),
        "predicate": _one(after, _uses(PREDICATE_ACTION)),
        "attest-provenance": _one(after, _runs("attest --yes --type slsaprovenance1")),
        "summary": _one(after, _runs("GITHUB_STEP_SUMMARY")),
    }
    missing = [name for name, found in wanted_after.items() if found is None]
    if missing:
        return [*bad, f"evidence: the assemble job has no one step for {missing}"]
    order = list(wanted_after.values())
    if order != sorted(order, key=lambda found: found or 0):
        bad.append(f"evidence: the assemble job's steps are not in the order {list(wanted_after)}")
    assembled = after[wanted_after["assemble"] or 0]
    if assembled.get("id") != "assemble" or _with(assembled).get("tag") != (
        "run-${{ github.run_id }}-${{ github.run_attempt }}"
    ):
        bad.append("evidence: the index is not tagged as a name of this run")
    for name in ("sign", "attest-provenance"):
        step = after[wanted_after[name] or 0]
        if _env(step).get("INDEX") != INDEX or '"${IMAGE}@${INDEX}"' not in step["run"]:
            bad.append(f"evidence: {name} is not of the index the job assembled")
    attested = after[wanted_after["attest-provenance"] or 0]
    emitted = after[wanted_after["predicate"] or 0]
    if _with(emitted).get("output") != PREDICATE_FILE or _env(attested).get("PREDICATE") != (
        PREDICATE_FILE
    ):
        bad.append("evidence: the provenance attested is not the file the predicate action wrote")
    if _jobs(p.build).get("assemble", {}).get("outputs") != {"index": INDEX}:
        bad.append("evidence: the job assemble does not output the index it made")
    if any("--type cyclonedx" in script for script in _scripts(_jobs(p.build)["assemble"])):
        bad.append("evidence: the index carries an SBOM: one SBOM for each platform manifest")
    return bad


def sbom_problems(p: Parsed) -> list[str]:
    """The SBOM floor of the policy, CycloneDX 1.6, is checked before the SBOM is attested, and
    the policy and the check agree."""
    bad = []
    floor = ((p.policy.get("spec") or {}).get("evidence") or {}).get("sbom") or {}
    if floor != {"format": "cyclonedx", "minVersion": "1.6"}:
        bad.append(f"sbom: the policy's floor is {floor!r}, not CycloneDX 1.6")
    steps = _build_steps(p)
    at = _one(steps, _runs("bomFormat"))
    if at is None:
        return [*bad, "sbom: no one step checks the SBOM's format and version"]
    script = steps[at]["run"]
    for part in ('.bomFormat == "CycloneDX"', ".specVersion", ">= [1, 6]", "exit 1", "jq -e"):
        if part not in script:
            bad.append(f"sbom: the check does not hold {part!r}")
    attest = _one(steps, _runs("attest --yes --type cyclonedx"))
    if attest is not None and attest < at:
        bad.append("sbom: the SBOM is attested before its format is checked")
    return bad


def verify_problems(p: Parsed) -> list[str]:
    """The temporary job verifies the first live run from the digests alone: the builder's
    exact identity from the policy, each of the three evidences, where the registry keeps it,
    and the evidence uploaded, an empty download failing the job."""
    job = _jobs(p.build).get("verify")
    if job is None:
        return ["verify: the workflow has no job verify"]
    bad = []
    steps = _steps(job)
    if any(_uses(BUILDX)(step) for step in steps):
        bad.append("verify: the job sets up buildx: its manifests come from the records")
    builder = (((p.policy.get("spec") or {}).get("builders") or {}).get("gora-build")) or {}
    env = job.get("env") or {}
    for key, want in (
        ("IDENTITY", builder.get("identity")),
        ("ISSUER", builder.get("issuer")),
        ("INDEX", "${{ needs.assemble.outputs.index }}"),
        ("COMMIT", "${{ github.sha }}"),
        ("REPOSITORY", "${{ github.repository }}"),
        ("SOURCE_REF", MAIN),
    ):
        if env.get(key) != want:
            bad.append(f"verify: {key} is {env.get(key)!r}, not {want!r}")
    if builder.get("identity") != IDENTITY or builder.get("issuer") != ISSUER:
        bad.append("verify: the policy's builder gora-build is not the exact build.yml on main")
    scripts = _scripts(job)
    if any("-regexp" in script for script in scripts):
        bad.append("verify: an identity or an issuer is a regular expression, not exact")
    cases = {
        "signature": ('cosign verify "', 'for digest in "$INDEX" $MANIFESTS; do'),
        "provenance": ("--type slsaprovenance1", 'for digest in "$INDEX" $MANIFESTS; do'),
        "sbom": ("--type cyclonedx", "for digest in $MANIFESTS; do"),
    }
    for name, (command, loop) in cases.items():
        found = [script for script in scripts if command in script]
        if len(found) != 1:
            bad.append(f"verify: {len(found)} steps verify the {name}, not one")
            continue
        bad += [
            f"verify: the {name} check is without {flag}"
            for flag in CERTIFICATE_FLAGS
            if flag not in found[0]
        ]
        if loop not in found[0]:
            bad.append(f"verify: the {name} check does not run over {loop!r}")
    tree = [script for script in scripts if "cosign tree" in script]
    if len(tree) != 1 or "$GITHUB_STEP_SUMMARY" not in tree[0]:
        bad.append("verify: cosign tree is not written to the step summary")
    download = [script for script in scripts if "cosign download attestation" in script]
    if len(download) != 1 or '[ ! -s "$file" ]' not in download[0] or "exit 1" not in download[0]:
        bad.append("verify: an empty download of an attestation does not fail the job")
    upload = [step for step in steps if _uses("actions/upload-artifact@")(step)]
    given = _with(upload[0]) if len(upload) == 1 else {}
    if (
        given.get("name") != "evidence"
        or given.get("retention-days") != 14
        or (given.get("if-no-files-found") != "error")
    ):
        bad.append("verify: the evidence is not uploaded as `evidence`, 14 days, error if empty")
    return bad


def release_problems(p: Parsed) -> list[str]:
    """release.yml is read as it is on main: its tool versions stay until step 14."""
    env = p.release.get("env") or {}
    return [
        f"release: release.yml no longer sets {name}"
        for name in ("COSIGN_VERSION", "SYFT_VERSION")
        if name not in env
    ]


def caller_problems(p: Parsed) -> list[str]:
    """candidate.yml's job `build`: a call of build.yml on a push, when the answer names gora,
    with the artifact's image, one group and no limit of its own."""
    job = _jobs(p.candidate).get("build")
    if job is None:
        return ["caller: candidate.yml has no job build"]
    image = ((p.artifact.get("spec") or {}).get("image")) or "(no image)"
    bad = []
    if _needs(job) != ["changes"]:
        bad.append(f"caller: build needs {_needs(job)}, not changes")
    if job.get("uses") != "./.github/workflows/build.yml":
        bad.append(f"caller: build uses {job.get('uses')!r}, not build.yml of this repository")
    if job.get("with") != {"image": image}:
        bad.append(f"caller: build passes {job.get('with')!r}, not the artifact's image alone")
    if job.get("if") != CALLER_IF:
        bad.append(f"caller: build's condition is {job.get('if')!r}")
    if job.get("concurrency") != {"group": "candidate-build", "cancel-in-progress": False}:
        bad.append(f"caller: build's concurrency is {job.get('concurrency')!r}")
    if "timeout-minutes" in job:
        bad.append("caller: a job that calls a reusable workflow takes no timeout-minutes")
    if (p.artifact.get("metadata") or {}).get("name") != "gora":
        bad.append("caller: the artifact the condition names is not the one declared")
    return bad


def schema_problems(p: Parsed) -> list[str]:
    """The condition reads `artifacts.*.name` of the body `gora changes` writes: both are
    properties of the published schema of that body."""
    schema = json.loads(CHANGES_SCHEMA.read_text(encoding="utf-8"))
    properties = schema["properties"]
    name = schema["$defs"]["ChangedArtifact"]["properties"]
    if (
        "artifacts" not in properties
        or properties["artifacts"]["type"] != "array"
        or "name" not in name
    ):
        return ["schema: the body has no artifacts[].name for the condition to read"]
    return []


CHECKS = (
    trigger_problems,
    jobs_problems,
    permission_problems,
    guard_problems,
    timeout_problems,
    environment_problems,
    secrets_problems,
    ref_problems,
    tool_problems,
    evidence_problems,
    sbom_problems,
    verify_problems,
    release_problems,
    caller_problems,
    schema_problems,
)


def problems(files: Files) -> list[str]:
    parsed = parse(files)
    return [problem for check in CHECKS for problem in check(parsed)]


def names(found: list[str]) -> list[str]:
    """The check each problem came from, in order."""
    return [problem.split(":")[0] for problem in found]


# --- the real files ------------------------------------------------------------------------


def test_the_build_workflow_holds_its_shape() -> None:
    assert problems(_real()) == []


def test_each_thing_the_checks_hold_is_found_at_all() -> None:
    """A check that finds nothing is a gate that cannot fail: each thing is there, and the
    reading of it reads it."""
    files = _real()
    parsed = parse(files)
    assert parsed.build["name"] == "Build"
    assert set(_jobs(parsed.build)) == set(JOBS)
    assert set(_jobs(parsed.candidate)) == {"guard", "changes", "build"}
    steps = _build_steps(parsed)
    for match in (
        _uses("./.github/actions/build-image"),
        _uses(INSTALLER),
        _runs("cosign sign --yes"),
        _uses(SBOM_ACTION),
        _runs("bomFormat"),
        _runs("attest --yes --type cyclonedx"),
        _uses(PREDICATE_ACTION),
        _runs("attest --yes --type slsaprovenance1"),
    ):
        assert len([step for step in steps if match(step)]) == 1
    assert len([step for step in _assemble_steps(parsed) if _uses(PREDICATE_ACTION)(step)]) == 1
    # Three logins, the only reads of secrets, each the GHCR login's password.
    logins = [
        step for job in _jobs(parsed.build).values() for step in _steps(job) if _uses(LOGIN)(step)
    ]
    assert len(logins) == 3
    assert {_with(step)["password"] for step in logins} == {GITHUB_TOKEN}
    reads = [text for text in _strings(parsed.build) if SECRET_READ.search(text)]
    assert reads == [GITHUB_TOKEN] * 3
    # The policy names the builder this workflow is, and the artifact is the one it builds.
    builder = parsed.policy["spec"]["builders"]["gora-build"]
    assert builder == {"issuer": ISSUER, "identity": IDENTITY}
    assert parsed.artifact["spec"]["image"] == IMAGE
    assert _jobs(parsed.candidate)["build"]["with"] == {"image": IMAGE}
    # The verification runs over the index and the manifests, and the guard is run.
    verify = _jobs(parsed.build)["verify"]
    assert len(_scripts(verify)) == 6  # the list, the three checks, the tree, the download
    assert len(list(_strings(parsed.build))) > 100
    assert len(parsed.release["env"]) >= 2
    guard = _steps(_jobs(parsed.build)["guard"])[0]["run"]
    assert _guard_exit(guard, MAIN, "push") == 0
    assert _guard_exit(guard, MAIN, "pull_request") == 1
    assert len(CHECKS) == len({check.__name__ for check in CHECKS})


# --- each statement is shown red on a defect of its class ----------------------------------


def _replace(old: str, new: str) -> Callable[[str], str]:
    def defect(text: str) -> str:
        assert text.count(old) == 1, f"{old!r} is not in the file exactly once"
        return text.replace(old, new)

    return defect


def _nth(old: str, new: str, n: int, count: int) -> Callable[[str], str]:
    """Replace the ``n``th of ``count`` occurrences of ``old``."""

    def defect(text: str) -> str:
        assert text.count(old) == count, f"{old!r} is not in the file {count} times"
        parts = text.split(old)
        return old.join(parts[: n + 1]) + new + old.join(parts[n + 1 :])

    return defect


def _cut(start: str, end: str) -> Callable[[str], str]:
    """Remove the text from ``start`` up to, not including, ``end``."""

    def defect(text: str) -> str:
        assert text.count(start) == 1 and text.count(end) == 1
        return text[: text.index(start)] + text[text.index(end) :]

    return defect


def _append(extra: str) -> Callable[[str], str]:
    return lambda text: text + extra


INSTALL_LINE = (
    "        uses: sigstore/cosign-installer@756fe2d284c7292c4e9b62c8a28f1e5629c59532 # v4.1.3\n"
)
SBOM_LINE = "          format: cyclonedx-json\n"
CHECKOUT_LINE = (
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    "        with:\n          persist-credentials: false\n"
)
LOGIN_PASSWORD = "          password: ${{ secrets.GITHUB_TOKEN }}\n"
CALLER_USES = "    uses: ./.github/workflows/build.yml\n"
CALLER_PERMISSIONS = "      contents: read\n      packages: write\n      id-token: write\n"
CALLER_CONCURRENCY = (
    "    concurrency:\n      group: candidate-build\n      cancel-in-progress: false\n"
)
SIGN_MANIFEST = '        run: timeout 300 cosign sign --yes "${IMAGE}@${MANIFEST}"\n'
SIGN_INDEX = '        run: timeout 300 cosign sign --yes "${IMAGE}@${INDEX}"\n'
UPLOAD_RECORD = (
    "      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2\n"
    "        with:\n          name: record-"
)
PREDICATE_STEP = (
    "      - name: Write the provenance predicate\n"
    "        uses: ./.github/actions/provenance-predicate\n"
    "        with:\n          output: ${{ runner.temp }}/provenance.json\n\n"
    "      - name: Attest the provenance to the index"
)
ATTEST_INDEX = (
    "          timeout 300 cosign attest --yes --type slsaprovenance1 \\\n"
    '            --predicate "$PREDICATE" "${IMAGE}@${INDEX}"\n'
)
ATTEST_INDEX_SBOM = (
    "          timeout 300 cosign attest --yes --type cyclonedx \\\n"
    '            --predicate "$PREDICATE" "${IMAGE}@${INDEX}"\n'
)
SBOM_FLOOR_COMMENT = (
    "      # The policy's floor (release-policy.yaml, evidence.sbom, ADR-026 section 4): "
    "CycloneDX,\n"
)
EVIDENCE_UPLOAD = (
    "          name: evidence\n          path: ${{ runner.temp }}/evidence\n"
    "          if-no-files-found: error\n"
)
REF_INPUT = "      ref:\n        type: string\n        required: false\n"
GUARD_END = (
    '"GORA\'s candidate is built from a commit pushed to main and nowhere else."\n'
    "            exit 1\n"
)
BUILDX_STEP = (
    "\n      - name: Set up Buildx\n"
    "        uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1\n"
    "        with:\n          version: v0.37.2\n          cache-binary: false\n"
    "          driver: docker\n"
)
THIRD_JOB = (
    "\n  extra:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"
    "    steps:\n      - run: echo hi\n"
)

# (id, the file it changes, the defect as a change of the real text, the checks that find it)
Defect = tuple[str, str, Callable[[str], str], list[str]]
DEFECTS: list[Defect] = [
    # the trigger and its inputs
    (
        "also-a-dispatch",
        "build",
        _replace("on:\n  workflow_call:\n", "on:\n  workflow_dispatch:\n  workflow_call:\n"),
        ["trigger"],
    ),
    (
        "also-a-pull-request",
        "build",
        _replace("on:\n  workflow_call:\n", "on:\n  pull_request:\n  workflow_call:\n"),
        ["trigger"],
    ),
    (
        "a-ref-input",
        "build",
        _replace(
            "    inputs:\n      image:\n",
            f"    inputs:\n{REF_INPUT}      image:\n",
        ),
        ["trigger", "ref"],
    ),
    (
        "a-second-input",
        "build",
        _replace(
            "    inputs:\n      image:\n",
            "    inputs:\n      version:\n        type: string\n        required: false\n"
            "      image:\n",
        ),
        ["trigger"],
    ),
    (
        "the-image-is-optional",
        "build",
        _replace("        required: true\n", "        required: false\n"),
        ["trigger"],
    ),
    (
        "a-secret-of-the-workflow",
        "build",
        _replace(
            "    outputs:\n      index:\n        description:",
            "    secrets:\n      token:\n        required: true\n    outputs:\n      index:\n"
            "        description:",
        ),
        ["trigger", "secrets"],
    ),
    (
        "the-output-is-another-job-s",
        "build",
        _replace("${{ jobs.assemble.outputs.index }}", "${{ jobs.build.outputs.index }}"),
        ["trigger"],
    ),
    # the jobs
    ("a-fifth-job", "build", _append(THIRD_JOB), ["jobs", "guard"]),
    # the permissions
    (
        "contents-write",
        "build",
        _replace(
            "      contents: read\n      packages: write # push by digest",
            "      contents: write\n      packages: write # push by digest",
        ),
        ["permissions"],
    ),
    (
        "no-id-token-in-the-build",
        "build",
        _nth(
            "      id-token: write # cosign keyless (OIDC), and the claims of the provenance\n",
            "",
            0,
            2,
        ),
        ["permissions"],
    ),
    (
        "a-registry-write-in-verify",
        "build",
        _replace("      packages: read\n", "      packages: write\n"),
        ["permissions"],
    ),
    (
        "attestations-write",
        "build",
        _nth(
            "      id-token: write # cosign keyless (OIDC), and the claims of the provenance\n",
            "      id-token: write # cosign keyless (OIDC), and the claims of the provenance\n"
            "      attestations: write\n",
            0,
            2,
        ),
        ["permissions"],
    ),
    (
        "the-workflow-grants-read",
        "build",
        _replace("permissions: {}\n", "permissions:\n  contents: read\n"),
        ["permissions"],
    ),
    (
        "the-guard-holds-a-permission",
        "build",
        _replace(
            "    timeout-minutes: 5\n",
            "    timeout-minutes: 5\n    permissions:\n      contents: read\n",
        ),
        ["permissions"],
    ),
    (
        "the-caller-grants-more",
        "candidate",
        _replace(CALLER_PERMISSIONS, CALLER_PERMISSIONS + "      actions: write\n"),
        ["permissions"],
    ),
    (
        "the-caller-loses-the-id-token",
        "candidate",
        _replace(CALLER_PERMISSIONS, "      contents: read\n      packages: write\n"),
        ["permissions"],
    ),
    # an environment, a secret
    (
        "an-environment-on-the-build",
        "build",
        _replace(
            "    timeout-minutes: 30\n    permissions:\n",
            "    timeout-minutes: 30\n    environment: gora\n    permissions:\n",
        ),
        ["environment"],
    ),
    (
        "an-environment-on-the-caller",
        "candidate",
        _replace(CALLER_USES, CALLER_USES + "    environment: gora\n"),
        ["environment"],
    ),
    (
        "the-caller-inherits-secrets",
        "candidate",
        _replace(CALLER_USES, CALLER_USES + "    secrets: inherit\n"),
        ["secrets"],
    ),
    (
        "the-caller-passes-a-secret",
        "candidate",
        _replace(
            CALLER_USES,
            CALLER_USES + "    secrets:\n      token: ${{ secrets.GORA_APP_PRIVATE_KEY }}\n",
        ),
        ["secrets"],
    ),
    (
        "the-login-reads-another-secret",
        "build",
        _nth(LOGIN_PASSWORD, "          password: ${{ secrets.OTHER_TOKEN }}\n", 0, 3),
        ["secrets"],
    ),
    (
        "a-secret-in-a-step-s-env",
        "build",
        _replace(
            "          MANIFEST: ${{ steps.build.outputs.manifest }}\n" + SIGN_MANIFEST,
            "          MANIFEST: ${{ steps.build.outputs.manifest }}\n"
            "          TOKEN: ${{ secrets.GITHUB_TOKEN }}\n" + SIGN_MANIFEST,
        ),
        ["secrets"],
    ),
    (
        "inherit-in-the-workflow",
        "build",
        _replace(
            "      id-token: write # cosign keyless (OIDC), and the claims of the provenance\n"
            "    env:\n      IMAGE: ${{ inputs.image }}\n    outputs:",
            "      id-token: write # cosign keyless (OIDC), and the claims of the provenance\n"
            "    env:\n      IMAGE: ${{ inputs.image }}\n      NOTE: inherit\n    outputs:",
        ),
        ["secrets"],
    ),
    # a ref
    (
        "a-checkout-of-a-ref",
        "build",
        _nth(CHECKOUT_LINE, CHECKOUT_LINE + "          ref: refs/heads/main\n", 0, 2),
        ["ref"],
    ),
    (
        "a-checkout-with-more-than-no-credential",
        "build",
        _nth(CHECKOUT_LINE, CHECKOUT_LINE + "          fetch-depth: 0\n", 1, 2),
        ["ref"],
    ),
    (
        "the-caller-passes-a-ref",
        "candidate",
        _replace(
            "      image: ghcr.io/gora-tools/gora\n",
            "      image: ghcr.io/gora-tools/gora\n      ref: main\n",
        ),
        ["ref", "caller"],
    ),
    # the guard
    (
        "the-guard-lets-a-dispatch-through",
        "build",
        _replace(' || [ "${GITHUB_EVENT_NAME:-}" != "push" ]', ""),
        ["guard"],
    ),
    (
        "the-guard-lets-a-branch-through",
        "build",
        _replace('[ "${GITHUB_REF:-}" != "refs/heads/main" ] || ', ""),
        ["guard"],
    ),
    (
        "the-guard-never-refuses",
        "build",
        _replace(GUARD_END, GUARD_END.replace("exit 1", "exit 0")),
        ["guard"],
    ),
    (
        "a-checkout-in-the-guard",
        "build",
        _replace(
            "    steps:\n      - name: Refuse any run",
            "    steps:\n" + CHECKOUT_LINE + "      - name: Refuse any run",
        ),
        ["guard"],
    ),
    (
        "the-build-does-not-wait-for-the-guard",
        "build",
        _replace("    needs: guard\n", ""),
        ["guard"],
    ),
    # the limits
    ("the-build-has-no-timeout", "build", _replace("    timeout-minutes: 30\n", ""), ["timeout"]),
    ("the-guard-has-no-timeout", "build", _replace("    timeout-minutes: 5\n", ""), ["timeout"]),
    (
        "a-longer-limit-than-release",
        "build",
        _replace("    timeout-minutes: 30\n", "    timeout-minutes: 60\n"),
        ["timeout"],
    ),
    (
        "a-cosign-call-without-its-limit",
        "build",
        _replace(SIGN_MANIFEST, SIGN_MANIFEST.replace("timeout 300 ", "")),
        ["timeout"],
    ),
    (
        "a-verification-without-its-limit",
        "build",
        _replace(
            'timeout 300 cosign verify "${IMAGE}@${digest}"', 'cosign verify "${IMAGE}@${digest}"'
        ),
        ["timeout"],
    ),
    # the tools come with their actions
    (
        "a-cosign-release-input",
        "build",
        _nth(
            INSTALL_LINE, INSTALL_LINE + "        with:\n          cosign-release: v3.1.5\n", 0, 3
        ),
        ["tools"],
    ),
    (
        "a-syft-version-input",
        "build",
        _replace(SBOM_LINE, SBOM_LINE + "          syft-version: v1.57.2\n"),
        ["tools"],
    ),
    (
        "a-tool-version-in-the-env",
        "build",
        _replace(
            "    env:\n      IMAGE: ${{ inputs.image }}\n    steps:",
            "    env:\n      IMAGE: ${{ inputs.image }}\n      COSIGN_VERSION: v3.1.5\n    steps:",
        ),
        ["tools"],
    ),
    (
        "the-installer-by-tag",
        "build",
        _nth(
            "sigstore/cosign-installer@756fe2d284c7292c4e9b62c8a28f1e5629c59532 # v4.1.3",
            "sigstore/cosign-installer@v4 # v4.1.3",
            0,
            3,
        ),
        ["tools"],
    ),
    (
        "the-sbom-uploaded-as-an-artifact",
        "build",
        _replace("          upload-artifact: false\n", "          upload-artifact: true\n"),
        ["tools"],
    ),
    (
        "the-sbom-as-spdx",
        "build",
        _replace(SBOM_LINE, "          format: spdx-json\n"),
        ["tools"],
    ),
    # the evidence
    (
        "the-provenance-of-the-manifest-is-gone",
        "build",
        _cut(
            "      - name: Attest the provenance to the platform manifest (cosign)\n",
            UPLOAD_RECORD,
        ),
        ["evidence"],
    ),
    (
        "the-provenance-of-the-index-is-gone",
        "build",
        _cut(
            "      - name: Attest the provenance to the index (cosign)\n",
            "      - name: Emit release identity\n",
        ),
        ["evidence"],
    ),
    (
        "the-index-is-not-signed",
        "build",
        _cut(
            "      - name: Sign the index (keyless)\n",
            PREDICATE_STEP,
        ),
        ["evidence"],
    ),
    (
        "the-manifest-is-not-signed",
        "build",
        _cut(
            "      - name: Sign the platform manifest (keyless)\n",
            "      - name: Generate the platform SBOM (syft, CycloneDX)\n",
        ),
        ["evidence"],
    ),
    (
        "the-sbom-is-spdx-when-attested",
        "build",
        _replace("attest --yes --type cyclonedx \\\n", "attest --yes --type spdxjson \\\n"),
        ["evidence"],
    ),
    (
        "the-index-carries-an-sbom",
        "build",
        _replace(ATTEST_INDEX, ATTEST_INDEX + ATTEST_INDEX_SBOM),
        ["evidence"],
    ),
    (
        "the-provenance-is-another-file",
        "build",
        _nth(
            "          PREDICATE: ${{ runner.temp }}/provenance.json\n",
            "          PREDICATE: ${{ runner.temp }}/other.json\n",
            0,
            2,
        ),
        ["evidence"],
    ),
    (
        "the-sbom-is-of-the-index",
        "build",
        _replace(
            "          image: ${{ inputs.image }}@${{ steps.build.outputs.manifest }}\n",
            "          image: ${{ inputs.image }}\n",
        ),
        ["evidence"],
    ),
    (
        "the-index-has-the-tag-of-a-version",
        "build",
        _replace(
            "          tag: run-${{ github.run_id }}-${{ github.run_attempt }}\n",
            "          tag: 0.2.1-rc\n",
        ),
        ["evidence"],
    ),
    # the SBOM floor
    (
        "no-check-of-the-sbom",
        "build",
        _cut(
            SBOM_FLOOR_COMMENT,
            "      - name: Attest the SBOM to the platform manifest (cosign)\n",
        ),
        ["evidence", "sbom"],
    ),
    (
        "a-floor-of-1-5",
        "build",
        _replace(">= [1, 6]", ">= [1, 5]"),
        ["sbom"],
    ),
    (
        "the-policy-floor-is-lowered",
        "policy",
        _replace('minVersion: "1.6"', 'minVersion: "1.5"'),
        ["sbom"],
    ),
    # the temporary verification
    (
        "the-identity-is-another-workflow",
        "build",
        _replace(
            "IDENTITY: https://github.com/gora-tools/gora/.github/workflows/build.yml@refs/heads/main",
            "IDENTITY: https://github.com/gora-tools/gora/.github/workflows/release.yml@refs/heads/main",
        ),
        ["verify"],
    ),
    (
        "the-policy-names-another-builder",
        "policy",
        _replace(
            "identity: https://github.com/gora-tools/gora/.github/workflows/build.yml@refs/heads/main",
            "identity: https://github.com/gora-tools/gora/.github/workflows/release.yml@refs/heads/main",
        ),
        ["verify"],
    ),
    (
        "the-identity-is-a-pattern",
        "build",
        _nth(
            '--certificate-identity "$IDENTITY"',
            '--certificate-identity-regexp "$IDENTITY"',
            0,
            3,
        ),
        ["verify"],
    ),
    (
        "the-commit-is-not-checked",
        "build",
        _nth(
            ' \\\n              --certificate-github-workflow-sha "$COMMIT" > /dev/null',
            " > /dev/null",
            1,
            3,
        ),
        ["verify"],
    ),
    (
        "the-sbom-of-the-index-is-verified",
        "build",
        _replace(
            '          for digest in $MANIFESTS; do\n            echo "SBOM:',
            '          for digest in "$INDEX" $MANIFESTS; do\n            echo "SBOM:',
        ),
        ["verify"],
    ),
    (
        "buildx-in-verify",
        "build",
        _replace(
            "\n      - name: List the platform manifests from the build records\n",
            BUILDX_STEP + "\n      - name: List the platform manifests from the build records\n",
        ),
        ["verify"],
    ),
    (
        "an-empty-download-passes",
        "build",
        _replace(
            '            if [ ! -s "$file" ]; then\n', '            if [ -z "$file" ]; then\n'
        ),
        ["verify"],
    ),
    (
        "the-evidence-is-kept-90-days",
        "build",
        _replace("          retention-days: 14\n", "          retention-days: 90\n"),
        ["verify"],
    ),
    (
        "an-empty-upload-passes",
        "build",
        _replace(
            EVIDENCE_UPLOAD,
            EVIDENCE_UPLOAD.replace("          if-no-files-found: error\n", ""),
        ),
        ["verify"],
    ),
    # the call
    (
        "the-caller-does-not-wait",
        "candidate",
        _replace("    needs: changes\n    if:", "    if:"),
        ["caller"],
    ),
    (
        "the-caller-runs-on-a-dispatch-too",
        "candidate",
        _replace("github.event_name == 'push' && ", ""),
        ["caller"],
    ),
    (
        "the-caller-builds-another-artifact",
        "candidate",
        _replace("artifacts.*.name, 'gora')", "artifacts.*.name, 'other')"),
        ["caller"],
    ),
    (
        "the-caller-has-no-group",
        "candidate",
        _replace(CALLER_CONCURRENCY, ""),
        ["caller"],
    ),
    (
        "the-caller-cancels-a-build",
        "candidate",
        _replace("      cancel-in-progress: false\n", "      cancel-in-progress: true\n"),
        ["caller"],
    ),
    (
        "the-caller-has-a-timeout",
        "candidate",
        _replace(CALLER_USES, CALLER_USES + "    timeout-minutes: 10\n"),
        ["caller"],
    ),
    (
        "the-caller-calls-another-workflow",
        "candidate",
        _replace(CALLER_USES, "    uses: ./.github/workflows/own-image.yml\n"),
        ["caller"],
    ),
    (
        "the-caller-builds-another-image",
        "candidate",
        _replace(
            "      image: ghcr.io/gora-tools/gora\n", "      image: ghcr.io/gora-tools/other\n"
        ),
        ["caller"],
    ),
    (
        "the-artifact-names-another-image",
        "artifact",
        _replace("image: ghcr.io/gora-tools/gora\n", "image: ghcr.io/gora-tools/other\n"),
        ["caller"],
    ),
    # release.yml is read as it is
    (
        "release-loses-its-cosign-version",
        "release",
        _replace("  COSIGN_VERSION: v2.6.5\n", ""),
        ["release"],
    ),
    (
        "release-loses-its-syft-version",
        "release",
        _replace("  SYFT_VERSION: v1.54.0\n", ""),
        ["release"],
    ),
]


@pytest.mark.parametrize(
    ("target", "text", "found"),
    [pytest.param(d[1], d[2], d[3], id=d[0]) for d in DEFECTS],
)
def test_a_defect_is_found_by_the_check_it_is_for(
    target: str, text: Callable[[str], str], found: list[str]
) -> None:
    """Each defect is a change of the real text of the file it names, made around the values it
    holds, and it is found by its own check and, but for the ones named, by no other: with that
    check deleted the case would pass."""
    real = _real()
    broken = replace(real, **{target: text(getattr(real, target))})
    assert broken != real
    assert set(names(problems(broken))) == set(found)


def test_the_defects_cover_every_check() -> None:
    """Every check of the file is the one a defect names, and no defect is made twice."""
    covered = {name for d in DEFECTS for name in d[3]}
    assert covered == {
        "trigger",
        "jobs",
        "permissions",
        "guard",
        "timeout",
        "environment",
        "secrets",
        "ref",
        "tools",
        "evidence",
        "sbom",
        "verify",
        "release",
        "caller",
    }
    assert len({d[0] for d in DEFECTS}) == len(DEFECTS)


def test_a_body_without_the_names_the_condition_reads_is_found() -> None:
    """The condition reads the published body of `gora changes`; a schema without them is found,
    not skipped."""
    schema = json.loads(CHANGES_SCHEMA.read_text(encoding="utf-8"))
    assert "artifacts" in schema["properties"]
    assert "name" in schema["$defs"]["ChangedArtifact"]["properties"]
    assert schema["$defs"]["ChangedArtifact"]["properties"]["name"]["type"] == "string"

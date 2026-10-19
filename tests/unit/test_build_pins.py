"""Every build input is pinned, and the pins agree (ADR-021, TESTING rules 3 and 8).

Read from the files themselves, offline (TESTING rule 6): the workflows and the actions
they share, the Dockerfiles, pyproject.toml and .python-version, and the output of
scripts/gate/versions.sh, which reads the Dockerfile. Each check is a function of the parsed
text, and each is shown red here on a defect of its class (ADR-0016 rule 1) before it is
trusted on the real files.
"""

import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ACTIONS = sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
DOCKERFILES = sorted((ROOT / "deployments").rglob("*Dockerfile"))
RELEASE_DOCKERFILE = ROOT / "deployments" / "docker" / "Dockerfile"
GATE_IMAGES = ROOT / "deployments" / "docker" / "gate-images.Dockerfile"

RUNNERS = frozenset({"ubuntu-24.04", "ubuntu-24.04-arm"})
SETUP_UV = "astral-sh/setup-uv@"
UV_IMAGE = "ghcr.io/astral-sh/uv:"
SEMVER = re.compile(r"v?\d+\.\d+\.\d+")
# `uses: owner/repo[/path]@<commit> # <version>`; a local action (./...) is this repository.
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>\S+))?\s*$")
PINNED_ACTION = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
FROM = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
VERSIONED_INPUTS = ("version", "cosign-release", "syft-version")
VERSIONS_SH = ROOT / "scripts" / "gate" / "versions.sh"
# A setup-uv input CI fills from the versions.sh step: `${{ steps.<id>.outputs.<key> }}`.
DERIVED = re.compile(r"\$\{\{\s*steps\.(?P<step>[\w-]+)\.outputs\.(?P<key>uv|python)\s*\}\}")
SETUP_UV_INPUTS = (("uv", "version"), ("python", "python-version"))
ENV_REF = re.compile(r"\$\{\{\s*env\.(\w+)\s*\}\}")


def _yaml(path: Path) -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document


def _steps(document: dict[Any, Any]) -> list[dict[str, Any]]:
    """The steps of every job of a workflow, or of a composite action."""
    if "runs" in document:
        return list(document["runs"].get("steps", []))
    return [step for job in document.get("jobs", {}).values() for step in job.get("steps", [])]


# --- actions are pinned by commit ----------------------------------------------------------


def unpinned_uses(text: str) -> list[str]:
    """Each `uses:` that is not a full commit SHA with its version in a trailing comment."""
    bad = []
    for line in text.splitlines():
        match = USES.match(line)
        if match is None or match["ref"].startswith("./"):
            continue
        comment = match["comment"] or ""
        if not PINNED_ACTION.fullmatch(match["ref"]) or not SEMVER.fullmatch(comment):
            bad.append(line.strip())
    return bad


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_action_is_pinned_by_commit(path: Path) -> None:
    assert unpinned_uses(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/checkout@v7",
        "      - uses: actions/checkout@v7.0.1",
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "        uses: docker/login-action@dbcb813 # v4.6.0",
        "      - uses: docker/login-action@main # v4.6.0",
    ],
)
def test_an_action_by_tag_or_without_its_version_is_found(line: str) -> None:
    assert unpinned_uses(line) == [line.strip()]


def test_a_local_action_and_a_pinned_one_pass() -> None:
    text = (
        "      - uses: ./.github/actions/build-image\n"
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    assert unpinned_uses(text) == []


def test_the_actions_are_found_at_all() -> None:
    uses = [
        line
        for path in WORKFLOWS + ACTIONS
        for line in path.read_text(encoding="utf-8").splitlines()
        if USES.match(line)
    ]
    assert len(WORKFLOWS) >= 3
    assert len(ACTIONS) >= 2
    assert len(uses) >= 10


# --- every base image is pinned by digest --------------------------------------------------


def unpinned_froms(text: str) -> list[str]:
    """Each FROM line whose image has no digest; a FROM of an earlier stage is a stage."""
    bad, stages = [], set()
    for line in text.splitlines():
        if not line.startswith("FROM"):
            continue
        match = FROM.match(line)
        if match is None:
            bad.append(line)
            continue
        if match["image"] not in stages and not DIGEST.search(match["image"]):
            bad.append(line)
        if match["stage"]:
            stages.add(match["stage"])
    return bad


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_base_image_is_pinned_by_digest(path: Path) -> None:
    assert unpinned_froms(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "line",
    [
        "FROM python:3.12-slim",
        "FROM python:3.12-slim AS build",
        "FROM python@sha256:abc",
        "FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.12.22 AS uv",
        "FROM build",
    ],
)
def test_a_base_image_without_a_digest_is_found(line: str) -> None:
    assert unpinned_froms(line) == [line]


def test_a_later_stage_may_start_from_an_earlier_one() -> None:
    text = f"FROM python:3.12-slim@sha256:{'a' * 64} AS build\nFROM build AS test\n"
    assert unpinned_froms(text) == []


def test_the_dockerfiles_are_found_at_all() -> None:
    assert RELEASE_DOCKERFILE in DOCKERFILES
    assert GATE_IMAGES in DOCKERFILES


# --- runners are named, never ubuntu-latest ------------------------------------------------


def runners(document: dict[Any, Any]) -> list[str]:
    """Every runner a workflow's jobs run on, a matrix value counted for each entry."""
    found = []
    for job in document.get("jobs", {}).values():
        runs_on = job.get("runs-on")
        if runs_on == "${{ matrix.runner }}":
            matrix = job.get("strategy", {}).get("matrix", {})
            values = [entry.get("runner") for entry in matrix.get("include", [])]
            values += list(matrix.get("runner", []))
            found += values or ["(matrix.runner with no value)"]
        else:
            found.append(runs_on)
    return [str(r) for r in found]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_runner_is_named(path: Path) -> None:
    found = runners(_yaml(path))
    assert found
    assert [r for r in found if r not in RUNNERS] == []


@pytest.mark.parametrize(
    "jobs",
    [
        {"a": {"runs-on": "ubuntu-latest"}},
        {"a": {"runs-on": "ubuntu-24.04"}, "b": {"runs-on": "ubuntu-latest"}},
        {
            "a": {
                "runs-on": "${{ matrix.runner }}",
                "strategy": {"matrix": {"include": [{"runner": "ubuntu-latest"}]}},
            }
        },
        {"a": {"runs-on": "${{ matrix.runner }}", "strategy": {"matrix": {}}}},
        {"a": {"runs-on": "${{ matrix.os }}"}},
    ],
)
def test_ubuntu_latest_or_an_unknown_runner_is_found(jobs: dict[str, Any]) -> None:
    assert [r for r in runners({"jobs": jobs}) if r not in RUNNERS] != []


def test_each_platform_has_its_native_runner() -> None:
    """test and image run on both platforms, natively (ADR-021 section 4)."""
    jobs = _yaml(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
    for name in ("test", "image"):
        include = jobs[name]["strategy"]["matrix"]["include"]
        pairs = {(entry["runner"], entry["platform"]) for entry in include}
        assert pairs == {("ubuntu-24.04", "linux/amd64"), ("ubuntu-24.04-arm", "linux/arm64")}
    assert jobs["check"]["runs-on"] == "ubuntu-24.04"


# --- exact where a result is produced: one uv, one Python ----------------------------------


def uv_from_image(text: str, stage: str) -> str:
    """The uv version in the tag of the uv image a stage starts from."""
    for line in text.splitlines():
        match = FROM.match(line)
        if match and match["stage"] == stage and match["image"].startswith(UV_IMAGE):
            tag = match["image"][len(UV_IMAGE) :].split("@")[0]
            return tag.split("-")[0]
    return f"(no {UV_IMAGE} stage {stage})"


def python_declared(dockerfile: str) -> list[str]:
    """Every value the Dockerfile gives PYTHON_VERSION."""
    prefix = "ARG PYTHON_VERSION="
    return [line[len(prefix) :] for line in dockerfile.splitlines() if line.startswith(prefix)]


def run_versions(dockerfile: Path | None = None) -> tuple[int, str]:
    """scripts/gate/versions.sh, as CI runs it: its exit code and its standard output."""
    command = ["bash", str(VERSIONS_SH), *([str(dockerfile)] if dockerfile else [])]
    done = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    return done.returncode, done.stdout


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def accepts(required: str, version: str) -> bool:
    """Whether [tool.uv] required-version, an exact pin or a floor, admits ``version``."""
    match = re.fullmatch(r"(==|>=)(\d+\.\d+\.\d+)", required)
    if match is None:
        return False
    if match[1] == "==":
        return match[2] == version
    return _version(match[2]) <= _version(version)


def _required(pyproject: str) -> str:
    return str(tomllib.loads(pyproject).get("tool", {}).get("uv", {}).get("required-version"))


def _setup_uv_jobs(document: dict[Any, Any]) -> dict[str, list[dict[str, Any]]]:
    if "runs" in document:
        return {"(composite)": list(document["runs"].get("steps", []))}
    return {name: list(job.get("steps", [])) for name, job in document.get("jobs", {}).items()}


def not_derived(documents: list[dict[Any, Any]]) -> list[str]:
    """Each setup-uv step whose uv or Python is not the output of a versions.sh step that
    runs before it in the same job: a version typed into CI is a second declaration."""
    bad = []
    for document in documents:
        for job, steps in _setup_uv_jobs(document).items():
            sources: set[str] = set()
            for step in steps:
                run = str(step.get("run", ""))
                if "scripts/gate/versions.sh" in run and "GITHUB_OUTPUT" in run and "id" in step:
                    sources.add(str(step["id"]))
                if not str(step.get("uses", "")).startswith(SETUP_UV):
                    continue
                given = step.get("with") or {}
                if "version-file" in given:
                    bad.append(f"{job}: setup-uv version-file={given['version-file']}")
                for key, name in SETUP_UV_INPUTS:
                    match = DERIVED.fullmatch(str(given.get(name, "")))
                    if match is None or match["key"] != key or match["step"] not in sources:
                        bad.append(f"{job}: setup-uv {name}={given.get(name)!r}")
    return bad


def _setup_uv_count(documents: list[dict[Any, Any]]) -> int:
    return sum(
        str(step.get("uses", "")).startswith(SETUP_UV)
        for document in documents
        for steps in _setup_uv_jobs(document).values()
        for step in steps
    )


def test_versions_sh_reads_the_dockerfile() -> None:
    """The script prints exactly the two declarations, parsed here independently."""
    dockerfile = RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    (python,) = python_declared(dockerfile)
    code, out = run_versions()
    assert code == 0
    assert out == f"python={python}\nuv={uv_from_image(dockerfile, 'uv')}\n"


UV_STAGE = f"FROM {UV_IMAGE}0.12.22@sha256:{'a' * 64} AS uv\n"


@pytest.mark.parametrize(
    "dockerfile",
    [
        "ARG PYTHON_VERSION=3.12.15\n",
        UV_STAGE,
        UV_STAGE + "ARG PYTHON_VERSION=3.12\n",
        UV_STAGE + "ARG PYTHON_VERSION=3.12.15\nARG PYTHON_VERSION=3.12.14\n",
        UV_STAGE.replace(f"@sha256:{'a' * 64}", "") + "ARG PYTHON_VERSION=3.12.15\n",
        UV_STAGE.replace("0.12.22", "latest") + "ARG PYTHON_VERSION=3.12.15\n",
        UV_STAGE + UV_STAGE + "ARG PYTHON_VERSION=3.12.15\n",
    ],
    ids=[
        "no-uv",
        "no-python",
        "python-minor",
        "python-twice",
        "uv-no-digest",
        "uv-latest",
        "uv-twice",
    ],
)
def test_versions_sh_refuses_anything_but_one_exact_pair(dockerfile: str, tmp_path: Path) -> None:
    path = tmp_path / "Dockerfile"
    path.write_text(dockerfile, encoding="utf-8")
    assert run_versions(path) == (2, "")


def test_ci_installs_what_versions_sh_prints() -> None:
    """Every setup-uv takes uv and Python from the versions.sh step before it."""
    documents = [_yaml(path) for path in WORKFLOWS + ACTIONS]
    assert _setup_uv_count(documents) >= 2  # check and test
    assert not_derived(documents) == []


_SOURCE = {"id": "versions", "run": 'scripts/gate/versions.sh | tee -a "$GITHUB_OUTPUT"'}
_DERIVED_WITH = {
    "version": "${{ steps.versions.outputs.uv }}",
    "python-version": "${{ steps.versions.outputs.python }}",
}


@pytest.mark.parametrize(
    "steps",
    [
        [_SOURCE, {"uses": f"{SETUP_UV}x", "with": {**_DERIVED_WITH, "version": "0.12.22"}}],
        [_SOURCE, {"uses": f"{SETUP_UV}x", "with": {"version": _DERIVED_WITH["version"]}}],
        [_SOURCE, {"uses": f"{SETUP_UV}x", "with": {"version-file": "pyproject.toml"}}],
        [{"uses": f"{SETUP_UV}x", "with": _DERIVED_WITH}, _SOURCE],
        [
            {"id": "versions", "run": "echo uv=0.12.22"},
            {"uses": f"{SETUP_UV}x", "with": _DERIVED_WITH},
        ],
        [
            _SOURCE,
            {
                "uses": f"{SETUP_UV}x",
                "with": {**_DERIVED_WITH, "version": "${{ steps.versions.outputs.python }}"},
            },
        ],
    ],
    ids=["typed", "no-python", "version-file", "read-after", "other-step", "wrong-output"],
)
def test_a_version_ci_does_not_derive_is_found(steps: list[dict[str, Any]]) -> None:
    assert not_derived([{"jobs": {"a": {"steps": steps}}}]) != []


def test_the_derived_pair_passes() -> None:
    steps = [_SOURCE, {"uses": f"{SETUP_UV}x", "with": _DERIVED_WITH}]
    assert not_derived([{"jobs": {"a": {"steps": steps}}}]) == []


def _uv_inputs() -> tuple[str, str, str]:
    return (
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        RELEASE_DOCKERFILE.read_text(encoding="utf-8"),
        GATE_IMAGES.read_text(encoding="utf-8"),
    )


def uv_disagreements(pyproject: str, dockerfile: str, gate_images: str) -> list[str]:
    """The uv stage is the one exact uv; the Linux gate image carries it, and
    required-version admits it."""
    pinned = uv_from_image(dockerfile, "uv")
    bad = []
    if not re.fullmatch(r"\d+\.\d+\.\d+", pinned):
        bad.append(f"Dockerfile uv stage: {pinned}")
    if uv_from_image(gate_images, "linux-gate") != pinned:
        bad.append(f"linux-gate uv {uv_from_image(gate_images, 'linux-gate')} is not {pinned}")
    if not accepts(_required(pyproject), pinned):
        bad.append(f"required-version {_required(pyproject)} does not admit {pinned}")
    return bad


def test_one_uv_version_where_a_result_is_produced() -> None:
    """The release image's uv stage, the Linux gate image and CI (through versions.sh) use
    one uv, and pyproject.toml's required-version admits it."""
    assert uv_disagreements(*_uv_inputs()) == []


@pytest.mark.parametrize("where", ["dockerfile", "gate-images", "pyproject-above"])
def test_a_uv_version_that_differs_is_found(where: str) -> None:
    pyproject, dockerfile, gate_images = _uv_inputs()
    pinned = uv_from_image(dockerfile, "uv")
    if where == "dockerfile":
        dockerfile = dockerfile.replace(f"{UV_IMAGE}{pinned}@", f"{UV_IMAGE}0.0.1@")
    elif where == "gate-images":
        gate_images = gate_images.replace(f"{UV_IMAGE}{pinned}-", f"{UV_IMAGE}0.0.1-")
    else:
        pyproject = pyproject.replace(_required(pyproject), ">=99.0.0")
    assert uv_disagreements(pyproject, dockerfile, gate_images) != []


def test_the_exact_python_is_declared_once_next_to_its_base() -> None:
    """PYTHON_VERSION, in the build stage under the python base it describes, is the exact
    patch; the build compares the base's interpreter with it, and the base images, the Linux
    gate image and .python-version name its minor version."""
    dockerfile = RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    (declared,) = python_declared(dockerfile)
    assert re.fullmatch(r"3\.\d+\.\d+", declared)
    minor = declared.rsplit(".", 1)[0]
    lines = dockerfile.splitlines()
    bases = [(n, m["image"]) for n, line in enumerate(lines) if (m := FROM.match(line))]
    python = [(n, image) for n, image in bases if image.startswith("python:")]
    assert len(python) == 2
    assert len({image for _, image in python}) == 1  # build and runtime stages: one digest
    assert python[0][1].startswith(f"python:{minor}-slim@sha256:")
    build_stage = lines[python[0][0] : python[1][0]]
    assert f"ARG PYTHON_VERSION={declared}" in build_stage
    code = "\n".join(line for line in build_stage if not line.lstrip().startswith("#"))
    assert '"$PYTHON_VERSION"' in code  # the build compares the base's interpreter with it
    assert "UV_PYTHON_PREFERENCE=only-system" in dockerfile
    assert f"-python{minor}-" in GATE_IMAGES.read_text(encoding="utf-8")
    # Where a developer works, the minor version only: any patch of it runs locally.
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == minor


def uv_floor(pyproject: str) -> str | None:
    """The X of required-version ">=X", or None when it is not a floor."""
    match = re.fullmatch(r">=(\d+\.\d+\.\d+)", _required(pyproject))
    return match[1] if match else None


def floor_problems(pyproject: str, dockerfile: str) -> list[str]:
    floor, pinned = uv_floor(pyproject), uv_from_image(dockerfile, "uv")
    if floor is None:
        return [f"required-version {_required(pyproject)} is not a floor >=X.Y.Z"]
    if _version(floor) > _version(pinned):
        return [f"the floor {floor} is above the pinned uv {pinned}"]
    return []


def test_uv_is_a_floor_where_a_developer_works() -> None:
    """pyproject.toml asks for a lowest uv, not the pinned one, and never more than it."""
    pyproject, dockerfile, _ = _uv_inputs()
    assert floor_problems(pyproject, dockerfile) == []


@pytest.mark.parametrize("required", ["==0.12.22", ">=99.0.0", "~=0.12", "", ">=0.12"])
def test_a_pin_or_a_floor_above_the_pin_is_found(required: str) -> None:
    pyproject, dockerfile, _ = _uv_inputs()
    pyproject = pyproject.replace(f'"{_required(pyproject)}"', f'"{required}"')
    assert floor_problems(pyproject, dockerfile) != []


# --- the build backend: one exact version, and the image builds with it -------------------

BUILD_GROUP = "build"
NO_ISOLATION = "--no-build-isolation-package gora"


def run_instructions(dockerfile: str) -> list[str]:
    """Each RUN of a Dockerfile as one line: continuations joined, comment lines dropped."""
    runs: list[str] = []
    parts: list[str] | None = None
    for line in dockerfile.splitlines():
        if line.lstrip().startswith("#") or (parts is None and not line.startswith("RUN ")):
            continue
        text = line.rstrip()
        parts = [*(parts or []), text.removesuffix("\\")]
        if not text.endswith("\\"):
            runs.append(" ".join(" ".join(parts).split()))
            parts = None
    return runs


def backend_problems(pyproject: str, dockerfile: str) -> list[str]:
    """The group `build` pins the backend `[build-system]` names to one exact version, which
    the floor in `requires` admits; the image installs the project with that group and
    without build isolation, and its last sync drops the group."""
    document = tomllib.loads(pyproject)
    system = document.get("build-system", {})
    name = str(system.get("build-backend", "")).split(".")[0]
    requires = [str(r) for r in system.get("requires", [])]
    group = document.get("dependency-groups", {}).get(BUILD_GROUP)
    bad = []
    pinned = None
    if not (isinstance(group, list) and len(group) == 1 and isinstance(group[0], str)):
        bad.append(f"the group {BUILD_GROUP} is not one requirement: {group}")
    elif (exact := re.fullmatch(rf"{re.escape(name)}==(\d+\.\d+\.\d+)", group[0])) is None:
        bad.append(f"the group {BUILD_GROUP} is not {name}==X.Y.Z: {group[0]}")
    else:
        pinned = exact[1]
    floor = re.fullmatch(rf"{re.escape(name)}>=(\d+\.\d+\.\d+)", requires[0]) if requires else None
    if len(requires) != 1 or floor is None:
        bad.append(f"[build-system] requires is not one floor {name}>=X.Y.Z: {requires}")
    elif pinned is not None and _version(floor[1]) > _version(pinned):
        bad.append(f"the floor {floor[1]} is above the pinned {name} {pinned}")
    if (count := pyproject.count(f'"{name}==')) != 1:
        bad.append(f"{name} is pinned {count} times in pyproject.toml")
    syncs = [run for run in run_instructions(dockerfile) if run.startswith("RUN uv sync ")]
    project = [run for run in syncs if "--no-install-project" not in run.split()]
    if not project or not (NO_ISOLATION in project[0] and f"--group {BUILD_GROUP}" in project[0]):
        bad.append(f"the sync that installs the project lacks --group {BUILD_GROUP} {NO_ISOLATION}")
    if not syncs or "--group" in syncs[-1].split():
        bad.append("the last sync keeps a dependency group in the image")
    return bad


def _backend_inputs() -> tuple[str, str]:
    return (
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        RELEASE_DOCKERFILE.read_text(encoding="utf-8"),
    )


def test_the_build_backend_is_one_exact_version_the_image_builds_with() -> None:
    """hatchling is pinned once, in the group `build`, locked with hashes in uv.lock; the
    release image builds the project with it, not with what an isolated build would resolve
    from the index, then removes it. make image-smoke reads the wheel's Generator."""
    assert backend_problems(*_backend_inputs()) == []


@pytest.mark.parametrize(
    "defect",
    [
        "no-group",
        "group-range",
        "group-two",
        "requires-pin",
        "floor-above-pin",
        "pinned-twice",
        "isolated",
        "no-group-on-build",
        "group-left-in-image",
    ],
)
def test_a_backend_not_locked_where_the_image_is_built_is_found(defect: str) -> None:
    """Each defect is made on the real files, around the versions they hold today, so a
    Dependabot pull request that moves the pin and the floor changes no defect."""
    pyproject, dockerfile = _backend_inputs()
    (pinned,) = re.findall(r'"hatchling==(\d+\.\d+\.\d+)",', pyproject)
    (floor,) = re.findall(r'^requires = \["hatchling>=(\d+\.\d+\.\d+)"\]$', pyproject, re.M)
    pin, requires = f'"hatchling=={pinned}",', f'requires = ["hatchling>={floor}"]'
    major, minor, _ = _version(pinned)
    assert pyproject.count(pin) == pyproject.count(requires) == dockerfile.count(NO_ISOLATION) == 1
    if defect == "no-group":
        pyproject = pyproject.replace(f"{BUILD_GROUP} = [\n    {pin}\n]\n", "")
    elif defect == "group-range":
        pyproject = pyproject.replace(pin, f'"hatchling>={pinned}",')
    elif defect == "group-two":
        pyproject = pyproject.replace(pin, f'{pin} "editables",')
    elif defect == "requires-pin":
        pyproject = pyproject.replace(requires, 'requires = ["hatchling"]')
    elif defect == "floor-above-pin":
        pyproject = pyproject.replace(requires, f'requires = ["hatchling>={major}.{minor + 1}.0"]')
    elif defect == "pinned-twice":
        pyproject = pyproject.replace("test = [\n", f"test = [\n    {pin}\n", 1)
    elif defect == "isolated":
        dockerfile = dockerfile.replace(NO_ISOLATION, "")
    elif defect == "no-group-on-build":
        dockerfile = dockerfile.replace(f"--group {BUILD_GROUP} --no-editable", "--no-editable")
    else:
        dockerfile = dockerfile.replace(
            "RUN uv sync --locked --no-default-groups --no-editable\n",
            f"RUN uv sync --locked --no-default-groups --group {BUILD_GROUP} --no-editable\n",
        )
    assert backend_problems(pyproject, dockerfile) != []


# --- tools the actions install are pinned --------------------------------------------------


def unpinned_tools(document: dict[Any, Any]) -> list[str]:
    """Each version-like action input or *_VERSION value that is not one exact version."""
    env = {str(k): str(v) for k, v in (document.get("env") or {}).items()}
    bad = [
        f"env {k}={v}" for k, v in env.items() if k.endswith("_VERSION") and not SEMVER.fullmatch(v)
    ]
    for step in _steps(document):
        if str(step.get("uses", "")).startswith(SETUP_UV):
            continue  # derived from versions.sh: test_ci_installs_what_versions_sh_prints
        for key, value in (step.get("with") or {}).items():
            if key not in VERSIONED_INPUTS:
                continue
            resolved = ENV_REF.sub(lambda m: env.get(m[1], ""), str(value))
            if not SEMVER.fullmatch(resolved):
                bad.append(f"{step.get('uses')} {key}={value}")
    return bad


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_tool_version_is_exact(path: Path) -> None:
    assert unpinned_tools(_yaml(path)) == []


@pytest.mark.parametrize(
    "document",
    [
        {"runs": {"steps": [{"uses": "x", "with": {"version": "latest"}}]}},
        {"runs": {"steps": [{"uses": "x", "with": {"cosign-release": "v2"}}]}},
        {"env": {"SYFT_VERSION": "latest"}, "jobs": {}},
        {
            "env": {"COSIGN_VERSION": "main"},
            "jobs": {"a": {"steps": [{"with": {"cosign-release": "${{ env.COSIGN_VERSION }}"}}]}},
        },
    ],
)
def test_a_tool_at_latest_or_a_range_is_found(document: dict[Any, Any]) -> None:
    assert unpinned_tools(document) != []


def test_crane_is_pinned_by_version_and_checksum() -> None:
    script = (ROOT / "scripts" / "gate" / "crane.sh").read_text(encoding="utf-8")
    assert re.search(r"^VERSION=v\d+\.\d+\.\d+$", script, re.MULTILINE)
    assert len(re.findall(r"sum=[0-9a-f]{64} ;;", script)) >= 2  # both runner platforms


# --- the update path -----------------------------------------------------------------------


def test_dependabot_proposes_every_pin() -> None:
    """github-actions (workflows and the shared actions), docker (both Dockerfiles), uv.

    uv proposes every package in one pull request and raises each floor in pyproject.toml
    with it: a floor is the lowest version proven, the one uv.lock carries and CI runs
    (TESTING rule 3).
    """
    config = _yaml(ROOT / ".github" / "dependabot.yml")
    updates = {u["package-ecosystem"]: u for u in config["updates"]}
    assert set(updates) == {"github-actions", "docker", "uv"}
    assert {"/", "/.github/actions/*"} <= set(updates["github-actions"]["directories"])
    assert updates["docker"]["directory"] == "/deployments/docker"
    assert updates["uv"]["directory"] == "/"
    assert {u["schedule"]["interval"] for u in updates.values()} == {"weekly"}
    uv_groups = updates["uv"].get("groups", {}).values()
    assert any("*" in group.get("patterns", []) for group in uv_groups)
    assert updates["uv"].get("versioning-strategy") == "increase"

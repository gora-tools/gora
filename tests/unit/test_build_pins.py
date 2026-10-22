"""Every build input is pinned, and the pins agree (ADR-021, TESTING rules 3 and 8).

Read from the files themselves, offline (TESTING rule 6): the workflows and the actions
they share, the Dockerfiles, pyproject.toml and .python-version, and the output of
scripts/gate/versions.sh, which reads the Dockerfile. Each check is a function of the parsed
text, and each is shown red here on a defect of its class (ADR-0016 rule 1) before it is
trusted on the real files.
"""

import re
import shlex
import subprocess
import tomllib
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ACTIONS = sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
DOCKERFILES = sorted((ROOT / "deployments").rglob("*Dockerfile"))
RELEASE_DOCKERFILE = ROOT / "deployments" / "docker" / "Dockerfile"
MAKEFILE = ROOT / "Makefile"
RED_FIRST = ROOT / "scripts" / "gate" / "red-first.sh"
GATE_IMAGES = ROOT / "deployments" / "docker" / "gate-images.Dockerfile"
DEBIAN_SOURCES = ROOT / "deployments" / "docker" / "debian.sources"

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
        "FROM python:3.12.15-slim-trixie",
        "FROM python:3.12.15-slim-trixie AS build",
        "FROM python@sha256:abc",
        "FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.12.22 AS uv",
        "FROM build",
    ],
)
def test_a_base_image_without_a_digest_is_found(line: str) -> None:
    assert unpinned_froms(line) == [line]


def test_a_later_stage_may_start_from_an_earlier_one() -> None:
    text = f"FROM python:3.12.15-slim-trixie@sha256:{'a' * 64} AS build\nFROM build AS test\n"
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


def base_problems(dockerfile: str, sources: str) -> list[str]:
    """Both python bases are one image, ``python:<minor>-slim-<codename>@<digest>``: the minor
    version of PYTHON_VERSION, and the first suite of the ``debian`` stanza of debian.sources."""
    declared = python_declared(dockerfile)
    suites = archive_stanzas(sources).get("debian", {}).get("Suites", "").split(" ")
    if len(declared) != 1 or not suites[0]:
        return [f"PYTHON_VERSION declared {len(declared)} times; the debian stanza has {suites}"]
    minor = declared[0].rsplit(".", 1)[0]
    images = [m["image"] for line in dockerfile.splitlines() if (m := FROM.match(line))]
    python = [image for image in images if image.startswith("python:")]
    bad = []
    if len(python) != 2 or len(set(python)) != 1:
        bad.append(f"the python bases are not one image: {python}")
    want = rf"python:{re.escape(minor)}-slim-{re.escape(suites[0])}@sha256:[0-9a-f]{{64}}"
    bad += [f"{image} is not {want}" for image in python if re.fullmatch(want, image) is None]
    return bad


def test_the_base_names_the_python_minor_and_the_debian_release() -> None:
    """PYTHON_VERSION, in the build stage under the python base it describes, is the exact
    patch; the build compares the base's interpreter with it. Both bases are one image, by
    digest, whose tag names the minor version and the Debian release debian.sources names, so a
    digest bump cannot move the image to the next release; the Linux gate image and
    .python-version name the minor version."""
    dockerfile = RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    assert base_problems(dockerfile, DEBIAN_SOURCES.read_text(encoding="utf-8")) == []
    (declared,) = python_declared(dockerfile)
    assert re.fullmatch(r"3\.\d+\.\d+", declared)
    minor = declared.rsplit(".", 1)[0]
    lines = dockerfile.splitlines()
    bases = [(n, m["image"]) for n, line in enumerate(lines) if (m := FROM.match(line))]
    python = [(n, image) for n, image in bases if image.startswith("python:")]
    build_stage = lines[python[0][0] : python[1][0]]
    assert f"ARG PYTHON_VERSION={declared}" in build_stage
    code = "\n".join(line for line in build_stage if not line.lstrip().startswith("#"))
    assert '"$PYTHON_VERSION"' in code  # the build compares the base's interpreter with it
    assert "UV_PYTHON_PREFERENCE=only-system" in dockerfile
    assert f"-python{minor}-" in GATE_IMAGES.read_text(encoding="utf-8")
    # Where a developer works, the minor version only: any patch of it runs locally.
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == minor


@pytest.mark.parametrize(
    "defect", ["tag-without-codename", "tag-with-patch", "codename-other-than-sources"]
)
def test_a_base_whose_tag_misses_the_python_or_the_release_is_found(defect: str) -> None:
    """Each defect is made on the real files, around the values they hold."""
    real = (
        RELEASE_DOCKERFILE.read_text(encoding="utf-8"),
        DEBIAN_SOURCES.read_text(encoding="utf-8"),
    )
    dockerfile, sources = real
    (declared,) = python_declared(dockerfile)
    minor = declared.rsplit(".", 1)[0]
    codename = archive_stanzas(sources)["debian"]["Suites"].split(" ")[0]
    if defect == "tag-without-codename":
        dockerfile = dockerfile.replace(f"-slim-{codename}@", "-slim@")
    elif defect == "tag-with-patch":
        dockerfile = dockerfile.replace(f"python:{minor}-slim-", f"python:{declared}-slim-")
    else:
        sources = sources.replace(codename, "forky" if codename != "forky" else "sid")
    assert (dockerfile, sources) != real
    assert base_problems(dockerfile, sources) != []


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


SHELL_OPERATORS = frozenset({"&&", "||", ";", "|", "&"})


def uv_syncs(text: str) -> list[list[str]]:
    """The arguments of each `uv sync` command of a Makefile, a shell script or a Dockerfile:
    comment lines dropped, continuations joined, a command ended by the next shell operator.
    A line that only mentions `uv sync`, in a string, is no command."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    syncs = []
    for line in "\n".join(lines).replace("\\\n", " ").splitlines():
        if "uv sync" not in line:
            continue
        words = shlex.split(line)
        for at, pair in enumerate(pairwise(words)):
            if pair == ("uv", "sync"):
                rest = words[at + 2 :]
                end = next((n for n, word in enumerate(rest) if word in SHELL_OPERATORS), len(rest))
                syncs.append(rest[:end])
    return syncs


def has_option(arguments: list[str], option: str, value: str) -> bool:
    """Whether ``--option value`` or ``--option=value`` is among the arguments."""
    return f"{option}={value}" in arguments or (option, value) in pairwise(arguments)


def backend_problems(pyproject: str, dockerfile: str, makefile: str, red_first: str) -> list[str]:
    """The group `build` pins the backend `[build-system]` names to one exact version, which
    the floor in `requires` admits; every sync that installs the project, in the Makefile, in
    red-first.sh and in the image, installs that group and builds without build isolation, so
    no gate resolves the backend from the index; the image's last sync drops the group."""
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
    for name, text in (("the Makefile", makefile), ("red-first.sh", red_first)):
        arguments = [sync for sync in uv_syncs(text) if "--no-install-project" not in sync]
        if not arguments:
            bad.append(f"{name} runs no uv sync that installs the project")
        bad += [
            f"{name}: uv sync {' '.join(sync)} lacks --group {BUILD_GROUP} {NO_ISOLATION}"
            for sync in arguments
            if not (
                has_option(sync, "--group", BUILD_GROUP)
                and has_option(sync, "--no-build-isolation-package", "gora")
            )
        ]
    return bad


def _backend_inputs() -> tuple[str, str, str, str]:
    return (
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        RELEASE_DOCKERFILE.read_text(encoding="utf-8"),
        MAKEFILE.read_text(encoding="utf-8"),
        RED_FIRST.read_text(encoding="utf-8"),
    )


def test_the_build_backend_is_one_exact_version_the_image_builds_with() -> None:
    """hatchling is pinned once, in the group `build`, locked with hashes in uv.lock; the
    release image, make sync, make sync-test and red-first.sh build the project with it, not
    with what an isolated build would resolve from the index; the image then removes it. make
    image-smoke reads the wheel's Generator."""
    assert backend_problems(*_backend_inputs()) == []


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("\tuv sync --locked --group check\n", [["--locked", "--group", "check"]]),
        ('  run_to "$out" uv sync --locked || rc=$?\n', [["--locked"]]),
        ("RUN uv sync --locked \\\n        --no-editable\n", [["--locked", "--no-editable"]]),
        ("uv sync --locked && uv run --no-sync pytest\n", [["--locked"]]),
        ("# uv sync --locked, in a comment\n", []),
        ('echo "red-first: ERROR (uv sync exit $rc)"\n', []),
        ("uv pip sync requirements.txt\n", []),
    ],
    ids=["recipe", "function", "continued", "and-then", "comment", "string", "pip"],
)
def test_a_uv_sync_is_found_where_it_runs_and_nowhere_else(
    text: str, found: list[list[str]]
) -> None:
    assert uv_syncs(text) == found


@pytest.mark.parametrize(
    ("arguments", "found"),
    [
        (["--group", "build"], True),
        (["--group=build"], True),
        (["--group", "test"], False),
        (["--group", "test", "build"], False),
        (["--groups=build"], False),
    ],
)
def test_an_option_is_read_in_both_spellings(arguments: list[str], found: bool) -> None:
    assert has_option(arguments, "--group", "build") is found


def test_the_syncs_are_found_at_all() -> None:
    """The check above is only as good as its reading: the syncs it must read are there."""
    _, dockerfile, makefile, red_first = _backend_inputs()
    assert len(uv_syncs(makefile)) >= 2  # sync and sync-test
    assert len(uv_syncs(red_first)) >= 1
    assert len(uv_syncs(dockerfile)) >= 3


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
        "make-sync-isolated",
        "make-sync-test-no-group",
        "make-sync-gone",
        "red-first-isolated",
        "red-first-no-group",
        "red-first-sync-gone",
    ],
)
def test_a_backend_not_locked_where_the_image_is_built_is_found(defect: str) -> None:
    """Each defect is made on the real files, around the versions they hold today, so a
    Dependabot pull request that moves the pin and the floor changes no defect."""
    pyproject, dockerfile, makefile, red_first = _backend_inputs()
    (pinned,) = re.findall(r'"hatchling==(\d+\.\d+\.\d+)",', pyproject)
    (floor,) = re.findall(r'^requires = \["hatchling>=(\d+\.\d+\.\d+)"\]$', pyproject, re.M)
    pin, requires = f'"hatchling=={pinned}",', f'requires = ["hatchling>={floor}"]'
    major, minor, _ = _version(pinned)
    assert pyproject.count(pin) == pyproject.count(requires) == dockerfile.count(NO_ISOLATION) == 1
    assert makefile.count(NO_ISOLATION) == 2
    assert red_first.count(NO_ISOLATION) == 1
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
    elif defect == "group-left-in-image":
        dockerfile = dockerfile.replace(
            "RUN uv sync --locked --no-default-groups --no-editable\n",
            f"RUN uv sync --locked --no-default-groups --group {BUILD_GROUP} --no-editable\n",
        )
    elif defect == "make-sync-isolated":
        makefile = makefile.replace(f" {NO_ISOLATION}", "", 1)
    elif defect == "make-sync-test-no-group":
        makefile = makefile.replace(f"--group test --group {BUILD_GROUP}", "--group test")
    elif defect == "make-sync-gone":
        makefile = makefile.replace("uv sync", "uv pip sync")
    elif defect == "red-first-isolated":
        red_first = red_first.replace(f" \\\n    {NO_ISOLATION}", "")
    elif defect == "red-first-no-group":
        red_first = red_first.replace(f" --group {BUILD_GROUP}", "")
    else:
        red_first = red_first.replace("uv sync", "uv pip sync")
    assert backend_problems(pyproject, dockerfile, makefile, red_first) != []


# --- image-smoke looks for every tool of the dependency groups ----------------------------

REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _distribution(name: str) -> str:
    """A name as PEP 503 compares it, so ``types-PyYAML`` and ``types_pyyaml`` are one."""
    return re.sub(r"[-_.]+", "-", name).lower()


def group_distributions(pyproject: str) -> set[str]:
    """The distribution of every requirement in the dependency groups; an ``include-group``
    entry names a group, not a distribution."""
    groups = tomllib.loads(pyproject).get("dependency-groups", {})
    names = (
        REQUIREMENT_NAME.match(requirement)
        for requirements in groups.values()
        for requirement in requirements
        if isinstance(requirement, str)
    )
    return {_distribution(match[0]) for match in names if match}


def group_tools_problems(pyproject: str, makefile: str) -> list[str]:
    """The Makefile's GROUP_TOOLS, which image-smoke searches the release image for, is the
    distributions of the dependency groups: none missing, none other, none twice."""
    lists = re.findall(r"^GROUP_TOOLS :=(.*)$", makefile, re.MULTILINE)
    if len(lists) != 1:
        return [f"the Makefile sets GROUP_TOOLS {len(lists)} times, not once"]
    tools = [_distribution(name) for name in lists[0].split()]
    groups = group_distributions(pyproject)
    return [
        *[
            f"{name} is in GROUP_TOOLS twice"
            for name in sorted({n for n in tools if tools.count(n) > 1})
        ],
        *[
            f"{name} is in a dependency group and not in GROUP_TOOLS"
            for name in sorted(groups - set(tools))
        ],
        *[
            f"{name} is in GROUP_TOOLS and in no dependency group"
            for name in sorted(set(tools) - groups)
        ],
    ]


def _group_tools_inputs() -> tuple[str, str]:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8"), MAKEFILE.read_text(
        encoding="utf-8"
    )


def test_image_smoke_looks_for_every_tool_of_the_dependency_groups() -> None:
    """A tool added to a group is a tool the release image must not carry; image-smoke only
    searches for the names GROUP_TOOLS lists."""
    pyproject, makefile = _group_tools_inputs()
    assert group_tools_problems(pyproject, makefile) == []


def test_the_dependency_groups_are_found_at_all() -> None:
    pyproject, _ = _group_tools_inputs()
    assert {"pytest", "pytest-cov", "ruff", "mypy", "types-pyyaml", "hatchling"} <= (
        group_distributions(pyproject)
    )


def test_a_name_in_another_spelling_is_the_same_distribution() -> None:
    pyproject, makefile = _group_tools_inputs()
    assert "types-PyYAML" in makefile
    assert group_tools_problems(pyproject, makefile.replace("types-PyYAML", "types_pyyaml")) == []


@pytest.mark.parametrize(
    "defect", ["tool-missing", "tool-extra", "tool-twice", "group-grows", "list-gone", "list-twice"]
)
def test_a_tools_list_that_differs_from_the_groups_is_found(defect: str) -> None:
    """Each defect is made on the real files, around the names they hold."""
    pyproject, makefile = _group_tools_inputs()
    if defect == "tool-missing":
        broken = makefile.replace(" pytest-cov", "", 1)
    elif defect == "tool-extra":
        broken = makefile.replace(" hatchling", " hatchling bandit", 1)
    elif defect == "tool-twice":
        broken = makefile.replace(" hatchling", " hatchling ruff", 1)
    elif defect == "list-gone":
        broken = makefile.replace("GROUP_TOOLS :=", "OTHER_TOOLS :=", 1)
    elif defect == "list-twice":
        broken = makefile + "GROUP_TOOLS := ruff\n"
    else:
        broken = makefile
        pyproject = pyproject.replace('    "mypy>=', '    "bandit>=1.0",\n    "mypy>=', 1)
    assert (broken, pyproject) != _group_tools_inputs()
    assert group_tools_problems(pyproject, broken) != []


# --- the Debian archive: one snapshot, never the live archive; the lock pins its Release files

DEBIAN_LOCK = ROOT / "deployments" / "docker" / "debian.lock"
SNAPSHOT_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z")
ARCHIVE_URI = re.compile(
    r"https://snapshot\.debian\.org/archive/(?P<archive>debian|debian-security)/(?P<id>[^/]*)/"
)
KEYRINGS = "/usr/share/keyrings/"
SNAPSHOT_FIELDS = {"Types", "URIs", "Suites", "Components", "Signed-By", "Check-Valid-Until"}
LOCK_LINE = re.compile(r"(?P<hash>[0-9a-f]{64})  (?P<suite>\S+)")


def stanzas(sources: str) -> list[list[tuple[str, str]]]:
    """The stanzas of a deb822 sources file as (field, value) pairs; comments are skipped."""
    blocks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for line in sources.splitlines():
        if line.startswith("#"):
            continue
        if not line.strip():
            if current:
                blocks.append(current)
            current = []
            continue
        key, _, value = line.partition(":")
        current.append((key, value.strip()))
    if current:
        blocks.append(current)
    return blocks


def archive_stanzas(sources: str) -> dict[str, dict[str, str]]:
    """The fields of the first stanza of each archive (``debian``, ``debian-security``)."""
    found: dict[str, dict[str, str]] = {}
    for block in stanzas(sources):
        given = dict(block)
        match = ARCHIVE_URI.fullmatch(given.get("URIs", ""))
        if match is not None and match["archive"] not in found:
            found[match["archive"]] = given
    return found


def snapshot_problems(sources: str) -> list[str]:
    """Two stanzas, one for each archive, each at an import ``<ID>`` of snapshot.debian.org
    (the two may differ), signed by one keyring, with the Valid-Until check off because a
    snapshot's Release files are past it by design."""
    blocks = stanzas(sources)
    if len(blocks) != 2:
        return [f"{len(blocks)} stanzas, not two"]
    fields = [dict(block) for block in blocks]
    bad = []
    for block, given in zip(blocks, fields, strict=True):
        if len(block) != len(given) or set(given) != SNAPSHOT_FIELDS:
            bad.append(f"fields {[key for key, _ in block]}, not {sorted(SNAPSHOT_FIELDS)}")
    if bad:
        return bad
    for given in fields:
        for key, want in (("Types", "deb"), ("Components", "main"), ("Check-Valid-Until", "no")):
            if given[key] != want:
                bad.append(f"{key}: {given[key]}, not {want}")
    keyrings = {given["Signed-By"] for given in fields}
    if len(keyrings) != 1 or not all(
        k.startswith(KEYRINGS) and ".." not in k.split("/") for k in keyrings
    ):
        bad.append(f"Signed-By: {sorted(keyrings)}, not one file under {KEYRINGS}")
    matches = [ARCHIVE_URI.fullmatch(given["URIs"]) for given in fields]
    for given, match in zip(fields, matches, strict=True):
        if match is None or SNAPSHOT_ID.fullmatch(match["id"]) is None:
            bad.append(f"URIs: {given['URIs']}, not an import of snapshot.debian.org")
    archives = sorted(match["archive"] for match in matches if match is not None)
    if archives != ["debian", "debian-security"]:
        return [*bad, f"archives {archives}, not one stanza each for debian and debian-security"]
    named = archive_stanzas(sources)
    codename = named["debian"]["Suites"].split(" ")[0]
    for archive, suites in (
        ("debian", f"{codename} {codename}-updates"),
        ("debian-security", f"{codename}-security"),
    ):
        if named[archive]["Suites"] != suites:
            bad.append(f"Suites of {archive}: {named[archive]['Suites']}, not {suites}")
    return bad


def test_the_release_image_installs_from_the_snapshot_debian_sources_names() -> None:
    assert snapshot_problems(DEBIAN_SOURCES.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "defect",
    [
        "live-archive",
        "snapshot-format",
        "valid-until-checked",
        "third-stanza",
        "one-archive-twice",
        "other-suite",
        "keyrings-differ",
        "other-field",
    ],
)
def test_a_sources_file_that_is_not_the_snapshot_of_both_archives_is_found(defect: str) -> None:
    """Each defect is made on the real file, around the values it holds, so moving the
    snapshot changes no defect."""
    sources = DEBIAN_SOURCES.read_text(encoding="utf-8")
    first = sources.index("Types:")
    stanza = sources[first : sources.index("\n\n", first) + 1]
    if defect == "live-archive":
        broken = re.sub(
            r"^URIs: https://snap.*$",
            "URIs: http://deb.debian.org/debian",
            sources,
            count=1,
            flags=re.M,
        )
    elif defect == "snapshot-format":
        broken = sources
        for snapshot in set(SNAPSHOT_ID.findall(sources)):
            broken = broken.replace(snapshot, snapshot[:8])
    elif defect == "valid-until-checked":
        broken = sources.replace("Check-Valid-Until: no", "Check-Valid-Until: yes", 1)
    elif defect == "third-stanza":
        broken = sources + "\n" + stanza
    elif defect == "one-archive-twice":
        broken = sources.replace("archive/debian-security/", "archive/debian/")
    elif defect == "other-suite":
        broken = sources.replace("-updates", "-backports")
    elif defect == "keyrings-differ":
        (keyring,) = set(re.findall(r"^Signed-By: (.*)$", sources, re.M))
        head, _, tail = sources.rpartition(keyring)
        broken = head + KEYRINGS + "debian-archive-trixie-security-automatic.pgp" + tail
    else:
        broken = sources.replace(
            "Components: main\n", "Components: main\nArchitectures: arm64\n", 1
        )
    assert broken != sources
    assert snapshot_problems(broken) != []


def lock_problems(lock: str, sources: str) -> list[str]:
    """debian.lock holds one line ``<SHA-256>  <suite>`` for each suite of debian.sources, each
    once and no other: the hash of the Release file the snapshot serves for it."""
    suites = {
        suite
        for block in stanzas(sources)
        for suite in dict(block).get("Suites", "").split(" ")
        if suite
    }
    bad, pinned = [], []
    for line in lock.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = LOCK_LINE.fullmatch(line)
        if match is None:
            bad.append(f"not '<sha256>  <suite>': {line[:80]}")
        else:
            pinned.append(match["suite"])
    bad += [
        f"{s} is pinned {pinned.count(s)} times, not once"
        for s in sorted(suites)
        if pinned.count(s) != 1
    ]
    bad += [
        f"{s} is pinned and in no stanza of debian.sources" for s in sorted(set(pinned) - suites)
    ]
    return bad


def test_the_lock_pins_a_release_file_for_each_suite_of_the_sources() -> None:
    lock = DEBIAN_LOCK.read_text(encoding="utf-8")
    assert lock_problems(lock, DEBIAN_SOURCES.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "defect", ["lock-missing-suite", "lock-other-suite", "lock-suite-twice", "lock-not-sha256"]
)
def test_a_lock_that_does_not_match_the_sources_is_found(defect: str) -> None:
    """Each defect is made on the real file, around the lines it holds."""
    lock = DEBIAN_LOCK.read_text(encoding="utf-8")
    lines = [line for line in lock.splitlines() if line.strip() and not line.startswith("#")]
    if defect == "lock-missing-suite":
        broken = lock.replace(lines[1] + "\n", "")
    elif defect == "lock-other-suite":
        broken = lock.replace(lines[-1], lines[-1].split()[0] + "  trixie-backports")
    elif defect == "lock-suite-twice":
        broken = lock + lines[0] + "\n"
    else:
        broken = lock.replace(lines[0], lines[0][1:])
    assert broken != lock
    assert lock_problems(broken, DEBIAN_SOURCES.read_text(encoding="utf-8")) != []


# --- git in the release image: one exact Debian version, installed by one apt-get ---------

GIT_PREFIX = "ARG GIT_VERSION="
GIT_PACKAGE = '"git=$GIT_VERSION"'
GIT_LISTS = "rm -rf /var/lib/apt/lists/*"
SOURCES_COPY = "COPY deployments/docker/debian.sources /etc/apt/sources.list.d/debian.sources"
LOCK_COPY = "COPY deployments/docker/debian.lock /etc/apt/debian.lock"
SOURCE_CHECK = (
    'test "$(ls -A /etc/apt/sources.list.d)" = debian.sources',
    "test ! -e /etc/apt/sources.list",
)
LOCK_PATH = "/etc/apt/debian.lock"
RELEASE_FILES = "/var/lib/apt/lists/*_InRelease"
LIVE_ARCHIVES = ("deb.debian.org", "security.debian.org")
# A Debian version: an optional epoch, an upstream version, a revision. No `*`, no range.
DEBIAN_VERSION = re.compile(r"(\d+:)?\d[A-Za-z0-9.+~]*-[A-Za-z0-9.+~]+")
APT = re.compile(r"\b(?:apt-get|apt)\s|\bdpkg\s+(?:-i|--install)\b")
UPGRADE = re.compile(r"\b(?:dist-)?upgrade\b")


def git_problems(dockerfile: str) -> list[str]:
    """The release image installs git at the one exact version ARG GIT_VERSION declares, in
    its runtime stage, by the one RUN that runs apt-get: after the two COPY lines that put
    debian.sources and debian.lock in place, beginning with the check that debian.sources is
    the only apt source, and checking the Release files apt fetched against the lock between
    the update and the install, which installs that package alone. No other RUN of the file
    runs apt or dpkg, and the live archive is named nowhere."""
    lines = dockerfile.splitlines()
    last_from = max((n for n, line in enumerate(lines) if FROM.match(line)), default=-1)
    declared = [
        (n, line[len(GIT_PREFIX) :]) for n, line in enumerate(lines) if line.startswith(GIT_PREFIX)
    ]
    bad = []
    if len(declared) != 1:
        bad.append(f"{GIT_PREFIX} is declared {len(declared)} times, not once")
    else:
        number, version = declared[0]
        if number < last_from:
            bad.append(f"{GIT_PREFIX} is not in the runtime stage")
        if DEBIAN_VERSION.fullmatch(version) is None:
            bad.append(f"{GIT_PREFIX}{version} is not one exact Debian version")
    runs = run_instructions(dockerfile)
    apt = [run for run in runs if APT.search(run)]
    stage_lines = lines[last_from:]
    stage = run_instructions("\n".join(stage_lines))
    installs = [run for run in stage if "apt-get install" in run]
    if len(installs) != 1:
        return [*bad, f"the runtime stage has {len(installs)} RUNs of apt-get install, not one"]
    (install,) = installs
    commands = [command.strip() for command in install.removeprefix("RUN ").split("&&")]
    steps = [command.split(" ")[:2] for command in commands]
    update = next((n for n, step in enumerate(steps) if step == ["apt-get", "update"]), -1)
    after = next((n for n, step in enumerate(steps) if step == ["apt-get", "install"]), -1)
    if not 0 <= update < after:
        return [*bad, "apt-get install does not follow apt-get update"]
    run_start = [n for n, line in enumerate(stage_lines) if line.startswith("RUN ")][
        stage.index(install)
    ]
    for copy in (SOURCES_COPY, LOCK_COPY):
        where = [n for n, line in enumerate(stage_lines) if line == copy]
        if len(where) != 1 or where[0] > run_start:
            bad.append(f"{copy!r} is not once in the runtime stage before the RUN")
    for name in ("debian.sources", "debian.lock"):
        copied = [line for line in lines if line.startswith("COPY") and name in line]
        if len(copied) != 1:
            bad.append(f"{name} is copied {len(copied)} times, not once")
    if tuple(commands[:2]) != SOURCE_CHECK:
        bad.append("the RUN does not begin with the check that debian.sources is the only source")
    if "--error-on=any" not in commands[update].split(" "):
        bad.append("apt-get update lacks --error-on=any")
    if not any(LOCK_PATH in c and RELEASE_FILES in c for c in commands[update + 1 : after]):
        bad.append(f"no command between the update and the install checks {RELEASE_FILES}")
    bad += [
        f"RUN --mount reads the build context: {run}"
        for run in runs
        if run.startswith("RUN --mount")
    ]
    bad += [f"the Dockerfile names the live archive {h}" for h in LIVE_ARCHIVES if h in dockerfile]
    if commands[-1] != GIT_LISTS:
        bad.append(f"the install does not end with {GIT_LISTS}")
    arguments = iter(commands[after].split(" ")[2:])
    flags, words = [], []
    for argument in arguments:
        if argument == "-o":  # an option and the word after it are one
            flags.append(f"-o {next(arguments, '')}")
        elif argument.startswith("-"):
            flags.append(argument)
        else:
            words.append(argument)
    if "--no-install-recommends" not in flags:
        bad.append("apt-get install lacks --no-install-recommends")
    if words != [GIT_PACKAGE]:
        bad.append(f"apt-get install installs more or less than {GIT_PACKAGE}")
    bad += [f"RUN runs apt elsewhere: {run}" for run in apt if run != install]
    bad += [f"RUN upgrades the image: {run}" for run in apt if UPGRADE.search(run)]
    return bad


def test_the_release_image_installs_one_exact_git() -> None:
    assert git_problems(RELEASE_DOCKERFILE.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "defect",
    [
        "unpinned",
        "wildcard",
        "declared-twice",
        "in-build-stage",
        "recommends",
        "second-package",
        "lists-kept",
        "upgrade",
        "apt-elsewhere",
        "sources-not-copied",
        "lock-not-copied",
        "sources-unchecked",
        "lock-unchecked",
        "no-error-on",
        "live-archive-named",
        "run-mount",
    ],
)
def test_an_apt_install_not_pinned_is_found(defect: str) -> None:
    """Each defect is made on the real file, around the version it holds, so raising the pin
    changes no defect."""
    dockerfile = RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    (declaration,) = re.findall(rf"^{GIT_PREFIX}.*$", dockerfile, re.M)
    block = re.search(
        rf"^{GIT_PREFIX}.*\n(?:COPY .*\n)*RUN .*\\\n(?:.*\\\n)*.*\n", dockerfile, re.M
    )
    assert block is not None
    build_from = re.search(r"^FROM .* AS build\n", dockerfile, re.M)
    assert build_from is not None
    check = " && ".join(SOURCE_CHECK)
    if defect == "unpinned":
        broken = dockerfile.replace(GIT_PACKAGE, "git")
    elif defect == "wildcard":
        broken = dockerfile.replace(declaration, declaration.rsplit("-", 1)[0] + "-*")
    elif defect == "declared-twice":
        broken = dockerfile.replace(declaration, f"{declaration}\n{declaration}")
    elif defect == "in-build-stage":
        broken = dockerfile.replace(block[0], "").replace(build_from[0], build_from[0] + block[0])
    elif defect == "recommends":
        broken = dockerfile.replace(" --no-install-recommends", "")
    elif defect == "second-package":
        broken = dockerfile.replace(GIT_PACKAGE, f"{GIT_PACKAGE} curl")
    elif defect == "lists-kept":
        broken = dockerfile.replace(f" \\\n    && {GIT_LISTS}", "")
    elif defect == "upgrade":
        broken = dockerfile.replace(
            "&& apt-get install", "&& apt-get upgrade --yes && apt-get install", 1
        )
    elif defect == "apt-elsewhere":
        broken = dockerfile.replace(build_from[0], build_from[0] + "RUN apt-get install curl\n")
    elif defect == "sources-not-copied":
        broken = dockerfile.replace(SOURCES_COPY + "\n", "")
    elif defect == "lock-not-copied":
        broken = dockerfile.replace(LOCK_COPY + "\n", "")
    elif defect == "sources-unchecked":
        broken = dockerfile.replace(f"{check} \\\n    && apt-get update", "apt-get update", 1)
    elif defect == "lock-unchecked":
        broken = re.sub(
            r" +&& python -c '.*?" + re.escape(LOCK_PATH) + r" \\\n", "", dockerfile, flags=re.S
        )
    elif defect == "no-error-on":
        broken = dockerfile.replace(" --error-on=any", "")
    elif defect == "live-archive-named":
        broken = dockerfile + "# mirror: http://deb.debian.org/debian\n"
    else:
        broken = dockerfile.replace("RUN test", "RUN --mount=type=bind,target=/mnt test", 1)
    assert broken != dockerfile
    assert git_problems(broken) != []


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


BUILDX = "docker/setup-buildx-action@"
BUILDX_INPUTS = ("version", "cache-binary")


def buildx_settings(text: str) -> list[tuple[str, ...]]:
    """What each setup-buildx step of a workflow or an action must share with the others: the
    action's pin with its version comment, and the inputs ``version`` and ``cache-binary``.
    The driver and its options differ with what a job builds, and are not compared."""
    pins = [
        (match["ref"], match["comment"] or "")
        for line in text.splitlines()
        if (match := USES.match(line)) and match["ref"].startswith(BUILDX)
    ]
    steps = [s for s in _steps(yaml.safe_load(text)) if str(s.get("uses", "")).startswith(BUILDX)]
    inputs = [tuple(str((s.get("with") or {}).get(key)) for key in BUILDX_INPUTS) for s in steps]
    return [(*pin, *given) for pin, given in zip(pins, inputs, strict=True)]


def buildx_problems(texts: dict[str, str]) -> list[str]:
    """The setup-buildx steps of every file are one declaration written more than once (the
    release workflow and the two actions CI shares with it): it is the same everywhere."""
    found = {row for text in texts.values() for row in buildx_settings(text)}
    return [f"setup-buildx is set {len(found)} ways: {sorted(found)}"] if len(found) > 1 else []


def _buildx_texts() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in WORKFLOWS + ACTIONS
    }


def test_buildx_is_set_up_the_same_way_in_every_file() -> None:
    """One action pin, one buildx version, no binary cache: written in release.yml, in
    assemble-index and in build-image, and equal."""
    assert buildx_problems(_buildx_texts()) == []


def test_the_buildx_steps_are_found_at_all() -> None:
    held = {path: buildx_settings(text) for path, text in _buildx_texts().items()}
    assert sorted(path for path, rows in held.items() if rows) == [
        ".github/actions/assemble-index/action.yml",
        ".github/actions/build-image/action.yml",
        ".github/workflows/release.yml",
    ]
    assert all(len(rows) == 1 for rows in held.values() if rows)


@pytest.mark.parametrize("defect", ["version", "pin", "pin-comment", "cache-binary", "no-version"])
@pytest.mark.parametrize(
    "path",
    [
        ".github/actions/assemble-index/action.yml",
        ".github/actions/build-image/action.yml",
        ".github/workflows/release.yml",
    ],
)
def test_a_buildx_step_that_differs_from_the_others_is_found(path: str, defect: str) -> None:
    """Each defect is made in one file, around the values it holds, so moving the pin or the
    version in every file at once changes no defect."""
    texts = _buildx_texts()
    ((ref, comment, version, _),) = buildx_settings(texts[path])
    if defect == "version":
        broken = re.sub(
            rf"^(\s+version: ){re.escape(version)}$", r"\g<1>v0.0.1", texts[path], flags=re.M
        )
    elif defect == "pin":
        broken = texts[path].replace(ref.split("@")[1], "0" * 40)
    elif defect == "pin-comment":
        broken = texts[path].replace(f"# {comment}", "# v0.0.1")
    elif defect == "cache-binary":
        broken = texts[path].replace("cache-binary: false", "cache-binary: true")
    else:
        broken = re.sub(rf"^\s+version: {re.escape(version)}\n", "", texts[path], flags=re.M)
    assert broken != texts[path]
    assert buildx_problems({**texts, path: broken}) != []


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

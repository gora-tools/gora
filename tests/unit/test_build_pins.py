"""Every build input is pinned, and the pins agree (ADR-021, TESTING rules 3 and 8).

Read from the files themselves, offline (TESTING rule 6): the workflows and the actions
they share, the Dockerfiles, pyproject.toml and .python-version, and the output of
scripts/gate/versions.sh, which reads the release Dockerfile. Each check is a function of the parsed
text, and each is shown red here on a defect of its class (ADR-0016 rule 1) before it is
trusted on the real files.
"""

import json
import re
import shlex
import subprocess
import tomllib
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.integration.test_own_release_files import PUSHED_TARGET, _reached, _stages

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ACTIONS = sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
DOCKERFILES = sorted((ROOT / "deployments").rglob("*Dockerfile"))
RELEASE_DOCKERFILE = ROOT / "deployments" / "docker" / "Dockerfile"
MAKEFILE = ROOT / "Makefile"
RED_FIRST = ROOT / "scripts" / "gate" / "red-first.sh"
GATE_IMAGES = ROOT / "deployments" / "docker" / "gate-images.Dockerfile"
BASE_DOCKERFILE = ROOT / "deployments" / "docker" / "base.Dockerfile"
SCANNER_DOCKERFILE = ROOT / "deployments" / "docker" / "scanner.Dockerfile"
OWN_WORKFLOW = ROOT / ".github" / "workflows" / "own-image.yml"
BASE_WORKFLOW = ROOT / ".github" / "workflows" / "base.yml"
SCANNER_WORKFLOW = ROOT / ".github" / "workflows" / "scanner.yml"
OWN_IMAGE_SMOKE = ROOT / "scripts" / "gate" / "own-image-smoke.sh"
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


def found_only(problems: list[str], *named: str) -> None:
    """A defect case finds what its one defect causes, each problem named by a part of its
    message, in order, and nothing else: a case that also trips a second check stays red
    with the check it is for deleted (ADR-0016 rule 1)."""
    assert len(problems) == len(named), problems
    assert all(name in problem for name, problem in zip(named, problems, strict=True)), problems


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
    assert BASE_DOCKERFILE in DOCKERFILES
    assert SCANNER_DOCKERFILE in DOCKERFILES


# --- runners are named, never ubuntu-latest ------------------------------------------------


def runners(document: dict[Any, Any]) -> list[str]:
    """Every runner a workflow's jobs run on, a matrix value counted for each entry. A job that
    calls a reusable workflow runs on nothing: the called jobs name their runners."""
    found = []
    for job in document.get("jobs", {}).values():
        if "uses" in job:
            continue
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
    document = _yaml(path)
    found = runners(document)
    # A caller of a reusable workflow has no job that runs: it is one call.
    assert found or all("uses" in job for job in document["jobs"].values())
    assert [r for r in found if r not in RUNNERS] == []


def test_a_call_runs_on_nothing_and_its_reusable_workflow_is_not_a_call() -> None:
    assert runners(_yaml(BASE_WORKFLOW)) == runners(_yaml(SCANNER_WORKFLOW)) == []
    # guard, the two platforms of build, publish
    assert sorted(runners(_yaml(OWN_WORKFLOW))) == [
        "ubuntu-24.04",
        "ubuntu-24.04",
        "ubuntu-24.04",
        "ubuntu-24.04-arm",
    ]
    assert runners({"jobs": {"a": {"uses": "./x.yml"}, "b": {"runs-on": "ubuntu-latest"}}}) == [
        "ubuntu-latest"
    ]


@pytest.mark.parametrize(
    ("jobs", "found"),
    [
        ({"a": {"runs-on": "ubuntu-latest"}}, "ubuntu-latest"),
        ({"a": {"runs-on": "ubuntu-24.04"}, "b": {"runs-on": "ubuntu-latest"}}, "ubuntu-latest"),
        (
            {
                "a": {
                    "runs-on": "${{ matrix.runner }}",
                    "strategy": {"matrix": {"include": [{"runner": "ubuntu-latest"}]}},
                }
            },
            "ubuntu-latest",
        ),
        (
            {"a": {"runs-on": "${{ matrix.runner }}", "strategy": {"matrix": {}}}},
            "(matrix.runner with no value)",
        ),
        ({"a": {"runs-on": "${{ matrix.os }}"}}, "${{ matrix.os }}"),
    ],
    ids=["jobs0", "jobs1", "jobs2", "jobs3", "jobs4"],
)
def test_ubuntu_latest_or_an_unknown_runner_is_found(jobs: dict[str, Any], found: str) -> None:
    assert [r for r in runners({"jobs": jobs}) if r not in RUNNERS] == [found]


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


SHIPPED_PYTHON = "ARG BASE_PYTHON_VERSION="


def declared(dockerfile: str, prefix: str) -> list[str]:
    """Every value the Dockerfile gives the argument ``prefix`` names (``ARG NAME=``)."""
    return [line[len(prefix) :] for line in dockerfile.splitlines() if line.startswith(prefix)]


def python_declared(dockerfile: str) -> list[str]:
    """Every value the Dockerfile gives PYTHON_VERSION, which base.Dockerfile declares for the
    build of the base. The Python the release image ships is its BASE_PYTHON_VERSION."""
    return declared(dockerfile, "ARG PYTHON_VERSION=")


def run_versions(dockerfile: Path | None = None, script: Path = VERSIONS_SH) -> tuple[int, str]:
    """scripts/gate/versions.sh, as CI runs it: its exit code and its standard output. Given
    a release Dockerfile, it reads that file alone."""
    command = ["bash", str(script), *([str(dockerfile)] if dockerfile else [])]
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


def test_versions_sh_reads_the_release_dockerfile() -> None:
    """The script prints exactly the two declarations, parsed here independently, of the
    release Dockerfile: the uv of its uv stage and the Python the image ships, beside the base
    digest. The PYTHON_VERSION of base.Dockerfile, which the build of the base checks, is not
    the release's: the release Dockerfile has none."""
    release = RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    (python,) = declared(release, SHIPPED_PYTHON)
    assert python_declared(release) == []
    code, out = run_versions()
    assert code == 0
    assert out == f"python={python}\nuv={uv_from_image(release, 'uv')}\n"


UV_STAGE = f"FROM {UV_IMAGE}0.12.22@sha256:{'a' * 64} AS uv\n"
PYTHON_ARG = f"{SHIPPED_PYTHON}3.12.15\n"
# The base's own pair, beside the release Dockerfile as in the repository, at a patch the
# release does not ship yet: a script that read it would answer with that patch.
BASE_BESIDE = "ARG PYTHON_VERSION=3.12.16\n"


@pytest.mark.parametrize(
    "release",
    [
        PYTHON_ARG,
        UV_STAGE,
        UV_STAGE + f"{SHIPPED_PYTHON}3.12\n",
        UV_STAGE + PYTHON_ARG + f"{SHIPPED_PYTHON}3.12.14\n",
        UV_STAGE.replace(f"@sha256:{'a' * 64}", "") + PYTHON_ARG,
        UV_STAGE.replace("0.12.22", "latest") + PYTHON_ARG,
        UV_STAGE + UV_STAGE + PYTHON_ARG,
        # The base's name for it is not the release's: a PYTHON_VERSION here is not read.
        UV_STAGE + "ARG PYTHON_VERSION=3.12.15\n",
    ],
    ids=[
        "no-uv",
        "no-python",
        "python-minor",
        "python-twice",
        "uv-no-digest",
        "uv-latest",
        "uv-twice",
        "the-base-name-in-the-release-file",
    ],
)
def test_versions_sh_refuses_anything_but_one_exact_pair(release: str, tmp_path: Path) -> None:
    path = tmp_path / "Dockerfile"
    path.write_text(release, encoding="utf-8")
    (tmp_path / "base.Dockerfile").write_text(BASE_BESIDE, encoding="utf-8")
    assert run_versions(path) == (2, "")


def test_versions_sh_refuses_a_missing_dockerfile(tmp_path: Path) -> None:
    assert run_versions(tmp_path / "Dockerfile") == (2, "")


def test_versions_sh_accepts_one_exact_pair(tmp_path: Path) -> None:
    """The refusals above are the one defect each: the same file with none is accepted, and
    its Python is the release's, not that of the base beside it."""
    path = tmp_path / "Dockerfile"
    path.write_text(UV_STAGE + PYTHON_ARG, encoding="utf-8")
    (tmp_path / "base.Dockerfile").write_text(BASE_BESIDE, encoding="utf-8")
    assert run_versions(path) == (0, "python=3.12.15\nuv=0.12.22\n")


def test_a_versions_sh_that_reads_the_base_again_is_found(tmp_path: Path) -> None:
    """The defect is made on the real script: the Python read from base.Dockerfile beside the
    release Dockerfile, as it was before the release declared its own. The trap above answers
    with the base's patch, and the acceptance fails."""
    real = VERSIONS_SH.read_text(encoding="utf-8")
    reads = 'awk -F= \'$1 == "ARG BASE_PYTHON_VERSION" { print $2 }\' "$file"'
    assert real.count(reads) == 1
    base = (
        'awk -F= \'$1 == "ARG PYTHON_VERSION" { print $2 }\' "$(dirname "$file")/base.Dockerfile"'
    )
    script = tmp_path / "versions.sh"
    script.write_text(real.replace(reads, base), encoding="utf-8")
    path = tmp_path / "Dockerfile"
    path.write_text(UV_STAGE + PYTHON_ARG, encoding="utf-8")
    (tmp_path / "base.Dockerfile").write_text(BASE_BESIDE, encoding="utf-8")
    assert run_versions(path, script) == (0, "python=3.12.16\nuv=0.12.22\n")


def test_ci_installs_what_versions_sh_prints() -> None:
    """Every setup-uv takes uv and Python from the versions.sh step before it."""
    documents = [_yaml(path) for path in WORKFLOWS + ACTIONS]
    assert _setup_uv_count(documents) >= 2  # check and test
    assert not_derived(documents) == []


_SOURCE = {
    "id": "versions",
    "shell": "bash",
    "run": 'scripts/gate/versions.sh | tee -a "$GITHUB_OUTPUT"',
}
_DERIVED_WITH = {
    "version": "${{ steps.versions.outputs.uv }}",
    "python-version": "${{ steps.versions.outputs.python }}",
}


_NOT_DERIVED_UV = "a: setup-uv version='${{ steps.versions.outputs.uv }}'"
_NOT_DERIVED_PYTHON = "a: setup-uv python-version='${{ steps.versions.outputs.python }}'"


@pytest.mark.parametrize(
    ("steps", "found"),
    [
        (
            [_SOURCE, {"uses": f"{SETUP_UV}x", "with": {**_DERIVED_WITH, "version": "0.12.22"}}],
            ["a: setup-uv version='0.12.22'"],
        ),
        (
            [_SOURCE, {"uses": f"{SETUP_UV}x", "with": {"version": _DERIVED_WITH["version"]}}],
            ["a: setup-uv python-version=None"],
        ),
        (
            # Both inputs derived, so the version file is the one defect.
            [
                _SOURCE,
                {
                    "uses": f"{SETUP_UV}x",
                    "with": {**_DERIVED_WITH, "version-file": "pyproject.toml"},
                },
            ],
            ["a: setup-uv version-file=pyproject.toml"],
        ),
        (
            [{"uses": f"{SETUP_UV}x", "with": _DERIVED_WITH}, _SOURCE],
            [_NOT_DERIVED_UV, _NOT_DERIVED_PYTHON],
        ),
        (
            [
                {"id": "versions", "run": "echo uv=0.12.22"},
                {"uses": f"{SETUP_UV}x", "with": _DERIVED_WITH},
            ],
            [_NOT_DERIVED_UV, _NOT_DERIVED_PYTHON],
        ),
        (
            [
                _SOURCE,
                {
                    "uses": f"{SETUP_UV}x",
                    "with": {**_DERIVED_WITH, "version": "${{ steps.versions.outputs.python }}"},
                },
            ],
            ["a: setup-uv version='${{ steps.versions.outputs.python }}'"],
        ),
    ],
    ids=["typed", "no-python", "version-file", "read-after", "other-step", "wrong-output"],
)
def test_a_version_ci_does_not_derive_is_found(
    steps: list[dict[str, Any]], found: list[str]
) -> None:
    """read-after and other-step have one defect each, the step the inputs read: both inputs
    name it, so both are found."""
    assert not_derived([{"jobs": {"a": {"steps": steps}}}]) == found


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
        # Above the floor, so only the gate image disagrees.
        dockerfile = dockerfile.replace(f"{UV_IMAGE}{pinned}@", f"{UV_IMAGE}99.0.0@")
        found = f"linux-gate uv {pinned} is not 99.0.0"
    elif where == "gate-images":
        gate_images = gate_images.replace(f"{UV_IMAGE}{pinned}-", f"{UV_IMAGE}0.0.1-")
        found = f"linux-gate uv 0.0.1 is not {pinned}"
    else:
        pyproject = pyproject.replace(_required(pyproject), ">=99.0.0")
        found = f"required-version >=99.0.0 does not admit {pinned}"
    assert uv_disagreements(pyproject, dockerfile, gate_images) == [found]


def base_problems(dockerfile: str, sources: str) -> list[str]:
    """GORA's own base image (base.Dockerfile) has one python base,
    ``python:<minor>-slim-<codename>@<digest>``: the minor version of PYTHON_VERSION, and the
    first suite of the ``debian`` stanza of debian.sources."""
    declared = python_declared(dockerfile)
    suites = archive_stanzas(sources).get("debian", {}).get("Suites", "").split(" ")
    if len(declared) != 1 or not suites[0]:
        return [f"PYTHON_VERSION declared {len(declared)} times; the debian stanza has {suites}"]
    minor = declared[0].rsplit(".", 1)[0]
    images = [m["image"] for line in dockerfile.splitlines() if (m := FROM.match(line))]
    python = [image for image in images if image.startswith("python:")]
    bad = []
    if len(python) != 1:
        bad.append(f"the python bases are not one image: {python}")
    want = rf"python:{re.escape(minor)}-slim-{re.escape(suites[0])}@sha256:[0-9a-f]{{64}}"
    bad += [f"{image} is not {want}" for image in python if re.fullmatch(want, image) is None]
    return bad


def test_gora_base_image_names_the_python_minor_and_the_debian_release() -> None:
    """base.Dockerfile, which declares PYTHON_VERSION alone, has one python base, by digest,
    whose tag names the minor version of its PYTHON_VERSION and the Debian release
    debian.sources names, so a digest bump cannot move the image to the next release; the
    build compares the base's interpreter with PYTHON_VERSION. The release image has no
    python base of its own: it builds with the base's interpreter, the only one uv may use
    (UV_PYTHON_PREFERENCE). The Linux gate image and .python-version name the minor version."""
    dockerfile = BASE_DOCKERFILE.read_text(encoding="utf-8")
    sources = DEBIAN_SOURCES.read_text(encoding="utf-8")
    assert base_problems(dockerfile, sources) == []
    (declared,) = python_declared(dockerfile)
    assert re.fullmatch(r"3\.\d+\.\d+", declared)
    minor = declared.rsplit(".", 1)[0]
    lines = dockerfile.splitlines()
    (first,) = [n for n, line in enumerate(lines) if FROM.match(line)]
    code = "\n".join(line for line in lines[first:] if not line.lstrip().startswith("#"))
    assert f"ARG PYTHON_VERSION={declared}" in lines[first:]
    assert '"$PYTHON_VERSION"' in code  # the build compares the base's interpreter with it
    assert "UV_PYTHON_PREFERENCE=only-system" in RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    assert f"-python{minor}-" in GATE_IMAGES.read_text(encoding="utf-8")
    # Where a developer works, the minor version only: any patch of it runs locally.
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == minor


@pytest.mark.parametrize(
    "defect", ["tag-without-codename", "tag-with-patch", "codename-other-than-sources", "two-bases"]
)
def test_a_base_image_whose_tag_misses_the_python_or_the_release_is_found(defect: str) -> None:
    """Each defect is made on the real files, around the values they hold."""
    real = (
        BASE_DOCKERFILE.read_text(encoding="utf-8"),
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
    elif defect == "codename-other-than-sources":
        sources = sources.replace(codename, "forky" if codename != "forky" else "sid")
    else:
        first = next(line for line in dockerfile.splitlines() if FROM.match(line))
        dockerfile = dockerfile.replace(first, f"{first}\n{first}", 1)
    assert (dockerfile, sources) != real
    found = {
        "two-bases": ["the python bases are not one image: "],
    }.get(defect, [" is not python:"])
    found_only(base_problems(dockerfile, sources), *found)


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


NOT_A_FLOOR = "is not a floor >=X.Y.Z"


@pytest.mark.parametrize(
    ("required", "found"),
    [
        ("==0.12.22", NOT_A_FLOOR),
        (">=99.0.0", "the floor 99.0.0 is above the pinned uv"),
        ("~=0.12", NOT_A_FLOOR),
        ("", NOT_A_FLOOR),
        (">=0.12", NOT_A_FLOOR),
    ],
    ids=["==0.12.22", ">=99.0.0", "~=0.12", "", ">=0.12"],
)
def test_a_pin_or_a_floor_above_the_pin_is_found(required: str, found: str) -> None:
    pyproject, dockerfile, _ = _uv_inputs()
    pyproject = pyproject.replace(f'"{_required(pyproject)}"', f'"{required}"')
    found_only(floor_problems(pyproject, dockerfile), found)


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


def pushed_part(dockerfile: str) -> str:
    """The Dockerfile without the stages the pushed image is not built from (the stage test),
    so that what is said of the image is said of its stages and not of the tests' (the same
    stages the inputs check reads, tests/integration/test_own_release_files.py)."""
    head, *chunks = re.split(r"(?m)^(?=FROM\s)", dockerfile)
    stages = _stages(dockerfile)
    assert len(chunks) == len(stages), "a FROM the two readings of the Dockerfile do not share"
    reached = _reached(stages, PUSHED_TARGET)
    return head + "".join(
        chunk for chunk, stage in zip(chunks, stages, strict=True) if stage.name in reached
    )


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
    syncs = [
        run for run in run_instructions(pushed_part(dockerfile)) if run.startswith("RUN uv sync ")
    ]
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


# What each defect below causes, by a part of its message.
BACKEND_FOUND = {
    "no-group": "the group build is not one requirement: None",
    "group-range": "the group build is not hatchling==X.Y.Z: hatchling==",
    "group-two": "the group build is not one requirement: ['hatchling==",
    "requires-pin": "[build-system] requires is not one floor hatchling>=X.Y.Z",
    "floor-above-pin": "is above the pinned hatchling",
    "pinned-twice": "hatchling is pinned 2 times in pyproject.toml",
    "isolated": "the sync that installs the project lacks",
    "no-group-on-build": "the sync that installs the project lacks",
    "group-left-in-image": "the last sync keeps a dependency group in the image",
    "make-sync-isolated": "the Makefile: uv sync --locked --all-extras --group check ",
    "make-sync-test-no-group": "the Makefile: uv sync --locked --all-extras --group test ",
    "make-sync-gone": "the Makefile runs no uv sync that installs the project",
    "red-first-isolated": "red-first.sh: uv sync ",
    "red-first-no-group": "red-first.sh: uv sync ",
    "red-first-sync-gone": "red-first.sh runs no uv sync that installs the project",
}


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
        # The group renamed, not deleted, so the pin is still there once.
        pyproject = pyproject.replace(
            f"{BUILD_GROUP} = [\n    {pin}\n]\n", f"backend = [\n    {pin}\n]\n"
        )
    elif defect == "group-range":
        # A prefix match is a range; it keeps `"hatchling==` once, so the count holds.
        pyproject = pyproject.replace(pin, f'"hatchling=={major}.{minor}.*",')
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
    found_only(backend_problems(pyproject, dockerfile, makefile, red_first), BACKEND_FOUND[defect])


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


GROUP_TOOLS_FOUND = {
    "tool-missing": "pytest-cov is in a dependency group and not in GROUP_TOOLS",
    "tool-extra": "bandit is in GROUP_TOOLS and in no dependency group",
    "tool-twice": "ruff is in GROUP_TOOLS twice",
    "group-grows": "bandit is in a dependency group and not in GROUP_TOOLS",
    "list-gone": "the Makefile sets GROUP_TOOLS 0 times, not once",
    "list-twice": "the Makefile sets GROUP_TOOLS 2 times, not once",
}


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
    assert group_tools_problems(pyproject, broken) == [GROUP_TOOLS_FOUND[defect]]


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


def test_the_base_image_installs_from_the_snapshot_debian_sources_names() -> None:
    assert snapshot_problems(DEBIAN_SOURCES.read_text(encoding="utf-8")) == []


NOT_AN_IMPORT = "not an import of snapshot.debian.org"
SNAPSHOT_FOUND = {
    # A URI of the live archive is no URI of an archive at all, so the archives are also short
    # of debian: one defect, two checks, and no edit of a live URI that trips only the first.
    "live-archive": (NOT_AN_IMPORT, "archives ['debian-security'], not one stanza each"),
    # Both stanzas carry the import, so both are found by the one check.
    "snapshot-format": (NOT_AN_IMPORT, NOT_AN_IMPORT),
    "valid-until-checked": ("Check-Valid-Until: yes, not no",),
    "third-stanza": ("3 stanzas, not two",),
    "one-archive-twice": ("archives ['debian', 'debian'], not one stanza each",),
    "other-suite": ("Suites of debian: ",),
    "keyrings-differ": ("Signed-By: ",),
    "other-field": ("fields ['Types', 'URIs', 'Suites', 'Components', 'Architectures',",),
}


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
    found_only(snapshot_problems(broken), *SNAPSHOT_FOUND[defect])


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


LOCK_FOUND = {
    "lock-missing-suite": "-updates is pinned 0 times, not once",
    "lock-other-suite": "trixie-backports is pinned and in no stanza of debian.sources",
    "lock-suite-twice": " is pinned 2 times, not once",
    "lock-not-sha256": "not '<sha256>  <suite>': ",
}


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
        # Added, not replaced, so every suite of the sources is still pinned once.
        broken = lock + lines[-1].split()[0] + "  trixie-backports\n"
    elif defect == "lock-suite-twice":
        broken = lock + lines[0] + "\n"
    else:
        # Added, not replaced, so every suite of the sources is still pinned once.
        broken = lock + lines[0][1:] + "\n"
    assert broken != lock
    found_only(
        lock_problems(broken, DEBIAN_SOURCES.read_text(encoding="utf-8")), LOCK_FOUND[defect]
    )


# --- git in the base image: one exact Debian version, installed by one apt-get ------------

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
UPGRADES = {("apt-get", verb) for verb in ("upgrade", "dist-upgrade", "full-upgrade")}
SIMULATED_UPGRADE = '"apt-get", "--simulate", "upgrade"'
# What the check after the install must detect: the text of the check that does it, and the
# fact it finds. A package apt would still upgrade or keep back is older than the snapshot's;
# one that `apt list --installed` marks `local` is at a version no source serves (newer, or not
# in Debian), which the simulated upgrade does not see.
LEFTOVER_DETECTIONS = (
    (('"Inst "',), "a package apt would still upgrade"),
    (('"The following packages have been kept back',), "a package apt would keep back"),
    (('"apt", "list", "--installed"', '"local"'), "an installed package no source serves"),
)
APT_FLAGS = ("--yes", "--no-install-recommends")
ACQUIRE = "-o Acquire::"


def apt_arguments(command: str) -> tuple[list[str], list[str]]:
    """The flags and the words that follow ``apt-get <verb>``."""
    arguments = iter(command.split(" ")[2:])
    flags, words = [], []
    for argument in arguments:
        if argument == "-o":  # an option and the word after it are one
            flags.append(f"-o {next(arguments, '')}")
        elif argument.startswith("-"):
            flags.append(argument)
        else:
            words.append(argument)
    return flags, words


def upgrade_problems(commands: list[str], update: int, install: int) -> list[str]:
    """The RUN upgrades what the python image carries to the snapshot's versions by one
    ``apt-get upgrade``, after the check of the Release files and before the install. The
    command is held by an allow-list: the verb ``upgrade`` (``dist-upgrade`` and
    ``full-upgrade`` may install new packages), ``--yes``, ``--no-install-recommends`` and the
    ``-o Acquire::`` retries and timeouts of the install, and no other word, so that nothing
    that may install a package is let in by a new spelling (``--with-new-pkgs``,
    ``-o APT::Get::Upgrade-Allow-New=true``). After the install one command checks that
    every package is the snapshot's: the simulated upgrade lists none and keeps none back, and
    no installed package is marked ``local``; a check without one of the three fails."""
    steps = [tuple(command.split(" ")[:2]) for command in commands]
    upgrades = [n for n, step in enumerate(steps) if step in UPGRADES]
    if len(upgrades) != 1:
        return [f"the RUN has {len(upgrades)} apt-get upgrades, not one"]
    (upgrade,) = upgrades
    flags, words = apt_arguments(commands[upgrade])
    acquire = [flag for flag in apt_arguments(commands[install])[0] if flag.startswith(ACQUIRE)]
    allowed = [*APT_FLAGS, *acquire]
    bad = [f"apt-get upgrade lacks {flag}" for flag in allowed if flag not in flags]
    if steps[upgrade][1] != "upgrade":
        bad.append(f"apt-get {steps[upgrade][1]} may install new packages")
    bad += [
        f"apt-get upgrade is given {flag}, which is not one of its flags"
        for flag in flags
        if flag not in allowed
    ]
    bad += [f"apt-get upgrade is given {word}, which it does not take" for word in words]
    lock = next((n for n, c in enumerate(commands) if LOCK_PATH in c and RELEASE_FILES in c), -1)
    if not max(update, lock) < upgrade < install:
        bad.append("apt-get upgrade is not between the check of the Release files and the install")
    checks = [n for n, c in enumerate(commands) if SIMULATED_UPGRADE in c]
    if not checks:
        bad.append("no command after the install checks that apt would upgrade nothing")
    elif checks[0] < install:
        bad.append("the check that apt would upgrade nothing does not follow apt-get install")
    else:
        bad += [
            f"the check after the install does not detect {found}"
            for texts, found in LEFTOVER_DETECTIONS
            if any(text not in commands[checks[0]] for text in texts)
        ]
    return bad


def git_problems(dockerfile: str) -> list[str]:
    """The base image installs git at the one exact version ARG GIT_VERSION declares, in its
    last stage (its only one), by the one RUN that runs apt-get: after the two COPY lines that
    put debian.sources and debian.lock in place, beginning with the check that debian.sources
    is the only apt source, and checking the Release files apt fetched against the lock
    between the update and the install, which installs that package alone. The same RUN
    upgrades the packages already there (``upgrade_problems``) and removes the lists last. No
    other RUN of the file runs apt or dpkg, and the live archive is named nowhere."""
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
    flags, words = apt_arguments(commands[after])
    bad += [f"apt-get install lacks {flag}" for flag in APT_FLAGS if flag not in flags]
    if words != [GIT_PACKAGE]:
        bad.append(f"apt-get install installs more or less than {GIT_PACKAGE}")
    bad += upgrade_problems(commands, update, after)
    bad += [f"RUN runs apt elsewhere: {run}" for run in apt if run != install]
    return bad


def test_the_base_image_installs_one_exact_git() -> None:
    assert git_problems(_base()) == []


NOT_IN_THE_STAGE = "is not once in the runtime stage before the RUN"
UPGRADE_MISPLACED = "apt-get upgrade is not between the check of the Release files and the install"
GIT_FOUND = {
    "unpinned": 'apt-get install installs more or less than "git=$GIT_VERSION"',
    "wildcard": "is not one exact Debian version",
    "declared-twice": "ARG GIT_VERSION= is declared 2 times, not once",
    "install-twice": "the runtime stage has 2 RUNs of apt-get install, not one",
    "recommends": "apt-get install lacks --no-install-recommends",
    "second-package": 'apt-get install installs more or less than "git=$GIT_VERSION"',
    "lists-kept": "the install does not end with rm -rf /var/lib/apt/lists/*",
    "upgrade-gone": "the RUN has 0 apt-get upgrades, not one",
    "upgrade-dist": "apt-get dist-upgrade may install new packages",
    "upgrade-full": "apt-get full-upgrade may install new packages",
    "upgrade-new-pkgs": "apt-get upgrade is given --with-new-pkgs, which is not one of its flags",
    "upgrade-allow-new": (
        "apt-get upgrade is given -o APT::Get::Upgrade-Allow-New=true, "
        "which is not one of its flags"
    ),
    "upgrade-extra-word": "apt-get upgrade is given curl, which it does not take",
    "upgrade-recommends": "apt-get upgrade lacks --no-install-recommends",
    "upgrade-prompts": "apt-get upgrade lacks --yes",
    "upgrade-retries": "apt-get upgrade lacks -o Acquire::Retries=10",
    "upgrade-after-install": UPGRADE_MISPLACED,
    "upgrade-before-lock-check": UPGRADE_MISPLACED,
    "leftover-gone": "no command after the install checks that apt would upgrade nothing",
    "leftover-before-install": "does not follow apt-get install",
    "leftover-inst-gone": "does not detect a package apt would still upgrade",
    "leftover-kept-gone": "does not detect a package apt would keep back",
    "leftover-unserved-gone": "does not detect an installed package no source serves",
    "apt-elsewhere": "RUN runs apt elsewhere: RUN apt-get clean",
    "sources-not-copied": f"{SOURCES_COPY!r} {NOT_IN_THE_STAGE}",
    "lock-not-copied": f"{LOCK_COPY!r} {NOT_IN_THE_STAGE}",
    "sources-copied-twice": "debian.sources is copied 2 times, not once",
    "sources-unchecked": "the RUN does not begin with the check that debian.sources is the only",
    "lock-unchecked": "no command between the update and the install checks",
    "no-error-on": "apt-get update lacks --error-on=any",
    "live-archive-named": "the Dockerfile names the live archive deb.debian.org",
    "run-mount": "RUN --mount reads the build context: RUN --mount=type=bind,target=/mnt true",
}


@pytest.mark.parametrize(
    "defect",
    [
        "unpinned",
        "wildcard",
        "declared-twice",
        "install-twice",
        "recommends",
        "second-package",
        "lists-kept",
        "upgrade-gone",
        "upgrade-dist",
        "upgrade-full",
        "upgrade-new-pkgs",
        "upgrade-allow-new",
        "upgrade-extra-word",
        "upgrade-recommends",
        "upgrade-prompts",
        "upgrade-retries",
        "upgrade-after-install",
        "upgrade-before-lock-check",
        "leftover-gone",
        "leftover-before-install",
        "leftover-inst-gone",
        "leftover-kept-gone",
        "leftover-unserved-gone",
        "apt-elsewhere",
        "sources-not-copied",
        "lock-not-copied",
        "sources-copied-twice",
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
    dockerfile = _base()
    (declaration,) = re.findall(rf"^{GIT_PREFIX}.*$", dockerfile, re.M)
    base_from = re.search(r"^FROM .*\n", dockerfile, re.M)
    assert base_from is not None
    check = " && ".join(SOURCE_CHECK)
    # The lines of the apt step the defects below move or change, as the file wraps them.
    upgrade, install = (
        next(line for line in dockerfile.splitlines(keepends=True) if f"&& apt-get {verb} " in line)
        for verb in ("upgrade", "install")
    )
    lock_check = "    && python -c 'import glob"
    leftover_check = re.compile(r" +&& python -c 'import itertools.*?' \\\n", re.S)
    leftover = leftover_check.search(dockerfile)
    assert leftover is not None
    if defect == "unpinned":
        broken = dockerfile.replace(GIT_PACKAGE, "git")
    elif defect == "wildcard":
        broken = dockerfile.replace(declaration, declaration.rsplit("-", 1)[0] + "-*")
    elif defect == "declared-twice":
        broken = dockerfile.replace(declaration, f"{declaration}\n{declaration}")
    elif defect == "install-twice":
        broken = dockerfile + "RUN apt-get install --no-install-recommends curl\n"
    elif defect == "recommends":
        # The install's: the last of the two.
        head, _, tail = dockerfile.rpartition(" --no-install-recommends")
        broken = head + tail
    elif defect == "second-package":
        broken = dockerfile.replace(GIT_PACKAGE, f"{GIT_PACKAGE} curl")
    elif defect == "lists-kept":
        broken = dockerfile.replace(f" \\\n    && {GIT_LISTS}", "")
    elif defect == "upgrade-gone":
        broken = dockerfile.replace(upgrade, "")
    elif defect == "upgrade-dist":
        broken = dockerfile.replace(upgrade, upgrade.replace("get upgrade", "get dist-upgrade"))
    elif defect == "upgrade-full":
        broken = dockerfile.replace(upgrade, upgrade.replace("get upgrade", "get full-upgrade"))
    elif defect == "upgrade-new-pkgs":
        broken = dockerfile.replace(upgrade, upgrade.replace("upgrade", "upgrade --with-new-pkgs"))
    elif defect == "upgrade-allow-new":
        extra = " -o APT::Get::Upgrade-Allow-New=true \\\n"
        broken = dockerfile.replace(upgrade, upgrade.replace(" \\\n", extra))
    elif defect == "upgrade-extra-word":
        broken = dockerfile.replace(upgrade, upgrade.replace(" \\\n", " curl \\\n"))
    elif defect == "upgrade-recommends":
        broken = dockerfile.replace(upgrade, upgrade.replace(" --no-install-recommends", ""))
    elif defect == "upgrade-prompts":
        broken = dockerfile.replace(upgrade, upgrade.replace(" --yes", ""))
    elif defect == "upgrade-retries":
        broken = dockerfile.replace(upgrade, upgrade.replace(" -o Acquire::Retries=10", ""))
    elif defect == "upgrade-after-install":
        broken = dockerfile.replace(upgrade, "").replace(install, install + upgrade)
    elif defect == "upgrade-before-lock-check":
        broken = dockerfile.replace(upgrade, "").replace(lock_check, upgrade + lock_check)
    elif defect == "leftover-gone":
        broken = dockerfile.replace(leftover[0], "")
    elif defect == "leftover-before-install":
        broken = dockerfile.replace(leftover[0], "").replace(install, leftover[0] + install)
    elif defect == "leftover-inst-gone":
        broken = re.sub(r"^( +)left = .*$", r"\1left = []; \\", dockerfile, flags=re.M)
    elif defect == "leftover-kept-gone":
        # The heading is found on one line and its names read on the next: both go.
        broken = re.sub(
            r"^ +heading = .*\n( +)kept = .*$", r"\1kept = []; \\", dockerfile, flags=re.M
        )
    elif defect == "leftover-unserved-gone":
        broken = re.sub(r"^( +)local = .*$", r"\1local = []; \\", dockerfile, flags=re.M)
    elif defect == "apt-elsewhere":
        broken = dockerfile.replace(base_from[0], base_from[0] + "RUN apt-get clean\n")
    elif defect == "sources-not-copied":
        # After the RUN that installs, so it is still copied once, and not where apt reads it.
        broken = dockerfile.replace(SOURCES_COPY + "\n", "") + SOURCES_COPY + "\n"
    elif defect == "lock-not-copied":
        broken = dockerfile.replace(LOCK_COPY + "\n", "") + LOCK_COPY + "\n"
    elif defect == "sources-copied-twice":
        copy = "COPY deployments/docker/debian.sources /tmp/debian.sources\n"
        broken = dockerfile.replace(base_from[0], base_from[0] + copy)
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
        # A RUN of its own: on the RUN of the install, the mount would also hide the check that
        # begins it.
        broken = dockerfile + "RUN --mount=type=bind,target=/mnt true\n"
    assert broken != dockerfile
    found_only(git_problems(broken), GIT_FOUND[defect])


# --- GORA's own base image: Debian, Python and git, and nothing of GORA --------------------

APT_OPTION = re.compile(r"-o (Acquire::[A-Za-z:]+)=(\d+)")
BASE_INSTRUCTIONS = frozenset({"FROM", "ARG", "RUN", "COPY", "LABEL"})
BASE_COPIES = (SOURCES_COPY, LOCK_COPY)
SOURCE_LABEL = 'org.opencontainers.image.source="https://github.com/gora-tools/gora"'
SNAPSHOT_LABELS = (
    ("DEBIAN_SNAPSHOT", "debian", "tools.gora.debian-snapshot"),
    ("DEBIAN_SECURITY_SNAPSHOT", "debian-security", "tools.gora.debian-security-snapshot"),
)


def _base() -> str:
    return BASE_DOCKERFILE.read_text(encoding="utf-8")


def patience_problems(dockerfile: str) -> list[str]:
    """Each apt-get command (the update, the upgrade and the install) retries a failed download
    at least ten times and times out an HTTP and an HTTPS connection: the snapshot answered 503
    and 504 for two days. Here only: the base is built rarely."""
    commands = [
        command.strip()
        for run in run_instructions(dockerfile)
        for command in run.removeprefix("RUN ").split("&&")
        if command.strip().startswith("apt-get ")
    ]
    if len(commands) != 3:
        return [f"{len(commands)} apt-get commands, not the update, the upgrade and the install"]
    bad = []
    for command in commands:
        options = {key: int(value) for key, value in APT_OPTION.findall(command)}
        name = " ".join(command.split(" ")[:2])
        if options.get("Acquire::Retries", 0) < 10:
            bad.append(f"{name}: Acquire::Retries is {options.get('Acquire::Retries')}, not 10+")
        bad += [
            f"{name}: {key} is not set"
            for key in ("Acquire::http::Timeout", "Acquire::https::Timeout")
            if options.get(key, 0) <= 0
        ]
    return bad


def test_apt_is_patient_in_the_base_image_and_only_there() -> None:
    assert patience_problems(_base()) == []
    # The release image is built on every change: it does not wait ten times.
    release = RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    assert "Acquire::Retries=10" not in release


@pytest.mark.parametrize(
    ("defect", "found"),
    [
        ("retries-gone", "apt-get update: Acquire::Retries is None"),
        ("retries-low", "apt-get install: Acquire::Retries is 9"),
        ("upgrade-retries-low", "apt-get upgrade: Acquire::Retries is 9"),
        ("http-timeout-gone", "apt-get update: Acquire::http::Timeout is not set"),
        ("https-timeout-zero", "apt-get install: Acquire::https::Timeout is not set"),
    ],
)
def test_an_apt_that_gives_up_early_is_found(defect: str, found: str) -> None:
    """Each defect is made on the real file, in one of the two commands."""
    dockerfile = _base()
    if defect == "retries-gone":
        broken = dockerfile.replace(" -o Acquire::Retries=10", "", 1)
    elif defect == "retries-low":
        head, _, tail = dockerfile.rpartition("Acquire::Retries=10")
        broken = f"{head}Acquire::Retries=9{tail}"
    elif defect == "upgrade-retries-low":
        head, sep, tail = dockerfile.partition("&& apt-get upgrade ")
        broken = head + sep + tail.replace("Acquire::Retries=10", "Acquire::Retries=9", 1)
    elif defect == "http-timeout-gone":
        broken = dockerfile.replace(" -o Acquire::http::Timeout=60", "", 1)
    else:
        head, _, tail = dockerfile.rpartition("Acquire::https::Timeout=60")
        broken = f"{head}Acquire::https::Timeout=0{tail}"
    assert broken != dockerfile
    found_only(patience_problems(broken), found)


def scope_problems(dockerfile: str) -> list[str]:
    """The base image holds Debian, Python and git: one python base, the two files of the
    snapshot, and no user, no entrypoint, no uv and no GORA code."""
    bad = []
    for line in dockerfile.replace("\\\n", " ").splitlines():
        instruction = line.split(" ", 1)[0]
        if not line or line.startswith("#"):
            continue
        if instruction not in BASE_INSTRUCTIONS:
            bad.append(f"{instruction} is no instruction of the base image")
        if instruction == "COPY" and line not in BASE_COPIES:
            bad.append(f"the base image copies more than the snapshot: {line}")
        if instruction == "FROM" and not line.startswith("FROM python:"):
            bad.append(f"the base image starts from more than Python: {line}")
        if instruction == "RUN" and re.search(r"\buv\b|\bpip\b", line):
            bad.append(f"the base image installs more than git: {line[:60]}")
    return bad


def test_the_base_image_holds_debian_python_and_git_and_nothing_of_gora() -> None:
    assert scope_problems(_base()) == []


@pytest.mark.parametrize(
    ("defect", "found"),
    [
        ("user", "USER is no instruction"),
        ("entrypoint", "ENTRYPOINT is no instruction"),
        ("gora-code", "the base image copies more than the snapshot: COPY src/ src/"),
        ("uv", "the base image starts from more than Python: FROM ghcr.io/astral-sh/uv:"),
        ("pip", "the base image installs more than git: "),
    ],
)
def test_a_base_image_that_holds_more_than_git_is_found(defect: str, found: str) -> None:
    dockerfile = _base()
    broken = (
        dockerfile
        + {
            "user": "USER 10001:10001\n",
            "entrypoint": 'ENTRYPOINT ["/app/.venv/bin/gora"]\n',
            "gora-code": "COPY src/ src/\n",
            "uv": f"FROM ghcr.io/astral-sh/uv:0.13.7@sha256:{'a' * 64} AS uv\n",
            "pip": "RUN pip install uv\n",
        }[defect]
    )
    (problem,) = scope_problems(broken)
    assert problem.startswith(found)


def label_problems(dockerfile: str, sources: str) -> list[str]:
    """The image says where it comes from and which two imports of the Debian archive it was
    built from: the same ids debian.sources names, which the ARGs repeat for the labels."""
    named = archive_stanzas(sources)
    bad = []
    lines = dockerfile.replace("\\\n", " ").splitlines()
    for arg, archive, key in SNAPSHOT_LABELS:
        match = ARCHIVE_URI.fullmatch(named.get(archive, {}).get("URIs", ""))
        want = match["id"] if match else None
        declared = [line[len(f"ARG {arg}=") :] for line in lines if line.startswith(f"ARG {arg}=")]
        if declared != [want]:
            bad.append(f"ARG {arg} is {declared}, debian.sources names {want}")
        if f"{key}=${arg}" not in " ".join(line for line in lines if line.startswith("LABEL ")):
            bad.append(f"no label {key}=${arg}")
    if SOURCE_LABEL not in " ".join(line for line in lines if line.startswith("LABEL ")):
        bad.append(f"no label {SOURCE_LABEL}")
    return bad


def test_the_base_image_carries_its_source_and_the_snapshots_it_was_built_from() -> None:
    assert label_problems(_base(), DEBIAN_SOURCES.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("defect", ["snapshot-moved", "security-moved", "label-gone", "no-source"])
def test_a_label_that_misses_the_source_or_the_snapshot_is_found(defect: str) -> None:
    """Each defect is made on the real files, around the ids they hold."""
    dockerfile, sources = _base(), DEBIAN_SOURCES.read_text(encoding="utf-8")
    ids = {}
    for archive in ("debian", "debian-security"):
        match = ARCHIVE_URI.fullmatch(archive_stanzas(sources)[archive]["URIs"])
        assert match is not None
        ids[archive] = match["id"]
    if defect == "snapshot-moved":
        sources = sources.replace(ids["debian"], "20300101T000000Z")
        found = f"ARG DEBIAN_SNAPSHOT is ['{ids['debian']}'], debian.sources names 20300101T000000Z"
    elif defect == "security-moved":
        sources = sources.replace(ids["debian-security"], "20300101T000000Z")
        found = "ARG DEBIAN_SECURITY_SNAPSHOT is "
    elif defect == "label-gone":
        dockerfile = dockerfile.replace(
            " \\\n      tools.gora.debian-snapshot=$DEBIAN_SNAPSHOT", ""
        )
        found = "no label tools.gora.debian-snapshot=$DEBIAN_SNAPSHOT"
    else:
        dockerfile = dockerfile.replace(SOURCE_LABEL, 'org.opencontainers.image.vendor="x"')
        found = "no label org.opencontainers.image.source="
    assert (dockerfile, sources) != (_base(), DEBIAN_SOURCES.read_text(encoding="utf-8"))
    found_only(label_problems(dockerfile, sources), found)


# --- the release image starts from GORA's base image, and only the base installs a package ---

GORA_BASE = re.compile(r"ghcr\.io/gora-tools/gora-base@sha256:[0-9a-f]{64}")


def release_problems(dockerfile: str) -> list[str]:
    """The stages the pushed image is built from (``pushed_part``) have no FROM but the uv
    stage and GORA's base image, named by the digest of its index and no tag, and at least one
    FROM of the base: no stage of them starts from another. The stage test starts from runtime,
    which ``stage_problems`` holds. In the whole file, no RUN runs apt or dpkg, and nothing
    declares the base's PYTHON_VERSION or GIT_VERSION or copies a Debian file, which
    base.Dockerfile does alone; the pair the release declares for itself is BASE_PYTHON_VERSION
    and BASE_GIT_VERSION (``shipped_problems``). A build of it reaches the registry of the base
    and PyPI, and never Docker Hub or the Debian archive."""
    bad = []
    bases = 0
    for line in pushed_part(dockerfile).splitlines():
        if not line.startswith("FROM"):
            continue
        match = FROM.match(line)
        if match is None:
            bad.append(f"a FROM this check does not read: {line}")
        elif match["stage"] == "uv" and match["image"].startswith(UV_IMAGE):
            continue
        elif GORA_BASE.fullmatch(match["image"]) is None:
            bad.append(f"FROM {match['image']} is not GORA's base image by digest")
        else:
            bases += 1
    if bases == 0:
        bad.append("no FROM starts from GORA's base image")
    bad += [
        f"RUN runs apt or dpkg: {run}" for run in run_instructions(dockerfile) if APT.search(run)
    ]
    lines = dockerfile.splitlines()
    for prefix in ("ARG PYTHON_VERSION=", GIT_PREFIX):
        bad += [
            f"{line} is declared in base.Dockerfile alone"
            for line in lines
            if line.startswith(prefix)
        ]
    bad += [
        f"{line} copies a Debian file, which only base.Dockerfile uses"
        for line in lines
        if line.startswith("COPY") and "deployments/docker/debian." in line
    ]
    return bad


def apt_steps(dockerfiles: dict[str, str]) -> list[str]:
    """The names of the Dockerfiles with a RUN that runs apt or dpkg."""
    return sorted(
        name
        for name, text in dockerfiles.items()
        if any(APT.search(run) for run in run_instructions(text))
    )


def _release() -> str:
    return RELEASE_DOCKERFILE.read_text(encoding="utf-8")


def test_the_release_image_starts_from_gora_base_and_installs_no_package() -> None:
    assert release_problems(_release()) == []
    assert len(GORA_BASE.findall(_release())) == 2  # the build stage and the runtime stage


def test_only_the_base_image_has_an_apt_step() -> None:
    files = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in DOCKERFILES}
    assert len(files) >= 3
    assert apt_steps(files) == ["deployments/docker/base.Dockerfile"]


@pytest.mark.parametrize(
    ("defect", "found"),
    [
        ("apt-get", "RUN runs apt or dpkg: RUN apt-get install --yes curl"),
        ("docker-hub-from", "FROM python:3.12-slim-trixie@sha256:"),
        ("base-by-tag", "FROM ghcr.io/gora-tools/gora-base:latest is not GORA's base image"),
        ("other-registry", "FROM ghcr.io/other/gora-base@sha256:"),
        ("python-declared", "ARG PYTHON_VERSION=3.12.15 is declared in base.Dockerfile alone"),
        ("git-declared", "ARG GIT_VERSION=1:2.47.3-1 is declared in base.Dockerfile alone"),
        ("debian-copied", "copies a Debian file"),
        ("runtime-from-build", "FROM build is not GORA's base image by digest"),
        ("runtime-from-uv", "FROM uv is not GORA's base image by digest"),
    ],
)
def test_a_release_image_that_installs_a_package_or_starts_elsewhere_is_found(
    defect: str, found: str
) -> None:
    """Each defect is made on the real file, around the base it names."""
    dockerfile = _release()
    base = GORA_BASE.search(dockerfile)
    assert base is not None
    if defect == "apt-get":
        broken = dockerfile + "RUN apt-get install --yes curl\n"
    elif defect == "docker-hub-from":
        # The build stage only: the runtime stage still starts from the base.
        broken = dockerfile.replace(base[0], f"python:3.12-slim-trixie@sha256:{'a' * 64}", 1)
    elif defect == "base-by-tag":
        broken = dockerfile.replace(base[0], "ghcr.io/gora-tools/gora-base:latest", 1)
    elif defect == "other-registry":
        broken = dockerfile.replace(base[0], base[0].replace("gora-tools", "other"), 1)
    elif defect == "python-declared":
        broken = dockerfile + "ARG PYTHON_VERSION=3.12.15\n"
    elif defect == "git-declared":
        broken = dockerfile + "ARG GIT_VERSION=1:2.47.3-1\n"
    elif defect == "runtime-from-build":
        # A stage name, not an image: the pushed image would carry the build stage's tools.
        broken = dockerfile.replace(f"FROM {base[0]} AS runtime\n", "FROM build AS runtime\n")
    elif defect == "runtime-from-uv":
        broken = dockerfile.replace(f"FROM {base[0]} AS runtime\n", "FROM uv AS runtime\n")
    else:
        broken = dockerfile + "COPY deployments/docker/debian.lock /etc/apt/debian.lock\n"
    assert broken != dockerfile
    found_only(release_problems(broken), found)


@pytest.mark.parametrize("path", ["gate-images.Dockerfile", "Dockerfile"])
def test_an_apt_step_outside_the_base_image_is_found(path: str) -> None:
    files = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in DOCKERFILES}
    name = f"deployments/docker/{path}"
    files[name] += "RUN apt-get install --yes curl\n"
    assert apt_steps(files) == sorted([name, "deployments/docker/base.Dockerfile"])


# --- the release image declares the Python and the git it ships, beside the base digest --------

SHIPPED = (
    # The argument, what it holds, and the label that carries it into the image.
    ("BASE_PYTHON_VERSION", re.compile(r"\d+\.\d+\.\d+"), "tools.gora.python-version"),
    ("BASE_GIT_VERSION", DEBIAN_VERSION, "tools.gora.git-version"),
)


def shipped_problems(dockerfile: str) -> list[str]:
    """The release Dockerfile declares what the image ships of its base, beside the base
    digest it starts from: each ARG BASE_* once, in the stage the image is pushed from, as one
    exact version, with the image labelled with both after them. base.Dockerfile declares its
    own pair, PYTHON_VERSION and GIT_VERSION, for the build of the base. No check holds the two
    pairs equal: they differ between the pull request that moves the base and the one that
    moves the digest, and a check that held them equal would fail the first for ever."""
    stages = {stage.name: stage for stage in _stages(dockerfile)}
    runtime = stages.get(PUSHED_TARGET)
    if runtime is None:
        return [f"the Dockerfile has no stage {PUSHED_TARGET}"]
    bad = []
    for name, exact, key in SHIPPED:
        prefix = f"ARG {name}="
        found = [
            (stage.name, instruction)
            for stage in stages.values()
            for instruction in stage.instructions
            if instruction.startswith(prefix)
        ]
        if len(found) != 1:
            bad.append(f"{prefix} is declared {len(found)} times, not once")
        elif found[0][0] != PUSHED_TARGET:
            bad.append(f"{found[0][1]} is not in the stage {PUSHED_TARGET}")
        elif exact.fullmatch(found[0][1][len(prefix) :]) is None:
            bad.append(f"{found[0][1]} is not one exact version")
        after = next((n for n, i in enumerate(runtime.instructions) if i.startswith(prefix)), -1)
        labels = [
            word
            for instruction in runtime.instructions[after + 1 :]
            if instruction.startswith("LABEL ")
            for word in instruction.split()
        ]
        if f"{key}=${name}" not in labels:
            bad.append(f"no label {key}=${name} after {prefix} in the stage {PUSHED_TARGET}")
    return bad


def test_the_release_image_declares_what_it_ships_beside_its_base() -> None:
    assert shipped_problems(_release()) == []


SHIPPED_FOUND = {
    "python-twice": ("ARG BASE_PYTHON_VERSION= is declared 2 times, not once",),
    "python-not-exact": ("ARG BASE_PYTHON_VERSION=3.12 is not one exact version",),
    "git-not-exact": ("-* is not one exact version",),
    "python-in-the-build-stage": ("is not in the stage runtime",),
    "label-missing": (
        "no label tools.gora.git-version=$BASE_GIT_VERSION after ARG BASE_GIT_VERSION= in",
    ),
    "label-before-the-arguments": (
        "no label tools.gora.python-version=$BASE_PYTHON_VERSION after",
        "no label tools.gora.git-version=$BASE_GIT_VERSION after",
    ),
}


@pytest.mark.parametrize("defect", list(SHIPPED_FOUND))
def test_a_release_image_that_does_not_declare_what_it_ships_is_found(defect: str) -> None:
    """Each defect is made on the real file, around the values it holds."""
    dockerfile = _release()
    (python,) = re.findall(r"^ARG BASE_PYTHON_VERSION=.*$", dockerfile, re.M)
    (git,) = re.findall(r"^ARG BASE_GIT_VERSION=.*$", dockerfile, re.M)
    label = re.search(r"^LABEL tools\.gora\.python-version=.*\\\n.*\n", dockerfile, re.M)
    build = re.search(r"^FROM .* AS build\n", dockerfile, re.M)
    assert label is not None and build is not None
    if defect == "python-twice":
        broken = dockerfile.replace(f"{python}\n", f"{python}\n{python}\n")
    elif defect == "python-not-exact":
        broken = dockerfile.replace(python, python.rsplit(".", 1)[0])
    elif defect == "git-not-exact":
        broken = dockerfile.replace(git, git.rsplit("-", 1)[0] + "-*")
    elif defect == "python-in-the-build-stage":
        broken = dockerfile.replace(f"{python}\n", "").replace(build[0], f"{build[0]}{python}\n")
    elif defect == "label-missing":
        broken = dockerfile.replace(" \\\n      tools.gora.git-version=$BASE_GIT_VERSION", "")
    else:
        arguments = f"{python}\n{git}\n"
        assert arguments in dockerfile
        broken = dockerfile.replace(label[0], "").replace(arguments, label[0] + arguments)
    assert broken != dockerfile
    found_only(shipped_problems(broken), *SHIPPED_FOUND[defect])


def makefile_problems(makefile: str) -> list[str]:
    """The Makefile reads the Python and the git the release image ships where the release
    Dockerfile declares them: RELEASE_PYTHON through scripts/gate/versions.sh, RELEASE_GIT from
    its ARG BASE_GIT_VERSION. No line of it but a comment names base.Dockerfile, whose pair is
    the base build's."""
    lines = makefile.splitlines()
    python = [line for line in lines if line.startswith("RELEASE_PYTHON =")]
    git = [line for line in lines if line.startswith("RELEASE_GIT =")]
    bad = []
    if len(python) != 1 or "shell scripts/gate/versions.sh" not in python[0]:
        bad.append("RELEASE_PYTHON does not read scripts/gate/versions.sh")
    if len(git) != 1 or "'s/^ARG BASE_GIT_VERSION=//p' deployments/docker/Dockerfile" not in git[0]:
        bad.append("RELEASE_GIT does not read ARG BASE_GIT_VERSION of the release Dockerfile")
    bad += [
        f"the Makefile reads base.Dockerfile: {line.strip()[:60]}"
        for line in lines
        if "base.Dockerfile" in line and not line.lstrip().startswith("#")
    ]
    return bad


def test_the_makefile_reads_what_the_release_ships_where_the_release_declares_it() -> None:
    assert makefile_problems(MAKEFILE.read_text(encoding="utf-8")) == []


MAKEFILE_FOUND = {
    "git-from-the-base": (
        "RELEASE_GIT does not read ARG BASE_GIT_VERSION",
        "the Makefile reads base.Dockerfile: RELEASE_GIT",
    ),
    "message-names-the-base": ("the Makefile reads base.Dockerfile: @echo",),
    "python-not-from-versions-sh": ("RELEASE_PYTHON does not read scripts/gate/versions.sh",),
}


@pytest.mark.parametrize("defect", list(MAKEFILE_FOUND))
def test_a_makefile_that_reads_the_base_again_is_found(defect: str) -> None:
    """Each defect is made on the real file."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    if defect == "git-from-the-base":
        was = "s/^ARG BASE_GIT_VERSION=//p' deployments/docker/Dockerfile"
        now = "s/^ARG GIT_VERSION=//p' deployments/docker/base.Dockerfile"
    elif defect == "message-names-the-base":
        was = "(BASE_PYTHON_VERSION of deployments/docker/Dockerfile)"
        now = "(deployments/docker/base.Dockerfile)"
    else:
        was, now = "shell scripts/gate/versions.sh", "shell scripts/gate/probe.sh"
    assert makefile.count(was) == 1
    found_only(makefile_problems(makefile.replace(was, now)), *MAKEFILE_FOUND[defect])


# --- the git tests run in the stage test, and the pushed image is the stage runtime -------------

TEST_STAGE = "test"
BUILD_PUSH = "docker/build-push-action@"
BUILD_IMAGE_ACTION = ROOT / ".github" / "actions" / "build-image" / "action.yml"
# What a module of the suite does that runs the product against git: it runs the real git
# in the environment the differential tests share, or builds the adapter. A module that uses
# git to drive a script of the gate (test_red_first.py) runs no product. This file names the
# patterns in order to find them, and is the one module left out.
RUNS_GIT = re.compile(r"hermetic_git_env|GitRepository\(")


def git_test_modules() -> list[str]:
    """Every module of the suite that runs the product against the git on PATH, derived from
    the sources, so that a new one cannot be left out of the stage."""
    here = Path(__file__).resolve()
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("test_*.py")
        if path.resolve() != here and RUNS_GIT.search(path.read_text(encoding="utf-8"))
    )


def stage_modules(dockerfile: str) -> list[str]:
    """The test modules the stage test names in its pytest run, in the order it names them."""
    stage = next(stage for stage in _stages(dockerfile) if stage.name == TEST_STAGE)
    runs = [i for i in stage.instructions if i.split()[0].upper() == "RUN" and "-m pytest" in i]
    return [word for run in runs for word in run.split() if word.endswith(".py")]


def stage_problems(dockerfile: str, modules: list[str]) -> list[str]:
    """The stage test starts from the stage runtime and changes no user, so it has the image's
    git, Python and user; it runs pytest once, on exactly ``modules``, each by its path."""
    stages = {stage.name: stage for stage in _stages(dockerfile)}
    stage = stages.get(TEST_STAGE)
    if PUSHED_TARGET not in stages or stage is None:
        return [f"the Dockerfile has no stage {PUSHED_TARGET} or no stage {TEST_STAGE}"]
    bad = []
    if stage.base != PUSHED_TARGET:
        bad.append(f"the stage {TEST_STAGE} starts from {stage.base}, not {PUSHED_TARGET}")
    bad += [
        f"the stage {TEST_STAGE} changes the user: {instruction}"
        for instruction in stage.instructions
        if instruction.split()[0].upper() == "USER"
    ]
    runs = [i for i in stage.instructions if i.split()[0].upper() == "RUN" and "-m pytest" in i]
    if len(runs) != 1:
        return [*bad, f"the stage {TEST_STAGE} runs pytest {len(runs)} times, not once"]
    listed = [word for word in runs[0].split() if word.endswith(".py")]
    bad += [
        f"{name} runs git and the stage {TEST_STAGE} does not run it"
        for name in sorted(set(modules) - set(listed))
    ]
    bad += [
        f"the stage {TEST_STAGE} runs {name}, which runs no git"
        for name in sorted(set(listed) - set(modules))
    ]
    bad += [f"{name} is run twice" for name in sorted({n for n in listed if listed.count(n) > 1})]
    return bad


def test_the_test_stage_runs_every_module_that_runs_git() -> None:
    assert stage_problems(_release(), git_test_modules()) == []


def test_the_modules_that_run_git_are_found_at_all() -> None:
    """The derivation finds the four modules the stage was made for, and not this one. A floor,
    not the list: the stage's list lives in the Dockerfile alone, and the check above holds it
    to what the derivation finds, so a module added later fails there and not twice."""
    found = git_test_modules()
    assert {
        "tests/integration/test_git_adapter.py",
        "tests/integration/test_identifiers_against_git.py",
        "tests/integration/test_input_globs_against_git.py",
        "tests/integration/test_tag_message_against_git.py",
    } <= set(found)
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() not in found


@pytest.mark.parametrize(
    "defect",
    [
        "new-module",
        "module-dropped",
        "module-twice",
        "module-without-git",
        "not-from-runtime",
        "no-test-stage",
        "user-changed",
        "no-pytest",
        "pytest-twice",
    ],
)
def test_a_test_stage_that_misses_a_module_or_leaves_the_image_is_found(defect: str) -> None:
    """Each defect is made on the real file, around the modules and stages it holds: the
    modules the stage runs are the baseline, so that a module the tree has and the stage lacks
    is found by the check above and by it alone."""
    dockerfile = _release()
    modules = stage_modules(dockerfile)
    adapter, identifiers = "tests/integration/test_git_adapter.py", "test_identifiers_against_git"
    stage = "FROM runtime AS test\n"
    found = {
        "new-module": "tests/integration/test_x_against_git.py runs git and the stage test does",
        "module-dropped": f"tests/integration/{identifiers}.py runs git and the stage test does",
        "module-twice": f"{adapter} is run twice",
        "module-without-git": f"the stage test runs {adapter}, which runs no git",
        "not-from-runtime": "the stage test starts from build, not runtime",
        "no-test-stage": "the Dockerfile has no stage runtime or no stage test",
        "user-changed": "the stage test changes the user: USER root",
        "no-pytest": "the stage test runs pytest 0 times, not once",
        "pytest-twice": "the stage test runs pytest 2 times, not once",
    }[defect]
    if defect == "new-module":
        modules = [*modules, "tests/integration/test_x_against_git.py"]
    elif defect == "module-dropped":
        dockerfile = dockerfile.replace(f"        tests/integration/{identifiers}.py \\\n", "")
    elif defect == "module-twice":
        line = f"        {adapter} \\\n"
        dockerfile = dockerfile.replace(line, line + line)
    elif defect == "module-without-git":
        modules = [name for name in modules if name != adapter]
    elif defect == "not-from-runtime":
        dockerfile = dockerfile.replace(stage, "FROM build AS test\n")
    elif defect == "no-test-stage":
        dockerfile = dockerfile.replace(stage, "FROM runtime AS other\n")
    elif defect == "user-changed":
        dockerfile = dockerfile.replace(stage, stage + "USER root\n")
    elif defect == "no-pytest":
        dockerfile = dockerfile.replace("-m pytest", "-m pip")
    else:
        dockerfile += "RUN /app/.venv/bin/python -m pytest tests/unit\n"
    assert (dockerfile, modules) != (_release(), stage_modules(_release()))
    found_only(stage_problems(dockerfile, modules), found)


def target_problems(action: dict[Any, Any], makefile: str) -> list[str]:
    """The pushed image is the stage runtime, by name, in the build action (CI and the release
    share it) and in ``make docker-build``; the stage test is built before it, with no cache,
    and exported nowhere. A last stage left to be the default would push the tests."""
    steps = [s for s in _steps(action) if str(s.get("uses", "")).startswith(BUILD_PUSH)]
    if len(steps) != 2:
        return [f"the action has {len(steps)} builds, not the test build and the push"]
    test, push = (step.get("with") or {} for step in steps)
    bad = []
    if test.get("target") != TEST_STAGE:
        bad.append(f"the first build of the action is target {test.get('target')}, not test")
    if push.get("target") != PUSHED_TARGET:
        bad.append(f"the push of the action is target {push.get('target')}, not runtime")
    for name, given in (("test", test), ("push", push)):
        if given.get("no-cache") is not True or given.get("pull") is not True:
            bad.append(f"the {name} build of the action is not no-cache and pull")
    if test.get("outputs") != "type=cacheonly":
        bad.append(f"the test build exports {test.get('outputs')}, not type=cacheonly")
    if "push=true" not in str(push.get("outputs")):
        bad.append("the push build of the action does not push")
    recipes = re.findall(r"^docker-build:.*\n(?:\t.*\n)+", makefile, re.MULTILINE)
    targets = re.findall(r"--target (\S+)", recipes[0]) if len(recipes) == 1 else []
    if targets != [TEST_STAGE, PUSHED_TARGET]:
        bad.append(f"make docker-build builds the targets {targets}, not [test, runtime] in order")
    elif "--target test --output type=cacheonly" not in recipes[0]:
        bad.append("make docker-build exports the test build")
    return bad


def _target_inputs() -> tuple[dict[Any, Any], str]:
    return _yaml(BUILD_IMAGE_ACTION), MAKEFILE.read_text(encoding="utf-8")


def test_the_pushed_image_is_the_runtime_stage_everywhere() -> None:
    assert target_problems(*_target_inputs()) == []


@pytest.mark.parametrize(
    "defect",
    [
        "push-target-gone",
        "push-target-test",
        "test-build-gone",
        "test-target-gone",
        "test-build-pushed",
        "test-build-cached",
        "push-build-cached",
        "push-gone",
        "make-target-gone",
        "make-test-gone",
        "make-order",
        "make-test-exported",
    ],
)
def test_a_pushed_target_that_is_not_the_runtime_stage_is_found(defect: str) -> None:
    """Each defect is made on the real files, around the builds they hold."""
    action, makefile = _target_inputs()
    steps = [s for s in _steps(action) if str(s.get("uses", "")).startswith(BUILD_PUSH)]
    test, push = (step["with"] for step in steps)
    found = {
        "push-target-gone": "the push of the action is target None, not runtime",
        "push-target-test": "the push of the action is target test, not runtime",
        "test-build-gone": "the action has 1 builds, not the test build and the push",
        "test-target-gone": "the first build of the action is target None, not test",
        "test-build-pushed": "the test build exports type=image,push=true, not type=cacheonly",
        "test-build-cached": "the test build of the action is not no-cache and pull",
        "push-build-cached": "the push build of the action is not no-cache and pull",
        "push-gone": "the push build of the action does not push",
        "make-target-gone": "make docker-build builds the targets ['test'], not [test, runtime]",
        "make-test-gone": "make docker-build builds the targets ['runtime'], not [test, runtime]",
        "make-order": "make docker-build builds the targets ['runtime', 'test'], not [test,",
        "make-test-exported": "make docker-build exports the test build",
    }[defect]
    if defect == "push-target-gone":
        del push["target"]
    elif defect == "push-target-test":
        push["target"] = TEST_STAGE
    elif defect == "test-build-gone":
        action["runs"]["steps"].remove(steps[0])
    elif defect == "test-target-gone":
        del test["target"]
    elif defect == "test-build-pushed":
        test["outputs"] = "type=image,push=true"
    elif defect == "test-build-cached":
        test["no-cache"] = False
    elif defect == "push-build-cached":
        del push["pull"]
    elif defect == "push-gone":
        push["outputs"] = "type=image,push-by-digest=true"
    elif defect == "make-target-gone":
        makefile = makefile.replace("--target runtime ", "")
    elif defect == "make-test-gone":
        makefile = makefile.replace("--target test --output type=cacheonly ", "")
    elif defect == "make-order":
        makefile = makefile.replace("--target test", "--target TMP", 1)
        makefile = makefile.replace("--target runtime", "--target test", 1)
        makefile = makefile.replace("--target TMP", "--target runtime", 1)
    else:
        makefile = makefile.replace("--target test --output type=cacheonly", "--target test")
    assert (action, makefile) != _target_inputs()
    found_only(target_problems(action, makefile), found)


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
    ("document", "found"),
    [
        ({"runs": {"steps": [{"uses": "x", "with": {"version": "latest"}}]}}, "x version=latest"),
        (
            {"runs": {"steps": [{"uses": "x", "with": {"cosign-release": "v2"}}]}},
            "x cosign-release=v2",
        ),
        ({"env": {"SYFT_VERSION": "latest"}, "jobs": {}}, "env SYFT_VERSION=latest"),
        (
            # A variable not named *_VERSION, so only the input that reads it is found.
            {
                "env": {"COSIGN": "main"},
                "jobs": {"a": {"steps": [{"with": {"cosign-release": "${{ env.COSIGN }}"}}]}},
            },
            "None cosign-release=${{ env.COSIGN }}",
        ),
    ],
    ids=["document0", "document1", "document2", "document3"],
)
def test_a_tool_at_latest_or_a_range_is_found(document: dict[Any, Any], found: str) -> None:
    assert unpinned_tools(document) == [found]


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
    release workflow, the base image's workflow and the two actions CI shares with the release):
    it is the same everywhere."""
    found = {row for text in texts.values() for row in buildx_settings(text)}
    return [f"setup-buildx is set {len(found)} ways: {sorted(found)}"] if len(found) > 1 else []


def _buildx_texts() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in WORKFLOWS + ACTIONS
    }


def test_buildx_is_set_up_the_same_way_in_every_file() -> None:
    """One action pin, one buildx version, no binary cache: written in release.yml, in
    own-image.yml, in assemble-index and in build-image, and equal."""
    assert buildx_problems(_buildx_texts()) == []


def test_the_buildx_steps_are_found_at_all() -> None:
    held = {path: buildx_settings(text) for path, text in _buildx_texts().items()}
    assert sorted(path for path, rows in held.items() if rows) == [
        ".github/actions/assemble-index/action.yml",
        ".github/actions/build-image/action.yml",
        ".github/workflows/own-image.yml",
        ".github/workflows/release.yml",
    ]
    assert all(len(rows) == 1 for rows in held.values() if rows)


BUILDX_FOUND = {
    "version": "'v0.0.1', 'False')",
    "pin": f"@{'0' * 40}'",
    "pin-comment": "', 'v0.0.1', 'v",
    "cache-binary": "'True')",
    "no-version": "'None', 'False')",
}


@pytest.mark.parametrize("defect", ["version", "pin", "pin-comment", "cache-binary", "no-version"])
@pytest.mark.parametrize(
    "path",
    [
        ".github/actions/assemble-index/action.yml",
        ".github/actions/build-image/action.yml",
        ".github/workflows/own-image.yml",
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
    (problem,) = buildx_problems({**texts, path: broken})
    assert problem.startswith("setup-buildx is set 2 ways: ")
    # The way the defect sets it is one of the two.
    assert BUILDX_FOUND[defect] in problem


# --- a pipe fails on its first command -----------------------------------------------------

CI = ROOT / ".github" / "workflows" / "ci.yml"
PIPE = re.compile(r"(?<!\|)\|(?!\|)")
PIPEFAIL = re.compile(r"\bset\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*o\s+pipefail\b")
NOT_A_SHELL = frozenset({"python", "pwsh", "powershell", "cmd"})


def _default_shell(scope: dict[Any, Any]) -> str | None:
    shell = ((scope.get("defaults") or {}).get("run") or {}).get("shell")
    return None if shell is None else str(shell)


def _run_steps(document: dict[Any, Any]) -> list[tuple[str, str | None, dict[str, Any]]]:
    """Each step that runs a command, of a workflow or a composite action, as (job, the shell
    its job or workflow defaults to, step)."""
    if "runs" in document:
        return [("(composite)", None, s) for s in document["runs"].get("steps", []) if "run" in s]
    found = []
    for name, job in document.get("jobs", {}).items():
        shell = _default_shell(job) or _default_shell(document)
        found += [(name, shell, s) for s in job.get("steps", []) if "run" in s]
    return found


def unguarded_pipes(document: dict[Any, Any]) -> list[str]:
    """Each ``run`` step that holds a pipe and runs in a shell that ends the pipe with the status
    of its last command. GitHub's default shell is ``bash -e``; ``shell: bash`` is ``bash -eo
    pipefail``, as is ``set -o pipefail`` in the step. A ``|`` anywhere in the code counts, a
    quoted one too: a step that holds one asks for the shell and loses nothing. Steps in
    another language than a shell, and comment lines, hold no pipe."""
    bad = []
    for job, default, step in _run_steps(document):
        shell = str(step.get("shell") or default or "")
        lines = str(step["run"]).splitlines()
        code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
        if not PIPE.search(code) or shell.split(" ")[0] in NOT_A_SHELL:
            continue
        if shell == "bash" or "pipefail" in shell or PIPEFAIL.search(code):
            continue
        bad.append(f"{job}: {step.get('name') or step.get('id') or lines[0]}")
    return bad


@pytest.mark.parametrize("path", WORKFLOWS + ACTIONS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_a_piped_step_fails_on_its_first_command(path: Path) -> None:
    """versions.sh | tee under bash -e ends with tee's status: versions.sh exiting 2 leaves
    the outputs empty and the step green."""
    assert unguarded_pipes(_yaml(path)) == []


_PIPED = 'scripts/gate/versions.sh | tee -a "$GITHUB_OUTPUT"'


def _job(*steps: dict[str, Any], **job: Any) -> dict[str, Any]:
    return {"a": {"steps": list(steps), **job}}


@pytest.mark.parametrize(
    ("document", "found"),
    [
        ({"jobs": _job({"run": _PIPED})}, f"a: {_PIPED}"),
        ({"jobs": _job({"run": _PIPED, "shell": "sh"})}, f"a: {_PIPED}"),
        ({"jobs": _job({"run": _PIPED, "shell": "bash -e {0}"})}, f"a: {_PIPED}"),
        ({"jobs": _job({"run": "a |& tee b"})}, "a: a |& tee b"),
        ({"jobs": _job({"run": f"# set -o pipefail\n{_PIPED}"})}, "a: # set -o pipefail"),
        ({"jobs": _job({"run": "x=$(a | b)"})}, "a: x=$(a | b)"),
        (
            {"jobs": _job({"run": _PIPED, "shell": "sh"}), "defaults": {"run": {"shell": "bash"}}},
            f"a: {_PIPED}",
        ),
        (
            {"jobs": _job({"run": _PIPED, "shell": "sh"}, defaults={"run": {"shell": "bash"}})},
            f"a: {_PIPED}",
        ),
        ({"runs": {"steps": [{"run": _PIPED, "shell": "sh"}]}}, f"(composite): {_PIPED}"),
    ],
    ids=[
        "default",
        "sh",
        "bash-e",
        "stderr-pipe",
        "pipefail-in-comment",
        "in-substitution",
        "step-over-workflow",
        "step-over-job",
        "composite",
    ],
)
def test_a_pipe_that_ends_with_its_last_command_is_found(
    document: dict[Any, Any], found: str
) -> None:
    assert unguarded_pipes(document) == [found]


@pytest.mark.parametrize(
    "document",
    [
        {"jobs": _job({"run": _PIPED, "shell": "bash"})},
        {"jobs": _job({"run": _PIPED, "shell": "bash -eo pipefail {0}"})},
        {"jobs": _job({"run": f"set -o pipefail\n{_PIPED}"})},
        {"jobs": _job({"run": f"set -euo pipefail\n{_PIPED}"})},
        {"jobs": _job({"run": _PIPED}, defaults={"run": {"shell": "bash"}})},
        {"jobs": _job({"run": _PIPED}), "defaults": {"run": {"shell": "bash"}}},
        {"jobs": _job({"run": "make a || make b"})},
        {"jobs": _job({"run": f"# {_PIPED}\nmake"})},
        {"jobs": _job({"run": "a | b", "shell": "python"})},
        {"runs": {"steps": [{"run": _PIPED, "shell": "bash"}]}},
        {"jobs": _job({"uses": "x"})},
    ],
    ids=[
        "bash",
        "template",
        "set-o",
        "set-euo",
        "job-default",
        "workflow-default",
        "or",
        "comment",
        "python",
        "composite",
        "no-run",
    ],
)
def test_a_pipe_that_fails_on_its_first_command_passes(document: dict[Any, Any]) -> None:
    assert unguarded_pipes(document) == []


@pytest.mark.parametrize(
    ("name", "jobs"),
    [("ci.yml", ["check", "test"]), ("release.yml", ["guard", "verify"])],
    ids=["ci.yml", "release.yml"],
)
def test_a_real_piped_step_that_loses_its_shell_is_found(name: str, jobs: list[str]) -> None:
    """The defect is made on the real file: every ``shell: bash`` line removed. It is found in
    each job whose piped step had it, and nowhere else."""
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    broken = re.sub(r"^ +shell: bash\n", "", text, flags=re.MULTILINE)
    assert broken != text
    assert [p.split(": ")[0] for p in unguarded_pipes(yaml.safe_load(broken))] == jobs


def test_the_versions_step_of_ci_is_the_one_the_tests_model() -> None:
    """_SOURCE stands for the step of the check and test jobs that reads versions.sh."""
    jobs = _yaml(CI)["jobs"]
    for name in ("check", "test"):
        (step,) = [s for s in jobs[name]["steps"] if s.get("id") == "versions"]
        assert {key: step.get(key) for key in _SOURCE} == _SOURCE
    assert unguarded_pipes({"jobs": _job(_SOURCE)}) == []


def test_crane_is_pinned_by_version_and_checksum() -> None:
    script = (ROOT / "scripts" / "gate" / "crane.sh").read_text(encoding="utf-8")
    assert re.search(r"^VERSION=v\d+\.\d+\.\d+$", script, re.MULTILINE)
    assert len(re.findall(r"sum=[0-9a-f]{64} ;;", script)) >= 2  # both runner platforms


# --- GORA's own images: a person runs a caller from main, and the reusable workflow pushes by
# --- digest ---------------------------------------------------------------------------------

BUILD_PUSH = "docker/build-push-action@"
ASSEMBLE = "./.github/actions/assemble-index"
GRANTED = {"contents": "read", "packages": "write"}
PUSH_COMMAND = re.compile(r"\b(?:crane\"|docker)\s+push\s+(?P<args>.*)$", re.M)
# crane v0.22.1 pushing a layout to a repository alone writes the tag latest as well; pushing
# it to a digest writes no tag (measured in a registry). So every push names the digest.
PUSH_TARGET = ' "$IMAGE@$want")'
INDEX_TAG = "run-${{ github.run_id }}-${{ github.run_attempt }}"
NATIVE = {("ubuntu-24.04", "linux/amd64"), ("ubuntu-24.04-arm", "linux/arm64")}


def _needed(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _upstream(jobs: dict[str, dict[str, Any]], name: str) -> set[str]:
    """Every job ``name`` waits for, directly or through another job."""
    found: set[str] = set()
    pending = _needed(jobs[name])
    while pending:
        job = pending.pop()
        if job not in found:
            found.add(job)
            pending += _needed(jobs[job])
    return found


SMOKE_SCRIPT = "scripts/gate/own-image-smoke.sh"


def own_image_problems(text: str) -> list[str]:
    """What own-image.yml must be (ADR-011, amendments 2026-11-05 and 2026-11-07): called and
    never started, with no concurrency group of its own (its caller's would deadlock it);
    refused unless the caller's ref is main, by a guard every job waits for; each platform
    built natively, with no cache, of the Dockerfile its caller names, and smoked by the one
    script on a registry on the runner before one job, the only one that may write packages,
    moves the same images to GHCR by digest; nothing pushed by tag but the index, under a name
    of the run."""
    document = yaml.safe_load(text)
    jobs: dict[str, dict[str, Any]] = document.get("jobs", {})
    bad = []
    # YAML 1.1 reads the key `on` as true.
    trigger = document.get("on", document.get(True))
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_call"}:
        bad.append(f"the trigger is {trigger}, not workflow_call alone")
    if "concurrency" in document:
        bad.append("the workflow has a concurrency group of its own")
    if _needed(jobs.get("guard", {"needs": "?"})):
        bad.append("the guard waits for another job")
    bad += [
        f"{name} does not wait for the guard"
        for name in jobs
        if name != "guard" and "guard" not in _upstream(jobs, name)
    ]
    if document.get("permissions") != {}:
        bad.append(f"the workflow grants {document.get('permissions')}, not nothing")
    for name, job in jobs.items():
        given = job.get("permissions", {})
        bad += [
            f"{name} holds {key}: {value}"
            for key, value in given.items()
            if GRANTED.get(key) != value
        ]
        if name != "publish" and "packages" in given:
            bad.append(f"{name} may write packages")
    env = document.get("env", {})
    if env.get("IMAGE") != "${{ inputs.image }}":
        bad.append(f"IMAGE is {env.get('IMAGE')}, not the image input alone")
    rehearsal = r"localhost:5000/gora-\$\{\{ inputs\.kind \}\}"
    if not re.fullmatch(rehearsal, str(env.get("REHEARSAL"))):
        bad.append(f"REHEARSAL is {env.get('REHEARSAL')}, not a repository on the runner")
    for name, job in jobs.items():
        for step in job.get("steps", []):
            uses, given = str(step.get("uses", "")), step.get("with") or {}
            if uses.startswith(BUILD_PUSH):
                if "push-by-digest=true" not in str(given.get("outputs", "")).split(","):
                    bad.append(f"{name}: the build does not push by digest")
                if "tags" in given:
                    bad.append(f"{name}: the build pushes by tag")
                if given.get("no-cache") is not True or given.get("pull") is not True:
                    bad.append(f"{name}: the build is not pulled and uncached")
                if {"cache-from", "cache-to"} & set(given):
                    bad.append(f"{name}: the build may use a cache")
                if given.get("provenance") != "mode=max":
                    bad.append(f"{name}: the build has no provenance mode=max")
                if given.get("file") != "${{ inputs.dockerfile }}":
                    bad.append(f"{name}: the build is not of the Dockerfile input")
            if uses == ASSEMBLE and given.get("tag") != INDEX_TAG:
                bad.append(f"the index tag is {given.get('tag')}, not one of this run")
            bad += [
                f'{name}: a push to {push["args"]}, not to a digest, "$IMAGE@$want"'
                for push in PUSH_COMMAND.finditer(str(step.get("run", "")))
                if not push["args"].endswith(PUSH_TARGET)
            ]
    build = json.dumps(jobs.get("build", {}))
    if any(reaches in build for reaches in ("env.IMAGE", "$IMAGE", "ghcr.io", "login-action")):
        bad.append("build reaches GHCR before the smoke")
    include = jobs.get("build", {}).get("strategy", {}).get("matrix", {}).get("include", [])
    if {(entry.get("runner"), entry.get("platform")) for entry in include} != NATIVE:
        bad.append("the platforms are not each built on their native runner")
    smokes = [
        step
        for step in jobs.get("build", {}).get("steps", [])
        if SMOKE_SCRIPT in str(step.get("run", ""))
    ]
    if len(smokes) != 1 or not re.fullmatch(
        rf'{re.escape(SMOKE_SCRIPT)} "\$KIND" "\$REHEARSAL@\$DIGEST" "\$PLATFORM"',
        str(smokes[0].get("run", "")).strip() if smokes else "",
    ):
        bad.append(f"build does not run {SMOKE_SCRIPT} on the image it pushed to the runner")
    return bad


def test_own_image_yml_is_called_from_main_and_pushes_by_digest() -> None:
    assert own_image_problems(OWN_WORKFLOW.read_text(encoding="utf-8")) == []


CALLERS = {
    "base.yml": ("base-image", "deployments/docker/base.Dockerfile", "gora-base", "base"),
    "scanner.yml": (
        "scanner-image",
        "deployments/docker/scanner.Dockerfile",
        "gora-scanner",
        "scanner",
    ),
}


def caller_problems(text: str, name: str) -> list[str]:
    """What a caller of own-image.yml is and no more (ADR-011, the entry "the image scan runs from
    GORA's own scanner image, with its database in it"): workflow_dispatch alone; no permission by
    default; a concurrency group of its own, which waits and cancels nothing; and one job, one
    call of the reusable workflow, that grants the ceiling its publish job needs and passes its
    image's Dockerfile, repository and kind."""
    group, dockerfile, repository, kind = CALLERS[name]
    document = yaml.safe_load(text)
    bad = []
    trigger = document.get("on", document.get(True))
    if trigger != {"workflow_dispatch": None}:
        bad.append(f"the trigger is {trigger}, not workflow_dispatch alone")
    if document.get("permissions") != {}:
        bad.append(f"the workflow grants {document.get('permissions')}, not nothing")
    if document.get("concurrency") != {"group": group, "cancel-in-progress": False}:
        bad.append(f"the concurrency is {document.get('concurrency')}, not {group} and waiting")
    # YAML 1.1 reads the key `on` as true.
    keys = {"on" if key is True else str(key) for key in document}
    if keys - {"name", "on", "permissions", "concurrency", "jobs"}:
        bad.append(f"the workflow holds {sorted(keys)}, more than a call")
    jobs = document.get("jobs", {})
    if list(jobs) != ["image"]:
        bad.append(f"the jobs are {list(jobs)}, not one call named image")
        return bad
    job = jobs["image"]
    if set(job) != {"name", "permissions", "uses", "with"}:
        bad.append(f"the call holds {sorted(job)}, not name, permissions, uses and with")
    if job.get("uses") != "./.github/workflows/own-image.yml":
        bad.append(f"the call is of {job.get('uses')}, not own-image.yml")
    if job.get("permissions") != {"contents": "read", "packages": "write"}:
        bad.append(f"the call grants {job.get('permissions')}, not contents read, packages write")
    want = {"dockerfile": dockerfile, "image": f"ghcr.io/gora-tools/{repository}", "kind": kind}
    if job.get("with") != want:
        bad.append(f"the call passes {job.get('with')}, not {want}")
    return bad


@pytest.mark.parametrize("name", list(CALLERS))
def test_each_caller_is_a_dispatch_and_one_call_of_the_reusable_workflow(name: str) -> None:
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    assert caller_problems(text, name) == []


def test_the_callers_groups_differ_from_each_other() -> None:
    """Two images may be built at once; two runs of one image may not."""
    groups = [_yaml(ROOT / ".github" / "workflows" / name)["concurrency"] for name in CALLERS]
    assert len({group["group"] for group in groups}) == 2


def test_the_own_image_jobs_are_found_at_all() -> None:
    jobs = _yaml(OWN_WORKFLOW)["jobs"]
    assert list(jobs) == ["guard", "build", "publish"]
    assert _needed(jobs["build"]) == ["guard"]
    assert _needed(jobs["publish"]) == ["build"]
    assert jobs["guard"]["steps"][0].get("id") == "guard"
    steps = jobs["build"]["steps"]
    names = [step.get("name") for step in steps]
    # The image is smoked before the job that can push it to GHCR has anything to push.
    at = names.index("Smoke the image on this runner")
    assert at < names.index("Save the image as an OCI layout")
    smoke = steps[at]
    assert SMOKE_SCRIPT in smoke["run"]
    # The check of the pushes reads something: one crane push, in the publish job.
    pushes = [
        push["args"]
        for job in jobs.values()
        for step in job["steps"]
        for push in PUSH_COMMAND.finditer(str(step.get("run", "")))
    ]
    assert len(pushes) == 1
    assert pushes[0].endswith(PUSH_TARGET)


def base_guard(ref: str, tmp_path: Path, text: str | None = None) -> tuple[int, str]:
    """The guard step of own-image.yml run as GitHub runs a bash step, for a run started on
    ``ref`` (a called workflow reads its caller's ref)."""
    document = yaml.safe_load(text or OWN_WORKFLOW.read_text(encoding="utf-8"))
    (step,) = [s for s in document["jobs"]["guard"]["steps"] if s.get("id") == "guard"]
    assert step["shell"] == "bash"
    script = tmp_path / "guard.sh"
    script.write_text(step["run"], encoding="utf-8")
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env={"PATH": "/usr/bin:/bin", "GITHUB_REF": ref},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode, done.stdout + done.stderr


def test_the_base_guard_accepts_main(tmp_path: Path) -> None:
    code, output = base_guard("refs/heads/main", tmp_path)
    assert code == 0, output


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/feature",
        "refs/heads/main2",
        "refs/heads/main/x",
        "refs/heads/Main",
        "refs/tags/main",
        "refs/tags/v0.2.0-rc.1",
        "refs/pull/1/merge",
        "main",
        "",
    ],
)
def test_the_base_guard_refuses_any_ref_but_main(ref: str, tmp_path: Path) -> None:
    code, output = base_guard(ref, tmp_path)
    assert code == 1, output
    assert "refs/heads/main" in output


BASE_WORKFLOW_FOUND = {
    "trigger-push": ("the trigger is ",),
    "own-concurrency": ("the workflow has a concurrency group of its own",),
    "image-typed": ("IMAGE is ghcr.io/gora-tools/gora-base, not the image input alone",),
    "smoke-skipped": (f"build does not run {SMOKE_SCRIPT} on the image it pushed",),
    "guard-skipped": ("build does not wait for the guard", "publish does not wait for the guard"),
    "write-everywhere": ("the workflow grants ",),
    "extra-permission": ("publish holds id-token: write",),
    "write-in-build": ("build may write packages",),
    "build-by-tag": ("build: the build pushes by tag",),
    "not-by-digest": ("build: the build does not push by digest",),
    "crane-by-tag": ("publish: a push to ",),
    "crane-to-repository": ("publish: a push to ",),
    "index-tag-moves": ("the index tag is latest, not one of this run",),
    "cached": ("build: the build may use a cache",),
    "to-ghcr-first": ("build reaches GHCR before the smoke",),
    "emulated": ("the platforms are not each built on their native runner",),
    "other-file": ("build: the build is not of the Dockerfile input",),
}


@pytest.mark.parametrize("defect", list(BASE_WORKFLOW_FOUND))
def test_an_own_image_workflow_that_pushes_by_tag_or_runs_early_is_found(defect: str) -> None:
    """Each defect is made on the real file, around the lines it holds."""
    text = OWN_WORKFLOW.read_text(encoding="utf-8")
    if defect == "trigger-push":
        broken = text.replace("on:\n  workflow_call:\n", "on:\n  push:\n  workflow_call:\n")
    elif defect == "own-concurrency":
        broken = text.replace(
            "permissions: {}\n", "permissions: {}\n\nconcurrency:\n  group: x\n", 1
        )
    elif defect == "image-typed":
        broken = text.replace("IMAGE: ${{ inputs.image }}", "IMAGE: ghcr.io/gora-tools/gora-base")
    elif defect == "smoke-skipped":
        broken = text.replace(f'run: {SMOKE_SCRIPT} "$KIND"', 'run: true "$KIND"')
    elif defect == "guard-skipped":
        broken = text.replace("    needs: guard\n", "", 1)
    elif defect == "write-everywhere":
        broken = text.replace("permissions: {}\n", "permissions:\n  packages: write\n", 1)
    elif defect == "extra-permission":
        broken = text.replace(
            "      packages: write #", "      id-token: write\n      packages: write #"
        )
    elif defect == "write-in-build":
        broken = text.replace(
            "    permissions:\n      contents: read\n    steps:",
            "    permissions:\n      contents: read\n      packages: write\n    steps:",
            1,
        )
    elif defect == "build-by-tag":
        broken = text.replace(
            "          sbom: false\n", "          sbom: false\n          tags: x/y:latest\n"
        )
    elif defect == "not-by-digest":
        broken = text.replace("push-by-digest=true,", "")
    elif defect == "crane-by-tag":
        broken = text.replace('"$IMAGE@$want")', '"$IMAGE:latest")')
    elif defect == "crane-to-repository":
        # What the workflow did before: the repository alone, and crane writes latest.
        broken = text.replace('"$IMAGE@$want")', '"$IMAGE")')
    elif defect == "index-tag-moves":
        broken = text.replace(INDEX_TAG, "latest")
    elif defect == "cached":
        broken = text.replace(
            "          no-cache: true\n",
            "          no-cache: true\n          cache-from: type=gha\n",
        )
    elif defect == "to-ghcr-first":
        broken = text.replace("name=${{ env.REHEARSAL }}", "name=${{ env.IMAGE }}")
    elif defect == "emulated":
        broken = text.replace("runner: ubuntu-24.04-arm", "runner: ubuntu-24.04")
    else:
        broken = text.replace(
            "file: ${{ inputs.dockerfile }}", "file: deployments/docker/Dockerfile"
        )
    assert broken != text
    found_only(own_image_problems(broken), *BASE_WORKFLOW_FOUND[defect])


CALLER_FOUND = {
    "trigger-push": ("the trigger is ",),
    "second-trigger-schedule": ("the trigger is ",),
    "write-everywhere": ("the workflow grants ",),
    "group-shared": ("the concurrency is ",),
    "group-cancels": ("the concurrency is ",),
    "second-job": ("the jobs are ",),
    "job-with-steps": ("the call holds ",),
    "job-with-if": ("the call holds ",),
    "secrets-inherited": ("the call holds ",),
    "extra-permission": ("the call grants ",),
    "other-workflow": ("the call is of ",),
    "other-dockerfile": ("the call passes ",),
    "other-image": ("the call passes ",),
    "other-kind": ("the call passes ",),
    "extra-input": ("the call passes ",),
}


@pytest.mark.parametrize("name", list(CALLERS))
@pytest.mark.parametrize("defect", list(CALLER_FOUND))
def test_a_caller_that_can_run_off_main_or_is_more_than_a_call_is_found(
    name: str, defect: str
) -> None:
    """Each defect is made on the real caller, around the lines it holds."""
    path = ROOT / ".github" / "workflows" / name
    text = path.read_text(encoding="utf-8")
    group, dockerfile, repository, kind = CALLERS[name]
    if defect == "trigger-push":
        broken = text.replace("on:\n  workflow_dispatch:\n", "on:\n  workflow_dispatch:\n  push:\n")
    elif defect == "second-trigger-schedule":
        broken = text.replace(
            "on:\n  workflow_dispatch:\n",
            "on:\n  workflow_dispatch:\n  schedule:\n    - cron: '0 3 * * 1'\n",
        )
    elif defect == "write-everywhere":
        broken = text.replace("permissions: {}\n", "permissions:\n  packages: write\n", 1)
    elif defect == "group-shared":
        other = next(g for n, (g, *_) in CALLERS.items() if n != name)
        broken = text.replace(f"group: {group}\n", f"group: {other}\n")
    elif defect == "group-cancels":
        broken = text.replace("cancel-in-progress: false", "cancel-in-progress: true")
    elif defect == "second-job":
        broken = text + "  second:\n    uses: ./.github/workflows/own-image.yml\n"
    elif defect == "job-with-steps":
        broken = text + "    steps:\n      - run: echo\n"
    elif defect == "job-with-if":
        broken = text + "    if: github.ref == 'refs/heads/main'\n"
    elif defect == "secrets-inherited":
        broken = text + "    secrets: inherit\n"
    elif defect == "extra-permission":
        broken = text.replace(
            "      packages: write #", "      id-token: write\n      packages: write #"
        )
    elif defect == "other-workflow":
        broken = text.replace("./.github/workflows/own-image.yml", "./.github/workflows/ci.yml")
    elif defect == "other-dockerfile":
        broken = text.replace(dockerfile, "deployments/docker/Dockerfile")
    elif defect == "other-image":
        broken = text.replace(f"ghcr.io/gora-tools/{repository}", "ghcr.io/other/image")
    elif defect == "other-kind":
        broken = text.replace(f"kind: {kind}", "kind: none")
    else:
        broken = text + "      extra: x\n"
    assert broken != text
    found_only(caller_problems(broken, name), *CALLER_FOUND[defect])


def test_a_guard_that_lets_a_branch_through_is_found(tmp_path: Path) -> None:
    """The defect on the real file: the guard no longer compares the ref with main."""
    text = OWN_WORKFLOW.read_text(encoding="utf-8")
    broken = text.replace('[ "${GITHUB_REF:-}" != "refs/heads/main" ]', '[ -z "${GITHUB_REF:-}" ]')
    assert broken != text
    code, _ = base_guard("refs/heads/feature", tmp_path, broken)
    assert code == 0


# --- GORA's scanner image: Grype by digest, its database in the image, offline at run time ---

GRYPE_FROM = re.compile(r"ghcr\.io/anchore/grype:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}")
# The settings that keep the image offline (`grype config`, documented at the pinned version),
# each with the value that turns the network access off.
OFFLINE = {
    "GRYPE_DB_AUTO_UPDATE": "false",
    "GRYPE_DB_VALIDATE_AGE": "false",
    "GRYPE_CHECK_FOR_APP_UPDATE": "false",
}
DB_DIR = "/grype-db"
GRYPE_UPDATE = '["/grype", "db", "update"]'
ATTRIBUTION = "deployments/docker/scanner-data-sources.md"


def instructions(dockerfile: str) -> list[str]:
    """Each instruction of a Dockerfile as one line: continuations joined, comments dropped."""
    lines = [line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")]
    joined = "\n".join(lines).replace("\\\n", " ")
    return [" ".join(line.split()) for line in joined.splitlines() if line.strip()]


def scanner_problems(dockerfile: str) -> list[str]:
    """GORA's scanner image (ADR-011, the entry "the image scan runs from GORA's own scanner
    image, with its database in it"): one base, Grype's image by the digest of its index; the
    database downloaded in the build, into DB_DIR, which Grype is pointed at; and the update of
    the database, its age check and the check for a newer Grype off in the image's environment."""
    steps = instructions(dockerfile)
    froms = [step.split(" ", 1)[1] for step in steps if step.startswith("FROM ")]
    bad = []
    if len(froms) != 1 or GRYPE_FROM.fullmatch(froms[0]) is None:
        bad.append(f"the bases are {froms}, not Grype's image by tag and digest")
    env: dict[str, str] = {}
    at: dict[str, int] = {}
    for number, step in enumerate(steps):
        if step.startswith("ENV "):
            for word in shlex.split(step[4:]):
                key, _, value = word.partition("=")
                env[key] = value
                at[key] = number
    bad += [
        f"{key} is {env[key]}, not {want}" if key in env else f"{key} is not set, not {want}"
        for key, want in OFFLINE.items()
        if env.get(key) != want
    ]
    if env.get("GRYPE_DB_CACHE_DIR") != DB_DIR:
        bad.append(f"GRYPE_DB_CACHE_DIR is {env.get('GRYPE_DB_CACHE_DIR')}, not {DB_DIR}")
    update = [n for n, step in enumerate(steps) if step == f"RUN {GRYPE_UPDATE}"]
    if len(update) != 1:
        bad.append(f"the database is downloaded {len(update)} times in the build, not once")
    elif at.get("GRYPE_DB_CACHE_DIR", len(steps)) > update[0]:
        bad.append("the database is downloaded before GRYPE_DB_CACHE_DIR names where")
    if 'RUN ["/grype", "db", "status"]' not in steps:
        bad.append("the build does not check the status of the database it downloaded")
    if f"COPY {ATTRIBUTION} /usr/share/doc/gora-scanner/DATA-SOURCES.md" not in steps:
        bad.append("the data sources and their terms are not copied into the image")
    return bad


def test_the_scanner_image_is_grype_by_digest_with_its_database_and_offline() -> None:
    assert scanner_problems(SCANNER_DOCKERFILE.read_text(encoding="utf-8")) == []


def test_the_scanner_image_carries_the_attribution_of_its_data() -> None:
    text = (ROOT / ATTRIBUTION).read_text(encoding="utf-8")
    assert "https://oss.anchore.com/docs/reference/grype/data-sources/" in text
    assert "not found" in text  # the terms that were not found are said so, not stated


SCANNER_FOUND = {
    "auto-update-on": "GRYPE_DB_AUTO_UPDATE is not set, not false",
    "age-check-on": "GRYPE_DB_VALIDATE_AGE is not set, not false",
    "app-update-check-on": "GRYPE_CHECK_FOR_APP_UPDATE is not set, not false",
    "auto-update-true": "GRYPE_DB_AUTO_UPDATE is true, not false",
    "db-dir-moved": "GRYPE_DB_CACHE_DIR is /elsewhere, not /grype-db",
    "no-download": "the database is downloaded 0 times in the build, not once",
    "download-before-dir": "the database is downloaded before GRYPE_DB_CACHE_DIR names where",
    "no-status": "the build does not check the status of the database it downloaded",
    "base-by-tag": "the bases are ",
    "base-twice": "the bases are ",
    "no-attribution": "the data sources and their terms are not copied into the image",
}


@pytest.mark.parametrize("defect", list(SCANNER_FOUND))
def test_a_scanner_image_that_can_reach_the_network_or_misses_its_data_is_found(
    defect: str,
) -> None:
    """Each defect is made on the real file, around the lines it holds."""
    text = SCANNER_DOCKERFILE.read_text(encoding="utf-8")
    base = next(line for line in text.splitlines() if line.startswith("FROM "))
    if defect == "auto-update-on":
        broken = text.replace("ENV GRYPE_DB_AUTO_UPDATE=false \\\n    ", "ENV ")
    elif defect == "age-check-on":
        broken = text.replace("    GRYPE_DB_VALIDATE_AGE=false \\\n", "")
    elif defect == "app-update-check-on":
        broken = text.replace(" \\\n    GRYPE_CHECK_FOR_APP_UPDATE=false", "")
    elif defect == "auto-update-true":
        broken = text.replace("GRYPE_DB_AUTO_UPDATE=false", "GRYPE_DB_AUTO_UPDATE=true")
    elif defect == "db-dir-moved":
        broken = text.replace(f"GRYPE_DB_CACHE_DIR={DB_DIR}", "GRYPE_DB_CACHE_DIR=/elsewhere")
    elif defect == "no-download":
        broken = text.replace(f"RUN {GRYPE_UPDATE}\n", "")
    elif defect == "download-before-dir":
        broken = text.replace(f"ENV GRYPE_DB_CACHE_DIR={DB_DIR}\n", "").replace(
            f"RUN {GRYPE_UPDATE}\n", f"RUN {GRYPE_UPDATE}\nENV GRYPE_DB_CACHE_DIR={DB_DIR}\n"
        )
    elif defect == "no-status":
        broken = text.replace('RUN ["/grype", "db", "status"]\n', "")
    elif defect == "base-by-tag":
        broken = text.replace(base, "FROM ghcr.io/anchore/grype:latest")
    elif defect == "base-twice":
        broken = text.replace(base, f"{base}\n{base}")
    else:
        broken = re.sub(r"(?m)^COPY .*\n", "", text)
    assert broken != text
    found_only(scanner_problems(broken), SCANNER_FOUND[defect])


def test_the_dockerfile_reader_joins_continuations_and_drops_comments() -> None:
    text = "# a comment\nENV A=1 \\\n    B=2\n  # indented comment\nRUN x\n"
    assert instructions(text) == ["ENV A=1 B=2", "RUN x"]


# The smoke script, run against a stand-in for docker that answers as Grype's image does.
STUB_DOCKER = """#!/bin/sh
case "$*" in
  *" version"*) echo "Application: grype"; echo "Version: 0.0.0" ;;
  *" db status"*) echo "Schema: v6.1.10"; echo "Built: 2026-11-07T06:18:48Z"
    echo "Status:    ${STUB_STATUS:-valid}" ;;
  *certifi@2026.6.17*)
    echo "certifi 2026.6.17 2026.10.18 python ${STUB_ADVISORY:-GHSA-6q48-6c7w-9gw2}"
    exit "${STUB_VULNERABLE_EXIT:-2}" ;;
  *certifi@2026.10.23*) echo "No vulnerabilities found"; exit "${STUB_FIXED_EXIT:-0}" ;;
  *) echo "stub docker: unexpected $*" >&2; exit 99 ;;
esac
"""
STUB_TIMEOUT = '#!/bin/sh\nshift\nexec "$@"\n'


def smoke_scanner(tmp_path: Path, **env: str) -> tuple[int, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("timeout", STUB_TIMEOUT)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    done = subprocess.run(
        ["bash", str(OWN_IMAGE_SMOKE), "scanner", "gora-scanner:test", "linux/arm64"],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return done.returncode, done.stdout + done.stderr


def test_the_scanner_smoke_passes_when_the_database_sees_the_advisory(tmp_path: Path) -> None:
    code, output = smoke_scanner(tmp_path)
    assert code == 0, output
    assert "smoke: scanner image holds" in output


@pytest.mark.parametrize(
    ("env", "said"),
    [
        ({"STUB_STATUS": "invalid"}, "the database status is not valid with no network"),
        ({"STUB_VULNERABLE_EXIT": "0"}, "should fail the scan (exit 2) and exited 0"),
        ({"STUB_VULNERABLE_EXIT": "1"}, "should fail the scan (exit 2) and exited 1"),
        ({"STUB_ADVISORY": "GHSA-other"}, "failed the scan without reporting GHSA-6q48-6c7w-9gw2"),
        ({"STUB_FIXED_EXIT": "2"}, "should pass the scan (exit 0) and exited 2"),
    ],
    ids=["status-invalid", "advisory-unseen", "other-failure", "wrong-advisory", "fixed-fails"],
)
def test_the_scanner_smoke_fails_on_each_thing_it_checks(
    env: dict[str, str], said: str, tmp_path: Path
) -> None:
    code, output = smoke_scanner(tmp_path, **env)
    assert code == 1, output
    assert "smoke: FAIL: " in output
    assert said in output


def test_the_smoke_packages_can_be_swapped_for_the_red_first_proof(tmp_path: Path) -> None:
    code, output = smoke_scanner(tmp_path, SCANNER_SMOKE_VULNERABLE="pkg:pypi/certifi@2026.10.23")
    assert code == 1, output
    assert "pkg:pypi/certifi@2026.10.23 should fail the scan (exit 2) and exited 0" in output


def test_the_smoke_script_refuses_a_wrong_call(tmp_path: Path) -> None:
    for args in ([], ["scanner"], ["other", "image", "linux/amd64"]):
        done = subprocess.run(
            ["bash", str(OWN_IMAGE_SMOKE), *args], capture_output=True, text=True, check=False
        )
        assert done.returncode == 64, done.stderr


def test_the_smoke_script_runs_every_container_offline_and_read_only() -> None:
    """The base's checks moved here from the workflow's step: the same container options and
    the same four assertions; the scanner's containers run as the gate will."""
    text = OWN_IMAGE_SMOKE.read_text(encoding="utf-8")
    assert text.count("--read-only --network none") == 2
    checks = ("dpkg-query --show", "dpkg-query --search /usr/bin/git", "/usr/bin/git --version")
    for check in checks:
        assert check in text
    assert "deployments/docker/base.Dockerfile" in text
    assert "--only-fixed --fail-on high" in text
    assert "GHSA-6q48-6c7w-9gw2" in text


# --- the update path -----------------------------------------------------------------------


def test_dependabot_proposes_every_pin() -> None:
    """github-actions (workflows and the shared actions), docker (every Dockerfile of
    deployments/docker: the Python base of GORA's base image, the uv stage, the gate images),
    uv. Not GORA's base image, which the release Dockerfile names by a digest alone and a
    person moves after running base.yml.

    uv proposes every package in one pull request and raises each floor in pyproject.toml
    with it: a floor is the lowest version proven, the one uv.lock carries and CI runs
    (TESTING rule 3).
    """
    config = _yaml(ROOT / ".github" / "dependabot.yml")
    updates = {u["package-ecosystem"]: u for u in config["updates"]}
    assert set(updates) == {"github-actions", "docker", "uv"}
    assert {"/", "/.github/actions/*"} <= set(updates["github-actions"]["directories"])
    assert updates["docker"]["directory"] == "/deployments/docker"
    # An ignore with no update-types ignores every update of the image.
    base_ignores = [
        entry
        for entry in updates["docker"]["ignore"]
        if entry["dependency-name"] == "ghcr.io/gora-tools/gora-base"
    ]
    assert base_ignores == [{"dependency-name": "ghcr.io/gora-tools/gora-base"}]
    # The header says how a base is applied, since Dependabot does not.
    header = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8").split("version:")[0]
    assert "base.yml" in header
    # Grype's image is proposed like any other pin, and the header says how its bump is applied.
    assert "scanner.yml" in header
    assert not [e for e in updates["docker"]["ignore"] if "grype" in e["dependency-name"]]
    assert updates["uv"]["directory"] == "/"
    assert {u["schedule"]["interval"] for u in updates.values()} == {"weekly"}
    uv_groups = updates["uv"].get("groups", {}).values()
    assert any("*" in group.get("patterns", []) for group in uv_groups)
    assert updates["uv"].get("versioning-strategy") == "increase"

"""Every build input is pinned, and the pins agree (ADR-021, TESTING rules 3 and 8).

Read from the files themselves, offline (TESTING rule 6): the workflows and the actions
they share, the Dockerfiles, pyproject.toml and .python-version. Each check is a function
of the parsed text, and each is shown red here on a defect of its class (ADR-0016 rule 1)
before it is trusted on the real files.
"""

import re
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


# --- one uv version ------------------------------------------------------------------------


def uv_from_image(text: str, stage: str) -> str:
    """The uv version in the tag of the uv image a stage starts from."""
    for line in text.splitlines():
        match = FROM.match(line)
        if match and match["stage"] == stage and match["image"].startswith(UV_IMAGE):
            tag = match["image"][len(UV_IMAGE) :].split("@")[0]
            return tag.split("-")[0]
    return f"(no {UV_IMAGE} stage {stage})"


def uv_required(pyproject: str) -> str:
    """The exact version [tool.uv] required-version names; anything else is not a pin."""
    required = str(tomllib.loads(pyproject).get("tool", {}).get("uv", {}).get("required-version"))
    return required.removeprefix("==") if re.fullmatch(r"==\d+\.\d+\.\d+", required) else required


def uv_in_ci(documents: list[dict[Any, Any]], pyproject_version: str) -> list[str]:
    """The version every setup-uv step installs: its version input, or pyproject.toml's."""
    found = []
    for document in documents:
        for step in _steps(document):
            if not str(step.get("uses", "")).startswith(SETUP_UV):
                continue
            given = step.get("with", {}) or {}
            if "version" in given:
                found.append(str(given["version"]))
            elif given.get("version-file") == "pyproject.toml":
                found.append(pyproject_version)
            else:
                found.append("(setup-uv without version-file: pyproject.toml)")
    return found


def uv_versions(pyproject: str, dockerfile: str, gate_images: str, ci: list[str]) -> set[str]:
    return (
        {uv_required(pyproject), uv_from_image(dockerfile, "uv")}
        | {uv_from_image(gate_images, "linux-gate")}
        | set(ci)
    )


def _uv_inputs() -> tuple[str, str, str, list[dict[Any, Any]]]:
    return (
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        RELEASE_DOCKERFILE.read_text(encoding="utf-8"),
        GATE_IMAGES.read_text(encoding="utf-8"),
        [_yaml(path) for path in WORKFLOWS + ACTIONS],
    )


def test_one_uv_version_everywhere() -> None:
    """pyproject.toml, the uv stage of the release image, the Linux gate image and every
    setup-uv in CI name one uv version. A bump changes all of them in one commit."""
    pyproject, dockerfile, gate_images, documents = _uv_inputs()
    required = uv_required(pyproject)
    ci = uv_in_ci(documents, required)
    assert ci
    assert uv_versions(pyproject, dockerfile, gate_images, ci) == {required}
    assert re.fullmatch(r"\d+\.\d+\.\d+", required)


@pytest.mark.parametrize(
    "where",
    ["pyproject", "dockerfile", "gate-images", "ci-version", "ci-no-file", "pyproject-range"],
)
def test_a_uv_version_that_differs_is_found(where: str) -> None:
    pyproject, dockerfile, gate_images, documents = _uv_inputs()
    required = uv_required(pyproject)
    other = "0.0.1"
    if where == "pyproject":
        pyproject = pyproject.replace(f'"=={required}"', f'"=={other}"')
    elif where == "pyproject-range":
        pyproject = pyproject.replace(f'"=={required}"', f'">={required}"')
    elif where == "dockerfile":
        dockerfile = dockerfile.replace(f"{UV_IMAGE}{required}@", f"{UV_IMAGE}{other}@")
    elif where == "gate-images":
        gate_images = gate_images.replace(f"{UV_IMAGE}{required}-", f"{UV_IMAGE}{other}-")
    else:
        step = {"uses": f"{SETUP_UV}x", "with": {"version": other}}
        if where == "ci-no-file":
            step = {"uses": f"{SETUP_UV}x", "with": {}}
        documents = [*documents, {"runs": {"steps": [step]}}]
    ci = uv_in_ci(documents, uv_required(pyproject))
    assert len(uv_versions(pyproject, dockerfile, gate_images, ci)) > 1


# --- one Python patch version --------------------------------------------------------------


def test_python_is_declared_once_to_the_patch() -> None:
    """.python-version names the patch: the suite gets it from uv, the release image from its
    base, whose build fails on another patch (UV_PYTHON_PREFERENCE=only-system) and whose
    smoke compares `python --version` with it. The base images name the same minor version."""
    declared = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"3\.\d+\.\d+", declared)
    minor = declared.rsplit(".", 1)[0]
    dockerfile = RELEASE_DOCKERFILE.read_text(encoding="utf-8")
    bases = [m["image"] for line in dockerfile.splitlines() if (m := FROM.match(line))]
    python = [image for image in bases if image.startswith("python:")]
    assert len(python) == 2
    assert len(set(python)) == 1  # build and runtime stages: one digest
    assert python[0].startswith(f"python:{minor}-slim@sha256:")
    assert "COPY pyproject.toml uv.lock .python-version ./" in dockerfile
    assert "UV_PYTHON_PREFERENCE=only-system" in dockerfile
    assert f"-python{minor}-" in GATE_IMAGES.read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["uv"]["python-preference"] == "only-managed"


# --- tools the actions install are pinned --------------------------------------------------


def unpinned_tools(document: dict[Any, Any]) -> list[str]:
    """Each version-like action input or *_VERSION value that is not one exact version."""
    env = {str(k): str(v) for k, v in (document.get("env") or {}).items()}
    bad = [
        f"env {k}={v}" for k, v in env.items() if k.endswith("_VERSION") and not SEMVER.fullmatch(v)
    ]
    for step in _steps(document):
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


# --- the update path -----------------------------------------------------------------------


def test_dependabot_proposes_every_pin() -> None:
    """github-actions (workflows and the shared actions), docker (both Dockerfiles), uv."""
    config = _yaml(ROOT / ".github" / "dependabot.yml")
    updates = {u["package-ecosystem"]: u for u in config["updates"]}
    assert set(updates) == {"github-actions", "docker", "uv"}
    assert {"/", "/.github/actions/*"} <= set(updates["github-actions"]["directories"])
    assert updates["docker"]["directory"] == "/deployments/docker"
    assert updates["uv"]["directory"] == "/"
    assert {u["schedule"]["interval"] for u in updates.values()} == {"weekly"}

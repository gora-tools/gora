"""GORA's artifact carries its source commit, not its version (L9, ADR-022 section 3).

One image is first ``X.Y.Z-rc`` and then ``X.Y.Z`` (ADR-024): a version built into it would be
wrong once it enters ``prod``, or would force a rebuild. So the package version is a fixed
label, never bumped; ``src/gora`` has no version literal and reads no version from package
metadata. What GORA shows comes from its environment: ``GORA_REVISION``, which the release image
sets from its build, and ``GORA_VERSION``, which a deployment sets.
"""

import ast
import tomllib
from pathlib import Path

import pytest

import gora
from gora.build_info import BuildInfo, build_info

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(gora.__file__).parent
REVISION = "0123456789abcdef0123456789abcdef01234567"


def test_the_package_version_is_a_fixed_label() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == "0.0.0"


def test_the_lockfile_follows() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    (package,) = [p for p in lock["package"] if p["name"] == "gora"]
    assert package["version"] == "0.0.0"


# --- no version inside the package ---------------------------------------------------------

METADATA = frozenset({"importlib.metadata", "importlib_metadata", "pkg_resources"})


def version_sources(source: str) -> list[str]:
    """Each ``__version__``, and each import of a module that reads package metadata."""
    found = []
    for node in ast.walk(ast.parse(source)):
        held = (isinstance(node, ast.Name) and node.id == "__version__") or (
            isinstance(node, ast.alias) and node.name == "__version__"
        )
        if held:
            found.append("__version__")
        elif isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if alias.name in METADATA]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {alias.name for alias in node.names}
            if module in METADATA:
                found.append(module)
            elif module == "importlib" and "metadata" in names:
                found.append("importlib.metadata")
    return found


def test_the_package_finds_its_modules() -> None:
    assert len(list(SRC.rglob("*.py"))) > 20


@pytest.mark.parametrize(
    "path", sorted(SRC.rglob("*.py")), ids=lambda p: p.relative_to(SRC).as_posix()
)
def test_no_module_holds_or_reads_a_version(path: Path) -> None:
    assert version_sources(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        '__version__ = "0.1.0"',
        "from gora import __version__",
        "import importlib.metadata",
        "from importlib.metadata import version",
        "from importlib import metadata",
        "import importlib_metadata",
        "import pkg_resources",
    ],
)
def test_a_version_held_or_read_is_found(source: str) -> None:
    assert version_sources(source) != []


def test_the_package_has_no_version_attribute() -> None:
    assert not hasattr(gora, "__version__")


# --- what it shows comes from its environment ----------------------------------------------


def test_outside_a_release_image_the_revision_is_unknown_and_the_version_not_set() -> None:
    assert build_info({}) == BuildInfo(revision="unknown", version="not set")


def test_the_revision_and_the_version_come_from_the_environment() -> None:
    environ = {"GORA_REVISION": REVISION, "GORA_VERSION": "0.2.0-rc.7"}
    assert build_info(environ) == BuildInfo(revision=REVISION, version="0.2.0-rc.7")


def test_an_empty_value_counts_as_absent() -> None:
    environ = {"GORA_REVISION": "", "GORA_VERSION": ""}
    assert build_info(environ) == BuildInfo(revision="unknown", version="not set")


def test_it_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GORA_REVISION", REVISION)
    monkeypatch.setenv("GORA_VERSION", "0.2.0")
    assert build_info() == BuildInfo(revision=REVISION, version="0.2.0")

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
# ``__version__`` is found as a name wherever it appears; this is the other spelling.
VERSION_NAMES = frozenset({"version"})


def _held_literal(node: ast.AST) -> bool:
    """``VERSION = "1.2.3"``, ``version: str = "1.2.3"``: a name for a version, given a string.
    ``__version__`` is found by name."""
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        return False
    named = any(isinstance(t, ast.Name) and t.id.lower() in VERSION_NAMES for t in targets)
    return named and isinstance(value, ast.Constant) and isinstance(value.value, str)


def _imports_metadata_by_call(node: ast.AST) -> bool:
    """``importlib.import_module(...)`` or ``import_module(...)`` of a metadata module, or of a
    name that is not a literal, which could be one."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name not in ("import_module", "__import__"):
        return False
    first = node.args[0] if node.args else None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value in METADATA or first.value.startswith("importlib.metadata")
    return True


def version_sources(source: str) -> list[str]:
    """Each ``__version__``, each string given to a name for a version, and each import of a
    module that reads package metadata, by statement or by call."""
    found = []
    for node in ast.walk(ast.parse(source)):
        held = (
            (isinstance(node, ast.Name) and node.id == "__version__")
            or (isinstance(node, ast.Attribute) and node.attr == "__version__")
            or (isinstance(node, ast.alias) and node.name == "__version__")
        )
        if held:
            found.append("__version__")
        elif _held_literal(node):
            found.append("VERSION =")
        elif _imports_metadata_by_call(node):
            found.append("import_module")
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


HELD_OR_READ = [
    ('__version__ = "0.1.0"', "__version__"),
    ("from gora import __version__", "__version__"),
    ("import importlib.metadata", "importlib.metadata"),
    ("from importlib.metadata import version", "importlib.metadata"),
    ("from importlib import metadata", "importlib.metadata"),
    ("import importlib_metadata", "importlib_metadata"),
    ("import pkg_resources", "pkg_resources"),
    ('VERSION = "0.1.0"', "VERSION ="),
    ('version = "0.1.0"', "VERSION ="),
    ('__version__: str = "0.1.0"', "__version__"),
    ("print(gora.__version__)", "__version__"),
    ("print(pkg.sub.__version__)", "__version__"),
    ('importlib.import_module("importlib.metadata")', "import_module"),
    ('import_module("pkg_resources")', "import_module"),
    ("importlib.import_module(name)", "import_module"),
    ('__import__("importlib.metadata")', "import_module"),
]


@pytest.mark.parametrize(
    "source",
    [
        'VERSION_FILE = "VERSION"',
        "version = build_info().version",
        'importlib.import_module("json")',
        'name = "version"',
    ],
)
def test_a_name_or_a_call_that_is_no_version_is_not_found(source: str) -> None:
    assert version_sources(source) == []


@pytest.mark.parametrize(
    ("source", "found"), HELD_OR_READ, ids=[source for source, _ in HELD_OR_READ]
)
def test_a_version_held_or_read_is_found(source: str, found: str) -> None:
    assert version_sources(source) == [found]


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

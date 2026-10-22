"""Every third-party module the source imports is a dependency pyproject.toml declares.

A module the source imports directly, which only a dependency of a dependency provides, works
until that dependency drops it, and uv.lock does not say that it was ever relied on
(pydantic-core, imported by the domain models, was one). The check reads the imports of
src/gora and the installed distributions that provide each module; the suite runs with every
extra installed (make sync-test), so none is missing. Each check is shown red here on a defect
of its class (ADR-0016 rule 1) before it is trusted on the real files.
"""

import ast
import re
import sys
import tomllib
from collections.abc import Mapping
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "gora"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
OWN = frozenset({"gora", *sys.stdlib_module_names})


def _normalised(name: str) -> str:
    """A name as PEP 503 compares it: ``pydantic_core`` and ``pydantic-core`` are one."""
    return re.sub(r"[-_.]+", "-", name).lower()


def imported(source: str) -> set[str]:
    """The top-level module of every absolute import in ``source``, in any scope."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def declared(pyproject: str) -> set[str]:
    """The distributions of ``[project] dependencies`` and of every extra, normalised."""
    project = tomllib.loads(pyproject)["project"]
    extras = project.get("optional-dependencies", {}).values()
    requirements = [*project.get("dependencies", []), *(r for extra in extras for r in extra)]
    return {_normalised(match[0]) for r in requirements if (match := NAME.match(r))}


def undeclared(modules: set[str], pyproject: str, providers: Mapping[str, list[str]]) -> list[str]:
    """Each third-party module that no declared dependency provides, with what does."""
    wanted = declared(pyproject)
    bad = []
    for module in sorted(modules - OWN):
        given = sorted({_normalised(name) for name in providers.get(module, [])})
        if not wanted & set(given):
            bad.append(f"{module}: provided by {', '.join(given) or 'nothing installed'}")
    return bad


def _source_modules() -> set[str]:
    return set().union(*(imported(p.read_text(encoding="utf-8")) for p in SOURCE.rglob("*.py")))


def _pyproject() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_every_third_party_module_the_source_imports_is_declared() -> None:
    assert undeclared(_source_modules(), _pyproject(), packages_distributions()) == []


def test_the_imports_of_the_source_are_found_at_all() -> None:
    assert {"pydantic", "pydantic_core", "pydantic_settings", "ruamel", "typer"} <= (
        _source_modules() - OWN
    )


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import os, yaml\n", {"os", "yaml"}),
        ("import a.b.c as d\n", {"a"}),
        ("from a.b import c\n", {"a"}),
        ("from . import c\nfrom .d import e\nfrom .. import f\n", set()),
        ("def f():\n    import a\n", {"a"}),
        ("if TYPE_CHECKING:\n    from a import b\n", {"a"}),
        ("class C:\n    def m(self):\n        from a.b import c\n", {"a"}),
        ("# import a\nx = 'import b'\n", set()),
    ],
    ids=[
        "plain",
        "dotted",
        "from",
        "relative",
        "in-function",
        "type-checking",
        "in-method",
        "text",
    ],
)
def test_an_import_is_found_in_any_scope_and_a_relative_one_is_not(
    source: str, found: set[str]
) -> None:
    assert imported(source) == found


def test_a_module_only_a_transitive_dependency_provides_is_found() -> None:
    """pydantic requires annotated-types; nothing here declares it."""
    problems = undeclared({"annotated_types"}, _pyproject(), packages_distributions())
    assert problems == ["annotated_types: provided by annotated-types"]


def test_a_declared_dependency_of_an_extra_provides_its_module() -> None:
    assert undeclared({"fastapi"}, _pyproject(), packages_distributions()) == []


def test_the_standard_library_and_the_package_itself_need_no_declaration() -> None:
    assert undeclared({"os", "json", "gora"}, _pyproject(), packages_distributions()) == []


def test_a_module_nothing_installed_provides_is_found() -> None:
    problems = undeclared({"not_installed_anywhere"}, _pyproject(), packages_distributions())
    assert problems == ["not_installed_anywhere: provided by nothing installed"]


def test_pydantic_core_removed_from_the_declarations_is_found() -> None:
    """The defect is made on the real file: the line that declares pydantic-core removed."""
    pyproject = _pyproject()
    broken = re.sub(r'^\s+"pydantic-core[^"]*",\n', "", pyproject, flags=re.MULTILINE)
    assert broken != pyproject
    problems = undeclared(_source_modules(), broken, packages_distributions())
    assert problems == ["pydantic_core: provided by pydantic-core"]

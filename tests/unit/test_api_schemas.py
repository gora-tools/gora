"""The published JSON Schemas of the API's bodies, ``/v1`` (release-model section 11).

The committed schema of ``changes`` is the one generated from its model, byte for byte, written
the way the release files' schemas are, and it says what the model says: the same fields, all
required, the grammars' patterns and lengths. The bodies ``gora changes --output json`` produces
validate against it in ``tests/integration/test_changes.py``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import gora
from gora.api.schemas import DESCRIPTIONS, FILES, SCHEMAS, schema, schema_json, write
from gora.domain.release.identifiers import GRAMMARS
from gora.domain.release_repository.schema import DIALECT

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED = REPO_ROOT / "contracts" / "schemas" / "api" / "v1"
REGENERATE = "uv run python -m gora.api.schemas contracts/schemas/api/v1"
KINDS = sorted(FILES)


def committed(kind: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((COMMITTED / FILES[kind][0]).read_text(encoding="utf-8"))
    return loaded


def test_the_committed_files_are_exactly_the_published_schemas() -> None:
    assert sorted(p.name for p in COMMITTED.iterdir()) == sorted(f for f, _, _ in FILES.values())
    assert KINDS == ["Changes"]


@pytest.mark.parametrize("kind", KINDS)
def test_the_committed_schema_is_the_generated_one(kind: str) -> None:
    assert (COMMITTED / FILES[kind][0]).read_text(encoding="utf-8") == schema_json(kind), (
        f"the committed schema is stale; regenerate it: {REGENERATE}"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_the_committed_schema_is_written_deterministically(kind: str) -> None:
    text = (COMMITTED / FILES[kind][0]).read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    assert text.isascii()


@pytest.mark.parametrize("kind", KINDS)
def test_it_is_a_valid_draft_2020_12_schema_with_its_id(kind: str) -> None:
    published = committed(kind)
    assert published["$schema"] == DIALECT == "https://json-schema.org/draft/2020-12/schema"
    assert published["$id"] == f"{SCHEMAS}/{FILES[kind][0]}"
    assert SCHEMAS == "https://gora.tools/schemas/api/v1"
    assert published["title"] and published["description"]
    assert all(d.get("description") for d in published.get("$defs", {}).values())
    Draft202012Validator.check_schema(published)


def test_the_generated_schema_is_the_same_every_time() -> None:
    assert schema("Changes") == schema("Changes")
    assert schema_json("Changes") == schema_json("Changes")


def test_every_definition_and_the_schema_itself_has_a_description_of_its_own() -> None:
    published = committed("Changes")
    assert set(published["$defs"]) | {"Changes"} == set(DESCRIPTIONS)
    assert len(set(DESCRIPTIONS.values())) == len(DESCRIPTIONS)


def test_it_states_what_the_model_states() -> None:
    """Every field is required and none is added: ``registered`` is null, never absent."""
    published = committed("Changes")
    assert published["additionalProperties"] is False
    assert list(published["properties"]) == ["artifacts", "commit", "project", "source"]
    assert sorted(published["required"]) == sorted(published["properties"])
    defs = published["$defs"]
    changed = defs["ChangedArtifact"]
    assert sorted(changed["properties"]) == ["name", "registered", "version"]
    assert sorted(changed["required"]) == ["name", "registered", "version"]
    assert changed["properties"]["registered"]["anyOf"] == [
        {"$ref": "#/$defs/RegisteredCandidate"},
        {"type": "null"},
    ]
    registered = defs["RegisteredCandidate"]
    assert sorted(registered["properties"]) == ["commit", "version"]
    assert sorted(registered["required"]) == ["commit", "version"]
    assert all(d["additionalProperties"] is False for d in defs.values())


def _patterns(node: Any) -> list[dict[str, Any]]:
    found = []
    if isinstance(node, dict):
        if "pattern" in node:
            found.append(node)
        for value in node.values():
            found += _patterns(value)
    elif isinstance(node, list):
        for value in node:
            found += _patterns(value)
    return found


def test_every_published_pattern_and_length_is_a_grammar_s() -> None:
    published = {(s["pattern"], s["maxLength"]) for s in _patterns(committed("Changes"))}
    assert len(published) == 4  # calibration: project/source, commit, name, version
    assert published <= {(g.pattern, g.max_length) for g in GRAMMARS}


def test_the_schemas_are_written_to_the_directory_given(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write(out)
    assert sorted(p.name for p in out.iterdir()) == ["changes.json"]
    assert (out / "changes.json").read_text(encoding="utf-8") == schema_json("Changes")


def test_the_command_that_regenerates_them_writes_what_is_committed(tmp_path: Path) -> None:
    """The command the test names, and the pull request's review, runs as documented."""
    src = Path(gora.__file__).parent.parent
    out = tmp_path / "out"
    out.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(src)}
    subprocess.run(
        [sys.executable, "-m", "gora.api.schemas", str(out)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=True,
    )
    assert (out / "changes.json").read_bytes() == (COMMITTED / "changes.json").read_bytes()

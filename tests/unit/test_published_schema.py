"""The published JSON Schema of manifest v0.1 (ADR-003, "Published contract").

The committed file is the one generated from the model, byte for byte. GORA's own manifests
validate against it, and documents GORA refuses fail it. Its patterns are the grammars'
patterns, and Python ``re`` reads them the way a JSON Schema validator does: unanchored
search, the pattern carrying its own anchors.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from gora.domain.release.identifiers import MANIFEST_GRAMMARS, STAGE, Grammar
from gora.domain.release.schema import DIALECT, manifest_schema_json
from tests.unit.test_identifiers import SAMPLES

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "release-manifest.v0.1.schema.json"
MANIFESTS = ["contracts/examples/release-manifest.yaml", "release-manifest.yaml"]
REGENERATE = (
    "uv run python -m gora.domain.release.schema "
    "> contracts/schemas/release-manifest.v0.1.schema.json"
)


def committed() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


def validator() -> Draft202012Validator:
    return Draft202012Validator(committed())


def test_the_committed_schema_is_the_generated_one() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == manifest_schema_json(), (
        f"the committed schema is stale; regenerate it: {REGENERATE}"
    )


def test_the_committed_schema_is_written_deterministically() -> None:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    assert text.isascii()


def test_it_is_a_valid_draft_2020_12_schema() -> None:
    schema = committed()
    assert schema["$schema"] == DIALECT == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("relative", MANIFESTS)
def test_gora_s_own_manifests_validate(relative: str) -> None:
    document = yaml.safe_load((REPO_ROOT / relative).read_text(encoding="utf-8"))
    errors = [e.message for e in validator().iter_errors(document)]
    assert errors == []


V01: dict[str, Any] = {
    "version": "v0.1",
    "release": "1.2.0",
    "candidate": {
        "version": "1.2.0-rc.1",
        "image": "ghcr.io/test/service",
        "digest": "sha256:" + "a" * 64,
    },
    "stages": {"dev": {"version": "1.2.0-rc.1", "digest": "sha256:" + "a" * 64}},
}


def _edited(path: str, value: Any) -> dict[str, Any]:
    """V01 with the value at a dotted path replaced; ``None`` deletes the key."""
    document = json.loads(json.dumps(V01))
    *parents, last = path.split(".")
    target = document
    for key in parents:
        target = target[key]
    if value is None:
        del target[last]
    else:
        target[last] = value
    return document


REFUSED_DOCUMENTS = {
    "stage-name": {**V01, "stages": {"dev[eu]": V01["stages"]["dev"]}},
    "stage-name-uppercase": {**V01, "stages": {"Dev": V01["stages"]["dev"]}},
    "extra-root-field": {**V01, "owner": "team-x"},
    "extra-candidate-field": _edited("candidate.signed", True),
    "digest-65": _edited("candidate.digest", "sha256:" + "a" * 65),
    "version-build-metadata": _edited("candidate.version", "1.2.0-rc.1+b1"),
    "image-no-registry": _edited("candidate.image", "test/service"),
    "release-missing": _edited("release", None),
    "release-pre-release": _edited("release", "1.2.0-rc.1"),
    "schema-version": _edited("version", "v0.2"),
    "version-too-long": _edited("stages.dev.version", "1.0.0-" + "a" * 123),
}


def test_the_control_document_validates() -> None:
    assert list(validator().iter_errors(V01)) == []


@pytest.mark.parametrize("name", sorted(REFUSED_DOCUMENTS))
def test_a_document_gora_refuses_fails_the_schema(name: str) -> None:
    assert list(validator().iter_errors(REFUSED_DOCUMENTS[name])) != []


# --- the patterns -----------------------------------------------------------------------------


def _string_schemas(node: Any) -> list[dict[str, Any]]:
    """Every subschema that carries a pattern, wherever it sits."""
    found = []
    if isinstance(node, dict):
        if "pattern" in node:
            found.append(node)
        for value in node.values():
            found += _string_schemas(value)
    elif isinstance(node, list):
        for value in node:
            found += _string_schemas(value)
    return found


def test_every_published_pattern_is_a_grammar_s_and_every_grammar_is_published() -> None:
    published = {(s["pattern"], s["maxLength"]) for s in _string_schemas(committed())}
    grammars = {(g.pattern, g.max_length) for g in MANIFEST_GRAMMARS}
    assert published == grammars


def test_the_stage_name_pattern_constrains_property_names() -> None:
    stages = committed()["properties"]["stages"]
    assert stages["propertyNames"] == {"pattern": STAGE.pattern, "maxLength": STAGE.max_length}
    assert "patternProperties" not in stages  # which would leave other names unchecked
    assert stages["additionalProperties"] == {"$ref": "#/$defs/StageState"}


def _accepted_by_schema(grammar: Grammar, value: str) -> bool:
    """As a JSON Schema validator reads ``pattern`` and ``maxLength``: search, then length."""
    return re.search(grammar.pattern, value) is not None and len(value) <= grammar.max_length


@pytest.mark.parametrize("grammar", MANIFEST_GRAMMARS, ids=lambda g: g.name)
def test_python_re_reads_each_pattern_as_the_checker_does(grammar: Grammar) -> None:
    valid, invalid = SAMPLES[grammar.name]
    samples = [v for v in valid + invalid if not v.endswith("\n")]
    disagree = [v for v in samples if _accepted_by_schema(grammar, v) != grammar.is_valid(v)]
    assert disagree == []
    assert any(not grammar.is_valid(v) for v in samples)  # calibration: refusals were sampled


def test_python_s_dollar_is_the_one_known_difference_and_the_checker_holds() -> None:
    """Python's ``$`` also matches before a final newline; ECMA-262's does not.

    A Python validator reading the published pattern accepts ``dev\\n``; GORA's checker uses
    ``fullmatch`` and refuses it, as an ECMA-262 validator does.
    """
    assert re.search(STAGE.pattern, "dev\n") is not None
    assert STAGE.is_valid("dev\n") is False

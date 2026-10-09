"""The published JSON Schemas of the release files, gora.tools/v1alpha1 (ADR-026 section 11).

Each committed schema is the one generated from its model, byte for byte. The examples validate
against it, and documents GORA refuses fail it. Its patterns are the grammars' and the formats'
patterns, and every grammar and format a model uses is published; a keyed mapping states its
key pattern under ``propertyNames``, never ``patternProperties``.
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from gora.domain.release.identifiers import GRAMMARS, NAME, PRINCIPAL, STAGE, Grammar
from gora.domain.release_repository import yaml12
from gora.domain.release_repository.formats import FORMATS
from gora.domain.release_repository.schema import DIALECT, FILES, schema_json

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = REPO_ROOT / "contracts" / "schemas" / "v1alpha1"
EXAMPLES = REPO_ROOT / "contracts" / "examples" / "v1alpha1"
REGENERATE = "uv run python -m gora.domain.release_repository.schema contracts/schemas/v1alpha1"
KINDS = sorted(FILES)
EXAMPLE_OF = {
    "Policy": "policy",
    "Artifact": "artifact",
    "StageState": "stage-state",
    "Rejection": "rejection",
}


def committed(kind: str) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((SCHEMAS / FILES[kind][0]).read_text(encoding="utf-8"))
    return schema


def example(kind: str) -> dict[str, Any]:
    document = yaml12.load((EXAMPLES / f"{EXAMPLE_OF[kind]}.yaml").read_bytes(), source=kind)
    assert isinstance(document, dict)
    return document


def errors(kind: str, document: object) -> list[str]:
    return [e.message for e in Draft202012Validator(committed(kind)).iter_errors(document)]


def test_the_committed_files_are_exactly_the_four_schemas() -> None:
    assert sorted(p.name for p in SCHEMAS.iterdir()) == sorted(f for f, _ in FILES.values())


@pytest.mark.parametrize("kind", KINDS)
def test_the_committed_schema_is_the_generated_one(kind: str) -> None:
    assert (SCHEMAS / FILES[kind][0]).read_text(encoding="utf-8") == schema_json(kind), (
        f"the committed schema is stale; regenerate it: {REGENERATE}"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_the_committed_schema_is_written_deterministically(kind: str) -> None:
    text = (SCHEMAS / FILES[kind][0]).read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    assert text.isascii()
    assert schema_json(kind) == schema_json(kind)


@pytest.mark.parametrize("kind", KINDS)
def test_it_is_a_valid_draft_2020_12_schema_with_its_id(kind: str) -> None:
    schema = committed(kind)
    assert schema["$schema"] == DIALECT == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"https://gora.tools/schemas/v1alpha1/{FILES[kind][0]}"
    assert schema["title"] and schema["description"]
    assert all(d.get("description") for d in schema.get("$defs", {}).values())
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("kind", KINDS)
def test_the_example_validates(kind: str) -> None:
    assert errors(kind, example(kind)) == []


def _edited(document: dict[str, Any], at: tuple[str | int, ...], value: object) -> Any:
    changed = copy.deepcopy(document)
    target: Any = changed
    for part in at[:-1]:
        target = target[part]
    if value is None:
        del target[at[-1]]
    else:
        target[at[-1]] = value
    return changed


REFUSED: list[tuple[str, tuple[str | int, ...], object]] = [
    # an unknown field, at each level
    ("Policy", ("x",), 1),
    ("Policy", ("spec", "x"), 1),
    ("Policy", ("spec", "stages", 0, "x"), 1),
    ("Policy", ("spec", "requests", "x"), []),
    ("Policy", ("spec", "evidence", "sbom", "x"), 1),
    ("Policy", ("spec", "attesters", "gora-pipeline", "issuer"), "https://a"),
    ("Artifact", ("spec", "source", "x"), 1),
    ("StageState", ("spec", "evidence", "x"), 1),
    ("Rejection", ("metadata", "x"), 1),
    # a missing required field
    ("Policy", ("spec", "requests", "reject"), None),
    ("Policy", ("spec", "branchAgeDays"), None),
    ("Artifact", ("spec", "initialVersion"), None),
    ("StageState", ("writtenBy",), None),
    ("Rejection", ("spec", "reason"), None),
    # a wrong kind
    ("Policy", ("kind",), "Artifact"),
    ("StageState", ("kind",), "Rejection"),
    ("Artifact", ("apiVersion",), "gora.tools/v2"),
    # a bad value for each pattern, and the bounds
    ("Policy", ("spec", "stages", 0, "name"), "Dev"),
    ("Policy", ("spec", "stages", 0, "verify", 0), "Deploy"),
    ("Policy", ("spec", "stages", 1, "approvers"), {"team/QA": 1}),
    ("Policy", ("spec", "stages", 1, "approvers"), {"team/qa": 0}),
    ("Policy", ("spec", "stages", 1, "approvers"), {}),
    ("Policy", ("spec", "reporters", "deploy"), "argocd"),
    ("Policy", ("spec", "reporters"), {"Deploy": "app/x"}),
    ("Policy", ("spec", "builders", "cally-ci", "issuer"), "http://a"),
    ("Policy", ("spec", "builders", "cally-ci", "identity"), "ci@acme.example"),
    ("Policy", ("spec", "builders", "cally-ci", "sigstore"), "private"),
    ("Policy", ("spec", "attesters", "gora-pipeline", "publicKey"), "not a key"),
    ("Policy", ("spec", "requests", "reject"), []),
    ("Policy", ("spec", "requests", "promote", "dev"), ["team/Developers"]),
    ("Policy", ("spec", "evidence", "sbom", "minVersion"), "1.06"),
    ("Policy", ("spec", "branchAgeDays"), 36501),
    ("Policy", ("spec", "branchAgeDays"), 1.5),
    ("Artifact", ("metadata", "name"), "API"),
    ("Artifact", ("spec", "image"), "cally-api"),
    ("Artifact", ("spec", "source", "repository"), "github.com/Acme/x"),
    ("Artifact", ("spec", "source", "tagPrefix"), "api"),
    ("Artifact", ("spec", "source", "inputs"), ["a b"]),
    ("Artifact", ("spec", "source", "inputs"), []),
    ("Artifact", ("spec", "initialVersion"), "0.1.0"),
    ("Artifact", ("spec", "stable"), "false"),
    ("Artifact", ("spec", "stages"), {}),
    ("StageState", ("spec", "digest"), "sha256:x"),
    ("StageState", ("spec", "version"), "1.4.0-rc.1"),
    ("StageState", ("spec", "sourceCommit"), "abc"),
    ("StageState", ("spec", "evidence", "result"), "failed"),
    ("StageState", ("spec", "evidence", "time"), "2026-10-09 12:00:00"),
    ("StageState", ("writtenBy",), "0.3.0+b"),
    ("Rejection", ("metadata", "version"), "1.1.0-rc"),
    ("Rejection", ("spec", "reason"), " padded"),
    ("Rejection", ("spec", "reason"), "two\nlines"),
    ("Rejection", ("spec", "reason"), "x" * 501),
]


@pytest.mark.parametrize(("kind", "at", "value"), REFUSED, ids=lambda x: str(x)[:40])
def test_a_document_gora_refuses_fails_the_schema(
    kind: str, at: tuple[str | int, ...], value: object
) -> None:
    assert errors(kind, _edited(example(kind), at, value)) != []


def test_the_control_an_absent_optional_field_validates() -> None:
    document = _edited(example("Policy"), ("spec", "stages", 1, "approvers"), None)
    assert errors("Policy", document) == []


def test_null_for_an_optional_field_fails_the_schema() -> None:
    for kind, at in [
        ("Policy", ("spec", "stages", 1, "approvers")),
        ("Policy", ("spec", "builders", "cally-ci", "sigstore")),
        ("Artifact", ("spec", "stages")),
        ("Artifact", ("spec", "stages", "qa", "verify")),
    ]:
        document = example(kind)
        target: Any = document
        for part in at[:-1]:
            target = target[part]
        target[at[-1]] = None
        assert errors(kind, document) != [], at


# --- the patterns ---------------------------------------------------------------------------------


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


USED = {
    "name", "stage name", "principal", "reporter", "OIDC issuer", "signer identity", "image",
    "source repository", "input glob", "rc version", "final version", "digest", "source commit",
    "version", "timestamp", "SBOM version", "tag prefix", "reason", "public key",
}  # fmt: skip


def test_every_published_pattern_is_a_grammar_s_or_a_format_s() -> None:
    published = {
        (s["pattern"], s["maxLength"]) for kind in KINDS for s in _string_schemas(committed(kind))
    }
    assert len(published) > 15  # calibration: the schemas were read
    assert published <= {(g.pattern, g.max_length) for g in (*GRAMMARS, *FORMATS)}


def test_every_grammar_and_format_a_model_uses_is_published() -> None:
    published = {
        (s["pattern"], s["maxLength"]) for kind in KINDS for s in _string_schemas(committed(kind))
    }
    grammars = {g.name: g for g in (*GRAMMARS, *FORMATS)}
    assert set(grammars) >= USED
    assert [
        name
        for name in sorted(USED)
        if (grammars[name].pattern, grammars[name].max_length) not in published
    ] == []


@pytest.mark.parametrize("kind", KINDS)
def test_keyed_mappings_use_property_names_never_pattern_properties(kind: str) -> None:
    text = (SCHEMAS / FILES[kind][0]).read_text(encoding="utf-8")
    assert "patternProperties" not in text


def test_the_keyed_mappings_state_their_key_pattern() -> None:
    spec = committed("Policy")["$defs"]
    approvers = spec["Stage"]["properties"]["approvers"]
    assert approvers["propertyNames"]["pattern"] == PRINCIPAL.pattern
    assert approvers["minProperties"] == 1 and "default" not in approvers
    builders = spec["PolicySpec"]["properties"]["builders"]
    assert builders["propertyNames"]["pattern"] == NAME.pattern
    assert builders["additionalProperties"]["oneOf"] == [
        {"$ref": "#/$defs/IdentitySigner"},
        {"$ref": "#/$defs/KeySigner"},
    ]
    promote = spec["Requests"]["properties"]["promote"]
    assert promote["propertyNames"]["pattern"] == STAGE.pattern


def _accepted_by_schema(grammar: Grammar, value: str) -> bool:
    """As a JSON Schema validator reads ``pattern`` and ``maxLength``: search, then length."""
    return re.search(grammar.pattern, value) is not None and len(value) <= grammar.max_length


def test_a_grammar_s_extra_rules_are_stated_where_it_is_published() -> None:
    descriptions = {
        s["pattern"]: s.get("description", "")
        for kind in KINDS
        for s in _string_schemas(committed(kind))
    }
    for grammar in (*GRAMMARS, *FORMATS):
        if grammar.extra is not None and grammar.pattern in descriptions:
            assert descriptions[grammar.pattern] == grammar.rule, grammar.name

"""The published JSON Schemas of the release files, gora.tools/v1alpha1 (ADR-026 section 11).

Each committed schema is the one generated from its model, byte for byte. The examples validate
against it, and documents GORA refuses fail it, one for each rule of one file the schema says;
documents GORA accepts pass it. Its patterns are the grammars' and the formats' patterns, every
grammar and format a model uses is published, and each published pattern and length agrees with
the checker on the samples, but for those only an extra refuses; a keyed mapping states its key
pattern under ``propertyNames``, never ``patternProperties``.
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
from gora.domain.release_repository.files import InvalidReleaseFileError, parse
from gora.domain.release_repository.formats import FORMATS
from gora.domain.release_repository.schema import DIALECT, FILES, schema_json
from tests.unit.test_identifiers import (
    ASTRAL_INVISIBLE,
    FORMAT_SAMPLES,
    HOST_254,
    SAMPLES,
    SBOM_BELOW_FLOOR,
    UNREAL_DATES,
)
from tests.unit.test_release_keys import ARMOR, UNSUPPORTED

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
PATH_OF = {
    "Policy": "release-policy.yaml",
    "Artifact": "artifacts/api/artifact.yaml",
    "StageState": "state/qa/api.yaml",
    "Rejection": "rejections/api/1.1.0.yaml",
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


def gora_refuses(kind: str, document: object) -> bool:
    try:
        parse(PATH_OF[kind], json.dumps(document).encode("utf-8"))
    except InvalidReleaseFileError:
        return True
    return False


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


QA_TWICE = ["team/qa", "team/qa"]


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
    # what the schema says of the rules of one file: no item twice in a list a rule refuses a
    # repeat in, no empty stage addition or request mapping, the approvers of a stage
    ("Policy", ("spec", "stages", 0, "verify"), ["deploy", "deploy"]),
    ("Policy", ("spec", "requests", "promote", "qa"), QA_TWICE),
    ("Policy", ("spec", "requests", "rollback", "qa"), QA_TWICE),
    ("Policy", ("spec", "requests", "reject"), QA_TWICE),
    ("Policy", ("spec", "requests", "migrate"), QA_TWICE),
    ("Artifact", ("spec", "source", "inputs"), ["src/**", "src/**"]),
    ("Artifact", ("spec", "builders"), ["cally-ci", "cally-ci"]),
    ("Artifact", ("spec", "stages", "qa", "verify"), ["contract", "contract"]),
    ("Artifact", ("spec", "stages", "qa"), {}),
    ("Policy", ("spec", "requests", "promote"), {}),
    ("Policy", ("spec", "requests", "rollback"), {}),
    ("Policy", ("spec", "stages", 0, "approvers"), {"team/qa": 1}),
    ("Policy", ("spec", "stages", 3, "approvers"), None),
]


@pytest.mark.parametrize(("kind", "at", "value"), REFUSED, ids=lambda x: str(x)[:40])
def test_a_document_gora_refuses_fails_the_schema(
    kind: str, at: tuple[str | int, ...], value: object
) -> None:
    document = _edited(example(kind), at, value)
    assert gora_refuses(kind, document)
    assert errors(kind, document) != []


ACCEPTED: list[tuple[str, list[tuple[tuple[str | int, ...], object]]]] = [
    ("Policy", [(("spec", "stages", 1, "approvers"), None)]),
    (
        "Policy",
        [
            (("spec", "stages", 3, "approvers"), None),
            (("spec", "stages", 3, "gate"), "auto"),
            (("spec", "stages", 1, "approvers"), None),
        ],
    ),
    ("Policy", [(("spec", "requests", "reject"), ["team/qa", "team/release-owners"])]),
    ("Artifact", [(("spec", "stages", "qa", "verify"), None)]),
    ("Artifact", [(("spec", "stages", "qa", "approvers"), None)]),
    ("Artifact", [(("spec", "stages"), None)]),
]


@pytest.mark.parametrize(("kind", "edits"), ACCEPTED, ids=lambda x: str(x)[:50])
def test_the_control_a_document_gora_accepts_validates(
    kind: str, edits: list[tuple[tuple[str | int, ...], object]]
) -> None:
    """The new keywords refuse nothing GORA accepts: an absent optional field, prod with an auto
    gate and no approvers, a stage addition with one of its two fields."""
    document = example(kind)
    for at, value in edits:
        document = _edited(document, at, value)
    assert not gora_refuses(kind, document)
    assert errors(kind, document) == []


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


def _accepted_by_schema(published: dict[str, Any], value: str) -> bool:
    """As a JSON Schema validator reads ``pattern`` and ``maxLength``: search, then length."""
    return (
        re.search(published["pattern"], value) is not None and len(value) <= published["maxLength"]
    )


def _published(grammar: Grammar) -> dict[str, Any]:
    """The pattern and length the committed schemas publish under the grammar's rule."""
    found = {
        (s["pattern"], s["maxLength"])
        for kind in KINDS
        for s in _string_schemas(committed(kind))
        if s.get("description") == grammar.rule
    }
    assert len(found) == 1, grammar.name
    ((pattern, max_length),) = found
    return {"pattern": pattern, "maxLength": max_length}


def _key(fixture: str) -> str:
    return fixture.removesuffix("\n")


# The samples the published pattern and length accept and the checker refuses: those only a
# grammar's or a format's extra refuses, which its description states (test below).
EXTRA_ONLY = {
    "source repository": ["github.com/a/.", "github.com/a/..", "github.com/a/x.git"],
    "input glob": [
        "/src", "src/", "a//b", ".", "a/./b", "a/../b", "a**", "**a", "***", "a/**/**/b", "]",
        "[]", "[a", "[a-]", "[-a]", "[--a]", "[a-c-e]", "[c-a]", "[a/b]",
    ],
    "OIDC issuer": ["https://a:65536", f"https://{HOST_254}"],
    "signer identity": ["https://a:65536/x", f"https://{HOST_254}/x"],
    "timestamp": UNREAL_DATES,
    "SBOM version": SBOM_BELOW_FLOOR,
    "reason": [f"a{chr(code)}b" for code in ASTRAL_INVISIBLE],
    "public key": [
        *(_key(UNSUPPORTED[name]) for name in UNSUPPORTED),
        *(_key(ARMOR[name]) for name in ("line-of-76", "short-middle-line", "padding-inside")),
    ],
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(USED))
def test_the_published_pattern_and_length_agree_with_the_checker(name: str) -> None:
    """Python's ``$`` also matches before a final newline, so a sample ending in one is left out
    (``test_published_schema`` holds that one difference)."""
    grammar = {g.name: g for g in (*GRAMMARS, *FORMATS)}[name]
    published = _published(grammar)
    valid, invalid = {**SAMPLES, **FORMAT_SAMPLES}[name]
    samples = [v for v in valid + invalid if not v.endswith("\n")]
    by_schema = {v for v in samples if _accepted_by_schema(published, v)}
    by_checker = {v for v in samples if grammar.is_valid(v)}
    assert by_checker - by_schema == set()
    assert sorted(by_schema - by_checker) == sorted(EXTRA_ONLY.get(name, []))
    assert [v for v in EXTRA_ONLY.get(name, []) if grammar.problem(v) == grammar.rule] == []
    assert len(samples) - len(by_schema) > 0  # calibration: the schema refused samples


def test_a_grammar_s_extra_rules_are_stated_where_it_is_published() -> None:
    descriptions = {
        s["pattern"]: s.get("description", "")
        for kind in KINDS
        for s in _string_schemas(committed(kind))
    }
    for grammar in (*GRAMMARS, *FORMATS):
        if grammar.extra is not None and grammar.pattern in descriptions:
            assert descriptions[grammar.pattern] == grammar.rule, grammar.name

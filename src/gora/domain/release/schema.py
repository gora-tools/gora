"""The published JSON Schema of manifest v0.1 (ADR-003, "Published contract").

Generated from the model, so the schema and the loader cannot disagree on a field, a
pattern or a length. Pure: ``manifest_schema_json`` returns the text, and the committed copy,
``contracts/schemas/release-manifest.v0.1.schema.json``, is held to it by a test. To write
it after a model change:

    uv run python -m gora.domain.release.schema \
        > contracts/schemas/release-manifest.v0.1.schema.json

The schema is draft 2020-12 and its patterns are ECMA-262 (see ``identifiers``). It
describes what one manifest may hold; the invariants that relate fields to each other (a
version maps to one digest, the candidate is on the release line) are stated in its
description and enforced by GORA, not by the schema.
"""

import json
import sys
from typing import Any

from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from gora.domain.release.manifest import ManifestVersion, ReleaseManifest

DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Descriptions for readers of the contract. The models' docstrings are written for readers
# of the code, and say how GORA enforces the rules rather than what they are.
DESCRIPTIONS = {
    "ReleaseManifest": (
        "GORA release manifest, schema v0.1: one release line, one active candidate, and for "
        "each stage the version and digest the stage holds, for a single image (ADR-003). "
        "Beyond what this schema checks, GORA refuses a manifest in which a version is "
        "recorded with two digests, or whose candidate's MAJOR.MINOR.PATCH is not the "
        "release line."
    ),
    "ReleaseCandidate": (
        "The active release candidate: one immutable artifact digest under one release "
        "identity. The image is the manifest's one image."
    ),
    "StageState": "What is deployed in one stage: a version and its digest.",
    ManifestVersion.__name__: "Manifest schema version.",
}


class _Generator(GenerateJsonSchema):
    """Pydantic's generator, with a dict's key schema kept whole under ``propertyNames``.

    Pydantic turns a patterned key into ``patternProperties``, which leaves every key that
    does not match the pattern allowed and unchecked. A stage name that breaks the grammar
    must fail validation, so the pattern goes into ``propertyNames`` and the values into
    ``additionalProperties``.
    """

    def dict_schema(self, schema: core_schema.DictSchema) -> JsonSchemaValue:
        json_schema = super().dict_schema(schema)
        patterns = json_schema.pop("patternProperties", None)
        if patterns:
            ((pattern, values),) = patterns.items()
            json_schema["additionalProperties"] = values
            json_schema["propertyNames"] = {
                **json_schema.get("propertyNames", {}),
                "pattern": pattern,
            }
        return json_schema


def manifest_schema() -> dict[str, Any]:
    """The JSON Schema of manifest v0.1, as a mapping."""
    schema = ReleaseManifest.model_json_schema(schema_generator=_Generator)
    for name, definition in schema.get("$defs", {}).items():
        definition["description"] = DESCRIPTIONS[name]
    schema["description"] = DESCRIPTIONS["ReleaseManifest"]
    schema["title"] = "GORA release manifest v0.1"
    return {"$schema": DIALECT, **schema}


def manifest_schema_json() -> str:
    """The schema as committed: sorted keys, two-space indent, ASCII, one trailing newline."""
    return json.dumps(manifest_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(manifest_schema_json())

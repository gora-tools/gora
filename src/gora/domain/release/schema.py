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

from gora.domain.release.manifest import ManifestVersion, ReleaseManifest
from gora.domain.release_repository.schema import Generator

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
    "StageState": "What one stage holds: a version and its digest.",
    ManifestVersion.__name__: "Manifest schema version.",
}


def manifest_schema() -> dict[str, Any]:
    """The JSON Schema of manifest v0.1, as a mapping."""
    schema = ReleaseManifest.model_json_schema(schema_generator=Generator)
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

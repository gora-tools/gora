"""The published JSON Schemas of the API's bodies, ``/v1`` (release-model section 11).

Generated from the models of the services that answer, with the generator of the release files'
schemas, so a schema and its model cannot disagree on a field, a pattern or a length. Pure:
``schema_json`` returns the text; the committed copies under ``contracts/schemas/api/v1/`` are
held to it by a test. To write them after a model change:

    uv run python -m gora.api.schemas contracts/schemas/api/v1

Only ``changes`` is published: the body of ``gora changes --output json``, which the API's
``GET /v1/projects/{p}/changes`` will serve (the route waits for the server's clones, ADR-006).
"""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from gora.domain.release_repository.schema import DIALECT, Generator
from gora.services.changes import Changes

SCHEMAS = "https://gora.tools/schemas/api/v1"

# The file each body's schema is published as, its model and its title.
FILES: dict[str, tuple[str, type[BaseModel], str]] = {
    "Changes": ("changes.json", Changes, "GORA changes, API v1"),
}

# Descriptions for readers of the contract, one per schema and per definition.
DESCRIPTIONS = {
    "Changes": (
        "The artifacts a commit of main is a candidate of, in name order: the body of `gora "
        "changes --output json`. project is the release repository and source the repository "
        "the artifacts are built from; commit is the commit asked about, by its full object "
        "name. Every field is present: registered is null while the artifact has no registered "
        "candidate."
    ),
    "ChangedArtifact": (
        "An artifact the commit is a candidate of: its name, the rc version the candidate "
        "takes, and its newest registered candidate, or null."
    ),
    "RegisteredCandidate": (
        "The newest registered candidate of an artifact: its rc version and the commit its rc "
        "tag names."
    ),
}


def schema(kind: str) -> dict[str, Any]:
    """The JSON Schema of one body, as a mapping."""
    file, model, title = FILES[kind]
    generated = model.model_json_schema(by_alias=True, schema_generator=Generator)
    for name, definition in generated.get("$defs", {}).items():
        definition["description"] = DESCRIPTIONS[name]
    generated["description"] = DESCRIPTIONS[kind]
    generated["title"] = title
    return {"$schema": DIALECT, "$id": f"{SCHEMAS}/{file}", **generated}


def schema_json(kind: str) -> str:
    """A schema as committed: sorted keys, two-space indent, ASCII, one trailing newline."""
    return json.dumps(schema(kind), indent=2, sort_keys=True) + "\n"


def write(directory: Path) -> None:
    """Write every schema into ``directory``."""
    for kind, (file, _, _) in FILES.items():
        (directory / file).write_text(schema_json(kind), encoding="utf-8")


if __name__ == "__main__":
    write(Path(sys.argv[1]))

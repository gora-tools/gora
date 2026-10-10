"""The published JSON Schemas of the release files, ``gora.tools/v1alpha1`` (ADR-026 section 11).

Generated from the models, so a schema and its model cannot disagree on a field, a pattern or a
length. Pure: ``schema_json`` returns the text; the committed copies under
``contracts/schemas/v1alpha1/`` are held to it by a test. To write them after a model change:

    uv run python -m gora.domain.release_repository.schema contracts/schemas/v1alpha1

The schemas are draft 2020-12 and their patterns are ECMA-262 (see ``identifiers``). The models
are the gate; a schema is their published form for editors and other tools, and the rules beyond
JSON Schema are stated in its descriptions. ``Generator`` is the schema generator of every
published schema, v0.1's included.
"""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from gora.domain.release_repository import v1alpha1

DIALECT = "https://json-schema.org/draft/2020-12/schema"


class Generator(GenerateJsonSchema):
    """Pydantic's generator, with a dict's key schema kept whole under ``propertyNames``.

    Pydantic turns a patterned key into ``patternProperties``, which leaves every key that
    does not match the pattern allowed and unchecked. A key that breaks its grammar must fail
    validation, so the pattern goes into ``propertyNames`` and the values into
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


# The file each kind's schema is published as, and its title.
FILES = {
    "Policy": ("policy.json", "GORA release policy, gora.tools/v1alpha1"),
    "Artifact": ("artifact.json", "GORA artifact declaration, gora.tools/v1alpha1"),
    "StageState": ("stage-state.json", "GORA stage state, gora.tools/v1alpha1"),
    "Rejection": ("rejection.json", "GORA rejection, gora.tools/v1alpha1"),
}

_ONE_FILE = (
    "An optional field is absent or holds a value, never null, and a mapping or list it holds "
    "is never empty. Top-level fields appear in the order apiVersion, kind, metadata, spec, "
    "writtenBy."
)
# JSON Schema's integer admits 30.0; GORA does not.
_INTEGERS = "An integer is written without a fraction: 30, never 30.0. "

# Descriptions for readers of the contract, one per schema and per definition. The models'
# docstrings are written for readers of the code.
DESCRIPTIONS = {
    "Policy": (
        "The release policy of a project, release-policy.yaml at the root of its release "
        "repository, owned by the release owners (ADR-026 section 4). Beyond what this schema "
        "checks, GORA refuses a policy in which: two stages have one name, or the last stage is "
        "not prod; a stage's verification has no reporter; requests.promote or requests.rollback "
        "does not name exactly the stages; one signer is both a builder and an attester (two "
        "keys are one signer when their key is, two identities when their issuer and identity "
        "are, whatever their instance). " + _INTEGERS + _ONE_FILE
    ),
    "PolicySpec": "The policy: stages, reporters, signers, who may ask, evidence, branch age.",
    "Stage": (
        "A stage of the promotion graph, in order: its gate, the groups that approve a "
        "promotion into it and how many reviews each gives, and its verifications."
    ),
    "IdentitySigner": (
        "A Sigstore identity: an OIDC issuer, the exact identity of a workload, and the "
        "instance whose trust root verifies it (public by default, or github). Chosen when "
        "publicKey is absent."
    ),
    "KeySigner": "A public key, in PEM: ECDSA P-256 (uncompressed point) or Ed25519.",
    "Requests": (
        "Who may ask for each action: promote and rollback per stage, naming exactly the "
        "stages; reject and migrate for the project."
    ),
    "Evidence": "The floor of the evidence a candidate carries.",
    "Sbom": "The SBOM format and its lowest version, CycloneDX 1.6 at least.",
    "Artifact": (
        "The declaration of one artifact, artifacts/<name>/artifact.yaml, owned by its team "
        "(ADR-026 section 5); metadata.name is the name in the path. Beyond what this schema "
        "checks, GORA refuses a declaration in which: tagPrefix is neither empty nor the "
        "name followed by /. " + _INTEGERS + _ONE_FILE
    ),
    "ArtifactMetadata": "The artifact's name, as its path names it.",
    "ArtifactSpec": (
        "The image, the source and its inputs, the builders, the first candidate's version, "
        "whether the major version has left 0, and what the artifact adds to stages."
    ),
    "Source": (
        "The code repository the artifact is built from, its tag prefix, and the input globs "
        "whose change makes a candidate, matched as git's glob pathspec magic."
    ),
    "StageAddition": (
        "What the artifact adds to one stage of the policy: approvers, verifications, or both."
    ),
    "StageState": (
        "What one stage holds of one artifact, state/<stage>/<artifact>.yaml, written by GORA "
        "only (ADR-026 section 6); metadata names the stage and the artifact of its path. "
        "Beyond what this schema checks, GORA refuses a version that is not the final version "
        "X.Y.Z in prod, or not the rc version X.Y.Z-rc in any other stage. " + _ONE_FILE
    ),
    "StageStateMetadata": "The stage and the artifact, as the path names them.",
    "StageStateSpec": (
        "The image and digest the stage holds, its version, the source commit it was built "
        "from, and the evidence check that let it in."
    ),
    "StageEvidence": (
        "The evidence check that let the digest in: the VSA's digest, the policy digest, the "
        "result and the time (RFC 3339, UTC)."
    ),
    "Rejection": (
        "A rejected version of one artifact, rejections/<artifact>/<version>.yaml, written by "
        "GORA only (ADR-026 section 7); metadata names the artifact and the final version of "
        "its path. Its content never changes and the file is never removed. " + _ONE_FILE
    ),
    "RejectionMetadata": "The artifact and the final version, as the path names them.",
    "RejectionSpec": "The digest rejected, under both its rc and its final version, and why.",
}


def schema(kind: str) -> dict[str, Any]:
    """The JSON Schema of one kind, as a mapping."""
    model = v1alpha1.MODELS[kind]
    file, title = FILES[kind]
    generated = model.model_json_schema(by_alias=True, schema_generator=Generator)
    for name, definition in generated.get("$defs", {}).items():
        definition["description"] = DESCRIPTIONS[name]
    generated["description"] = DESCRIPTIONS[kind]
    generated["title"] = title
    return {"$schema": DIALECT, "$id": f"{v1alpha1.SCHEMAS}/{file}", **generated}


def schema_json(kind: str) -> str:
    """A schema as committed: sorted keys, two-space indent, ASCII, one trailing newline."""
    return json.dumps(schema(kind), indent=2, sort_keys=True) + "\n"


def write(directory: Path) -> None:
    """Write the four schemas into ``directory``."""
    for kind, (file, _) in FILES.items():
        (directory / file).write_text(schema_json(kind), encoding="utf-8")


if __name__ == "__main__":
    write(Path(sys.argv[1]))

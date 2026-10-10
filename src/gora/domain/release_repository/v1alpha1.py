"""The models of the release files, schema ``gora.tools/v1alpha1`` (ADR-026 sections 3 to 7).

Pure: values in, models out. One model per kind, ``Policy``, ``Artifact``, ``StageState`` and
``Rejection``, each holding every rule one file can be checked against on its own; the rules
between files, and those of history and the host, are not here. No command uses them yet.

Every model is strict, refuses an unknown field and is frozen. Fields are named only by the
file's camelCase. An optional field is absent or holds a value, never ``null``, and a mapping
or a list it holds is never empty: it is typed as the mapping or list itself, empty by default,
with ``min_length=1``, so ``{}`` or ``[]`` in a file fails and ``null`` fails the type. Rule
code reads an empty optional field as absent. A rule broken is reported, with every other one,
as one ``release_rules`` error whose context holds ``(path, rule)`` pairs, the paths written as
the loader writes them; no rule text holds a value from the document.
"""

from collections.abc import Hashable, Iterable
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    WithJsonSchema,
    model_validator,
)
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from gora.domain.release.identifiers import (
    DIGEST,
    FINAL_VERSION,
    IMAGE,
    INPUT_GLOB,
    NAME,
    OIDC_ISSUER,
    PRINCIPAL,
    RC_VERSION,
    REPORTER,
    SIGNER_IDENTITY,
    SOURCE_COMMIT,
    SOURCE_REPOSITORY,
    STAGE,
    VERSION,
    Grammar,
)
from gora.domain.release_repository import keys
from gora.domain.release_repository.formats import (
    PUBLIC_KEY,
    REASON,
    SBOM_VERSION,
    TAG_PREFIX,
    TIMESTAMP,
)
from gora.domain.release_repository.yaml12 import PATH_KEY

API_VERSION = "gora.tools/v1alpha1"
SCHEMAS = "https://gora.tools/schemas/v1alpha1"
PROD = "prod"


def _described(grammar: Grammar) -> dict[str, Any]:
    """A grammar's published schema, its rule, extra rules included, as the description."""
    return {**grammar.json_schema, "description": grammar.rule}


Name = Annotated[str, AfterValidator(NAME.validate), WithJsonSchema(_described(NAME))]
StageName = Annotated[str, AfterValidator(STAGE.validate), WithJsonSchema(_described(STAGE))]
Principal = Annotated[
    str, AfterValidator(PRINCIPAL.validate), WithJsonSchema(_described(PRINCIPAL))
]
Reporter = Annotated[str, AfterValidator(REPORTER.validate), WithJsonSchema(_described(REPORTER))]
OidcIssuer = Annotated[
    str, AfterValidator(OIDC_ISSUER.validate), WithJsonSchema(_described(OIDC_ISSUER))
]
SignerIdentity = Annotated[
    str, AfterValidator(SIGNER_IDENTITY.validate), WithJsonSchema(_described(SIGNER_IDENTITY))
]
PublicKey = Annotated[
    str, AfterValidator(PUBLIC_KEY.validate), WithJsonSchema(_described(PUBLIC_KEY))
]
SbomVersion = Annotated[
    str, AfterValidator(SBOM_VERSION.validate), WithJsonSchema(_described(SBOM_VERSION))
]
Image = Annotated[str, AfterValidator(IMAGE.validate), WithJsonSchema(_described(IMAGE))]
SourceRepository = Annotated[
    str, AfterValidator(SOURCE_REPOSITORY.validate), WithJsonSchema(_described(SOURCE_REPOSITORY))
]
TagPrefix = Annotated[
    str, AfterValidator(TAG_PREFIX.validate), WithJsonSchema(_described(TAG_PREFIX))
]
InputGlob = Annotated[
    str, AfterValidator(INPUT_GLOB.validate), WithJsonSchema(_described(INPUT_GLOB))
]
RcVersion = Annotated[
    str, AfterValidator(RC_VERSION.validate), WithJsonSchema(_described(RC_VERSION))
]
FinalVersion = Annotated[
    str, AfterValidator(FINAL_VERSION.validate), WithJsonSchema(_described(FINAL_VERSION))
]
Digest = Annotated[str, AfterValidator(DIGEST.validate), WithJsonSchema(_described(DIGEST))]
SourceCommit = Annotated[
    str, AfterValidator(SOURCE_COMMIT.validate), WithJsonSchema(_described(SOURCE_COMMIT))
]
Timestamp = Annotated[
    str, AfterValidator(TIMESTAMP.validate), WithJsonSchema(_described(TIMESTAMP))
]
Reason = Annotated[str, AfterValidator(REASON.validate), WithJsonSchema(_described(REASON))]
Version = Annotated[str, AfterValidator(VERSION.validate), WithJsonSchema(_described(VERSION))]

STATE_VERSION = (
    "must be an rc version, MAJOR.MINOR.PATCH-rc, or a final version, MAJOR.MINOR.PATCH "
    "(SemVer 2.0.0 core, no leading zeros), at most 128 characters"
)


def _state_version(value: str) -> str:
    if RC_VERSION.is_valid(value) or FINAL_VERSION.is_valid(value):
        return value
    raise PydanticCustomError("release_identifier", STATE_VERSION)


StateVersion = Annotated[
    str,
    AfterValidator(_state_version),
    WithJsonSchema({"anyOf": [_described(RC_VERSION), _described(FINAL_VERSION)]}),
]
ApproverCount = Annotated[int, Field(ge=1, le=100)]
Principals = Annotated[list[Principal], Field(min_length=1)]


def _no_default(schema: dict[str, Any]) -> None:
    """An optional field's schema states no default: absent is absent, not a value."""
    schema.pop("default", None)


# --- the rules' texts, constant, so none can carry a value ------------------------------------

RULES_BROKEN = "the file breaks rules of its kind"
RULE_STAGE_TWICE = "names a stage that an earlier entry of spec.stages names"
RULE_LAST_STAGE = "must end with the stage prod"
RULE_AUTO_APPROVERS = "must be absent: an auto stage names no approvers"
RULE_PROD_APPROVERS = "required: prod, when its gate is approval, names its own approvers"
RULE_USER_ONE = "must be 1: a user gives one review"
RULE_NO_REPORTER = "has no reporter in spec.reporters"
RULE_TWICE = "appears earlier in the same list"
RULE_NO_PROMOTE = "has no entry in spec.requests.promote"
RULE_NO_ROLLBACK = "has no entry in spec.requests.rollback"
RULE_NOT_A_STAGE = "is not a stage of spec.stages"
RULE_SIGNER_BOTH = "is also a builder: no signer is both a builder and an attester"
RULE_TAG_PREFIX = "must be empty, or the artifact's name followed by /"
RULE_ADDITION_EMPTY = "must name approvers, verify or both"
RULE_PROD_FINAL = "must be a final version, MAJOR.MINOR.PATCH, in prod"
RULE_RC_BEFORE_PROD = "must be an rc version, MAJOR.MINOR.PATCH-rc, in a stage before prod"

Problems = list[tuple[str, str]]


def key(path: str, name: str, position: int) -> str:
    """The path of a mapping's entry: its key when the loader would show it, else its place."""
    shown = name if PATH_KEY.fullmatch(name) else f"<key {position}>"
    return f"{path}.{shown}"


def _keyed(path: str, mapping: dict[str, Any]) -> Iterable[tuple[str, str, Any]]:
    """Each entry of a mapping with its path, in the file's order."""
    for position, (name, value) in enumerate(mapping.items(), start=1):
        yield key(path, name, position), name, value


def _repeats(items: Iterable[Hashable]) -> list[int]:
    """The index of every item that an earlier item repeats."""
    seen: set[Hashable] = set()
    found = []
    for index, item in enumerate(items):
        if item in seen:
            found.append(index)
        seen.add(item)
    return found


def _repeated(path: str, items: Iterable[Hashable]) -> Problems:
    """A problem at every item of a list that an earlier item repeats."""
    return [(f"{path}[{index}]", RULE_TWICE) for index in _repeats(items)]


def _approvers(path: str, approvers: dict[str, int]) -> Problems:
    return [
        (at, RULE_USER_ONE)
        for at, name, number in _keyed(path, approvers)
        if name.startswith("user/") and number != 1
    ]


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
    )


class _File(_Model):
    """A whole release file: its rules run once every field is valid."""

    def rule_problems(self) -> Problems:
        return []

    @model_validator(mode="after")
    def _holds_its_rules(self) -> Self:
        problems = self.rule_problems()
        if problems:
            raise PydanticCustomError("release_rules", RULES_BROKEN, {"problems": tuple(problems)})
        return self


# --- Policy (ADR-026 section 4) ----------------------------------------------------------------


class Stage(_Model):
    name: StageName
    gate: Literal["auto", "approval"]
    approvers: dict[Principal, ApproverCount] = Field(
        default_factory=dict, min_length=1, json_schema_extra=_no_default
    )
    verify: list[Name] = Field(min_length=1)


class IdentitySigner(_Model):
    issuer: OidcIssuer
    identity: SignerIdentity
    sigstore: Literal["public", "github"] = "public"


class KeySigner(_Model):
    public_key: PublicKey


SIGNER_IDENTITY_TAG = "signer:identity"
SIGNER_KEY_TAG = "signer:key"
# The tags are strings no field can have, so a renderer can drop them from a path.
SIGNER_TAGS = frozenset({SIGNER_IDENTITY_TAG, SIGNER_KEY_TAG})


def _signer_kind(value: Any) -> str:
    """A key signer holds ``publicKey``; anything else is read as an identity."""
    if isinstance(value, dict) and "publicKey" in value:
        return SIGNER_KEY_TAG
    if isinstance(value, KeySigner):
        return SIGNER_KEY_TAG
    return SIGNER_IDENTITY_TAG


Signer = Annotated[
    Annotated[IdentitySigner, Tag(SIGNER_IDENTITY_TAG)] | Annotated[KeySigner, Tag(SIGNER_KEY_TAG)],
    Discriminator(_signer_kind),
]


def same_signer(a: IdentitySigner | KeySigner, b: IdentitySigner | KeySigner) -> bool:
    """Two keys are one signer when their key is; two identities when their issuer and
    identity are, whatever their instance."""
    if isinstance(a, KeySigner) and isinstance(b, KeySigner):
        return keys.same_key(a.public_key, b.public_key)
    if isinstance(a, IdentitySigner) and isinstance(b, IdentitySigner):
        return (a.issuer, a.identity) == (b.issuer, b.identity)
    return False


class Requests(_Model):
    promote: dict[StageName, Principals]
    rollback: dict[StageName, Principals]
    reject: Principals
    migrate: Principals


class Sbom(_Model):
    format: Literal["cyclonedx"]
    min_version: SbomVersion


class Evidence(_Model):
    sbom: Sbom


class PolicySpec(_Model):
    stages: list[Stage] = Field(min_length=1)
    reporters: dict[Name, Reporter] = Field(min_length=1)
    builders: dict[Name, Signer] = Field(min_length=1)
    attesters: dict[Name, Signer] = Field(min_length=1)
    requests: Requests
    evidence: Evidence
    branch_age_days: int = Field(ge=1, le=36500)


class Policy(_File):
    api_version: Literal["gora.tools/v1alpha1"]
    kind: Literal["Policy"]
    spec: PolicySpec

    def rule_problems(self) -> Problems:
        spec = self.spec
        problems: Problems = []
        names = [stage.name for stage in spec.stages]
        problems += [(f"spec.stages[{i}].name", RULE_STAGE_TWICE) for i in _repeats(names)]
        if names[-1] != PROD:
            problems.append(("spec.stages", RULE_LAST_STAGE))
        for index, stage in enumerate(spec.stages):
            at = f"spec.stages[{index}]"
            if stage.gate == "auto" and stage.approvers:
                problems.append((f"{at}.approvers", RULE_AUTO_APPROVERS))
            if stage.name == PROD and stage.gate == "approval" and not stage.approvers:
                problems.append((f"{at}.approvers", RULE_PROD_APPROVERS))
            problems += _approvers(f"{at}.approvers", stage.approvers)
            problems += _repeated(f"{at}.verify", stage.verify)
            problems += [
                (f"{at}.verify[{j}]", RULE_NO_REPORTER)
                for j, name in enumerate(stage.verify)
                if name not in spec.reporters
            ]
        requests = spec.requests
        for field, missing in (("promote", RULE_NO_PROMOTE), ("rollback", RULE_NO_ROLLBACK)):
            entries: dict[str, list[str]] = getattr(requests, field)
            problems += [
                (f"spec.stages[{index}].name", missing)
                for index, name in enumerate(names)
                if name not in entries
            ]
            for at, name, principals in _keyed(f"spec.requests.{field}", entries):
                if name not in names:
                    problems.append((at, RULE_NOT_A_STAGE))
                problems += _repeated(at, principals)
        problems += _repeated("spec.requests.reject", requests.reject)
        problems += _repeated("spec.requests.migrate", requests.migrate)
        problems += [
            (at, RULE_SIGNER_BOTH)
            for at, _, attester in _keyed("spec.attesters", spec.attesters)
            if any(same_signer(builder, attester) for builder in spec.builders.values())
        ]
        return problems


# --- Artifact (ADR-026 section 5) --------------------------------------------------------------


class ArtifactMetadata(_Model):
    name: Name


class Source(_Model):
    repository: SourceRepository
    tag_prefix: TagPrefix
    inputs: list[InputGlob] = Field(min_length=1)


class StageAddition(_Model):
    approvers: dict[Principal, ApproverCount] = Field(
        default_factory=dict, min_length=1, json_schema_extra=_no_default
    )
    verify: list[Name] = Field(default_factory=list, min_length=1, json_schema_extra=_no_default)


class ArtifactSpec(_Model):
    image: Image
    source: Source
    builders: list[Name] = Field(min_length=1)
    initial_version: RcVersion
    stable: bool
    stages: dict[StageName, StageAddition] = Field(
        default_factory=dict, min_length=1, json_schema_extra=_no_default
    )


class Artifact(_File):
    api_version: Literal["gora.tools/v1alpha1"]
    kind: Literal["Artifact"]
    metadata: ArtifactMetadata
    spec: ArtifactSpec

    def rule_problems(self) -> Problems:
        spec = self.spec
        problems: Problems = []
        if spec.source.tag_prefix not in ("", f"{self.metadata.name}/"):
            problems.append(("spec.source.tagPrefix", RULE_TAG_PREFIX))
        problems += _repeated("spec.source.inputs", spec.source.inputs)
        problems += _repeated("spec.builders", spec.builders)
        for at, _, addition in _keyed("spec.stages", spec.stages):
            if not addition.approvers and not addition.verify:
                problems.append((at, RULE_ADDITION_EMPTY))
            problems += _approvers(f"{at}.approvers", addition.approvers)
            problems += _repeated(f"{at}.verify", addition.verify)
        return problems


# --- StageState (ADR-026 section 6) ------------------------------------------------------------


class StageStateMetadata(_Model):
    stage: StageName
    artifact: Name


class StageEvidence(_Model):
    vsa: Digest
    policy: Digest
    result: Literal["passed"]
    time: Timestamp


class StageStateSpec(_Model):
    image: Image
    digest: Digest
    version: StateVersion
    source_commit: SourceCommit
    evidence: StageEvidence


class StageState(_File):
    MODELINE: ClassVar[str] = f"{SCHEMAS}/stage-state.json"

    api_version: Literal["gora.tools/v1alpha1"]
    kind: Literal["StageState"]
    metadata: StageStateMetadata
    spec: StageStateSpec
    written_by: Version

    def rule_problems(self) -> Problems:
        version = self.spec.version
        if self.metadata.stage == PROD and not FINAL_VERSION.is_valid(version):
            return [("spec.version", RULE_PROD_FINAL)]
        if self.metadata.stage != PROD and not RC_VERSION.is_valid(version):
            return [("spec.version", RULE_RC_BEFORE_PROD)]
        return []

    def to_document(self) -> dict[str, object]:
        """The file's content in the writer's order, for ``canonical.dump``."""
        document: dict[str, object] = self.model_dump(mode="json", by_alias=True)
        return document


# --- Rejection (ADR-026 section 7) -------------------------------------------------------------


class RejectionMetadata(_Model):
    artifact: Name
    version: FinalVersion


class RejectionSpec(_Model):
    digest: Digest
    reason: Reason


class Rejection(_File):
    MODELINE: ClassVar[str] = f"{SCHEMAS}/rejection.json"

    api_version: Literal["gora.tools/v1alpha1"]
    kind: Literal["Rejection"]
    metadata: RejectionMetadata
    spec: RejectionSpec
    written_by: Version

    def to_document(self) -> dict[str, object]:
        """The file's content in the writer's order, for ``canonical.dump``."""
        document: dict[str, object] = self.model_dump(mode="json", by_alias=True)
        return document


MODELS: dict[str, type[_File]] = {
    "Policy": Policy,
    "Artifact": Artifact,
    "StageState": StageState,
    "Rejection": Rejection,
}

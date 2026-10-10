"""The models of the release files and their parsing (ADR-026 sections 3 to 7).

Every rule of every model has its own test at its own path; each step of ``parse`` is tested in
order. Expected paths and rules are written out literally. No message ever holds a value from
the document, and a key only when the loader's key pattern admits it: sentinels placed in every
field and every mapping show it.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from gora.domain.release.identifiers import NAME, PRINCIPAL, STAGE
from gora.domain.release_repository import canonical, files, v1alpha1, yaml12
from gora.domain.release_repository.files import (
    InvalidReleaseFileError,
    Location,
    Problem,
    locate,
    parse,
)
from gora.domain.release_repository.formats import REASON
from gora.domain.release_repository.v1alpha1 import (
    RULE_ADDITION_EMPTY,
    RULE_AUTO_APPROVERS,
    RULE_LAST_STAGE,
    RULE_NO_PROMOTE,
    RULE_NO_REPORTER,
    RULE_NO_ROLLBACK,
    RULE_NOT_A_STAGE,
    RULE_PROD_APPROVERS,
    RULE_PROD_FINAL,
    RULE_RC_BEFORE_PROD,
    RULE_SIGNER_BOTH,
    RULE_STAGE_TWICE,
    RULE_TAG_PREFIX,
    RULE_TWICE,
    RULE_USER_ONE,
)
from tests.unit.test_release_keys import P256, P256_OTHER

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "v1alpha1"
PATHS = {
    "policy": "release-policy.yaml",
    "artifact": "artifacts/api/artifact.yaml",
    "stage-state": "state/qa/api.yaml",
    "rejection": "rejections/api/1.1.0.yaml",
}
HEX = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DELETE = object()
Path_ = tuple[str | int, ...]


def example(name: str) -> dict[str, Any]:
    document = yaml12.load((EXAMPLES / f"{name}.yaml").read_bytes(), source=name)
    assert isinstance(document, dict)
    return document


def edited(document: dict[str, Any], at: Path_, value: object) -> dict[str, Any]:
    """A copy of ``document`` with the value at ``at`` replaced; ``DELETE`` removes it."""
    changed = copy.deepcopy(document)
    target: Any = changed
    for part in at[:-1]:
        target = target[part]
    if value is DELETE:
        del target[at[-1]]
    else:
        target[at[-1]] = value
    return changed


def text(document: object) -> bytes:
    """A document as a file: JSON is YAML 1.2, and carries floats the writer refuses."""
    return json.dumps(document).encode("utf-8")


def problems(name: str, document: object, path: str | None = None) -> list[tuple[str, str]]:
    with pytest.raises(InvalidReleaseFileError) as refused:
        parse(path or PATHS[name], text(document))
    return [(problem.path, problem.rule) for problem in refused.value.problems]


def holds(name: str, document: object, path: str | None = None) -> files.ReleaseFile:
    return parse(path or PATHS[name], text(document))


# --- the examples ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PATHS))
def test_each_example_parses_at_its_path(name: str) -> None:
    release_file = parse(PATHS[name], (EXAMPLES / f"{name}.yaml").read_bytes())
    assert type(release_file.model) is v1alpha1.MODELS[release_file.location.kind]
    assert release_file.document == example(name)


def test_the_document_is_kept_as_loaded_with_no_default_filled_in() -> None:
    release_file = parse(PATHS["policy"], (EXAMPLES / "policy.yaml").read_bytes())
    model = release_file.model
    assert isinstance(model, v1alpha1.Policy)
    builder = model.spec.builders["cally-ci"]
    assert isinstance(builder, v1alpha1.IdentitySigner) and builder.sigstore == "public"
    spec: Any = release_file.document["spec"]
    assert "sigstore" not in spec["builders"]["cally-ci"]
    assert model.spec.stages[0].approvers == {}  # absent reads as empty
    assert "approvers" not in spec["stages"][0]
    assert canonical.policy_digest(release_file.document, example("artifact")).startswith("sha256:")


# --- Policy rules (ADR-026 section 4) -------------------------------------------------------------

POLICY = example("policy")
ARTIFACT = example("artifact")
STAGE_STATE = example("stage-state")
REJECTION = example("rejection")
STAGES = ("spec", "stages")


def test_a_stage_name_is_unique() -> None:
    stages = [*POLICY["spec"]["stages"], POLICY["spec"]["stages"][3]]
    assert problems("policy", edited(POLICY, STAGES, stages)) == [
        ("spec.stages[4].name", RULE_STAGE_TWICE)
    ]


def test_the_last_stage_is_prod() -> None:
    dev, qa, staging, prod = POLICY["spec"]["stages"]
    assert problems("policy", edited(POLICY, STAGES, [dev, qa, prod, staging])) == [
        ("spec.stages", RULE_LAST_STAGE)
    ]


def test_an_auto_stage_names_no_approvers() -> None:
    document = edited(POLICY, (*STAGES, 0, "approvers"), {"team/qa": 1})
    assert problems("policy", document) == [("spec.stages[0].approvers", RULE_AUTO_APPROVERS)]


def test_prod_with_an_approval_gate_names_its_approvers() -> None:
    document = edited(POLICY, (*STAGES, 3, "approvers"), DELETE)
    assert problems("policy", document) == [("spec.stages[3].approvers", RULE_PROD_APPROVERS)]


def test_prod_with_an_auto_gate_and_another_approval_stage_may_name_none() -> None:
    document = edited(POLICY, (*STAGES, 3, "approvers"), DELETE)
    document = edited(document, (*STAGES, 3, "gate"), "auto")
    document = edited(document, (*STAGES, 1, "approvers"), DELETE)
    assert isinstance(holds("policy", document).model, v1alpha1.Policy)


def test_a_user_approver_gives_one_review() -> None:
    document = edited(POLICY, (*STAGES, 1, "approvers"), {"team/qa": 2, "user/octocat": 2})
    assert problems("policy", document) == [("spec.stages[1].approvers.<key 2>", RULE_USER_ONE)]
    holds("policy", edited(POLICY, (*STAGES, 1, "approvers"), {"user/octocat": 1}))


def test_every_verification_of_a_stage_has_a_reporter() -> None:
    document = edited(POLICY, (*STAGES, 0, "verify"), ["deploy", "unknown"])
    assert problems("policy", document) == [("spec.stages[0].verify[1]", RULE_NO_REPORTER)]


def test_a_reporter_no_stage_names_is_allowed() -> None:
    assert "contract" in POLICY["spec"]["reporters"]
    assert all("contract" not in stage["verify"] for stage in POLICY["spec"]["stages"])
    holds("policy", POLICY)


def test_a_stage_names_a_verification_once() -> None:
    document = edited(POLICY, (*STAGES, 0, "verify"), ["deploy", "deploy"])
    assert problems("policy", document) == [("spec.stages[0].verify[1]", RULE_TWICE)]


@pytest.mark.parametrize(
    ("field", "rule"), [("promote", RULE_NO_PROMOTE), ("rollback", RULE_NO_ROLLBACK)]
)
def test_a_stage_missing_from_requests_is_named_by_its_place(field: str, rule: str) -> None:
    document = edited(POLICY, ("spec", "requests", field, "qa"), DELETE)
    assert problems("policy", document) == [("spec.stages[1].name", rule)]


@pytest.mark.parametrize("field", ["promote", "rollback"])
def test_a_request_key_that_is_not_a_stage_is_named_at_its_key(field: str) -> None:
    document = edited(POLICY, ("spec", "requests", field, "uat"), ["team/qa"])
    assert problems("policy", document) == [(f"spec.requests.{field}.uat", RULE_NOT_A_STAGE)]


@pytest.mark.parametrize(
    "at",
    [
        ("spec", "requests", "promote", "dev"),
        ("spec", "requests", "rollback", "prod"),
        ("spec", "requests", "reject"),
        ("spec", "requests", "migrate"),
    ],
    ids=lambda at: ".".join(map(str, at)),
)
def test_a_list_of_principals_names_one_principal_once(at: Path_) -> None:
    document = edited(POLICY, at, ["team/qa", "team/qa"])
    assert problems("policy", document) == [(".".join(map(str, at)) + "[1]", RULE_TWICE)]


@pytest.mark.parametrize(
    "at",
    [
        ("spec", "requests", "promote", "dev"),
        ("spec", "requests", "rollback", "prod"),
        ("spec", "requests", "reject"),
        ("spec", "requests", "migrate"),
    ],
    ids=lambda at: ".".join(map(str, at)),
)
def test_a_list_of_principals_is_never_empty(at: Path_) -> None:
    document = edited(POLICY, at, [])
    assert problems("policy", document) == [(".".join(map(str, at)), "must not be empty")]


@pytest.mark.parametrize("field", ["reject", "migrate"])
def test_reject_and_migrate_are_required(field: str) -> None:
    document = edited(POLICY, ("spec", "requests", field), DELETE)
    assert problems("policy", document) == [(f"spec.requests.{field}", "required")]


IDENTITY = {
    "issuer": "https://token.actions.githubusercontent.com",
    "identity": "https://github.com/acme/cally-ci/.github/workflows/build.yml@refs/heads/main",
}


@pytest.mark.parametrize(
    "attester",
    [
        IDENTITY,
        {**IDENTITY, "sigstore": "github"},  # whatever the instance
    ],
    ids=["identity", "identity-other-instance"],
)
def test_no_identity_is_both_a_builder_and_an_attester(attester: dict[str, str]) -> None:
    document = edited(POLICY, ("spec", "attesters", "gora-pipeline"), attester)
    assert problems("policy", document) == [("spec.attesters.gora-pipeline", RULE_SIGNER_BOTH)]


def test_no_key_is_both_a_builder_and_an_attester() -> None:
    document = edited(
        POLICY, ("spec", "builders", "key-ci"), {"publicKey": P256.removesuffix("\n")}
    )
    assert problems("policy", document) == [("spec.attesters.gora-pipeline", RULE_SIGNER_BOTH)]


def test_two_builders_or_two_attesters_may_share_a_signer() -> None:
    document = edited(POLICY, ("spec", "builders", "cally-ci-2"), IDENTITY)
    document = edited(document, ("spec", "attesters", "gora-2"), {"publicKey": P256})
    holds("policy", document)


def test_a_different_key_or_identity_is_another_signer() -> None:
    document = edited(POLICY, ("spec", "builders", "key-ci"), {"publicKey": P256_OTHER})
    other = {**IDENTITY, "identity": IDENTITY["identity"].replace("main", "release")}
    document = edited(document, ("spec", "attesters", "other"), other)
    holds("policy", document)


# --- signers --------------------------------------------------------------------------------------


def test_the_signer_variants() -> None:
    document = edited(POLICY, ("spec", "builders", "gh"), {**IDENTITY, "sigstore": "github"})
    model = holds("policy", document).model
    assert isinstance(model, v1alpha1.Policy)
    assert isinstance(model.spec.builders["gh"], v1alpha1.IdentitySigner)
    assert isinstance(model.spec.attesters["gora-pipeline"], v1alpha1.KeySigner)


def test_a_signer_with_both_a_key_and_an_issuer_is_refused() -> None:
    document = edited(POLICY, ("spec", "attesters", "gora-pipeline", "issuer"), IDENTITY["issuer"])
    assert problems("policy", document) == [
        ("spec.attesters.gora-pipeline.issuer", "unknown field")
    ]


def test_an_identity_signer_names_its_missing_fields_without_a_variant() -> None:
    document = edited(POLICY, ("spec", "builders", "cally-ci"), {"sigstore": "public"})
    assert problems("policy", document) == [
        ("spec.builders.cally-ci.issuer", "required"),
        ("spec.builders.cally-ci.identity", "required"),
    ]


@pytest.mark.parametrize(
    ("identity", "valid"),
    [
        ("https://github.com/acme/x/.github/workflows/b.yml@refs/heads/main", True),
        ("ci@acme.iam.gserviceaccount.com", False),
        ("spiffe://acme/ci", False),
        ("https://github.com/acme/*", False),
    ],
)
def test_an_identity_is_an_https_uri(identity: str, valid: bool) -> None:
    document = edited(POLICY, ("spec", "builders", "cally-ci", "identity"), identity)
    if valid:
        holds("policy", document)
    else:
        assert [path for path, _ in problems("policy", document)] == [
            "spec.builders.cally-ci.identity"
        ]


# --- Artifact rules (ADR-026 section 5) -----------------------------------------------------------


@pytest.mark.parametrize(("prefix", "valid"), [("", True), ("api/", True), ("web/", False)])
def test_a_tag_prefix_is_empty_or_the_artifact_s_name(prefix: str, valid: bool) -> None:
    document = edited(ARTIFACT, ("spec", "source", "tagPrefix"), prefix)
    if valid:
        holds("artifact", document)
    else:
        assert problems("artifact", document) == [("spec.source.tagPrefix", RULE_TAG_PREFIX)]


def test_an_input_is_named_once() -> None:
    document = edited(ARTIFACT, ("spec", "source", "inputs"), ["src/**", "pom.xml", "src/**"])
    assert problems("artifact", document) == [("spec.source.inputs[2]", RULE_TWICE)]


def test_inputs_are_never_empty() -> None:
    document = edited(ARTIFACT, ("spec", "source", "inputs"), [])
    assert problems("artifact", document) == [("spec.source.inputs", "must not be empty")]


def test_a_builder_is_named_once() -> None:
    document = edited(ARTIFACT, ("spec", "builders"), ["cally-ci", "cally-ci"])
    assert problems("artifact", document) == [("spec.builders[1]", RULE_TWICE)]


def test_stages_when_present_hold_an_entry() -> None:
    assert problems("artifact", edited(ARTIFACT, ("spec", "stages"), {})) == [
        ("spec.stages", "must not be empty")
    ]
    holds("artifact", edited(ARTIFACT, ("spec", "stages"), DELETE))


def test_a_stage_addition_names_approvers_verify_or_both() -> None:
    assert problems("artifact", edited(ARTIFACT, ("spec", "stages", "qa"), {})) == [
        ("spec.stages.qa", RULE_ADDITION_EMPTY)
    ]
    holds("artifact", edited(ARTIFACT, ("spec", "stages", "qa", "verify"), DELETE))
    holds("artifact", edited(ARTIFACT, ("spec", "stages", "qa", "approvers"), DELETE))


def test_a_stage_addition_s_user_approver_gives_one_review() -> None:
    document = edited(ARTIFACT, ("spec", "stages", "qa", "approvers"), {"user/octocat": 3})
    assert problems("artifact", document) == [("spec.stages.qa.approvers.<key 1>", RULE_USER_ONE)]


def test_a_stage_addition_names_a_verification_once() -> None:
    document = edited(ARTIFACT, ("spec", "stages", "qa", "verify"), ["contract", "contract"])
    assert problems("artifact", document) == [("spec.stages.qa.verify[1]", RULE_TWICE)]


def test_an_approver_number_is_1_to_100() -> None:
    at = ("spec", "stages", "qa", "approvers")
    assert problems("artifact", edited(ARTIFACT, at, {"team/qa": 0})) == [
        ("spec.stages.qa.approvers.<key 1>", "must be at least 1")
    ]
    assert problems("artifact", edited(ARTIFACT, at, {"team/qa": 101})) == [
        ("spec.stages.qa.approvers.<key 1>", "must be at most 100")
    ]
    holds("artifact", edited(ARTIFACT, at, {"team/qa": 100}))


def test_branch_age_days_is_1_to_36500() -> None:
    at = ("spec", "branchAgeDays")
    assert problems("policy", edited(POLICY, at, 0)) == [(".".join(at), "must be at least 1")]
    assert problems("policy", edited(POLICY, at, 36501)) == [
        (".".join(at), "must be at most 36500")
    ]


# --- StageState and Rejection ---------------------------------------------------------------------


def test_a_stage_before_prod_holds_an_rc_version() -> None:
    document = edited(STAGE_STATE, ("spec", "version"), "1.4.0")
    assert problems("stage-state", document) == [("spec.version", RULE_RC_BEFORE_PROD)]


def test_prod_holds_a_final_version() -> None:
    in_prod = edited(STAGE_STATE, ("metadata", "stage"), "prod")
    assert problems("stage-state", in_prod, "state/prod/api.yaml") == [
        ("spec.version", RULE_PROD_FINAL)
    ]
    holds("stage-state", edited(in_prod, ("spec", "version"), "1.4.0"), "state/prod/api.yaml")


@pytest.mark.parametrize("version", ["1.4.0-rc.1", "v1.4.0", "1.4"])
def test_a_state_version_is_an_rc_or_a_final_version(version: str) -> None:
    document = edited(STAGE_STATE, ("spec", "version"), version)
    assert problems("stage-state", document) == [("spec.version", v1alpha1.STATE_VERSION)]


def test_a_state_result_is_passed_only() -> None:
    document = edited(STAGE_STATE, ("spec", "evidence", "result"), "failed")
    assert problems("stage-state", document) == [("spec.evidence.result", "must be one of: passed")]


def test_a_reason_with_a_bidirectional_control_is_refused() -> None:
    document = edited(REJECTION, ("spec", "reason"), f"login {chr(0x202E)}fails")
    assert problems("rejection", document) == [("spec.reason", REASON.rule)]


# --- metadata against the path, reported with the rules -------------------------------------------


@pytest.mark.parametrize(
    ("name", "at", "value", "path", "rule"),
    [
        (
            "artifact",
            ("metadata", "name"),
            "web",
            "metadata.name",
            "must be the name in the path, artifacts/<name>/artifact.yaml",
        ),
        (
            "stage-state",
            ("metadata", "stage"),
            "dev",
            "metadata.stage",
            "must be the stage in the path, state/<stage>/<artifact>.yaml",
        ),
        (
            "stage-state",
            ("metadata", "artifact"),
            "web",
            "metadata.artifact",
            "must be the artifact in the path, state/<stage>/<artifact>.yaml",
        ),
        (
            "rejection",
            ("metadata", "artifact"),
            "web",
            "metadata.artifact",
            "must be the artifact in the path, rejections/<artifact>/<version>.yaml",
        ),
        (
            "rejection",
            ("metadata", "version"),
            "1.2.0",
            "metadata.version",
            "must be the version in the path, rejections/<artifact>/<version>.yaml",
        ),
    ],
)
def test_metadata_names_what_the_path_names(
    name: str, at: Path_, value: str, path: str, rule: str
) -> None:
    assert problems(name, edited(example(name), at, value)) == [(path, rule)]


def test_metadata_and_rules_are_reported_together() -> None:
    document = edited(ARTIFACT, ("metadata", "name"), "web")
    document = edited(document, ("spec", "builders"), ["cally-ci", "cally-ci"])
    assert [p for p, _ in problems("artifact", document)] == ["metadata.name", "spec.builders[1]"]


def test_metadata_is_checked_only_once_every_field_holds() -> None:
    document = edited(ARTIFACT, ("metadata", "name"), "web")
    document = edited(document, ("spec", "stable"), "no")
    assert problems("artifact", document) == [("spec.stable", "must be true or false")]


# --- strictness, unknown fields, null -------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "at", "value", "rule"),
    [
        ("policy", ("spec", "stages", 1, "approvers", "team/qa"), "1", "must be an integer"),
        ("policy", ("spec", "stages", 1, "approvers", "team/qa"), True, "must be an integer"),
        ("policy", ("spec", "branchAgeDays"), "30", "must be an integer"),
        ("policy", ("spec", "branchAgeDays"), 30.0, "must be an integer"),
        ("artifact", ("spec", "stable"), "true", "must be true or false"),
        ("artifact", ("spec", "stable"), 1, "must be true or false"),
        ("artifact", ("spec", "stable"), 0.0, "must be true or false"),
        ("artifact", ("spec", "image"), 1, "must be a string"),
        ("artifact", ("spec", "image"), 1.0, "must be a string"),
        ("artifact", ("spec", "builders"), 1.0, "must be a list"),
        ("artifact", ("spec", "stages"), 1.0, "must be a mapping"),
        ("artifact", ("spec", "source"), 1.0, "must be a mapping"),
        ("policy", ("spec", "evidence", "sbom", "minVersion"), 1.6, "must be a string"),
        ("rejection", ("writtenBy",), 1.0, "must be a string"),
    ],
    ids=lambda x: str(x) if not isinstance(x, tuple) else ".".join(map(str, x)),
)
def test_values_are_read_strictly(name: str, at: Path_, value: object, rule: str) -> None:
    found = problems(name, edited(example(name), at, value))
    assert [r for _, r in found] == [rule]


def _mappings(node: object, at: Path_ = ()) -> list[Path_]:
    """The path of every mapping in a document, the root included."""
    found: list[Path_] = []
    if isinstance(node, dict):
        found.append(at)
        for key, value in node.items():
            found += _mappings(value, (*at, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _mappings(value, (*at, index))
    return found


KEYED = {"approvers", "reporters", "builders", "attesters", "promote", "rollback"}


def _model_mappings(name: str) -> list[Path_]:
    """Every mapping a model reads, not a mapping keyed by names (approvers, reporters...)."""
    return [
        at
        for at in _mappings(example(name))
        if not (at and at[-1] in KEYED) and not (name == "artifact" and at == ("spec", "stages"))
    ]


MODEL_MAPPINGS = {name: _model_mappings(name) for name in PATHS}


@pytest.mark.parametrize(
    ("name", "at"),
    [(name, at) for name in sorted(PATHS) for at in MODEL_MAPPINGS[name]],
    ids=lambda x: ".".join(map(str, x)) if isinstance(x, tuple) else x,
)
def test_an_unknown_field_is_refused_at_every_level(name: str, at: Path_) -> None:
    document = edited(example(name), (*at, "unknownField"), "x")
    found = problems(name, document)
    expected = ".".join(f"[{p}]" if isinstance(p, int) else p for p in (*at, "unknownField"))
    assert found == [(expected.replace(".[", "["), "unknown field")]


OPTIONAL = [
    ("policy", (*STAGES, 1, "approvers"), "must be a mapping"),
    ("policy", ("spec", "builders", "cally-ci", "sigstore"), "must be one of: public, github"),
    ("artifact", ("spec", "stages"), "must be a mapping"),
    ("artifact", ("spec", "stages", "qa", "approvers"), "must be a mapping"),
    ("artifact", ("spec", "stages", "qa", "verify"), "must be a list"),
]


@pytest.mark.parametrize(("name", "at", "rule"), OPTIONAL, ids=lambda x: str(x))
def test_null_is_refused_for_every_optional_field(name: str, at: Path_, rule: str) -> None:
    found = problems(name, edited(example(name), at, None))
    assert found == [
        (".".join(f"[{p}]" if isinstance(p, int) else p for p in at).replace(".[", "["), rule)
    ]


# --- the layout -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "location"),
    [
        ("release-policy.yaml", Location("Policy")),
        ("artifacts/api/artifact.yaml", Location("Artifact", artifact="api")),
        ("state/qa/api.yaml", Location("StageState", stage="qa", artifact="api")),
        ("state/prod/db-migrate.yaml", Location("StageState", stage="prod", artifact="db-migrate")),
        ("rejections/api/1.1.0.yaml", Location("Rejection", artifact="api", version="1.1.0")),
    ],
)
def test_locate_every_file_of_the_layout(path: str, location: Location) -> None:
    assert locate(path) == location


@pytest.mark.parametrize(
    "path",
    [
        "artifacts/api/other.yaml",
        "artifacts/Api/artifact.yaml",
        "artifacts/api/x/artifact.yaml",
        "artifacts/artifact.yaml",
        "artifacts/api",
        "artifacts/README.md",
        "state/qa/api.yml",
        "state/QA/api.yaml",
        "state/qa/sub/api.yaml",
        "state/qa.yaml",
        "state/qa/.yaml",
        "state/qa/api",
        "state/qa/Api.yaml",
        "rejections/api/1.1.0-rc.yaml",
        "rejections/api/v1.1.0.yaml",
        "rejections/api/1.1.yaml",
        "rejections/api.yaml",
        "rejections/api/1.1.0.yaml/x",
    ],
)
def test_any_other_path_under_the_layout_is_refused(path: str) -> None:
    with pytest.raises(InvalidReleaseFileError) as refused:
        locate(path)
    assert [(p.source, p.path, p.rule) for p in refused.value.problems] == [
        (path, "$", files.RULE_LAYOUT)
    ]


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "release-policy.yml",
        "release-manifest.yaml",
        "src/state/qa/api.yaml",
        ".github/CODEOWNERS",
        "artifacts",
        "statex/qa/api.yaml",
        "State/qa/api.yaml",
    ],
)
def test_a_path_that_is_not_a_release_file_is_none(path: str) -> None:
    assert locate(path) is None


def test_a_refused_path_is_shown_escaped() -> None:
    with pytest.raises(InvalidReleaseFileError) as refused:
        locate("state/qa/a\x1b[31m\u00e9\U0001f600\\.yml")
    assert refused.value.problems[0].source == "state/qa/a\\x1b[31m\\xe9\\U0001f600\\\\.yml"
    assert str(refused.value).isascii()


# --- parse, step by step --------------------------------------------------------------------------


def test_step_1_a_path_that_is_not_a_release_file_is_the_caller_s_error() -> None:
    with pytest.raises(ValueError, match="not a release file") as refused:
        parse("README.md", b"x: 1\n")
    assert not isinstance(refused.value, InvalidReleaseFileError)


def test_step_1_a_refused_path_comes_before_its_content() -> None:
    with pytest.raises(InvalidReleaseFileError) as refused:
        parse("state/qa/api.yml", b"[not: yaml")
    assert refused.value.problems[0].rule == files.RULE_LAYOUT


def test_step_2_a_loader_error_keeps_its_line_and_column() -> None:
    with pytest.raises(InvalidReleaseFileError) as refused:
        parse(PATHS["rejection"], b"apiVersion: a\napiVersion: b\n")
    (problem,) = refused.value.problems
    assert problem == Problem(PATHS["rejection"], "<key 2>", 2, 1, yaml12.RULE_DUPLICATE_KEY)
    assert str(problem) == f"{PATHS['rejection']}:2:1: <key 2>: {yaml12.RULE_DUPLICATE_KEY}"


def test_step_3_the_document_is_a_mapping() -> None:
    assert problems("rejection", ["apiVersion"]) == [("$", "must be a mapping")]


def test_step_4_the_schema_version_comes_first() -> None:
    other = {"writtenBy": "0.4.0", "spec": {}, "kind": "Policy", "apiVersion": "gora.tools/v2"}
    assert problems("rejection", other) == [
        (
            "apiVersion",
            "schema version gora.tools/v2 is not supported by this GORA (it supports "
            "gora.tools/v1alpha1); written by GORA 0.4.0",
        )
    ]


def test_step_4_a_missing_schema_version_is_required() -> None:
    assert problems("artifact", edited(ARTIFACT, ("apiVersion",), DELETE)) == [
        ("apiVersion", "required")
    ]


NOT_SUPPORTED = (
    "schema version gora.tools/v1beta2 is not supported by this GORA (it supports "
    "gora.tools/v1alpha1)"
)
NOT_NAMED = "is not a schema version this GORA supports (it supports gora.tools/v1alpha1)"


@pytest.mark.parametrize(
    ("name", "written_by", "rule"),
    [
        ("policy", None, NOT_SUPPORTED),
        ("artifact", None, NOT_SUPPORTED),
        ("policy", "0.4.0", NOT_SUPPORTED),  # only GORA's own files carry writtenBy
        ("stage-state", "0.4.0", NOT_SUPPORTED + "; written by GORA 0.4.0"),
        ("rejection", "0.4.0", NOT_SUPPORTED + "; written by GORA 0.4.0"),
        ("rejection", "0.4.0-rc.1", NOT_SUPPORTED + "; written by GORA 0.4.0-rc.1"),
        ("rejection", "0.4", NOT_SUPPORTED),
        ("rejection", 4, NOT_SUPPORTED),
        ("rejection", None, NOT_SUPPORTED),
    ],
)
def test_step_4_an_unsupported_schema_version_is_named(
    name: str, written_by: object, rule: str
) -> None:
    document = edited(example(name), ("apiVersion",), "gora.tools/v1beta2")
    document["writtenBy"] = written_by
    if written_by is None:
        del document["writtenBy"]
    assert problems(name, document) == [("apiVersion", rule)]


@pytest.mark.parametrize(
    "value",
    ["gora.tools/v2\n", "gora.tools/v0", "gora.tools/v1alpha0", "v1alpha1", "x/v1", 1, None, []],
    ids=repr,
)
def test_step_4_any_other_value_is_not_named(value: object) -> None:
    assert problems("policy", edited(POLICY, ("apiVersion",), value)) == [("apiVersion", NOT_NAMED)]


def test_step_5_the_top_level_order() -> None:
    reordered = {
        key: STAGE_STATE[key] for key in ("apiVersion", "metadata", "kind", "spec", "writtenBy")
    }
    assert problems("stage-state", reordered) == [("kind", files.RULE_ORDER)]
    last = {"writtenBy": "0.3.0", **{k: v for k, v in REJECTION.items() if k != "writtenBy"}}
    assert problems("rejection", last) == [("apiVersion", files.RULE_ORDER)]


def test_step_5_an_unknown_top_level_field_may_stand_anywhere_and_is_refused_later() -> None:
    document = {"x-note": 1, **POLICY}
    assert problems("policy", document) == [("x-note", "unknown field")]


@pytest.mark.parametrize("kind", ["Artifact", "Policy", "stagestate", 1, None])
def test_step_6_the_kind_is_the_one_the_path_says(kind: object) -> None:
    document = edited(STAGE_STATE, ("kind",), kind)
    assert problems("stage-state", document) == [("kind", "must be StageState, as the path says")]


def test_step_6_a_missing_kind_is_required() -> None:
    assert problems("stage-state", edited(STAGE_STATE, ("kind",), DELETE)) == [("kind", "required")]


def test_step_6_comes_before_the_fields_and_after_the_order() -> None:
    wrong = edited(STAGE_STATE, ("kind",), "Rejection")
    assert problems("stage-state", edited(wrong, ("spec", "digest"), "x")) == [
        ("kind", "must be StageState, as the path says")
    ]
    reordered = {"kind": "Rejection", **{k: v for k, v in wrong.items() if k != "kind"}}
    assert problems("stage-state", reordered) == [("apiVersion", files.RULE_ORDER)]


def test_step_7_every_field_problem_together_in_a_stable_order() -> None:
    document = edited(STAGE_STATE, ("spec", "digest"), "x")
    document = edited(document, ("spec", "sourceCommit"), "y")
    document = edited(document, ("metadata", "stage"), "Q")
    expected = [
        ("metadata.stage", STAGE.rule),
        ("spec.digest", "must be sha256: followed by exactly 64 lowercase hex digits"),
        (
            "spec.sourceCommit",
            "must be a git object name: 40 (SHA-1) or 64 (SHA-256) lower-case hex digits",
        ),
    ]
    assert problems("stage-state", document) == expected
    assert problems("stage-state", document) == expected


def test_a_problem_renders_with_and_without_a_position() -> None:
    assert str(Problem("f.yaml", "spec.x", 3, 7, "required")) == "f.yaml:3:7: spec.x: required"
    assert str(Problem("f.yaml", "spec.x", None, None, "required")) == "f.yaml: spec.x: required"
    error = InvalidReleaseFileError(
        (Problem("f", "a", None, None, "r1"), Problem("f", "b", None, None, "r2"))
    )
    assert str(error) == "f: a: r1\nf: b: r2"
    assert isinstance(error, ValueError)


# --- paths and keys -------------------------------------------------------------------------------


def test_a_key_the_loader_would_not_show_is_named_by_its_place() -> None:
    document = edited(POLICY, (*STAGES, 1, "approvers"), {"team/qa": 1, "Team/QA": 1})
    assert problems("policy", document) == [("spec.stages[1].approvers.<key 2>", PRINCIPAL.rule)]


def test_a_key_the_loader_would_show_is_named() -> None:
    document = edited(POLICY, ("spec", "reporters", "Smoke"), "app/x")
    assert problems("policy", document) == [("spec.reporters.Smoke", NAME.rule)]


def test_render_path_drops_pydantic_s_markers() -> None:
    signer = ("spec", "builders", "cally-ci", "signer:identity", "issuer")
    assert files.render_path(POLICY, signer) == "spec.builders.cally-ci.issuer"
    key = ("spec", "stages", 1, "approvers", "team/qa", "[key]")
    assert files.render_path(POLICY, key) == "spec.stages[1].approvers.<key 1>"
    assert files.render_path(POLICY, ()) == "$"


# --- no value in a message ------------------------------------------------------------------------

SENTINEL = "Zq9SENTINELzq9"
SENTINEL_KEY = "Sentinel Key/Zq9"


def _leaves(node: object, at: Path_ = ()) -> list[Path_]:
    found: list[Path_] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += _leaves(value, (*at, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _leaves(value, (*at, index))
    else:
        found.append(at)
    return found


def _messages(name: str, document: object) -> str | None:
    try:
        parse(PATHS[name], text(document))
    except InvalidReleaseFileError as refused:
        return str(refused)
    return None


def test_a_sentinel_value_in_any_field_appears_in_no_message() -> None:
    refused = 0
    for name in sorted(PATHS):
        for at in _leaves(example(name)):
            message = _messages(name, edited(example(name), at, SENTINEL))
            if message is not None:
                refused += 1
                assert SENTINEL not in message, (name, at)
    assert refused > 60  # calibration: the sentinel was refused, and its message read


def test_a_sentinel_key_in_any_mapping_appears_in_no_message() -> None:
    assert not yaml12._PATH_KEY.fullmatch(SENTINEL_KEY)
    checked = 0
    for name in sorted(PATHS):
        for at in _mappings(example(name)):
            message = _messages(name, edited(example(name), (*at, SENTINEL_KEY), 1))
            assert message is not None, (name, at)
            assert "Zq9" not in message and "<key " in message, (name, at)
            checked += 1
    assert checked > 30


def test_a_sentinel_schema_version_is_not_named() -> None:
    message = _messages("policy", edited(POLICY, ("apiVersion",), SENTINEL))
    assert message is not None and SENTINEL not in message


# --- every error type GORA can meet has its own rule ----------------------------------------------

JSON_VALUES: list[object] = [None, True, 1, 1.5, "s", [], {}, ["s"], {"k": "v"}]


def test_every_field_fed_every_json_type_gets_a_rule_of_gora_s_table() -> None:
    rules: set[str] = set()
    for name in sorted(PATHS):
        document = example(name)
        paths = {at[:depth] for at in _leaves(document) for depth in range(1, len(at) + 1)}
        for at in sorted(paths, key=str):
            for value in JSON_VALUES:
                try:
                    parse(PATHS[name], text(edited(document, at, value)))
                except InvalidReleaseFileError as refused:
                    rules |= {problem.rule for problem in refused.problems}
    assert len(rules) > 30  # calibration: many refusals were rendered
    assert [r for r in rules if r.startswith("invalid (")] == []


def test_an_error_type_outside_the_table_renders_as_invalid() -> None:
    assert (
        files.rule({"type": "forced_unknown", "msg": "x", "ctx": {}}) == "invalid (forced_unknown)"
    )


# --- writing and reading back StageState and Rejection --------------------------------------------

STAGE_STATE_BYTES = f"""\
# yaml-language-server: $schema=https://gora.tools/schemas/v1alpha1/stage-state.json
apiVersion: gora.tools/v1alpha1
kind: StageState
metadata:
  stage: qa
  artifact: api
spec:
  image: ghcr.io/acme/cally-api
  digest: sha256:{HEX}
  version: 1.4.0-rc
  sourceCommit: 9fceb02d0ae598e95dc970b74767f19372d61af8
  evidence:
    vsa: sha256:2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae
    policy: sha256:fcde2b2edba56bf408601fb721fe9b5c338d10ee429ea04fae5511b68fbf8fb9
    result: passed
    time: "2026-10-09T12:00:00Z"
writtenBy: 0.3.0
""".encode()
REJECTION_BYTES = f"""\
# yaml-language-server: $schema=https://gora.tools/schemas/v1alpha1/rejection.json
apiVersion: gora.tools/v1alpha1
kind: Rejection
metadata:
  artifact: api
  version: 1.1.0
spec:
  digest: sha256:{HEX}
  reason: "login fails on Safari"
writtenBy: 0.3.0
""".encode()


@pytest.mark.parametrize(
    ("name", "expected"),
    [("stage-state", STAGE_STATE_BYTES), ("rejection", REJECTION_BYTES)],
    ids=["stage-state", "rejection"],
)
def test_written_canonically_and_read_back_equal(name: str, expected: bytes) -> None:
    model = holds(name, example(name)).model
    assert isinstance(model, v1alpha1.StageState | v1alpha1.Rejection)
    written = canonical.dump(model.to_document(), modeline=model.MODELINE).encode("utf-8")
    assert written == expected
    assert (EXAMPLES / f"{name}.yaml").read_bytes() == expected  # the examples are canonical
    assert parse(PATHS[name], written).model == model


def test_to_document_is_in_the_writer_s_order() -> None:
    model = holds("stage-state", STAGE_STATE).model
    assert isinstance(model, v1alpha1.StageState)
    document = model.to_document()
    assert list(document) == ["apiVersion", "kind", "metadata", "spec", "writtenBy"]
    spec = document["spec"]
    assert isinstance(spec, dict)
    assert list(spec) == ["image", "digest", "version", "sourceCommit", "evidence"]

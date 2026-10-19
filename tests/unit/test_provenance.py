"""Provenance in the one form GORA accepts, its binding and its level (ADR-018 sections 2 and
3, ADR-027 section 7).

What is held: a statement counts only in the form of GitHub's ``workflow/v1`` build type, with
no field missing and none added at any depth, and only when it describes the build that the
artifact's declaration, the host's repository identifier and the image's revision describe;
``resolvedDependencies[0].uri`` is checked whole. Signed by a Sigstore identity, it must also
agree with the builder's certificate, which the host issued: ``.13``, ``.14``, ``.15``, the
identity, the workflow that ran (``.18``) and the runner (``.11``). Signed by a key, the
statement's own facts are compared with the host and the image, and ``builder.id`` is read for
its form only. The level is data: ``SLSA_BUILD_LEVEL_3`` only for a Sigstore identity that is a
reusable workflow on a GitHub-hosted runner, ``SLSA_BUILD_LEVEL_2`` otherwise, a key always.

The fixture is a statement GitHub's toolkit wrote, not one written here, so the checker is
proven against a producer it does not control: the workflow ``release.yml`` of
``dani-garcia/vaultwarden``, which at the commit the statement names (``58a980f8``) calls
``actions/attest@1e69f48a`` (v4.2.2), on a push to ``main``, for the image index
``ghcr.io/dani-garcia/vaultwarden@sha256:67f0bd65…``; fetched on 2026-10-19 and kept as the
Sigstore bundle whose subject is that image. Its workflow signs from the top level, not from a
reusable workflow, so the real statement gives ``SLSA_BUILD_LEVEL_2`` with its certificate, and
with a key; the level-3 case below is a mutation of it, synthetic and named so, until GORA's own
``build.yml`` gives a real one. ``CERTIFICATE`` holds the fields of the bundle's Fulcio
certificate as read from it; the adapter that verifies the signature maps a certificate onto the
same type. Expectations are written out literally.
"""

import base64
import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gora.domain.evidence import certificate as cert
from gora.domain.evidence.certificate import Certificate
from gora.domain.evidence.provenance import (
    ANOTHER_BUILD_CONFIG,
    ANOTHER_BUILDER,
    ANOTHER_COMMIT,
    ANOTHER_DEPENDENCY,
    ANOTHER_DIGEST,
    ANOTHER_IMAGE,
    ANOTHER_REPOSITORY_ID,
    ANOTHER_RUN,
    ANOTHER_RUNNER,
    ANOTHER_SOURCE,
    MISSING,
    NOT_A_BUILDER,
    NOT_A_COMMIT,
    NOT_A_DIGEST,
    NOT_A_REPOSITORY_URL,
    NOT_A_RUN,
    NOT_A_RUNNER,
    NOT_A_WORKFLOW_PATH,
    NOT_AN_ID,
    NOT_AN_IMAGE,
    NOT_JSON,
    NOT_MAIN,
    NOT_OBJECT,
    NOT_PUSH,
    NOT_STRING,
    ONE_ENTRY,
    UNKNOWN,
    WRONG_BUILD_TYPE,
    WRONG_PREDICATE_TYPE,
    WRONG_STATEMENT_TYPE,
    BuildLevel,
    EvidenceError,
    Expected,
    RunnerEnvironment,
    build_level,
    read_statement,
    verify,
)
from gora.domain.release_repository.v1alpha1 import IdentitySigner

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "provenance" / "vaultwarden.sigstore.json"
)
PAYLOAD_SHA256 = "ca241206469f262487686e9bf72efc606aa4d940fad41cbc7e8a98598f17b398"

ISSUER = "https://token.actions.githubusercontent.com"
WORKFLOW = (
    "https://github.com/dani-garcia/vaultwarden/.github/workflows/release.yml@refs/heads/main"
)
REUSABLE = "https://github.com/dani-garcia/vaultwarden/.github/workflows/build.yml@refs/heads/main"
COMMIT = "58a980f850464f7d9cdc99a765df36f0a3308b91"
DIGEST = "sha256:67f0bd65017c20acfb4c3b531c81eadc065cbb343eeb1b6b9e0390d1ecd6738d"
OTHER_COMMIT = "a" * 40
SHARED = "https://github.com/dani-garcia/workflows/.github/workflows/release.yml@refs/heads/main"

CERTIFICATE = Certificate(
    issuer=ISSUER,
    identity=WORKFLOW,
    source_commit=COMMIT,
    source_ref="refs/heads/main",
    repository_id="121898717",
    build_config=WORKFLOW,
    runner_environment="github-hosted",
)
KEY = None


def signer(identity: str) -> IdentitySigner:
    return IdentitySigner.model_validate({"issuer": ISSUER, "identity": identity})


EXPECTED = Expected(
    image="ghcr.io/dani-garcia/vaultwarden",
    digest=DIGEST,
    repository="github.com/dani-garcia/vaultwarden",
    repository_id="121898717",
    commit=COMMIT,
    builders=(signer(WORKFLOW),),
)

W = "predicate.buildDefinition.externalParameters.workflow"
G = "predicate.buildDefinition.internalParameters.github"
R = "predicate.buildDefinition.resolvedDependencies[0]"
DEFINITION = ("predicate", "buildDefinition")
WORKFLOW_KEYS = (*DEFINITION, "externalParameters", "workflow")
GITHUB_KEYS = (*DEFINITION, "internalParameters", "github")
DEPENDENCY_KEYS = (*DEFINITION, "resolvedDependencies", 0)


def payload() -> bytes:
    bundle = json.loads(FIXTURE.read_bytes())
    assert bundle["dsseEnvelope"]["payloadType"] == "application/vnd.in-toto+json"
    return base64.b64decode(bundle["dsseEnvelope"]["payload"], validate=True)


def document() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(payload())
    return loaded


def mutated(change: Callable[[dict[str, Any]], None]) -> bytes:
    doc = copy.deepcopy(document())
    change(doc)
    return json.dumps(doc).encode()


def at(doc: Any, keys: tuple[str | int, ...]) -> Any:
    for key in keys:
        doc = doc[key]
    return doc


def setting(keys: tuple[str | int, ...], value: Any) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        at(doc, keys[:-1])[keys[-1]] = value

    return change


def adding(keys: tuple[str | int, ...], key: str) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        at(doc, keys)[key] = "x"

    return change


def removing(keys: tuple[str | int, ...]) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        del at(doc, keys[:-1])[keys[-1]]

    return change


def problems(
    data: bytes, certificate: Certificate | None = CERTIFICATE, expected: Expected = EXPECTED
) -> list[tuple[str, str]]:
    with pytest.raises(EvidenceError) as caught:
        verify(data, certificate, expected)
    return caught.value.problems


# --- the real statement ------------------------------------------------------------------------


def test_the_fixture_is_the_statement_the_toolkit_signed() -> None:
    assert hashlib.sha256(payload()).hexdigest() == PAYLOAD_SHA256


def test_the_real_statement_counts_for_its_build_at_level_2() -> None:
    verified = verify(payload(), CERTIFICATE, EXPECTED)
    statement = verified.statement
    provenance = statement.provenance
    assert statement.name == "ghcr.io/dani-garcia/vaultwarden"
    assert statement.digest == DIGEST
    assert provenance.workflow.repository == "https://github.com/dani-garcia/vaultwarden"
    assert provenance.workflow.path == ".github/workflows/release.yml"
    assert provenance.workflow.ref == "refs/heads/main"
    assert provenance.event == "push"
    assert provenance.repository_id == "121898717"
    assert provenance.repository_owner_id == "725423"
    assert provenance.runner_environment is RunnerEnvironment.GITHUB_HOSTED
    assert provenance.dependency == "git+https://github.com/dani-garcia/vaultwarden@refs/heads/main"
    assert provenance.commit == COMMIT
    assert provenance.builder == WORKFLOW
    assert provenance.invocation == (
        "https://github.com/dani-garcia/vaultwarden/actions/runs/38883327219/attempts/1"
    )
    # The workflow signs from the top level: not reusable, so level 2 on a GitHub-hosted runner.
    assert provenance.reusable is False
    assert verified.level is BuildLevel.L2


def test_the_real_statement_signed_by_a_key_counts_at_level_2() -> None:
    verified = verify(payload(), KEY, replace(EXPECTED, builders=()))
    assert verified.statement.provenance.commit == COMMIT
    assert verified.level is BuildLevel.L2


# --- the form ----------------------------------------------------------------------------------

FORM = [
    pytest.param(
        setting(("_type",), "https://in-toto.io/Statement/v0.1"),
        [("_type", WRONG_STATEMENT_TYPE)],
        id="statement-type",
    ),
    pytest.param(
        setting(("predicateType",), "https://slsa.dev/provenance/v0.2"),
        [("predicateType", WRONG_PREDICATE_TYPE)],
        id="predicate-type",
    ),
    pytest.param(
        setting(
            (*DEFINITION, "buildType"),
            "https://slsa-framework.github.io/github-actions-buildtypes/workflow/v1",
        ),
        [("predicate.buildDefinition.buildType", WRONG_BUILD_TYPE)],
        id="build-type",
    ),
    pytest.param(
        setting(("subject", 0, "name"), "ghcr.io/dani-garcia/vaultwarden:testing"),
        [("subject[0].name", NOT_AN_IMAGE)],
        id="subject-name-form",
    ),
    pytest.param(
        setting(("subject", 0, "digest", "sha256"), "67F0" + "0" * 60),
        [("subject[0].digest.sha256", NOT_A_DIGEST)],
        id="subject-digest-form",
    ),
    pytest.param(
        setting((*WORKFLOW_KEYS, "ref"), "refs/heads/feature"),
        [(f"{W}.ref", NOT_MAIN)],
        id="workflow-ref",
    ),
    pytest.param(
        setting((*WORKFLOW_KEYS, "ref"), "refs/tags/1.35.0"),
        [(f"{W}.ref", NOT_MAIN)],
        id="workflow-ref-tag",
    ),
    pytest.param(
        setting((*WORKFLOW_KEYS, "repository"), "https://github.com/dani-garcía/vaultwarden"),
        [(f"{W}.repository", NOT_A_REPOSITORY_URL)],
        id="workflow-repository-not-ascii",
    ),
    pytest.param(
        setting((*WORKFLOW_KEYS, "repository"), "https://gitlab.com/dani-garcia/vaultwarden"),
        [(f"{W}.repository", NOT_A_REPOSITORY_URL)],
        id="workflow-repository-host",
    ),
    pytest.param(
        setting((*WORKFLOW_KEYS, "path"), "release.yml"),
        [(f"{W}.path", NOT_A_WORKFLOW_PATH)],
        id="workflow-path",
    ),
    pytest.param(
        setting((*GITHUB_KEYS, "event_name"), "workflow_dispatch"),
        [(f"{G}.event_name", NOT_PUSH)],
        id="event",
    ),
    pytest.param(
        setting((*GITHUB_KEYS, "event_name"), "pull_request"),
        [(f"{G}.event_name", NOT_PUSH)],
        id="event-pr",
    ),
    pytest.param(
        setting((*GITHUB_KEYS, "repository_id"), "0121898717"),
        [(f"{G}.repository_id", NOT_AN_ID)],
        id="repository-id-form",
    ),
    pytest.param(
        setting((*GITHUB_KEYS, "repository_owner_id"), "dani-garcia"),
        [(f"{G}.repository_owner_id", NOT_AN_ID)],
        id="owner-id-form",
    ),
    pytest.param(
        setting((*GITHUB_KEYS, "runner_environment"), "github-hosted-large"),
        [(f"{G}.runner_environment", NOT_A_RUNNER)],
        id="runner",
    ),
    pytest.param(
        setting((*DEPENDENCY_KEYS, "digest", "gitCommit"), "58A980F8"),
        [(f"{R}.digest.gitCommit", NOT_A_COMMIT)],
        id="commit-form",
    ),
    pytest.param(
        setting(
            ("predicate", "runDetails", "metadata", "invocationId"),
            "https://github.com/dani-garcia/vaultwarden/actions/runs/38883327219",
        ),
        [("predicate.runDetails.metadata.invocationId", NOT_A_RUN)],
        id="run-form",
    ),
    pytest.param(
        setting(
            ("predicate", "runDetails", "builder", "id"),
            "https://github.com/dani-garcia/vaultwarden",
        ),
        [("predicate.runDetails.builder.id", NOT_A_BUILDER)],
        id="builder-not-a-workflow",
    ),
    pytest.param(
        setting(
            ("predicate", "runDetails", "builder", "id"),
            WORKFLOW.replace("vaultwarden/", "vaultwarden\u212a/"),
        ),
        [("predicate.runDetails.builder.id", NOT_A_BUILDER)],
        id="builder-out-of-ascii",
    ),
    pytest.param(
        adding((), "x\nsubject[0].name: ok\x1b[2J"),
        [("<key 4>", UNKNOWN)],
        id="unknown-key-shown-by-its-place",
    ),
    # Every object holds exactly its fields: an unknown one is refused, at any depth.
    pytest.param(adding((), "signature"), [("signature", UNKNOWN)], id="unknown-statement-field"),
    pytest.param(
        adding(("subject", 0, "digest"), "sha512"),
        [("subject[0].digest.sha512", UNKNOWN)],
        id="second-subject-digest",
    ),
    pytest.param(
        adding(("predicate",), "extra"),
        [("predicate.extra", UNKNOWN)],
        id="unknown-predicate-field",
    ),
    pytest.param(
        adding((*DEFINITION, "externalParameters"), "inputs"),
        [("predicate.buildDefinition.externalParameters.inputs", UNKNOWN)],
        id="unknown-external-parameter",
    ),
    pytest.param(
        adding(WORKFLOW_KEYS, "sha"), [(f"{W}.sha", UNKNOWN)], id="unknown-workflow-field"
    ),
    pytest.param(
        adding((*DEFINITION, "internalParameters"), "gitlab"),
        [("predicate.buildDefinition.internalParameters.gitlab", UNKNOWN)],
        id="unknown-internal-parameter",
    ),
    pytest.param(
        adding(GITHUB_KEYS, "actor"), [(f"{G}.actor", UNKNOWN)], id="unknown-github-field"
    ),
    pytest.param(
        adding(DEPENDENCY_KEYS, "name"), [(f"{R}.name", UNKNOWN)], id="unknown-dependency-field"
    ),
    pytest.param(
        adding((*DEPENDENCY_KEYS, "digest"), "sha256"),
        [(f"{R}.digest.sha256", UNKNOWN)],
        id="second-dependency-digest",
    ),
    pytest.param(
        adding(("predicate", "runDetails"), "byproducts"),
        [("predicate.runDetails.byproducts", UNKNOWN)],
        id="byproducts",
    ),
    pytest.param(
        adding(("predicate", "runDetails", "builder"), "version"),
        [("predicate.runDetails.builder.version", UNKNOWN)],
        id="builder-version",
    ),
    pytest.param(
        adding(("predicate", "runDetails", "metadata"), "startedOn"),
        [("predicate.runDetails.metadata.startedOn", UNKNOWN)],
        id="started-on",
    ),
    pytest.param(removing((*WORKFLOW_KEYS, "path")), [(f"{W}.path", MISSING)], id="missing-path"),
    pytest.param(
        removing(("predicate", "runDetails", "metadata")),
        [("predicate.runDetails.metadata", MISSING)],
        id="missing-metadata",
    ),
    pytest.param(removing(("subject",)), [("subject", MISSING)], id="missing-subject"),
    pytest.param(
        lambda doc: doc["subject"].append(copy.deepcopy(doc["subject"][0])),
        [("subject", ONE_ENTRY)],
        id="two-subjects",
    ),
    pytest.param(setting(("subject",), []), [("subject", ONE_ENTRY)], id="no-subject"),
    pytest.param(
        lambda doc: at(doc, DEFINITION)["resolvedDependencies"].append(
            {"uri": "x", "digest": {"gitCommit": "y"}}
        ),
        [("predicate.buildDefinition.resolvedDependencies", ONE_ENTRY)],
        id="two-dependencies",
    ),
    pytest.param(
        setting((*GITHUB_KEYS, "repository_id"), 121898717),
        [(f"{G}.repository_id", NOT_STRING)],
        id="number-not-string",
    ),
    pytest.param(
        setting(("predicate", "runDetails", "builder"), WORKFLOW),
        [("predicate.runDetails.builder", NOT_OBJECT)],
        id="string-not-object",
    ),
]


@pytest.mark.parametrize(("change", "expected"), FORM)
def test_a_statement_out_of_form_is_refused_by_its_form(
    change: Callable[[dict[str, Any]], None], expected: list[tuple[str, str]]
) -> None:
    assert problems(mutated(change)) == expected


def test_the_form_is_judged_before_the_binding() -> None:
    def change(doc: dict[str, Any]) -> None:
        at(doc, WORKFLOW_KEYS)["path"] = "release.yml"
        at(doc, DEPENDENCY_KEYS)["digest"]["gitCommit"] = OTHER_COMMIT

    assert problems(mutated(change)) == [(f"{W}.path", NOT_A_WORKFLOW_PATH)]


def test_every_problem_of_the_form_is_listed() -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["_type"] = "x"
        at(doc, GITHUB_KEYS)["event_name"] = "schedule"
        at(doc, GITHUB_KEYS)["runner_environment"] = "x"

    assert problems(mutated(change)) == [
        ("_type", WRONG_STATEMENT_TYPE),
        (f"{G}.event_name", NOT_PUSH),
        (f"{G}.runner_environment", NOT_A_RUNNER),
    ]


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"\xff" + payload(), id="not-utf-8"),
        pytest.param(b"[]", id="not-an-object-at-the-top"),
        pytest.param(b'{"_type": NaN}', id="nan"),
        pytest.param(b"not json", id="not-json"),
        pytest.param(b"[" * 100_000 + b"]" * 100_000, id="nested-too-deep"),
        pytest.param(payload().replace(b'"_type":', b'"_type":"x","_type":', 1), id="a-key-twice"),
    ],
)
def test_a_payload_that_is_not_one_json_object_is_refused(data: bytes) -> None:
    expected = [("", NOT_OBJECT)] if data == b"[]" else [("", NOT_JSON)]
    with pytest.raises(EvidenceError) as caught:
        read_statement(data)
    assert caught.value.problems == expected


# --- the binding -------------------------------------------------------------------------------

BINDING = [
    pytest.param(
        setting(("subject", 0, "name"), "ghcr.io/dani-garcia/other"),
        [("subject[0].name", ANOTHER_IMAGE)],
        id="subject-name",
    ),
    pytest.param(
        setting(("subject", 0, "digest", "sha256"), "ab" * 32),
        [("subject[0].digest.sha256", ANOTHER_DIGEST)],
        id="subject-digest",
    ),
    pytest.param(
        setting((*WORKFLOW_KEYS, "repository"), "https://github.com/dani-garcia/vaultwarden2"),
        [(f"{W}.repository", ANOTHER_SOURCE), (W, ANOTHER_BUILD_CONFIG)],
        id="workflow-repository",
    ),
    pytest.param(
        setting((*GITHUB_KEYS, "repository_id"), "121898718"),
        [(f"{G}.repository_id", ANOTHER_REPOSITORY_ID)],
        id="repository-id",
    ),
    pytest.param(
        setting(
            (*DEPENDENCY_KEYS, "uri"),
            "git+https://github.com/dani-garcia/vaultwarden@refs/heads/dev",
        ),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-ref",
    ),
    pytest.param(
        setting(
            (*DEPENDENCY_KEYS, "uri"), "git+https://github.com/dani-garcia/other@refs/heads/main"
        ),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-repository",
    ),
    pytest.param(
        setting(
            (*DEPENDENCY_KEYS, "uri"), "https://github.com/dani-garcia/vaultwarden@refs/heads/main"
        ),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-without-git-plus",
    ),
    pytest.param(
        setting((*DEPENDENCY_KEYS, "uri"), "git+https://github.com/dani-garcia/vaultwarden"),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-without-ref",
    ),
    pytest.param(
        setting(
            (*DEPENDENCY_KEYS, "uri"),
            "git+https://github.com/dani-garcia/vaultwarden@refs/heads/main@refs/heads/main",
        ),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-twice",
    ),
    pytest.param(
        setting(
            (*DEPENDENCY_KEYS, "uri"),
            "git+https://github.com/dani-garcia/vaultwarden@refs/heads/main ",
        ),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-trailing-space",
    ),
    pytest.param(
        setting((*DEPENDENCY_KEYS, "digest", "gitCommit"), OTHER_COMMIT),
        [(f"{R}.digest.gitCommit", ANOTHER_COMMIT)],
        id="commit",
    ),
    pytest.param(
        setting(
            ("predicate", "runDetails", "metadata", "invocationId"),
            "https://github.com/dani-garcia/other/actions/runs/1/attempts/1",
        ),
        [("predicate.runDetails.metadata.invocationId", ANOTHER_RUN)],
        id="run-of-another-repository",
    ),
    pytest.param(
        setting(("predicate", "runDetails", "builder", "id"), REUSABLE),
        [("predicate.runDetails.builder.id", ANOTHER_BUILDER)],
        id="builder",
    ),
]


@pytest.mark.parametrize(("change", "expected"), BINDING)
def test_a_statement_of_another_build_is_refused_by_its_binding(
    change: Callable[[dict[str, Any]], None], expected: list[tuple[str, str]]
) -> None:
    assert problems(mutated(change)) == expected


KEY_BINDING = [
    pytest.param(
        setting((*GITHUB_KEYS, "repository_id"), "121898718"),
        [(f"{G}.repository_id", ANOTHER_REPOSITORY_ID)],
        id="repository-id",
    ),
    pytest.param(
        setting(
            (*DEPENDENCY_KEYS, "uri"),
            "git+https://github.com/dani-garcia/vaultwarden@refs/heads/dev",
        ),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-ref",
    ),
    pytest.param(
        setting(
            (*DEPENDENCY_KEYS, "uri"),
            "git+https://github.com/dani-garcia/other@refs/heads/main",
        ),
        [(f"{R}.uri", ANOTHER_DEPENDENCY)],
        id="dependency-repository",
    ),
    pytest.param(
        setting((*DEPENDENCY_KEYS, "digest", "gitCommit"), OTHER_COMMIT),
        [(f"{R}.digest.gitCommit", ANOTHER_COMMIT)],
        id="commit",
    ),
    pytest.param(
        setting((*WORKFLOW_KEYS, "repository"), "https://github.com/dani-garcia/vaultwarden2"),
        [(f"{W}.repository", ANOTHER_SOURCE)],
        id="workflow-repository",
    ),
    pytest.param(
        setting(
            ("predicate", "runDetails", "metadata", "invocationId"),
            "https://github.com/dani-garcia/other/actions/runs/1/attempts/1",
        ),
        [("predicate.runDetails.metadata.invocationId", ANOTHER_RUN)],
        id="run-of-another-repository",
    ),
    pytest.param(
        setting(("subject", 0, "digest", "sha256"), "ab" * 32),
        [("subject[0].digest.sha256", ANOTHER_DIGEST)],
        id="subject-digest",
    ),
]


@pytest.mark.parametrize(("change", "expected"), KEY_BINDING)
def test_a_key_s_statement_of_another_build_is_refused_by_the_host_and_the_image(
    change: Callable[[dict[str, Any]], None], expected: list[tuple[str, str]]
) -> None:
    assert problems(mutated(change), KEY) == expected


@pytest.mark.parametrize(
    "builder",
    [
        pytest.param(REUSABLE, id="another-file"),
        pytest.param(SHARED, id="another-repository"),
        pytest.param(REUSABLE.replace("refs/heads/main", "a" * 40), id="a-commit"),
    ],
)
def test_a_key_s_builder_id_is_read_for_its_form_and_changes_no_verdict(builder: str) -> None:
    change = setting(("predicate", "runDetails", "builder", "id"), builder)
    verified = verify(mutated(change), KEY, EXPECTED)
    assert verified.statement.provenance.reusable is True
    assert verified.level is BuildLevel.L2


def test_a_key_s_builder_id_out_of_form_is_refused() -> None:
    change = setting(("predicate", "runDetails", "builder", "id"), "a key")
    assert problems(mutated(change), KEY) == [("predicate.runDetails.builder.id", NOT_A_BUILDER)]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            setting((*WORKFLOW_KEYS, "repository"), "https://github.com/Dani-Garcia/VaultWarden"),
            id="workflow-repository",
        ),
        pytest.param(
            setting(
                (*DEPENDENCY_KEYS, "uri"),
                "git+https://github.com/Dani-Garcia/VaultWarden@refs/heads/main",
            ),
            id="dependency",
        ),
        pytest.param(
            setting(
                ("predicate", "runDetails", "metadata", "invocationId"),
                "https://github.com/DANI-GARCIA/vaultwarden/actions/runs/38883327219/attempts/1",
            ),
            id="run",
        ),
    ],
)
def test_a_repository_s_owner_and_name_are_compared_without_case(
    change: Callable[[dict[str, Any]], None],
) -> None:
    assert verify(mutated(change), KEY, EXPECTED).level is BuildLevel.L2


def test_the_workflow_that_ran_is_the_certificate_s_exactly() -> None:
    """Its case too: the statement repeats what the host issued, from the same token."""
    change = setting((*WORKFLOW_KEYS, "repository"), "https://github.com/Dani-Garcia/VaultWarden")
    assert problems(mutated(change)) == [(W, ANOTHER_BUILD_CONFIG)]


def test_the_ref_of_a_dependency_is_compared_with_case() -> None:
    change = setting(
        (*DEPENDENCY_KEYS, "uri"), "git+https://github.com/dani-garcia/vaultwarden@refs/heads/Main"
    )
    assert problems(mutated(change)) == [(f"{R}.uri", ANOTHER_DEPENDENCY)]


def test_a_name_out_of_ascii_never_matches_the_declaration_s() -> None:
    """The KELVIN SIGN lowers to ``k``: it would name a declaration's ``k`` if a name were
    lowered before its grammar is checked."""
    expected = replace(EXPECTED, repository="github.com/dani-garcia/vaultwardenk")

    def change(doc: dict[str, Any]) -> None:
        at(doc, WORKFLOW_KEYS)["repository"] = "https://github.com/dani-garcia/vaultwardenk"
        at(doc, DEPENDENCY_KEYS)["uri"] = (
            "git+https://github.com/dani-garcia/vaultwarden\u212a@refs/heads/main"
        )
        doc["predicate"]["runDetails"]["metadata"]["invocationId"] = (
            "https://github.com/dani-garcia/vaultwardenk/actions/runs/38883327219/attempts/1"
        )

    assert problems(mutated(change), KEY, expected) == [(f"{R}.uri", ANOTHER_DEPENDENCY)]


CERTIFICATES = [
    pytest.param(
        replace(CERTIFICATE, issuer="https://token.actions.githubusercontent.com/"),
        [("certificate.identity", cert.NOT_A_SIGNER)],
        id="issuer",
    ),
    pytest.param(
        replace(CERTIFICATE, identity=REUSABLE),
        [
            ("predicate.runDetails.builder.id", ANOTHER_BUILDER),
            ("certificate.identity", cert.NOT_A_SIGNER),
        ],
        id="identity",
    ),
    pytest.param(
        replace(CERTIFICATE, source_commit=OTHER_COMMIT),
        [("certificate.sourceRepositoryDigest", cert.ANOTHER_COMMIT)],
        id="source-commit",
    ),
    pytest.param(
        replace(CERTIFICATE, source_commit=""),
        [("certificate.sourceRepositoryDigest", cert.ANOTHER_COMMIT)],
        id="source-commit-absent",
    ),
    # The dependency is bound to the certificate's ref, not only to main.
    pytest.param(
        replace(CERTIFICATE, source_ref="refs/heads/dev"),
        [(f"{R}.uri", ANOTHER_DEPENDENCY), ("certificate.sourceRepositoryRef", cert.NOT_MAIN)],
        id="source-ref",
    ),
    pytest.param(
        replace(CERTIFICATE, source_ref=""),
        [(f"{R}.uri", ANOTHER_DEPENDENCY), ("certificate.sourceRepositoryRef", cert.NOT_MAIN)],
        id="source-ref-absent",
    ),
    pytest.param(
        replace(CERTIFICATE, repository_id="121898718"),
        [("certificate.sourceRepositoryIdentifier", cert.ANOTHER_REPOSITORY)],
        id="repository-id",
    ),
    pytest.param(
        replace(CERTIFICATE, repository_id=""),
        [("certificate.sourceRepositoryIdentifier", cert.ANOTHER_REPOSITORY)],
        id="repository-id-absent",
    ),
    pytest.param(
        replace(CERTIFICATE, build_config=REUSABLE), [(W, ANOTHER_BUILD_CONFIG)], id="build-config"
    ),
    pytest.param(
        replace(CERTIFICATE, build_config=""), [(W, ANOTHER_BUILD_CONFIG)], id="build-config-absent"
    ),
    pytest.param(
        replace(CERTIFICATE, runner_environment="self-hosted"),
        [(f"{G}.runner_environment", ANOTHER_RUNNER)],
        id="runner",
    ),
    pytest.param(
        replace(CERTIFICATE, runner_environment=""),
        [(f"{G}.runner_environment", ANOTHER_RUNNER)],
        id="runner-absent",
    ),
]


@pytest.mark.parametrize(("certificate", "expected"), CERTIFICATES)
def test_a_statement_signed_by_another_run_is_refused_by_its_certificate(
    certificate: Certificate, expected: list[tuple[str, str]]
) -> None:
    assert problems(payload(), certificate) == expected


DECLARATIONS = [
    pytest.param(
        replace(EXPECTED, image="ghcr.io/gora-tools/gora"),
        [("subject[0].name", ANOTHER_IMAGE)],
        id="image",
    ),
    pytest.param(
        replace(EXPECTED, digest="sha256:" + "ab" * 32),
        [("subject[0].digest.sha256", ANOTHER_DIGEST)],
        id="digest",
    ),
    pytest.param(
        replace(EXPECTED, repository="github.com/gora-tools/gora"),
        [
            (f"{W}.repository", ANOTHER_SOURCE),
            (f"{R}.uri", ANOTHER_DEPENDENCY),
            ("predicate.runDetails.metadata.invocationId", ANOTHER_RUN),
        ],
        id="repository",
    ),
    pytest.param(
        replace(EXPECTED, repository_id="1"),
        [
            (f"{G}.repository_id", ANOTHER_REPOSITORY_ID),
            ("certificate.sourceRepositoryIdentifier", cert.ANOTHER_REPOSITORY),
        ],
        id="repository-id",
    ),
    pytest.param(
        replace(EXPECTED, commit=OTHER_COMMIT),
        [
            (f"{R}.digest.gitCommit", ANOTHER_COMMIT),
            ("certificate.sourceRepositoryDigest", cert.ANOTHER_COMMIT),
        ],
        id="revision",
    ),
    pytest.param(
        replace(EXPECTED, builders=(signer(REUSABLE),)),
        [("certificate.identity", cert.NOT_A_SIGNER)],
        id="builder",
    ),
]


@pytest.mark.parametrize(("expected", "found"), DECLARATIONS)
def test_a_statement_is_refused_for_a_build_it_does_not_describe(
    expected: Expected, found: list[tuple[str, str]]
) -> None:
    assert problems(payload(), CERTIFICATE, expected) == found


def test_a_certificate_needs_an_identity_among_the_builders() -> None:
    with pytest.raises(ValueError, match=f"^{cert.NO_SIGNER}$"):
        verify(payload(), CERTIFICATE, replace(EXPECTED, builders=()))


def test_any_of_the_artifact_s_builders_may_sign() -> None:
    expected = replace(EXPECTED, builders=(signer(REUSABLE), signer(WORKFLOW)))
    assert verify(payload(), CERTIFICATE, expected).level is BuildLevel.L2


# --- the level ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("runner", "reusable", "identity", "level"),
    [
        (RunnerEnvironment.GITHUB_HOSTED, True, True, BuildLevel.L3),
        (RunnerEnvironment.GITHUB_HOSTED, False, True, BuildLevel.L2),
        (RunnerEnvironment.SELF_HOSTED, True, True, BuildLevel.L2),
        (RunnerEnvironment.SELF_HOSTED, False, True, BuildLevel.L2),
        (RunnerEnvironment.GITHUB_HOSTED, True, False, BuildLevel.L2),
        (RunnerEnvironment.GITHUB_HOSTED, False, False, BuildLevel.L2),
        (RunnerEnvironment.SELF_HOSTED, True, False, BuildLevel.L2),
        (RunnerEnvironment.SELF_HOSTED, False, False, BuildLevel.L2),
    ],
)
def test_level_3_needs_an_identity_a_reusable_workflow_and_a_github_hosted_runner(
    runner: RunnerEnvironment, reusable: bool, identity: bool, level: BuildLevel
) -> None:
    assert build_level(runner, reusable=reusable, identity=identity) is level


def _as_reusable(doc: dict[str, Any]) -> None:
    doc["predicate"]["runDetails"]["builder"]["id"] = REUSABLE


@pytest.mark.parametrize(
    ("builder", "reusable"),
    [
        pytest.param(WORKFLOW, False, id="the-workflow-that-ran"),
        pytest.param(
            WORKFLOW.replace("dani-garcia/vaultwarden", "Dani-Garcia/VaultWarden"),
            False,
            id="owner-and-name-in-another-case",
        ),
        pytest.param(REUSABLE, True, id="another-file"),
        pytest.param(
            WORKFLOW.replace("release.yml", "Release.yml"), True, id="a-file-in-another-case"
        ),
        pytest.param(WORKFLOW.replace("refs/heads/main", "refs/heads/v2"), True, id="another-ref"),
        pytest.param(SHARED, True, id="another-repository"),
    ],
)
def test_a_builder_is_reusable_when_it_is_not_the_workflow_that_ran(
    builder: str, reusable: bool
) -> None:
    statement = read_statement(
        mutated(setting(("predicate", "runDetails", "builder", "id"), builder))
    )
    assert statement.provenance.reusable is reusable


REUSABLE_CERTIFICATE = replace(CERTIFICATE, identity=REUSABLE)
REUSABLE_EXPECTED = replace(EXPECTED, builders=(signer(REUSABLE),))


def test_synthetic_a_reusable_builder_on_a_github_hosted_runner_gives_level_3() -> None:
    """Synthetic: the real statement with its builder made a reusable workflow, not a proof.
    The first provenance of GORA's own ``build.yml`` is the real case (ADR-018 section 2)."""
    verified = verify(mutated(_as_reusable), REUSABLE_CERTIFICATE, REUSABLE_EXPECTED)
    assert verified.statement.provenance.reusable is True
    assert verified.level is BuildLevel.L3


def test_synthetic_a_reusable_builder_on_a_self_hosted_runner_gives_level_2() -> None:
    def change(doc: dict[str, Any]) -> None:
        _as_reusable(doc)
        at(doc, GITHUB_KEYS)["runner_environment"] = "self-hosted"

    certificate = replace(REUSABLE_CERTIFICATE, runner_environment="self-hosted")
    verified = verify(mutated(change), certificate, REUSABLE_EXPECTED)
    assert verified.statement.provenance.runner_environment is RunnerEnvironment.SELF_HOSTED
    assert verified.level is BuildLevel.L2


def test_synthetic_a_reusable_builder_signed_by_a_key_gives_level_2() -> None:
    assert verify(mutated(_as_reusable), KEY, EXPECTED).level is BuildLevel.L2


def test_the_real_statement_on_a_self_hosted_runner_still_counts_at_level_2() -> None:
    change = setting((*GITHUB_KEYS, "runner_environment"), "self-hosted")
    certificate = replace(CERTIFICATE, runner_environment="self-hosted")
    assert verify(mutated(change), certificate, EXPECTED).level is BuildLevel.L2


# The level rests on what the host issued, never on what the signer writes: a statement that
# claims another workflow ran, or another runner, than its certificate names is refused.


def test_a_statement_that_names_another_workflow_than_its_certificate_is_refused() -> None:
    change = setting((*WORKFLOW_KEYS, "path"), ".github/workflows/caller.yml")
    assert problems(mutated(change)) == [(W, ANOTHER_BUILD_CONFIG)]


def test_a_statement_that_names_another_runner_than_its_certificate_is_refused() -> None:
    certificate = replace(REUSABLE_CERTIFICATE, runner_environment="self-hosted")
    assert problems(mutated(_as_reusable), certificate, REUSABLE_EXPECTED) == [
        (f"{G}.runner_environment", ANOTHER_RUNNER)
    ]


# --- the caller's errors -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("image", "ghcr.io/dani-garcia/vaultwarden:testing", "image must be"),
        ("digest", "sha256:ABC", "digest must be"),
        ("repository", "github.com/Dani-Garcia/vaultwarden", "repository must be"),
        ("repository_id", "0", "repository id must be"),
        ("commit", "58a980f8", "commit must be"),
    ],
)
def test_an_expectation_out_of_its_grammar_is_the_caller_s_error(
    field: str, value: Any, message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}"):
        replace(EXPECTED, **{field: value})


def test_an_evidence_error_lists_each_path_and_reason() -> None:
    error = EvidenceError([("a.b", "is wrong"), ("c", "is missing")])
    assert str(error) == "a.b: is wrong\nc: is missing"

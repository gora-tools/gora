"""GORA's own build's provenance, the second fixture (TESTING rule 5, ADR-018 sections 2 and 3).

TESTING rule 5: a checker of foreign evidence is proven on a statement a foreign producer made
(``test_provenance.py``, vaultwarden's), and its own producer's statement is the second fixture.
This is that fixture, and the real Build L3 case that ``test_provenance.py`` could only mutate.

Where it comes from: the first live run of ``.github/workflows/build.yml`` (run 46029301799, the
push of ``b738174``, the merge of #186), on 2026-11-27. Its job ``verify`` passed ``cosign verify``
and ``cosign verify-attestation`` for the index and for both platform manifests, and uploaded the
artifact ``evidence``. The fixture is the provenance bundle of the ``linux/amd64`` platform
manifest, ``ghcr.io/gora-tools/gora@sha256:70287dfb…``, byte for byte as ``cosign download
attestation`` gave it: the one provenance bundle of that digest, and the statement that passed
``verify-attestation`` in that run, with the identity and ``github.sha``. cosign v3.1.4, which the
action ``sigstore/cosign-installer`` carried in that run, wrote it as a Sigstore bundle v0.3.

What this proves: a real statement, written by cosign from GORA's own build, passes GORA's form and
binding through ``read_statement`` and ``verify`` at Build L3, against certificate fields that are
literals. They were read from the bundle's ``verificationMaterial.certificate.rawBytes`` with
``openssl x509 -inform DER -text`` on 2026-11-27. GORA parses no X.509 (``certificate.py`` is a
plain class; parsing the certificate is the Sigstore adapter's, not built yet), so these fields
are not ones GORA extracted.

What it does not prove: nothing here verifies the signature, and GORA did not verify this
statement's signature; ``cosign`` did, in that run. A test ties the literals to the bundle's
certificate once the Sigstore adapter, not built yet, parses it. The binding cases vary the
certificate and ``Expected``, never the signed payload. Expectations are written out literally.
"""

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from gora.domain.evidence import certificate as cert
from gora.domain.evidence.certificate import Certificate
from gora.domain.evidence.provenance import (
    ANOTHER_BUILD_CONFIG,
    ANOTHER_DIGEST,
    ANOTHER_RUNNER,
    BuildLevel,
    EvidenceError,
    Expected,
    RunnerEnvironment,
    verify,
)
from gora.domain.release_repository.v1alpha1 import IdentitySigner

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "provenance" / "gora.sigstore.json"
PAYLOAD_SHA256 = "1855f6ca842ad6a39f8e6a20316abb315581561891bc6ccd358598e1a48a68ab"

ISSUER = "https://token.actions.githubusercontent.com"
BUILDER = "https://github.com/gora-tools/gora/.github/workflows/build.yml@refs/heads/main"
CALLER = "https://github.com/gora-tools/gora/.github/workflows/candidate.yml@refs/heads/main"
COMMIT = "b7381742aef72c1d22d9a00bfd62c014f22b38f3"
DIGEST = "sha256:70287dfb8128158ace293fa9341af2b216a21193fe3215bbe289424ae61cb7f6"
INDEX_DIGEST = "sha256:2001a74729cdbfcde78bc50c216cce4783966fa56f618416ee8fb0770389c7d7"
OTHER_COMMIT = "a" * 40

CERTIFICATE = Certificate(
    issuer=ISSUER,
    identity=BUILDER,
    source_commit=COMMIT,
    source_ref="refs/heads/main",
    repository_id="1205220004",
    build_config=CALLER,
    runner_environment="github-hosted",
)

EXPECTED = Expected(
    image="ghcr.io/gora-tools/gora",
    digest=DIGEST,
    repository="github.com/gora-tools/gora",
    repository_id="1205220004",
    commit=COMMIT,
    builders=(IdentitySigner.model_validate({"issuer": ISSUER, "identity": BUILDER}),),
)

W = "predicate.buildDefinition.externalParameters.workflow"
G = "predicate.buildDefinition.internalParameters.github"


def bundle() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_bytes())
    return loaded


def payload() -> bytes:
    envelope = bundle()["dsseEnvelope"]
    assert envelope["payloadType"] == "application/vnd.in-toto+json"
    return base64.b64decode(envelope["payload"], validate=True)


def problems(
    certificate: Certificate = CERTIFICATE, expected: Expected = EXPECTED
) -> list[tuple[str, str]]:
    with pytest.raises(EvidenceError) as caught:
        verify(payload(), certificate, expected)
    return caught.value.problems


def test_the_fixture_is_the_statement_the_build_wrote() -> None:
    assert hashlib.sha256(payload()).hexdigest() == PAYLOAD_SHA256


def test_the_fixture_is_a_sigstore_bundle_v0_3_of_an_in_toto_v0_1_statement() -> None:
    assert bundle()["mediaType"] == "application/vnd.dev.sigstore.bundle.v0.3+json"
    assert bundle()["dsseEnvelope"]["payloadType"] == "application/vnd.in-toto+json"
    assert json.loads(payload())["_type"] == "https://in-toto.io/Statement/v0.1"


def test_the_real_statement_counts_for_its_build_at_level_3() -> None:
    verified = verify(payload(), CERTIFICATE, EXPECTED)
    provenance = verified.statement.provenance
    assert verified.statement.name == "ghcr.io/gora-tools/gora"
    assert verified.statement.digest == DIGEST
    assert provenance.workflow.repository == "https://github.com/gora-tools/gora"
    assert provenance.workflow.path == ".github/workflows/candidate.yml"
    assert provenance.workflow.ref == "refs/heads/main"
    assert provenance.event == "push"
    assert provenance.runner_environment is RunnerEnvironment.GITHUB_HOSTED
    assert provenance.builder == BUILDER
    # The build ran in a reusable workflow called by candidate.yml: not the workflow that ran.
    assert provenance.reusable is True
    assert provenance.invocation == (
        "https://github.com/gora-tools/gora/actions/runs/46029301799/attempts/1"
    )
    assert verified.level is BuildLevel.L3


def test_the_identity_and_the_issuer_are_the_builder_the_policy_names() -> None:
    policy = yaml.safe_load((ROOT / "release-policy.yaml").read_text(encoding="utf-8"))
    builder = policy["spec"]["builders"]["gora-build"]
    assert builder == {"issuer": ISSUER, "identity": BUILDER}


BINDING = [
    pytest.param(
        replace(CERTIFICATE, source_commit=OTHER_COMMIT),
        EXPECTED,
        [("certificate.sourceRepositoryDigest", cert.ANOTHER_COMMIT)],
        id="commit",
    ),
    pytest.param(
        replace(CERTIFICATE, repository_id="1205220005"),
        EXPECTED,
        [("certificate.sourceRepositoryIdentifier", cert.ANOTHER_REPOSITORY)],
        id="repository-id",
    ),
    pytest.param(
        replace(CERTIFICATE, build_config=BUILDER),
        EXPECTED,
        [(W, ANOTHER_BUILD_CONFIG)],
        id="workflow-that-ran",
    ),
    pytest.param(
        replace(CERTIFICATE, runner_environment="self-hosted"),
        EXPECTED,
        [(f"{G}.runner_environment", ANOTHER_RUNNER)],
        id="runner",
    ),
    pytest.param(
        CERTIFICATE,
        replace(EXPECTED, digest=INDEX_DIGEST),
        [("subject[0].digest.sha256", ANOTHER_DIGEST)],
        id="the-index-digest",
    ),
]


@pytest.mark.parametrize(("certificate", "expected", "found"), BINDING)
def test_the_binding_bites_on_the_real_statement(
    certificate: Certificate, expected: Expected, found: list[tuple[str, str]]
) -> None:
    assert problems(certificate, expected) == found

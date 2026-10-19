"""A CI signer's certificate, bound to the repository, the ref and the commit its role
requires (ADR-027 section 7).

What is held: the issuer and the identity are one signer's the policy names for the role,
compared exactly; the source repository identifier is the host's for the repository the role
names; the source ref is exactly ``refs/heads/main``; for a builder, the source repository
digest is the commit the image carries; for an attester it is not read. An absent extension
is the empty string and is refused as any other value. The workflow that ran and the runner
(``.18``, ``.11``) bind a statement of provenance, not a role. The values are those of a real
certificate (``test_provenance``). Expectations are written out literally.
"""

from dataclasses import replace

import pytest

from gora.domain.evidence.certificate import (
    ANOTHER_COMMIT,
    ANOTHER_REPOSITORY,
    NO_SIGNER,
    NOT_A_SIGNER,
    NOT_MAIN,
    Certificate,
    certificate_problems,
)
from gora.domain.release_repository.v1alpha1 import IdentitySigner

ISSUER = "https://token.actions.githubusercontent.com"
IDENTITY = (
    "https://github.com/dani-garcia/vaultwarden/.github/workflows/release.yml@refs/heads/main"
)
COMMIT = "58a980f850464f7d9cdc99a765df36f0a3308b91"
REPOSITORY_ID = "121898717"

CERTIFICATE = Certificate(
    issuer=ISSUER,
    identity=IDENTITY,
    source_commit=COMMIT,
    source_ref="refs/heads/main",
    repository_id=REPOSITORY_ID,
    build_config=IDENTITY,
    runner_environment="github-hosted",
)
SIGNERS = (IdentitySigner.model_validate({"issuer": ISSUER, "identity": IDENTITY}),)


def builder(certificate: Certificate) -> list[tuple[str, str]]:
    return certificate_problems(
        certificate, signers=SIGNERS, repository_id=REPOSITORY_ID, commit=COMMIT
    )


def attester(certificate: Certificate) -> list[tuple[str, str]]:
    return certificate_problems(
        certificate, signers=SIGNERS, repository_id=REPOSITORY_ID, commit=None
    )


def test_a_builder_s_certificate_of_its_run_has_no_problem() -> None:
    assert builder(CERTIFICATE) == []


@pytest.mark.parametrize(
    ("certificate", "expected"),
    [
        pytest.param(
            replace(CERTIFICATE, issuer="https://token.actions.githubusercontent.com/"),
            [("certificate.identity", NOT_A_SIGNER)],
            id="issuer-slash",
        ),
        pytest.param(
            replace(CERTIFICATE, identity=IDENTITY.upper()),
            [("certificate.identity", NOT_A_SIGNER)],
            id="identity-case",
        ),
        pytest.param(
            replace(CERTIFICATE, identity=IDENTITY + "/"),
            [("certificate.identity", NOT_A_SIGNER)],
            id="identity-suffix",
        ),
        pytest.param(
            replace(CERTIFICATE, repository_id="121898718"),
            [("certificate.sourceRepositoryIdentifier", ANOTHER_REPOSITORY)],
            id="repository-id",
        ),
        pytest.param(
            replace(CERTIFICATE, repository_id=""),
            [("certificate.sourceRepositoryIdentifier", ANOTHER_REPOSITORY)],
            id="repository-id-absent",
        ),
        pytest.param(
            replace(CERTIFICATE, source_ref="refs/heads/feature"),
            [("certificate.sourceRepositoryRef", NOT_MAIN)],
            id="ref",
        ),
        pytest.param(
            replace(CERTIFICATE, source_ref="refs/tags/v1.0.0"),
            [("certificate.sourceRepositoryRef", NOT_MAIN)],
            id="ref-tag",
        ),
        pytest.param(
            replace(CERTIFICATE, source_ref=""),
            [("certificate.sourceRepositoryRef", NOT_MAIN)],
            id="ref-absent",
        ),
        pytest.param(
            replace(CERTIFICATE, source_commit="a" * 40),
            [("certificate.sourceRepositoryDigest", ANOTHER_COMMIT)],
            id="commit",
        ),
        pytest.param(
            replace(CERTIFICATE, source_commit=""),
            [("certificate.sourceRepositoryDigest", ANOTHER_COMMIT)],
            id="commit-absent",
        ),
        pytest.param(
            Certificate(
                issuer="",
                identity="",
                source_commit="",
                source_ref="",
                repository_id="",
                build_config="",
                runner_environment="",
            ),
            [
                ("certificate.identity", NOT_A_SIGNER),
                ("certificate.sourceRepositoryIdentifier", ANOTHER_REPOSITORY),
                ("certificate.sourceRepositoryRef", NOT_MAIN),
                ("certificate.sourceRepositoryDigest", ANOTHER_COMMIT),
            ],
            id="nothing",
        ),
    ],
)
def test_a_builder_s_certificate_of_another_run_is_refused(
    certificate: Certificate, expected: list[tuple[str, str]]
) -> None:
    assert builder(certificate) == expected


def test_an_attester_s_certificate_is_not_bound_to_a_commit() -> None:
    assert attester(replace(CERTIFICATE, source_commit="")) == []
    assert attester(replace(CERTIFICATE, source_ref="refs/heads/dev")) == [
        ("certificate.sourceRepositoryRef", NOT_MAIN)
    ]


@pytest.mark.parametrize(
    ("signers", "repository_id", "commit", "message"),
    [
        ((), REPOSITORY_ID, COMMIT, NO_SIGNER),
        (SIGNERS, "0121898717", COMMIT, "repository id must be"),
        (SIGNERS, "", COMMIT, "repository id must be"),
        (SIGNERS, REPOSITORY_ID, "58a980f8", "commit must be"),
        (SIGNERS, REPOSITORY_ID, "", "commit must be"),
    ],
)
def test_a_role_out_of_its_grammar_is_the_caller_s_error(
    signers: tuple[IdentitySigner, ...], repository_id: str, commit: str, message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}"):
        certificate_problems(
            CERTIFICATE, signers=signers, repository_id=repository_id, commit=commit
        )

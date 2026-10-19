"""A CI signer's certificate and its binding to a repository (ADR-027 section 7).

Pure: the adapter that verifies a signature reads the certificate and passes its fields as a
``Certificate``; this module decides whether they are the ones the signer's role requires.

A Sigstore identity names the workflow that signed, not the repository whose run signed: a
reusable workflow in a public repository can be called from any repository, and the
certificate of that run names the workflow all the same. So a signature by a CI identity counts
only when the fields Fulcio records from the run's token are the ones its role requires:

- the issuer and the identity are those of one of the signers the policy names for the role,
  compared exactly, as strings, the comparison the policy's own rules use (``same_signer``);
- the source repository identifier (``1.3.6.1.4.1.57264.1.15``) is the immutable identifier
  GORA reads from the host for the repository the role names: a builder's, the artifact's
  source repository; an attester's, the release repository;
- the source repository ref (``1.3.6.1.4.1.57264.1.14``) is exactly ``refs/heads/main``: a
  builder's by L5, an attester's by L22;
- for a builder, the source repository digest (``1.3.6.1.4.1.57264.1.13``) is exactly the
  source commit the image carries as ``org.opencontainers.image.revision`` (L9).

An extension the certificate does not carry is passed as the empty string, and is refused as
any other value that is not the required one. ``certificate_problems`` lists every field that
fails, each with a path and a value-free reason; a caller's error is a ``ValueError``.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from gora.domain.release.identifiers import SOURCE_COMMIT
from gora.domain.release_repository.v1alpha1 import IdentitySigner, Problems

MAIN = "refs/heads/main"

NOT_A_SIGNER = "is not a signer the policy names for this role: issuer and identity, exactly"
ANOTHER_REPOSITORY = (
    "is not the identifier the host gives the repository the role names (1.3.6.1.4.1.57264.1.15)"
)
NOT_MAIN = "is not refs/heads/main (1.3.6.1.4.1.57264.1.14; L5, L22)"
ANOTHER_COMMIT = (
    "is not the source commit the image carries as org.opencontainers.image.revision "
    "(1.3.6.1.4.1.57264.1.13; L9)"
)

REPOSITORY_ID_RULE = "must be a repository identifier: decimal digits, no leading zero"
NO_SIGNER = "a role names at least one signer"

_REPOSITORY_ID = re.compile(r"[1-9][0-9]{0,19}")


@dataclass(frozen=True)
class Certificate:
    """The fields of a CI signer's Fulcio certificate that GORA checks.

    ``issuer`` and ``identity`` are the OIDC issuer and the subject alternative name;
    ``source_commit``, ``source_ref`` and ``repository_id`` are the extensions
    ``1.3.6.1.4.1.57264.1.13``, ``.14`` and ``.15``; ``build_config`` and
    ``runner_environment`` are ``.18``, the workflow that ran, and ``.11``, which provenance
    is bound to (``provenance``). An absent extension is the empty string.
    """

    issuer: str
    identity: str
    source_commit: str
    source_ref: str
    repository_id: str
    build_config: str
    runner_environment: str


def is_repository_id(value: str) -> bool:
    """Whether ``value`` has the form of a host's repository identifier."""
    return _REPOSITORY_ID.fullmatch(value) is not None


def certificate_problems(
    certificate: Certificate,
    *,
    signers: Sequence[IdentitySigner],
    repository_id: str,
    commit: str | None,
) -> Problems:
    """Every field of ``certificate`` that is not what its role requires.

    ``signers`` are the identities the policy names for the role, ``repository_id`` the
    identifier the host gives the repository the role names, and ``commit`` the source commit
    the image carries, for a builder, or None for an attester, whose certificate names no build.
    """
    if not signers:
        raise ValueError(NO_SIGNER)
    if not is_repository_id(repository_id):
        raise ValueError(f"repository id {REPOSITORY_ID_RULE}")
    if commit is not None and not SOURCE_COMMIT.is_valid(commit):
        raise ValueError(f"commit {SOURCE_COMMIT.rule}")
    problems: Problems = []
    named = {(signer.issuer, signer.identity) for signer in signers}
    if (certificate.issuer, certificate.identity) not in named:
        problems.append(("certificate.identity", NOT_A_SIGNER))
    if certificate.repository_id != repository_id:
        problems.append(("certificate.sourceRepositoryIdentifier", ANOTHER_REPOSITORY))
    if certificate.source_ref != MAIN:
        problems.append(("certificate.sourceRepositoryRef", NOT_MAIN))
    if commit is not None and certificate.source_commit != commit:
        problems.append(("certificate.sourceRepositoryDigest", ANOTHER_COMMIT))
    return problems

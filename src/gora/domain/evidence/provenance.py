"""SLSA provenance in the one form GORA accepts, its binding, and the build level it gives
(ADR-018 sections 2 and 3, ADR-027 section 7; L5, L7, L9 of release-model section 10).

Pure: the adapter that verifies a signature passes the statement it signed, as the payload
bytes of its envelope, with the signer: the ``Certificate`` of a builder that is a Sigstore
identity, or None for a builder that is a key, whose signature the adapter verified with a key
the artifact's builders name. Reading the registry, the host and the certificate is the
adapters'.

**The form.** A statement is an in-toto Statement v1 whose predicate is SLSA provenance v1 of
the build type ``https://actions.github.io/buildtypes/workflow/v1``, the form GitHub's own
toolkit writes from the claims of a run's OIDC token. Every field below is required, and no
other field is accepted, at any depth: an unknown field is refused, not ignored, so a producer
whose output drifts from this form is seen at once.

- ``_type``: ``https://in-toto.io/Statement/v1``; ``predicateType``:
  ``https://slsa.dev/provenance/v1``.
- ``subject``: exactly one, its ``name`` an image and its ``digest`` exactly one ``sha256``.
- ``buildDefinition.buildType``: the build type above.
- ``buildDefinition.externalParameters``: only ``workflow``, with only ``ref``, exactly
  ``refs/heads/main`` (L5), ``repository``, ``https://github.com/<owner>/<name>``, and
  ``path``, a workflow file under ``.github/workflows/``.
- ``buildDefinition.internalParameters``: only ``github``, with only ``event_name``, exactly
  ``push``, ``repository_id`` and ``repository_owner_id``, decimal identifiers, and
  ``runner_environment``, ``github-hosted`` or ``self-hosted``.
- ``buildDefinition.resolvedDependencies``: exactly one, with only ``uri`` and ``digest``,
  which holds only ``gitCommit``, an object name.
- ``runDetails.builder``: only ``id``, a workflow,
  ``https://github.com/<owner>/<name>/.github/workflows/<file>@<ref>``, a ref or a commit;
  ``runDetails.metadata``: only ``invocationId``, a run,
  ``https://github.com/<owner>/<name>/actions/runs/<id>/attempts/<n>``.

The payload is UTF-8 JSON in which no object repeats a key and every value above is a string.

**The binding.** A statement in that form counts for a digest only when it is the build the
artifact's declaration, the host and the image describe (``Expected``):

- its subject is the declaration's image at that digest;
- ``workflow.repository`` is the declaration's source repository, its owner and name compared
  without case, since GitHub compares them so; the identifier below is the repository's
  identity, and a name only its address;
- ``repository_id`` is the identifier GORA reads from the host for that repository;
- ``resolvedDependencies[0].uri`` is, as a whole, ``git+`` the source repository ``@`` the
  source ref: the repository and the ref are read from one string, not from ``gitCommit``
  alone;
- ``gitCommit`` is the source commit the image carries (L9);
- ``invocationId`` is a run of the source repository, compared as ``workflow.repository`` is.

Who vouches for these facts differs by signer. For a Sigstore identity the host states them in
the certificate Fulcio issues from the run's token, and the statement must agree with it:

- the certificate is bound as ``certificate`` says, for a builder (``.13``, ``.14``, ``.15``,
  issuer and identity), and the source ref of the dependency is the certificate's ``.14``;
- ``builder.id`` is the certificate's identity;
- the workflow that ran, ``<repository>/<path>@<ref>``, is exactly the certificate's ``.18``;
- ``runner_environment`` is exactly the certificate's ``.11``.

For a key the signer asserts them, and GORA compares them with the host and the image: the
source ref is ``refs/heads/main``, and ``builder.id`` is read for its form only, since nothing
names what it must be. The statement is trusted as far as the key the policy names as a builder.

**The level.** It is data in the verification summary, never a reason to refuse (ADR-018
section 2). ``SLSA_BUILD_LEVEL_3`` needs three facts together: the builder is a Sigstore
identity, so the host stated the facts above; it is a reusable workflow, ``builder.id`` not the
workflow that ran (the owner and name compared without case), so the signing identity belongs to
a workflow the calling steps cannot reach; and the runner is ``github-hosted``. For a Sigstore
identity the last two are the certificate's own facts, ``.18`` and ``.11``, which the statement
must repeat. Any other build gives ``SLSA_BUILD_LEVEL_2``; a key always does, since GORA cannot
see who can reach a key.

``verify`` reads the form first: a statement not in it is refused with every problem of its
form, and its binding is not judged. Otherwise every problem of the binding is listed. Each
problem is a path and a value-free reason, in an ``EvidenceError``. A caller's error is a
``ValueError``.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from gora.domain.evidence.certificate import (
    MAIN,
    Certificate,
    certificate_problems,
    is_repository_id,
)
from gora.domain.release.identifiers import DIGEST, IMAGE, SOURCE_COMMIT, SOURCE_REPOSITORY
from gora.domain.release_repository.v1alpha1 import IdentitySigner, Problems
from gora.domain.release_repository.yaml12 import PATH_KEY

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"
EVENT = "push"


class RunnerEnvironment(StrEnum):
    GITHUB_HOSTED = "github-hosted"
    SELF_HOSTED = "self-hosted"


class BuildLevel(StrEnum):
    L2 = "SLSA_BUILD_LEVEL_2"
    L3 = "SLSA_BUILD_LEVEL_3"


NOT_JSON = "is not UTF-8 JSON whose objects each name a key once"
MISSING = "is missing"
UNKNOWN = "is not a field of workflow/v1 provenance; an unknown field is refused"
NOT_OBJECT = "must be an object"
NOT_STRING = "must be a string"
ONE_ENTRY = "must hold exactly one entry"

WRONG_STATEMENT_TYPE = f"must be {STATEMENT_TYPE}"
WRONG_PREDICATE_TYPE = f"must be {PREDICATE_TYPE}"
WRONG_BUILD_TYPE = f"must be {BUILD_TYPE}"
NOT_MAIN = f"must be {MAIN}: a build on main (L5)"
NOT_PUSH = f"must be {EVENT}: a build of a commit pushed to main"
NOT_AN_IMAGE = "must be an image: a fully qualified repository name, no tag, no digest"
NOT_A_DIGEST = "must be 64 lowercase hex digits"
NOT_A_REPOSITORY_URL = "must be https://github.com/<owner>/<name>"
NOT_A_WORKFLOW_PATH = "must be a workflow file: .github/workflows/<name>.yml or .yaml"
NOT_AN_ID = "must be decimal digits, no leading zero"
NOT_A_RUNNER = "must be github-hosted or self-hosted"
NOT_A_COMMIT = "must be an object name: 40 or 64 lowercase hex digits"
NOT_A_BUILDER = (
    "must be a workflow: https://github.com/<owner>/<name>/.github/workflows/<file>@<ref or commit>"
)
NOT_A_RUN = "must be a run: https://github.com/<owner>/<name>/actions/runs/<id>/attempts/<n>"

ANOTHER_IMAGE = "is not the image the artifact's declaration names"
ANOTHER_DIGEST = "is not the digest being verified"
ANOTHER_SOURCE = "is not the source repository the artifact's declaration names"
ANOTHER_REPOSITORY_ID = "is not the identifier the host gives the source repository"
ANOTHER_DEPENDENCY = "is not git+ the declaration's source repository @ the source ref"
ANOTHER_COMMIT = "is not the source commit the image carries (L9)"
ANOTHER_RUN = "is not a run of the source repository the artifact's declaration names"
ANOTHER_BUILDER = "is not the identity of the builder's certificate"
ANOTHER_BUILD_CONFIG = (
    "is not the workflow the builder's certificate names as the one that ran "
    "(1.3.6.1.4.1.57264.1.18)"
)
ANOTHER_RUNNER = (
    "is not the runner environment the builder's certificate names (1.3.6.1.4.1.57264.1.11)"
)


# The template of the form: a dict is an object with exactly these keys, a one-item list a
# list of exactly one such item, ``str`` a string.
_FORM: dict[str, Any] = {
    "_type": str,
    "subject": [{"name": str, "digest": {"sha256": str}}],
    "predicateType": str,
    "predicate": {
        "buildDefinition": {
            "buildType": str,
            "externalParameters": {"workflow": {"ref": str, "repository": str, "path": str}},
            "internalParameters": {
                "github": {
                    "event_name": str,
                    "repository_id": str,
                    "repository_owner_id": str,
                    "runner_environment": str,
                }
            },
            "resolvedDependencies": [{"uri": str, "digest": {"gitCommit": str}}],
        },
        "runDetails": {"builder": {"id": str}, "metadata": {"invocationId": str}},
    },
}

_OWNER = "[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}"
_NAME = "[A-Za-z0-9._-]{1,100}"
_REPOSITORY_URL = re.compile(rf"https://github\.com/{_OWNER}/{_NAME}")
_WORKFLOW_PATH = re.compile(r"\.github/workflows/[A-Za-z0-9._-]+\.ya?ml")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_BUILDER = re.compile(
    rf"(https://github\.com/{_OWNER}/{_NAME})/(\.github/workflows/[A-Za-z0-9._-]+\.ya?ml)"
    r"@(refs/[A-Za-z0-9._/+-]+|[a-f0-9]{40}|[a-f0-9]{64})"
)
_RUNNERS = frozenset(runner.value for runner in RunnerEnvironment)
_RUN = re.compile(
    rf"(https://github\.com/{_OWNER}/{_NAME})"
    r"/actions/runs/[1-9][0-9]{0,19}/attempts/[1-9][0-9]{0,9}"
)

_WORKFLOW = "predicate.buildDefinition.externalParameters.workflow"
_GITHUB = "predicate.buildDefinition.internalParameters.github"
_DEPENDENCY = "predicate.buildDefinition.resolvedDependencies[0]"


class EvidenceError(Exception):
    """Evidence that does not count, with every problem found: a path and a reason each."""

    def __init__(self, problems: Problems) -> None:
        super().__init__(problems)
        self.problems = problems

    def __str__(self) -> str:
        return "\n".join(f"{path}: {reason}" for path, reason in self.problems)


@dataclass(frozen=True)
class Workflow:
    """The workflow that ran: ``externalParameters.workflow``."""

    repository: str
    path: str
    ref: str


@dataclass(frozen=True)
class Provenance:
    """A ``workflow/v1`` predicate, in the form above."""

    workflow: Workflow
    event: str
    repository_id: str
    repository_owner_id: str
    runner_environment: RunnerEnvironment
    dependency: str
    commit: str
    builder: str
    invocation: str

    @property
    def reusable(self) -> bool:
        """Whether the builder is a reusable workflow: not the workflow that ran.

        The builder is the workflow that ran when it names the same file at the same ref in
        the same repository, whose owner and name are compared without case, as GitHub
        compares them; the file and the ref are compared exactly.
        """
        builder = _BUILDER.fullmatch(self.builder)
        ran = (
            builder is not None
            and builder[1].lower() == self.workflow.repository.lower()
            and builder[2] == self.workflow.path
            and builder[3] == self.workflow.ref
        )
        return not ran


@dataclass(frozen=True)
class Statement:
    """An in-toto statement of one subject, ``name@digest``, and its provenance."""

    name: str
    digest: str
    provenance: Provenance


@dataclass(frozen=True)
class Expected:
    """What a statement must describe, from the declaration, the host and the image.

    ``image`` and ``repository`` are the declaration's ``spec.image`` and
    ``spec.source.repository``; ``digest`` is the digest being verified, an image index or a
    platform manifest; ``repository_id`` is the identifier GORA reads from the host for that
    repository; ``commit`` is the image's ``org.opencontainers.image.revision``; ``builders``
    are the Sigstore identities among the builders the declaration names, which a certificate
    must be one of.
    """

    image: str
    digest: str
    repository: str
    repository_id: str
    commit: str
    builders: tuple[IdentitySigner, ...]

    def __post_init__(self) -> None:
        for what, grammar, value in (
            ("image", IMAGE, self.image),
            ("digest", DIGEST, self.digest),
            ("repository", SOURCE_REPOSITORY, self.repository),
            ("commit", SOURCE_COMMIT, self.commit),
        ):
            if not grammar.is_valid(value):
                raise ValueError(f"{what} {grammar.rule}")
        if not is_repository_id(self.repository_id):
            raise ValueError("repository id must be decimal digits, no leading zero")


@dataclass(frozen=True)
class Verified:
    """A statement that counts for its digest, and the build level it gives."""

    statement: Statement
    level: BuildLevel


def build_level(runner: RunnerEnvironment, *, reusable: bool, identity: bool) -> BuildLevel:
    """``SLSA_BUILD_LEVEL_3`` for a reusable workflow on a GitHub-hosted runner, signed by a
    Sigstore identity; ``SLSA_BUILD_LEVEL_2`` otherwise."""
    if identity and reusable and runner is RunnerEnvironment.GITHUB_HOSTED:
        return BuildLevel.L3
    return BuildLevel.L2


def read_statement(payload: bytes) -> Statement:
    """The statement ``payload`` holds, or an ``EvidenceError`` with every problem of its form."""
    document = _json(payload)
    problems: Problems = []
    _shape(document, _FORM, "", problems)
    if problems:
        raise EvidenceError(problems)
    subject = document["subject"][0]
    definition = document["predicate"]["buildDefinition"]
    workflow = definition["externalParameters"]["workflow"]
    github = definition["internalParameters"]["github"]
    dependency = definition["resolvedDependencies"][0]
    details = document["predicate"]["runDetails"]
    for path, ok, reason in (
        ("_type", document["_type"] == STATEMENT_TYPE, WRONG_STATEMENT_TYPE),
        ("subject[0].name", IMAGE.is_valid(subject["name"]), NOT_AN_IMAGE),
        ("subject[0].digest.sha256", _full(_SHA256, subject["digest"]["sha256"]), NOT_A_DIGEST),
        ("predicateType", document["predicateType"] == PREDICATE_TYPE, WRONG_PREDICATE_TYPE),
        (
            "predicate.buildDefinition.buildType",
            definition["buildType"] == BUILD_TYPE,
            WRONG_BUILD_TYPE,
        ),
        (f"{_WORKFLOW}.ref", workflow["ref"] == MAIN, NOT_MAIN),
        (
            f"{_WORKFLOW}.repository",
            _full(_REPOSITORY_URL, workflow["repository"]),
            NOT_A_REPOSITORY_URL,
        ),
        (f"{_WORKFLOW}.path", _full(_WORKFLOW_PATH, workflow["path"]), NOT_A_WORKFLOW_PATH),
        (f"{_GITHUB}.event_name", github["event_name"] == EVENT, NOT_PUSH),
        (f"{_GITHUB}.repository_id", is_repository_id(github["repository_id"]), NOT_AN_ID),
        (
            f"{_GITHUB}.repository_owner_id",
            is_repository_id(github["repository_owner_id"]),
            NOT_AN_ID,
        ),
        (f"{_GITHUB}.runner_environment", github["runner_environment"] in _RUNNERS, NOT_A_RUNNER),
        (
            f"{_DEPENDENCY}.digest.gitCommit",
            SOURCE_COMMIT.is_valid(dependency["digest"]["gitCommit"]),
            NOT_A_COMMIT,
        ),
        (
            "predicate.runDetails.builder.id",
            _full(_BUILDER, details["builder"]["id"]),
            NOT_A_BUILDER,
        ),
        (
            "predicate.runDetails.metadata.invocationId",
            _run_repository(details["metadata"]["invocationId"]) is not None,
            NOT_A_RUN,
        ),
    ):
        if not ok:
            problems.append((path, reason))
    if problems:
        raise EvidenceError(problems)
    return Statement(
        name=subject["name"],
        digest=f"sha256:{subject['digest']['sha256']}",
        provenance=Provenance(
            workflow=Workflow(workflow["repository"], workflow["path"], workflow["ref"]),
            event=github["event_name"],
            repository_id=github["repository_id"],
            repository_owner_id=github["repository_owner_id"],
            runner_environment=RunnerEnvironment(github["runner_environment"]),
            dependency=dependency["uri"],
            commit=dependency["digest"]["gitCommit"],
            builder=details["builder"]["id"],
            invocation=details["metadata"]["invocationId"],
        ),
    )


def binding_problems(
    statement: Statement, certificate: Certificate | None, expected: Expected
) -> Problems:
    """Every way ``statement``, with its builder's ``certificate`` or None for a key, is not
    the build ``expected`` describes."""
    problems: Problems = []
    provenance = statement.provenance
    workflow = provenance.workflow
    ref = certificate.source_ref if certificate is not None else MAIN
    for path, ok, reason in (
        ("subject[0].name", statement.name == expected.image, ANOTHER_IMAGE),
        ("subject[0].digest.sha256", statement.digest == expected.digest, ANOTHER_DIGEST),
        (
            f"{_WORKFLOW}.repository",
            _same_repository(workflow.repository, expected.repository),
            ANOTHER_SOURCE,
        ),
        (
            f"{_GITHUB}.repository_id",
            provenance.repository_id == expected.repository_id,
            ANOTHER_REPOSITORY_ID,
        ),
        (
            f"{_DEPENDENCY}.uri",
            _is_dependency(provenance.dependency, expected.repository, ref),
            ANOTHER_DEPENDENCY,
        ),
        (f"{_DEPENDENCY}.digest.gitCommit", provenance.commit == expected.commit, ANOTHER_COMMIT),
        (
            "predicate.runDetails.metadata.invocationId",
            _same_repository(_run_repository(provenance.invocation) or "", expected.repository),
            ANOTHER_RUN,
        ),
    ):
        if not ok:
            problems.append((path, reason))
    if certificate is None:
        return problems
    for path, ok, reason in (
        (
            _WORKFLOW,
            f"{workflow.repository}/{workflow.path}@{workflow.ref}" == certificate.build_config,
            ANOTHER_BUILD_CONFIG,
        ),
        (
            f"{_GITHUB}.runner_environment",
            provenance.runner_environment.value == certificate.runner_environment,
            ANOTHER_RUNNER,
        ),
        (
            "predicate.runDetails.builder.id",
            provenance.builder == certificate.identity,
            ANOTHER_BUILDER,
        ),
    ):
        if not ok:
            problems.append((path, reason))
    problems += certificate_problems(
        certificate,
        signers=expected.builders,
        repository_id=expected.repository_id,
        commit=expected.commit,
    )
    return problems


def verify(payload: bytes, certificate: Certificate | None, expected: Expected) -> Verified:
    """The statement ``payload`` holds, when it counts for ``expected``, with its level.

    ``certificate`` is the builder's when the builder is a Sigstore identity, None when it is a
    key whose signature the caller verified.
    """
    statement = read_statement(payload)
    problems = binding_problems(statement, certificate, expected)
    if problems:
        raise EvidenceError(problems)
    provenance = statement.provenance
    return Verified(
        statement,
        build_level(
            provenance.runner_environment,
            reusable=provenance.reusable,
            identity=certificate is not None,
        ),
    )


def _json(payload: bytes) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise EvidenceError([("", NOT_JSON)]) from None


def _unique_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError(NOT_JSON)
    return dict(pairs)


def _no_constant(_: str) -> Any:
    raise ValueError(NOT_JSON)


def _shape(value: Any, form: Any, path: str, problems: Problems) -> None:
    if isinstance(form, dict):
        if not isinstance(value, dict):
            problems.append((path, NOT_OBJECT))
            return
        for key in form:
            at = f"{path}.{key}" if path else key
            if key not in value:
                problems.append((at, MISSING))
            else:
                _shape(value[key], form[key], at, problems)
        problems += [
            (_unknown(path, key, position), UNKNOWN)
            for position, key in enumerate(value)
            if key not in form
        ]
    elif isinstance(form, list):
        if not isinstance(value, list) or len(value) != 1:
            problems.append((path, ONE_ENTRY))
            return
        _shape(value[0], form[0], f"{path}[0]", problems)
    elif not isinstance(value, str):
        problems.append((path, NOT_STRING))


def _unknown(path: str, key: str, position: int) -> str:
    """The path of an unknown key: the key when it is a plain name, else its place, so a key
    never carries a line break or a terminal control into a message."""
    shown = key if PATH_KEY.fullmatch(key) else f"<key {position}>"
    return f"{path}.{shown}" if path else shown


def _full(pattern: re.Pattern[str], value: str) -> bool:
    return pattern.fullmatch(value) is not None


def _same_repository(url: str, repository: str) -> bool:
    """Whether ``url`` names ``repository``, ``github.com/<owner>/<name>`` in lower case.

    The URL is checked against its ASCII grammar first, so lowering it maps no character
    outside ASCII onto one inside.
    """
    return _full(_REPOSITORY_URL, url) and url.lower() == f"https://{repository}"


def _is_dependency(uri: str, repository: str, ref: str) -> bool:
    head, at, tail = uri.rpartition("@")
    return (
        bool(at)
        and head.startswith("git+")
        and _same_repository(head.removeprefix("git+"), repository)
        and tail == ref
    )


def _run_repository(invocation: str) -> str | None:
    """The repository URL of a run, ``<repository>/actions/runs/<id>/attempts/<n>``."""
    match = _RUN.fullmatch(invocation)
    return match.group(1) if match is not None else None

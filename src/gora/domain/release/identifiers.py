"""Identifier grammar of the release contract (ADR-003, "Identifier grammar").

Pure: no I/O. Every identifier that enters GORA is checked here, whether it arrives as a field
of a release file or as an argument. Each grammar is a plain checker (``problem``), a pydantic
validator (``validate``) and its JSON Schema (``json_schema``); the release files' models build
their field types from them.

Every pattern is written in the common subset of Python ``re`` and ECMA-262: no named groups,
no lookbehind (a lookahead is in both), no ``\\A``/``\\Z``, no possessive or atomic groups, no
``\\d``/``\\w``/``\\s`` and no inline flags. The JSON Schema publishes them to consumers that use
ECMA-262, where ``\\d`` is ASCII and ``$`` matches only at the end. Here a value must
``fullmatch``, so Python's ``$``, which also matches before a final newline, cannot accept one.
"""

import itertools
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_core import PydanticCustomError


@dataclass(frozen=True)
class Grammar:
    """One identifier grammar: a pattern, a length bound and the rule in words.

    ``rule`` is value-free: it says what a valid value looks like, never what was given, so
    it can be shown for any input. ``extra`` holds the rules the pattern does not express;
    ``refine`` may name a sharper reason, also value-free, for a value the pattern refuses.
    """

    name: str
    pattern: str
    max_length: int
    rule: str
    extra: Callable[[str], str | None] | None = None
    refine: Callable[[str], str | None] | None = None
    _compiled: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", re.compile(self.pattern))

    def problem(self, value: str) -> str | None:
        """Why ``value`` is not valid, without the value; None when it is valid.

        The length is checked before the pattern, so no pattern runs on unbounded input.
        """
        if len(value) > self.max_length or self._compiled.fullmatch(value) is None:
            sharper = self.refine(value) if self.refine is not None else None
            return sharper or self.rule
        return self.extra(value) if self.extra is not None else None

    def is_valid(self, value: str) -> bool:
        return self.problem(value) is None

    def validate(self, value: str) -> str:
        """The pydantic validator: the value itself, or an error carrying the rule only."""
        problem = self.problem(value)
        if problem is not None:
            raise PydanticCustomError("release_identifier", problem)
        return value

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "string", "pattern": self.pattern, "maxLength": self.max_length}


# SemVer 2.0.0 numeric identifier: no leading zero. Pre-release identifiers as semver.org.
_NUMBER = "(?:0|[1-9][0-9]*)"
_PRERELEASE_ID = "(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
_CORE = rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}"
# SemVer itself, and the subset GORA accepts: a version is also the git tag v<version> and the
# end of the branch gora/reject/<artifact>/<version> (ADR-023 section 6), and git refuses a ref
# that ends in ".lock", so the last pre-release identifier after a dot may not be "lock".
_SEMVER = re.compile(rf"^{_CORE}(?:-{_PRERELEASE_ID}(?:\.{_PRERELEASE_ID})*)?$")
_GORA_VERSION = rf"^{_CORE}(?:-{_PRERELEASE_ID}(?:\.(?!lock$){_PRERELEASE_ID})*)?$"


def _sharper_version_reason(value: str) -> str | None:
    """The reason for a SemVer version GORA refuses: ``+BUILD`` appended, or a ``.lock`` end."""
    version, plus, _ = value.partition("+")
    if plus and VERSION.is_valid(version):
        return VERSION_BUILD_METADATA
    if value.endswith(".lock") and _SEMVER.fullmatch(value):
        return VERSION_LOCK
    return None


VERSION_BUILD_METADATA = (
    "must not carry build metadata (+...): a GORA version is SemVer 2.0.0 without it"
)
VERSION_LOCK = (
    "must not end in .lock: a GORA version is also a git tag (v<version>) and part of a "
    "branch name, and git refuses a ref that ends in .lock"
)

VERSION = Grammar(
    name="version",
    pattern=_GORA_VERSION,
    max_length=128,
    rule=(
        "must be a SemVer 2.0.0 version without build metadata: MAJOR.MINOR.PATCH with an "
        "optional -PRERELEASE, no leading zeros, no +BUILD, not ending in .lock (a version is "
        "also a git tag and part of a branch name), at most 128 characters"
    ),
    refine=_sharper_version_reason,
)

STAGE = Grammar(
    name="stage name",
    pattern=r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    max_length=63,
    rule=(
        "must be a lowercase DNS label (a-z, 0-9, -), letter first, letter or digit last, "
        "at most 63 characters"
    ),
)

DIGEST = Grammar(
    name="digest",
    pattern=r"^sha256:[a-f0-9]{64}$",
    max_length=71,
    rule="must be sha256: followed by exactly 64 lowercase hex digits",
)

# distribution/reference: a domain component, a path component and its separators.
_DOMAIN_COMPONENT = "[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"
_PATH_COMPONENT = "[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
# An explicit registry host: localhost, a name with a dot, or any name with a port.
_REGISTRY = (
    "(?:"
    "localhost(?::[0-9]+)?"
    rf"|{_DOMAIN_COMPONENT}(?:\.{_DOMAIN_COMPONENT})+(?::[0-9]+)?"
    rf"|{_DOMAIN_COMPONENT}:[0-9]+"
    ")"
)

IMAGE = Grammar(
    name="image",
    pattern=rf"^{_REGISTRY}/{_PATH_COMPONENT}(?:/{_PATH_COMPONENT})*$",
    max_length=255,
    rule=(
        "must be a fully qualified repository name: a registry host (with a dot, a port, or "
        "localhost), then lowercase path components; no tag, no digest, at most 255 characters"
    ),
)


def _repository_name(value: str) -> str | None:
    name = value.partition("/")[2]
    return REPO_DOT_NAME if name in (".", "..") else None


REPO_DOT_NAME = "must not name the repository . or .."

REPO = Grammar(
    name="repository",
    pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$",
    max_length=140,
    rule=(
        "must be owner/name: an owner of letters, digits and - (letter or digit first, at "
        "most 39), and a name of letters, digits, '.', '_' and - (at most 100)"
    ),
    extra=_repository_name,
)


# --- the identifiers of the release files (ADR-026 sections 3 to 9) ---------------------------

NAME = Grammar(
    name="name",
    pattern=STAGE.pattern,
    max_length=STAGE.max_length,
    rule=(
        "must be a lowercase DNS label (a-z, 0-9, -), letter first, letter or digit last, "
        "at most 63 characters, as a stage name"
    ),
)

RC_VERSION = Grammar(
    name="rc version",
    pattern=rf"^{_CORE}-rc$",
    max_length=128,
    rule=(
        "must be an rc version, MAJOR.MINOR.PATCH-rc (SemVer 2.0.0 core, no leading zeros), "
        "at most 128 characters"
    ),
)

FINAL_VERSION = Grammar(
    name="final version",
    pattern=rf"^{_CORE}$",
    max_length=128,
    rule=(
        "must be a final version, MAJOR.MINOR.PATCH (SemVer 2.0.0 core, no leading zeros), "
        "at most 128 characters"
    ),
)

# A GitHub login: letters and digits with single hyphens inside, at most 39, here in lower case.
_LOGIN = "[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}"

PRINCIPAL = Grammar(
    name="principal",
    pattern=rf"^(?:team/[a-z0-9][a-z0-9_-]{{0,99}}|user/{_LOGIN})$",
    max_length=105,
    rule=(
        "must be team/<slug> (lower-case letters, digits, - and _, letter or digit first, at "
        "most 100) or user/<login> (lower-case letters and digits with single hyphens inside, "
        "at most 39); lower case only, since the host compares these names without case"
    ),
)

REPORTER = Grammar(
    name="reporter",
    pattern=r"^app/[a-z0-9][a-z0-9-]{0,33}$",
    max_length=38,
    rule=(
        "must be app/<slug>, a GitHub App: lower-case letters, digits and -, letter or digit "
        "first, at most 34 characters"
    ),
)


def _source_repository(value: str) -> str | None:
    name = value.rpartition("/")[2]
    if name in (".", ".."):
        return SOURCE_REPOSITORY_DOT_NAME
    if name.endswith(".git"):
        return SOURCE_REPOSITORY_DOT_GIT
    return None


SOURCE_REPOSITORY_DOT_NAME = "must not name the repository . or .."
SOURCE_REPOSITORY_DOT_GIT = "must not end in .git: a repository is named by its path on the host"

SOURCE_REPOSITORY = Grammar(
    name="source repository",
    pattern=rf"^github\.com/{_LOGIN}/[a-z0-9._-]{{1,100}}$",
    max_length=151,
    rule=(
        "must be github.com/<owner>/<name> in lower case: an owner of letters and digits with "
        "single hyphens inside (at most 39), and a name of letters, digits, '.', '_' and - (at "
        "most 100), not . or .., not ending in .git"
    ),
    extra=_source_repository,
)

SOURCE_COMMIT = Grammar(
    name="source commit",
    pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$",
    max_length=64,
    rule="must be a git object name: 40 (SHA-1) or 64 (SHA-256) lower-case hex digits",
)

GLOB_EDGE_SLASH = "must not start or end with /"
GLOB_EMPTY_COMPONENT = "must not hold an empty component (//)"
GLOB_DOT_COMPONENT = "must not hold a component . or .."
GLOB_DOUBLE_STAR = "must use ** only as a whole component"
GLOB_DOUBLE_STAR_TWICE = "must not hold two ** components in a row"
GLOB_CLASS = (
    "must close every class [...] within its component, with at least one member: a letter, a "
    "digit, '.' or '_', or a range of two lower-case letters, two upper-case letters or two "
    "digits in order; a ] only closes a class"
)
_CLASS_MEMBER = re.compile(r"[a-z]-[a-z]|[A-Z]-[A-Z]|[0-9]-[0-9]|[A-Za-z0-9._]")


def _class_problem(component: str) -> str | None:
    """A class is [, members, ], inside one component; a ] outside a class is refused."""
    at = 0
    while at < len(component):
        char = component[at]
        if char == "]":
            return GLOB_CLASS
        if char != "[":
            at += 1
            continue
        end = component.find("]", at + 1)
        if end == -1:
            return GLOB_CLASS
        members = component[at + 1 : end]
        position = 0
        while position < len(members):
            member = _CLASS_MEMBER.match(members, position)
            if member is None or (
                len(member.group()) == 3 and member.group()[0] > member.group()[2]
            ):
                return GLOB_CLASS
            position = member.end()
        if not members:
            return GLOB_CLASS
        at = end + 1
    return None


def _input_glob(value: str) -> str | None:
    """The structure of an input glob (ADR-026 section 9), checked after its pattern."""
    if value.startswith("/") or value.endswith("/"):
        return GLOB_EDGE_SLASH
    components = value.split("/")
    if "" in components:
        return GLOB_EMPTY_COMPONENT
    if any(c in (".", "..") for c in components):
        return GLOB_DOT_COMPONENT
    if any("**" in c and c != "**" for c in components):
        return GLOB_DOUBLE_STAR
    if any(a == b == "**" for a, b in itertools.pairwise(components)):
        return GLOB_DOUBLE_STAR_TWICE
    for component in components:
        problem = _class_problem(component)
        if problem is not None:
            return problem
    return None


INPUT_GLOB = Grammar(
    name="input glob",
    pattern=r"^[A-Za-z0-9._@+=,~%#*?\[\]/-]+$",
    max_length=512,
    rule=(
        "must be a path glob of letters, digits and '._@+=,~%#-', with *, ?, [...] and /, at "
        "most 512 characters: no leading or trailing /, no empty, . or .. component, ** only "
        "as a whole component and never twice in a row, and every class within one component, "
        "never empty, of letters, digits, '.', '_' and ranges (a-z, A-Z, 0-9) in order"
    ),
    extra=_input_glob,
)

# A host of lower-case DNS labels; the port and the path follow it.
_HOST = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
_PORT = "(?::[1-9][0-9]{0,4})?"
URL_HOST_LENGTH = "must have a host of at most 253 characters"
URL_PORT_RANGE = "must have a port from 1 to 65535"


def _host_and_port(value: str) -> str | None:
    authority = value.removeprefix("https://").partition("/")[0]
    host, _, port = authority.partition(":")
    if len(host) > 253:
        return URL_HOST_LENGTH
    if port and int(port) > 65535:
        return URL_PORT_RANGE
    return None


OIDC_ISSUER = Grammar(
    name="OIDC issuer",
    pattern=rf"^https://{_HOST}{_PORT}(?:/[A-Za-z0-9._~%@+=,-]+)*/?$",
    max_length=512,
    rule=(
        "must be an https URL: a host of lower-case DNS labels (at most 253 characters), an "
        "optional port from 1 to 65535, and a path of letters, digits and '._~%@+=,-'; at most "
        "512 characters"
    ),
    extra=_host_and_port,
)

SIGNER_IDENTITY = Grammar(
    name="signer identity",
    pattern=rf"^https://{_HOST}{_PORT}/[A-Za-z0-9._~%@+=,/-]*$",
    max_length=1024,
    rule=(
        "must be an https URI, the identity of a workload, never an email address: a host of "
        "lower-case DNS labels (at most 253 characters), an optional port from 1 to 65535, and "
        "a path of letters, digits and '._~%@+=,/-'; at most 1024 characters"
    ),
    extra=_host_and_port,
)

# The identifiers only the release files of gora.tools/v1alpha1 hold (ADR-026).
RELEASE_FILE_GRAMMARS = (
    NAME,
    RC_VERSION,
    FINAL_VERSION,
    PRINCIPAL,
    REPORTER,
    SOURCE_REPOSITORY,
    SOURCE_COMMIT,
    INPUT_GLOB,
    OIDC_ISSUER,
    SIGNER_IDENTITY,
)
# Every grammar.
GRAMMARS = (VERSION, STAGE, DIGEST, IMAGE, REPO, *RELEASE_FILE_GRAMMARS)

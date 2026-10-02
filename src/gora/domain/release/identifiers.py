"""Identifier grammar of the release contract (ADR-003, "Identifier grammar").

Pure: no I/O. Every value that enters GORA as a version, release line, stage, digest, image,
repository or base branch is checked here, whether it arrives as a manifest field or as a CLI
argument. Each grammar is a plain checker (``problem``) and, where a manifest field holds it,
a pydantic ``Annotated`` type.

Every pattern is written in the common subset of Python ``re`` and ECMA-262: no named groups,
no lookbehind, no ``\\A``/``\\Z``, no possessive or atomic groups, no ``\\d``/``\\w``/``\\s`` and
no inline flags. The JSON Schema publishes them to consumers that use ECMA-262, where ``\\d``
is ASCII and ``$`` matches only at the end. Here a value must ``fullmatch``, so Python's ``$``,
which also matches before a final newline, cannot accept one.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import AfterValidator, WithJsonSchema
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


def _build_metadata(value: str) -> str | None:
    """The sharper reason for a valid version with ``+BUILD`` appended."""
    version, plus, _ = value.partition("+")
    return VERSION_BUILD_METADATA if plus and VERSION.is_valid(version) else None


VERSION_BUILD_METADATA = (
    "must not carry build metadata (+...): a GORA version is SemVer 2.0.0 without it"
)

VERSION = Grammar(
    name="version",
    pattern=rf"^{_CORE}(?:-{_PRERELEASE_ID}(?:\.{_PRERELEASE_ID})*)?$",
    max_length=128,
    rule=(
        "must be a SemVer 2.0.0 version without build metadata: MAJOR.MINOR.PATCH with an "
        "optional -PRERELEASE, no leading zeros, no +BUILD, at most 128 characters"
    ),
    refine=_build_metadata,
)

RELEASE_LINE = Grammar(
    name="release line",
    pattern=rf"^{_CORE}$",
    max_length=128,
    rule="must be MAJOR.MINOR.PATCH (SemVer 2.0.0 core, no leading zeros), at most 128 characters",
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


def _git_branch(value: str) -> str | None:
    """The git check-ref-format rules the character set alone does not exclude."""
    components = value.split("/")
    if (
        ".." in value
        or value.endswith(".")
        or "" in components  # a leading, trailing or doubled slash
        or any(c.startswith(".") or c.endswith(".lock") for c in components)
    ):
        return BRANCH.rule
    return None


BRANCH = Grammar(
    name="branch name",
    pattern=r"^[A-Za-z0-9._/-]{1,255}$",
    max_length=255,
    rule=(
        "must be a git branch name of letters, digits, '.', '_', '/' and -, at most 255 "
        "characters: no '..', no leading, trailing or doubled '/', no trailing '.', and no "
        "component that starts with '.' or ends in '.lock'"
    ),
    extra=_git_branch,
)

# The grammars a manifest field holds; the JSON Schema publishes each of their patterns.
MANIFEST_GRAMMARS = (VERSION, RELEASE_LINE, STAGE, DIGEST, IMAGE)
# Every grammar, including the ones only the CLI applies.
GRAMMARS = (*MANIFEST_GRAMMARS, REPO, BRANCH)

Version = Annotated[str, AfterValidator(VERSION.validate), WithJsonSchema(VERSION.json_schema)]
ReleaseLine = Annotated[
    str, AfterValidator(RELEASE_LINE.validate), WithJsonSchema(RELEASE_LINE.json_schema)
]
StageName = Annotated[str, AfterValidator(STAGE.validate), WithJsonSchema(STAGE.json_schema)]
Digest = Annotated[str, AfterValidator(DIGEST.validate), WithJsonSchema(DIGEST.json_schema)]
Image = Annotated[str, AfterValidator(IMAGE.validate), WithJsonSchema(IMAGE.json_schema)]


def release_line_of(version: str) -> str:
    """The release line of a valid version: its MAJOR.MINOR.PATCH.

    ``1.2.0-rc.3`` -> ``1.2.0``. A pre-release starts at the first ``-``; the core holds
    none, while a pre-release identifier may (``1.2.0-x-y`` -> ``1.2.0``).
    """
    problem = VERSION.problem(version)
    if problem is not None:
        raise ValueError(f"not a version: {problem}")
    return version.partition("-")[0]

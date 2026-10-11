"""Versions of candidates, computed from Conventional Commits (ADR-024 section 1).

Pure: no I/O and no git. A candidate's version is the version of the artifact's previous
registered candidate, raised by the highest bump among the messages of the commits since it
(ADR-024 section 1, release-model section 5). Which commits those are is the caller's. A
message is read by the grammar of Conventional Commits 1.0.0
(https://www.conventionalcommits.org/en/v1.0.0/), which ADR-024 names and does not restate;
where that grammar is silent the rule says "GORA's choice".

Every error is value-free, as in ``identifiers``: no message holds any part of an input, so an
error can be shown whatever the input was.

``Version``. GORA has two forms, ``X.Y.Z-rc`` (a candidate, in every stage before ``prod``) and
``X.Y.Z`` (the same digest once it has entered ``prod``). A version is three numbers and
``final``. The order of the fields is load-bearing: the generated comparison is SemVer 2.0
precedence for those two forms, numbers compared as numbers and ``X.Y.Z-rc`` before ``X.Y.Z``,
since ``False < True``. A version is refused when a number is not an ``int`` (a ``bool`` is
not one) or is negative, when ``final`` is not a ``bool``, when a number is ``10**128`` or more
(no version of 128 characters holds it, and Python would refuse to print it with a message of
its own), and when its text is not an rc version or a final version of ``identifiers``, which
is where a version of more than 128 characters is refused, wherever it was computed.
``Version.parse`` accepts exactly the texts those two grammars accept.

``Bump``. ``PATCH < MINOR < MAJOR``.

``change``. One commit message, as ``main`` holds it: the squash commit's subject and body.

- A line ends at ``\\n``. A ``\\r`` right before it belongs to the line end (GORA's choice: a
  message written with CRLF reads as one written with LF); a ``\\r`` anywhere else stays in
  its line. The subject is the first line, the body every line after it.
- A blank line is empty or holds only spaces and tabs (git's convention for trailers; GORA's
  choice, since Conventional Commits says only "blank line").
- A subject conforms when it is what ``scripts/gate/pr-title.sh`` accepts under ``LC_ALL=C``,
  ``<type>[(<scope>)][!]: <description>``, and holds no control character, so the gate and
  GORA read a title alike. The type is letters; the scope is one or more characters other
  than parentheses and white space; the description starts with a character that is not white
  space.
- A footer line (items 8 to 10) begins with a token, ``BREAKING CHANGE`` or a word (a letter
  or digit, then letters, digits and ``-``), followed by ``: `` or `` #``. A word never starts
  with ``-``, so a list line ``- #12 done`` is not one.
- The footers begin at the first line of the body that is a footer line and follows a blank
  line (item 8), and run to the end of the message (item 10: a value may hold newlines, and
  parsing ends only at the next token). With no such line there are none. The earliest such
  line is taken (GORA's choice: Conventional Commits does not say where a body that holds a
  ``Token: `` paragraph ends), so no footer a person wrote is read as body.
- A breaking-change footer is a line of the footers that begins with ``BREAKING CHANGE: `` or
  ``BREAKING-CHANGE: ``, in capitals (items 12, 15 and 16; holding the synonym to capitals as
  well is GORA's choice). Its description may be on that line or on the lines after it, so
  nothing is required after the separator. A marker anywhere else (in a paragraph of the
  body, right under the subject, after a space or a list marker, in lower case, misspelled)
  is not read.
- A subject that does not conform counts as a patch (release-model section 5): no footers
  are read. Otherwise the type is read in lower case (item 15), the change is breaking when
  the subject has ``!`` or the footers hold a breaking-change footer, and its bump is
  ``MAJOR`` when breaking, ``MINOR`` for ``feat`` and ``PATCH`` for any other type. A revert
  is no exception (ADR-024 section 1): its own message decides.

``subject``. The subject as ``change`` reads it. When it conforms, its scope (``None`` without
one) and its description, everything after ``: `` as written; otherwise the subject as it is.
The change log lists a commit by it (``changelog``).

``bump_of``. The highest bump of the changes. A candidate always follows at least one commit
that changed its inputs, so none is a defect of the caller and is refused.

``next_version``. The previous candidate's version must be an rc version. When ``stable`` is
set and its major version is ``0``, the result is ``1.0.0-rc`` whatever the bump: the major
version leaves ``0`` once, and the next candidate is at least ``1.0.0-rc``. Otherwise a
``MAJOR`` bump counts as ``MINOR`` while the major version is ``0``; then ``MAJOR`` raises the
major version and resets the others, ``MINOR`` raises the minor version and resets the patch,
and ``PATCH`` raises the patch. The result is always an rc version, and newer than the
previous one.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from typing import Self

from gora.domain.release.identifiers import FINAL_VERSION, RC_VERSION

# No version of 128 characters holds a number of this size, and printing a larger one would
# make Python raise with a message of its own.
_NUMBER_BOUND = 10**128

_NOT_AN_INT = "a version number is an int"
_NEGATIVE = "a version number is not negative"
_FINAL_NOT_A_BOOL = "final is a bool"
_UNKNOWN_FORM = f"{RC_VERSION.rule}; or {FINAL_VERSION.rule}"
_NO_CHANGE = "a candidate follows at least one change"
_FINAL_PREVIOUS = "the previous candidate's version is an rc version"


@dataclass(frozen=True, order=True)
class Version:
    """``X.Y.Z-rc`` or ``X.Y.Z``, ordered as SemVer 2.0 orders those two forms.

    The order of the fields is the order of comparison: the numbers, then ``final``, so that
    ``X.Y.Z-rc`` is before ``X.Y.Z`` and both are before ``X.Y.(Z+1)-rc``.
    """

    major: int
    minor: int
    patch: int
    final: bool

    def __post_init__(self) -> None:
        numbers = (self.major, self.minor, self.patch)
        if any(isinstance(n, bool) or not isinstance(n, int) for n in numbers):
            raise ValueError(_NOT_AN_INT)
        if any(n < 0 for n in numbers):
            raise ValueError(_NEGATIVE)
        if not isinstance(self.final, bool):
            raise ValueError(_FINAL_NOT_A_BOOL)
        grammar = FINAL_VERSION if self.final else RC_VERSION
        if any(n >= _NUMBER_BOUND for n in numbers):
            raise ValueError(grammar.rule)
        problem = grammar.problem(str(self))
        if problem is not None:
            raise ValueError(problem)

    def __str__(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return core if self.final else f"{core}-rc"

    @classmethod
    def parse(cls, text: str) -> Self:
        """The version ``text`` writes; a ``ValueError`` naming both forms when it is neither."""
        final = FINAL_VERSION.is_valid(text)
        if not final and not RC_VERSION.is_valid(text):
            raise ValueError(_UNKNOWN_FORM)
        major, minor, patch = (int(part) for part in text.removesuffix("-rc").split("."))
        return cls(major, minor, patch, final)


class Bump(IntEnum):
    """The size of a change to a version. The order of the values is the order of the bumps."""

    PATCH = 1
    MINOR = 2
    MAJOR = 3


@dataclass(frozen=True)
class Change:
    """What one commit message says: whether it conforms, its type, and the bump it asks."""

    conforming: bool
    commit_type: str | None
    breaking: bool
    bump: Bump


# The subject as scripts/gate/pr-title.sh reads it with LC_ALL=C: <type>[(<scope>)][!]: then one
# character that is not white space. White space is written out, and so is the control class.
# The groups are the type, the scope and the "!".
_SUBJECT = re.compile(r"([A-Za-z]+)(?:\(([^() \t\n\v\f\r]+)\))?(!)?: [^ \t\n\v\f\r]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BLANK = re.compile(r"[ \t]*")
_FOOTER_LINE = re.compile(r"(?:BREAKING CHANGE|[A-Za-z0-9][A-Za-z0-9-]*)(?:: | #)")
_BREAKING_FOOTERS = ("BREAKING CHANGE: ", "BREAKING-CHANGE: ")


def _lines(message: str) -> list[str]:
    """The lines of a message: split at ``\\n``, a ``\\r`` before it taken off the line."""
    *ended, last = message.split("\n")
    return [*(line.removesuffix("\r") for line in ended), last]


def _breaking_footer(body: list[str]) -> bool:
    """Whether the footers of ``body`` hold a breaking-change footer."""
    for at in range(1, len(body)):
        if _BLANK.fullmatch(body[at - 1]) and _FOOTER_LINE.match(body[at]):
            return any(line.startswith(_BREAKING_FOOTERS) for line in body[at:])
    return False


@dataclass(frozen=True)
class Subject:
    """The subject of one commit message: its scope and its description when it conforms, and
    the subject as it is otherwise."""

    conforming: bool
    scope: str | None
    description: str


def _subject(line: str) -> re.Match[str] | None:
    found = _SUBJECT.match(line)
    return None if found is None or _CONTROL.search(line) is not None else found


def subject(message: str) -> Subject:
    """The subject of ``message``, the first of its lines, read as ``change`` reads it."""
    line = _lines(message)[0]
    found = _subject(line)
    if found is None:
        return Subject(conforming=False, scope=None, description=line)
    return Subject(conforming=True, scope=found.group(2), description=line[found.end() - 1 :])


def change(message: str) -> Change:
    """Read one commit message, the squash commit's subject and body, by Conventional Commits."""
    lines = _lines(message)
    found = _subject(lines[0])
    if found is None:
        return Change(conforming=False, commit_type=None, breaking=False, bump=Bump.PATCH)
    commit_type = found.group(1).lower()
    breaking = found.group(3) is not None or _breaking_footer(lines[1:])
    if breaking:
        bump = Bump.MAJOR
    elif commit_type == "feat":
        bump = Bump.MINOR
    else:
        bump = Bump.PATCH
    return Change(conforming=True, commit_type=commit_type, breaking=breaking, bump=bump)


def bump_of(changes: Iterable[Change]) -> Bump:
    """The highest bump of ``changes``; a candidate follows at least one."""
    highest = max((c.bump for c in changes), default=None)
    if highest is None:
        raise ValueError(_NO_CHANGE)
    return highest


def next_version(previous: Version, bump: Bump, *, stable: bool) -> Version:
    """The version of the candidate that follows the one at ``previous``, raised by ``bump``."""
    if previous.final:
        raise ValueError(_FINAL_PREVIOUS)
    if stable and previous.major == 0:
        return Version(1, 0, 0, final=False)
    if bump is Bump.MAJOR and previous.major == 0:
        bump = Bump.MINOR
    match bump:
        case Bump.MAJOR:
            return Version(previous.major + 1, 0, 0, final=False)
        case Bump.MINOR:
            return Version(previous.major, previous.minor + 1, 0, final=False)
        case Bump.PATCH:
            return Version(previous.major, previous.minor, previous.patch + 1, final=False)

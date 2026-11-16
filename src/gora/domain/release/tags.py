"""A candidate's rc tag: its name, the message GORA writes, and the record GORA reads back
(ADR-022 section 6, ADR-024 section 1, ADR-027 sections 1 and 4).

Pure: no I/O and no git. Creating the tag, and listing and reading tags, are the adapters'.

``is_version_tag`` and ``Tag``. The value a code host gives for a version tag: the name GORA
gives a version's tag, ``<tag prefix>v<version>``, its commit and its message, or none for a
lightweight tag (ADR-006). ``is_version_tag`` says whether a name is one, and ``Tag`` holds
only one.

``rc_tag``. The name of an artifact's rc tag: its tag prefix, ``v`` and the version, as
``v1.2.0-rc`` or ``api/v1.2.0-rc`` (ADR-026 section 5).

``rc_tag_message``. The message of the annotated tag, written once when the candidate is
registered. Its lines, each ending with ``\\n``:

- ``<artifact> <version>``, then a blank line;
- ``Image: <image>@<digest>`` and ``Policy digest: <policy digest>``, then a blank line;
- the change log of the candidate's changes (``changelog``), then a blank line;
- the trailer ``Gora-candidate: <artifact> <version> <digest>``, the last line.

GORA writes it byte by byte and does not rely on git's cleanup of a message: no line begins
with ``#`` or ends with white space, and no blank line follows another, so ``git tag`` would
keep it as it is. The caller's errors are checked first, in this order: the artifact, the
image, the digest, the policy digest, a final version, no change, then the object name of a
change.

``recorded``. The candidate a tag records, read back from its trailer (ADR-027 section 1): the
change log is never read. The last line of the message, after a final ``\\n`` is taken off,
must be the trailer, and the line before it blank: empty, or spaces and tabs only (git also
counts a line of ``\\r`` as blank; GORA does not, since it never writes one). The key is read
in any case, as git reads a trailer key (ADR-023 section 6), and must be followed by ``: `` and
three fields separated by single spaces: a name, an rc version and a digest. The trailer must
name the artifact and the version the tag's name names.

The trailer is an index, not a ground for trust. Once a project is connected, only GORA's
identity creates version tags (L10), so a tag's trailer is GORA's; but whoever reads it checks
the digest again: a promotion verifies its evidence (ADR-018 section 4), and a registration
asked again for a commit that carries the tag compares the digest it is given with the one the
tag records (ADR-024 section 1).

A caller's error is a ``ValueError``: an artifact, an image, a digest, a tag prefix or a
version outside its grammar, a final version, a tag prefix that is neither empty nor the
artifact's name and ``/``, a candidate with no change, a tag that is not the artifact's rc tag;
these are checked before the message is read. A tag whose message does not record its
candidate is a ``RecordError``, not a ``ValueError``, with a reason. Every message is
value-free, as in ``identifiers``.
"""

import re
from dataclasses import dataclass

from gora.domain.release.candidates import Candidate
from gora.domain.release.changelog import change_log
from gora.domain.release.identifiers import (
    DIGEST,
    IMAGE,
    NAME,
    RC_VERSION,
    SOURCE_COMMIT,
    VERSION,
    Grammar,
)
from gora.domain.release.versions import Version
from gora.domain.release_repository.formats import TAG_PREFIX

TRAILER = "Gora-candidate"

# A tag prefix, ``v`` and a version, each at its grammar's longest: 64 + 1 + 128.
MAX_TAG_NAME = TAG_PREFIX.max_length + 1 + VERSION.max_length

NOT_A_VERSION_TAG = "the name is a version tag: its tag prefix, v and a version"
NOT_A_MESSAGE = "the message is a string, or None for a lightweight tag"

FINAL = "the version is an rc version"
NO_CHANGE = "a candidate lists at least one change"
PREFIX = "the tag prefix is empty, or the artifact's name followed by /"
NOT_THE_TAG = "the tag is the artifact's rc tag: its tag prefix, v and an rc version"

NO_TRAILER = "the message ends with its Gora-candidate trailer, after a blank line"
FIELDS = "the trailer holds a name, an rc version and a digest, separated by single spaces"
ANOTHER = "the trailer names the artifact and the version the tag's name names"

_BLANK = re.compile(r"[ \t]*")


class RecordError(Exception):
    """A tag whose message does not record the candidate its name names, with a reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True)
class Recorded:
    """What an rc tag records of its candidate: its version and its digest."""

    version: Version
    digest: str


def is_version_tag(name: str) -> bool:
    """Whether ``name`` is the name of a version's tag: ``<tag prefix>v<version>``.

    The part up to and including the last ``/`` is a tag prefix, empty or a name and ``/``, and
    the rest is ``v`` and a version. A version holds no ``/``, so the last one ends the prefix.
    """
    if len(name) > MAX_TAG_NAME:
        return False
    prefix, slash, rest = name.rpartition("/")
    if not TAG_PREFIX.is_valid(prefix + slash):
        return False
    return rest.startswith("v") and VERSION.is_valid(rest[1:])


@dataclass(frozen=True)
class Tag:
    """A version tag as a code host gives it: what GORA reads of one, and no more.

    ``name`` is the tag's name, ``<tag prefix>v<version>`` (``is_version_tag``); ``commit`` the
    commit it points at, by its object name; ``message`` the text of its annotation, or None for
    a lightweight tag, which has none. The message is the host's text: it is read for what it
    records (``recorded``) and never shown, so it holds whatever the host sent.
    """

    name: str
    commit: str
    message: str | None

    def __post_init__(self) -> None:
        if not (isinstance(self.name, str) and is_version_tag(self.name)):
            raise ValueError(NOT_A_VERSION_TAG)
        if not (isinstance(self.commit, str) and SOURCE_COMMIT.is_valid(self.commit)):
            raise ValueError(f"commit {SOURCE_COMMIT.rule}")
        if self.message is not None and not isinstance(self.message, str):
            raise ValueError(NOT_A_MESSAGE)


def rc_tag(tag_prefix: str, version: Version) -> str:
    """The name of the rc tag of ``version``, under ``tag_prefix``."""
    if not TAG_PREFIX.is_valid(tag_prefix):
        raise ValueError(f"tag prefix {TAG_PREFIX.rule}")
    if version.final:
        raise ValueError(FINAL)
    return f"{tag_prefix}v{version}"


def rc_tag_message(
    candidate: Candidate, *, artifact: str, image: str, digest: str, policy_digest: str
) -> str:
    """The message of ``candidate``'s rc tag, for the build ``image@digest``."""
    _check(NAME, "artifact", artifact)
    _check(IMAGE, "image", image)
    _check(DIGEST, "digest", digest)
    _check(DIGEST, "policy digest", policy_digest)
    if candidate.version.final:
        raise ValueError(FINAL)
    if not candidate.changes:
        raise ValueError(NO_CHANGE)
    version = candidate.version
    return (
        f"{artifact} {version}\n"
        "\n"
        f"Image: {image}@{digest}\n"
        f"Policy digest: {policy_digest}\n"
        "\n"
        f"{change_log(candidate.changes)}"
        "\n"
        f"{TRAILER}: {artifact} {version} {digest}\n"
    )


def recorded(tag: str, message: str, *, artifact: str, tag_prefix: str) -> Recorded:
    """The candidate the rc tag ``tag`` records in ``message``; ``RecordError`` when it does not."""
    _check(NAME, "artifact", artifact)
    if tag_prefix not in ("", f"{artifact}/"):
        raise ValueError(PREFIX)
    text = tag.removeprefix(f"{tag_prefix}v")
    if text == tag or not RC_VERSION.is_valid(text):
        raise ValueError(NOT_THE_TAG)
    lines = message.removesuffix("\n").split("\n")
    if len(lines) < 2 or not _BLANK.fullmatch(lines[-2]):
        raise RecordError(NO_TRAILER)
    key, separator, value = lines[-1].partition(": ")
    if not separator or key.lower() != TRAILER.lower():
        raise RecordError(NO_TRAILER)
    fields = value.split(" ")
    if len(fields) != 3:
        raise RecordError(FIELDS)
    name, version, digest = fields
    if not (NAME.is_valid(name) and RC_VERSION.is_valid(version) and DIGEST.is_valid(digest)):
        raise RecordError(FIELDS)
    if name != artifact or version != text:
        raise RecordError(ANOTHER)
    return Recorded(Version.parse(version), digest)


def _check(grammar: Grammar, what: str, value: str) -> None:
    if not grammar.is_valid(value):
        raise ValueError(f"{what} {grammar.rule}")

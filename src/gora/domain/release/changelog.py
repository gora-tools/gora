"""The change log of a range of an artifact's versions (ADR-027 section 4).

Pure: no I/O and no git. The caller passes the commits of the range, oldest first: the
first-parent commits of ``main`` that changed the artifact's inputs, the same commits that
compute a version (``candidates``, ADR-024 section 1).

``change_log`` groups them, in this order: breaking changes, features, fixes, then every other
change, each group in the order given. A commit is breaking when ``versions.change`` reads it
so, whatever its type; otherwise a feature when its type is ``feat`` and a fix when it is
``fix``, in any case. A subject that does not conform goes to the last group as it is.

Each group is a line that names it, ``Breaking changes:``, ``Features:``, ``Fixes:`` or
``Other changes:``, then one line per commit: ``- ``, the description, `` (<scope>)`` when it
has one, and the first 12 hexadecimal digits of the commit. A group with no commit is left out;
one blank line separates two groups, and every line ends with ``\\n``. Plain text that reads as
Markdown, with no line that begins with ``#``: ``git tag`` strips such a line from a message by
default. No author: nothing in Git names a person (ADR-023 section 6).

A character no message of a tag can hold, NUL (git ends a message there) or a lone surrogate (a
message is UTF-8), is written as U+FFFD. Refusing it instead would refuse every later candidate
whose change log lists that commit.

A range with no commit has the empty change log. A caller's error is a ``ValueError``: a commit
whose object name is not a source commit. Every message is value-free, as in ``identifiers``.
"""

import re
from collections.abc import Sequence

from gora.domain.release.candidates import Commit
from gora.domain.release.identifiers import SOURCE_COMMIT
from gora.domain.release.versions import change, subject

BREAKING = "Breaking changes:"
FEATURES = "Features:"
FIXES = "Fixes:"
OTHER = "Other changes:"
GROUPS = (BREAKING, FEATURES, FIXES, OTHER)

# What no message of a tag can hold: git ends a message at a NUL, and a message is UTF-8.
_NOT_TEXT = re.compile("[\x00\ud800-\udfff]")


def change_log(commits: Sequence[Commit]) -> str:
    """The change log of ``commits``, oldest first: its groups, in the order of ``GROUPS``."""
    for commit in commits:
        if not SOURCE_COMMIT.is_valid(commit.sha):
            raise ValueError(f"source commit {SOURCE_COMMIT.rule}")
    groups: dict[str, list[str]] = {group: [] for group in GROUPS}
    for commit in commits:
        groups[_group(commit.message)].append(_line(commit))
    blocks = [[group, *lines] for group, lines in groups.items() if lines]
    return "\n".join("".join(f"{line}\n" for line in block) for block in blocks)


def _group(message: str) -> str:
    read = change(message)
    if read.breaking:
        return BREAKING
    if read.commit_type == "feat":
        return FEATURES
    if read.commit_type == "fix":
        return FIXES
    return OTHER


def _line(commit: Commit) -> str:
    read = subject(commit.message)
    description = _NOT_TEXT.sub("\ufffd", read.description)
    words = [description] if description else []
    if read.scope is not None:
        scope = _NOT_TEXT.sub("\ufffd", read.scope)
        words.append(f"({scope})")
    words.append(commit.sha[:12])
    return "- " + " ".join(words)

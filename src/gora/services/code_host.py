"""The code host's state, read through one port (ADR-006).

GORA reads the state of a repository, the head of a branch, its tags and what they record,
from the code host; a clone only supplies content addressed by name. ``CodeHost`` is what the
services need of the adapter that reads one, and each host has an adapter that serves it
(``gora.adapters.code_hosts`` says which one reads a repository). It is owned by its consumer,
as ``SlackPoster`` is: an adapter is checked against it and imports nothing of a service.
"""

from typing import Protocol

from gora.domain.release.tags import Tag


class CodeHost(Protocol):
    """The four reads of a repository's state, and no write.

    The contract every adapter keeps:

    - **No write.** An adapter of this port holds no method that changes the host.
    - **Every answer is checked against what was asked.** A ref, a name or a sha the host
      sends back is the one asked for, and an answer that is not the expected one is the
      adapter's error, never a value.
    - **A missing branch or tag is an error,** never an empty answer: a host answers the same
      for a missing branch and for a repository the caller cannot see, so no "not found" is a
      fact the caller may use.
    - **A caller's mistake is a ``ValueError``,** raised before any request: a branch that is
      empty or would leave its path, a tag prefix or a name outside its grammar.
    """

    def repository_id(self) -> str:
        """The host's identifier of the repository, in decimal digits (``REPOSITORY_ID``):
        its identity, since a name can be moved."""
        ...

    def branch_head(self, branch: str) -> str:
        """The commit ``branch`` points at, by its object name."""
        ...

    def tag_names(self, tag_prefix: str) -> tuple[str, ...]:
        """Every name the host holds under ``{tag_prefix}v``, in its order.

        The prefix is a string prefix, so ``vnext`` is among the names of ``v``: which names are
        version tags is the domain's to decide (``is_version_tag``), and the adapter returns what
        the host matched. ``tag_prefix`` is a tag prefix: empty, or an artifact's name and ``/``.
        """
        ...

    def tag(self, name: str) -> Tag:
        """The version tag ``name``: its commit and its message, or none for a lightweight tag.

        ``name`` is a version tag (``is_version_tag``), the name of a tag the host holds.
        """
        ...

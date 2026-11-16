"""Which adapter reads a repository: the code host its full name begins with (ADR-006).

A repository is named by its full name, ``<host>/<owner>/<name>``, the form a release file
holds (``SOURCE_REPOSITORY``). The part before the first ``/`` is the host, and the adapter
that reads it is one entry of one read-only mapping, with its own grammar of a full name and
its own setting, which its factory reads from ``Settings``. A second host is a second entry:
there is no registration function and no plug-in mechanism, so what GORA reads is what this
module names.

``code_host`` takes a full name and the settings and returns the port (``CodeHost``). A caller's
error is a ``ValueError`` before any request: a host with no entry, which is named through
``gora.text.visible`` (an empty host, and one too long to name, have their own words), a full
name its host's grammar refuses, an empty token. A message is value-free but for the host, and
never holds a token.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType

import httpx2

from gora.adapters.github import GitHubReader
from gora.config.settings import Settings
from gora.domain.release.identifiers import SOURCE_REPOSITORY
from gora.services.code_host import CodeHost
from gora.text import visible

GITHUB_VAR = "GORA_GITHUB_TOKEN"

# The longest host name there is: a DNS name of 253 characters.
MAX_HOST = 253

NO_HOST = "a repository's full name begins with its host"
HOST_TOO_LONG = f"the host of a repository's full name is over {MAX_HOST} characters"
NO_TOKEN = f"{GITHUB_VAR} is required to read a repository on github.com"

type Factory = Callable[[str, Settings, httpx2.BaseTransport | None], CodeHost]


def _github(
    repository: str, settings: Settings, transport: httpx2.BaseTransport | None
) -> CodeHost:
    problem = SOURCE_REPOSITORY.problem(repository)
    if problem is not None:
        raise ValueError(f"repository {problem}")
    token = settings.github_token.get_secret_value()
    if not token:
        raise ValueError(NO_TOKEN)
    return GitHubReader(repository.removeprefix("github.com/"), token, transport=transport)


# One entry for each host GORA reads. The adapter is checked against ``CodeHost`` where its
# factory returns it.
CODE_HOSTS: Mapping[str, Factory] = MappingProxyType({"github.com": _github})


def code_host(
    repository: str, settings: Settings, *, transport: httpx2.BaseTransport | None = None
) -> CodeHost:
    """The adapter that reads ``repository``, a full name, by the host the name begins with."""
    host = repository.partition("/")[0]
    if not host:
        raise ValueError(NO_HOST)
    if len(host) > MAX_HOST:
        raise ValueError(HOST_TOO_LONG)
    factory = CODE_HOSTS.get(host)
    if factory is None:
        raise ValueError(f"no adapter reads the code host {visible(host)}")
    return factory(repository, settings, transport)

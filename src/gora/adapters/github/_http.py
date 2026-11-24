"""What the GitHub reader and the GitHub writer share of GitHub (ADR-006).

The request path is not here. Every request of both is sent by ``gora.adapters.egress``'s
``BoundedRequest``, which every adapter shares, so no bound is written twice and none is missing
from one of the two: the deadline, the cap on the body, the cap on a path, the refused redirect,
the refused encoding, no retry, the safe text of an error (read the module's docstring for each).
``HttpCore`` is GitHub's configuration of it, and what is GitHub's lives here:

- one host, ``GITHUB_API``, a constant of the code that no caller can change, and the headers
  of every request: the token header, ``Accept: application/vnd.github+json`` and
  ``X-GitHub-Api-Version``. A test hands over a transport, never a URL or a client;
- the one place a GitHub token is taken, and what it takes (L22): ``HttpCore`` refuses with a
  ``ValueError``, before any client is built and in this order, a token that is not the
  ``b64token`` of a Bearer token (RFC 6750 section 2.1), and a token that is not an
  installation token of a GitHub App, which starts with ``ghs_``. The tokens of a user account
  (``ghp_``, ``github_pat_``, ``gho_``, ``ghu_``, ``ghr_``) are refused, whoever hands one over.
  Nothing else of a token is read: not its length, its characters beyond the first rule, nor a
  structure GitHub does not promise to keep, so any installation token passes, another App's
  and the ``GITHUB_TOKEN`` of Actions included; telling GORA's App's token from them is left to
  the pipeline step of the release workflow (ADR-022 section 14). A message holds no part of the
  token;
- the host's text of an error status, shown only as the ``message`` of its JSON body
  (``{"message": "Not Found", ...}``); a 403 or a 429 also names the values of
  ``X-RateLimit-Reset`` and ``Retry-After``, when present, and no other header. GitHub answers
  404 for a missing branch as for a repository the token cannot see, so no 404 is an answer:
  nothing here reads one as "there is no such file";
- ``GitHubError``, the one error that leaves the adapters.

The boundary is the adapters': whatever goes wrong in a call, a transport error, a URL httpx2
refuses, a stream the transport handed over already read or closed, a refused answer or a body
that is not what was expected, leaves as ``GitHubError``, whose message names the operation and
holds no token, no query string and nothing httpx2 wrote.
A body that does not parse as JSON, however it fails (not UTF-8, nested past what the parser
reads), is one such body. A value placed in a URL path never leaves the operation it was built
for: a part that is empty, ``.`` or ``..`` is a caller's ``ValueError`` before any request, as
is a path over the cap of ``egress.MAX_PATH``.
"""

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx2

from gora.adapters import egress
from gora.adapters.egress import UnexpectedError
from gora.domain.release.identifiers import REPO

GITHUB_API = "https://api.github.com"

PATH_PART = "a part of a path must not be empty, . or .."

# The token of the ``Bearer`` scheme GORA sends: RFC 6750 section 2.1, ``b64token``. Letters,
# digits and ``- . _ ~ + /``, then ``=`` only at the end. It is the standard of a Bearer token and
# not a structure of GitHub's, and both of GitHub's installation token forms fit it.
B64TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")
NOT_BEARER = "the GitHub token is not a Bearer token (RFC 6750, b64token)"
INSTALLATION_PREFIX = "ghs_"
NOT_INSTALLATION = (
    "the GitHub token is not an installation token of a GitHub App: GORA takes no person's "
    "token (L22)"
)


class GitHubError(RuntimeError):
    """A GitHub API call failed: an error status, a transport error, or a bad response."""


_GITHUB = egress.Host(
    api=GITHUB_API,
    error=GitHubError,
    text_field="message",
    rate_limits=dict.fromkeys((403, 429), ("x-ratelimit-reset", "retry-after")),
)


def segment(value: str) -> str:
    """A value placed in a URL path, ``/`` kept: GitHub reads it in refs and content paths.

    ``quote`` keeps ``.``, and httpx2 removes dot segments, so a part that is ``.`` or ``..``
    would send the request to another path than the operation's, and an empty part is no part:
    each is a caller's error, a ``ValueError``, before any request.
    """
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(PATH_PART)
    return quote(value, safe="/")


def checked_repository(repo: str) -> str:
    """``repo``, or ``ValueError`` when the repository grammar refuses it (ADR-003)."""
    problem = REPO.problem(repo)
    if problem is not None:
        raise ValueError(f"GitHub repository {problem}")
    return repo


def check_token(token: str) -> None:
    """``ValueError``, with a message that holds no part of ``token``, unless it is the token of a
    GitHub App installation in the form of a Bearer token.

    In order: a token that is not a ``b64token``; one that is not ``ghs_`` and at least one more
    character, compared with case. The empty token has none of the first rule's characters and is
    refused by the second, whose message says what is wanted.
    """
    if token and B64TOKEN.fullmatch(token) is None:
        raise ValueError(NOT_BEARER)
    if not token.startswith(INSTALLATION_PREFIX) or len(token) == len(INSTALLATION_PREFIX):
        raise ValueError(NOT_INSTALLATION)


def read_field[T](operation: str, read: Callable[[], T]) -> T:
    """Read a value out of an answer; one that is not the expected one is a GitHubError."""
    try:
        return read()
    except UnexpectedError as exc:
        raise GitHubError(f"{operation} failed: unexpected response ({exc.reason})") from exc
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise GitHubError(f"{operation} failed: unexpected response") from exc


class HttpCore(egress.BoundedRequest):
    """GitHub's configuration of the bounded request, and the JSON object most calls read."""

    def __init__(
        self, token: str, *, timeout: float, transport: httpx2.BaseTransport | None = None
    ) -> None:
        check_token(token)
        super().__init__(
            _GITHUB,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
            transport=transport,
        )

    def json_object(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """The body of a request, which must be a JSON object."""
        answer = self.send(method, path, json=json)
        data = read_field(f"{method} {path}", answer.json)
        if not isinstance(data, dict):
            raise GitHubError(f"{method} {path} failed: unexpected response")
        return data

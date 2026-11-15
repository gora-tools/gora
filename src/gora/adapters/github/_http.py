"""The HTTP core the GitHub reader and the GitHub writer share (ADR-006).

One request path, so no bound is written twice and none is missing from one of the two. For
every request of both:

- one host, ``GITHUB_API``, a constant of the code that no caller can change, and the client
  ``gora.adapters.egress`` builds: the token header, ``Accept: application/vnd.github+json``
  and ``X-GitHub-Api-Version``, TLS over GORA's own context, and no variable of the
  environment read. A test hands over a transport, never a URL or a client;
- httpx's timeout, 10 s unless given, for the connection and for each read, and a deadline for
  the whole request, body included: ``DEADLINE``. It is checked between HTTP framing events,
  when the headers arrive and as each chunk of the body does. Within one event (the headers,
  a chunk header, a trailer), which is at most 100 KiB (httpcore's limit on an incomplete
  event), each read is bounded by httpx's read timeout and no total deadline applies, so a
  host that sends such framing a byte at a time outlasts the deadline. The transport is TLS
  to ``api.github.com``, verified against the CA certificates of ``certifi``, so only that host
  can send such framing;
- a cap on the body, ``MAX_BODY``, checked against the length the host declares and against
  what arrives, as each chunk does: a body over it is refused, never read short. The body is
  read as it arrives, never decoded: every request asks for ``Accept-Encoding: identity``, an
  answer whose ``Content-Encoding`` is present and not ``identity`` is refused, and the bytes
  counted are the bytes sent, so a stream that decodes to nothing cannot outlast the bounds;
- a 3xx is refused and no redirect is followed: the identity of a repository is its id, never
  a name a redirect leads to;
- a status of 400 or more, a 404 included, is an error that names the status and shows the
  host's text only as the ``message`` of its JSON body (``{"message": "Not Found", ...}``),
  when that is printable ASCII of at most 200 characters, and nothing else of the body; a 403
  or a 429 also names the values of ``X-RateLimit-Reset`` and ``Retry-After``, when present,
  and no other header. GitHub answers 404 for a missing branch as for a repository the token
  cannot see, so no 404 is an answer: nothing here reads one as "there is no such file";
- no retry, for any status: a run stays deterministic and the caller sees the host's answer
  (ADR-006: retry semantics are defined per operation, and for these there are none).

The boundary is the adapters': whatever goes wrong in a call, a transport error, a URL httpx
refuses, a stream the transport handed over already read or closed, a refused answer or a body
that is not what was expected, leaves as ``GitHubError``, whose message names the operation and
holds no token, no query string and nothing httpx wrote.
A body that does not parse as JSON, however it fails (not UTF-8, nested past what the parser
reads), is one such body. A value placed in a URL path never leaves the operation it was built
for: a part that is empty, ``.`` or ``..`` is a caller's ``ValueError`` before any request.
"""

import json as jsonlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from gora.adapters import egress
from gora.domain.release.identifiers import REPO

GITHUB_API = "https://api.github.com"

DEADLINE = 60.0  # seconds: 48x the slowest measured answer, 1.25 s for 8,424 refs (ADR-006)
MAX_BODY = 64 * 1024 * 1024  # bytes: 17x the largest measured, 3.86 MB for 8,424 refs

# The host's text, a header value or the message of an error body, is shown only when it is
# printable ASCII and short: an error message reaches a terminal, and the host's text is not
# trusted to be safe there.
_PRINTABLE = re.compile(r"[ -~]+")
_HEADER_LIMIT = 64
_MESSAGE_LIMIT = 200
_DIGITS = re.compile(r"[0-9]{1,18}")
_RATE_LIMIT_HEADERS = ("x-ratelimit-reset", "retry-after")

NOT_JSON = "the body is not JSON"
PATH_PART = "a part of a path must not be empty, . or .."


class GitHubError(RuntimeError):
    """A GitHub API call failed: an error status, a transport error, or a bad response."""


class UnexpectedError(Exception):
    """An answer that is not the one asked for, with a reason that holds no value of it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Answer:
    """What the host answered: its status, its headers and the whole body, read in the bounds."""

    status: int
    headers: httpx.Headers
    body: bytes

    def json(self) -> Any:
        """The body as JSON; one that does not parse, for any reason, is an ``UnexpectedError``."""
        try:
            return jsonlib.loads(self.body)
        except (ValueError, RecursionError) as exc:  # a UnicodeDecodeError is a ValueError
            raise UnexpectedError(NOT_JSON) from exc


def segment(value: str) -> str:
    """A value placed in a URL path, ``/`` kept: GitHub reads it in refs and content paths.

    ``quote`` keeps ``.``, and httpx removes dot segments, so a part that is ``.`` or ``..``
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


def read_field[T](operation: str, read: Callable[[], T]) -> T:
    """Read a value out of an answer; one that is not the expected one is a GitHubError."""
    try:
        return read()
    except UnexpectedError as exc:
        raise GitHubError(f"{operation} failed: unexpected response ({exc.reason})") from exc
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise GitHubError(f"{operation} failed: unexpected response") from exc


def _now() -> float:
    return time.monotonic()


class HttpCore:
    """One client, the bounds above, and the answers of the host as ``Answer``."""

    def __init__(
        self, token: str, *, timeout: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not token:
            raise ValueError("GitHub token is required")
        self._client = egress.client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            transport=transport,
        )

    def send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Answer:
        """One request, one answer. Every status of 400 or more, and every 3xx, is a
        ``GitHubError`` naming ``method path``; no status is an answer but one below 400.

        The message is built from the operation, the status and the host's own text, or from
        the exception's type alone. Nothing httpx wrote goes into it, so neither the URL with
        its query nor a header with the token can; the original stays chained as ``__cause__``.
        """
        operation = f"{method} {path}"
        started = _now()
        try:
            with self._client.stream(
                method,
                f"{GITHUB_API}{path}",
                json=json,
                params=params,
                headers={"Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                answer = self._read(operation, response, started)
        except httpx.InvalidURL as exc:
            raise GitHubError(f"{operation} failed: invalid URL") from exc
        except (httpx.HTTPError, httpx.StreamError) as exc:  # a stream a hook read or closed
            raise GitHubError(f"{operation} failed: {type(exc).__name__}") from exc
        if answer.status < 400:
            return answer
        raise GitHubError(
            f"{operation} -> {answer.status}{_host_text(answer)}{_rate_limit(answer)}"
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

    def _read(self, operation: str, response: httpx.Response, started: float) -> Answer:
        status = response.status_code
        if 300 <= status < 400:
            raise GitHubError(
                f"{operation} failed: the host answered a redirect ({status}); "
                "no redirect is followed"
            )
        encoding = response.headers.get("content-encoding")
        if encoding is not None and encoding.strip().lower() != "identity":
            raise GitHubError(f"{operation} failed: the body is encoded")
        self._within_deadline(operation, started)
        declared = response.headers.get("content-length", "")
        if _DIGITS.fullmatch(declared) and int(declared) > MAX_BODY:
            raise _over_the_cap(operation)
        chunks: list[bytes] = []
        size = 0
        # The raw bytes, as they arrive: iter_bytes() decodes, and a raw chunk that decodes to
        # no byte yields nothing, so it would count against neither the cap nor the deadline.
        for chunk in response.iter_raw():
            size += len(chunk)
            if size > MAX_BODY:
                raise _over_the_cap(operation)
            chunks.append(chunk)
            self._within_deadline(operation, started)
        return Answer(status, response.headers, b"".join(chunks))

    @staticmethod
    def _within_deadline(operation: str, started: float) -> None:
        if _now() - started > DEADLINE:
            raise GitHubError(f"{operation} failed: no complete answer within {DEADLINE:g} s")


def _over_the_cap(operation: str) -> GitHubError:
    return GitHubError(f"{operation} failed: the body is over the cap of {MAX_BODY} bytes")


def _safe(value: str, limit: int) -> bool:
    """Whether ``value`` may reach a terminal: printable ASCII, at most ``limit`` characters."""
    return len(value) <= limit and _PRINTABLE.fullmatch(value) is not None


def _host_text(answer: Answer) -> str:
    """The ``message`` of the host's JSON error body when it is safe to show, else nothing.

    Nothing else of the body is shown: not a field, not a byte, whatever its size.
    """
    try:
        data = answer.json()
    except UnexpectedError:  # not JSON: nothing of the body is shown
        return ""
    message = data.get("message") if isinstance(data, dict) else None
    if isinstance(message, str) and _safe(message, _MESSAGE_LIMIT):
        return f": {message}"
    return ""


def _rate_limit(answer: Answer) -> str:
    """For a 403 or a 429, the values of the two headers that say when to ask again."""
    if answer.status not in (403, 429):
        return ""
    shown = [
        f"{name}: {value}"
        for name in _RATE_LIMIT_HEADERS
        if (value := answer.headers.get(name)) is not None and _safe(value, _HEADER_LIMIT)
    ]
    return f" ({', '.join(shown)})" if shown else ""

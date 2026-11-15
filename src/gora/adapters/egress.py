"""The one place GORA builds an outbound HTTP client and sends a request through it (ADR-006,
ADR-015, ADR-017).

A token goes to the host its adapter names in code and to no other, and nothing outside the
code decides whom the connection trusts. Every client of the GitHub reader, the GitHub writer
and the Slack adapter is built here:

- ``trust_env`` is off, for the client and for its transport. No variable of the environment
  reaches it: not ``HTTPS_PROXY``, ``HTTP_PROXY``, ``ALL_PROXY`` or ``NO_PROXY``, which would
  send the request, and the token with it, to the proxy; not ``SSL_CERT_FILE`` or
  ``SSL_CERT_DIR``, which httpx lets replace the trust store. Settings come from ``GORA_*``
  variables only (ADR-017), and no setting names a proxy or a certificate.
- The transport is TLS over an explicit context, ``tls_context``: certificate and host name
  verified, TLS 1.2 at the least, the strict checks of X.509, and the CA certificates of
  ``certifi`` and no others. The machine's trust store is not read.
- No proxy, no retry, HTTP/1.1, and no redirect is followed (``BoundedRequest`` refuses a 3xx
  by name).

A caller passes no URL: each adapter holds its host as a constant. ``transport`` is a seam for
tests, which hand over ``httpx.MockTransport``; no production caller passes one.

Every request of the three adapters is sent by ``BoundedRequest``, written once, so no bound is
written twice and none is missing from one of them. What differs per host is a ``Host``: its
address, the error class it raises, the JSON field of its error text and the headers it names
for a rate limit. For every request:

- a path, the string appended to the host, of at most ``MAX_PATH`` characters: a longer one is a
  caller's ``ValueError`` before any request, whose message names the cap and never the path;
- httpx's timeout, 10 s unless given, for the connection and for each read, and a deadline for
  the whole request, body included: ``DEADLINE``. It is checked between HTTP framing events,
  when the headers arrive and as each chunk of the body does. Within one event (the headers,
  a chunk header, a trailer), which is at most 100 KiB (httpcore's limit on an incomplete
  event), each read is bounded by httpx's read timeout and no total deadline applies, so a
  host that sends such framing a byte at a time outlasts the deadline. The transport is TLS
  to the host an adapter names, verified against the CA certificates of ``certifi``, so only
  that host can send such framing;
- a cap on the body, ``MAX_BODY``, checked against the length the host declares and against
  what arrives, as each chunk does: a body over it is refused, never read short. The body is
  read as it arrives, never decoded: every request asks for ``Accept-Encoding: identity``, an
  answer whose ``Content-Encoding`` is present and not ``identity`` is refused, and the bytes
  counted are the bytes sent, so a stream that decodes to nothing cannot outlast the bounds;
- a 3xx is refused and no redirect is followed;
- a status of 400 or more is an error that names the status and shows the host's text only as
  the JSON field the host's ``Host`` names, when that is printable ASCII of at most
  ``MESSAGE_LIMIT`` characters, and nothing else of the body; for the statuses a ``Host`` names it
  also shows the values of the headers it names, when they are printable ASCII of at most
  ``HEADER_LIMIT`` characters, and no other header;
- no retry, for any status: a run stays deterministic and the caller sees the host's answer
  (ADR-006: retry semantics are defined per operation, and for these there are none).

The boundary is the adapters': whatever goes wrong in a request, a transport error, a URL httpx
refuses, a stream the transport handed over already read or closed, or a refused answer, leaves
as the error class of the host, whose message names the operation and holds no token, no query
string and nothing httpx wrote.
"""

import json as jsonlib
import re
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import certifi
import httpx

DEADLINE = 60.0  # seconds: 48x the slowest measured answer, 1.25 s for 8,424 refs (ADR-006)
MAX_BODY = 64 * 1024 * 1024  # bytes: 17x the largest measured, 3.86 MB for 8,424 refs
MAX_PATH = 2048  # characters: 5.6x the longest path an operation builds, 366 (ADR-006)

# The host's text, a header value or the text of an error body, is shown only when it is
# printable ASCII and short: an error message reaches a terminal, and the host's text is not
# trusted to be safe there.
_PRINTABLE = re.compile(r"[ -~]+")
HEADER_LIMIT = 64
MESSAGE_LIMIT = 200
_DIGITS = re.compile(r"[0-9]{1,18}")

NOT_JSON = "the body is not JSON"


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


@dataclass(frozen=True)
class Host:
    """What differs per host: where it is, how its failures are raised and what it says.

    ``api`` is the address every path is appended to, a constant of the adapter's code.
    ``error`` makes the exception a failed request raises from its message. ``text_field`` is
    the field of the host's JSON error body that holds its text. ``rate_limits`` names, for
    each status that is a rate limit, the headers that say when to ask again, in lower case.
    It is copied into a read-only mapping when the Host is made.
    """

    api: str
    error: Callable[[str], Exception]
    text_field: str
    rate_limits: Mapping[int, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate_limits", MappingProxyType(dict(self.rate_limits)))


def tls_context() -> ssl.SSLContext:
    """The TLS context of every outbound connection: verified, strict, GORA's own CA list."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # CERT_REQUIRED and the host name check
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.load_verify_locations(cafile=certifi.where())
    return context


def client(
    *,
    timeout: float,
    headers: Mapping[str, str],
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """A client with ``headers`` and ``timeout``, built as the module's docstring says."""
    if transport is None:
        transport = httpx.HTTPTransport(
            verify=tls_context(), trust_env=False, retries=0, http1=True, http2=False
        )
    return httpx.Client(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=timeout,
        headers=headers,
    )


def is_safe(value: str, limit: int) -> bool:
    """Whether ``value`` may reach a terminal: printable ASCII, at most ``limit`` characters."""
    return len(value) <= limit and _PRINTABLE.fullmatch(value) is not None


def _now() -> float:
    return time.monotonic()


class BoundedRequest:
    """One client, the bounds above, and the answers of the host as ``Answer``."""

    def __init__(
        self,
        host: Host,
        *,
        headers: Mapping[str, str],
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._host = host
        self._client = client(timeout=timeout, headers=headers, transport=transport)

    def send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Answer:
        """One request, one answer. Every status of 400 or more, and every 3xx, is the host's
        error naming ``method path``; no status is an answer but one below 400.

        A ``path`` over ``MAX_PATH`` is a ``ValueError`` before any request. The message of an
        error is built from the operation, the status and the host's own text, or from the
        exception's type alone. Nothing httpx wrote goes into it, so neither the URL with its
        query nor a header with the token can; the original stays chained as ``__cause__``.
        """
        if len(path) > MAX_PATH:
            raise ValueError(f"a request path is over the cap of {MAX_PATH} characters")
        operation = f"{method} {path}"
        started = _now()
        try:
            with self._client.stream(
                method,
                f"{self._host.api}{path}",
                json=json,
                params=params,
                headers={"Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                answer = self._read(operation, response, started)
        except httpx.InvalidURL as exc:
            raise self._host.error(f"{operation} failed: invalid URL") from exc
        except (httpx.HTTPError, httpx.StreamError) as exc:  # a stream a hook read or closed
            raise self._host.error(f"{operation} failed: {type(exc).__name__}") from exc
        if answer.status < 400:
            return answer
        raise self._host.error(
            f"{operation} -> {answer.status}{self.host_text(answer)}{self._rate_limit(answer)}"
        )

    def host_text(self, answer: Answer) -> str:
        """``: <text>``, the text field of the host's JSON body when it is safe to show, else
        nothing.

        Nothing else of the body is shown: not a field, not a byte, whatever its size.
        """
        try:
            data = answer.json()
        except UnexpectedError:  # not JSON: nothing of the body is shown
            return ""
        text = data.get(self._host.text_field) if isinstance(data, dict) else None
        if isinstance(text, str) and is_safe(text, MESSAGE_LIMIT):
            return f": {text}"
        return ""

    def _rate_limit(self, answer: Answer) -> str:
        """For a status the host calls a rate limit, the values of the headers that say when to
        ask again."""
        shown = [
            f"{name}: {value}"
            for name in self._host.rate_limits.get(answer.status, ())
            if (value := answer.headers.get(name)) is not None and is_safe(value, HEADER_LIMIT)
        ]
        return f" ({', '.join(shown)})" if shown else ""

    def _read(self, operation: str, response: httpx.Response, started: float) -> Answer:
        status = response.status_code
        if 300 <= status < 400:
            raise self._host.error(
                f"{operation} failed: the host answered a redirect ({status}); "
                "no redirect is followed"
            )
        encoding = response.headers.get("content-encoding")
        if encoding is not None and encoding.strip().lower() != "identity":
            raise self._host.error(f"{operation} failed: the body is encoded")
        self._within_deadline(operation, started)
        declared = response.headers.get("content-length", "")
        if _DIGITS.fullmatch(declared) and int(declared) > MAX_BODY:
            raise self._over_the_cap(operation)
        chunks: list[bytes] = []
        size = 0
        # The raw bytes, as they arrive: iter_bytes() decodes, and a raw chunk that decodes to
        # no byte yields nothing, so it would count against neither the cap nor the deadline.
        for chunk in response.iter_raw():
            size += len(chunk)
            if size > MAX_BODY:
                raise self._over_the_cap(operation)
            chunks.append(chunk)
            self._within_deadline(operation, started)
        return Answer(status, response.headers, b"".join(chunks))

    def _within_deadline(self, operation: str, started: float) -> None:
        if _now() - started > DEADLINE:
            raise self._host.error(f"{operation} failed: no complete answer within {DEADLINE:g} s")

    def _over_the_cap(self, operation: str) -> Exception:
        return self._host.error(f"{operation} failed: the body is over the cap of {MAX_BODY} bytes")

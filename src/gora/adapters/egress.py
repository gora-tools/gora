"""The one place GORA builds an outbound HTTP client and sends a request through it (ADR-006,
ADR-015, ADR-017).

A token goes to the host its adapter names in code and to no other, and nothing outside the
code decides whom the connection trusts. Every client of the GitHub reader, the GitHub writer,
the Slack adapter and the registry adapter is built here:

- ``trust_env`` is off, for the client and for its transport. No variable of the environment
  reaches it: not ``HTTPS_PROXY``, ``HTTP_PROXY``, ``ALL_PROXY`` or ``NO_PROXY``, which would
  send the request, and the token with it, to the proxy; not ``SSL_CERT_FILE`` or
  ``SSL_CERT_DIR``, which httpx2 lets replace the trust store. Settings come from ``GORA_*``
  variables only (ADR-017), and no setting names a proxy or a certificate.
- The transport is TLS over an explicit context, ``tls_context``: certificate and host name
  verified, TLS 1.2 at the least, the strict checks of X.509, and the CA certificates of
  ``certifi`` and no others. The machine's trust store is not read.
- No proxy, no retry, HTTP/1.1, and no redirect is followed (``BoundedRequest`` refuses a 3xx
  by name), with one exception, below.

A caller passes no URL: each adapter holds its host as a constant. ``transport`` is a seam for
tests, which hand over ``httpx2.MockTransport``; no production caller passes one.

The one exception is a blob of a registry (ADR-006, the registry adapter): a request that is
given ``redirect``, a ``Host``, follows one 3xx, and only to that host. Not httpx2's own
``follow_redirects``, which follows any host and any number of hops, forwards headers by its
own rules and is outside the shared deadline. The ``Location`` must be an absolute ``https``
URL whose host is exactly the ``Host``'s, with no port, no user information and no fragment,
and what it appends to the host is within ``MAX_PATH``. It is followed once, through a client
built here for that host over the same transport, with no ``Authorization`` and none of the
first request's headers, within the deadline of the first request (the same start), with every
other bound below, and raising the first host's error class. A 3xx from it is refused. The
``Location`` is a signed link that expires: it is never shown. An error of the followed request
names ``<method> <path> via <host>`` and the status or the rule of the failure, a refused
``Location`` is named by the rule it broke, and neither the ``Location``, its path nor its
query is in a message, or chained as a cause.

Every request of the four adapters is sent by ``BoundedRequest``, written once, so no bound is
written twice and none is missing from one of them. What differs per host is a ``Host``: its
address, the error class it raises, the JSON field of its error text, the headers it names for a
rate limit and the shapes of its tokens. For every request:

- a path, the string appended to the host, of at most ``MAX_PATH`` characters: a longer one is a
  caller's ``ValueError`` before any request, whose message names the cap and never the path;
- httpx2's timeout, 10 s unless given, for the connection and for each read, and a deadline for
  the whole request, body included: ``DEADLINE``. It is checked between HTTP framing events,
  when the headers arrive and as each chunk of the body does. Within one event (the headers,
  a chunk header, a trailer), which is at most 100 KiB (httpcore2's limit on an incomplete
  event), each read is bounded by httpx2's read timeout and no total deadline applies, so a
  host that sends such framing a byte at a time outlasts the deadline. The transport is TLS
  to the host an adapter names, verified against the CA certificates of ``certifi``, so only
  that host can send such framing;
- a cap on the body, ``MAX_BODY``, checked against the length the host declares and against
  what arrives, as each chunk does: a body over it is refused, never read short. The body is
  read as it arrives, never decoded: every request asks for ``Accept-Encoding: identity``, an
  answer whose ``Content-Encoding`` is present and not ``identity`` is refused, and the bytes
  counted are the bytes sent, so a stream that decodes to nothing cannot outlast the bounds;
- a 3xx is refused and no redirect is followed, but for the one exception above;
- a status of 400 or more is an error that names the status and shows the host's text only as
  the JSON field the host's ``Host`` names, when that is printable ASCII of at most
  ``MESSAGE_LIMIT`` characters, and nothing else of the body; for the statuses a ``Host`` names it
  also shows the values of the headers it names, when they are printable ASCII of at most
  ``HEADER_LIMIT`` characters, and no other header. Each of those values, the text included, is
  shown only when it holds neither the credential of the ``Authorization`` header the request
  sent nor a string in the shape of the host's tokens (``Host.token_shapes``): a host that
  reflects a token in its text would otherwise put it on the terminal. What is left out is the
  value alone, and the error still names the operation and the status. A fragment of a token that
  has no prefix and is shorter than the whole is not detected: the host is the one the adapter
  names in code, over verified TLS;
- no retry, for any status: a run stays deterministic and the caller sees the host's answer
  (ADR-006: retry semantics are defined per operation, and for these there are none).

The boundary is the adapters': whatever goes wrong in a request, a transport error, a URL httpx2
refuses, a stream the transport handed over already read or closed, or a refused answer, leaves
as the error class of the host, whose message names the operation and holds no token, no query
string and nothing httpx2 wrote, and of the host's own text, only what is shown as above.
"""

import json as jsonlib
import re
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import certifi
import httpx2

DEADLINE = 60.0  # seconds: 48x the slowest measured answer, 1.25 s for 8,424 refs (ADR-006)
MAX_BODY = 64 * 1024 * 1024  # bytes: 17x the largest measured, 3.86 MB for 8,424 refs
MAX_PATH = 2048  # characters: 5.6x the longest path an operation builds, 366 (ADR-006)

# The host's text, a header value or the text of an error body, is shown only when it is
# printable ASCII and short, and holds no token (the credential sent, or a host's token shape):
# an error message reaches a terminal, and the host's text is not trusted to be safe there.
_PRINTABLE = re.compile(r"[ -~]+")
HEADER_LIMIT = 64
MESSAGE_LIMIT = 200
_DIGITS = re.compile(r"[0-9]{1,18}")
_PRINTABLE_NO_SPACE = re.compile(r"[!-~]+")

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
    headers: httpx2.Headers
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
    It is copied into a read-only mapping when the Host is made. ``token_shapes`` are the shapes
    of this host's tokens, regular expressions each host states for itself (there is no default,
    and this module names none): a text or a header value that holds a match is not shown. They
    are compiled once, when the Host is made, and matched without regard to case, since a false
    match only hides the host's text.
    """

    api: str
    error: Callable[[str], Exception]
    text_field: str
    rate_limits: Mapping[int, tuple[str, ...]]
    token_shapes: Sequence[str]
    _shapes: re.Pattern[str] | None = field(init=False, repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate_limits", MappingProxyType(dict(self.rate_limits)))
        object.__setattr__(self, "token_shapes", tuple(self.token_shapes))
        shapes = "|".join(f"(?:{shape})" for shape in self.token_shapes)
        object.__setattr__(self, "_shapes", re.compile(shapes, re.IGNORECASE) if shapes else None)

    def has_token_shape(self, value: str) -> bool:
        """Whether ``value`` holds a string in the shape of one of this host's tokens."""
        return self._shapes is not None and self._shapes.search(value) is not None


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
    transport: httpx2.BaseTransport | None = None,
) -> httpx2.Client:
    """A client with ``headers`` and ``timeout``, built as the module's docstring says."""
    if transport is None:
        transport = httpx2.HTTPTransport(
            verify=tls_context(), trust_env=False, retries=0, http1=True, http2=False
        )
    return httpx2.Client(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=timeout,
        headers=headers,
    )


def is_safe(value: str, limit: int) -> bool:
    """Whether ``value`` may reach a terminal: printable ASCII, at most ``limit`` characters."""
    return len(value) <= limit and _PRINTABLE.fullmatch(value) is not None


def _credential(headers: Mapping[str, str]) -> str | None:
    """The credential of the ``Authorization`` header, the part after the scheme
    (``Bearer <credential>``), and nothing else of the headers. A request with no such header, or
    with an empty credential, has none."""
    for name, value in headers.items():
        if name.lower() == "authorization":
            scheme, separator, credential = value.partition(" ")
            return (credential if separator else scheme) or None
    return None


def _authority(host: Host) -> str:
    """The name of the host a redirect may go to: its address without ``https://``."""
    authority = host.api.removeprefix("https://")
    if authority == host.api or not authority or any(c in authority for c in "/?#:@ "):
        raise ValueError("a redirect host is an https address with no port and no path")
    return authority


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
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self._host = host
        self._credential = _credential(headers)  # private, and no repr of the object shows it
        self._timeout = timeout
        self._transport = transport
        self._client = client(timeout=timeout, headers=headers, transport=transport)

    def send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        redirect: Host | None = None,
    ) -> Answer:
        """One request, one answer. Every status of 400 or more, and every 3xx, is the host's
        error naming ``method path``; no status is an answer but one below 400.

        ``redirect`` is the one exception, and only for a request that is given it: a 3xx is
        then followed once, to the ``Location`` the module's docstring allows, on that host.

        A ``path`` over ``MAX_PATH`` is a ``ValueError`` before any request. The message of an
        error is built from the operation, the status and the host's own text, or from the
        exception's type alone. Nothing httpx2 wrote goes into it, so neither the URL with its
        query nor a header with the token can; the original stays chained as ``__cause__``
        (for the followed request, not even that: it could hold the ``Location``).
        """
        if len(path) > MAX_PATH:
            raise ValueError(f"a request path is over the cap of {MAX_PATH} characters")
        authority = None if redirect is None else _authority(redirect)
        operation = f"{method} {path}"
        started = _now()
        answer: Answer | None = None
        location = ""
        try:
            with self._client.stream(
                method,
                f"{self._host.api}{path}",
                json=json,
                params=params,
                headers={"Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                if authority is not None and 300 <= response.status_code < 400:
                    location = self._location(operation, response, authority)
                else:
                    answer = self._read(operation, response, started)
        except httpx2.InvalidURL as exc:
            raise self._host.error(f"{operation} failed: invalid URL") from exc
        except (httpx2.HTTPError, httpx2.StreamError) as exc:  # a stream a hook read or closed
            raise self._host.error(f"{operation} failed: {type(exc).__name__}") from exc
        if answer is None:
            followed = f"{operation} via {authority}"
            answer = self._follow(method, followed, location, started)
            if answer.status < 400:
                return answer
            raise self._host.error(f"{followed} -> {answer.status}")  # the status alone
        if answer.status < 400:
            return answer
        raise self._host.error(
            f"{operation} -> {answer.status}{self.host_text(answer)}{self._rate_limit(answer)}"
        )

    def _location(self, operation: str, response: httpx2.Response, authority: str) -> str:
        """The ``Location`` of a 3xx when it is the one the redirect allows; else the host's
        error, which names the rule it broke and never the value."""
        status = response.status_code
        location = response.headers.get("location")

        def refused(rule: str) -> Exception:
            return self._host.error(
                f"{operation} failed: the host answered a redirect ({status}) whose Location "
                f"{rule}; only one to the host {authority} is followed"
            )

        if location is None:
            raise refused("is missing")
        if _PRINTABLE_NO_SPACE.fullmatch(location) is None or not location.startswith("https://"):
            raise refused("is not an absolute https URL")
        ends = (i for i in (location.find(c, len("https://")) for c in "/?#") if i >= 0)
        if location[len("https://") : min(ends, default=len(location))] != authority:
            raise refused("names another host, or adds a port or user information")
        if "#" in location:
            raise refused("has a fragment")
        if len(location) - len("https://") - len(authority) > MAX_PATH:
            raise refused(f"appends more than {MAX_PATH} characters to the host")
        return location

    def _follow(self, method: str, operation: str, location: str, started: float) -> Answer:
        """The one followed request: a client of its own over the same transport, no header of
        the first request, the first request's deadline, and no redirect after it."""
        try:
            with (
                client(timeout=self._timeout, headers={}, transport=self._transport) as http,
                http.stream(
                    method,
                    location,
                    headers={"Accept-Encoding": "identity"},
                    follow_redirects=False,
                ) as response,
            ):
                return self._read(operation, response, started)
        except httpx2.InvalidURL:
            raise self._host.error(f"{operation} failed: invalid URL") from None
        except (httpx2.HTTPError, httpx2.StreamError) as exc:
            raise self._host.error(f"{operation} failed: {type(exc).__name__}") from None

    def host_text(self, answer: Answer) -> str:
        """``: <text>``, the text field of the host's JSON body when it is safe to show, else
        nothing.

        Safe is printable ASCII of at most ``MESSAGE_LIMIT`` characters that holds neither the
        credential this request sent nor a string in the shape of the host's tokens: a host that
        reflects a token in its text shows no text. Nothing else of the body is shown: not a
        field, not a byte, whatever its size.
        """
        try:
            data = answer.json()
        except UnexpectedError:  # not JSON: nothing of the body is shown
            return ""
        text = data.get(self._host.text_field) if isinstance(data, dict) else None
        if isinstance(text, str) and is_safe(text, MESSAGE_LIMIT) and self._holds_no_token(text):
            return f": {text}"
        return ""

    def _holds_no_token(self, value: str) -> bool:
        """Whether ``value`` holds neither the credential sent nor a token's shape."""
        if self._credential is not None and self._credential in value:
            return False
        return not self._host.has_token_shape(value)

    def _rate_limit(self, answer: Answer) -> str:
        """For a status the host calls a rate limit, the values of the headers that say when to
        ask again, each shown on the terms of ``host_text``: a value that reflects a token is
        left out."""
        shown = [
            f"{name}: {value}"
            for name in self._host.rate_limits.get(answer.status, ())
            if (value := answer.headers.get(name)) is not None
            and is_safe(value, HEADER_LIMIT)
            and self._holds_no_token(value)
        ]
        return f" ({', '.join(shown)})" if shown else ""

    def _read(self, operation: str, response: httpx2.Response, started: float) -> Answer:
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

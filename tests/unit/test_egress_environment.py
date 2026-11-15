"""No variable of the environment decides where a token goes or whom TLS trusts (ADR-006,
ADR-017), and no URL and no client can be given to an adapter.

Each test here is red on the code before the change, through APIs that exist there, so
``make red-first`` proves them (ADR-016 rule 1). None imports ``gora.adapters.egress``, which
that code lacks; the tests of the module itself are ``test_egress.py``.

A host is impersonated inside the hermetic guard (TESTING rule 6), without a listener: the
suite allows ``AF_UNIX`` sockets only, no TCP and no name lookup. ``socket.create_connection``,
the function httpcore2 connects with, is replaced by one that records the address it was asked
for and hands back one end of ``socket.socketpair()``; a thread on the other end serves TLS
with a certificate for the host's name, issued by a CA of the test's own (``trustme``), and
records the ``Authorization`` of the request it receives. No real connection is made. The
guard replaces ``connect`` and the resolvers, not this function.
"""

import inspect
import json
import socket
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx2
import pytest
import trustme

from gora.adapters.github import GitHubAdapter, GitHubError, GitHubReader
from gora.adapters.github._http import HttpCore
from gora.adapters.slack import SlackAdapter, SlackError
from gora.config.settings import Settings
from gora.domain.notification import Notification, Span

REPO = "gora-tools/gora"
SENTINEL = "fake-sentinel-egress"
PROXY = "http://proxy.invalid:3128"
SERVE_SECONDS = 10.0  # a served connection that goes quiet ends, so no thread outlives its test

# The variables that name a proxy or a trust store. A test sets the one it is about; a
# developer's own are removed, so no verdict depends on the machine.
ENVIRONMENT = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)


@pytest.fixture(autouse=True)
def _no_proxy_and_no_trust_store_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


class _Socket(socket.socket):
    """One end of a socket pair that takes a TCP option as a TCP socket would: httpcore2 sets
    ``TCP_NODELAY`` after it connects, and an ``AF_UNIX`` socket answers ``EOPNOTSUPP``."""

    def setsockopt(self, *args: Any) -> None:
        if args[0] != socket.IPPROTO_TCP:
            super().setsockopt(*args)


class Impostor:
    """A host that is not the one asked for, and holds a certificate for its name.

    ``asked`` is each address a connection was asked for; ``authorization`` the value of the
    ``Authorization`` header of each request the impostor received. Leaving the ``with`` waits
    for every connection it served to end.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, host: str, answer: dict[str, Any]) -> None:
        self.asked: list[object] = []
        self.authorization: list[str] = []
        self._ca = trustme.CA()
        self._certificate = self._ca.issue_cert(host)
        self._answer = answer
        self._threads: list[threading.Thread] = []
        monkeypatch.setattr(socket, "create_connection", self._connect)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        for thread in self._threads:
            thread.join(SERVE_SECONDS + 5)
            assert not thread.is_alive(), "the impostor's thread did not end"

    def ca_file(self, directory: Path) -> str:
        """The CA that signed the impostor's certificate, as a file in ``directory``."""
        path = directory / "impostor-ca.pem"
        self._ca.cert_pem.write_to_path(str(path))
        return str(path)

    def trust(self, context: ssl.SSLContext) -> None:
        """Add the impostor's CA to the CAs ``context`` trusts."""
        self._ca.configure_trust(context)

    def trusting_context(self) -> ssl.SSLContext:
        """A client context that trusts the impostor's CA and no other."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.trust(context)
        return context

    def _connect(
        self, address: Any, timeout: float | None = None, source_address: Any = None
    ) -> socket.socket:
        self.asked.append(address)
        ours, theirs = socket.socketpair()
        thread = threading.Thread(target=self._serve, args=(theirs,))
        thread.start()
        self._threads.append(thread)
        connection = _Socket(fileno=ours.detach())
        connection.settimeout(timeout)
        return connection

    def _serve(self, sock: socket.socket) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._certificate.configure_cert(context)
        sock.settimeout(SERVE_SECONDS)
        try:
            with context.wrap_socket(sock, server_side=True) as tls:
                received = b""
                while b"\r\n\r\n" not in received:
                    chunk = tls.recv(65536)
                    if not chunk:
                        return
                    received += chunk
                head, _, body = received.partition(b"\r\n\r\n")
                headers = {
                    name.strip().lower(): value.strip()
                    for name, _, value in (line.partition(b":") for line in head.split(b"\r\n")[1:])
                }
                if b"authorization" in headers:
                    self.authorization.append(headers[b"authorization"].decode("ascii"))
                # The whole request is read, so that closing the connection resets nothing.
                while len(body) < int(headers.get(b"content-length", b"0")):
                    chunk = tls.recv(65536)
                    if not chunk:
                        return
                    body += chunk
                answer = json.dumps(self._answer).encode()
                tls.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: " + str(len(answer)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n" + answer
                )
        except OSError:  # the client refused the certificate, or hung up: an SSLError is one
            return
        finally:
            sock.close()


def refusing_connections(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every address ``socket.create_connection`` is asked for, each one refused."""
    asked: list[object] = []

    def refuse(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        asked.append(address)
        raise ConnectionRefusedError(f"refused in the test: {address!r}")

    monkeypatch.setattr(socket, "create_connection", refuse)
    return asked


def attempt(call: Callable[[], object]) -> Exception | None:
    """The error of the adapters ``call`` raised, or None when it returned."""
    try:
        call()
    except (GitHubError, SlackError, httpx2.HTTPError) as error:
        return error
    return None


_NOTE = Notification(summary=(Span("Promoted ", "word"),), detail=(Span("Digest: ", "word"),))


@dataclass(frozen=True)
class Subject:
    """One adapter operation, the host it must reach, and the error its refusal is."""

    host: str
    answer: dict[str, Any]
    call: Callable[[], object]
    refusal: type[Exception]


SUBJECTS = {
    "github-reader": Subject(
        "api.github.com",
        {"full_name": REPO, "id": 1457970812},
        lambda: GitHubReader(REPO, SENTINEL).repository_id(),
        GitHubError,
    ),
    "github-writer": Subject(
        "api.github.com",
        {"ref": "refs/heads/b"},
        lambda: GitHubAdapter(REPO, SENTINEL).create_branch("b", "abc"),
        GitHubError,
    ),
    "slack": Subject(
        "slack.com",
        {"ok": True, "channel": "C1", "ts": "1.1"},
        lambda: SlackAdapter(SENTINEL).post_message("C1", _NOTE),
        SlackError,
    ),
}
EVERY_SUBJECT = pytest.mark.parametrize("subject", SUBJECTS.values(), ids=list(SUBJECTS))


# --- the harness can fail both ways ----------------------------------------------------------


def test_the_impostor_serves_a_client_that_trusts_its_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calibration: a client given the impostor's CA is answered, by a thread behind a socket
    pair, over TLS, and the impostor records its token. Without this, "the impostor received
    nothing" below could mean "the impostor does not work"."""
    with Impostor(monkeypatch, "api.github.com", {"id": 7}) as impostor:
        transport = httpx2.HTTPTransport(verify=impostor.trusting_context(), trust_env=False)
        with httpx2.Client(transport=transport, trust_env=False) as client:
            response = client.get("https://api.github.com/x", headers={"Authorization": SENTINEL})

    assert response.json() == {"id": 7}
    assert impostor.asked == [("api.github.com", 443)]
    assert impostor.authorization == [SENTINEL]


# --- a trust store from the environment ------------------------------------------------------


@EVERY_SUBJECT
def test_a_ca_the_environment_names_is_not_trusted_and_the_impostor_receives_no_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subject: Subject
) -> None:
    """``SSL_CERT_FILE`` replaced the trust store of the client the adapters built: a host with
    a certificate from that CA was served the request, and the token with it."""
    with Impostor(monkeypatch, subject.host, subject.answer) as impostor:
        monkeypatch.setenv("SSL_CERT_FILE", impostor.ca_file(tmp_path))
        error = attempt(subject.call)

    assert impostor.authorization == [], "the impostor received the token"
    assert isinstance(error, subject.refusal)
    assert impostor.asked == [(subject.host, 443)]


# --- a proxy from the environment ------------------------------------------------------------


@pytest.mark.parametrize("variable", ["HTTPS_PROXY", "ALL_PROXY"])
@EVERY_SUBJECT
def test_a_proxy_the_environment_names_is_not_connected_to(
    monkeypatch: pytest.MonkeyPatch, variable: str, subject: Subject
) -> None:
    """With the variable set, the connection went to the proxy, which was told the host and
    held the token: ``("proxy.invalid", 3128)`` was the address asked."""
    asked = refusing_connections(monkeypatch)
    monkeypatch.setenv(variable, PROXY)

    error = attempt(subject.call)

    assert asked == [(subject.host, 443)]
    assert isinstance(error, subject.refusal)


# --- no URL and no client can be given -------------------------------------------------------


@pytest.mark.parametrize("cls", [HttpCore, GitHubReader, GitHubAdapter, SlackAdapter])
def test_an_adapter_takes_no_url_and_no_client(cls: type[object]) -> None:
    """The host is a constant of the code and the client is built by GORA: a caller that could
    name either could send the token anywhere, with whatever trust it brought."""
    parameters = set(inspect.signature(cls).parameters)

    assert parameters & {"base_url", "client"} == set()


def test_the_http_core_has_no_answer_for_a_404() -> None:
    """A 404 is an answer GitHub also gives for a branch that is missing and for a repository
    the token cannot see, so no caller may take it for "there is no such file"."""
    assert "missing_ok" not in inspect.signature(HttpCore.send).parameters


# --- the secrets of the settings -------------------------------------------------------------


def test_the_slack_secrets_are_not_in_the_text_of_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "GORA_SLACK_BOT_TOKEN": "fake-sentinel-bot",
        "GORA_SLACK_APP_TOKEN": "fake-sentinel-app",
        "GORA_SLACK_SIGNING_SECRET": "fake-sentinel-signing",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings()

    texts = {
        "repr": repr(settings),
        "str": str(settings),
        "model_dump": str(settings.model_dump()),
        "model_dump_json": settings.model_dump_json(),
    }

    leaks = {
        way: [name for name, value in values.items() if value in text]
        for way, text in texts.items()
    }
    assert {way: names for way, names in leaks.items() if names} == {}

"""The module that builds every outbound client (ADR-006, ADR-017): its TLS context and its
client, and what each refuses.

The tests that need the module's absence to be red, the environment, the proxy, the host and
the signatures, are ``test_egress_environment.py``; the impostor they build is used here too.
"""

import inspect
import ssl
from pathlib import Path
from typing import Any

import certifi
import httpx
import pytest

from gora.adapters import egress
from gora.adapters.github import GitHubAdapter, GitHubReader
from gora.adapters.github._http import HttpCore
from gora.adapters.slack import SlackAdapter
from tests.unit.test_egress_environment import (
    EVERY_SUBJECT,
    SENTINEL,
    Impostor,
    Subject,
    attempt,
)

CERTIFICATE = "-----BEGIN CERTIFICATE-----"


def _certifi_certificates() -> int:
    return Path(certifi.where()).read_text(encoding="ascii").count(CERTIFICATE)


def _get(context: ssl.SSLContext) -> httpx.Response:
    """One request to ``api.github.com`` over a transport that verifies with ``context``."""
    transport = httpx.HTTPTransport(verify=context, trust_env=False)
    with httpx.Client(transport=transport, trust_env=False) as client:
        return client.get("https://api.github.com/x", headers={"Authorization": SENTINEL})


# --- the TLS context -------------------------------------------------------------------------


def test_the_context_verifies_certificate_and_host_name_with_tls_1_2_and_strict_x509() -> None:
    context = egress.tls_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2
    assert context.verify_flags & ssl.VERIFY_X509_STRICT


def test_the_context_trusts_the_certificates_of_certifi_and_no_others() -> None:
    context = egress.tls_context()

    count = _certifi_certificates()
    assert count > 0  # calibration: the bundle was read at all
    assert context.cert_store_stats()["x509"] == count
    assert len(context.get_ca_certs()) == count


def test_the_trust_store_the_environment_names_is_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``SSL_CERT_FILE`` and ``SSL_CERT_DIR`` are what OpenSSL, ``ssl`` and httpx read for a
    trust store. Both name a CA here, and the context holds the same certificates as before."""
    with Impostor(monkeypatch, "api.github.com", {}) as impostor:
        monkeypatch.setenv("SSL_CERT_FILE", impostor.ca_file(tmp_path))
        directory = tmp_path / "certs"
        directory.mkdir()
        impostor.ca_file(directory)
        monkeypatch.setenv("SSL_CERT_DIR", str(directory))

        context = egress.tls_context()

    assert context.cert_store_stats()["x509"] == _certifi_certificates()


def test_the_context_accepts_a_certificate_for_the_host_from_a_ca_it_trusts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calibration: the strict context refuses what is wrong with a certificate, not every
    certificate."""
    with Impostor(monkeypatch, "api.github.com", {"id": 7}) as impostor:
        context = egress.tls_context()
        impostor.trust(context)

        response = _get(context)

    assert response.json() == {"id": 7}


def test_the_context_refuses_a_certificate_for_another_host_from_a_ca_it_trusts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Impostor(monkeypatch, "elsewhere.example", {"id": 7}) as impostor:
        context = egress.tls_context()
        impostor.trust(context)

        with pytest.raises(httpx.ConnectError):
            _get(context)

    assert impostor.authorization == []


@EVERY_SUBJECT
def test_a_certificate_no_ca_the_adapters_trust_signed_is_refused_with_no_environment_set(
    monkeypatch: pytest.MonkeyPatch, subject: Subject
) -> None:
    """The impostor holds a certificate for the host's name and its CA is trusted by nothing
    here: the connection is asked for the host and port the adapter names, and the token goes
    no further than the handshake."""
    with Impostor(monkeypatch, subject.host, subject.answer) as impostor:
        error = attempt(subject.call)

    assert impostor.asked == [(subject.host, 443)]
    assert impostor.authorization == []
    assert isinstance(error, subject.refusal)


# --- the client ------------------------------------------------------------------------------


def test_the_client_reads_no_environment_follows_no_redirect_and_sends_what_it_is_given() -> None:
    client = egress.client(timeout=3.0, headers={"X-Test": "1"})

    assert client.trust_env is False
    assert client.follow_redirects is False
    assert client.timeout == httpx.Timeout(3.0)
    assert client.headers["x-test"] == "1"


def test_the_transport_is_built_on_the_context_with_no_proxy_no_retry_and_http_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The arguments are written out. One added, such as a proxy or a retry count, or one
    changed, is a red test and a reviewed edit of this literal."""
    built: list[dict[str, Any]] = []

    class Recording(httpx.HTTPTransport):
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "HTTPTransport", Recording)

    egress.client(timeout=3.0, headers={})

    (kwargs,) = built
    context = kwargs.pop("verify")
    assert isinstance(context, ssl.SSLContext)
    assert context.cert_store_stats()["x509"] == _certifi_certificates()
    assert kwargs == {"trust_env": False, "retries": 0, "http1": True, "http2": False}


def test_a_transport_the_caller_gives_is_the_one_used_and_no_context_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_context() -> ssl.SSLContext:
        raise AssertionError("a context was built for a client that has a transport")

    monkeypatch.setattr(egress, "tls_context", no_context)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"seen": True}))

    client = egress.client(timeout=3.0, headers={}, transport=transport)

    assert client.get("https://api.github.com/x").json() == {"seen": True}


@pytest.mark.parametrize("cls", [HttpCore, GitHubReader, GitHubAdapter, SlackAdapter])
def test_an_adapter_takes_a_transport_for_a_test_and_it_defaults_to_none(
    cls: type[object],
) -> None:
    parameter = inspect.signature(cls).parameters["transport"]

    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

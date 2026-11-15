"""The one place GORA builds an outbound HTTP client (ADR-006, ADR-017).

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
- No proxy, no retry, HTTP/1.1, and no redirect is followed (the HTTP core of the GitHub
  adapter refuses a 3xx by name).

A caller passes no URL: each adapter holds its host as a constant. ``transport`` is a seam for
tests, which hand over ``httpx.MockTransport``; no production caller passes one.
"""

import ssl
from collections.abc import Mapping

import certifi
import httpx


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

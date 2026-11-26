"""The module that builds every outbound client (ADR-006, ADR-017): its TLS context and its
client, and what each refuses.

The tests that need the module's absence to be red, the environment, the proxy, the host and
the signatures, are ``test_egress_environment.py``; the impostor they build is used here too.
"""

import contextlib
import inspect
import ssl
from collections.abc import Callable, Mapping, MutableMapping
from pathlib import Path
from typing import Any, cast

import certifi
import httpx2
import pytest

from gora.adapters import egress
from gora.adapters.github import GitHubAdapter, GitHubError, GitHubReader
from gora.adapters.github._http import _GITHUB, HttpCore
from gora.adapters.slack import SlackAdapter
from gora.adapters.slack.adapter import _SLACK
from gora.domain.release import identifiers
from gora.domain.release.identifiers import Grammar
from gora.domain.release.tags import MAX_TAG_NAME, is_version_tag
from gora.domain.release_repository import formats
from tests.audit_fakes import mock_transport
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


def _get(context: ssl.SSLContext) -> httpx2.Response:
    """One request to ``api.github.com`` over a transport that verifies with ``context``."""
    transport = httpx2.HTTPTransport(verify=context, trust_env=False)
    with httpx2.Client(transport=transport, trust_env=False) as client:
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
    """``SSL_CERT_FILE`` and ``SSL_CERT_DIR`` are what OpenSSL, ``ssl`` and httpx2 read for a
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

        with pytest.raises(httpx2.ConnectError):
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
    assert client.timeout == httpx2.Timeout(3.0)
    assert client.headers["x-test"] == "1"


def test_the_transport_is_built_on_the_context_with_no_proxy_no_retry_and_http_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The arguments are written out. One added, such as a proxy or a retry count, or one
    changed, is a red test and a reviewed edit of this literal."""
    built: list[dict[str, Any]] = []

    class Recording(httpx2.HTTPTransport):
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx2, "HTTPTransport", Recording)

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
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json={"seen": True}))

    client = egress.client(timeout=3.0, headers={}, transport=transport)

    assert client.get("https://api.github.com/x").json() == {"seen": True}


@pytest.mark.parametrize("cls", [HttpCore, GitHubReader, GitHubAdapter, SlackAdapter])
def test_an_adapter_takes_a_transport_for_a_test_and_it_defaults_to_none(
    cls: type[object],
) -> None:
    parameter = inspect.signature(cls).parameters["transport"]

    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


# --- a host's rate-limit headers --------------------------------------------------------------


def _writable(limits: Mapping[int, tuple[str, ...]]) -> MutableMapping[int, tuple[str, ...]]:
    """``limits`` as a writable mapping: the type says it is not, and the test asks whether it
    is."""
    return cast("MutableMapping[int, tuple[str, ...]]", limits)


def test_a_hosts_rate_limits_cannot_be_changed_after_the_host_is_made() -> None:
    """The host's rate-limit headers are what its error messages show: a ``Host`` copies them
    into a read-only mapping, so neither the dict it was made from nor the host's own mapping
    changes them. The two hosts GORA ships are held the same way."""
    limits: dict[int, tuple[str, ...]] = {429: ("retry-after",)}
    shapes = ["tk[a-z]-"]
    host = egress.Host("https://host.example", RuntimeError, "error", limits, shapes)

    limits[429] = ()
    limits[503] = ("retry-after",)
    shapes.clear()

    assert dict(host.rate_limits) == {429: ("retry-after",)}
    assert host.token_shapes == ("tk[a-z]-",)  # copied too, and matched without regard to case
    assert host.has_token_shape("see TKB-x") and not host.has_token_shape("see tk-x")
    for held in (host.rate_limits, _GITHUB.rate_limits, _SLACK.rate_limits):
        with pytest.raises(TypeError):
            _writable(held)[429] = ()


# --- the cap on a path -----------------------------------------------------------------------

REPO = "gora-tools/gora"


def _paths_sent(call: Callable[[GitHubReader], object]) -> list[str]:
    """The paths a reader's call sent, as the transport received them, query left out."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.raw_path.split(b"?")[0].decode())
        return httpx2.Response(200, json={})

    with contextlib.suppress(GitHubError):  # an empty answer is not what an operation reads
        call(GitHubReader(REPO, SENTINEL, transport=mock_transport(handler)))
    return seen


def test_a_path_of_exactly_the_cap_is_sent_and_one_character_more_is_refused() -> None:
    """Calibration: the cap refuses what is over it, and only that. The message names the cap
    and never the path."""
    head = f"/repos/{REPO}/git/ref/heads/"
    branch = "a" * (egress.MAX_PATH - len(head))

    (sent,) = _paths_sent(lambda reader: reader.branch_head(branch))
    assert len(sent) == egress.MAX_PATH

    with pytest.raises(ValueError) as info:
        _paths_sent(lambda reader: reader.branch_head(branch + "a"))
    assert str(info.value) == f"a request path is over the cap of {egress.MAX_PATH} characters"


def _at_the_maximum(grammar: Grammar, fill: str = "a") -> str:
    value = fill * grammar.max_length
    assert grammar.is_valid(value)
    return value


def test_the_cap_is_at_least_four_times_the_longest_path_an_operation_builds() -> None:
    """MAX_PATH is measured as DEADLINE and MAX_BODY were (ADR-006): every operation of the
    reader and the writer is called with its inputs at their grammars' maxima, and the paths
    the transport receives are the measure. The repository is ``REPO``'s longest; a stage and an
    artifact name are ``STAGE``'s and ``NAME``'s; a version is ``VERSION``'s and
    ``FINAL_VERSION``'s; the branches are the ones GORA names itself (ADR-023, section 6), since
    the reader checks no grammar of a branch; the content paths are the release files GORA's
    model names (ADR-026, section 1). None may be refused."""
    repo = "a" * 39 + "/" + "b" * 100
    assert identifiers.REPO.is_valid(repo)
    assert len(repo) == identifiers.REPO.max_length
    stage = _at_the_maximum(identifiers.STAGE)
    artifact = _at_the_maximum(identifiers.NAME)
    version = "1.0.0-" + "a" * (identifiers.VERSION.max_length - 6)
    assert identifiers.VERSION.is_valid(version)
    final = "1.1.1" + "1" * (identifiers.FINAL_VERSION.max_length - 5)
    assert identifiers.FINAL_VERSION.is_valid(final)
    prefix = artifact + "/"
    assert formats.TAG_PREFIX.is_valid(prefix)
    # The longest version tag: a tag prefix of 64 characters, v, and a version of 128. Its
    # reader's operation asks twice, for the ref and for the tag object the ref names.
    tag = f"{prefix}v{version}"
    assert is_version_tag(tag)
    assert len(tag) == MAX_TAG_NAME == 193
    sha = _at_the_maximum(identifiers.SOURCE_COMMIT, "b")
    branches = [
        f"gora/reject/{artifact}/{version}",
        f"gora/promote/{stage}/{artifact}",
        f"gora/rollback/{stage}",
        f"gora/retire/{artifact}",
        "gora/migrate",
    ]
    files = [
        f"state/{stage}/{artifact}.yaml",
        f"artifacts/{artifact}/artifact.yaml",
        f"rejections/{artifact}/{final}.yaml",
        "release-policy.yaml",
        ".github/CODEOWNERS",
    ]
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.raw_path.split(b"?")[0].decode()
        seen.append(path)
        if path.endswith(f"/git/ref/tags/{tag}"):  # the ref of an annotated tag: a second ask
            ref = {"ref": f"refs/tags/{tag}", "object": {"type": "tag", "sha": sha}}
            return httpx2.Response(200, json=ref)
        return httpx2.Response(200, json={})

    transport = mock_transport(handler)
    reader = GitHubReader(repo, SENTINEL, transport=transport)
    writer = GitHubAdapter(repo, SENTINEL, transport=transport)
    calls: list[Callable[[], object]] = [
        reader.repository_id,
        lambda: reader.tag_names(prefix),
        lambda: reader.tag(tag),
        lambda: writer.get_pull_request_status(2**63 - 1),
        lambda: writer.find_open_pull_request("main"),
        lambda: writer.create_branch(branches[0], sha),
        lambda: writer.create_pull_request("h", "main", "t", "b"),
        *[lambda branch=branch: reader.branch_head(branch) for branch in branches],
        *[lambda path=path: writer.commit_file(path, "x", "b", "m") for path in files],
    ]
    for call in calls:
        with contextlib.suppress(GitHubError):  # an empty answer is not what an operation reads
            call()

    # None was refused (a ValueError would have left the call), and ``tag`` sent both its paths.
    assert len(seen) == len(calls) + 1
    assert f"/repos/{repo}/git/ref/tags/{tag}" in seen
    assert f"/repos/{repo}/git/tags/{sha}" in seen
    longest = max(len(path) for path in seen)
    longest_tag = max(len(path) for path in seen if "/git/ref/tags/" in path)
    assert longest > len(repo) + len(branches[0])  # calibration: the inputs are at their maxima
    assert longest_tag > len(repo) + len(tag)
    assert 4 * longest <= egress.MAX_PATH

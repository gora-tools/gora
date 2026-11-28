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
from gora.adapters.ghcr import GhcrRegistry
from gora.adapters.ghcr.adapter import _GHCR, _GHCR_TOKEN
from gora.adapters.github import GitHubAdapter, GitHubError, GitHubReader
from gora.adapters.github._http import _GITHUB, HttpCore
from gora.adapters.slack import SlackAdapter
from gora.adapters.slack.adapter import _SLACK
from gora.domain.release import identifiers
from gora.domain.release.identifiers import Grammar
from gora.domain.release.tags import MAX_TAG_NAME, is_version_tag
from gora.domain.release_repository import formats
from tests.audit_fakes import Chunks, mock_transport
from tests.unit.test_egress_environment import (
    EVERY_SUBJECT,
    SENTINEL,
    Impostor,
    Subject,
    attempt,
)
from tests.unit.test_github_reader import Clock

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


@pytest.mark.parametrize("cls", [HttpCore, GitHubReader, GitHubAdapter, SlackAdapter, GhcrRegistry])
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


# --- a host's refusals ------------------------------------------------------------------------


REFUSING = egress.Host(
    "https://host.example", RuntimeError, "message", {}, (), refusals={401: "the words of 401"}
)


def _refused(status: int, host: egress.Host = REFUSING) -> str:
    """The message of the error a request to ``host`` raises when it answers ``status``."""
    answer = mock_transport(lambda request: httpx2.Response(status, json={"message": "nope"}))
    request = egress.BoundedRequest(host, headers={}, timeout=5.0, transport=answer)
    with pytest.raises(RuntimeError) as info:
        request.send("GET", "/x")
    return str(info.value)


def test_a_status_a_host_names_in_its_refusals_ends_the_error_with_its_words() -> None:
    assert _refused(401) == "GET /x -> 401: nope (the words of 401)"


def test_a_status_a_host_does_not_name_in_its_refusals_adds_nothing() -> None:
    assert _refused(403) == "GET /x -> 403: nope"
    assert _refused(500) == "GET /x -> 500: nope"
    plain = egress.Host("https://host.example", RuntimeError, "message", {}, ())
    assert dict(plain.refusals) == {}  # empty unless given
    assert _refused(401, plain) == "GET /x -> 401: nope"


def _writable_words(refusals: Mapping[int, str]) -> MutableMapping[int, str]:
    return cast("MutableMapping[int, str]", refusals)


def test_a_hosts_refusals_cannot_be_changed_after_the_host_is_made() -> None:
    """The words are what an error says a status means: a ``Host`` copies them into a read-only
    mapping, as it does its rate limits. The token host of the registry adapter is held the same
    way."""
    words = {401: "the words of 401"}
    host = egress.Host("https://host.example", RuntimeError, "message", {}, (), refusals=words)

    words[401] = "other words"
    words[403] = "more words"

    assert dict(host.refusals) == {401: "the words of 401"}
    for held in (host.refusals, _GHCR_TOKEN.refusals, _GHCR.refusals):
        with pytest.raises(TypeError):
            _writable_words(held)[401] = "changed"


# --- the cap on a path -----------------------------------------------------------------------

REPO = "gora-tools/gora"
FIRST_HOST = egress.Host("https://api.github.com", RuntimeError, "message", {}, ())


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


NOT_A_PATH = {
    "user information": "@evil.example/x",
    "a longer host name": ".evil.example/x",
    "a port": ":8443/x",
    "no slash": "x/y",
    "a query": "?q=1",
    "a fragment": "#f",
    "the empty path": "",
}


@pytest.mark.parametrize("path", NOT_A_PATH.values(), ids=list(NOT_A_PATH))
def test_a_path_that_does_not_begin_with_a_slash_is_refused_before_any_request(path: str) -> None:
    """A URL parser reads the host up to the first ``/``, ``?`` or ``#``, so such a path could
    name another host, and the request would go there with the ``Authorization`` header. The
    transport answers ``200`` to any host and records what reaches it: nothing may."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    request = egress.BoundedRequest(
        FIRST_HOST,
        headers={"Authorization": f"Bearer {SENTINEL}"},
        timeout=5.0,
        transport=mock_transport(handler),
    )

    with pytest.raises(ValueError) as info:
        request.send("GET", path)

    assert str(info.value) == 'a request path must begin with "/"'
    if path:  # the empty path is in every string
        assert path not in str(info.value)
        assert path not in repr(info.value)
    assert seen == []


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


# --- the one redirect ------------------------------------------------------------------------
#
# A request that is given ``redirect`` follows one 3xx, to one host (the module's docstring). The
# hosts here are made up and the ``Location`` is synthetic; its path and its query carry the word
# ``SIGNED``, which no message may hold.


class RefusedError(Exception):
    """The error class of the first host: every refusal here is raised as it."""


FIRST = egress.Host("https://first.example", RefusedError, "message", {}, ())
STORAGE = "https://storage.example"
SENT = "cred-fake-egress-1"
SIGNED_PATH = "/blobs/SIGNED-PATH-1"
SIGNED_QUERY = "sig=SIGNED-QUERY-1&exp=1"
LOCATION = f"https://storage.example{SIGNED_PATH}?{SIGNED_QUERY}"
OPERATION = "GET /blobs/x"
FOLLOWED = f"{OPERATION} via storage.example"

Handler = Callable[[httpx2.Request], httpx2.Response]


def _redirecting(location: str | None = LOCATION, status: int = 307) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        # As bytes on the wire: a Location may hold what is not ASCII, as a host may send it.
        headers = [] if location is None else [(b"Location", location.encode("utf-8"))]
        return httpx2.Response(status, headers=headers)

    return handler


def _served(body: bytes = b"blob") -> Handler:
    return lambda request: httpx2.Response(200, content=body)


class Wire:
    """The first host and the storage host over one transport, and every request they saw."""

    def __init__(self, first: Handler, second: Handler | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self._first, self._second = first, second

        def handler(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            if request.url.host == "first.example":
                return self._first(request)
            assert self._second is not None, "the storage host was asked"
            return self._second(request)

        self.request = egress.BoundedRequest(
            FIRST,
            headers={"Authorization": f"Bearer {SENT}", "X-First": "1"},
            timeout=5.0,
            transport=mock_transport(handler),
        )

    def send(self, *, redirect: str | None = STORAGE) -> egress.Answer:
        return self.request.send("GET", "/blobs/x", redirect=redirect)


def _hides_the_location(error: BaseException) -> None:
    for text in (str(error), repr(error)):
        assert "SIGNED" not in text
        assert "sig=" not in text
    assert error.__cause__ is None


def test_without_redirect_a_3xx_is_still_refused_and_not_followed() -> None:
    wire = Wire(_redirecting())

    with pytest.raises(RefusedError) as info:
        wire.send(redirect=None)

    assert (
        str(info.value)
        == f"{OPERATION} failed: the host answered a redirect (307); no redirect is followed"
    )
    assert len(wire.requests) == 1


def test_a_redirect_is_followed_once_to_the_host_with_no_header_of_the_first_request() -> None:
    wire = Wire(_redirecting(), _served(b"the blob"))

    answer = wire.send()

    first, second = wire.requests
    assert first.headers["authorization"] == f"Bearer {SENT}"
    assert first.headers["x-first"] == "1"
    assert (second.method, str(second.url)) == ("GET", LOCATION)  # the Location, byte for byte
    assert "authorization" not in second.headers
    assert "x-first" not in second.headers
    assert second.headers["accept-encoding"] == "identity"
    assert (answer.status, answer.body) == (200, b"the blob")


def test_an_answer_that_is_not_a_redirect_is_returned_when_redirect_is_given() -> None:
    wire = Wire(_served(b"direct"))

    assert wire.send().body == b"direct"
    assert len(wire.requests) == 1


def test_a_status_of_400_or_more_is_an_error_when_redirect_is_given() -> None:
    wire = Wire(lambda request: httpx2.Response(404))

    with pytest.raises(RefusedError, match=f"{OPERATION} -> 404"):
        wire.send()


@pytest.mark.parametrize(
    "address",
    [
        "http://storage.example",
        "https://storage.example:8443",
        "https://storage.example:443",
        "https://storage.example/",
        "https://storage.example/blobs",
        "https://storage.example?x=1",
        "https://user@storage.example",
        "https://Storage.example",
        "https://",
        "storage.example",
        "https://storage.example:abc",
    ],
)
def test_a_redirect_is_an_https_address_and_nothing_is_sent_for_one_that_is_not(
    address: str,
) -> None:
    """The address is read by ``httpx2.URL`` too, and must be ``https://`` and a host name alone,
    written as the parser writes it back."""
    wire = Wire(_served())

    with pytest.raises(ValueError, match="https address"):
        wire.send(redirect=address)

    assert wire.requests == []


LONGEST = "https://storage.example/" + "a" * (egress.MAX_PATH - 1)


def test_what_a_location_appends_to_the_host_may_be_exactly_the_cap_and_not_a_character_more() -> (
    None
):
    """Calibration: the cap refuses what is over it, and only that."""
    wire = Wire(_redirecting(LONGEST), _served())
    assert wire.send().body == b"blob"

    over = Wire(_redirecting(LONGEST + "a"))
    with pytest.raises(RefusedError, match=f"appends more than {egress.MAX_PATH} characters"):
        over.send()
    assert len(over.requests) == 1


MISSING = "whose Location is missing;"
NOT_HTTPS = "whose Location is not an absolute https URL;"
FRAGMENT = "whose Location has a fragment;"
ANOTHER_HOST = "whose Location names another host;"
PORT = "whose Location names a port;"
USER_INFORMATION = "whose Location carries user information;"
REWRITTEN = "whose Location is not written as it is sent;"
OVER_THE_CAP = f"whose Location appends more than {egress.MAX_PATH} characters to the host;"

# One case, one rule: the rules are checked in this order (the module's docstring), and each
# case breaks the first rule it names and no rule before it. httpx2's readings are measured:
# the backslash ends the user information, so the host is ``other.example``; ``:443`` and
# capitals parse to no port and the lower-case host, and are written back without them; the
# trailing dot is kept; a non-ASCII host parses to its IDNA form; a space is written back as
# ``%20``; a port of letters is a parse error.
REFUSED = {
    "no location": (None, MISSING),
    "http": (f"http://storage.example{SIGNED_PATH}?{SIGNED_QUERY}", NOT_HTTPS),
    "a relative location": (f"{SIGNED_PATH}?{SIGNED_QUERY}", NOT_HTTPS),
    "a location the parser refuses": (
        f"https://storage.example:SIGNED{SIGNED_PATH}?{SIGNED_QUERY}",
        NOT_HTTPS,
    ),
    "a fragment": (f"{LOCATION}#SIGNED-FRAGMENT", FRAGMENT),
    "an empty fragment": (f"{LOCATION}#", FRAGMENT),
    "another host": (f"https://other.example{SIGNED_PATH}?{SIGNED_QUERY}", ANOTHER_HOST),
    "a host that begins with the one": (
        f"https://storage.example.other.example{SIGNED_PATH}?{SIGNED_QUERY}",
        ANOTHER_HOST,
    ),
    "the host after user information": (
        f"https://storage.example@other.example{SIGNED_PATH}?{SIGNED_QUERY}",
        ANOTHER_HOST,
    ),
    "the host after a backslash and user information": (
        f"https://storage.example\\@other.example{SIGNED_PATH}?{SIGNED_QUERY}",
        ANOTHER_HOST,
    ),
    "a host with a trailing dot": (
        f"https://storage.example.{SIGNED_PATH}?{SIGNED_QUERY}",
        ANOTHER_HOST,
    ),
    "a non-ASCII host": (
        f"https://st{chr(0xF6)}rage.example{SIGNED_PATH}?{SIGNED_QUERY}",
        ANOTHER_HOST,
    ),
    "a port": (f"https://storage.example:8443{SIGNED_PATH}?{SIGNED_QUERY}", PORT),
    "user information": (
        f"https://user@storage.example{SIGNED_PATH}?{SIGNED_QUERY}",
        USER_INFORMATION,
    ),
    "an explicit port 443": (
        f"https://storage.example:443{SIGNED_PATH}?{SIGNED_QUERY}",
        REWRITTEN,
    ),
    "a host in capitals": (f"https://STORAGE.example{SIGNED_PATH}?{SIGNED_QUERY}", REWRITTEN),
    "a space": (f"https://storage.example{SIGNED_PATH} 2?{SIGNED_QUERY}", REWRITTEN),
    "a path over the cap": (f"https://storage.example/{'SIGNED' * 400}", OVER_THE_CAP),
}


@pytest.mark.parametrize(("location", "rule"), REFUSED.values(), ids=list(REFUSED))
def test_a_location_the_redirect_does_not_allow_is_refused_by_its_rule_and_not_followed(
    location: str | None, rule: str
) -> None:
    wire = Wire(_redirecting(location))

    with pytest.raises(RefusedError) as info:
        wire.send()

    assert len(wire.requests) == 1  # the storage host was not asked: ``Wire`` would fail
    assert str(info.value) == (
        f"{OPERATION} failed: the host answered a redirect (307) {rule} "
        "only one to the host storage.example is followed"
    )
    _hides_the_location(info.value)


def test_a_second_redirect_is_refused_naming_the_operation_and_the_host_not_the_location() -> None:
    wire = Wire(_redirecting(), _redirecting("https://storage.example/SIGNED-SECOND?sig=SIGNED-2"))

    with pytest.raises(RefusedError) as info:
        wire.send()

    assert (
        str(info.value)
        == f"{FOLLOWED} failed: the host answered a redirect (307); no redirect is followed"
    )
    assert len(wire.requests) == 2
    _hides_the_location(info.value)


def test_the_deadline_counts_from_the_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first answer comes after 50 s and the followed one 11 s later: 11 s alone is within
    the deadline of 60 s, 61 s from the start is not."""
    clock = Clock(monkeypatch)

    def first(request: httpx2.Request) -> httpx2.Response:
        clock.advance(50)()
        return _redirecting()(request)

    def second(request: httpx2.Request) -> httpx2.Response:
        clock.advance(11)()
        return httpx2.Response(200, content=b"late")

    wire = Wire(first, second)

    with pytest.raises(RefusedError) as info:
        wire.send()

    assert str(info.value) == f"{FOLLOWED} failed: no complete answer within {egress.DEADLINE:g} s"
    _hides_the_location(info.value)


def test_the_body_cap_holds_for_the_followed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(egress, "MAX_BODY", 1000)

    def second(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=Chunks([b" " * 600, b" " * 600]))

    with pytest.raises(RefusedError) as info:
        Wire(_redirecting(), second).send()

    assert str(info.value) == f"{FOLLOWED} failed: the body is over the cap of 1000 bytes"
    _hides_the_location(info.value)


def test_the_encoding_rule_holds_for_the_followed_request() -> None:
    def second(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"Content-Encoding": "gzip"}, content=b"x")

    with pytest.raises(RefusedError) as info:
        Wire(_redirecting(), second).send()

    assert str(info.value) == f"{FOLLOWED} failed: the body is encoded"
    _hides_the_location(info.value)


def test_a_status_of_the_followed_request_is_shown_alone_and_nothing_of_its_body() -> None:
    def second(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"message": "SIGNED-TEXT"})

    with pytest.raises(RefusedError) as info:
        Wire(_redirecting(), second).send()

    assert str(info.value) == f"{FOLLOWED} -> 403"
    _hides_the_location(info.value)


def test_a_failure_of_the_followed_request_names_its_type_and_chains_nothing() -> None:
    """httpx2's own message of a failure can hold the URL, so it is neither shown nor kept."""

    def second(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(RefusedError) as info:
        Wire(_redirecting(), second).send()

    assert str(info.value) == f"{FOLLOWED} failed: ConnectError"
    _hides_the_location(info.value)


def test_the_credential_of_the_first_request_is_in_no_error_of_the_followed_one() -> None:
    wire = Wire(_redirecting(), lambda request: httpx2.Response(500))

    with pytest.raises(RefusedError) as info:
        wire.send()

    assert SENT not in str(info.value)

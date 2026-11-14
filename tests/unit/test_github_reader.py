"""Unit tests for the GitHub reader and the HTTP core it shares with the writer.

No test reaches the network: the host's real answers, recorded once by
``tests/fixtures/github/record.sh``, are replayed through ``httpx.MockTransport``. Each refusal
is a defect case made on a recorded answer with one field changed, so a test that passes names
the field the reader looked at.
"""

import copy
import json
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from gora.adapters.github import (
    GitHubError,
    GitHubReader,
    TagKind,
    TagObject,
    TagRef,
    _http,
    reader,
)
from gora.domain.release.tags import RecordError, recorded
from tests.audit_fakes import intercept_transport
from tests.unit.test_identifiers import INVALID_REPOS

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "github"

FIXTURES = "gora-tools/gora-fixtures"
OLD_NAME = "gora-tools/gora-fixtures-before"
GORA = "gora-tools/gora"
SENTINEL = "fake-sentinel-github-token"

COMMIT = "51c9f03ab5fdfb0ddd440eecd0dde61ba5bb7e01"
RC1 = "9117328866550023d4a7ee750a0e4226072da71f"
RC2 = "0ffeddcefe03df638ca1f78c8ffdbb39f149ca13"
RC3 = "3365fad432f0657966bcf83c0187ef90c44c674b"
UNKNOWN = "1111111111111111111111111111111111111111"
OTHER = "2222222222222222222222222222222222222222"

REPO_PATH = f"/repos/{FIXTURES}"
REF_MAIN = f"{REPO_PATH}/git/ref/heads/main"
TAG_REFS = f"{REPO_PATH}/git/matching-refs/tags/v"
TAG_REFS_NONE = f"{REPO_PATH}/git/matching-refs/tags/fixture/v"
GORA_TAG_REFS = f"/repos/{GORA}/git/matching-refs/tags/v"


def tag_path(sha: str) -> str:
    return f"{REPO_PATH}/git/tags/{sha}"


# --- the recorded answers --------------------------------------------------------------------


def replay(
    name: str,
    *,
    change: Callable[[Any], Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """A recorded answer, as the host sent it; ``change`` is a defect made on its body, and
    ``headers`` the ones added to it."""
    head, _, body = (FIXTURES_DIR / f"{name}.http").read_bytes().partition(b"\n\n")
    status_line, *lines = head.decode().split("\n")
    fields: list[tuple[str, str]] = []
    for line in lines:
        key, _, value = line.partition(": ")
        fields.append((key, value))
    fields.extend((headers or {}).items())
    if change is not None:
        body = json.dumps(change(json.loads(body))).encode()
    return httpx.Response(int(status_line.split()[1]), headers=fields, content=body)


def setting(*path: str | int, to: Any) -> Callable[[Any], Any]:
    """A defect: the field at ``path`` of the recorded body holds ``to`` instead."""

    def change(data: Any) -> Any:
        data = copy.deepcopy(data)
        node = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = to
        return data

    return change


def removing(*path: str | int) -> Callable[[Any], Any]:
    """A defect: the field at ``path`` of the recorded body is missing."""

    def change(data: Any) -> Any:
        data = copy.deepcopy(data)
        node = data
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
        return data

    return change


class Host:
    """A fake code host: it answers the paths it has routes for and records every request.

    A request to a path it has no route for is a ``KeyError`` in the test, never an answer.
    """

    def __init__(self, routes: dict[str, Callable[[], httpx.Response]]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes[request.url.path]()


def reader_on(
    handler: Callable[[httpx.Request], httpx.Response],
    repo: str = FIXTURES,
    *,
    follow_redirects: bool = False,
) -> GitHubReader:
    """A reader whose client carries a sentinel token, as the real one does."""
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {SENTINEL}"},
        follow_redirects=follow_redirects,
    )
    return GitHubReader(repo, SENTINEL, client=client)


def answering(path: str, name: str, **kwargs: Any) -> Host:
    return Host({path: lambda: replay(name, **kwargs)})


def tag_ref(name: str) -> TagRef:
    """The ref ``tag_refs`` returns for ``name``, from the recorded list."""
    host = answering(TAG_REFS, "tag-refs")
    (ref,) = (ref for ref in reader_on(host).tag_refs("") if ref.name == name)
    return ref


# --- the surface ---------------------------------------------------------------------------


def test_the_reader_has_exactly_these_public_methods() -> None:
    """The reader has no write: its operations are these four.

    The list is written out. A method added, renamed or removed is a red test and a reviewed
    edit of this list.
    """
    public = [name for name in dir(GitHubReader) if not name.startswith("_")]

    assert public == ["branch_head", "repository_id", "tag_object", "tag_refs"]


def test_token_required() -> None:
    with pytest.raises(ValueError, match="token is required"):
        GitHubReader(FIXTURES, "")


@pytest.mark.parametrize("repo", [*INVALID_REPOS, "o/r?sentinel-7f3a"])
def test_a_repository_the_grammar_refuses_is_refused_at_construction(repo: str) -> None:
    with pytest.raises(ValueError, match="GitHub repository") as info:
        GitHubReader(repo, "test-token")

    assert "sentinel-7f3a" not in str(info.value)


def test_the_client_the_reader_builds_itself_sends_its_headers_and_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No client is injected, so the request is the reader's own, held at the transport:
    nothing leaves the process."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replay("repo")

    intercept_transport(monkeypatch, answer)

    assert GitHubReader(FIXTURES, "test-token").repository_id() == 1457970812

    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == f"https://api.github.com{REPO_PATH}"
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 10.0
    )


def test_the_timeout_the_reader_is_given_is_the_one_its_client_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replay("repo")

    intercept_transport(monkeypatch, answer)

    GitHubReader(FIXTURES, "test-token", timeout=2.5).repository_id()

    (request,) = seen
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.5)


# --- repository_id -------------------------------------------------------------------------


def test_the_repository_id_is_the_one_the_host_recorded() -> None:
    host = answering(REPO_PATH, "repo")

    assert reader_on(host).repository_id() == 1457970812

    (request,) = host.requests
    assert request.method == "GET"
    assert request.url.query == b""


def test_the_host_may_spell_the_name_with_other_case() -> None:
    """GitHub compares names without case."""
    host = answering(REPO_PATH, "repo", change=setting("full_name", to="Gora-Tools/GORA-Fixtures"))

    assert reader_on(host).repository_id() == 1457970812


@pytest.mark.parametrize(
    "name",
    ["gora-tools/another", "gora-tools/gora-fixtures-before", ""],
    ids=["another-name", "the-old-name", "empty"],
)
def test_another_name_is_refused(name: str) -> None:
    host = answering(REPO_PATH, "repo", change=setting("full_name", to=name))

    with pytest.raises(GitHubError, match=reader.NOT_THE_REPOSITORY):
        reader_on(host).repository_id()


def test_a_name_that_only_lowers_to_the_name_asked_is_refused() -> None:
    """U+212A, the Kelvin sign, lower-cases to k: the comparison is made on ASCII."""
    host = answering(
        "/repos/owner/kit", "repo", change=setting("full_name", to=f"owner/{chr(0x212A)}it")
    )

    with pytest.raises(GitHubError, match=reader.NOT_THE_REPOSITORY):
        reader_on(host, "owner/kit").repository_id()


@pytest.mark.parametrize(
    "identity",
    ["1457970812", 0, -1, True, 1.5, None],
    ids=["a-string", "zero", "negative", "a-boolean", "a-float", "null"],
)
def test_an_id_that_is_not_a_positive_integer_is_refused(identity: object) -> None:
    host = answering(REPO_PATH, "repo", change=setting("id", to=identity))

    with pytest.raises(GitHubError, match=reader.NOT_A_POSITIVE_ID):
        reader_on(host).repository_id()


def test_a_renamed_repository_is_refused_and_its_redirect_is_not_followed() -> None:
    """The old name answers its real 301. A client that follows redirects would find the
    repository by its id; the identity of a repository is its id, never a name a redirect
    leads to."""
    host = answering(f"/repos/{OLD_NAME}", "repo-renamed")

    with pytest.raises(GitHubError) as info:
        reader_on(host, OLD_NAME, follow_redirects=True).repository_id()

    assert str(info.value) == (
        f"GET /repos/{OLD_NAME} failed: the repository was renamed or moved (301); "
        "no redirect is followed"
    )
    assert [request.url.path for request in host.requests] == [f"/repos/{OLD_NAME}"]


# --- branch_head -----------------------------------------------------------------------------


def test_the_head_of_a_branch_is_the_commit_the_host_recorded() -> None:
    host = answering(REF_MAIN, "ref-main")

    assert reader_on(host).branch_head("main") == COMMIT

    (request,) = host.requests
    assert request.method == "GET"
    assert request.url.query == b""


def test_a_branch_name_is_percent_encoded_except_its_slashes() -> None:
    branches = ["gora/promote-dev-1.2.0-rc.1", "a b/c#d?e%f" + chr(1)]
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return replay("ref-main", change=setting("ref", to=f"refs/heads/{branches[len(seen) - 1]}"))

    for branch in branches:
        assert reader_on(handler).branch_head(branch) == COMMIT

    assert seen == [
        f"{REPO_PATH}/git/ref/heads/gora/promote-dev-1.2.0-rc.1".encode(),
        f"{REPO_PATH}/git/ref/heads/a%20b/c%23d%3Fe%25f%01".encode(),
    ]


def test_an_empty_branch_is_a_caller_s_error_and_asks_nothing() -> None:
    host = Host({})

    with pytest.raises(ValueError, match="branch must not be empty"):
        reader_on(host).branch_head("")

    assert host.requests == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (setting("ref", to="refs/heads/another"), reader.NOT_THE_REF),
        (setting("ref", to="refs/tags/main"), reader.NOT_THE_REF),
        (setting("object", "type", to="tag"), reader.NOT_A_COMMIT),
        (setting("object", "type", to="blob"), reader.NOT_A_COMMIT),
        (setting("object", "sha", to="abc123"), reader.NOT_AN_OBJECT_NAME),
        (setting("object", "sha", to=COMMIT.upper()), reader.NOT_AN_OBJECT_NAME),
        (setting("object", "sha", to=7), reader.NOT_AN_OBJECT_NAME),
        (setting("object", to="a string"), reader.NOT_AN_OBJECT),
    ],
    ids=[
        "another-branch",
        "a-tag",
        "a-tag-object",
        "a-blob",
        "a-short-name",
        "upper-case",
        "not-a-string",
        "no-object",
    ],
)
def test_a_head_that_is_not_the_commit_of_the_branch_asked_is_refused(
    change: Callable[[Any], Any], reason: str
) -> None:
    host = answering(REF_MAIN, "ref-main", change=change)

    with pytest.raises(GitHubError, match=reason):
        reader_on(host).branch_head("main")


# --- tag_refs --------------------------------------------------------------------------------


FIXTURE_TAGS = (
    TagRef("v0.0.1-rc", RC1, TagKind.ANNOTATED),
    TagRef("v0.0.2-rc", RC2, TagKind.ANNOTATED),
    TagRef("v0.0.3-rc", RC3, TagKind.ANNOTATED),
    TagRef("v0.0.4-rc", COMMIT, TagKind.LIGHTWEIGHT),
    TagRef("vnext", COMMIT, TagKind.LIGHTWEIGHT),
)


def test_the_tags_are_the_refs_the_host_matched_in_its_one_response() -> None:
    """``vnext`` is among them: the prefix is a string prefix, and which names are rc tags is
    the domain's to decide."""
    host = answering(TAG_REFS, "tag-refs")

    assert reader_on(host).tag_refs("") == FIXTURE_TAGS

    (request,) = host.requests
    assert request.method == "GET"
    assert request.url.query == b""
    assert "link" not in replay("tag-refs").headers


def test_a_prefix_with_no_match_is_no_tag() -> None:
    host = answering(TAG_REFS_NONE, "tag-refs-none")

    assert reader_on(host).tag_refs("fixture/") == ()


def test_lightweight_tags_are_lightweight() -> None:
    host = answering(GORA_TAG_REFS, "gora-tag-refs")

    refs = reader_on(host, GORA).tag_refs("")

    assert [ref.name for ref in refs] == [f"v0.2.0-rc.{n}" for n in range(1, 7)]
    assert {ref.kind for ref in refs} == {TagKind.LIGHTWEIGHT}


@pytest.mark.parametrize("prefix", ["api", "API/", "a b/", "/", "a/b/", "../"])
def test_a_tag_prefix_outside_its_grammar_is_a_caller_s_error_and_asks_nothing(
    prefix: str,
) -> None:
    host = Host({})

    with pytest.raises(ValueError, match="tag prefix"):
        reader_on(host).tag_refs(prefix)

    assert host.requests == []


def test_refs_outside_the_prefix_asked_are_refused() -> None:
    """The host answers the refs under ``v``, and the reader asked for those under
    ``fixture/v``."""
    host = answering(f"{REPO_PATH}/git/matching-refs/tags/fixture/v", "tag-refs")

    with pytest.raises(GitHubError, match=reader.OUTSIDE_THE_PREFIX):
        reader_on(host).tag_refs("fixture/")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (setting(1, "ref", to="refs/tags/w0.0.2-rc"), reader.OUTSIDE_THE_PREFIX),
        (setting(1, "ref", to="refs/heads/v0.0.2-rc"), reader.OUTSIDE_THE_PREFIX),
        (setting(1, "ref", to=2), reader.OUTSIDE_THE_PREFIX),
        (setting(1, "object", "type", to="blob"), reader.NOT_A_TAG_OR_COMMIT),
        (setting(1, "object", "type", to="tree"), reader.NOT_A_TAG_OR_COMMIT),
        (setting(1, "object", "type", to=["tag"]), reader.NOT_A_TAG_OR_COMMIT),
        (setting(1, "object", "sha", to="abc123"), reader.NOT_AN_OBJECT_NAME),
        (setting(1, "object", to=None), reader.NOT_AN_OBJECT),
        (lambda refs: refs[0], reader.NOT_A_LIST),
    ],
    ids=[
        "another-prefix",
        "not-a-tag-ref",
        "a-ref-that-is-no-string",
        "a-blob",
        "a-tree",
        "a-type-that-is-no-string",
        "a-short-name",
        "no-object",
        "not-a-list",
    ],
)
def test_one_ref_that_is_not_what_was_asked_refuses_the_answer(
    change: Callable[[Any], Any], reason: str
) -> None:
    host = answering(TAG_REFS, "tag-refs", change=change)

    with pytest.raises(GitHubError, match=reason):
        reader_on(host).tag_refs("")


NEXT_PAGE = (
    '<https://api.github.com/repositories/1457970812/git/matching-refs/tags/v?page=2>; rel="next"'
)


@pytest.mark.parametrize(
    "link",
    [
        NEXT_PAGE,
        "",
    ],
    ids=["next-page", "empty"],
)
def test_an_answer_with_a_link_header_is_refused(link: str) -> None:
    """The endpoint returns every match in one response (measured), so a ``Link`` means it
    changed, and following it would be the code guessing."""
    host = answering(TAG_REFS, "tag-refs", headers={"Link": link})

    with pytest.raises(GitHubError, match=reader.PAGED):
        reader_on(host).tag_refs("")

    assert len(host.requests) == 1


# --- tag_object ------------------------------------------------------------------------------


def test_the_tag_object_of_an_annotated_tag_is_what_the_host_recorded() -> None:
    host = answering(tag_path(RC1), "tag-v0.0.1-rc")
    message = json.loads(replay("tag-v0.0.1-rc").content)["message"]

    tag = reader_on(host).tag_object(tag_ref("v0.0.1-rc"))

    assert tag == TagObject("v0.0.1-rc", RC1, COMMIT, message)
    (request,) = host.requests
    assert request.method == "GET"
    assert request.url.query == b""


def test_the_message_of_the_recorded_candidate_tag_is_one_the_domain_reads_back() -> None:
    """The fixture's first tag carries the message ``rc_tag_message`` wrote, and the domain
    reads its trailer back from the bytes the host kept."""
    host = answering(tag_path(RC1), "tag-v0.0.1-rc")

    tag = reader_on(host).tag_object(tag_ref("v0.0.1-rc"))

    candidate = recorded(tag.name, tag.message, artifact="fixture", tag_prefix="")
    assert str(candidate.version) == "0.0.1-rc"
    assert candidate.digest == "sha256:" + "a" * 64


def test_the_message_of_a_tag_with_no_trailer_is_returned_and_the_domain_refuses_it() -> None:
    host = answering(tag_path(RC2), "tag-v0.0.2-rc")

    tag = reader_on(host).tag_object(tag_ref("v0.0.2-rc"))

    assert tag.message == "fixture 0.0.2-rc\n\nAn annotated tag with no trailer.\n"
    with pytest.raises(RecordError):
        recorded(tag.name, tag.message, artifact="fixture", tag_prefix="")


def test_a_tag_of_a_tag_is_refused() -> None:
    """``v0.0.3-rc`` tags ``v0.0.1-rc``'s tag object: following the chain would be an unbounded
    number of requests, and GORA tags commits only."""
    host = answering(tag_path(RC3), "tag-v0.0.3-rc")

    with pytest.raises(GitHubError, match=reader.TAG_OF_A_TAG):
        reader_on(host).tag_object(tag_ref("v0.0.3-rc"))

    assert len(host.requests) == 1


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (setting("sha", to=OTHER), reader.NOT_THE_REFS_SHA),
        (setting("sha", to=7), reader.NOT_THE_REFS_SHA),
        (setting("tag", to="v0.0.2-rc"), reader.NOT_THE_REFS_NAME),
        (setting("tag", to="fixture/v0.0.1-rc"), reader.NOT_THE_REFS_NAME),
        (setting("object", "type", to="tag"), reader.TAG_OF_A_TAG),
        (setting("object", "type", to="blob"), reader.NOT_A_COMMIT),
        (setting("object", "type", to=None), reader.NOT_A_COMMIT),
        (setting("object", "sha", to="abc123"), reader.NOT_AN_OBJECT_NAME),
        (setting("object", to="a string"), reader.NOT_AN_OBJECT),
        (setting("message", to=7), reader.NOT_A_MESSAGE),
    ],
    ids=[
        "another-sha",
        "a-sha-that-is-no-string",
        "another-name",
        "another-prefix",
        "a-target-of-type-tag",
        "an-unknown-object-type",
        "no-object-type",
        "a-short-target",
        "no-target",
        "no-message",
    ],
)
def test_a_tag_object_that_is_not_the_refs_is_refused(
    change: Callable[[Any], Any], reason: str
) -> None:
    host = answering(tag_path(RC1), "tag-v0.0.1-rc", change=change)

    with pytest.raises(GitHubError, match=reason):
        reader_on(host).tag_object(tag_ref("v0.0.1-rc"))


def test_a_lightweight_tag_has_no_tag_object_and_asks_nothing() -> None:
    host = Host({})

    with pytest.raises(ValueError, match="only an annotated tag"):
        reader_on(host).tag_object(FIXTURE_TAGS[3])

    assert host.requests == []


def test_an_unknown_tag_sha_is_the_hosts_404() -> None:
    host = answering(tag_path(UNKNOWN), "tag-unknown")

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag_object(TagRef("v0.0.1-rc", UNKNOWN, TagKind.ANNOTATED))

    assert str(info.value).startswith(f"GET {tag_path(UNKNOWN)} -> 404: ")
    assert "Not Found" in str(info.value)


# --- the values the reader returns -----------------------------------------------------------


BAD_OBJECT_NAMES = ["", "abc123", COMMIT.upper(), COMMIT[:-1], COMMIT + "0", "g" * 40]


@pytest.mark.parametrize("object_name", BAD_OBJECT_NAMES)
def test_a_tag_ref_is_validated_where_it_is_built(object_name: str) -> None:
    with pytest.raises(ValueError, match="object name must be a git object name"):
        TagRef("v1", object_name, TagKind.ANNOTATED)


def test_a_tag_ref_has_a_name() -> None:
    with pytest.raises(ValueError, match="tag name must not be empty"):
        TagRef("", COMMIT, TagKind.ANNOTATED)


def test_values_are_frozen() -> None:
    ref = TagRef("v1", COMMIT, TagKind.LIGHTWEIGHT)
    tag = TagObject("v1", RC1, COMMIT, "m")

    with pytest.raises(FrozenInstanceError):
        ref.name = "v2"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        tag.message = "m2"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["object_name", "target"])
@pytest.mark.parametrize("value", BAD_OBJECT_NAMES)
def test_a_tag_object_is_validated_where_it_is_built(field: str, value: str) -> None:
    valid = TagObject("v1", RC1, COMMIT, "m")

    with pytest.raises(ValueError, match=f"{field.replace('_', ' ')} must be a git object name"):
        replace(valid, **{field: value})


def test_a_tag_object_has_a_name() -> None:
    with pytest.raises(ValueError, match="tag name must not be empty"):
        TagObject("", RC1, COMMIT, "m")


def test_the_kinds_are_these_two() -> None:
    assert [kind.value for kind in TagKind] == ["lightweight", "annotated"]


# --- the core: what every request is bound by ------------------------------------------------

RATE_LIMIT = {"X-RateLimit-Reset": "1794678624", "Retry-After": "60"}


@pytest.mark.parametrize("status", [403, 429])
def test_a_rate_limit_names_the_status_and_the_two_headers(status: int) -> None:
    host = Host({REPO_PATH: lambda: httpx.Response(status, headers=RATE_LIMIT, content=b"slow")})

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == (
        f"GET {REPO_PATH} -> {status}: slow (x-ratelimit-reset: 1794678624, retry-after: 60)"
    )


@pytest.mark.parametrize(
    ("headers", "shown"),
    [
        ({"Retry-After": "60"}, " (retry-after: 60)"),
        ({"X-RateLimit-Reset": "1794678624"}, " (x-ratelimit-reset: 1794678624)"),
        ({}, ""),
        ({"Retry-After": "a" + chr(27) + "[2Jb", "X-RateLimit-Reset": "x" * 65}, ""),
        ({"Retry-After": "", "X-RateLimit-Limit": "5000", "Link": "<x>"}, ""),
    ],
    ids=["retry-after-only", "reset-only", "neither", "unsafe-values", "no-other-header"],
)
def test_a_rate_limit_shows_the_headers_that_are_there_and_safe_to_show(
    headers: dict[str, str], shown: str
) -> None:
    host = Host({REPO_PATH: lambda: httpx.Response(429, headers=headers, content=b"slow")})

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} -> 429: slow{shown}"


def test_another_error_status_shows_no_header() -> None:
    host = Host({REPO_PATH: lambda: httpx.Response(500, headers=RATE_LIMIT, content=b"boom")})

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} -> 500: boom"


@pytest.mark.parametrize("status", [403, 429, 500, 502])
def test_nothing_is_retried(status: int) -> None:
    host = Host({REPO_PATH: lambda: httpx.Response(status, headers=RATE_LIMIT)})

    with pytest.raises(GitHubError):
        reader_on(host).repository_id()

    assert len(host.requests) == 1


def test_a_transport_error_is_not_retried_either() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(GitHubError):
        reader_on(handler).repository_id()

    assert len(calls) == 1


class Chunks(httpx.SyncByteStream):
    """A body that arrives in chunks, ``each`` called before every one of them."""

    def __init__(self, chunks: list[bytes], each: Callable[[], None] = lambda: None) -> None:
        self._chunks = chunks
        self._each = each

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._chunks:
            self._each()
            yield chunk


def test_a_body_over_the_cap_is_refused_never_read_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "MAX_BODY", 1000)
    host = Host({TAG_REFS: lambda: httpx.Response(200, stream=Chunks([b"[" * 600, b"]" * 600]))})

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag_refs("")

    assert str(info.value) == f"GET {TAG_REFS} failed: the body is over the cap of 1000 bytes"


def test_a_body_the_host_declares_over_the_cap_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_http, "MAX_BODY", 1000)

    def never() -> None:
        raise AssertionError("the body was read")

    host = Host(
        {
            TAG_REFS: lambda: httpx.Response(
                200, headers={"Content-Length": "1001"}, stream=Chunks([b"[]"], never)
            )
        }
    )

    with pytest.raises(GitHubError, match="over the cap of 1000 bytes"):
        reader_on(host).tag_refs("")


def test_a_body_of_exactly_the_cap_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calibration: the cap refuses one byte more, and only that."""
    monkeypatch.setattr(_http, "MAX_BODY", 1000)
    body = b"[]" + b" " * 998
    host = Host({TAG_REFS: lambda: httpx.Response(200, stream=Chunks([body[:500], body[500:]]))})

    assert reader_on(host).tag_refs("") == ()


class Clock:
    """A clock the test moves, in place of the one the core reads."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 0.0
        monkeypatch.setattr(_http, "_now", lambda: self.now)

    def advance(self, seconds: float) -> Callable[[], None]:
        def move() -> None:
            self.now += seconds

        return move


def test_an_answer_that_arrives_too_slowly_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(monkeypatch)
    slowly = [b"[", b" ", b"]"]
    host = Host({TAG_REFS: lambda: httpx.Response(200, stream=Chunks(slowly, clock.advance(30)))})

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag_refs("")

    assert str(info.value) == f"GET {TAG_REFS} failed: no complete answer within 60 s"


def test_an_answer_that_ends_at_the_deadline_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calibration: the deadline refuses what is past it, and only that."""
    clock = Clock(monkeypatch)
    host = Host(
        {TAG_REFS: lambda: httpx.Response(200, stream=Chunks([b"[", b"]"], clock.advance(30)))}
    )

    assert reader_on(host).tag_refs("") == ()


def test_headers_that_arrive_too_late_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(61)()
        return replay("repo")

    with pytest.raises(GitHubError, match="no complete answer within 60 s"):
        reader_on(handler).repository_id()


OPERATIONS: dict[str, Callable[[GitHubReader], object]] = {
    "repository_id": lambda r: r.repository_id(),
    "branch_head": lambda r: r.branch_head("main"),
    "tag_refs": lambda r: r.tag_refs(""),
    "tag_object": lambda r: r.tag_object(FIXTURE_TAGS[0]),
}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.TooManyRedirects],
    ids=lambda e: e.__name__,
)
def test_a_transport_error_becomes_github_error(
    operation: str, error: type[httpx.RequestError]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("[Errno 8] nodename nor servname provided", request=request)

    with pytest.raises(GitHubError) as info:
        OPERATIONS[operation](reader_on(handler))

    message = str(info.value)
    assert message.startswith("GET /repos/")  # names the operation
    assert message.endswith(f" failed: {error.__name__}")  # and nothing httpx wrote
    assert SENTINEL not in message
    assert isinstance(info.value.__cause__, httpx.HTTPError)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_url_httpx_refuses_becomes_github_error(operation: str) -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    unreadable = GitHubReader(
        FIXTURES, SENTINEL, base_url="https://api.github.com" + chr(1), client=client
    )

    with pytest.raises(GitHubError, match="failed: invalid URL") as info:
        OPERATIONS[operation](unreadable)

    assert called == []
    assert SENTINEL not in str(info.value)
    assert isinstance(info.value.__cause__, httpx.InvalidURL)


MALFORMED: dict[str, Callable[[], httpx.Response]] = {
    "not-json": lambda: httpx.Response(200, text="<html>not json</html>"),
    "wrong-object": lambda: httpx.Response(200, json={"unexpected": "shape"}),
    "a-list": lambda: httpx.Response(200, json=["a", "list"]),
    "list-of-wrong-objects": lambda: httpx.Response(200, json=[{"no": "ref"}]),
}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize("body", list(MALFORMED))
def test_an_answer_that_is_not_the_expected_json_becomes_github_error(
    operation: str, body: str
) -> None:
    with pytest.raises(GitHubError, match="unexpected response") as info:
        OPERATIONS[operation](reader_on(lambda request: MALFORMED[body]()))

    assert SENTINEL not in str(info.value)


REFUSALS: dict[str, Callable[[httpx.Request], httpx.Response]] = {
    "a-500": lambda request: httpx.Response(500, text="boom"),
    "a-redirect": lambda request: replay("repo-renamed"),
    "a-rate-limit": lambda request: httpx.Response(429, headers=RATE_LIMIT, text="slow"),
}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize("refusal", list(REFUSALS))
def test_a_refusal_names_the_operation_and_holds_no_token_and_no_query(
    operation: str, refusal: str
) -> None:
    with pytest.raises(GitHubError) as info:
        OPERATIONS[operation](reader_on(REFUSALS[refusal]))

    assert str(info.value).startswith("GET /repos/")
    assert SENTINEL not in str(info.value)
    assert "?" not in str(info.value)

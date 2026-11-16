"""Unit tests for the GitHub reader and the HTTP core it shares with the writer.

No test reaches the network: the host's real answers, recorded once by
``tests/fixtures/github/record.sh``, are replayed through ``httpx2.MockTransport``, each answer
as an unread stream as a transport returns it (``tests/audit_fakes.py``). Each refusal is a
defect case made on a recorded answer with one field changed, so a test that passes names the
field the reader looked at.
"""

import copy
import gzip
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from gora.adapters import egress
from gora.adapters.github import GitHubError, GitHubReader, reader
from gora.domain.release.tags import RecordError, Tag, recorded
from gora.services.code_host import CodeHost
from tests.audit_fakes import Chunks, intercept_transport, mock_transport
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
OTHER = "2222222222222222222222222222222222222222"

REPO_PATH = f"/repos/{FIXTURES}"
REF_MAIN = f"{REPO_PATH}/git/ref/heads/main"
TAG_REFS = f"{REPO_PATH}/git/matching-refs/tags/v"
TAG_REFS_NONE = f"{REPO_PATH}/git/matching-refs/tags/fixture/v"
GORA_TAG_REFS = f"/repos/{GORA}/git/matching-refs/tags/v"


def tag_path(sha: str) -> str:
    return f"{REPO_PATH}/git/tags/{sha}"


def ref_tag_path(name: str) -> str:
    return f"{REPO_PATH}/git/ref/tags/{name}"


# --- the recorded answers --------------------------------------------------------------------


def replay(
    name: str,
    *,
    change: Callable[[Any], Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
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
    return httpx2.Response(int(status_line.split()[1]), headers=fields, content=body)


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

    def __init__(self, routes: dict[str, Callable[[], httpx2.Response]]) -> None:
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.routes[request.url.path]()


def reader_on(
    handler: Callable[[httpx2.Request], httpx2.Response], repo: str = FIXTURES
) -> GitHubReader:
    """A reader on the sentinel token, whose requests the handler answers."""
    return GitHubReader(repo, SENTINEL, transport=mock_transport(handler))


def answering(path: str, name: str, **kwargs: Any) -> Host:
    return Host({path: lambda: replay(name, **kwargs)})


# --- the surface ---------------------------------------------------------------------------


def test_the_reader_has_exactly_these_public_methods() -> None:
    """The reader has no write: its operations are these four.

    The list is written out. A method added, renamed or removed is a red test and a reviewed
    edit of this list.
    """
    public = [name for name in dir(GitHubReader) if not name.startswith("_")]

    assert public == ["branch_head", "repository_id", "tag", "tag_names"]


def test_the_reader_has_exactly_the_methods_of_the_port() -> None:
    """``CodeHost`` is the port the services read a code host through, and an adapter serves it
    whole: a method the reader gains or loses is a method the port gains or loses, by a reviewed
    edit of the port."""
    reader_methods = [name for name in dir(GitHubReader) if not name.startswith("_")]
    port_methods = [name for name in dir(CodeHost) if not name.startswith("_")]

    assert reader_methods == port_methods


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
    """The transport is not replaced, so the request is the reader's own, held at the
    transport: nothing leaves the process."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return replay("repo")

    intercept_transport(monkeypatch, answer)

    assert GitHubReader(FIXTURES, "test-token").repository_id() == "1457970812"

    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == f"https://api.github.com{REPO_PATH}"
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert request.headers["accept-encoding"] == "identity"
    assert request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 10.0
    )


def test_every_request_asks_for_identity_encoding_whatever_the_client_s_defaults_say() -> None:
    """The client brings httpx2's own default headers, ``Accept-Encoding: gzip, deflate``
    among them; the request's own header is the one that is sent."""
    host = answering(REPO_PATH, "repo")

    reader_on(host).repository_id()

    (request,) = host.requests
    assert request.headers.get_list("accept-encoding") == ["identity"]


def test_the_timeout_the_reader_is_given_is_the_one_its_client_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return replay("repo")

    intercept_transport(monkeypatch, answer)

    GitHubReader(FIXTURES, "test-token", timeout=2.5).repository_id()

    (request,) = seen
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.5)


# --- repository_id -------------------------------------------------------------------------


def test_the_repository_id_is_the_one_the_host_recorded() -> None:
    host = answering(REPO_PATH, "repo")

    assert reader_on(host).repository_id() == "1457970812"

    (request,) = host.requests
    assert request.method == "GET"
    assert request.url.query == b""


def test_the_host_may_spell_the_name_with_other_case() -> None:
    """GitHub compares names without case."""
    host = answering(REPO_PATH, "repo", change=setting("full_name", to="Gora-Tools/GORA-Fixtures"))

    assert reader_on(host).repository_id() == "1457970812"


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
    ["1457970812", 1457970812.0, 0, -1, True, 1.5, None, 2**63, 10**4000],
    ids=[
        "a-string",
        "a-float-of-the-same-value",
        "zero",
        "negative",
        "a-boolean",
        "a-float",
        "null",
        "above-64-bits",
        "4000-digits",
    ],
)
def test_an_id_that_is_not_a_positive_64_bit_integer_is_refused(identity: object) -> None:
    host = answering(REPO_PATH, "repo", change=setting("id", to=identity))

    with pytest.raises(GitHubError, match=reader.NOT_AN_ID):
        reader_on(host).repository_id()


@pytest.mark.parametrize(
    ("identity", "text"),
    [(1, "1"), (2**63 - 1, "9223372036854775807")],
    ids=["the-first", "the-last-64-bit"],
)
def test_an_id_at_either_end_of_the_range_is_accepted(identity: int, text: str) -> None:
    """Calibration: the bound refuses one more than GitHub's ids hold, and only that. The id is
    returned as its decimal string, which ``REPOSITORY_ID`` accepts."""
    host = answering(REPO_PATH, "repo", change=setting("id", to=identity))

    assert reader_on(host).repository_id() == text


def test_a_renamed_repository_is_refused_and_its_redirect_is_not_followed() -> None:
    """The old name answers its real 301. Followed, it would lead to the repository by its id;
    the identity of a repository is its id, never a name a redirect leads to. The core says
    what it saw, a redirect, and not what it guesses the cause to be."""
    host = answering(f"/repos/{OLD_NAME}", "repo-renamed")

    with pytest.raises(GitHubError) as info:
        reader_on(host, OLD_NAME).repository_id()

    assert str(info.value) == (
        f"GET /repos/{OLD_NAME} failed: the host answered a redirect (301); no redirect is followed"
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

    def handler(request: httpx2.Request) -> httpx2.Response:
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
    "branch",
    ["..", ".", "../..", "a/../b", "a/./b", "a//b", "/a", "a/", "../../../user/x"],
)
def test_a_branch_that_would_leave_its_path_is_a_caller_s_error_and_asks_nothing(
    branch: str,
) -> None:
    """``quote`` keeps ``.`` and httpx2 removes dot segments: ``..`` would have sent the GET to
    another path, with the token."""
    host = Host({})

    with pytest.raises(ValueError, match="a part of a path must not be empty"):
        reader_on(host).branch_head(branch)

    assert host.requests == []


@pytest.mark.parametrize("branch", ["a.b", "...", "a/.b/c", "v1.0.", ".hidden"])
def test_a_branch_with_dots_that_are_no_whole_part_is_asked_for(branch: str) -> None:
    """Calibration: only a part that is exactly ``.`` or ``..`` is refused."""
    host = Host({})
    host.routes = {
        f"{REPO_PATH}/git/ref/heads/{branch}": lambda: replay(
            "ref-main", change=setting("ref", to=f"refs/heads/{branch}")
        )
    }

    assert reader_on(host).branch_head(branch) == COMMIT


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


# --- tag_names -------------------------------------------------------------------------------


FIXTURE_NAMES = ("v0.0.1-rc", "v0.0.2-rc", "v0.0.3-rc", "v0.0.4-rc", "vnext")


def test_the_names_are_the_refs_the_host_matched_in_its_one_response() -> None:
    """``vnext`` is among them: the prefix is a string prefix, and which names are version tags
    is the domain's to decide."""
    host = answering(TAG_REFS, "tag-refs")

    assert reader_on(host).tag_names("") == FIXTURE_NAMES

    (request,) = host.requests
    assert request.method == "GET"
    assert request.url.query == b""
    assert "link" not in replay("tag-refs").headers


def test_a_prefix_with_no_match_is_no_name() -> None:
    host = answering(TAG_REFS_NONE, "tag-refs-none")

    assert reader_on(host).tag_names("fixture/") == ()


def test_the_names_of_lightweight_tags_are_the_same_as_any() -> None:
    host = answering(GORA_TAG_REFS, "gora-tag-refs")

    names = reader_on(host, GORA).tag_names("")

    assert names == tuple(f"v0.2.0-rc.{n}" for n in range(1, 7))


@pytest.mark.parametrize("prefix", ["api", "API/", "a b/", "/", "a/b/", "../"])
def test_a_tag_prefix_outside_its_grammar_is_a_caller_s_error_and_asks_nothing(
    prefix: str,
) -> None:
    host = Host({})

    with pytest.raises(ValueError, match="tag prefix"):
        reader_on(host).tag_names(prefix)

    assert host.requests == []


def test_refs_outside_the_prefix_asked_are_refused() -> None:
    """The host answers the refs under ``v``, and the reader asked for those under
    ``fixture/v``."""
    host = answering(f"{REPO_PATH}/git/matching-refs/tags/fixture/v", "tag-refs")

    with pytest.raises(GitHubError, match=reader.OUTSIDE_THE_PREFIX):
        reader_on(host).tag_names("fixture/")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (setting(1, "ref", to="refs/tags/w0.0.2-rc"), reader.OUTSIDE_THE_PREFIX),
        (setting(1, "ref", to="refs/heads/v0.0.2-rc"), reader.OUTSIDE_THE_PREFIX),
        (setting(1, "ref", to=2), reader.OUTSIDE_THE_PREFIX),
        (removing(1, "ref"), None),
        (setting(1, to="refs/tags/v0.0.2-rc"), reader.NOT_AN_OBJECT),
        (lambda refs: refs[0], reader.NOT_A_LIST),
    ],
    ids=[
        "another-prefix",
        "not-a-tag-ref",
        "a-ref-that-is-no-string",
        "no-ref",
        "an-item-that-is-no-object",
        "not-a-list",
    ],
)
def test_one_ref_that_is_not_what_was_asked_refuses_the_answer(
    change: Callable[[Any], Any], reason: str | None
) -> None:
    host = answering(TAG_REFS, "tag-refs", change=change)

    with pytest.raises(GitHubError, match=reason or "unexpected response"):
        reader_on(host).tag_names("")


@pytest.mark.parametrize(
    "change",
    [
        setting(1, "object", to=None),
        setting(1, "object", "type", to="blob"),
        setting(1, "object", "sha", to="abc123"),
        removing(1, "object"),
        setting(1, "url", to=7),
    ],
    ids=["no-object", "a-blob", "a-short-sha", "object-missing", "another-field"],
)
def test_nothing_of_a_ref_but_its_name_is_read(change: Callable[[Any], Any]) -> None:
    """A name is all the port gives, and ``tag`` checks the rest of the ref it asks for: a
    field of an item that is not its ``ref`` is not looked at, so it cannot refuse the list."""
    host = answering(TAG_REFS, "tag-refs", change=change)

    assert reader_on(host).tag_names("") == FIXTURE_NAMES


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
        reader_on(host).tag_names("")

    assert len(host.requests) == 1


# --- tag -------------------------------------------------------------------------------------


def annotated(change: Callable[[Any], Any] | None = None) -> Host:
    """A host that holds the annotated tag ``v0.0.1-rc``: its ref, then its tag object, the
    second with ``change`` as a defect made on the recorded answer."""
    return Host(
        {
            ref_tag_path("v0.0.1-rc"): lambda: replay("ref-tag-v0.0.1-rc"),
            tag_path(RC1): lambda: replay("tag-v0.0.1-rc", change=change),
        }
    )


def test_a_lightweight_tag_is_its_commit_and_no_message_after_one_request() -> None:
    host = answering(ref_tag_path("v0.0.4-rc"), "ref-tag-v0.0.4-rc")

    assert reader_on(host).tag("v0.0.4-rc") == Tag("v0.0.4-rc", COMMIT, None)

    (request,) = host.requests
    assert request.method == "GET"
    assert request.url.query == b""


def test_an_annotated_tag_is_its_commit_and_its_message_after_two_requests() -> None:
    host = annotated()
    message = json.loads(replay("tag-v0.0.1-rc").content)["message"]

    assert reader_on(host).tag("v0.0.1-rc") == Tag("v0.0.1-rc", COMMIT, message)

    assert [request.url.path for request in host.requests] == [
        ref_tag_path("v0.0.1-rc"),
        tag_path(RC1),
    ]
    assert {(request.method, request.url.query) for request in host.requests} == {("GET", b"")}


def test_the_message_of_the_recorded_candidate_tag_is_one_the_domain_reads_back() -> None:
    """The fixture's first tag carries the message ``rc_tag_message`` wrote, and the domain
    reads its trailer back from the bytes the host kept."""
    tag = reader_on(annotated()).tag("v0.0.1-rc")

    assert tag.message is not None
    candidate = recorded(tag.name, tag.message, artifact="fixture", tag_prefix="")
    assert str(candidate.version) == "0.0.1-rc"
    assert candidate.digest == "sha256:" + "a" * 64


def test_the_message_of_a_tag_with_no_trailer_is_returned_and_the_domain_refuses_it() -> None:
    """``v0.0.2-rc`` has no recorded ref: its answer is the first tag's with its name and sha
    changed, which is what the host sends for any annotated tag."""

    def ref_of_the_second(data: Any) -> Any:
        data = setting("ref", to="refs/tags/v0.0.2-rc")(data)
        return setting("object", "sha", to=RC2)(data)

    host = Host(
        {
            ref_tag_path("v0.0.2-rc"): lambda: replay(
                "ref-tag-v0.0.1-rc", change=ref_of_the_second
            ),
            tag_path(RC2): lambda: replay("tag-v0.0.2-rc"),
        }
    )

    tag = reader_on(host).tag("v0.0.2-rc")

    assert tag.message == "fixture 0.0.2-rc\n\nAn annotated tag with no trailer.\n"
    with pytest.raises(RecordError):
        recorded(tag.name, tag.message, artifact="fixture", tag_prefix="")


def test_a_tag_of_a_tag_is_refused_at_the_second_answer() -> None:
    """``v0.0.3-rc`` tags ``v0.0.1-rc``'s tag object: following the chain would be an unbounded
    number of requests, and GORA tags commits only."""
    host = Host(
        {
            ref_tag_path("v0.0.3-rc"): lambda: replay("ref-tag-v0.0.3-rc"),
            tag_path(RC3): lambda: replay("tag-v0.0.3-rc"),
        }
    )

    with pytest.raises(GitHubError, match=reader.TAG_OF_A_TAG):
        reader_on(host).tag("v0.0.3-rc")

    assert [request.url.path for request in host.requests] == [
        ref_tag_path("v0.0.3-rc"),
        tag_path(RC3),
    ]


LONGEST_VERSION_TAG = "a" * 63 + "/v1.0.0-" + "a" * 122


@pytest.mark.parametrize(
    "name",
    [
        "",
        "v",
        "a/b/v1.0.0",
        "../v1.0.0",
        "API/v1.0.0",
        "v1.0.0-rc.lock",
        "vnext",
        "v1.0.0\n",
        LONGEST_VERSION_TAG + "a",
    ],
    ids=[
        "empty",
        "v-alone",
        "two-prefixes",
        "dot-dot",
        "upper-case-prefix",
        "ending-in-lock",
        "no-version",
        "a-trailing-new-line",
        "194-characters",
    ],
)
def test_a_name_that_is_not_a_version_tag_is_a_caller_s_error_and_asks_nothing(name: str) -> None:
    host = Host({})

    with pytest.raises(ValueError, match="the name is a version tag"):
        reader_on(host).tag(name)

    assert host.requests == []


def test_a_name_of_193_characters_is_asked_for() -> None:
    """Calibration: the longest version tag is a tag prefix of 64 characters, ``v`` and a
    version of 128, and the bound refuses one more than that, and only that."""
    assert len(LONGEST_VERSION_TAG) == 193
    host = Host(
        {
            ref_tag_path(LONGEST_VERSION_TAG): lambda: replay(
                "ref-tag-v0.0.4-rc", change=setting("ref", to=f"refs/tags/{LONGEST_VERSION_TAG}")
            )
        }
    )

    assert reader_on(host).tag(LONGEST_VERSION_TAG).name == LONGEST_VERSION_TAG


@pytest.mark.parametrize(
    "ref",
    [
        "refs/tags/v0.0.4-rc.1",
        "refs/tags/v0.0.4",
        "refs/tags/V0.0.4-rc",
        "refs/tags/v0.0.1-rc",
        "refs/heads/v0.0.4-rc",
        "v0.0.4-rc",
        "",
        2,
        None,
    ],
    ids=[
        "a-longer-name",
        "a-shorter-name",
        "another-case",
        "another-tag",
        "a-branch",
        "no-namespace",
        "empty",
        "not-a-string",
        "null",
    ],
)
def test_a_ref_that_is_not_the_one_asked_is_refused(ref: object) -> None:
    host = answering(ref_tag_path("v0.0.4-rc"), "ref-tag-v0.0.4-rc", change=setting("ref", to=ref))

    with pytest.raises(GitHubError, match=reader.NOT_THE_REF):
        reader_on(host).tag("v0.0.4-rc")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (setting("object", "type", to="blob"), reader.NOT_A_TAG_OR_COMMIT),
        (setting("object", "type", to="tree"), reader.NOT_A_TAG_OR_COMMIT),
        (setting("object", "type", to=["commit"]), reader.NOT_A_TAG_OR_COMMIT),
        (setting("object", "sha", to="abc123"), reader.NOT_AN_OBJECT_NAME),
        (setting("object", "sha", to=COMMIT.upper()), reader.NOT_AN_OBJECT_NAME),
        (setting("object", "sha", to=7), reader.NOT_AN_OBJECT_NAME),
        (setting("object", to="a string"), reader.NOT_AN_OBJECT),
    ],
    ids=[
        "a-blob",
        "a-tree",
        "a-type-that-is-no-string",
        "a-short-sha",
        "upper-case",
        "no-string",
        "no-object",
    ],
)
def test_an_object_that_is_not_a_commit_or_a_tag_with_a_name_is_refused(
    change: Callable[[Any], Any], reason: str
) -> None:
    host = answering(ref_tag_path("v0.0.4-rc"), "ref-tag-v0.0.4-rc", change=change)

    with pytest.raises(GitHubError, match=reason):
        reader_on(host).tag("v0.0.4-rc")

    assert len(host.requests) == 1


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (setting("sha", to=OTHER), reader.NOT_THE_REFS_SHA),
        (setting("sha", to=7), reader.NOT_THE_REFS_SHA),
        (setting("tag", to="v0.0.2-rc"), reader.NOT_THE_REFS_NAME),
        (setting("tag", to="fixture/v0.0.1-rc"), reader.NOT_THE_REFS_NAME),
        (setting("tag", to=7), reader.NOT_THE_REFS_NAME),
        (setting("object", "type", to="tag"), reader.TAG_OF_A_TAG),
        (setting("object", "type", to="blob"), reader.NOT_A_COMMIT),
        (setting("object", "type", to=None), reader.NOT_A_COMMIT),
        (setting("object", "sha", to="abc123"), reader.NOT_AN_OBJECT_NAME),
        (setting("object", to="a string"), reader.NOT_AN_OBJECT),
        (setting("message", to=7), reader.NOT_A_MESSAGE),
        (setting("message", to=None), reader.NOT_A_MESSAGE),
    ],
    ids=[
        "another-sha",
        "a-sha-that-is-no-string",
        "another-name",
        "another-prefix",
        "a-name-that-is-no-string",
        "a-target-of-type-tag",
        "an-unknown-object-type",
        "no-object-type",
        "a-short-target",
        "no-target",
        "no-message",
        "a-null-message",
    ],
)
def test_an_annotation_that_is_not_the_refs_is_refused(
    change: Callable[[Any], Any], reason: str
) -> None:
    host = annotated(change)

    with pytest.raises(GitHubError, match=reason):
        reader_on(host).tag("v0.0.1-rc")

    assert len(host.requests) == 2


@pytest.mark.parametrize(
    ("name", "recorded_as"),
    [("v0.0.1", "ref-tag-v0.0.1"), ("v9.9.9-rc", "ref-tag-unknown")],
    ids=["a-prefix-of-an-existing-tag", "no-such-tag"],
)
def test_a_tag_the_host_does_not_hold_is_its_404_and_never_a_value(
    name: str, recorded_as: str
) -> None:
    """GitHub answers 404 for a tag that is not there and for a repository the token cannot
    see, so a 404 is no fact about the tag. ``v0.0.1`` is a prefix of ``v0.0.1-rc``, and the
    endpoint does not match it: the recorded answer is the 404."""
    host = answering(ref_tag_path(name), recorded_as)

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag(name)

    assert str(info.value) == f"GET {ref_tag_path(name)} -> 404: Not Found"
    assert len(host.requests) == 1


def test_an_annotation_the_host_does_not_hold_is_its_404() -> None:
    host = Host(
        {
            ref_tag_path("v0.0.1-rc"): lambda: replay("ref-tag-v0.0.1-rc"),
            tag_path(RC1): lambda: replay("tag-unknown"),
        }
    )

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag("v0.0.1-rc")

    assert str(info.value) == f"GET {tag_path(RC1)} -> 404: Not Found"


MARKER = "sentinel-message-4c1e"
# Controls, a bidi override, a lone surrogate, text outside ASCII, an astral character and a
# backslash, built from code points: JSON carries each of them as the host sent it.
AWKWARD = (
    f"fixture 0.0.1-rc\n\n{MARKER}{chr(0)}{chr(27)}[2J\r{chr(0x202E)}{chr(0x85)}{chr(0xD800)}"
    f" {chr(0xE9)}{chr(0x4E2D)}{chr(0x1F600)}\t{chr(92)}\n"
)


def test_a_message_the_host_sends_reaches_the_tag_as_it_is() -> None:
    """The message is the host's text: no refusal of the reader holds it, so the ``Tag`` has it
    as the host sent it, and the domain, which reads only a trailer, refuses it with a reason
    that holds none of it."""
    host = annotated(setting("message", to=AWKWARD))

    tag = reader_on(host).tag("v0.0.1-rc")

    assert tag.message == AWKWARD
    with pytest.raises(RecordError) as info:
        recorded(tag.name, tag.message, artifact="fixture", tag_prefix="")
    assert MARKER not in str(info.value)
    assert MARKER not in repr(info.value)


@pytest.mark.parametrize(
    "defect",
    [
        setting("sha", to=OTHER),
        setting("tag", to="v0.0.2-rc"),
        setting("object", "type", to="tag"),
        setting("object", "sha", to="abc123"),
    ],
    ids=["another-sha", "another-name", "a-tag-of-a-tag", "a-short-target"],
)
def test_the_message_of_a_refused_tag_is_in_no_error_text(defect: Callable[[Any], Any]) -> None:
    def both(data: Any) -> Any:
        return defect(setting("message", to=AWKWARD)(data))

    host = annotated(both)

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag("v0.0.1-rc")

    assert MARKER not in str(info.value)
    assert MARKER not in repr(info.value)
    assert isinstance(info.value.__cause__, egress.UnexpectedError)
    assert MARKER not in str(info.value.__cause__)


def test_a_tag_the_domain_refuses_is_an_answer_that_is_not_the_expected_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``Tag`` is built inside ``read_field``: its ``ValueError`` leaves as a
    ``GitHubError`` that names the operation and no value, as any answer that is not the one
    expected does."""

    def refused(name: str, commit: str, message: str | None) -> Tag:
        raise ValueError(f"refused {name} {commit} {message}")

    monkeypatch.setattr(reader, "Tag", refused)

    for host, name in (
        (answering(ref_tag_path("v0.0.4-rc"), "ref-tag-v0.0.4-rc"), "v0.0.4-rc"),
        (annotated(), "v0.0.1-rc"),
    ):
        with pytest.raises(GitHubError, match="unexpected response") as info:
            reader_on(host).tag(name)

        assert str(info.value).endswith("failed: unexpected response")
        assert COMMIT not in str(info.value)


# --- the core: what every request is bound by ------------------------------------------------

RATE_LIMIT = {"X-RateLimit-Reset": "1794678624", "Retry-After": "60"}


def error_body(message: str) -> bytes:
    """An error body as GitHub sends one, with the ``documentation_url`` that is never shown."""
    return json.dumps(
        {"message": message, "documentation_url": "https://docs.github.com/rest"}
    ).encode()


@pytest.mark.parametrize("status", [403, 429])
def test_a_rate_limit_names_the_status_and_the_two_headers(status: int) -> None:
    host = Host(
        {REPO_PATH: lambda: httpx2.Response(status, headers=RATE_LIMIT, content=error_body("slow"))}
    )

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
    host = Host(
        {REPO_PATH: lambda: httpx2.Response(429, headers=headers, content=error_body("slow"))}
    )

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} -> 429: slow{shown}"


def test_another_error_status_shows_no_header() -> None:
    host = Host(
        {REPO_PATH: lambda: httpx2.Response(500, headers=RATE_LIMIT, content=error_body("boom"))}
    )

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} -> 500: boom"


def test_the_host_s_text_of_an_error_is_its_message_and_nothing_else_of_the_body() -> None:
    """A 404 shows ``Not Found``: the recorded answer's ``message``, and not its other fields."""
    host = answering(ref_tag_path("v9.9.9-rc"), "ref-tag-unknown")

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag("v9.9.9-rc")

    assert str(info.value) == f"GET {ref_tag_path('v9.9.9-rc')} -> 404: Not Found"
    assert "docs.github.com" not in str(info.value)


def test_a_message_of_exactly_200_characters_is_shown() -> None:
    """Calibration: the limit refuses one character more, and only that."""
    host = Host({REPO_PATH: lambda: httpx2.Response(500, content=error_body("x" * 200))})

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} -> 500: {'x' * 200}"


ESCAPE = chr(27)

UNSAFE_BODIES = {
    "a-control-character": error_body(f"Not{ESCAPE}[2J Found"),
    "a-new-line": error_body("Not\nFound"),
    "a-tab": error_body("Not\tFound"),
    "a-delete": error_body("Not" + chr(127) + "Found"),
    "over-200-characters": error_body("x" * 201),
    "not-ascii": error_body("Not Found" + chr(0xE9)),
    "an-empty-message": error_body(""),
    "not-json": f"<html>{ESCAPE}[2J not json</html>".encode(),
    "empty": b"",
    "a-list": json.dumps(["Not Found"]).encode(),
    "no-message": json.dumps({"error": "Not Found"}).encode(),
    "a-message-that-is-no-string": json.dumps({"message": ["Not Found"]}).encode(),
    "nested-past-what-the-parser-reads": b"[" * 100_000,
    "bytes-that-are-no-text": bytes(range(128, 256)),
}


@pytest.mark.parametrize("body", list(UNSAFE_BODIES))
def test_a_body_that_is_not_safe_to_show_shows_nothing_of_it(body: str) -> None:
    host = Host({REPO_PATH: lambda: httpx2.Response(500, content=UNSAFE_BODIES[body])})

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} -> 500"


@pytest.mark.parametrize("status", [403, 429, 500, 502])
def test_nothing_is_retried(status: int) -> None:
    host = Host({REPO_PATH: lambda: httpx2.Response(status, headers=RATE_LIMIT)})

    with pytest.raises(GitHubError):
        reader_on(host).repository_id()

    assert len(host.requests) == 1


def test_a_transport_error_is_not_retried_either() -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        raise httpx2.ConnectError("unreachable", request=request)

    with pytest.raises(GitHubError):
        reader_on(handler).repository_id()

    assert len(calls) == 1


def test_a_response_read_before_the_core_sees_it_is_a_github_error_not_a_body() -> None:
    """A transport returns an unread stream. A response built with ``content=`` or ``json=`` has
    been read, and the core reads a stream, nothing else: it does not take the body such a
    response holds, which no real transport returns and which would be read whole, before the
    cap and outside the deadline."""
    transport = httpx2.MockTransport(lambda request: replay("repo"))

    with pytest.raises(GitHubError) as info:
        GitHubReader(FIXTURES, SENTINEL, transport=transport).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} failed: StreamConsumed"
    assert isinstance(info.value.__cause__, httpx2.StreamError)


@pytest.mark.parametrize(
    ("spoil", "error"),
    [
        (lambda response: response.read(), "StreamConsumed"),
        (lambda response: next(response.iter_raw()), "StreamConsumed"),
        (lambda response: response.close(), "StreamClosed"),
    ],
    ids=["reads-the-body", "reads-part-of-it", "closes-it"],
)
def test_a_stream_the_transport_handed_over_read_or_closed_is_a_github_error(
    spoil: Callable[[httpx2.Response], object], error: str
) -> None:
    """``httpx2.StreamError`` is no ``httpx2.HTTPError``: it left ``send`` as itself. No real
    transport hands over such a stream; a hook on the client that read or closed it would have
    left it so, and the client GORA builds has no hook, so only the seam reaches this."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        response = httpx2.Response(200, stream=Chunks([b"{}"]))
        spoil(response)
        return response

    with pytest.raises(GitHubError) as info:
        GitHubReader(FIXTURES, SENTINEL, transport=httpx2.MockTransport(handler)).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} failed: {error}"
    assert isinstance(info.value.__cause__, httpx2.StreamError)
    assert SENTINEL not in str(info.value)


def pulled_at_most(chunks: int) -> Callable[[], None]:
    """A callback for ``Chunks`` that fails the test when it is called more than ``chunks``
    times: the chunk after the last one the core may pull is a body read past its limit."""
    pulled = 0

    def each() -> None:
        nonlocal pulled
        pulled += 1
        if pulled > chunks:
            raise AssertionError("the body was read past its limit")

    return each


def test_a_body_over_the_cap_is_refused_never_read_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core stops at the chunk that crosses the cap: the one after it is never pulled, so a
    core that reads the whole body first and counts it once afterwards is red."""
    monkeypatch.setattr(egress, "MAX_BODY", 1000)
    chunks = [b"[" * 600, b"]" * 600, b"x"]
    host = Host({TAG_REFS: lambda: httpx2.Response(200, stream=Chunks(chunks, pulled_at_most(2)))})

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag_names("")

    assert str(info.value) == f"GET {TAG_REFS} failed: the body is over the cap of 1000 bytes"


def test_a_body_the_host_declares_over_the_cap_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(egress, "MAX_BODY", 1000)

    def never() -> None:
        raise AssertionError("the body was read")

    host = Host(
        {
            TAG_REFS: lambda: httpx2.Response(
                200, headers={"Content-Length": "1001"}, stream=Chunks([b"[]"], never)
            )
        }
    )

    with pytest.raises(GitHubError, match="over the cap of 1000 bytes"):
        reader_on(host).tag_names("")


def test_a_body_of_exactly_the_cap_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calibration: the cap refuses one byte more, and only that."""
    monkeypatch.setattr(egress, "MAX_BODY", 1000)
    body = b"[]" + b" " * 998
    host = Host({TAG_REFS: lambda: httpx2.Response(200, stream=Chunks([body[:500], body[500:]]))})

    assert reader_on(host).tag_names("") == ()


EMPTY_DEFLATE_BLOCK = bytes([0, 0, 0, 255, 255])


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "zstd", "Gzip", "gzip, identity"])
def test_an_encoded_body_is_refused_before_it_is_read(encoding: str) -> None:
    """A stream of empty deflate blocks decodes to no byte at all, so decoding would count
    against neither the cap nor the deadline: it never ends. Nothing is decoded, and the
    refusal comes with the headers, before a byte of the body is read."""

    def never() -> None:
        raise AssertionError("the body was read")

    host = Host(
        {
            REPO_PATH: lambda: httpx2.Response(
                200,
                headers={"Content-Encoding": encoding},
                stream=Chunks([EMPTY_DEFLATE_BLOCK] * 1000, never),
            )
        }
    )

    with pytest.raises(GitHubError) as info:
        reader_on(host).repository_id()

    assert str(info.value) == f"GET {REPO_PATH} failed: the body is encoded"


def test_a_gzip_body_is_refused_not_decoded() -> None:
    host = Host(
        {
            REPO_PATH: lambda: httpx2.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=gzip.compress(replay("repo").content),
            )
        }
    )

    with pytest.raises(GitHubError, match="the body is encoded"):
        reader_on(host).repository_id()


@pytest.mark.parametrize("encoding", ["identity", "Identity", " identity "])
def test_a_body_that_declares_identity_is_read(encoding: str) -> None:
    """Calibration: what is refused is an encoding, and a plain body is read as it arrives."""
    host = Host(
        {
            REPO_PATH: lambda: httpx2.Response(
                200,
                headers={"Content-Encoding": encoding},
                stream=Chunks([replay("repo").content]),
            )
        }
    )

    assert reader_on(host).repository_id() == "1457970812"


def test_the_cap_counts_the_bytes_that_arrive_not_the_bytes_they_decode_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream of chunks that decode to nothing is refused by the cap on what is sent."""
    monkeypatch.setattr(egress, "MAX_BODY", 1000)
    chunks = [EMPTY_DEFLATE_BLOCK] * 201
    host = Host({TAG_REFS: lambda: httpx2.Response(200, stream=Chunks(chunks))})

    with pytest.raises(GitHubError, match="over the cap of 1000 bytes"):
        reader_on(host).tag_names("")


DEEP = b"[" * 200_000 + b"]" * 200_000


@pytest.mark.parametrize("operation", ["repository_id", "branch_head", "tag_names", "tag"])
def test_a_body_nested_past_what_the_parser_reads_is_a_github_error(operation: str) -> None:
    """About 400 KB, far under the cap: ``json.loads`` raises ``RecursionError`` on it."""
    host = Host({path: lambda: httpx2.Response(200, content=DEEP) for path in ALL_PATHS})

    with pytest.raises(GitHubError) as info:
        OPERATIONS[operation](reader_on(host))

    assert str(info.value).endswith("failed: unexpected response (the body is not JSON)")
    assert isinstance(info.value.__cause__, egress.UnexpectedError)
    assert SENTINEL not in str(info.value)


def test_a_body_that_is_not_utf_8_is_a_github_error() -> None:
    host = answering(REPO_PATH, "repo")
    host.routes[REPO_PATH] = lambda: httpx2.Response(
        200, content=b'{"id": "' + bytes([255]) + b'"}'
    )

    with pytest.raises(GitHubError, match=r"unexpected response \(the body is not JSON\)"):
        reader_on(host).repository_id()


class Clock:
    """A clock the test moves, in place of the one the core reads."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 0.0
        monkeypatch.setattr(egress, "_now", lambda: self.now)

    def advance(self, seconds: float) -> Callable[[], None]:
        def move() -> None:
            self.now += seconds

        return move


def test_an_answer_that_arrives_too_slowly_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core stops at the chunk at which the clock passes the deadline: the one after it is
    never pulled, so a core that reads the whole body first and checks the clock once
    afterwards is red."""
    clock = Clock(monkeypatch)
    slowly = [b"[", b" ", b"]", b"x"]
    guard, move = pulled_at_most(3), clock.advance(30)

    def each() -> None:
        guard()
        move()

    host = Host({TAG_REFS: lambda: httpx2.Response(200, stream=Chunks(slowly, each))})

    with pytest.raises(GitHubError) as info:
        reader_on(host).tag_names("")

    assert str(info.value) == f"GET {TAG_REFS} failed: no complete answer within 60 s"


def test_an_answer_that_ends_at_the_deadline_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calibration: the deadline refuses what is past it, and only that."""
    clock = Clock(monkeypatch)
    host = Host(
        {TAG_REFS: lambda: httpx2.Response(200, stream=Chunks([b"[", b"]"], clock.advance(30)))}
    )

    assert reader_on(host).tag_names("") == ()


def test_headers_that_arrive_too_late_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(monkeypatch)

    def handler(request: httpx2.Request) -> httpx2.Response:
        clock.advance(61)()
        return replay("repo")

    with pytest.raises(GitHubError, match="no complete answer within 60 s"):
        reader_on(handler).repository_id()


OPERATIONS: dict[str, Callable[[GitHubReader], object]] = {
    "repository_id": lambda r: r.repository_id(),
    "branch_head": lambda r: r.branch_head("main"),
    "tag_names": lambda r: r.tag_names(""),
    "tag": lambda r: r.tag("v0.0.1-rc"),
}
ALL_PATHS = [REPO_PATH, REF_MAIN, TAG_REFS, ref_tag_path("v0.0.1-rc"), tag_path(RC1)]


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize(
    "error",
    [httpx2.ConnectError, httpx2.ReadTimeout, httpx2.RemoteProtocolError, httpx2.TooManyRedirects],
    ids=lambda e: e.__name__,
)
def test_a_transport_error_becomes_github_error(
    operation: str, error: type[httpx2.RequestError]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error("[Errno 8] nodename nor servname provided", request=request)

    with pytest.raises(GitHubError) as info:
        OPERATIONS[operation](reader_on(handler))

    message = str(info.value)
    assert message.startswith("GET /repos/")  # names the operation
    assert message.endswith(f" failed: {error.__name__}")  # and nothing httpx2 wrote
    assert SENTINEL not in message
    assert isinstance(info.value.__cause__, httpx2.HTTPError)


@pytest.mark.parametrize("length", [3_000, 5_000, 70_000])
def test_a_branch_that_makes_a_path_over_the_cap_is_a_value_error_before_any_request(
    length: int,
) -> None:
    """A branch is the reader's only free-form value in a path. One of 70,000 characters made
    a URL httpx2 refuses (65,536) and was a ``GitHubError`` naming the operation with the whole
    path; one of 3,000 was sent. Now the cap on a path refuses both, as a caller's error, and
    the message names the cap and never the path."""
    called: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        called.append(request)
        return httpx2.Response(200)

    with pytest.raises(ValueError, match="over the cap of") as info:
        reader_on(handler).branch_head("a" * length)

    assert called == []
    assert len(str(info.value)) < 200
    assert "aaaa" not in str(info.value)
    assert SENTINEL not in str(info.value)


MALFORMED: dict[str, Callable[[], httpx2.Response]] = {
    "not-json": lambda: httpx2.Response(200, text="<html>not json</html>"),
    "wrong-object": lambda: httpx2.Response(200, json={"unexpected": "shape"}),
    "a-list": lambda: httpx2.Response(200, json=["a", "list"]),
    "list-of-wrong-objects": lambda: httpx2.Response(200, json=[{"no": "ref"}]),
    "nested-too-deep": lambda: httpx2.Response(200, content=DEEP),
    "not-utf-8": lambda: httpx2.Response(200, content=b'{"id": "' + bytes([255]) + b'"}'),
}


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize("body", list(MALFORMED))
def test_an_answer_that_is_not_the_expected_json_becomes_github_error(
    operation: str, body: str
) -> None:
    with pytest.raises(GitHubError, match="unexpected response") as info:
        OPERATIONS[operation](reader_on(lambda request: MALFORMED[body]()))

    assert SENTINEL not in str(info.value)


REFUSALS: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {
    "a-500": lambda request: httpx2.Response(500, content=error_body("boom")),
    "a-redirect": lambda request: replay("repo-renamed"),
    "a-rate-limit": lambda request: httpx2.Response(
        429, headers=RATE_LIMIT, content=error_body("slow")
    ),
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

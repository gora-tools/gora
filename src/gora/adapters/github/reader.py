"""GitHub reader — the state of a repository, read from the code host, with no write (ADR-006).

The state of a repository, the head of a branch, its tags and the tag objects GORA reads
back, comes from the code host; a clone only supplies content addressed by name. This is the
read side of the GitHub adapter, a class of its own so that no write method can be added to it
unseen: its operations are the four below, and a test holds the list.

- ``repository_id``: the host's id of the repository; ``full_name`` must be the repository
  asked, compared as GitHub does, without case, and ``id`` a positive integer of at most
  2**63 - 1, since GitHub's ids are 64-bit.
- ``branch_head``: the commit a branch points at; the ref must be the one asked, its object a
  commit named by a git object name.
- ``tag_refs``: the refs under ``refs/tags/<tag prefix>v``, from ``matching-refs``, which is
  not paginated: every match comes in one response (measured, ADR-006). A ``Link`` header
  means that has changed, and following it would be the code guessing, so it is refused. The
  prefix is a string prefix, so ``vnext`` is among the matches of ``v``: which names are rc
  tags is the domain's to decide, and the adapter returns what the host matched. A ref's
  object is a commit (a lightweight tag) or a tag (an annotated one), and nothing else.
- ``tag_object``: the annotated tag object of a ref ``tag_refs`` returned, which must be that
  ref's: its sha, its name. Its object must be a commit: a tag of a tag is refused, since
  following the chain would be an unbounded number of requests and GORA tags commits only.

Results are frozen dataclasses, validated where they are built, with object names by
``SOURCE_COMMIT``; no raw dictionary leaves the adapter. Every request goes through the
bounded request every adapter shares (``gora.adapters.egress``), configured for GitHub by the
core the writer shares (``_http``), with its bounds, to the one host that core names and over
the one client ``gora.adapters.egress`` builds: the reader takes no URL and no client, only,
for a test, a transport. Construction refuses a caller's error
with ``ValueError``: a repository the repository grammar refuses (ADR-003), or an empty token;
so does a call with a branch that is empty, has a part that is empty, ``.`` or ``..`` (it
would leave the path of its operation) or builds a path over ``egress.MAX_PATH``, a tag prefix
outside its grammar or a ref that is not annotated. Any other failure of a call, whatever the
host sent, is a ``GitHubError``, and the message of a refusal names the operation and the
rule, never a value the host sent.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx2

from gora.adapters.egress import UnexpectedError
from gora.adapters.github._http import (
    GitHubError,
    HttpCore,
    checked_repository,
    read_field,
    segment,
)
from gora.domain.release.identifiers import SOURCE_COMMIT
from gora.domain.release_repository.formats import TAG_PREFIX

NOT_THE_REPOSITORY = "full_name is not the repository asked"
NOT_AN_ID = "id is not a positive 64-bit integer"
MAX_ID = 2**63 - 1
NOT_THE_REF = "ref is not the one asked"
NOT_A_COMMIT = "the object is not a commit"
NOT_AN_OBJECT_NAME = "the object name is not a git object name"
NOT_AN_OBJECT = "the answer is not a JSON object"
NOT_A_LIST = "the answer is not a JSON list"
OUTSIDE_THE_PREFIX = "a ref is outside the prefix asked"
NOT_A_TAG_OR_COMMIT = "a ref's object is neither a commit nor a tag"
PAGED = "the answer has a Link header, and the endpoint returns every match in one response"
NOT_THE_REFS_SHA = "the tag object's sha is not the ref's"
NOT_THE_REFS_NAME = "the tag object's name is not the ref's"
TAG_OF_A_TAG = "the tag object points at a tag, and GORA tags commits only"
NOT_A_MESSAGE = "the tag object's message is not a string"

NO_BRANCH = "branch must not be empty"
NOT_ANNOTATED = "only an annotated tag has a tag object"


class TagKind(Enum):
    """What a tag ref points at: a commit directly, or a tag object."""

    LIGHTWEIGHT = "lightweight"
    ANNOTATED = "annotated"


@dataclass(frozen=True)
class TagRef:
    """A tag as the host lists it: its name without ``refs/tags/``, its object name, its kind."""

    name: str
    object_name: str
    kind: TagKind

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tag name must not be empty")
        if not SOURCE_COMMIT.is_valid(self.object_name):
            raise ValueError(f"object name {SOURCE_COMMIT.rule}")


@dataclass(frozen=True)
class TagObject:
    """An annotated tag object: its name, its own object name, the commit it tags, its message."""

    name: str
    object_name: str
    target: str
    message: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tag name must not be empty")
        for what, value in (("object name", self.object_name), ("target", self.target)):
            if not SOURCE_COMMIT.is_valid(value):
                raise ValueError(f"{what} {SOURCE_COMMIT.rule}")


_KINDS = {"commit": TagKind.LIGHTWEIGHT, "tag": TagKind.ANNOTATED}


class GitHubReader:
    """The four reads of the repository's state, and no write."""

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        timeout: float = 10.0,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self._repo = checked_repository(repo)
        self._core = HttpCore(token, timeout=timeout, transport=transport)

    def repository_id(self) -> int:
        """The host's id of the repository, which is its identity: a name can be moved."""
        path = f"/repos/{self._repo}"
        data = self._core.json_object("GET", path)
        return read_field(f"GET {path}", lambda: self._id(data))

    def branch_head(self, branch: str) -> str:
        """The commit a branch points at, by its object name."""
        if not branch:
            raise ValueError(NO_BRANCH)
        path = f"/repos/{self._repo}/git/ref/heads/{segment(branch)}"
        data = self._core.json_object("GET", path)
        return read_field(f"GET {path}", lambda: self._head(data, f"refs/heads/{branch}"))

    def tag_refs(self, tag_prefix: str) -> tuple[TagRef, ...]:
        """Every ref under ``refs/tags/<tag_prefix>v``, as the host matched them, in its order."""
        if not TAG_PREFIX.is_valid(tag_prefix):
            raise ValueError(f"tag prefix {TAG_PREFIX.rule}")
        asked = f"refs/tags/{tag_prefix}v"
        # The grammar admits lower-case letters, digits, - and a closing /: nothing to encode.
        path = f"/repos/{self._repo}/git/matching-refs/tags/{tag_prefix}v"
        operation = f"GET {path}"
        answer = self._core.send("GET", path)
        if "link" in answer.headers:
            raise GitHubError(f"{operation} failed: unexpected response ({PAGED})")
        items = read_field(operation, answer.json)
        return read_field(operation, lambda: self._refs(items, asked))

    def tag_object(self, ref: TagRef) -> TagObject:
        """The annotated tag object of ``ref``, a ref that ``tag_refs`` returned."""
        if ref.kind is not TagKind.ANNOTATED:
            raise ValueError(NOT_ANNOTATED)
        path = f"/repos/{self._repo}/git/tags/{ref.object_name}"
        data = self._core.json_object("GET", path)
        return read_field(f"GET {path}", lambda: self._tag_object(data, ref))

    # --- what the host sent, checked ---

    def _id(self, data: dict[str, Any]) -> int:
        name = data["full_name"]
        # ASCII only: lower-casing is not the identity on every character (U+212A lowers to k).
        if not (isinstance(name, str) and name.isascii() and name.lower() == self._repo.lower()):
            raise UnexpectedError(NOT_THE_REPOSITORY)
        identity = data["id"]
        if (
            not isinstance(identity, int)
            or isinstance(identity, bool)
            or not 0 < identity <= MAX_ID
        ):
            raise UnexpectedError(NOT_AN_ID)
        return identity

    @staticmethod
    def _head(data: dict[str, Any], asked: str) -> str:
        if data["ref"] != asked:
            raise UnexpectedError(NOT_THE_REF)
        return _object_name(data["object"], must_be="commit")

    @staticmethod
    def _refs(items: Any, asked: str) -> tuple[TagRef, ...]:
        if not isinstance(items, list):
            raise UnexpectedError(NOT_A_LIST)
        refs: list[TagRef] = []
        for item in items:
            ref = item["ref"]
            if not (isinstance(ref, str) and ref.startswith(asked)):
                raise UnexpectedError(OUTSIDE_THE_PREFIX)
            kind = _kind(item["object"])
            refs.append(TagRef(ref.removeprefix("refs/tags/"), _object_name(item["object"]), kind))
        return tuple(refs)

    @staticmethod
    def _tag_object(data: dict[str, Any], ref: TagRef) -> TagObject:
        if data["sha"] != ref.object_name:
            raise UnexpectedError(NOT_THE_REFS_SHA)
        if data["tag"] != ref.name:
            raise UnexpectedError(NOT_THE_REFS_NAME)
        message = data["message"]
        if not isinstance(message, str):
            raise UnexpectedError(NOT_A_MESSAGE)
        target = data["object"]
        if isinstance(target, dict) and target.get("type") == "tag":
            raise UnexpectedError(TAG_OF_A_TAG)
        return TagObject(ref.name, ref.object_name, _object_name(target, must_be="commit"), message)


def _kind(obj: Any) -> TagKind:
    if not isinstance(obj, dict):
        raise UnexpectedError(NOT_AN_OBJECT)
    kind = obj["type"]
    if not isinstance(kind, str) or kind not in _KINDS:
        raise UnexpectedError(NOT_A_TAG_OR_COMMIT)
    return _KINDS[kind]


def _object_name(obj: Any, *, must_be: str | None = None) -> str:
    """The object name of a ref's ``object``, which must be a commit when ``must_be`` says."""
    if not isinstance(obj, dict):
        raise UnexpectedError(NOT_AN_OBJECT)
    if must_be is not None and obj["type"] != must_be:
        raise UnexpectedError(NOT_A_COMMIT)
    sha = obj["sha"]
    if not (isinstance(sha, str) and SOURCE_COMMIT.is_valid(sha)):
        raise UnexpectedError(NOT_AN_OBJECT_NAME)
    return sha

"""A candidate's rc tag: its name, its message and the record read back (ADR-022 section 6,
ADR-024 section 1, ADR-027 sections 1 and 4).

What is held: the rc tag of an artifact is its tag prefix, ``v`` and the rc version; its
message names the artifact, the version, the image with the digest and the policy digest,
carries the change log and ends with the trailer ``Gora-candidate: <artifact> <version>
<digest>``; GORA writes it byte by byte, with no line that begins with ``#``, ends with white
space or is a second blank line in a row, so it does not depend on git's cleanup of a message;
what is read back is the trailer alone, from the last line after a blank line, and only when it
names the artifact and the version the tag's name names.

Expectations are written out literally, never derived from the code under test. ``c(n)`` is
the object name whose 40 digits are all the hexadecimal digit ``n``.
"""

import itertools

import pytest

from gora.domain.release import tags
from gora.domain.release.candidates import Candidate, Commit
from gora.domain.release.identifiers import DIGEST, IMAGE, NAME, SOURCE_COMMIT
from gora.domain.release.tags import (
    ANOTHER,
    FIELDS,
    FINAL,
    NO_CHANGE,
    NO_TRAILER,
    NOT_THE_TAG,
    PREFIX,
    Recorded,
    RecordError,
    rc_tag,
    rc_tag_message,
    recorded,
)
from gora.domain.release.versions import Version
from gora.domain.release_repository.formats import TAG_PREFIX

D = "sha256:" + "ab" * 32
P = "sha256:" + "cd" * 32
E = "sha256:" + "ef" * 32
IMG = "ghcr.io/gora-tools/gora"


def c(n: int) -> str:
    return f"{n:x}" * 40


def commit(n: int, message: str) -> Commit:
    return Commit(c(n), message, frozenset({b"src/a.py"}))


def v(text: str) -> Version:
    return Version.parse(text)


FIRST = Candidate(commit(1, "feat: first"), v("0.2.1-rc"), (commit(1, "feat: first"),))
LATER = Candidate(
    commit(5, "chore: release files only"),
    v("0.3.0-rc"),
    (commit(2, "feat(api): add retry"), commit(3, "fix: handle empty input"), commit(4, "x!: y")),
)

FIRST_MESSAGE = (
    "gora 0.2.1-rc\n"
    "\n"
    f"Image: ghcr.io/gora-tools/gora@{D}\n"
    f"Policy digest: {P}\n"
    "\n"
    "Features:\n"
    "- first 111111111111\n"
    "\n"
    f"Gora-candidate: gora 0.2.1-rc {D}\n"
)
LATER_MESSAGE = (
    "api 0.3.0-rc\n"
    "\n"
    f"Image: registry.example:5000/team/api@{E}\n"
    f"Policy digest: {P}\n"
    "\n"
    "Breaking changes:\n"
    "- y 444444444444\n"
    "\n"
    "Features:\n"
    "- add retry (api) 222222222222\n"
    "\n"
    "Fixes:\n"
    "- handle empty input 333333333333\n"
    "\n"
    f"Gora-candidate: api 0.3.0-rc {E}\n"
)


# --- the name ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "version", "expected"),
    [
        pytest.param("", "1.2.0-rc", "v1.2.0-rc", id="no-prefix"),
        pytest.param("api/", "0.0.1-rc", "api/v0.0.1-rc", id="prefix"),
    ],
)
def test_the_rc_tag_is_the_prefix_v_and_the_version(
    prefix: str, version: str, expected: str
) -> None:
    assert rc_tag(prefix, v(version)) == expected


@pytest.mark.parametrize(
    ("prefix", "version", "reason"),
    [
        pytest.param("api", "1.2.0-rc", f"tag prefix {TAG_PREFIX.rule}", id="no-slash"),
        pytest.param("Api/", "1.2.0-rc", f"tag prefix {TAG_PREFIX.rule}", id="upper-case"),
        pytest.param("/", "1.2.0-rc", f"tag prefix {TAG_PREFIX.rule}", id="slash-only"),
        pytest.param("api/", "1.2.0", FINAL, id="final-name"),
    ],
)
def test_a_bad_prefix_or_a_final_version_has_no_rc_tag(
    prefix: str, version: str, reason: str
) -> None:
    with pytest.raises(ValueError) as raised:
        rc_tag(prefix, v(version))
    assert str(raised.value) == reason


# --- the message ---------------------------------------------------------------------------------


def test_the_first_candidate_s_message() -> None:
    """The first candidate's change log is its own commit (ADR-027 section 4)."""
    message = rc_tag_message(FIRST, artifact="gora", image=IMG, digest=D, policy_digest=P)
    assert message == FIRST_MESSAGE


def test_a_later_candidate_s_message_lists_its_changes_not_its_own_commit() -> None:
    """A candidate that carries the changes of others lists them; its own commit, which
    changed only release files, is not among its changes (``release-only-carries`` of
    ``test_candidates``)."""
    message = rc_tag_message(
        LATER, artifact="api", image="registry.example:5000/team/api", digest=E, policy_digest=P
    )
    assert message == LATER_MESSAGE


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        pytest.param({"artifact": "Gora"}, f"artifact {NAME.rule}", id="artifact"),
        pytest.param({"image": "ghcr.io/gora-tools/gora:1"}, f"image {IMAGE.rule}", id="image"),
        pytest.param({"image": "gora"}, f"image {IMAGE.rule}", id="image-no-registry"),
        pytest.param({"digest": "sha256:" + "AB" * 32}, f"digest {DIGEST.rule}", id="digest"),
        pytest.param({"policy_digest": "ab" * 32}, f"policy digest {DIGEST.rule}", id="policy"),
    ],
)
def test_a_field_outside_its_grammar_is_a_caller_s_error(
    change: dict[str, str], reason: str
) -> None:
    fields = {"artifact": "gora", "image": IMG, "digest": D, "policy_digest": P} | change
    with pytest.raises(ValueError) as raised:
        rc_tag_message(FIRST, **fields)
    assert str(raised.value) == reason


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        pytest.param(
            Candidate(FIRST.commit, v("0.2.1"), FIRST.changes), FINAL, id="final-candidate"
        ),
        pytest.param(Candidate(FIRST.commit, FIRST.version, ()), NO_CHANGE, id="no-change"),
        pytest.param(
            Candidate(FIRST.commit, FIRST.version, (Commit("1" * 39, "fix: a", frozenset()),)),
            f"source commit {SOURCE_COMMIT.rule}",
            id="bad-commit",
        ),
    ],
)
def test_a_candidate_that_cannot_be_one_is_a_caller_s_error(
    candidate: Candidate, reason: str
) -> None:
    with pytest.raises(ValueError) as raised:
        rc_tag_message(candidate, artifact="gora", image=IMG, digest=D, policy_digest=P)
    assert str(raised.value) == reason


GOOD = {"artifact": "gora", "image": IMG, "digest": D, "policy_digest": P}
BAD = {"artifact": "Gora", "image": "gora", "digest": "sha256:x", "policy_digest": "x"}
WORST = Candidate(FIRST.commit, v("0.2.1"), ())
BAD_COMMIT = Candidate(FIRST.commit, FIRST.version, (Commit("1" * 39, "fix: a", frozenset()),))


@pytest.mark.parametrize(
    ("good", "candidate", "reason"),
    [
        pytest.param((), WORST, f"artifact {NAME.rule}", id="order-1-artifact"),
        pytest.param(("artifact",), WORST, f"image {IMAGE.rule}", id="order-2-image"),
        pytest.param(("artifact", "image"), WORST, f"digest {DIGEST.rule}", id="order-3-digest"),
        pytest.param(
            ("artifact", "image", "digest"),
            WORST,
            f"policy digest {DIGEST.rule}",
            id="order-4-policy-digest",
        ),
        pytest.param(tuple(GOOD), WORST, FINAL, id="order-5-final"),
        pytest.param(
            tuple(GOOD), Candidate(FIRST.commit, FIRST.version, ()), NO_CHANGE, id="order-6-change"
        ),
        pytest.param(tuple(GOOD), BAD_COMMIT, f"source commit {SOURCE_COMMIT.rule}", id="order-7"),
    ],
)
def test_the_caller_s_errors_come_in_their_order(
    good: tuple[str, ...], candidate: Candidate, reason: str
) -> None:
    """Each row mends one more of what the row before got wrong."""
    fields = {name: GOOD[name] if name in good else BAD[name] for name in GOOD}
    with pytest.raises(ValueError) as raised:
        rc_tag_message(candidate, **fields)
    assert str(raised.value) == reason


AWKWARD = Candidate(
    commit(10, "chore: release files only"),
    v("1.0.0-rc"),
    (
        commit(6, "# not a heading"),
        commit(7, "fix: trailing  "),
        commit(8, ""),
        commit(9, "Gora-candidate: other 9.9.9-rc " + "sha256:" + "00" * 32),
        commit(11, "fix: control\x1b[0m"),
        commit(12, "x\r"),
        commit(13, "x\x00y"),
    ),
)
MESSAGES = [
    pytest.param(FIRST_MESSAGE, id="first"),
    pytest.param(LATER_MESSAGE, id="later"),
    pytest.param(
        rc_tag_message(AWKWARD, artifact="gora", image=IMG, digest=D, policy_digest=P),
        id="awkward",
    ),
]


@pytest.mark.parametrize("message", MESSAGES)
def test_the_message_does_not_depend_on_git_s_cleanup(message: str) -> None:
    """No line begins with ``#`` or ends with white space, no blank line follows another, and
    the message ends with exactly one line end: what ``git tag`` would strip is not there."""
    lines = message.split("\n")
    assert lines[-1] == ""
    body = lines[:-1]
    assert body[0] != ""
    assert body[-1] != ""
    assert not any(line.startswith("#") for line in body)
    assert not any(line != line.rstrip(" \t\r\v\f") for line in body)
    assert not any(a == b == "" for a, b in itertools.pairwise(body))


def test_the_awkward_message_as_written() -> None:
    """A subject that begins with ``#``, or reads as GORA's trailer, sits after ``- `` in the
    change log, and the trailer is still the last line."""
    message = rc_tag_message(AWKWARD, artifact="gora", image=IMG, digest=D, policy_digest=P)
    assert message == (
        "gora 1.0.0-rc\n"
        "\n"
        f"Image: ghcr.io/gora-tools/gora@{D}\n"
        f"Policy digest: {P}\n"
        "\n"
        "Fixes:\n"
        "- trailing   777777777777\n"
        "\n"
        "Other changes:\n"
        "- # not a heading 666666666666\n"
        "- 888888888888\n"
        f"- Gora-candidate: other 9.9.9-rc sha256:{'00' * 32} 999999999999\n"
        "- fix: control\x1b[0m bbbbbbbbbbbb\n"
        "- x\r cccccccccccc\n"
        "- x\ufffdy dddddddddddd\n"
        "\n"
        f"Gora-candidate: gora 1.0.0-rc {D}\n"
    )


# --- the record read back ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "message", "artifact", "prefix", "expected"),
    [
        pytest.param(
            "v0.2.1-rc", FIRST_MESSAGE, "gora", "", Recorded(v("0.2.1-rc"), D), id="read-first"
        ),
        pytest.param(
            "api/v0.3.0-rc",
            LATER_MESSAGE,
            "api",
            "api/",
            Recorded(v("0.3.0-rc"), E),
            id="read-prefix",
        ),
        pytest.param(
            "v0.2.1-rc",
            FIRST_MESSAGE.removesuffix("\n"),
            "gora",
            "",
            Recorded(v("0.2.1-rc"), D),
            id="no-final-line-end",
        ),
        pytest.param(
            "v0.2.1-rc",
            FIRST_MESSAGE.replace("Gora-candidate", "GORA-CANDIDATE"),
            "gora",
            "",
            Recorded(v("0.2.1-rc"), D),
            id="key-in-any-case",
        ),
        pytest.param(
            "v0.2.1-rc",
            FIRST_MESSAGE.replace("\n\nGora-candidate", "\n \t\nGora-candidate"),
            "gora",
            "",
            Recorded(v("0.2.1-rc"), D),
            id="blank-of-spaces",
        ),
        pytest.param(
            "v0.2.1-rc",
            f"Gora-candidate: gora 9.9.9-rc {E}\n\nGora-candidate: gora 0.2.1-rc {D}\n",
            "gora",
            "",
            Recorded(v("0.2.1-rc"), D),
            id="only-the-last-line",
        ),
    ],
)
def test_the_trailer_is_read_back(
    tag: str, message: str, artifact: str, prefix: str, expected: Recorded
) -> None:
    assert recorded(tag, message, artifact=artifact, tag_prefix=prefix) == expected


@pytest.mark.parametrize(
    ("message", "tag", "artifact", "prefix", "expected"),
    [
        pytest.param(
            FIRST_MESSAGE, "v0.2.1-rc", "gora", "", Recorded(v("0.2.1-rc"), D), id="first"
        ),
        pytest.param(
            LATER_MESSAGE, "api/v0.3.0-rc", "api", "api/", Recorded(v("0.3.0-rc"), E), id="later"
        ),
        pytest.param(
            rc_tag_message(AWKWARD, artifact="gora", image=IMG, digest=D, policy_digest=P),
            "v1.0.0-rc",
            "gora",
            "",
            Recorded(v("1.0.0-rc"), D),
            id="awkward",
        ),
    ],
)
def test_every_message_written_is_read_back(
    message: str, tag: str, artifact: str, prefix: str, expected: Recorded
) -> None:
    """A round trip: what ``rc_tag_message`` writes, ``recorded`` reads."""
    assert recorded(tag, message, artifact=artifact, tag_prefix=prefix) == expected


TRAILER = f"Gora-candidate: gora 0.2.1-rc {D}"


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        pytest.param("", NO_TRAILER, id="empty-message"),
        pytest.param(f"{TRAILER}\n", NO_TRAILER, id="no-line-before"),
        pytest.param(f"gora 0.2.1-rc\n{TRAILER}\n", NO_TRAILER, id="no-blank-before"),
        pytest.param(f"x\n\nSigned-off-by: a\n{TRAILER}\n", NO_TRAILER, id="two-trailers"),
        pytest.param(f"x\n\n{TRAILER}\nSigned-off-by: a\n", NO_TRAILER, id="not-last"),
        pytest.param(f"x\n\n{TRAILER}\n\n", NO_TRAILER, id="blank-after"),
        pytest.param(f"x\r\n\r\n{TRAILER}\r\n", NO_TRAILER, id="crlf-message"),
        pytest.param(f"x\n\nGora-candidate:gora 0.2.1-rc {D}\n", NO_TRAILER, id="no-space"),
        pytest.param(f"x\n\nGora-candidate : gora 0.2.1-rc {D}\n", NO_TRAILER, id="space-key"),
        pytest.param(f"x\n\nGora-promotion: gora 0.2.1-rc {D}\n", NO_TRAILER, id="other-key"),
        pytest.param("x\n\nGora-candidate: gora 0.2.1-rc\n", FIELDS, id="two-fields"),
        pytest.param(f"x\n\nGora-candidate: gora 0.2.1-rc {D} x\n", FIELDS, id="four-fields"),
        pytest.param(f"x\n\nGora-candidate: gora  0.2.1-rc {D}\n", FIELDS, id="two-spaces"),
        pytest.param(f"x\n\nGora-candidate: gora 0.2.1-rc {D} \n", FIELDS, id="trailing-space"),
        pytest.param(f"x\n\n{TRAILER}\r\n", FIELDS, id="cr-after-trailer"),
        pytest.param(f"x\n\nGora-candidate: Gora 0.2.1-rc {D}\n", FIELDS, id="bad-name"),
        pytest.param(f"x\n\nGora-candidate: gora 0.2.1 {D}\n", FIELDS, id="trailer-final"),
        pytest.param(f"x\n\nGora-candidate: gora v0.2.1-rc {D}\n", FIELDS, id="v-version"),
        pytest.param(f"x\n\nGora-candidate: gora 0.2.1-rc {D.upper()}\n", FIELDS, id="bad-digest"),
        pytest.param(f"x\n\nGora-candidate: api 0.2.1-rc {D}\n", ANOTHER, id="other-artifact"),
        pytest.param(f"x\n\nGora-candidate: gora 0.2.2-rc {D}\n", ANOTHER, id="other-version"),
    ],
)
def test_a_tag_that_does_not_record_its_candidate_is_a_record_error(
    message: str, reason: str
) -> None:
    with pytest.raises(RecordError) as raised:
        recorded("v0.2.1-rc", message, artifact="gora", tag_prefix="")
    assert not isinstance(raised.value, ValueError)
    assert raised.value.reason == reason
    assert str(raised.value) == reason


@pytest.mark.parametrize(
    ("tag", "artifact", "prefix", "reason"),
    [
        pytest.param("v0.2.1-rc", "Gora", "", f"artifact {NAME.rule}", id="reader-artifact"),
        pytest.param("api/v0.2.1-rc", "gora", "api/", PREFIX, id="another-s-prefix"),
        pytest.param("v0.2.1-rc", "gora", "gora", PREFIX, id="prefix-no-slash"),
        pytest.param("api/v0.2.1-rc", "gora", "", NOT_THE_TAG, id="prefixed-tag"),
        pytest.param("gora/v0.2.1-rc", "gora", "", NOT_THE_TAG, id="own-name-unprefixed"),
        pytest.param("v0.2.1-rc", "gora", "gora/", NOT_THE_TAG, id="missing-prefix"),
        pytest.param("v0.2.1", "gora", "", NOT_THE_TAG, id="final-tag"),
        pytest.param("v0.2.0-rc.1", "gora", "", NOT_THE_TAG, id="old-hand-tag"),
        pytest.param("0.2.1-rc", "gora", "", NOT_THE_TAG, id="no-v"),
        pytest.param("vv0.2.1-rc", "gora", "", NOT_THE_TAG, id="vv"),
    ],
)
def test_a_tag_that_is_not_the_artifact_s_rc_tag_is_a_caller_s_error(
    tag: str, artifact: str, prefix: str, reason: str
) -> None:
    """Checked before the message, so a bad message does not hide it."""
    with pytest.raises(ValueError) as raised:
        recorded(tag, "", artifact=artifact, tag_prefix=prefix)
    assert str(raised.value) == reason


def test_a_record_error_carries_no_value() -> None:
    """The reasons are fixed texts: none holds a name, a version or a digest."""
    reasons = [tags.NO_TRAILER, tags.FIELDS, tags.ANOTHER]
    assert all("gora" not in r and "sha256" not in r and "0." not in r for r in reasons)

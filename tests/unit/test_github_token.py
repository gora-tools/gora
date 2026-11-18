"""GORA takes only an installation token, in the form of a Bearer token (L22, ADR-006).

``HttpCore``, the one place that sends a GitHub token, for the reader and the writer, refuses
with a ``ValueError``, before any client is built and in this order:

1. a token that is not a ``b64token`` (RFC 6750 section 2.1: letters, digits and ``- . _ ~ + /``,
   then ``=`` only at the end), which a header value of RFC 9110 would otherwise let through
   (a space, a tab, a control character) or httpx2 would refuse with a text that holds a
   character of the token;
2. a token that does not start with ``ghs_`` and at least one more character: a token of a user
   account (``ghp_``, ``github_pat_``, ``gho_``, ``ghu_``, ``ghr_``) is refused, whoever hands
   it over. Nothing else of a token is read, so any installation token passes.

Every message is the rule's text and holds no part of the token.

A token a test expects GORA to accept starts with ``ghs_``; one it expects refused carries the
prefix under test. Every one breaks the secrets scan's runs: a ``-`` right after the prefix, and
no run of 20 or more letters, digits or ``_`` anywhere after it. The stateless token's shape is
built here from short parts, never written as a literal the scan would read.
"""

import re

import httpx2
import pytest

from gora.adapters import egress
from gora.adapters.github import GitHubAdapter, GitHubReader
from gora.adapters.github._http import INSTALLATION_PREFIX, NOT_BEARER, NOT_INSTALLATION
from tests.audit_fakes import mock_transport
from tests.integration import test_audit_secrets, test_changes, test_cli_literal_output
from tests.unit import test_code_hosts, test_egress_environment, test_github_adapter
from tests.unit import test_github_reader as reader_tests
from tests.unit.test_github_reader import replay

REPO = "gora-tools/gora-fixtures"

# Not a b64token: refused by rule 1, whatever the prefix. ``chr`` for the characters a source
# file would have to hold as escapes.
NOT_B64 = {
    "newline": "ghs_x\n",
    "carriage-return-and-header": "ghs_x\r\nX: y",
    "trailing-space": "ghs_x ",
    "leading-space": " ghs_x",
    "tab": "ghs_x\tb",
    "control": "ghs_x" + chr(1) + "b",
    "delete": "ghs_x" + chr(0x7F) + "b",
    "nul": "ghs_x" + chr(0) + "b",
    "non-ascii": "ghs_" + chr(0xE9),
    "comma": "ghs_x,b",
    "equals-inside": "ghs_x=y",
    "equals-then-more": "ghs_x==y",
    "colon": "ghs_x:b",
    "quote": 'ghs_x"b',
    "backslash": "ghs_x" + chr(92) + "b",
    "semicolon": "ghs_x;b",
    "user-account-and-not-b64": "ghp_-x\n",  # rule 1 first: the prefix is not read
    "only-equals": "=",
}

# A b64token that is not an installation token: refused by rule 2.
NOT_INSTALLATION_TOKEN = {
    "classic": "ghp_-fake-1",
    "fine-grained": "github_pat_-fake-1",
    "oauth": "gho_-fake-1",
    "user-to-server": "ghu_-fake-1",
    "refresh": "ghr_-fake-1",
    "unknown-prefix": "xyz_-fake-1",
    "no-prefix": "fake-1",
    "upper-case": "GHS_-fake-1",
    "prefix-not-at-the-start": "x-ghs_-fake-1",
    "prefix-alone": "ghs_",
    "prefix-without-underscore": "ghs-fake-1",
    "empty": "",
}


def stateless() -> str:
    """An installation token of the stateless shape: ``ghs_``, a number, ``_`` and a JWT of dots
    and dashes, about 520 characters. Built from parts of five characters."""
    part = "-".join(["abc12"] * 28)
    return "ghs_" + "123" + "_" + ".".join([part] * 3)


ACCEPTED = {
    "dash": "ghs_-fake-1",
    "tilde": "ghs_x~",
    "equals": "ghs_x=",
    "two-equals": "ghs_x==",
    "plus-and-slash": "ghs_x+y/z",
    "dots": "ghs_a.b.c",
    "stateless": stateless(),
}


class Requests:
    """A transport handler that records every request and answers none of them."""

    def __init__(self) -> None:
        self.seen: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        raise AssertionError("a request was made")


def _reader(token: str, requests: Requests) -> object:
    return GitHubReader(REPO, token, transport=mock_transport(requests))


def _writer(token: str, requests: Requests) -> object:
    return GitHubAdapter(REPO, token, transport=mock_transport(requests))


BUILDERS = pytest.mark.parametrize("build", [_reader, _writer], ids=["reader", "writer"])


def _refused(build: object, token: str) -> ValueError:
    requests = Requests()
    with pytest.raises(ValueError) as refused:
        build(token, requests)  # type: ignore[operator]
    assert requests.seen == []  # no request, since no client
    return refused.value


# --- rule 1: the form of a Bearer token ------------------------------------------------------


@BUILDERS
@pytest.mark.parametrize("token", NOT_B64.values(), ids=list(NOT_B64))
def test_a_token_that_is_not_a_b64token_is_refused_before_any_request(
    build: object, token: str
) -> None:
    error = _refused(build, token)

    assert str(error) == NOT_BEARER
    assert error.__cause__ is None and error.__context__ is None  # the token is chained nowhere
    assert token not in str(error) and token not in repr(error)


@pytest.mark.parametrize("token", NOT_B64.values(), ids=list(NOT_B64))
def test_a_token_that_httpx2_would_refuse_never_reaches_it(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before this rule a non-ASCII token was refused by httpx2 with a text holding a character of
    it and its position; the core is where it ends, with no client built."""
    built: list[object] = []
    real = egress.BoundedRequest.__init__

    def counting(self: egress.BoundedRequest, *args: object, **kwargs: object) -> None:
        built.append(self)
        real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(egress.BoundedRequest, "__init__", counting)

    with pytest.raises(ValueError):
        GitHubReader(REPO, token)

    assert built == []


# --- rule 2: an installation token -----------------------------------------------------------


@BUILDERS
@pytest.mark.parametrize("token", NOT_INSTALLATION_TOKEN.values(), ids=list(NOT_INSTALLATION_TOKEN))
def test_a_token_that_is_not_an_installation_token_is_refused_before_any_request(
    build: object, token: str
) -> None:
    error = _refused(build, token)

    assert str(error) == NOT_INSTALLATION
    assert error.__cause__ is None
    if token:
        assert token not in str(error) and token not in repr(error)
    assert INSTALLATION_PREFIX not in str(error)  # the message does not name the prefix


def test_the_messages_are_the_rules_text_and_name_no_prefix() -> None:
    assert NOT_BEARER == "the GitHub token is not a Bearer token (RFC 6750, b64token)"
    assert NOT_INSTALLATION == (
        "the GitHub token is not an installation token of a GitHub App: GORA takes no "
        "person's token (L22)"
    )
    for prefix in ("ghp_", "github_pat_", "gho_", "ghu_", "ghr_", "ghs_"):
        assert prefix not in NOT_BEARER + NOT_INSTALLATION


def test_the_first_rule_is_read_before_the_second() -> None:
    both = _refused(_reader, "ghp_-x\n")
    second_only = _refused(_reader, "ghp_-x")
    assert str(both) == NOT_BEARER
    assert str(second_only) == NOT_INSTALLATION


def test_an_installation_prefix_is_compared_with_case_and_needs_one_character_after_it() -> None:
    for token in ("GHS_-x", "Ghs_-x", "ghs_"):
        assert str(_refused(_writer, token)) == NOT_INSTALLATION
    for token in ("ghs_-", "ghs_x"):
        _writer(token, Requests())  # accepted: it is built


# --- what passes -----------------------------------------------------------------------------


@pytest.mark.parametrize("token", ACCEPTED.values(), ids=list(ACCEPTED))
def test_an_installation_token_in_the_form_of_a_bearer_token_is_accepted_and_sent_as_it_is(
    token: str,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return replay("repo")

    assert GitHubReader(reader_tests.FIXTURES, token, transport=mock_transport(handler))
    assert GitHubReader(
        reader_tests.FIXTURES, token, transport=mock_transport(handler)
    ).repository_id()
    (request,) = seen
    assert request.headers["authorization"] == f"Bearer {token}"
    GitHubAdapter(REPO, token, transport=mock_transport(Requests()))  # the writer takes it too


def test_the_stateless_shape_is_what_it_says_it_is() -> None:
    token = stateless()
    assert 500 <= len(token) <= 540
    assert token.count(".") == 2 and "-" in token
    assert token.startswith("ghs_123_")


def test_nothing_of_a_token_is_read_but_its_prefix_and_its_form() -> None:
    """No length, no character set beyond the first rule, no structure: one character after the
    prefix passes, and so does a token of 4,000 characters."""
    GitHubReader(REPO, "ghs_x")
    GitHubReader(REPO, "ghs_" + "ab-" * 1300)
    GitHubReader(REPO, "ghs_" + "0" * 40)  # the shape of a token the scan reads: only a test
    # that builds it at run time may hold one


# --- the test tokens of the suite ------------------------------------------------------------

SENTINELS = {
    "reader": reader_tests.SENTINEL,
    "writer": test_github_adapter.SENTINEL,
    "code-hosts": test_code_hosts.SENTINEL,
    "egress": test_egress_environment.SENTINEL,
    "audit": test_audit_secrets.GITHUB_SENTINEL,
    "changes": test_changes.SENTINEL,
    "literal-output": test_cli_literal_output.SENTINEL,
}

# The secrets scan's own patterns (scan.sh, gitleaks): a prefix and a run of 20 or more.
SCANNED = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")
A_RUN = re.compile(r"[A-Za-z0-9_]{20,}")


@pytest.mark.parametrize("name", SENTINELS)
def test_a_test_token_is_an_installation_token_that_no_scan_reads(name: str) -> None:
    token = SENTINELS[name]
    assert token.startswith("ghs_-")
    assert SCANNED.search(token) is None
    assert A_RUN.search(token) is None


def test_the_test_tokens_are_distinct_and_none_is_part_of_another() -> None:
    tokens = list(SENTINELS.values())
    assert len(set(tokens)) == len(tokens) == 7
    for token in tokens:
        assert [other for other in tokens if token in other] == [token]
    slack = test_egress_environment.SLACK_SENTINEL
    assert not any(slack in token or token in slack for token in tokens)

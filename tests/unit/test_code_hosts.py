"""Which adapter reads a repository (ADR-006): the code host its full name begins with.

What is held: the host is the part of the full name before its first ``/``, and one entry of a
read-only mapping names the adapter that reads it; a host with no entry is a caller's error,
named through ``visible``, before any setting is read or request made; the GitHub entry checks
the full name, reads ``GORA_GITHUB_TOKEN`` and returns the reader, whose one request carries
that token; the token's value is in no text the settings or an error shows.
"""

import inspect
from typing import NoReturn, cast

import httpx2
import pytest

from gora.adapters import code_hosts
from gora.adapters.code_hosts import (
    CODE_HOSTS,
    HOST_TOO_LONG,
    MAX_HOST,
    NO_HOST,
    NO_TOKEN,
    code_host,
)
from gora.adapters.github import GitHubReader
from gora.config.settings import Settings
from gora.domain.release.identifiers import SOURCE_REPOSITORY
from tests.audit_fakes import mock_transport
from tests.unit.test_github_reader import replay

SENTINEL = "fake-sentinel-github-token-9c4e"
REPOSITORY = "github.com/gora-tools/gora-fixtures"


class Untouched:
    """Settings that fail the test when anything is read from them."""

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"a setting was read: {name}")


class Requests:
    """A transport handler that records every request and answers none of them."""

    def __init__(self) -> None:
        self.seen: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        raise AssertionError("a request was made")


def settings_with_token(monkeypatch: pytest.MonkeyPatch, token: str = SENTINEL) -> Settings:
    """The settings of a process whose environment holds ``GORA_GITHUB_TOKEN``."""
    monkeypatch.setenv("GORA_GITHUB_TOKEN", token)
    return Settings()


def asked(repository: str, settings: Settings, requests: Requests) -> ValueError:
    """The ``ValueError`` of ``code_host``, with no request made."""
    with pytest.raises(ValueError) as info:
        code_host(repository, settings, transport=mock_transport(requests))
    assert requests.seen == []
    return info.value


# --- the host ------------------------------------------------------------------------------


def test_one_host_has_an_adapter_and_the_mapping_cannot_be_changed() -> None:
    assert list(CODE_HOSTS) == ["github.com"]

    with pytest.raises(TypeError):
        CODE_HOSTS["gitlab.com"] = CODE_HOSTS["github.com"]  # type: ignore[index]


@pytest.mark.parametrize(
    "repository",
    ["gitlab.com/o/r", "bitbucket.org/o/r", "GitHub.com/o/r", "github.com.evil.example/o/r", "r"],
)
def test_a_host_with_no_entry_is_named_and_asks_nothing(repository: str) -> None:
    requests = Requests()

    error = asked(repository, cast("Settings", Untouched()), requests)

    assert str(error) == f"no adapter reads the code host {repository.partition('/')[0]}"


def test_a_host_is_shown_as_visible_shows_it() -> None:
    """A control character in the host is escaped, so the message holds no terminal sequence."""
    host = f"evil{chr(27)}[2J{chr(0x202E)}.example"

    error = asked(f"{host}/o/r", cast("Settings", Untouched()), Requests())

    assert str(error) == "no adapter reads the code host evil\\x1b[2J\\u202e.example"
    assert chr(27) not in str(error)
    assert chr(0x202E) not in str(error)


@pytest.mark.parametrize("repository", ["", "/o/r", "/"])
def test_an_empty_host_has_its_own_words(repository: str) -> None:
    error = asked(repository, cast("Settings", Untouched()), Requests())

    assert str(error) == NO_HOST


def test_a_host_of_253_characters_is_named_and_one_of_254_is_not() -> None:
    """Calibration: the longest DNS name is named; one more is not shown at all."""
    longest = "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 61
    assert len(longest) == MAX_HOST == 253

    named = asked(f"{longest}/o/r", cast("Settings", Untouched()), Requests())
    over = asked(f"{longest}d/o/r", cast("Settings", Untouched()), Requests())

    assert str(named) == f"no adapter reads the code host {longest}"
    assert str(over) == HOST_TOO_LONG
    assert "dddd" not in str(over)


# --- the GitHub entry ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "repository",
    [
        "github.com",
        "github.com/",
        "github.com/owner",
        "github.com/owner/name/extra",
        "github.com/Owner/name",
        "github.com/owner/Name",
        "github.com/owner/name.git",
        "github.com/owner/..",
        "github.com/-owner/name",
        "github.com/owner/name\n",
    ],
)
def test_a_full_name_the_grammar_refuses_is_refused_before_any_request(
    repository: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = asked(repository, settings_with_token(monkeypatch), Requests())

    assert str(error) == f"repository {SOURCE_REPOSITORY.problem(repository)}"


def test_a_refused_full_name_is_not_in_the_error() -> None:
    error = asked("github.com/Sentinel-Owner-4f1a/x", Settings(), Requests())

    assert "Sentinel" not in str(error)


def test_the_name_is_checked_before_the_token_is_read() -> None:
    """A full name that is refused never reads the settings, the token included."""
    error = asked("github.com/Owner/name", cast("Settings", Untouched()), Requests())

    assert str(error) == f"repository {SOURCE_REPOSITORY.problem('github.com/Owner/name')}"


def test_an_empty_token_names_the_setting_that_holds_it() -> None:
    error = asked(REPOSITORY, Settings(), Requests())

    assert str(error) == NO_TOKEN
    assert "GORA_GITHUB_TOKEN" in str(error)


def test_a_known_repository_sends_one_request_to_github_with_the_token_of_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return replay("repo")

    host = code_host(
        REPOSITORY, settings_with_token(monkeypatch), transport=mock_transport(handler)
    )

    assert seen == []  # building the adapter asks nothing
    assert isinstance(host, GitHubReader)
    assert host.repository_id() == "1457970812"
    (request,) = seen
    assert request.method == "GET"
    assert request.url.host == "api.github.com"
    assert request.url.path == "/repos/gora-tools/gora-fixtures"
    assert request.headers["authorization"] == f"Bearer {SENTINEL}"


def test_the_token_is_unwrapped_in_one_place_of_the_module() -> None:
    assert inspect.getsource(code_hosts).count("get_secret_value()") == 1


# --- the token is never shown --------------------------------------------------------------


def test_the_token_is_in_neither_the_repr_nor_the_str_of_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with_token(monkeypatch)

    assert settings.github_token.get_secret_value() == SENTINEL
    assert SENTINEL not in repr(settings)
    assert SENTINEL not in str(settings)
    assert SENTINEL not in repr(settings.github_token)
    assert SENTINEL not in str(settings.github_token)
    assert SENTINEL not in repr(settings.model_dump())


def test_the_token_is_in_no_error_of_code_host(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with_token(monkeypatch)
    errors = [
        asked(repository, settings, Requests())
        for repository in ["", "gitlab.com/o/r", "github.com/Owner/name", "github.com/o"]
    ]

    assert len(errors) == 4  # calibration: each of the refusals above was one
    for error in errors:
        assert SENTINEL not in str(error)
        assert SENTINEL not in repr(error)

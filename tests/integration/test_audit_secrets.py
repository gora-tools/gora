"""No secret reaches the audit journal (ADR-0017), by name and by value.

Two guards, because each misses what the other catches. A *name* guard fails when an event
carries a field called token, secret, password or key, at any depth, whatever its value:
it stops the next field before it holds anything. A *value* guard sets every GORA token and
secret to a sentinel, runs every operation an adapter audits, and fails when a sentinel
appears in the journal or in the events the journal missed and kept in ``unrecorded`` (what
a command prints when the journal fails): it stops a secret that arrives under an innocent
name.

Both run the real adapters against fake GitHub and Slack transports, so the events they
inspect are the ones production writes. Each is shown to fail in the calibration tests at
the end, and the event types are taken from the source, so a new one cannot be left
unsampled.
"""

import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

import gora
from gora.audit.sink import Attached, attach, resolve_journal
from gora.config.settings import Settings
from gora.domain.notification import Notification, Span
from tests.audit_fakes import Remote, fail_writes, journal_events

SECRET_NAME = re.compile(r"token|secret|password|key", re.IGNORECASE)

# GORA reads no GitHub token from the environment: the adapter is given one by its caller.
GITHUB_SENTINEL = "fake-sentinel-github-token"

NOTIFICATION = Notification(
    summary=(Span("Promoted ", "word"), Span("1.0.0", "value")),
    detail=(Span("Digest: ", "word"), Span("sha256:abc", "digest")),
)


def event_types_in_source() -> set[str]:
    """Every action string passed to ``.record(...)`` anywhere under ``src/gora``."""
    actions: set[str] = set()
    for path in Path(gora.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                actions.add(node.args[0].value)
    return actions


def secret_field_names(value: object, where: str = "") -> list[str]:
    """Paths of every field name, at any depth, that looks like a secret."""
    found: list[str] = []
    if isinstance(value, dict):
        for name, inner in value.items():
            here = f"{where}.{name}" if where else str(name)
            if SECRET_NAME.search(str(name)):
                found.append(here)
            found += secret_field_names(inner, here)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found += secret_field_names(inner, f"{where}[{index}]")
    return found


def secret_variables() -> dict[str, str]:
    """Every GORA_*TOKEN and GORA_*SECRET Settings reads, each with its own fake value."""
    names = [
        f"GORA_{field.upper()}"
        for field in Settings.model_fields
        if field.endswith(("token", "secret"))
    ]
    return {name: f"fake-sentinel-{i:04d}-{name.lower()}" for i, name in enumerate(sorted(names))}


def leaks(sentinels: dict[str, str], *haystacks: bytes) -> list[str]:
    """The variables whose sentinel appears in any of the haystacks."""
    return [
        name
        for name, value in sentinels.items()
        if any(value.encode() in haystack for haystack in haystacks)
    ]


@pytest.fixture
def sentinels(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values = secret_variables()
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.fixture
def remote() -> Remote:
    return Remote()


@pytest.fixture
def journal() -> Iterator[Attached]:
    """The sink attached to ``gora.audit``, writing where the isolation fixture points it."""
    attached = attach(resolve_journal(os.environ))
    yield attached
    attached.detach()


def run_every_audited_operation(remote: Remote, slack_token: str) -> None:
    """The GitHub adapter's create_branch, commit_file and create_pull_request, and Slack's post."""
    github = remote.github("o/r", GITHUB_SENTINEL)
    github.create_branch("gora/promote-dev", "basesha")
    github.commit_file("state/dev/gora.yaml", "version: 1\n", "gora/promote-dev", "promote")
    github.create_pull_request("gora/promote-dev", "main", "Promote", "body")
    remote.slack(slack_token).post_message("C1", NOTIFICATION)


# --- the guards ----------------------------------------------------------------------


def test_every_event_type_in_the_source_is_sampled_and_has_no_secret_field_name(
    sentinels: dict[str, str], remote: Remote, journal: Attached
) -> None:
    run_every_audited_operation(remote, Settings().slack_bot_token)

    events = journal_events(journal.journal.path)

    # Every type in the source was produced by a real run. A new one fails here until it is
    # sampled; the AST scan, not a list kept in this file, says what "every" is.
    assert {str(e["action"]) for e in events} == event_types_in_source()
    # Calibration of the scan itself: it finds the types we know are there.
    assert {
        "github.create_branch",
        "github.commit_file",
        "github.create_pull_request",
        "slack.post_message",
    } <= event_types_in_source()

    bad = {str(e["action"]): secret_field_names(e) for e in events if secret_field_names(e)}
    assert bad == {}, f"field names that look like secrets: {bad}"


def test_no_secret_value_reaches_the_journal_or_the_events_it_missed(
    sentinels: dict[str, str],
    remote: Remote,
    journal: Attached,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every = {**sentinels, "the GitHub adapter's token": GITHUB_SENTINEL}

    run_every_audited_operation(remote, Settings().slack_bot_token)

    # The sentinels are really in play: the adapters sent them, so a leak was possible.
    sent = remote.authorization_headers()
    assert f"Bearer {GITHUB_SENTINEL}" in sent
    assert f"Bearer {sentinels['GORA_SLACK_BOT_TOKEN']}" in sent
    assert len(journal_events(journal.journal.path)) == 4
    assert leaks(every, journal.journal.path.read_bytes()) == []
    assert journal.journal.unrecorded == []

    # The same operations with the journal failing: the events are kept for the command to
    # print instead.
    fail_writes(monkeypatch)
    failing = attach(resolve_journal({"GORA_AUDIT_LOG": str(tmp_path / "again" / "audit.jsonl")}))
    try:
        run_every_audited_operation(Remote(), Settings().slack_bot_token)
        missed = list(failing.journal.unrecorded)
    finally:
        failing.detach()
    assert len(missed) == 4
    assert leaks(every, "\n".join(missed).encode()) == []


def test_the_secret_variables_are_the_ones_settings_reads() -> None:
    names = set(secret_variables())
    # Derived from Settings, so a new token field is covered the day it is added.
    assert {"GORA_SLACK_BOT_TOKEN", "GORA_SLACK_APP_TOKEN"} <= names
    assert "GORA_SLACK_SIGNING_SECRET" in names


# --- calibration: each guard registers what it guards against -------------------------


def test_the_name_guard_flags_a_secret_field_at_any_depth() -> None:
    assert secret_field_names({"details": {"api_token": 1}}) == ["details.api_token"]
    assert secret_field_names({"a": [{"x": {"Password": 1}}]}) == ["a[0].x.Password"]
    assert secret_field_names({"details": {"signing_Secret": 1, "ssh_key": 2}}) == [
        "details.signing_Secret",
        "details.ssh_key",
    ]
    assert secret_field_names({"details": {"version": 1, "digest": "sha256:x"}}) == []


def test_the_value_guard_flags_a_planted_sentinel() -> None:
    sentinels = {"GORA_GITHUB_TOKEN": "fake-sentinel-0000", "GORA_SLACK_BOT_TOKEN": "fake-other"}
    assert leaks(sentinels, b'{"details": {"x": "fake-sentinel-0000"}}\n') == ["GORA_GITHUB_TOKEN"]
    assert leaks(sentinels, b'{"details": {"x": "nothing"}}\n') == []

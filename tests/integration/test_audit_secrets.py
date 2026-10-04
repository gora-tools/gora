"""No secret reaches the audit journal (ADR-0017), by name and by value.

Two guards, because each misses what the other catches. A *name* guard fails when an event
carries a field called token, secret, password or key, at any depth, whatever its value:
it stops the next field before it holds anything. A *value* guard sets every GORA token and
secret to a sentinel, runs every mutating command, and fails when a sentinel appears in the
journal or in the events a command prints when the journal missed them: it stops a secret
that arrives under an innocent name.

Both run the real services and adapters against fake GitHub and Slack transports, so the
events they inspect are the ones production writes. Each is shown to fail in the calibration
tests at the end, and the event types are taken from the source, so a new one cannot be
left unsampled.
"""

import ast
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

import gora
from gora.config.settings import Settings
from gora.transport.cli.commands import app
from tests.audit_fakes import (
    DIGEST_B,
    IMAGE,
    Remote,
    fail_writes,
    journal_events,
    seed_manifest,
    stderr_events,
)

runner = CliRunner()

SECRET_NAME = re.compile(r"token|secret|password|key", re.IGNORECASE)


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
    monkeypatch.setenv("GORA_SLACK_ENABLED", "true")
    monkeypatch.setenv("GORA_SLACK_CHANNEL", "C1")
    return values


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Remote:
    fake = Remote()
    fake.install(monkeypatch)
    return fake


def run_every_mutating_command(directory: Path) -> list[Result]:
    """cut-rc, promote --open-pr, promote and rollback; Slack on, so each also posts."""
    directory.mkdir(exist_ok=True)
    manifest = directory / "release-manifest.yaml"
    seed_manifest(manifest)
    m = str(manifest)
    return [
        runner.invoke(app, ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.2", "-d", DIGEST_B, "-m", m]),
        runner.invoke(app, ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", m]),
        runner.invoke(app, ["promote", "dev", "-m", m]),
        runner.invoke(
            app, ["rollback", "dev", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", m]
        ),
    ]


# --- the guards ----------------------------------------------------------------------


def test_every_event_type_in_the_source_is_sampled_and_has_no_secret_field_name(
    tmp_path: Path, sentinels: dict[str, str], remote: Remote
) -> None:
    results = run_every_mutating_command(tmp_path)
    assert [r.exit_code for r in results] == [0, 0, 0, 0], [r.output for r in results]

    journal = tmp_path / "isolated" / "audit" / "audit.jsonl"
    events = journal_events(journal)

    # Every type in the source was produced by a real run. A new one fails here until it is
    # sampled; the AST scan, not a list kept in this file, says what "every" is.
    assert {str(e["action"]) for e in events} == event_types_in_source()
    # Calibration of the scan itself: it finds the types we know are there.
    assert {"cut-rc", "promote", "rollback", "promotion_pr", "slack.post_message"} <= (
        event_types_in_source()
    )

    bad = {str(e["action"]): secret_field_names(e) for e in events if secret_field_names(e)}
    assert bad == {}, f"field names that look like secrets: {bad}"


def test_no_secret_value_reaches_the_journal_or_the_printed_fallback(
    tmp_path: Path, sentinels: dict[str, str], remote: Remote, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "isolated" / "audit" / "audit.jsonl"

    results = run_every_mutating_command(tmp_path)

    assert [r.exit_code for r in results] == [0, 0, 0, 0]
    # The sentinels are really in play: the adapters sent them, so a leak was possible.
    sent = remote.authorization_headers()
    assert f"Bearer {sentinels['GORA_GITHUB_TOKEN']}" in sent
    assert f"Bearer {sentinels['GORA_SLACK_BOT_TOKEN']}" in sent
    assert len(journal_events(journal)) >= 8
    printed = b"".join(r.output.encode() + r.stderr.encode() for r in results)
    assert leaks(sentinels, journal.read_bytes(), printed) == []

    # The same commands with the journal failing: the events go to stderr instead.
    fail_writes(monkeypatch)
    results = run_every_mutating_command(tmp_path / "again")
    assert [r.exit_code for r in results] == [3, 3, 3, 3]
    fallback = b"".join(r.stderr.encode() for r in results)
    assert len([e for r in results for e in stderr_events(r.stderr)]) >= 8
    assert leaks(sentinels, fallback, b"".join(r.output.encode() for r in results)) == []


def test_the_secret_variables_are_the_ones_settings_reads() -> None:
    names = set(secret_variables())
    # Derived from Settings, so a new token field is covered the day it is added.
    assert {"GORA_GITHUB_TOKEN", "GORA_SLACK_BOT_TOKEN", "GORA_SLACK_APP_TOKEN"} <= names
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

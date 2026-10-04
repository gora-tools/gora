"""The CLI's audit journal (ADR-0017): where it is, how it is written, what a failure does.

The journal is the one thing in GORA that must exist because a command ran. Each test here
holds one clause of that: the path precedence, the modes, one line per event, nothing
created by a read-only command, a refusal before any mutation, and a write that fails
after one without aborting the sequence.
"""

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

import gora
import gora.audit.sink as sink
from gora.transport.cli.commands import app
from tests.audit_fakes import (
    DIGEST_B,
    IMAGE,
    Remote,
    complete_lines,
    digest_of,
    fail_writes,
    journal_events,
    seed_manifest,
    stderr_events,
    tear_first_write,
)

runner = CliRunner()

OPEN_PR_ACTIONS = [
    "github.create_branch",
    "github.commit_file",
    "github.create_pull_request",
    "promotion_pr",
]


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def journal(_isolated_home: Path) -> Path:
    """The journal the isolation fixture points GORA_AUDIT_LOG at."""
    return _isolated_home / "audit" / "audit.jsonl"


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Remote:
    fake = Remote()
    fake.install(monkeypatch)
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "fake-github-token")
    return fake


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "release-manifest.yaml"
    seed_manifest(path)
    return path


# One invocation of each mutating command, each one changing the seeded manifest: a new
# candidate; its candidate promoted to a stage it is not in yet; dev rolled back to the
# release prod runs.
Command = Callable[[Path], list[str]]
MUTATING: dict[str, Command] = {
    "cut-rc": lambda m: [
        "cut-rc",
        "-i",
        IMAGE,
        "-v",
        "1.2.0-rc.2",
        "-d",
        DIGEST_B,
        "-m",
        str(m),
    ],
    "promote": lambda m: ["promote", "staging", "-m", str(m)],
    "rollback": lambda m: [
        "rollback",
        "dev",
        "--to-version",
        "1.1.9",
        "--to-digest",
        DIGEST_B,
        "-m",
        str(m),
    ],
    "promote-open-pr": lambda m: ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", str(m)],
}


def run(args: list[str]) -> Result:
    return runner.invoke(app, args)


# --- where the journal is ------------------------------------------------------------


def test_default_is_home_dot_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("GORA_AUDIT_LOG")

    result = run(MUTATING["promote"](manifest))

    assert result.exit_code == 0, result.output
    path = home / ".local" / "state" / "gora" / "audit.jsonl"
    assert [e["action"] for e in journal_events(path)] == ["promote"]
    # Directories created by GORA are 0700, and the file 0600.
    for directory in (home / ".local", home / ".local" / "state", path.parent):
        assert mode(directory) == 0o700, directory
    assert mode(path) == 0o600


def test_xdg_state_home_is_used_when_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    monkeypatch.delenv("GORA_AUDIT_LOG")

    result = run(MUTATING["promote"](manifest))

    assert result.exit_code == 0, result.output
    assert len(journal_events(tmp_path / "st" / "gora" / "audit.jsonl")) == 1
    assert not (Path(os.environ["HOME"]) / ".local").exists()


@pytest.mark.parametrize("xdg", ["", "relative/x"], ids=["empty", "relative"])
def test_empty_or_relative_xdg_state_home_falls_back_to_home(
    xdg: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", xdg)
    monkeypatch.delenv("GORA_AUDIT_LOG")
    monkeypatch.chdir(tmp_path)

    result = run(MUTATING["promote"](manifest))

    assert result.exit_code == 0, result.output
    home = Path(os.environ["HOME"])
    assert len(journal_events(home / ".local" / "state" / "gora" / "audit.jsonl")) == 1
    assert not (tmp_path / "relative").exists()


def test_override_wins_and_nothing_is_written_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    chosen = tmp_path / "j.jsonl"
    monkeypatch.setenv("GORA_AUDIT_LOG", str(chosen))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))

    result = run(MUTATING["promote"](manifest))

    assert result.exit_code == 0, result.output
    assert len(journal_events(chosen)) == 1
    assert not (tmp_path / "st").exists()
    assert not (Path(os.environ["HOME"]) / ".local").exists()


def test_empty_override_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    monkeypatch.setenv("GORA_AUDIT_LOG", "")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))

    assert run(MUTATING["promote"](manifest)).exit_code == 0
    assert (tmp_path / "st" / "gora" / "audit.jsonl").exists()


@pytest.mark.parametrize("name", MUTATING)
def test_relative_override_is_a_usage_error_before_anything_changes(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest: Path,
    remote: Remote,
) -> None:
    monkeypatch.setenv("GORA_AUDIT_LOG", "rel.jsonl")
    monkeypatch.chdir(tmp_path)
    before = digest_of(manifest)

    result = run(MUTATING[name](manifest))

    assert result.exit_code == 2, result.output
    assert "GORA_AUDIT_LOG must be an absolute path" in result.stderr
    assert digest_of(manifest) == before
    assert remote.requests == []
    assert not (tmp_path / "rel.jsonl").exists()


def test_a_pre_existing_wide_directory_is_tightened_by_one_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    gora = tmp_path / "st" / "gora"
    gora.mkdir(parents=True)
    gora.chmod(0o755)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    monkeypatch.delenv("GORA_AUDIT_LOG")

    assert run(MUTATING["promote"](manifest)).exit_code == 0

    assert mode(gora) == 0o700


# --- what is written -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["cut-rc", "promote", "rollback"])
def test_one_mutating_command_adds_exactly_one_line(
    name: str, journal: Path, manifest: Path
) -> None:
    result = run(MUTATING[name](manifest))

    assert result.exit_code == 0, result.output
    lines = journal.read_text().splitlines()
    assert len(lines) == 1
    assert journal_events(journal)[0]["action"] == name
    assert journal.read_bytes().endswith(b"\n")


def test_a_second_command_appends(journal: Path, manifest: Path) -> None:
    run(MUTATING["promote"](manifest))
    run(MUTATING["rollback"](manifest))

    assert [e["action"] for e in journal_events(journal)] == ["promote", "rollback"]


def test_open_pr_records_every_step(journal: Path, manifest: Path, remote: Remote) -> None:
    result = run(MUTATING["promote-open-pr"](manifest))

    assert result.exit_code == 0, result.output
    assert [e["action"] for e in journal_events(journal)] == OPEN_PR_ACTIONS


def test_a_refused_command_records_nothing(journal: Path, tmp_path: Path) -> None:
    result = run(["promote", "dev", "-m", str(tmp_path / "absent.yaml")])

    assert result.exit_code == 1
    assert journal.read_bytes() == b""


# --- read-only commands leave no trace -----------------------------------------------


def test_version_and_status_create_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    home = tmp_path / "fresh"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("GORA_AUDIT_LOG")

    assert run(["version"]).exit_code == 0
    assert run(["status", "-m", str(manifest)]).exit_code == 0

    assert list(home.iterdir()) == []


def test_version_and_status_do_not_create_the_override_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    monkeypatch.setenv("GORA_AUDIT_LOG", str(tmp_path / "never" / "j.jsonl"))

    assert run(["version"]).exit_code == 0
    assert run(["status", "-m", str(manifest)]).exit_code == 0

    assert not (tmp_path / "never").exists()


def test_the_api_process_attaches_no_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    from fastapi.testclient import TestClient

    from gora.main import app as api

    home = tmp_path / "api-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("GORA_AUDIT_LOG")

    assert TestClient(api).get("/healthz").status_code == 200

    assert logging.getLogger("gora.audit").handlers == []
    assert list(home.iterdir()) == []


# --- before any mutation -------------------------------------------------------------


@pytest.fixture
def unwritable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    if os.geteuid() == 0:
        pytest.skip("root can write to a 0500 directory")
    directory = tmp_path / "ro"
    directory.mkdir()
    directory.chmod(0o500)
    monkeypatch.setenv("GORA_AUDIT_LOG", str(directory / "audit.jsonl"))
    yield directory
    directory.chmod(0o700)


@pytest.mark.usefixtures("unwritable")
@pytest.mark.parametrize("name", MUTATING)
def test_an_unopenable_journal_stops_the_command_before_any_mutation(
    name: str, manifest: Path, remote: Remote
) -> None:
    before = digest_of(manifest)

    result = run(MUTATING[name](manifest))

    assert result.exit_code == 1, result.output
    assert "Cannot open the audit journal" in result.stderr
    assert "no manifest write, no remote call" in result.stderr
    assert digest_of(manifest) == before
    assert remote.requests == []
    assert result.stdout == ""


def test_an_unopenable_journal_creates_no_manifest(
    tmp_path: Path, unwritable: Path, remote: Remote
) -> None:
    path = tmp_path / "new-manifest.yaml"

    result = run(MUTATING["cut-rc"](path))

    assert result.exit_code == 1
    assert not path.exists()


# The 0500 cases above cannot fail as root, which is how a container gate runs. These
# cannot be opened by anyone: the path is a directory (EISDIR), or its parent is a regular
# file (ENOTDIR).
@pytest.fixture(params=["path-is-a-directory", "parent-is-a-file"])
def unopenable(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    if request.param == "path-is-a-directory":
        path = tmp_path / "journal-dir"
        path.mkdir()
    else:
        (tmp_path / "not-a-dir").write_text("x")
        path = tmp_path / "not-a-dir" / "audit.jsonl"
    monkeypatch.setenv("GORA_AUDIT_LOG", str(path))
    return path


@pytest.mark.parametrize("name", MUTATING)
def test_an_unopenable_journal_stops_the_command_before_any_mutation_even_as_root(
    name: str, unopenable: Path, manifest: Path, remote: Remote
) -> None:
    before = digest_of(manifest)

    result = run(MUTATING[name](manifest))

    assert result.exit_code == 1, result.output
    assert "Cannot open the audit journal" in result.stderr
    assert "no manifest write, no remote call" in result.stderr
    assert digest_of(manifest) == before
    assert remote.requests == []
    assert result.stdout == ""


def test_an_unopenable_journal_creates_no_manifest_even_as_root(
    tmp_path: Path, unopenable: Path, remote: Remote
) -> None:
    path = tmp_path / "new-manifest.yaml"

    result = run(MUTATING["cut-rc"](path))

    assert result.exit_code == 1
    assert not path.exists()


def test_a_fifo_at_the_journal_path_fails_instead_of_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    fifo = tmp_path / "j.fifo"
    os.mkfifo(fifo)
    before = digest_of(manifest)
    env = {
        **os.environ,
        "GORA_AUDIT_LOG": str(fifo),
        "PYTHONPATH": str(Path(gora.__file__).parent.parent),
    }
    cli = "from gora.transport.cli.commands import app; app()"

    # A real process with a timeout: a command that blocks on the open never returns.
    done = subprocess.run(
        [sys.executable, "-c", cli, *MUTATING["promote"](manifest)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert done.returncode == 1, done.stderr
    assert "Cannot open the audit journal" in done.stderr
    assert digest_of(manifest) == before


def test_a_journal_that_is_not_a_regular_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, remote: Remote
) -> None:
    monkeypatch.setenv("GORA_AUDIT_LOG", "/dev/null")
    before = digest_of(manifest)

    result = run(MUTATING["promote"](manifest))

    assert result.exit_code == 1
    assert "not a regular file" in result.stderr
    assert digest_of(manifest) == before


@pytest.mark.parametrize("name", MUTATING)
def test_the_journal_is_opened_once_and_the_same_descriptor_is_held(
    name: str, monkeypatch: pytest.MonkeyPatch, journal: Path, manifest: Path, remote: Remote
) -> None:
    opened: list[int] = []
    real = sink.open_journal

    def counting(target: sink.JournalTarget) -> int:
        fd = real(target)
        opened.append(fd)
        return fd

    monkeypatch.setattr(sink, "open_journal", counting)
    # Move the file away at the first write. A command that closed and reopened the path
    # would start a second file at the old name; this one keeps writing to the moved one.
    moved = journal.with_name("moved.jsonl")
    real_write = sink._write

    def write(fd: int, data: bytes | memoryview) -> int:
        if journal.exists():
            os.rename(journal, moved)
        return real_write(fd, data)

    monkeypatch.setattr(sink, "_write", write)

    result = run(MUTATING[name](manifest))

    assert result.exit_code == 0, result.output
    assert len(opened) == 1
    assert not journal.exists()
    assert len(moved.read_text().splitlines()) == (4 if name == "promote-open-pr" else 1)


# --- a write that fails after the mutation -------------------------------------------


def test_late_failure_completes_all_four_steps_and_exits_3(
    monkeypatch: pytest.MonkeyPatch, journal: Path, manifest: Path, remote: Remote
) -> None:
    fail_writes(monkeypatch)

    result = run(MUTATING["promote-open-pr"](manifest))

    # The sequence was not aborted: branch, commit, PR, all made.
    methods = [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in remote.github_requests]
    assert ("POST", "refs") in methods
    assert ("PUT", "release-manifest.yaml") in methods
    assert ("POST", "pulls") in methods
    assert "Opened promotion PR #7" in result.stdout

    assert result.exit_code == 3, result.output
    lines = result.stderr.splitlines()
    assert "The operation completed. 4 events were not recorded in the audit journal:" in lines
    first_event = next(i for i, line in enumerate(lines) if line.startswith("{"))
    assert (
        lines.index("The operation completed. 4 events were not recorded in the audit journal:")
        < first_event
    )
    assert [e["action"] for e in stderr_events(result.stderr)] == OPEN_PR_ACTIONS
    assert journal.read_bytes() == b""


def test_late_failure_in_the_middle_prints_only_the_missing_events(
    monkeypatch: pytest.MonkeyPatch, journal: Path, manifest: Path, remote: Remote
) -> None:
    fail_writes(monkeypatch, after=2)

    result = run(MUTATING["promote-open-pr"](manifest))

    assert result.exit_code == 3, result.output
    assert [e["action"] for e in journal_events(journal)] == OPEN_PR_ACTIONS[:2]
    assert [e["action"] for e in stderr_events(result.stderr)] == OPEN_PR_ACTIONS[2:]
    assert "2 events were not recorded" in result.stderr


@pytest.mark.parametrize("name", ["cut-rc", "promote", "rollback"])
def test_late_failure_keeps_the_local_result_and_exits_3(
    name: str, monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    fail_writes(monkeypatch)
    before = digest_of(manifest)

    result = run(MUTATING[name](manifest))

    assert result.exit_code == 3, result.output
    assert digest_of(manifest) != before  # the operation completed
    assert "The operation completed. 1 event was not recorded" in result.stderr
    assert [e["action"] for e in stderr_events(result.stderr)] == [name]


def test_late_failure_is_still_exit_1_when_the_operation_itself_failed(
    monkeypatch: pytest.MonkeyPatch, manifest: Path
) -> None:
    remote = Remote(fail_pull_request=True)
    remote.install(monkeypatch)
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "fake-github-token")
    fail_writes(monkeypatch)

    result = run(MUTATING["promote-open-pr"](manifest))

    # Branch and commit happened, the PR did not: refused or failed, not "completed".
    assert result.exit_code == 1, result.output
    assert "The operation did not complete. 2 events were not recorded" in result.stderr
    assert [e["action"] for e in stderr_events(result.stderr)] == OPEN_PR_ACTIONS[:2]


def test_the_notifier_is_not_failed_by_the_journal(
    monkeypatch: pytest.MonkeyPatch, manifest: Path, remote: Remote
) -> None:
    monkeypatch.setenv("GORA_SLACK_ENABLED", "true")
    monkeypatch.setenv("GORA_SLACK_BOT_TOKEN", "fake-slack-token")
    monkeypatch.setenv("GORA_SLACK_CHANNEL", "C1")
    fail_writes(monkeypatch)

    result = run(MUTATING["promote"](manifest))

    assert result.exit_code == 3, result.output
    assert len(remote.slack_requests) == 1  # the post went out; its event is the second
    assert [e["action"] for e in stderr_events(result.stderr)] == ["promote", "slack.post_message"]
    assert "slack notify failed" not in result.stderr


def test_the_sink_is_detached_after_every_command(manifest: Path) -> None:
    import logging

    run(MUTATING["promote"](manifest))

    assert logging.getLogger("gora.audit").handlers == []


# --- a torn line never merges with the next event ----------------------------------------


def test_a_torn_write_leaves_no_unparseable_line_and_exits_3(
    monkeypatch: pytest.MonkeyPatch, journal: Path, manifest: Path, remote: Remote
) -> None:
    monkeypatch.setenv("GORA_SLACK_ENABLED", "true")
    monkeypatch.setenv("GORA_SLACK_BOT_TOKEN", "fake-slack-token")
    monkeypatch.setenv("GORA_SLACK_CHANNEL", "C1")
    tear_first_write(monkeypatch)

    result = run(MUTATING["promote"](manifest))  # two events: promote, then the Slack post

    assert result.exit_code == 3, result.output
    assert [json.loads(line) for line in complete_lines(journal.read_bytes())] == []
    assert [e["action"] for e in stderr_events(result.stderr)] == ["promote", "slack.post_message"]
    assert "2 events were not recorded" in result.stderr


def test_a_journal_left_without_a_newline_by_a_dead_command_is_not_joined(
    journal: Path, manifest: Path
) -> None:
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b'{"action": "tor')

    result = run(MUTATING["promote"](manifest))

    assert result.exit_code == 0, result.output
    lines = journal.read_bytes().splitlines()
    assert lines[0] == b'{"action": "tor'  # untouched
    assert [json.loads(line)["action"] for line in lines[1:]] == ["promote"]

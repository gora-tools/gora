"""The audit journal sink (ADR-0017): its path, its files, and what its handler does on failure."""

import io
import json
import logging
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import gora
import gora.audit.sink as sink
from gora.audit.logger import AuditLogger
from gora.audit.sink import (
    Journal,
    JournalPathError,
    JournalTarget,
    attach,
    open_journal,
    resolve_journal,
)
from tests.audit_fakes import complete_lines, tear_first_write

audit_logger = logging.getLogger("gora.audit")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- path ----------------------------------------------------------------------------


def test_override_wins_over_xdg_and_home() -> None:
    target = resolve_journal(
        {"GORA_AUDIT_LOG": "/a/j.jsonl", "XDG_STATE_HOME": "/x", "HOME": "/h"},
    )
    assert target == JournalTarget(Path("/a/j.jsonl"), own_dir=False)


def test_xdg_state_home_wins_over_home() -> None:
    target = resolve_journal({"XDG_STATE_HOME": "/x", "HOME": "/h"})
    assert target == JournalTarget(Path("/x/gora/audit.jsonl"), own_dir=True)


def test_home_default_is_dot_local_state_not_a_platform_directory() -> None:
    target = resolve_journal({"HOME": "/h"})
    assert target == JournalTarget(Path("/h/.local/state/gora/audit.jsonl"), own_dir=True)


@pytest.mark.parametrize("xdg", ["", "relative/x", "."], ids=["empty", "relative", "dot"])
def test_xdg_state_home_empty_or_relative_is_ignored(xdg: str) -> None:
    target = resolve_journal({"XDG_STATE_HOME": xdg, "HOME": "/h"})
    assert target.path == Path("/h/.local/state/gora/audit.jsonl")


def test_empty_override_is_unset() -> None:
    target = resolve_journal({"GORA_AUDIT_LOG": "", "HOME": "/h"})
    assert target.path == Path("/h/.local/state/gora/audit.jsonl")


@pytest.mark.parametrize("value", ["rel.jsonl", "./j.jsonl", "~/j.jsonl", "a/b"])
def test_relative_override_is_an_error(value: str) -> None:
    with pytest.raises(JournalPathError, match="absolute"):
        resolve_journal({"GORA_AUDIT_LOG": value, "HOME": "/h"})


@pytest.mark.parametrize("env", [{}, {"HOME": ""}, {"HOME": "rel"}], ids=["unset", "empty", "rel"])
def test_no_usable_home_names_the_variable_to_set(env: dict[str, str]) -> None:
    with pytest.raises(JournalPathError, match="GORA_AUDIT_LOG"):
        resolve_journal(env)


# --- files ---------------------------------------------------------------------------


def test_missing_directories_are_created_0700_even_under_a_loose_umask(tmp_path: Path) -> None:
    target = JournalTarget(tmp_path / "a" / "b" / "gora" / "audit.jsonl", own_dir=True)
    old = os.umask(0)
    try:
        os.close(open_journal(target))
    finally:
        os.umask(old)

    for directory in (tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "gora"):
        assert mode(directory) == 0o700, directory
    assert mode(target.path) == 0o600


def test_existing_own_directory_with_wider_mode_is_tightened(tmp_path: Path) -> None:
    gora = tmp_path / "gora"
    gora.mkdir()
    gora.chmod(0o755)

    os.close(open_journal(JournalTarget(gora / "audit.jsonl", own_dir=True)))

    assert mode(gora) == 0o700


def test_an_operator_chosen_directory_is_not_touched(tmp_path: Path) -> None:
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    chosen.chmod(0o755)

    os.close(open_journal(JournalTarget(chosen / "j.jsonl", own_dir=False)))

    assert mode(chosen) == 0o755


def test_existing_file_with_wider_mode_is_tightened_and_appended_to(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.write_text("old\n")
    path.chmod(0o644)

    fd = open_journal(JournalTarget(path, own_dir=False))
    os.write(fd, b"new\n")
    os.close(fd)

    assert mode(path) == 0o600
    assert path.read_text() == "old\nnew\n"


def test_a_file_that_is_not_regular_is_refused_and_left_alone() -> None:
    # /dev/null would swallow every event and still look open.
    before = mode(Path("/dev/null"))
    with pytest.raises(OSError, match="not a regular file"):
        open_journal(JournalTarget(Path("/dev/null"), own_dir=False))
    assert mode(Path("/dev/null")) == before


def test_a_directory_at_the_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        open_journal(JournalTarget(tmp_path, own_dir=False))


# --- the handler ---------------------------------------------------------------------


def _journal(path: Path) -> Journal:
    return Journal(open_journal(JournalTarget(path, own_dir=False)), path)


def test_each_event_is_one_newline_terminated_json_line(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    attached = attach(JournalTarget(path, own_dir=False))
    try:
        AuditLogger().record("cut-rc", version="1.0.0-rc.1")
        AuditLogger().record("promote", stage="dev")
    finally:
        attached.detach()

    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    events = [json.loads(line) for line in raw.splitlines()]
    assert [e["action"] for e in events] == ["cut-rc", "promote"]
    assert attached.journal.unrecorded == []


def test_every_event_is_synced_after_its_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_write, real_sync = sink._write, sink._sync

    def write(fd: int, data: bytes | memoryview) -> int:
        calls.append("write")
        return real_write(fd, data)

    def sync(fd: int) -> None:
        calls.append("sync")
        real_sync(fd)

    monkeypatch.setattr(sink, "_write", write)
    monkeypatch.setattr(sink, "_sync", sync)
    attached = attach(JournalTarget(tmp_path / "j.jsonl", own_dir=False))
    try:
        AuditLogger().record("a")
        AuditLogger().record("b")
    finally:
        attached.detach()

    assert calls == ["write", "sync", "write", "sync"]


def test_a_failed_write_is_kept_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(fd: int, data: object) -> int:
        raise OSError(28, "No space left on device")

    attached = attach(JournalTarget(tmp_path / "j.jsonl", own_dir=False))
    monkeypatch.setattr(sink, "_write", fail)
    try:
        AuditLogger().record("a")  # must not raise into the service that recorded it
        AuditLogger().record("b")
    finally:
        attached.detach()

    assert [json.loads(line)["action"] for line in attached.journal.unrecorded] == ["a", "b"]
    assert (tmp_path / "j.jsonl").read_bytes() == b""


def test_a_failed_sync_counts_as_unrecorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(fd: int) -> None:
        raise OSError(5, "I/O error")

    attached = attach(JournalTarget(tmp_path / "j.jsonl", own_dir=False))
    monkeypatch.setattr(sink, "_sync", fail)
    try:
        AuditLogger().record("a")
    finally:
        attached.detach()

    assert len(attached.journal.unrecorded) == 1


def test_a_short_write_is_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = sink._write
    monkeypatch.setattr(sink, "_write", lambda fd, data: real(fd, bytes(data[:5])))
    path = tmp_path / "j.jsonl"
    attached = attach(JournalTarget(path, own_dir=False))
    try:
        AuditLogger().record("cut-rc")
    finally:
        attached.detach()

    assert json.loads(path.read_text())["action"] == "cut-rc"
    assert attached.journal.unrecorded == []


def test_a_write_that_makes_no_progress_is_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attached = attach(JournalTarget(tmp_path / "j.jsonl", own_dir=False))
    monkeypatch.setattr(sink, "_write", lambda fd, data: 0)
    try:
        AuditLogger().record("a")
    finally:
        attached.detach()

    assert len(attached.journal.unrecorded) == 1


def test_a_stock_handler_swallows_the_failure_this_one_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Calibration for the design: why ``Journal`` is not a ``logging.FileHandler``.

    The stock handler hands a failed write to ``handleError``, which prints to stderr and
    returns. The caller is never told, so a command built on it would exit 0 with the
    event unrecorded.
    """

    class FailingStream(io.StringIO):
        def write(self, s: str) -> int:
            raise OSError(28, "No space left on device")

    logger = logging.getLogger("gora.test.stock")
    logger.propagate = False
    stock = logging.StreamHandler(FailingStream())
    logger.addHandler(stock)
    try:
        logger.warning("an event")  # raises nothing
    finally:
        logger.removeHandler(stock)

    assert "Logging error" in capsys.readouterr().err  # reported to nobody who can act on it


def test_attach_and_detach_restore_the_logger(tmp_path: Path) -> None:
    before = (audit_logger.level, audit_logger.propagate, list(audit_logger.handlers))

    attached = attach(JournalTarget(tmp_path / "j.jsonl", own_dir=False))
    assert audit_logger.handlers == [attached.journal]
    assert audit_logger.propagate is False
    assert audit_logger.isEnabledFor(logging.INFO)  # it inherits WARNING otherwise
    attached.detach()

    assert (audit_logger.level, audit_logger.propagate, list(audit_logger.handlers)) == before


def test_events_after_detach_do_not_reach_the_file(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    attached = attach(JournalTarget(path, own_dir=False))
    AuditLogger().record("during")
    attached.detach()
    AuditLogger().record("after")

    assert [json.loads(line)["action"] for line in path.read_text().splitlines()] == ["during"]


def test_importing_the_sink_attaches_nothing() -> None:
    # Nothing is attached at import time; a handler exists only inside a mutating command.
    assert audit_logger.handlers == []


def test_event_lines_are_printable_ascii_so_they_can_be_shown_as_they_are() -> None:
    captured: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = Capture()
    audit_logger.addHandler(handler)
    old_level = audit_logger.level
    audit_logger.setLevel(logging.INFO)
    try:
        hostile = "a\x1b[31m\x7f\x85" + chr(0x202E) + chr(0x2028) + "\n\rb"
        event = AuditLogger().record("cut-rc", version=hostile)
    finally:
        audit_logger.setLevel(old_level)
        audit_logger.removeHandler(handler)

    (line,) = captured
    assert all(0x20 <= ord(c) < 0x7F for c in line), line
    assert json.loads(line)["details"]["version"] == hostile == event["details"]["version"]


# --- a torn line never merges with the next event --------------------------------------


def test_a_torn_write_breaks_the_journal_for_the_rest_of_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tear_first_write(monkeypatch)
    path = tmp_path / "j.jsonl"
    attached = attach(JournalTarget(path, own_dir=False))
    try:
        AuditLogger().record("first")
        AuditLogger().record("second")
    finally:
        attached.detach()

    # Every complete line parses; the second event was not written after the torn bytes.
    raw = path.read_bytes()
    assert [json.loads(line) for line in complete_lines(raw)] == []
    assert len(raw) == 10 and b"second" not in raw  # only the torn bytes of the first
    assert [json.loads(line)["action"] for line in attached.journal.unrecorded] == [
        "first",
        "second",
    ]


def test_a_failed_sync_breaks_the_journal_for_the_rest_of_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(fd: int) -> None:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(sink, "_sync", fail)
    path = tmp_path / "j.jsonl"
    attached = attach(JournalTarget(path, own_dir=False))
    try:
        AuditLogger().record("first")
        AuditLogger().record("second")
    finally:
        attached.detach()

    assert [json.loads(line)["action"] for line in path.read_text().splitlines()] == ["first"]
    assert [json.loads(line)["action"] for line in attached.journal.unrecorded] == [
        "first",
        "second",
    ]


def test_a_tail_without_a_newline_is_not_joined_by_the_next_event(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.write_bytes(b'{"action": "tor')  # a previous command died mid-line

    attached = attach(JournalTarget(path, own_dir=False))
    try:
        AuditLogger().record("one")
        AuditLogger().record("two")
    finally:
        attached.detach()

    raw = path.read_bytes()
    assert raw.startswith(b'{"action": "tor\n')  # existing bytes are neither truncated nor edited
    lines = raw.splitlines()
    assert lines[0] == b'{"action": "tor'
    assert [json.loads(line)["action"] for line in lines[1:]] == ["one", "two"]
    assert attached.journal.unrecorded == []


@pytest.mark.parametrize("existing", [b"", b'{"action": "old"}\n'], ids=["empty", "terminated"])
def test_no_newline_is_added_when_the_file_ends_properly(tmp_path: Path, existing: bytes) -> None:
    path = tmp_path / "j.jsonl"
    path.write_bytes(existing)

    attached = attach(JournalTarget(path, own_dir=False))
    try:
        AuditLogger().record("new")
    finally:
        attached.detach()

    raw = path.read_bytes()
    assert raw.startswith(existing)
    assert b"\n\n" not in raw
    assert not raw.startswith(b"\n")
    assert json.loads(raw[len(existing) :])["action"] == "new"


def test_a_fifo_with_a_reader_is_refused_as_not_regular(tmp_path: Path) -> None:
    fifo = tmp_path / "j.fifo"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with pytest.raises(OSError, match="not a regular file"):
            open_journal(JournalTarget(fifo, own_dir=False))
    finally:
        os.close(reader)


def test_a_fifo_with_no_reader_fails_at_once_instead_of_blocking(tmp_path: Path) -> None:
    """``O_NONBLOCK``: opening a FIFO for writing otherwise waits for a reader forever.

    A real process with a timeout: an open that blocks never returns, and a call in this
    one would hold the whole session until the test's own timeout.
    """
    fifo = tmp_path / "j.fifo"
    os.mkfifo(fifo)
    code = (
        "import errno\n"
        "from pathlib import Path\n"
        "from gora.audit.sink import JournalTarget, open_journal\n"
        "try:\n"
        f"    open_journal(JournalTarget(Path({str(fifo)!r}), own_dir=False))\n"
        "except OSError as exc:\n"
        "    print(errno.errorcode[exc.errno])\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(gora.__file__).parent.parent)}

    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ENXIO"  # no reader: refused at once, not waited for


def test_events_go_to_the_descriptor_held_not_to_the_path(tmp_path: Path) -> None:
    """The file is opened once. Moved away after the first event, it still takes the second;
    nothing is written at the old path, which a reopen would have created again."""
    path = tmp_path / "j.jsonl"
    moved = tmp_path / "moved.jsonl"
    attached = attach(JournalTarget(path, own_dir=False))
    try:
        AuditLogger().record("first")
        os.rename(path, moved)
        AuditLogger().record("second")
    finally:
        attached.detach()

    assert not path.exists()
    assert [json.loads(line)["action"] for line in moved.read_text().splitlines()] == [
        "first",
        "second",
    ]
    assert attached.journal.unrecorded == []

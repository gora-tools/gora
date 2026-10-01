"""The audit journal: where audit events go, and the sink that writes them (ADR-0017).

``AuditLogger`` emits each event on the ``gora.audit`` logger. Nothing here runs at
import time: the CLI attaches a ``Journal`` to that logger, lazily, for a mutating
command, and detaches it when the command ends. The API process never attaches one.

The handler is not a stock ``logging.FileHandler``. Stdlib handlers swallow a write
error in ``handleError`` and carry on, so a command whose journal write failed would
exit 0. ``Journal`` catches the failure itself and keeps the event, so the CLI can
inspect ``unrecorded`` before it exits.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

AUDIT_LOG_ENV = "GORA_AUDIT_LOG"
STATE_HOME_ENV = "XDG_STATE_HOME"

_AUDIT_LOGGER = "gora.audit"
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_GROUP_OTHER = 0o077


class JournalPathError(ValueError):
    """The journal location cannot be resolved from the environment (a usage error)."""


@dataclass(frozen=True)
class JournalTarget:
    """Where the journal is, and whether its directory is GORA's own.

    ``own_dir`` is true for the default locations, whose last directory is ``gora`` and
    is tightened to 0700 when wider. A ``GORA_AUDIT_LOG`` directory is the operator's
    choice: missing directories are created 0700, an existing one is left as it is.
    """

    path: Path
    own_dir: bool


def _absolute(value: str) -> bool:
    return os.path.isabs(value)


def resolve_journal(environ: Mapping[str, str]) -> JournalTarget:
    """The journal path: GORA_AUDIT_LOG > $XDG_STATE_HOME/gora/audit.jsonl >
    $HOME/.local/state/gora/audit.jsonl.

    ``GORA_AUDIT_LOG`` empty counts as unset; relative is an error and nothing is written.
    ``XDG_STATE_HOME`` empty or relative is ignored (XDG Base Directory spec). With no
    usable ``HOME`` either, there is no default, and the error says what to set.
    """
    override = environ.get(AUDIT_LOG_ENV, "")
    if override:
        if not _absolute(override):
            raise JournalPathError(f"{AUDIT_LOG_ENV} must be an absolute path, got {override}")
        return JournalTarget(Path(override), own_dir=False)

    state_home = environ.get(STATE_HOME_ENV, "")
    if state_home and _absolute(state_home):
        return JournalTarget(Path(state_home) / "gora" / "audit.jsonl", own_dir=True)

    home = environ.get("HOME", "")
    if home and _absolute(home):
        return JournalTarget(Path(home) / ".local" / "state" / "gora" / "audit.jsonl", own_dir=True)

    raise JournalPathError(
        f"cannot locate the audit journal: HOME is not an absolute path; set {AUDIT_LOG_ENV}"
    )


def _write(fd: int, data: bytes | memoryview) -> int:
    """The one place an event reaches the file; tests replace it to fail a write."""
    return os.write(fd, data)


def _sync(fd: int) -> None:
    os.fsync(fd)


def _ensure_dir(directory: Path, *, tighten: bool) -> None:
    """Create missing directories 0700 (``makedirs`` would leave parents at the umask)."""
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        try:
            path.mkdir(mode=_DIR_MODE)
        except FileExistsError:
            if not path.is_dir():
                raise
            continue  # another gora created it first
        path.chmod(_DIR_MODE)  # mkdir's mode is masked by the umask
    if tighten and directory.stat().st_mode & _GROUP_OTHER:
        directory.chmod(_DIR_MODE)


def open_journal(target: JournalTarget) -> int:
    """Open the journal for appending and return the descriptor.

    O_WRONLY|O_APPEND|O_CREAT, mode 0600, tightened if the file was wider. Anything but a
    regular file is refused: ``/dev/null`` would silence the journal and still look open.
    O_NONBLOCK makes opening a FIFO with no reader fail at once instead of waiting for one
    forever; it changes nothing for a regular file. Raises ``OSError``; the caller holds
    the descriptor for the whole command.
    """
    _ensure_dir(target.path.parent, tighten=target.own_dir)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK
    fd = os.open(target.path, flags, _FILE_MODE)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        if info.st_mode & _GROUP_OTHER:
            os.fchmod(fd, _FILE_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def torn_tail(path: Path) -> bool:
    """True if the file is non-empty and its last byte is not a newline.

    A previous command died in the middle of a line. A file that cannot be read counts as
    torn too: a newline in front of a clean tail is a blank line, a missing one merges two
    events.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return True
    try:
        size = os.fstat(fd).st_size
        return size > 0 and os.pread(fd, 1, size - 1) != b"\n"
    except OSError:
        return True
    finally:
        os.close(fd)


class Journal(logging.Handler):
    """Appends each audit event to the journal as one newline-terminated JSON line.

    One write per event, then fsync. A failure never propagates into the service or
    adapter that recorded the event: it is kept in ``unrecorded``, in order, and the
    CLI reports it before it exits. The descriptor is held until ``close``.

    After a failed write or fsync the journal is broken for the rest of the command: the
    tail of the file is unknown (a write may have landed part of a line), so a later event
    written after it could merge with it into one unreadable line. Every later event goes
    to ``unrecorded`` and nothing more is written. Across commands, ``newline_first`` makes
    the first event start with a newline when the file already ends mid-line.
    """

    def __init__(self, fd: int, path: Path, *, newline_first: bool = False) -> None:
        super().__init__(level=logging.INFO)
        self.path = path
        self.unrecorded: list[str] = []
        self._fd: int | None = fd
        self._newline_first = newline_first
        self._broken = False

    def emit(self, record: logging.LogRecord) -> None:
        line = record.getMessage()
        if self._broken:
            self.unrecorded.append(line)
            return
        try:
            if self._fd is None:
                raise OSError(errno.EBADF, "journal is closed")
            lead = b"\n" if self._newline_first else b""
            data = memoryview(lead + (line + "\n").encode("utf-8"))
            while data:
                written = _write(self._fd, data)
                if written <= 0:
                    raise OSError(errno.EIO, "journal write made no progress")
                data = data[written:]
            _sync(self._fd)
            self._newline_first = False
        except Exception:
            self._broken = True
            self.unrecorded.append(line)

    def close(self) -> None:
        self.acquire()
        try:
            fd, self._fd = self._fd, None
            if fd is not None:
                # Each event was fsynced as it was written; a failing close loses nothing.
                with contextlib.suppress(OSError):
                    os.close(fd)
        finally:
            self.release()
        super().close()


class Attached:
    """A journal attached to ``gora.audit``; ``detach`` restores the logger."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        logger = logging.getLogger(_AUDIT_LOGGER)
        self._level = logger.level
        self._propagate = logger.propagate
        logger.setLevel(logging.INFO)  # the logger inherits WARNING, which drops every event
        logger.propagate = False
        logger.addHandler(journal)

    def detach(self) -> None:
        logger = logging.getLogger(_AUDIT_LOGGER)
        logger.removeHandler(self.journal)
        logger.setLevel(self._level)
        logger.propagate = self._propagate
        self.journal.close()


def attach(target: JournalTarget) -> Attached:
    """Open the journal and attach it to ``gora.audit``. Raises ``OSError``."""
    fd = open_journal(target)
    return Attached(Journal(fd, target.path, newline_first=torn_tail(target.path)))

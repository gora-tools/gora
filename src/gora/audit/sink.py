"""The audit journal: where audit events go, and the sink that writes them (ADR-0017).

``AuditLogger`` emits each event on the ``gora.audit`` logger. Nothing here runs at
import time: a mutating command attaches a ``Journal`` to that logger, lazily, and
detaches it when the command ends. No command does until ``gora register``
(release-model section 13). The API process never attaches one.

A process that runs without a person, the release repository's pipeline and GORA's server,
writes the journal to standard error instead: ``GORA_AUDIT_LOG=-`` selects the ``Stream``
(ADR-027 section 3). It is file descriptor 2, written as it is: no path, no file, no
regular-file check, no ``fsync``, no last byte to check. The stream's tail is never known, since
another writer may have left a line unterminated, so every event starts with a newline, in the
same write as the event, under the handler's lock. A write that lands fewer bytes than that is
a failure, as one that raises is, so a fragment is never left in the stream unnoticed; after a
failure the stream is broken for the rest of the command, as the file is. Before anything is
attached, the stream is checked: open for writing, blocking, and not the null device.

The handler is not a stock ``logging.FileHandler``. Stdlib handlers swallow a write
error in ``handleError`` and carry on, so a command whose journal write failed would
exit 0. ``Journal`` catches the failure itself and keeps the event, so a command can
inspect ``unrecorded`` before it exits.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
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
STREAM = "-"  # the value of GORA_AUDIT_LOG that selects standard error
STDERR = 2

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


@dataclass(frozen=True)
class StreamTarget:
    """The stream: standard error, file descriptor 2, never a path (ADR-027 section 3)."""

    fd: int = STDERR


def _absolute(value: str) -> bool:
    return os.path.isabs(value)


def resolve_journal(environ: Mapping[str, str]) -> JournalTarget | StreamTarget:
    """The journal path: GORA_AUDIT_LOG > $XDG_STATE_HOME/gora/audit.jsonl >
    $HOME/.local/state/gora/audit.jsonl; or the stream, for ``GORA_AUDIT_LOG=-``.

    ``GORA_AUDIT_LOG`` empty counts as unset; ``-`` is standard error, never a path; relative
    is an error and nothing is written.
    ``XDG_STATE_HOME`` empty or relative is ignored (XDG Base Directory spec). With no
    usable ``HOME`` either, there is no default, and the error says what to set.
    """
    override = environ.get(AUDIT_LOG_ENV, "")
    if override == STREAM:
        return StreamTarget()
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
    command reports it before it exits. The descriptor is held until ``close``.

    After a failed write or fsync the journal is broken for the rest of the command: the
    tail of the file is unknown (a write may have landed part of a line), so a later event
    written after it could merge with it into one unreadable line. Every later event goes
    to ``unrecorded`` and nothing more is written. Across commands, ``newline_first`` makes
    the first event start with a newline when the file already ends mid-line.
    """

    def __init__(self, fd: int, path: Path | None, *, newline_first: bool = False) -> None:
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
            self._put(self._fd, memoryview(lead + (line + "\n").encode("utf-8")))
            self._newline_first = False
        except Exception:
            self._broken = True
            self.unrecorded.append(line)

    def _put(self, fd: int, data: memoryview) -> None:
        """Write one event to the file: a short write is completed, then the file is synced."""
        while data:
            written = _write(fd, data)
            if written <= 0:
                raise OSError(errno.EIO, "journal write made no progress")
            data = data[written:]
        _sync(fd)

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


class Stream(Journal):
    """The journal as standard error: one write per event, and no ``fsync`` (ADR-027 section 3).

    Each event starts with a newline, in the same write: the stream's tail is never known, and
    an event after another writer's unterminated text would merge with it into one line no
    reader keeps (ADR-017 rule 2, before every event). A write that lands fewer bytes than the
    event is a failure, as one that raises is: a pipe's reader would get a fragment, and the
    next event appended to it would be lost with it. Either breaks the stream for the rest of
    the command, as for the file. The descriptor is the process's standard error: it is never
    closed. A stream has no path.
    """

    def __init__(self, fd: int = STDERR) -> None:
        super().__init__(fd, None)

    def _put(self, fd: int, data: memoryview) -> None:
        event = b"\n" + bytes(data)
        if _write(fd, event) != len(event):
            raise OSError(errno.EIO, "a short write to the stream")

    def close(self) -> None:
        self.acquire()
        try:
            self._fd = None
        finally:
            self.release()
        logging.Handler.close(self)


NULL_DEVICE = (
    "standard error is the null device, which is refused, as it is for the file (ADR-017); a"
    " sink that swallows events behind a pipe cannot be seen, and is the platform's to keep"
)
NON_BLOCKING = (
    "standard error is non-blocking, and an event would fail under backpressure instead of"
    " waiting; send it to a file (2> file) or through a pipe (| cat)"
)


def check_stream(fd: int) -> None:
    """Raise ``OSError`` unless ``fd`` is a stream the journal can trust, checked before any
    mutation as the file's open is (ADR-017, failure policy rule 1): open, for writing,
    blocking, and not the null device, which would swallow every event and still look open.
    The device is compared by its number, since the stream has no path."""
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        info = os.fstat(fd)
    except OSError as error:
        raise OSError(error.errno, f"standard error is not open: {error.strerror}") from None
    if flags & os.O_ACCMODE not in (os.O_WRONLY, os.O_RDWR):
        raise OSError(errno.EBADF, "standard error is not open for writing")
    if flags & os.O_NONBLOCK:
        raise OSError(errno.EAGAIN, NON_BLOCKING)
    null = os.stat(os.devnull)
    if stat.S_ISCHR(info.st_mode) and info.st_rdev == null.st_rdev:
        raise OSError(errno.EINVAL, NULL_DEVICE)


def attach(target: JournalTarget | StreamTarget) -> Attached:
    """Open the journal, or check the stream, and attach it to ``gora.audit``. Raises
    ``OSError``."""
    if isinstance(target, StreamTarget):
        check_stream(target.fd)
        return Attached(Stream(target.fd))
    fd = open_journal(target)
    return Attached(Journal(fd, target.path, newline_first=torn_tail(target.path)))

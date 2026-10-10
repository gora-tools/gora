"""The CLI shows every value literally, whatever the value contains.

``gora version`` prints two values it reads from the environment, the revision and the
version, and a usage error quotes the argument that caused it. Each renders literally: it
cannot raise a markup error, produce a style or a link, or be swallowed. A character the
terminal acts on or renders as nothing — Unicode categories Cc, Cf, Zl and Zp — is shown as
an escape, and a backslash as two, so no value can move the cursor, start a line, or pass
for another. Results go to stdout, usage errors to stderr, and no line is wrapped or folded
on either stream, tty or not.

An argument a command does not take is a usage error, exit 2, the argument shown escaped
(the CLI output contract, #40 and #42). The group lists ``version`` among its commands. The
matrix below runs every hostile value through every path a value can reach.
"""

import os
import pty
import re
import select
import struct
import subprocess
import sys
import time
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import typer.rich_utils
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner, Result

import gora.transport.cli.commands as commands
from gora.transport.cli.commands import app

# Narrower than any line below, so a console that wraps cannot pass unnoticed.
NARROW = 40

HOSTILE = {
    "close-tag": "[/x]",
    "style-tag": "[bold]",
    "bracketed": "dev[eu]",
    "link": "[link=https://example.invalid]x[/link]",
    "long": "x" * 150,
    "emoji-code": ":warning:",
    # What a terminal acts on: escape sequences, line and cell movement, C1, bidi.
    "sgr": "\x1b[31mred\x1b[0m",
    "clear-screen": "dev\x1b[2J\x1b[Hfake",
    "osc8-link": "\x1b]8;;https://example.invalid/\x1b\\link\x1b]8;;\x1b\\",
    "osc-title-bel": "\x1b]0;title\x07x",
    "lf-forge": "dev\nPromoted to prod",
    "cr-overwrite": "dev\rPromoted to prod",
    "crlf": "dev\r\nfake",
    "tab": "a\tb",
    "backspace": "prod\x08\x08\x08\x08dev",
    "bel": "x\x07",
    "vt-ff": "a\x0bb\x0cc",
    "c0-bounds": "\x01x\x1f",
    "del": "x\x7fy",
    "c1-csi": "\x9b2J",
    "c1-nel-osc": "a\x85b\x9d0;t\x9c",
    "c1-bounds": "\x80x\x9f",
    "bidi-rlo": "\u202eved",
    "bidi-all": "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069x",
    "markup-and-esc": "[bold]\x1b[1m",
    "nul": "x\x00y",
    # Next to the escaped ranges, and a backslash: each is shown as it is.
    # Invisible without being a C0, C1 or bidi control: format characters and separators.
    "zero-width-space": "de\u200bv",
    "line-separator": "dev\u2028Promoted to prod",
    "paragraph-separator": "dev\u2029Promoted to prod",
    "lrm-rlm": "a\u200eb\u200fc",
    "arabic-letter-mark": "a\u061cb",
    "bom": "\ufeffdev",
    "soft-hyphen": "de\u00adv",
    "tag-above-bmp": "dev\U000e0041",
    # Text that would read as an escape if a backslash were shown as it is.
    "backslash": "a\\x1bb",
    # Shown as they are: a symbol outside the BMP, spaces and letters that are visible.
    "emoji": "\U0001f600",
    "not-controls": " ~\u00a0\u00e9\u202f",
}

# The one value an argv element or an environment variable cannot hold.
NOT_ARGV = {"nul"}

# A Select Graphic Rendition sequence: colour and weight, the only escape output carries.
SGR = re.compile(r"\x1b\[[0-9;]*m")
# Unicode general categories the CLI must never print raw: controls, format characters,
# line and paragraph separators.
INVISIBLE = {"Cc", "Cf", "Zl", "Zp"}


def _shown(text: str) -> str:
    """How the CLI must show ``text``: a backslash as two; each invisible character as
    ``\\xNN`` below U+0100, ``\\uNNNN`` up to U+FFFF, else ``\\UNNNNNNNN``, lowercase hex;
    every other character as it is."""
    out = []
    for char in text:
        code = ord(char)
        if char == "\\":
            out.append("\\\\")
        elif unicodedata.category(char) not in INVISIBLE:
            out.append(char)
        elif code < 0x100:
            out.append(f"\\x{code:02x}")
        elif code <= 0xFFFF:
            out.append(f"\\u{code:04x}")
        else:
            out.append(f"\\U{code:08x}")
    return "".join(out)


def _raw_controls(stream: str) -> list[str]:
    """The invisible characters in ``stream`` other than the LF that ends each line."""
    return [c for c in stream.replace("\n", "") if unicodedata.category(c) in INVISIBLE]


runner = CliRunner()


@dataclass
class Case:
    """One command path: what to run, and where its output must appear."""

    args: list[str]
    exit_code: int
    stream: str  # "stdout" or "stderr": where `lines` must appear; the other stays empty
    lines: list[str]


@pytest.fixture
def narrow(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Both consoles report a width narrower than the lines they print."""
    monkeypatch.setenv("COLUMNS", str(NARROW))
    assert commands.console.width == NARROW, "console width did not follow COLUMNS"
    assert commands.err_console.width == NARROW, "err_console width did not follow COLUMNS"
    yield


# --- the matrix: every command path a value can reach ------------------------------


def _version_revision(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    mp.setenv("GORA_REVISION", v)
    return Case(["version"], 0, "stdout", [f"revision: {v}", "version:  not set"])


def _version_version(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    mp.setenv("GORA_VERSION", v)
    return Case(["version"], 0, "stdout", ["revision: unknown", f"version:  {v}"])


PATHS: dict[str, Callable[[Path, str, pytest.MonkeyPatch], Case]] = {
    "version-revision": _version_revision,
    "version-version": _version_version,
}


def _matrix(paths: list[str]) -> list[Any]:
    """Every (path, value) pair a real invocation can produce."""
    return [
        pytest.param(path, value, id=f"{path}-{name}")
        for path in paths
        for name, value in HOSTILE.items()
        if name not in NOT_ARGV
    ]


def _assert_case(result: Result, case: Case) -> None:
    out, err = result.stdout, result.stderr
    assert result.exit_code == case.exit_code, f"stdout={out!r} stderr={err!r}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"{type(result.exception).__name__}: {result.exception}"
    )
    assert _raw_controls(out + err) == [], f"stdout={out!r} stderr={err!r}"
    shown, other = (out, err) if case.stream == "stdout" else (err, out)
    lines = shown.split("\n")
    for line in map(_shown, case.lines):
        assert line in lines, f"{line!r} is not a line of {case.stream}: {shown!r}"
    assert other == "", f"unexpected output on the other stream: {other!r}"


@pytest.mark.usefixtures("narrow")
@pytest.mark.parametrize(("path_name", "value"), _matrix(list(PATHS)))
def test_value_renders_literally(
    path_name: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = PATHS[path_name](tmp_path, value, monkeypatch)
    _assert_case(runner.invoke(app, case.args), case)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["version", "{v}"], "Got unexpected extra argument(s) ({v})"),
        # Click reads `--name=value`, so only what precedes a `=` is the option's name.
        (["version", "--x{v}"], "No such option: --x{name}"),
        (["{v}"], "No such command '{v}'."),
    ],
    ids=["extra-argument", "unknown-option", "unknown-command"],
)
@pytest.mark.parametrize(
    "value", [pytest.param(v, id=k) for k, v in HOSTILE.items() if k not in NOT_ARGV]
)
# Typer colours its error box when GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS is set at
# import; both modes run here, whatever the machine's environment.
@pytest.mark.parametrize("force_terminal", [None, True], ids=["plain", "coloured"])
def test_usage_error_shows_the_argument_escaped(
    args: list[str],
    message: str,
    value: str,
    force_terminal: bool | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Click quotes the offending argument in its error; it is shown like any value.

    Wide enough that Typer's error box does not wrap the message: its layout is
    Typer's own, and only the escaping is in question here.
    """
    monkeypatch.setenv("COLUMNS", "1000")
    monkeypatch.setattr(typer.rich_utils, "FORCE_TERMINAL", force_terminal)

    result = runner.invoke(app, [a.format(v=value) for a in args])

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert ("\x1b[" in result.stderr) == bool(force_terminal), result.stderr
    # Typer's own colours are the only escape sequences; nothing else acts on the terminal.
    stderr = SGR.sub("", result.stderr)
    assert _raw_controls(stderr) == [], result.stderr
    shown = _shown(message.format(v=value, name=value.partition("=")[0]))
    assert shown in stderr, result.stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["nosuch"], "No such command 'nosuch'."),
        (["versio"], "No such command 'versio'. Did you mean 'version'?"),
        # Quoted as Python quotes a string, which is how Typer has always shown the name.
        (["it's"], 'No such command "it\'s".'),
        # After `--` what looks like an option is still parsed as one, by Typer.
        (["--", "--x"], "No such option: --x"),
    ],
    ids=["no-match", "close-match", "apostrophe", "after-double-dash"],
)
def test_unknown_command_is_shown_as_typer_shows_it(
    args: list[str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The matrix above runs hostile names; an ordinary one reads as it always did."""
    monkeypatch.setenv("COLUMNS", "1000")

    result = runner.invoke(app, args)

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert message in SGR.sub("", result.stderr), result.stderr


def test_every_command_refuses_its_leftover_arguments_itself() -> None:
    """The matrix above runs `version` only; every command must be built the same way.

    Click's own check of leftover arguments quotes them as the installed Typer escapes
    them, which differs between versions. A command registered without ``cls=_Command``
    would take that path, and show a backslash differently from every other command.
    """
    group = get_command(app)
    assert isinstance(group, TyperGroup)
    assert group.commands, "the app registers no command"
    stray = [n for n, c in group.commands.items() if not isinstance(c, commands._Command)]
    assert stray == [], f"commands that do not refuse their leftover arguments themselves: {stray}"


def test_the_help_lists_version_as_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``version`` the only command, Typer would collapse the app into it (a group stays
    one through its callback): ``gora version`` would be an unexpected argument, and ``--help``
    would show that command's help, with no Commands entry."""
    monkeypatch.setenv("COLUMNS", "1000")

    helped = runner.invoke(app, ["--help"])
    ran = runner.invoke(app, ["version"])

    assert helped.exit_code == 0, helped.output
    # The word alone also appears in the help text of the command and of the group.
    entry = re.compile(r"^\W*version\s+Show the source commit")
    assert any(entry.search(line) for line in helped.stdout.splitlines()), helped.stdout
    assert ran.exit_code == 0, ran.output


@pytest.mark.usefixtures("narrow")
def test_version_shows_the_revision_and_the_version_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv("GORA_REVISION", revision)
    monkeypatch.setenv("GORA_VERSION", "0.2.0-rc.7")

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"revision: {revision}\nversion:  0.2.0-rc.7\n"
    assert result.stderr == ""


@pytest.mark.usefixtures("narrow")
def test_version_outside_a_release_image() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == "revision: unknown\nversion:  not set\n"
    assert result.stderr == ""


# --- a real terminal: still literal, still unwrapped ---------------------------------

OSC = "\x1b]"


def _read_all(fds: list[int], timeout: float = 60) -> list[bytes]:
    """Drain every fd at once: a child blocked writing one would never close the other."""
    chunks: dict[int, list[bytes]] = {fd: [] for fd in fds}
    open_fds = set(fds)
    deadline = time.monotonic() + timeout
    while open_fds:
        ready, _, _ = select.select(list(open_fds), [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            raise TimeoutError("the CLI did not finish writing")
        for fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # EIO once the child closes its end
                chunk = b""
            if chunk:
                chunks[fd].append(chunk)
            else:
                open_fds.discard(fd)
    return [b"".join(chunks[fd]) for fd in fds]


def _run_on_tty(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run the real CLI with stdout and stderr each on its own narrow terminal."""
    import fcntl
    import termios

    masters, slaves = [], []
    for _ in range(2):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, NARROW, 0, 0))
        masters.append(master)
        slaves.append(slave)
    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES", "NO_COLOR")}
    env["TERM"] = "xterm-256color"
    proc = subprocess.Popen(
        [sys.executable, "-m", "gora.transport.cli.commands", *args],
        stdin=subprocess.DEVNULL,
        stdout=slaves[0],
        stderr=slaves[1],
        cwd=cwd,
        env=env,
    )
    for slave in slaves:
        os.close(slave)
    # The pty turns each LF into CRLF; a CR the CLI wrote is left for the caller to find.
    out, err = (raw.decode().replace("\r\n", "\n") for raw in _read_all(masters))
    for master in masters:
        os.close(master)
    return proc.wait(timeout=60), out, err


TTY_PATHS = ["version-revision", "version-version"]


@pytest.fixture(scope="module")
def colour_capable_pty(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Calibration, once: ``--help`` is styled on this pty.

    ``version`` prints with no style, so its output holding no escape sequence proves
    nothing by itself. This shows the terminal below is one that would have shown colour.

    A module fixture is set up before the test's own ``_isolated_home``, so the child would
    start with the real home and journal: it gets its own, under this fixture's directory
    (TESTING rule 6).
    """
    root = tmp_path_factory.mktemp("calibration")
    (root / "home").mkdir()
    with pytest.MonkeyPatch.context() as isolated:
        isolated.setenv("HOME", str(root / "home"))
        isolated.setenv("XDG_STATE_HOME", str(root / "state"))
        isolated.setenv("XDG_CONFIG_HOME", str(root / "config"))
        isolated.setenv("GORA_AUDIT_LOG", str(root / "audit" / "audit.jsonl"))
        code, out, err = _run_on_tty(["--help"], cwd=root)
    assert code == 0, f"stdout={out!r} stderr={err!r}"
    assert "\x1b[" in out + err, "expected a color-capable terminal"


@pytest.mark.usefixtures("colour_capable_pty")
@pytest.mark.parametrize(("path_name", "value"), _matrix(TTY_PATHS))
def test_value_renders_literally_on_a_tty(
    path_name: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = PATHS[path_name](tmp_path, value, monkeypatch)

    code, out, err = _run_on_tty(case.args, cwd=tmp_path)

    assert OSC not in out + err, "output carries a terminal hyperlink"
    assert "Traceback" not in out + err
    assert code == case.exit_code, f"stdout={out!r} stderr={err!r}"
    # GORA's own colours are the only escape sequences; nothing else acts on the terminal.
    assert _raw_controls(SGR.sub("", out + err)) == [], f"stdout={out!r} stderr={err!r}"
    shown = SGR.sub("", out if case.stream == "stdout" else err)
    lines = shown.split("\n")
    for line in map(_shown, case.lines):
        assert line in lines, f"{line!r} is not a line of {case.stream}: {shown!r}"

"""The CLI reports exactly what happened, whatever the values it reports contain.

Every value a command interpolates — an argument, manifest content, an exception's
text, a path, a URL — renders literally: it cannot raise a markup error, produce a
style or a link, or be swallowed. Messages on a non-zero exit path go to stderr,
results to stdout, and no line is wrapped or folded on either stream, tty or not.
"""

import hashlib
import os
import pty
import re
import select
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import Result
from typer.testing import CliRunner

import gora.adapters.github as github
import gora.transport.cli.commands as commands
from gora.adapters.github import GitHubError, PullRequest
from gora.domain.release.manifest import ReleaseManifest
from gora.services.notifier import Notifier
from gora.transport.cli.commands import app

DIGEST_A = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIGEST_B = "sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
IMAGE = "ghcr.io/test/service"
DIGEST_PATTERN_ERROR = "  digest: String should match pattern '^sha256:[a-f0-9]{64}$'"

# Narrower than any line below, so a console that wraps cannot pass unnoticed.
NARROW = 40

HOSTILE = {
    "close-tag": "[/x]",
    "style-tag": "[bold]",
    "bracketed": "dev[eu]",
    "link": "[link=https://example.invalid]x[/link]",
    "long": "x" * 150,
    "emoji-code": ":warning:",
}

runner = CliRunner()


@dataclass
class Case:
    """One command path: what to run, and where and how its output must appear."""

    args: list[str]
    exit_code: int
    stream: str  # "stdout" or "stderr": where `lines` must appear
    lines: list[str]
    # Each tuple must appear together on one line (a table row).
    together: list[tuple[str, ...]] = field(default_factory=list)
    other_stream_empty: bool = True
    check: Callable[[], None] | None = None


def _seed(path: Path, **overrides: Any) -> None:
    """Write a manifest as a document, so any value can reach any field."""
    doc: dict[str, Any] = {
        "version": "v0.1",
        "release": "1.2.0",
        "candidate": {"version": "1.2.0-rc.1", "image": IMAGE, "digest": DIGEST_A},
        "stages": {"dev": {"version": "1.2.0-rc.1", "digest": DIGEST_A}},
    }
    doc.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _hostile_path(tmp_path: Path, value: str) -> Path:
    """A manifest path that contains the value; a `/` in it nests a directory."""
    return tmp_path / "m" / value / "release-manifest.yaml"


class FakeGitHub:
    """Stands in for GitHubAdapter: no network, a PR URL built from the repo."""

    def __init__(self, repo: str, token: str) -> None:
        self._repo = repo

    def get_ref_sha(self, branch: str) -> str:
        if branch.startswith("gora/promote-"):
            raise GitHubError("404 Not Found")
        if branch != "main":
            raise GitHubError(f"base branch not found: {branch}")
        return "basesha"

    def create_branch(self, name: str, from_sha: str) -> None:
        pass

    def get_file_sha(self, path: str, branch: str) -> str | None:
        return None

    def commit_file(
        self, path: str, content: str, branch: str, message: str, *, sha: str | None = None
    ) -> str:
        return "commitsha"

    def find_open_pull_request(self, head: str) -> PullRequest | None:
        return None

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
        url = f"https://github.com/{self._repo}/pull/42"
        return PullRequest(number=42, url=url, state="open", head=head, base=base, title=title)


@pytest.fixture
def narrow(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Both consoles report a width narrower than the lines they print."""
    monkeypatch.setenv("COLUMNS", str(NARROW))
    assert commands.console.width == NARROW, "console width did not follow COLUMNS"
    assert commands.err_console.width == NARROW, "err_console width did not follow COLUMNS"
    yield


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "GitHubAdapter", FakeGitHub)
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "test-token")


def _failing_notifier(text: str) -> Callable[[object], Notifier]:
    class _Poster:
        def post_message(self, channel: str, message: str) -> object:
            raise RuntimeError(text)

    return lambda _settings: Notifier(_Poster(), "C1", enabled=True)  # type: ignore[arg-type]


# --- the matrix: every command path a value can reach ------------------------------


def _status_no_manifest(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = _hostile_path(tmp, v)
    return Case(["status", "-m", str(path)], 1, "stderr", [f"No manifest found at {path}"])


def _status_unloadable_path(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = _hostile_path(tmp, v)
    path.parent.mkdir(parents=True)
    path.write_text("- a list\n", encoding="utf-8")
    line = f"Cannot load manifest {path}: expected a mapping at the top level, got a sequence"
    return Case(["status", "-m", str(path)], 1, "stderr", [line])


def _status_unknown_field(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path, **{v: "y"})
    line = f"Unsupported manifest {path}: fields not defined by schema v0.1: {v}"
    return Case(["status", "-m", str(path)], 1, "stderr", [line])


def _status_manifest_content(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(
        path,
        release=v,
        candidate={"version": v, "image": v, "digest": DIGEST_A},
        stages={v: {"version": v, "digest": DIGEST_B}},
    )
    lines = [f"Release:  {v}", f"Candidate: {v}", f"  digest: {DIGEST_A}", f"  image:  {v}"]
    return Case(["status", "-m", str(path)], 0, "stdout", lines, together=[(v, v, DIGEST_B)])


def _cut_rc_image(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    args = ["cut-rc", "-i", v, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-m", str(path)]
    return Case(args, 0, "stdout", [f"  image:   {v}", f"  digest:  {DIGEST_A}"])


def _cut_rc_version(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    args = ["cut-rc", "-i", IMAGE, "-v", v, "-d", DIGEST_A, "-m", str(path)]
    return Case(args, 0, "stdout", [f"Cut release candidate {v}"])


def _cut_rc_release(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    args = ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-r", v, "-m", str(path)]
    return Case(args, 0, "stdout", [f"  release: {v}"])


def _cut_rc_path(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = _hostile_path(tmp, v)
    path.parent.mkdir(parents=True)
    args = ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-m", str(path)]
    return Case(args, 0, "stdout", [f"  created new manifest: {path}"])


def _cut_rc_bad_digest(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    args = ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", v, "-m", str(path)]
    lines = ["Invalid release candidate:", DIGEST_PATTERN_ERROR]
    return Case(args, 1, "stderr", lines)


def _cut_rc_release_mismatch(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    args = ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.2", "-d", DIGEST_B, "-r", v, "-m", str(path)]
    line = f"Release line mismatch: manifest is '1.2.0', requested '{v}'"
    return Case(args, 1, "stderr", [line])


def _cut_rc_manifest_release(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path, release=v)
    args = ["cut-rc", "-i", IMAGE, "-v", "9.9.9", "-d", DIGEST_B, "-r", "9.9.9", "-m", str(path)]
    line = f"Release line mismatch: manifest is '{v}', requested '9.9.9'"
    return Case(args, 1, "stderr", [line])


def _promote_stage(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)

    def persisted() -> None:
        assert ReleaseManifest.from_yaml(path).stages[v].digest == DIGEST_A

    return Case(["promote", v, "-m", str(path)], 0, "stdout", [f"Promoted to {v}"], check=persisted)


def _promote_manifest_version(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path, candidate={"version": v, "image": IMAGE, "digest": DIGEST_A})
    return Case(["promote", "dev", "-m", str(path)], 0, "stdout", [f"  version: {v}"])


def _promote_no_manifest(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = _hostile_path(tmp, v)
    line = f"No manifest at {path}; run cut-rc first"
    return Case(["promote", "dev", "-m", str(path)], 1, "stderr", [line])


def _promote_notify_failed(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    mp.setattr(commands, "build_notifier", _failing_notifier(v))
    args = ["promote", "dev", "-m", str(path)]
    case = Case(args, 0, "stderr", [f"  (slack notify failed: {v})"], other_stream_empty=False)
    return case


def _open_pr_stage(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    args = ["promote", v, "--open-pr", "--repo", "o/r", "-m", str(path)]
    line = f"  1.2.0-rc.1 to {v}, digest {DIGEST_A}"
    return Case(args, 0, "stdout", [line])


def _open_pr_manifest_version(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path, candidate={"version": v, "image": IMAGE, "digest": DIGEST_A})
    args = ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", str(path)]
    return Case(args, 0, "stdout", [f"  {v} to dev, digest {DIGEST_A}"])


def _open_pr_repo(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    args = ["promote", "dev", "--open-pr", "--repo", v, "-m", str(path)]
    line = f"Opened promotion PR #42 https://github.com/{v}/pull/42"
    return Case(args, 0, "stdout", [line])


def _open_pr_base(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    args = ["promote", "dev", "--open-pr", "--repo", "o/r", "--base", v, "-m", str(path)]
    return Case(args, 1, "stderr", [f"base branch not found: {v}"])


def _open_pr_path(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = _hostile_path(tmp, v)
    args = ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", str(path)]
    line = f"Cannot load manifest {path}: cannot read file: No such file or directory"
    return Case(args, 1, "stderr", [line])


def _rollback_stage(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    args = ["rollback", v, "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(path)]
    return Case(args, 0, "stdout", [f"Rolled back {v}", f"  digest:  {DIGEST_B}"])


def _rollback_version(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    args = ["rollback", "dev", "--to-version", v, "--to-digest", DIGEST_B, "-m", str(path)]
    return Case(args, 0, "stdout", [f"  version: {v}"])


def _rollback_bad_digest(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = tmp / "release-manifest.yaml"
    _seed(path)
    args = ["rollback", "dev", "--to-version", "1.1.9", "--to-digest", v, "-m", str(path)]
    return Case(args, 1, "stderr", ["Invalid rollback target:", DIGEST_PATTERN_ERROR])


def _rollback_no_manifest(tmp: Path, v: str, mp: pytest.MonkeyPatch) -> Case:
    path = _hostile_path(tmp, v)
    args = ["rollback", "dev", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(path)]
    return Case(args, 1, "stderr", [f"No manifest at {path}; nothing to roll back"])


PATHS: dict[str, Callable[[Path, str, pytest.MonkeyPatch], Case]] = {
    "status-no-manifest": _status_no_manifest,
    "status-unloadable-path": _status_unloadable_path,
    "status-unknown-field": _status_unknown_field,
    "status-manifest-content": _status_manifest_content,
    "cut-rc-image": _cut_rc_image,
    "cut-rc-version": _cut_rc_version,
    "cut-rc-release": _cut_rc_release,
    "cut-rc-path": _cut_rc_path,
    "cut-rc-bad-digest": _cut_rc_bad_digest,
    "cut-rc-release-mismatch": _cut_rc_release_mismatch,
    "cut-rc-manifest-release": _cut_rc_manifest_release,
    "promote-stage": _promote_stage,
    "promote-manifest-version": _promote_manifest_version,
    "promote-no-manifest": _promote_no_manifest,
    "promote-notify-failed": _promote_notify_failed,
    "open-pr-stage": _open_pr_stage,
    "open-pr-manifest-version": _open_pr_manifest_version,
    "open-pr-repo": _open_pr_repo,
    "open-pr-base": _open_pr_base,
    "open-pr-path": _open_pr_path,
    "rollback-stage": _rollback_stage,
    "rollback-version": _rollback_version,
    "rollback-bad-digest": _rollback_bad_digest,
    "rollback-no-manifest": _rollback_no_manifest,
}


def _assert_case(result: Result, case: Case) -> None:
    out, err = result.stdout, result.stderr
    assert result.exit_code == case.exit_code, f"stdout={out!r} stderr={err!r}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"{type(result.exception).__name__}: {result.exception}"
    )
    shown, other = (out, err) if case.stream == "stdout" else (err, out)
    lines = shown.splitlines()
    for line in case.lines:
        assert line in lines, f"{line!r} is not a line of {case.stream}: {shown!r}"
    for parts in case.together:
        assert any(all(p in line for p in parts) for line in lines), (
            f"{parts!r} not together on one line of {case.stream}: {shown!r}"
        )
    if case.other_stream_empty:
        assert other == "", f"unexpected output on the other stream: {other!r}"
    if case.check is not None:
        case.check()


@pytest.mark.usefixtures("narrow", "fake_github")
@pytest.mark.parametrize("value", HOSTILE.values(), ids=HOSTILE.keys())
@pytest.mark.parametrize("path_name", PATHS)
def test_value_renders_literally(
    path_name: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = PATHS[path_name](tmp_path, value, monkeypatch)
    _assert_case(runner.invoke(app, case.args), case)


@pytest.mark.usefixtures("narrow")
def test_promote_close_tag_persists_and_succeeds(tmp_path: Path) -> None:
    """The measured failure: the stage was written, then the output crashed."""
    path = tmp_path / "release-manifest.yaml"
    _seed(path)

    result = runner.invoke(app, ["promote", "[/x]", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "Promoted to [/x]" in result.stdout.splitlines()
    assert ReleaseManifest.from_yaml(path).stages["[/x]"].digest == DIGEST_A


@pytest.mark.usefixtures("narrow")
def test_swallowed_network_error_text_is_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    text = "[Errno 8] nodename nor servname provided, or not known"
    monkeypatch.setattr(commands, "build_notifier", _failing_notifier(text))

    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])

    assert result.exit_code == 0
    assert "Promoted to dev" in result.stdout.splitlines()
    assert f"  (slack notify failed: {text})" in result.stderr.splitlines()


@pytest.mark.usefixtures("narrow")
@pytest.mark.parametrize(
    ("args", "line"),
    [
        (["--repo", ""], "--repo owner/name is required with --open-pr"),
        (["--repo", "o/r"], "GORA_GITHUB_TOKEN is required with --open-pr"),
    ],
    ids=["no-repo", "no-token"],
)
def test_open_pr_precondition_goes_to_stderr(tmp_path: Path, args: list[str], line: str) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = runner.invoke(app, ["promote", "dev", "--open-pr", *args, "-m", str(path)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert line in result.stderr.splitlines()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.usefixtures("narrow")
def test_promote_without_candidate_goes_to_stderr(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path, candidate=None)

    result = runner.invoke(app, ["promote", "dev", "-m", str(path)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "No active release candidate to promote" in result.stderr.splitlines()


@pytest.mark.usefixtures("narrow")
def test_status_without_manifest_hint_goes_to_stderr(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", "-m", str(tmp_path / "absent.yaml")])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Run gora cut-rc to create a release candidate." in result.stderr.splitlines()


@pytest.mark.usefixtures("narrow")
def test_version_is_one_line_on_stdout() -> None:
    from gora import __version__

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"gora {__version__}\n"
    assert result.stderr == ""


@pytest.mark.usefixtures("narrow", "fake_github")
def test_open_pr_line_has_no_decorative_characters(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)

    result = runner.invoke(app, ["promote", "dev", "--open-pr", "--repo", "o/r", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert f"  1.2.0-rc.1 to dev, digest {DIGEST_A}" in result.stdout.splitlines()
    assert result.stdout.isascii()


@pytest.mark.usefixtures("narrow")
def test_status_table_has_no_decorative_characters(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)

    result = runner.invoke(app, ["status", "-m", str(path)])

    assert result.exit_code == 0, result.output
    assert result.stdout.isascii()


# --- a real terminal: color on, still literal, still unwrapped ----------------------

SGR = re.compile(r"\x1b\[[0-9;]*m")
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
    out, err = (raw.decode().replace("\r\n", "\n") for raw in _read_all(masters))
    for master in masters:
        os.close(master)
    return proc.wait(timeout=60), out, err


TTY_PATHS = ["status-manifest-content", "promote-stage", "promote-no-manifest"]


@pytest.mark.parametrize("value", HOSTILE.values(), ids=HOSTILE.keys())
@pytest.mark.parametrize("path_name", TTY_PATHS)
def test_value_renders_literally_on_a_tty(
    path_name: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = PATHS[path_name](tmp_path, value, monkeypatch)

    code, out, err = _run_on_tty(case.args, cwd=tmp_path)

    assert "\x1b[" in out + err, "expected a color-capable terminal"
    assert OSC not in out + err, "output carries a terminal hyperlink"
    assert "Traceback" not in out + err
    assert code == case.exit_code, f"stdout={out!r} stderr={err!r}"
    shown = SGR.sub("", out if case.stream == "stdout" else err)
    lines = shown.splitlines()
    for line in case.lines:
        assert line in lines, f"{line!r} is not a line of {case.stream}: {shown!r}"
    for parts in case.together:
        assert any(all(p in line for p in parts) for line in lines), shown

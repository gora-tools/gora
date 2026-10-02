"""The release contract, end to end through the CLI (ADR-003, ADR-0017).

A value that breaks an identifier grammar is a usage error: exit 2, the parameter and the
rule named, the value shown escaped, and nothing opened, written or sent. An operation that
breaks an invariant exits 1 and says the manifest is unchanged: the file is byte-identical,
the journal has no new event and no notification goes out. Re-cutting the active candidate
is not an error and not an event either.

This module imports nothing the contract introduced, so it runs unchanged against the code
before it: that run is the red half of each test (ADR-0016 rule 1).
"""

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import Result
from typer.testing import CliRunner

import gora.adapters.github as github
import gora.transport.cli.commands as commands
from gora.transport.cli.commands import app

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
IMAGE = "ghcr.io/test/service"
REPO_ROOT = Path(__file__).resolve().parents[2]

runner = CliRunner()


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def journal() -> Path:
    return Path(os.environ["GORA_AUDIT_LOG"])


def events() -> list[str]:
    path = journal()
    return (
        [json.loads(line)["action"] for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture(autouse=True)
def wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typer draws a usage error in a box as wide as the terminal; wide enough not to wrap."""
    monkeypatch.setenv("COLUMNS", "1000")


class Notifications:
    """A notifier that is switched on and records every event the CLI asks it to send."""

    active = True
    skipped = None
    channel = "C1"

    def __init__(self) -> None:
        self.attempts: list[tuple[str, dict[str, Any]]] = []

    def cut_rc(self, *args: Any, **kwargs: Any) -> None:
        self.attempts.append(("cut_rc", kwargs))

    def promoted(self, *args: Any, **kwargs: Any) -> None:
        self.attempts.append(("promoted", kwargs))

    def rolled_back(self, *args: Any, **kwargs: Any) -> None:
        self.attempts.append(("rolled_back", kwargs))

    def promotion_pr(self, *args: Any, **kwargs: Any) -> None:
        self.attempts.append(("promotion_pr", kwargs))


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> Notifications:
    spy = Notifications()
    monkeypatch.setattr(commands, "build_notifier", lambda _settings: spy)
    return spy


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    """Line 1.2.0: rc.1 on digest A, promoted to dev; prod on 1.1.9, digest B."""
    path = tmp_path / "release-manifest.yaml"
    doc = {
        "version": "v0.1",
        "release": "1.2.0",
        "candidate": {"version": "1.2.0-rc.1", "image": IMAGE, "digest": DIGEST_A},
        "stages": {
            "dev": {"version": "1.2.0-rc.1", "digest": DIGEST_A},
            "prod": {"version": "1.1.9", "digest": DIGEST_B},
        },
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


# --- invariants: refused, exit 1, nothing changed --------------------------------------------


def assert_refused(
    result: Result, path: Path, before: str | None, journal_before: list[str]
) -> None:
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert "the manifest is unchanged" in result.stderr, result.stderr
    assert result.stdout == ""
    assert sha(path) == before
    assert events() == journal_before


def test_e1_recutting_a_version_with_another_digest_is_refused(
    manifest: Path, notified: Notifications
) -> None:
    before = sha(manifest)

    result = run("cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_C, "-m", str(manifest))

    assert_refused(result, manifest, before, [])
    assert "with another digest" in result.stderr
    assert notified.attempts == []


def test_e1_from_a_fresh_manifest(tmp_path: Path, notified: Notifications) -> None:
    """The audit's reproduction: cut-rc twice, same version, another digest."""
    path = tmp_path / "release-manifest.yaml"
    first = run("cut-rc", "-i", IMAGE, "-v", "1.0.0-rc.1", "-d", DIGEST_A, "-m", str(path))
    assert first.exit_code == 0, first.output
    before, logged = sha(path), events()

    result = run("cut-rc", "-i", IMAGE, "-v", "1.0.0-rc.1", "-d", DIGEST_B, "-m", str(path))

    assert_refused(result, path, before, logged)
    assert logged == ["cut-rc"]
    assert [name for name, _ in notified.attempts] == ["cut_rc"]  # the first cut only


def test_recutting_the_active_candidate_is_a_quiet_no_op(
    manifest: Path, notified: Notifications
) -> None:
    before = sha(manifest)
    journal().parent.mkdir(parents=True, exist_ok=True)
    journal().write_text("")

    result = run("cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-m", str(manifest))

    assert result.exit_code == 0, result.output
    assert "already the active candidate" in result.stdout
    assert sha(manifest) == before
    assert journal().read_bytes() == b""
    assert notified.attempts == []


def test_a_candidate_off_the_release_line_is_refused(manifest: Path) -> None:
    before = sha(manifest)
    result = run("cut-rc", "-i", IMAGE, "-v", "1.3.0-rc.1", "-d", DIGEST_C, "-m", str(manifest))
    assert_refused(result, manifest, before, [])
    assert "release line" in result.stderr


def test_a_fresh_manifest_on_another_release_line_than_its_version_is_not_created(
    tmp_path: Path,
) -> None:
    path = tmp_path / "release-manifest.yaml"
    args = ["cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-r", "1.3.0"]
    result = run(*args, "-m", str(path))
    assert_refused(result, path, None, [])


def test_a_candidate_with_another_image_is_refused(manifest: Path) -> None:
    before = sha(manifest)
    other = "ghcr.io/test/other"
    result = run("cut-rc", "-i", other, "-v", "1.2.0-rc.2", "-d", DIGEST_C, "-m", str(manifest))
    assert_refused(result, manifest, before, [])
    assert "one image" in result.stderr


def test_f7_rolling_back_a_stage_that_does_not_exist_is_refused(
    manifest: Path, notified: Notifications
) -> None:
    before = sha(manifest)

    result = run(
        "rollback", "qa", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", str(manifest)
    )

    assert_refused(result, manifest, before, [])
    assert "stage does not exist" in result.stderr
    assert "qa" not in yaml.safe_load(manifest.read_text())["stages"]
    assert notified.attempts == []


def test_rolling_back_to_a_version_recorded_with_another_digest_is_refused(
    manifest: Path,
) -> None:
    before = sha(manifest)
    result = run(
        "rollback", "dev", "--to-version", "1.1.9", "--to-digest", DIGEST_C, "-m", str(manifest)
    )
    assert_refused(result, manifest, before, [])


def test_the_allowed_operations_still_work(manifest: Path) -> None:
    """Control: the refusals above differ from these only in the invariant they break."""
    m = str(manifest)
    assert run("cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.2", "-d", DIGEST_C, "-m", m).exit_code == 0
    assert run("promote", "prod", "-m", m).exit_code == 0
    rollback = run("rollback", "prod", "--to-version", "1.1.9", "--to-digest", DIGEST_B, "-m", m)
    assert rollback.exit_code == 0, rollback.output
    final = run("cut-rc", "-i", IMAGE, "-v", "1.2.0", "-d", DIGEST_C, "-m", m)  # the retag
    assert final.exit_code == 0, final.output
    assert events() == ["cut-rc", "promote", "rollback", "cut-rc"]


# --- e) a manifest that already breaks the contract is refused on load -----------------------


@pytest.mark.parametrize(
    ("edit", "named"),
    [
        (lambda doc: doc["stages"]["dev"].update(digest=DIGEST_C), "another digest"),
        (lambda doc: doc.update(release="1.3.0"), "must equal release"),
        (lambda doc: doc["candidate"].update(version="1.2.0-rc.1+b1"), "candidate.version"),
        (lambda doc: doc["stages"].update({"dev[eu]": doc["stages"]["dev"]}), "stages.dev[eu]"),
    ],
    ids=["version-two-digests", "candidate-off-the-line", "build-metadata", "bad-stage-name"],
)
@pytest.mark.parametrize("command", ["status", "promote"])
def test_a_manifest_that_breaks_the_contract_is_refused(
    manifest: Path, edit: Any, named: str, command: str
) -> None:
    doc = yaml.safe_load(manifest.read_text())
    edit(doc)
    manifest.write_text(yaml.safe_dump(doc, sort_keys=False))
    before = sha(manifest)

    args = ["status"] if command == "status" else ["promote", "staging"]
    result = run(*args, "-m", str(manifest))

    assert result.exit_code == 1, result.output
    first = result.stderr.splitlines()[0]
    assert first.startswith(f"Cannot load manifest {manifest}: "), result.stderr
    assert named in first
    assert sha(manifest) == before


# --- F6: loading invents nothing ------------------------------------------------------------


def test_f6_a_manifest_without_created_at_does_not_gain_one(manifest: Path) -> None:
    assert "created_at" not in manifest.read_text()

    result = run("promote", "staging", "-m", str(manifest))

    assert result.exit_code == 0, result.output
    assert "created_at" not in manifest.read_text()


def test_cut_rc_records_when_the_candidate_was_cut(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    assert (
        run("cut-rc", "-i", IMAGE, "-v", "1.2.0-rc.1", "-d", DIGEST_A, "-m", str(path)).exit_code
        == 0
    )
    assert "created_at" in yaml.safe_load(path.read_text())["candidate"]


def test_status_reads_the_repo_root_manifest() -> None:
    """GORA releases itself with this file; it must stay loadable under the contract."""
    path = REPO_ROOT / "release-manifest.yaml"
    before = sha(path)

    result = run("status", "-m", str(path))

    assert result.exit_code == 0, result.output
    assert sha(path) == before


# --- grammar: a bad argument is a usage error, before the journal opens ------------------------


def cut(flag: str, value: str) -> list[str]:
    options = {"-i": IMAGE, "-v": "1.2.0-rc.1", "-d": DIGEST_A, flag: value}
    return ["cut-rc", *[part for option in options.items() for part in option]]


def open_pr(flag: str, value: str) -> list[str]:
    options = {"--repo": "o/r", "--base": "main", flag: value}
    return ["promote", "dev", "--open-pr", *[part for option in options.items() for part in option]]


def rollback(flag: str, value: str) -> list[str]:
    options = {"--to-version": "1.1.9", "--to-digest": DIGEST_B, flag: value}
    stage = options.pop("STAGE", "prod")
    return ["rollback", stage, *[part for option in options.items() for part in option]]


# (arguments, the bad value, the parameter as click names it, the grammar's noun)
VERSION_FLAG, IMAGE_FLAG, DIGEST_FLAG = (
    "'--version' / '-v'",
    "'--image' / '-i'",
    "'--digest' / '-d'",
)
BAD_ARGUMENTS = {
    "version-build-metadata": (cut("-v", "1.0.0+b1"), "1.0.0+b1", VERSION_FLAG, "version"),
    "version-leading-zero": (cut("-v", "1.02.0"), "1.02.0", VERSION_FLAG, "version"),
    "version-129": (cut("-v", "1.0.0-" + "a" * 123), "1.0.0-" + "a" * 123, VERSION_FLAG, "version"),
    "image-no-registry": (cut("-i", "team/service"), "team/service", IMAGE_FLAG, "image"),
    "image-uppercase": (cut("-i", "ghcr.io/Team/s"), "ghcr.io/Team/s", IMAGE_FLAG, "image"),
    "image-tag": (cut("-i", IMAGE + ":1.2.0"), IMAGE + ":1.2.0", IMAGE_FLAG, "image"),
    "digest-63": (cut("-d", DIGEST_A[:-1]), DIGEST_A[:-1], DIGEST_FLAG, "digest"),
    "digest-65": (cut("-d", DIGEST_A + "a"), DIGEST_A + "a", DIGEST_FLAG, "digest"),
    "digest-uppercase": (cut("-d", DIGEST_A.upper()), DIGEST_A.upper(), DIGEST_FLAG, "digest"),
    "digest-junk": (cut("-d", DIGEST_A + " x"), DIGEST_A + " x", DIGEST_FLAG, "digest"),
    "release-two-parts": (cut("-r", "1.2"), "1.2", "'--release' / '-r'", "release line"),
    "stage-bracketed": (["promote", "dev[eu]"], "dev[eu]", "'STAGE'", "stage name"),
    "stage-uppercase": (["promote", "Prod"], "Prod", "'STAGE'", "stage name"),
    "stage-64": (["promote", "a" * 64], "a" * 64, "'STAGE'", "stage name"),
    "stage-trailing-dash": (["promote", "dev-"], "dev-", "'STAGE'", "stage name"),
    "repo-control": (open_pr("--repo", "o\x01/r"), "o\x01/r", "'--repo'", "repository"),
    "repo-query": (open_pr("--repo", "o/r?x=1"), "o/r?x=1", "'--repo'", "repository"),
    "repo-two-slashes": (open_pr("--repo", "o/r/x"), "o/r/x", "'--repo'", "repository"),
    "repo-dot-dot": (open_pr("--repo", "o/.."), "o/..", "'--repo'", "repository"),
    "base-dot-dot": (open_pr("--base", "a..b"), "a..b", "'--base'", "branch name"),
    "base-lock": (open_pr("--base", "main.lock"), "main.lock", "'--base'", "branch name"),
    "base-leading-slash": (open_pr("--base", "/main"), "/main", "'--base'", "branch name"),
    "base-control": (open_pr("--base", "ma\x1bin"), "ma\x1bin", "'--base'", "branch name"),
    "rollback-stage": (rollback("STAGE", "Prod"), "Prod", "'STAGE'", "stage name"),
    "rollback-version": (
        rollback("--to-version", "1.1.9+b"),
        "1.1.9+b",
        "'--to-version'",
        "version",
    ),
    "rollback-digest": (
        rollback("--to-digest", "sha256:abc"),
        "sha256:abc",
        "'--to-digest'",
        "digest",
    ),
}


def _escaped(value: str) -> str:
    """The two control characters used above, as the CLI shows them."""
    return value.replace("\x01", "\\x01").replace("\x1b", "\\x1b")


@pytest.fixture
def no_remote(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """A GitHub adapter that records being built; a bad argument must never build one."""
    built: list[str] = []
    monkeypatch.setattr(github, "GitHubAdapter", lambda repo, token: built.append(repo))
    monkeypatch.setenv("GORA_GITHUB_TOKEN", "fake-sentinel-github")
    yield built


@pytest.mark.parametrize("name", sorted(BAD_ARGUMENTS))
def test_a_bad_argument_is_a_usage_error_before_anything_opens(
    name: str, manifest: Path, notified: Notifications, no_remote: list[str]
) -> None:
    args, value, hint, noun = BAD_ARGUMENTS[name]
    before = sha(manifest)

    result = run(*args, "-m", str(manifest))

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    message = f"Invalid value for {hint}: '{_escaped(value)}' is not a valid {noun}: must"
    assert message in result.stderr, result.stderr
    assert "\x01" not in result.stderr and "\x1b" not in result.stderr
    assert not journal().exists(), "the journal was opened"
    assert sha(manifest) == before
    assert notified.attempts == []
    assert no_remote == []


def test_a_bad_argument_creates_no_manifest(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    result = run("cut-rc", "-i", IMAGE, "-v", "1.0.0+b1", "-d", DIGEST_A, "-m", str(path))
    assert result.exit_code == 2
    assert not path.exists()
    assert not journal().exists()


def test_a_good_value_next_to_the_bad_ones_passes(manifest: Path, no_remote: list[str]) -> None:
    """Control: the same command with valid values gets past the parser."""
    result = run("promote", "staging", "-m", str(manifest))
    assert result.exit_code == 0, result.output
    assert events() == ["promote"]

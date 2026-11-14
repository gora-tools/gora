"""The image scan (ADR-011, amendment 2026-11-07): ``scripts/gate/image-scan.sh``, the Makefile
target that calls it, the scanner as a gate image, and the build action that runs it.

The scripts run for real, with ``docker`` a shim that logs each call, so that what is asserted is
what the scan asks Docker to do: the scanner container has no network, a read-only root file
system and a tmpfs at ``/tmp``, reads a volume mounted read-only and never a host path or the
Docker socket, and fails on a vulnerability with a fix of high or critical severity; the volume
is filled through standard input by a container that has no network either, and is removed
whatever the scan's verdict; an expired exception stops the scan before any container starts.
The scanner itself is measured by the red-first proofs of the pull request, not here (TESTING
rule 6: no test needs Docker).
"""

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "gate"
IMAGE_SCAN = GATE / "image-scan.sh"
EXCEPTIONS = GATE / "scan-exceptions.yaml"
GATE_IMAGES = ROOT / "deployments" / "docker" / "gate-images.Dockerfile"
MAKEFILE = ROOT / "Makefile"
ACTION = ROOT / ".github" / "actions" / "build-image" / "action.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

SCANNER = "scanner-img"
STAGE = "stage-img"
PLATFORM = "linux/arm64"
RUN = f"{GATE / 'run-container.sh'} image-scan SCAN_TIMEOUT 60"

# docker: logs every call. An image is always present for linux/arm64. The volume is the file
# `volume` of FAKE_DIR. A run that names oci-dir: is the scan, and exits FAKE_SCAN_EXIT; any other
# run is the stage, which keeps its standard input in `stage.in` and exits FAKE_STAGE_EXIT.
DOCKER_SHIM = """\
#!/bin/sh
echo "$*" >> "$FAKE_DIR/docker.log"
case "$1" in
  ps | rm) exit 0 ;;
  image) echo linux/arm64; exit 0 ;;
  save) echo "TAR-OF-$2"; exit 0 ;;
  volume)
    case "$2" in
      inspect) [ -e "$FAKE_DIR/volume" ]; exit $? ;;
      create) : > "$FAKE_DIR/volume"; echo "$3"; exit 0 ;;
      rm) rm -f "$FAKE_DIR/volume"; exit 0 ;;
    esac
    ;;
  run)
    case "$*" in
      *oci-dir:*) exit "${FAKE_SCAN_EXIT:-0}" ;;
      *) cat > "$FAKE_DIR/stage.in"; exit "${FAKE_STAGE_EXIT:-0}" ;;
    esac
    ;;
esac
exit 64
"""


@dataclass
class Scan:
    code: int
    out: str
    err: str
    fake: Path

    @property
    def calls(self) -> list[str]:
        log = self.fake / "docker.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def runs(self) -> list[str]:
        return [call for call in self.calls if call.startswith("run ")]

    @property
    def scan(self) -> str:
        found = [call for call in self.runs() if "oci-dir:" in call]
        assert len(found) == 1, self.calls
        return found[0]

    @property
    def stage(self) -> str:
        found = [call for call in self.runs() if "oci-dir:" not in call]
        assert len(found) == 1, self.calls
        return found[0]

    @property
    def staged(self) -> bytes:
        return (self.fake / "stage.in").read_bytes()


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "docker"
    shim.write_text(DOCKER_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    (tmp_path / "fake").mkdir()
    return tmp_path


def _env(fake: Path, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": f"{fake / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DIR": str(fake / "fake"),
        "HOME": str(fake),
        "XDG_STATE_HOME": str(fake / "state"),
        **extra,
    }


def scan(fake: Path, *args: str, **env: str) -> Scan:
    argv = [
        str(IMAGE_SCAN),
        "--platform",
        PLATFORM,
        "--scanner",
        SCANNER,
        "--stage-image",
        STAGE,
        "--run",
        RUN,
        *args,
    ]
    done = subprocess.run(
        argv, env=_env(fake, **env), capture_output=True, text=True, timeout=60, check=False
    )
    return Scan(done.returncode, done.stdout, done.stderr, fake / "fake")


def _exceptions(fake: Path, text: str) -> Path:
    path = fake / "exceptions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _entry(expires: str, vulnerability: str = "GHSA-6q48-6c7w-9gw2") -> str:
    return (
        f"ignore:\n  - vulnerability: {vulnerability}\n    package:\n      name: certifi\n"
        f"    reason: A probe.\n    expires: {expires}\n"
    )


# --- what the scan asks Docker to do --------------------------------------------------------


def test_the_scanner_runs_offline_read_only_and_from_the_volume(fake: Path) -> None:
    run = scan(fake, "--image", "gora:smoke")
    assert run.code == 0, run.err
    call = run.scan.split()
    assert " ".join(call[call.index("--network") :][:2]) == "--network none", run.scan
    assert "--read-only" in call
    assert " ".join(call[call.index("--tmpfs") :][:2]) == "--tmpfs /tmp"
    assert " ".join(call[call.index("-v") :][:2]) == "-v gora-scan:/scan:ro"
    assert "--entrypoint" not in call, "the scanner has no timeout(1): the host bounds it"
    tail = call[call.index(SCANNER) :]
    assert tail == [
        SCANNER,
        "--platform",
        PLATFORM,
        "-c",
        "/scan/scan-exceptions.yaml",
        "--only-fixed",
        "--fail-on",
        "high",
        "oci-dir:/scan",
    ]


def test_the_scanner_never_gets_a_host_path_or_the_docker_socket(fake: Path) -> None:
    run = scan(fake, "--image", "gora:smoke")
    for call in run.runs():
        words = call.split()
        mounts = [words[i + 1] for i, word in enumerate(words) if word in ("-v", "--volume")]
        assert mounts, call
        for mount in mounts:
            assert mount.startswith("gora-scan:/scan"), f"a host path is mounted: {mount}"
        assert "docker.sock" not in call
    assert "docker.sock" not in IMAGE_SCAN.read_text(encoding="utf-8")


def test_the_volume_is_filled_by_an_offline_read_only_container_through_standard_input(
    fake: Path,
) -> None:
    run = scan(fake, "--image", "gora:smoke")
    call = run.stage.split()
    assert " ".join(call[call.index("--network") :][:2]) == "--network none"
    assert "--read-only" in call and "-i" in call
    assert " ".join(call[call.index("-v") :][:2]) == "-v gora-scan:/scan"
    assert call[call.index("--entrypoint") :][:2] == ["--entrypoint", "timeout"]
    assert call[call.index(STAGE) :] == [
        STAGE,
        "-k",
        "10",
        "60",
        "tar",
        "-x",
        "-i",
        "--no-overwrite-dir",
        "-C",
        "/scan",
    ]
    assert run.runs().index(run.stage) < run.runs().index(run.scan), "stage, then scan"


def test_the_stream_is_the_saved_image_then_the_exceptions_file(fake: Path) -> None:
    run = scan(fake, "--image", "gora:smoke")
    assert "save gora:smoke" in run.calls
    staged = run.staged
    assert b"TAR-OF-gora:smoke" in staged
    assert staged.index(b"TAR-OF-gora:smoke") < staged.index(b"scan-exceptions.yaml")
    assert b"ignore:" in staged


def test_a_layout_directory_is_streamed_as_a_tar(fake: Path) -> None:
    layout = fake / "layout"
    layout.mkdir()
    (layout / "index.json").write_text('{"schemaVersion": 2}\n', encoding="utf-8")
    run = scan(fake, "--layout", str(layout))
    assert run.code == 0, run.err
    assert not any(call.startswith("save ") for call in run.calls)
    assert b"index.json" in run.staged and b'{"schemaVersion": 2}' in run.staged


def test_the_volume_is_removed_whatever_the_verdict(fake: Path) -> None:
    for exit_code in ("0", "2", "1", "124"):
        run = scan(fake, "--image", "gora:smoke", FAKE_SCAN_EXIT=exit_code)
        assert run.code == int(exit_code), run.err
        assert "volume rm -f gora-scan" in run.calls
        assert not (fake / "fake" / "volume").exists()
        (fake / "fake" / "docker.log").unlink()


def test_a_finding_at_or_above_the_threshold_fails_the_scan_and_is_not_called_a_pass(
    fake: Path,
) -> None:
    run = scan(fake, "--image", "gora:smoke", FAKE_SCAN_EXIT="2")
    assert run.code == 2
    assert "passed" not in run.out


def test_a_clean_scan_says_what_it_checked(fake: Path) -> None:
    run = scan(fake, "--image", "gora:smoke")
    assert run.code == 0
    passed = "image-scan: passed: no vulnerability with a fix, of high or critical severity"
    assert passed in run.out


def test_a_stage_that_fails_ends_the_scan_before_the_scanner_runs(fake: Path) -> None:
    run = scan(fake, "--image", "gora:smoke", FAKE_STAGE_EXIT="3")
    assert run.code == 3
    assert not any("oci-dir:" in call for call in run.runs())
    assert "volume rm -f gora-scan" in run.calls


# --- the volume that is left, and the wrong calls -------------------------------------------


def test_a_volume_left_from_another_scan_stops_the_scan_and_names_what_removes_it(
    fake: Path,
) -> None:
    (fake / "fake" / "volume").write_text("", encoding="utf-8")
    run = scan(fake, "--image", "gora:smoke")
    assert run.code == 2
    assert "docker volume rm gora-scan" in run.err
    assert run.runs() == []
    assert not any(call.startswith("volume create") for call in run.calls)
    assert (fake / "fake" / "volume").exists(), "another scan's volume is not this one's to remove"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--image", "gora:smoke", "--layout", "somewhere"],
        ["--image"],
        ["--unknown", "x", "--image", "gora:smoke"],
    ],
)
def test_a_wrong_call_is_refused_before_docker_is_called(fake: Path, args: list[str]) -> None:
    run = scan(fake, *args)
    assert run.code == 64
    assert run.calls == []


# --- the exceptions: the date, before anything runs -----------------------------------------


def test_an_expired_exception_stops_the_scan_before_any_container_starts(fake: Path) -> None:
    path = _exceptions(fake, _entry("2000-01-01"))
    run = scan(fake, "--image", "gora:smoke", "--exceptions", str(path))
    assert run.code == 2
    assert "the exception for GHSA-6q48-6c7w-9gw2 expired on 2000-01-01" in run.err
    assert run.calls == [], "no volume, no container: the check is the host's, first"


def test_every_expired_entry_is_named(fake: Path) -> None:
    text = _entry("2000-01-01") + (
        "  - vulnerability: CVE-2026-0001\n    package:\n      name: other\n"
        "    reason: A probe.\n    expires: 2000-02-02\n"
    )
    run = scan(fake, "--image", "gora:smoke", "--exceptions", str(_exceptions(fake, text)))
    assert run.code == 2
    assert "GHSA-6q48-6c7w-9gw2 expired on 2000-01-01" in run.err
    assert "CVE-2026-0001 expired on 2000-02-02" in run.err


def test_an_exception_that_has_not_expired_goes_to_the_scanner_as_it_is(fake: Path) -> None:
    path = _exceptions(fake, _entry("2999-12-31"))
    run = scan(fake, "--image", "gora:smoke", "--exceptions", str(path))
    assert run.code == 0, run.err
    assert "-c /scan/exceptions.yaml" in run.scan
    assert b"expires: 2999-12-31" in run.staged


def test_an_entry_dated_today_still_holds_and_one_dated_yesterday_does_not(fake: Path) -> None:
    """The script's day is the host's, in UTC: the same day as this test's, but for a run across
    midnight."""
    today = datetime.now(UTC).date()
    held = scan(
        fake,
        "--image",
        "gora:smoke",
        "--exceptions",
        str(_exceptions(fake, _entry(today.isoformat()))),
    )
    assert held.code == 0, held.err
    gone = scan(
        fake,
        "--image",
        "gora:smoke",
        "--exceptions",
        str(_exceptions(fake, _entry((today - timedelta(days=1)).isoformat()))),
    )
    assert gone.code == 2
    assert "expired on" in gone.err


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (
            "ignore:\n  - vulnerability: GHSA-6q48-6c7w-9gw2\n    package:\n      name: certifi\n",
            "the exception for GHSA-6q48-6c7w-9gw2 has no expires date",
        ),
        (
            _entry("soon"),
            "the exception for GHSA-6q48-6c7w-9gw2 has no ISO date in expires: soon",
        ),
        (
            _entry('"2999-12-31"'),
            'has no ISO date in expires: "2999-12-31"',
        ),
    ],
)
def test_an_entry_with_no_date_it_can_read_stops_the_scan(
    fake: Path, text: str, found: str
) -> None:
    run = scan(fake, "--image", "gora:smoke", "--exceptions", str(_exceptions(fake, text)))
    assert run.code == 2
    assert found in run.err
    assert run.calls == []


def test_the_comments_and_the_empty_list_of_the_real_file_are_not_entries(fake: Path) -> None:
    run = scan(fake, "--image", "gora:smoke")
    assert run.code == 0, run.err
    assert "expired" not in run.err


def test_a_missing_exceptions_file_stops_the_scan(fake: Path) -> None:
    run = scan(fake, "--image", "gora:smoke", "--exceptions", str(fake / "absent.yaml"))
    assert run.code == 2
    assert "does not exist" in run.err
    assert run.calls == []


# --- the wiring: target, scanner image, action, workflows -----------------------------------


def _recipe(makefile: str, target: str) -> str:
    lines: list[str] = []
    inside = False
    for line in makefile.splitlines():
        if line.startswith(f"{target}:") and not line.startswith(f"{target}:="):
            inside = True
            continue
        if inside and (line.startswith("\t") or line.startswith("#")):
            lines.append(line)
            continue
        if inside:
            break
    return "\n".join(lines)


def test_the_target_calls_the_script_with_its_limit_named_by_the_macro() -> None:
    recipe = _recipe(MAKEFILE.read_text(encoding="utf-8"), "image-scan")
    assert "scripts/gate/image-scan.sh" in recipe
    assert "$(call gate_run,image-scan,SCAN_TIMEOUT)" in recipe
    assert '--scanner "$(SCANNER_IMAGE)"' in recipe and '--stage-image "$(LINUX_IMAGE)"' in recipe
    assert "SCAN_TIMEOUT ?=" in MAKEFILE.read_text(encoding="utf-8")


def test_the_rehearsal_scans_after_it_smokes() -> None:
    recipe = _recipe(MAKEFILE.read_text(encoding="utf-8"), "docker-smoke")
    steps = [line.strip() for line in recipe.splitlines() if "MAKE" in line]
    assert steps == [
        "@$(MAKE) --no-print-directory image-smoke",
        "@$(MAKE) --no-print-directory image-scan",
    ]


def test_the_scanner_is_a_gate_image_by_digest_and_dependabot_leaves_it_to_a_person() -> None:
    done = subprocess.run(
        [str(GATE / "image.sh"), "scanner"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().startswith("ghcr.io/gora-tools/gora-scanner@sha256:")
    assert "@" in done.stdout and ":latest" not in done.stdout
    dependabot: dict[str, Any] = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    docker = next(u for u in dependabot["updates"] if u["package-ecosystem"] == "docker")
    ignored = {rule["dependency-name"] for rule in docker["ignore"]}
    assert {"ghcr.io/gora-tools/gora-base", "ghcr.io/gora-tools/gora-scanner"} <= ignored
    assert "scanner:" in GATE_IMAGES.read_text(encoding="utf-8"), "the header names the stage"


def _steps(path: Path) -> list[dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "runs" in document:
        return list(document["runs"]["steps"])
    return [step for job in document["jobs"].values() for step in job.get("steps", [])]


def test_the_build_action_saves_the_layout_once_after_the_smoke_and_scans_it() -> None:
    steps = _steps(ACTION)
    names = [step.get("name", "") for step in steps]
    smoke = names.index("Smoke the pushed digest on this runner")
    save = names.index("Save the pushed image as an OCI layout")
    scan_step = names.index("Scan the pushed image")
    assert smoke < save < scan_step == len(names) - 1
    assert "crane" in steps[save]["run"] and "--format=oci" in steps[save]["run"]
    assert steps[scan_step]["run"].startswith("make image-scan SCAN_LAYOUT=")
    assert steps[scan_step]["env"]["LAYOUT"] == steps[save]["env"]["LAYOUT"]


def test_ci_pulls_nothing_twice_and_the_release_keeps_the_layout_out_of_its_record() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert "crane" not in ci.split("  assemble:")[0], "the action makes the layout, once"
    assert "layout: ${{ runner.temp }}/image/layout" in ci
    release = RELEASE.read_text(encoding="utf-8")
    assert "layout: ${{ runner.temp }}/layout" in release
    assert "path: ${{ runner.temp }}/record\n" in release


def test_the_exceptions_file_is_the_one_the_scan_reads_by_default() -> None:
    assert EXCEPTIONS.is_file()
    assert "scripts/gate/scan-exceptions.yaml" in IMAGE_SCAN.read_text(encoding="utf-8")

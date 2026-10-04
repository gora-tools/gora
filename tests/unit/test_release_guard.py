"""The release workflow builds a release candidate and nothing else (ADR-018 section 7).

A final tag such as ``v0.2.0`` would build a new image for a version whose candidate digest
already passed every stage: the rebuild ADR-004 forbids. The final version is a retag of that
digest. So the release workflow refuses, as its first step, a tag whose version has no
pre-release part, and every other job needs that step.

The guard is the ``guard`` step of ``.github/workflows/release.yml``, a ``shell: bash`` step.
These tests load it from the file and run it as GitHub runs a bash step, so what is tested
is what the workflow runs.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from gora.domain.release.identifiers import VERSION

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


def _workflow() -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document


def _jobs() -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = _workflow()["jobs"]
    return jobs


def guard(ref: str, tmp_path: Path) -> tuple[int, str]:
    """The guard step run for a push of ``ref``: its exit code and its output."""
    (step,) = [s for s in _jobs()["guard"]["steps"] if s.get("id") == "guard"]
    assert step["shell"] == "bash"
    script = tmp_path / "guard.sh"
    script.write_text(step["run"], encoding="utf-8")
    # GitHub's command for `shell: bash`, with nothing in the environment but the ref.
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env={"PATH": "/usr/bin:/bin", "GITHUB_REF": ref},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode, done.stdout + done.stderr


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def upstream(jobs: dict[str, dict[str, Any]], name: str) -> set[str]:
    """Every job ``name`` waits for, directly or through another job."""
    found: set[str] = set()
    pending = _needs(jobs[name])
    while pending:
        job = pending.pop()
        if job not in found:
            found.add(job)
            pending += _needs(jobs[job])
    return found


# --- the guard runs before anything else ---------------------------------------------------


def not_waiting(jobs: dict[str, dict[str, Any]]) -> list[str]:
    """Each job other than the guard that can start before the guard has passed."""
    return [name for name in jobs if name != "guard" and "guard" not in upstream(jobs, name)]


def test_the_guard_is_the_first_step_and_every_job_waits_for_it() -> None:
    jobs = _jobs()
    assert _needs(jobs["guard"]) == []
    assert jobs["guard"]["steps"][0].get("id") == "guard"
    assert {"build", "assemble", "verify"} < set(jobs)
    assert not_waiting(jobs) == []


def test_a_job_that_does_not_wait_for_the_guard_is_found() -> None:
    jobs: dict[str, dict[str, Any]] = {
        "guard": {},
        "build": {"needs": "guard"},
        "assemble": {"needs": ["build"]},
        "verify": {"needs": ["assemble"]},
        "early": {},
    }
    assert not_waiting(jobs) == ["early"]


def test_the_trigger_stays_on_version_tags() -> None:
    """ADR-011 amendment: until registration exists, a pushed rc tag starts the build."""
    document = _workflow()
    # YAML 1.1 reads the key `on` as true.
    assert document.get("on", document.get(True)) == {"push": {"tags": ["v*"]}}


# --- what the guard accepts ----------------------------------------------------------------


def test_the_guard_refuses_a_final_version(tmp_path: Path) -> None:
    code, output = guard("refs/tags/v0.2.0", tmp_path)
    assert code == 1
    assert "pre-release" in output


def test_the_guard_accepts_a_release_candidate(tmp_path: Path) -> None:
    code, output = guard("refs/tags/v0.2.0-rc.7", tmp_path)
    assert code == 0, output
    assert "0.2.0-rc.7" in output


CANDIDATES = [
    "0.2.0-rc.7",
    "0.1.0-rc.1",
    "1.0.0-rc.10",
    "1.0.0-alpha",
    "1.0.0-0.3.7",
    "1.0.0-x.7.z.92",
    "1.0.0-x-y-z.--",
    "1.0.0-lock",
    "10.20.30-rc.1." + "1" * 100,
]
NOT_CANDIDATES = [
    "0.2.0",
    "1.0.0",
    "10.20.30",
    "0.2.0-rc.7+build.1",
    "0.2.0+build.1",
    "0.2.0-",
    "0.2.0-rc..1",
    "0.2.0-rc.01",
    "01.2.0-rc.1",
    "0.2-rc.1",
    "0.2.0.1-rc.1",
    "0.2.0-rc_1",
    "0.2.0-rc.1 ",
    "0.2.0-rc.lock",
    "0.2.0-rc.1." + "1" * 120,
    "",
]


@pytest.mark.parametrize("version", CANDIDATES + NOT_CANDIDATES)
def test_the_guard_accepts_exactly_a_gora_version_with_a_prerelease(
    version: str, tmp_path: Path
) -> None:
    """The guard agrees with the version grammar of the release contract (ADR-003): a tag
    builds exactly when its version is a GORA version and has a pre-release part."""
    candidate = VERSION.is_valid(version) and "-" in version
    assert candidate == (version in CANDIDATES)
    code, output = guard(f"refs/tags/v{version}", tmp_path)
    assert code == (0 if candidate else 1), output


@pytest.mark.parametrize(
    "ref",
    [
        "refs/tags/0.2.0-rc.7",
        "refs/tags/V0.2.0-rc.7",
        "refs/heads/v0.2.0-rc.7",
        "refs/tags/api/v0.2.0-rc.7",
        "v0.2.0-rc.7",
        "",
    ],
)
def test_the_guard_refuses_anything_but_a_v_tag(ref: str, tmp_path: Path) -> None:
    code, output = guard(ref, tmp_path)
    assert code == 1, output

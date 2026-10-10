"""The identifier grammar against the real git binary (ADR-003, "Identifier grammar").

A version becomes the tag ``v<version>`` (release.yml), and the names, rc versions and
final versions of the release files form the tags and branches GORA names from them
(ADR-026). Whatever the grammar accepts, git must accept too: GORA may be stricter than git,
never looser. The oracle is ``git check-ref-format``, run with no user or system
configuration; it reads none, and the environment makes sure. It is calibrated first: it
must refuse what git refuses.

git is on every machine that runs this suite and in CI; a missing git fails the module
rather than skipping it, because a differential test that cannot run proves nothing.
"""

import os
import random
import shutil
import string
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from gora.domain.release.identifiers import (
    FINAL_VERSION,
    NAME,
    RC_VERSION,
    STAGE,
    VERSION,
)
from tests.unit.test_identifiers import (
    INVALID_FINAL_VERSIONS,
    INVALID_RC_VERSIONS,
    INVALID_STAGES,
    VALID_FINAL_VERSIONS,
    VALID_RC_VERSIONS,
    VALID_STAGES,
    VALID_VERSIONS,
    _random_version,
)

GIT = shutil.which("git")


def hermetic_git_env(home: Path) -> dict[str, str]:
    """The environment every differential test runs git in: no user or system configuration."""
    assert GIT is not None, "git is required: the differential tests compare against it"
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CEILING_DIRECTORIES": str(home.parent),
        "LC_ALL": "C",
    }


@pytest.fixture(scope="module")
def git_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    yield hermetic_git_env(tmp_path_factory.mktemp("git-home"))


def git_accepts(env: dict[str, str], names: list[str], *, branch: bool) -> dict[str, bool]:
    """git's verdict on each name: ``--branch NAME``, or a full ref name.

    One git at a time. Started from a pool of threads, these processes deadlocked the
    whole interpreter under amd64 emulation on Apple silicon (TESTING rule 8).
    """
    flag = ["--branch"] if branch else []
    verdicts = {}
    for name in names:
        args = [str(GIT), "check-ref-format", *flag, name]
        run = subprocess.run(args, env=env, cwd=env["HOME"], capture_output=True, check=False)
        verdicts[name] = run.returncode == 0
    return verdicts


# --- the oracle refuses what git refuses ------------------------------------------------------


def test_the_oracle_registers_git_s_refusals(git_env: dict[str, str]) -> None:
    branches = git_accepts(git_env, ["main", "-x", "HEAD", "a.lock", "a..b"], branch=True)
    assert branches == {"main": True, "-x": False, "HEAD": False, "a.lock": False, "a..b": False}
    tags = git_accepts(git_env, ["refs/tags/v1.2.0-rc.1", "refs/tags/v1.2.0-a.lock"], branch=False)
    assert tags == {"refs/tags/v1.2.0-rc.1": True, "refs/tags/v1.2.0-a.lock": False}


# --- version, as the tag v<version> --------------------------------------------------------------


def _lock_variants(version: str) -> list[str]:
    """Pre-release endings near ``.lock``: some git refuses in a ref, some it does not."""
    base = version if "-" in version else f"{version}-rc"
    return [f"{base}.lock", f"{base}.lock.1", f"{base}-lock", f"{base}.lockx", f"{base}.a-lock"]


def _versions() -> list[str]:
    rng = random.Random(20261002)
    generated = [_random_version(rng) for _ in range(400)]
    corpus = VALID_VERSIONS + generated + ["1.2.0-a.lock", "1.2.0-rc.1.lock", "1.2.0-lock"]
    corpus += [variant for v in VALID_VERSIONS + generated[:60] for variant in _lock_variants(v)]
    return sorted({v for v in corpus if VERSION.is_valid(v)})


def test_every_version_gora_accepts_is_a_tag_git_accepts(git_env: dict[str, str]) -> None:
    accepted = _versions()
    assert len(accepted) > 300  # calibration
    assert any(v.endswith("-lock") for v in accepted)  # a lock that is not a .lock stays

    verdicts = git_accepts(git_env, [f"refs/tags/v{v}" for v in accepted], branch=False)

    assert [ref for ref, ok in verdicts.items() if not ok] == []


# --- the release files: names, rc and final versions, and the refs they form (ADR-026) ------------


def _names() -> list[str]:
    rng = random.Random(20261009)
    generated = [
        rng.choice(string.ascii_lowercase)
        + "".join(rng.choice(string.ascii_lowercase + string.digits + "-") for _ in range(10))
        + rng.choice(string.ascii_lowercase + string.digits)
        for _ in range(60)
    ]
    corpus = VALID_STAGES + INVALID_STAGES + generated + ["lock", "a-lock", "head", "x" * 63]
    return sorted({name for name in corpus if NAME.is_valid(name)})


def _cores() -> list[str]:
    rng = random.Random(20261009)
    generated = [
        f"{rng.randint(0, 99)}.{rng.randint(0, 99)}.{rng.randint(0, 10**6)}" for _ in range(60)
    ]
    corpus = VALID_FINAL_VERSIONS + INVALID_FINAL_VERSIONS + generated
    corpus += [v.removesuffix("-rc") for v in VALID_RC_VERSIONS + INVALID_RC_VERSIONS]
    return sorted({core for core in corpus if FINAL_VERSION.is_valid(core)})


def test_every_name_rc_and_final_version_gora_accepts_is_a_tag_git_accepts(
    git_env: dict[str, str],
) -> None:
    names, cores = _names(), _cores()
    rcs = sorted({f"{core}-rc" for core in cores} | set(VALID_RC_VERSIONS))
    rcs = [v for v in rcs if RC_VERSION.is_valid(v)]
    assert len(names) > 50 and len(cores) > 50 and len(rcs) > 50  # calibration
    rng = random.Random(20261009)
    refs = [f"refs/tags/v{v}" for v in rcs + cores]
    refs += [f"refs/tags/{rng.choice(names)}/v{v}" for v in rcs + cores]

    verdicts = git_accepts(git_env, refs, branch=False)

    assert [ref for ref, ok in verdicts.items() if not ok] == []


def test_every_branch_gora_names_from_them_is_a_branch_git_accepts(
    git_env: dict[str, str],
) -> None:
    rng = random.Random(20261009)
    names, cores = _names(), _cores()
    stages = sorted({s for s in VALID_STAGES + names if STAGE.is_valid(s)})
    branches = [f"gora/promote/{rng.choice(stages)}/{rng.choice(names)}" for _ in range(150)]
    branches += [f"gora/reject/{rng.choice(names)}/{rng.choice(cores)}" for _ in range(150)]
    branches += [f"gora/retire/{name}" for name in names]
    branches += ["gora/promote/" + "a" * 63 + "/" + "b" * 63, "gora/rollback/prod", "gora/migrate"]
    branches = sorted(set(branches))
    assert len(branches) > 300  # calibration

    verdicts = git_accepts(git_env, branches, branch=True)
    assert [name for name, ok in verdicts.items() if not ok] == []

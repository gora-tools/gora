"""The rc tag's message against the real git binary (ADR-027 sections 1 and 4).

GORA writes the message of its rc tag byte by byte and does not rely on git's cleanup of a
message. Whatever creates the tag, the message must survive it: the oracle is ``git tag -a -F``
with its default cleanup (``strip``: comment lines, trailing white space, repeated and edge
blank lines), read back from the tag object, ``git cat-file tag``, after its header. It is
calibrated first: a message with a line that begins with ``#``, a line that ends with spaces and
two blank lines in a row must come back stripped of all three. And git must read the same
trailer GORA reads, and no other: ``git interpret-trailers --parse``.

git is on every machine that runs this suite and in CI; a missing git fails the module rather
than skipping it, because a differential test that cannot run proves nothing. One git at a
time (TESTING rule 8), with no user or system configuration.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.integration.test_identifiers_against_git import GIT, hermetic_git_env
from tests.unit.test_tags import MESSAGES

IDENTITY = ["-c", "user.name=gora-test", "-c", "user.email=gora-test@example.invalid"]


@pytest.fixture(scope="module")
def repository(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, dict[str, str]]]:
    home = tmp_path_factory.mktemp("git-home")
    env = hermetic_git_env(home)
    work = tmp_path_factory.mktemp("tags")
    run(work, env, "init", "--quiet")
    run(work, env, *IDENTITY, "commit", "--quiet", "--allow-empty", "-m", "base")
    yield work, env


def run(work: Path, env: dict[str, str], *args: str, stdin: str | None = None) -> str:
    assert GIT is not None
    done = subprocess.run(
        [GIT, *args],
        cwd=work,
        env=env,
        input=None if stdin is None else stdin.encode("utf-8"),
        capture_output=True,
        check=True,
        timeout=30,
    )
    # Bytes, decoded here: text mode would turn a \r into a line end on the way back.
    return done.stdout.decode("utf-8")


def through_git_tag(work: Path, env: dict[str, str], name: str, message: str) -> str:
    """``message`` as git keeps it when ``git tag -a -F`` creates the tag, cleanup by default."""
    file = work / f"{name}.txt"
    file.write_bytes(message.encode("utf-8"))
    run(work, env, *IDENTITY, "tag", "-a", "-F", str(file), name)
    raw = run(work, env, "cat-file", "tag", f"refs/tags/{name}")
    return raw.split("\n\n", 1)[1]


def test_the_oracle_strips_what_gora_never_writes(
    repository: tuple[Path, dict[str, str]],
) -> None:
    work, env = repository
    kept = through_git_tag(work, env, "calibration", "a\n# comment\nb  \n\n\nc\n")
    assert kept == "a\nb\n\nc\n"


@pytest.mark.parametrize("message", MESSAGES)
def test_git_tag_keeps_the_message_byte_for_byte(
    repository: tuple[Path, dict[str, str]], message: str, request: pytest.FixtureRequest
) -> None:
    work, env = repository
    name = request.node.callspec.id
    assert through_git_tag(work, env, f"m-{name}", message) == message


@pytest.mark.parametrize("message", MESSAGES)
def test_git_reads_the_one_trailer_gora_reads(
    repository: tuple[Path, dict[str, str]], message: str
) -> None:
    work, env = repository
    last = message.removesuffix("\n").rsplit("\n", 1)[1]
    assert run(work, env, "interpret-trailers", "--parse", stdin=message) == f"{last}\n"

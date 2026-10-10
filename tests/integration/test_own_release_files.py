"""GORA's own release files hold (ADR-026 section 16), and say what the image is made of.

Read from the checkout, not from git: the export ``make verify-export`` runs in has no
``.git``. Each file of the layout is a ``TreeEntry`` with the mode the file system gives it,
as git would give it for a commit.

Two things are held. The files load as one release tree, and the policy gives the stages the
maintainer decided. And every source of the release image, which the Dockerfile's ``COPY``
and ``ADD`` lines name, is an input of the artifact's declaration (L8): a change to anything
the image is made of is a change of the artifact. A source added to the Dockerfile and not to
the inputs fails here, naming it.
"""

import os
import re
import stat
from pathlib import Path

import pytest

from gora.domain.release_repository import files, inputs, v1alpha1
from gora.domain.release_repository.tree import ReleaseTree, TreeEntry, load_tree

ROOT = Path(__file__).resolve().parents[2]
RELEASE_REPOSITORY = "github.com/gora-tools/gora"
DOCKERFILE = "deployments/docker/Dockerfile"
ARTIFACT = "gora"

COPY_OR_ADD = re.compile(r"^(?:COPY|ADD)\s+(?P<arguments>.+)$", re.IGNORECASE)


def _entry(path: Path) -> TreeEntry:
    """A file of the checkout as git would give it: a symbolic link, an executable or a blob."""
    info = path.lstat()
    relative = path.relative_to(ROOT).as_posix().encode()
    if stat.S_ISLNK(info.st_mode):
        return TreeEntry(relative, "120000", os.readlink(path).encode())
    mode = "100755" if info.st_mode & stat.S_IXUSR else "100644"
    return TreeEntry(relative, mode, path.read_bytes())


def _layout_entries() -> list[TreeEntry]:
    """Every path of the checkout in the layout: the policy, and everything under
    ``artifacts/``, ``state/`` and ``rejections/``. A symbolic link to a directory is an entry
    too, and is not followed."""
    paths: list[Path] = []
    policy = ROOT / files.POLICY_PATH
    if policy.is_symlink() or policy.exists():
        paths.append(policy)
    for root in files.LAYOUT_ROOTS:
        for directory, subdirectories, names in os.walk(ROOT / root):
            here = Path(directory)
            paths += [here / name for name in names]
            paths += [here / name for name in subdirectories if (here / name).is_symlink()]
    return [_entry(path) for path in paths]


def _tree() -> ReleaseTree:
    return load_tree(_layout_entries(), release_repository=RELEASE_REPOSITORY)


def _instructions(dockerfile: str) -> list[str]:
    """The instructions of a Dockerfile: comment lines dropped, continuations joined."""
    instructions: list[str] = []
    current = ""
    for line in dockerfile.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if line.rstrip().endswith("\\"):
            current += line.rstrip()[:-1] + " "
            continue
        instructions.append((current + line).strip())
        current = ""
    if current:
        instructions.append(current.strip())
    return [instruction for instruction in instructions if instruction]


def _sources(dockerfile: str) -> list[str]:
    """The build-context sources of every ``COPY`` and ``ADD``, a directory without its ``/``.

    What cannot be read as a plain path is refused, never skipped: a source this misses is a
    source nothing checks.
    """
    sources: list[str] = []
    for instruction in _instructions(dockerfile):
        found = COPY_OR_ADD.match(instruction)
        if found is None:
            continue
        arguments = found["arguments"]
        if arguments.lstrip().startswith("["):
            pytest.fail(f"the JSON form of COPY or ADD is not read: {instruction}")
        words = arguments.split()
        if any(word.startswith("--from") for word in words):
            continue  # another stage or image, not the build context
        if any("<<" in word for word in words):
            pytest.fail(f"a heredoc source is not read: {instruction}")
        *named, _destination = [word for word in words if not word.startswith("--")]
        for source in named:
            if "://" in source:
                pytest.fail(f"a URL source is not read: {instruction}")
            if any(char in source for char in "*?["):
                pytest.fail(f"a wildcard source is not read: {instruction}")
            sources.append(source.rstrip("/"))
    return sources


def test_the_release_files_of_the_checkout_load_and_give_the_stages_of_the_policy() -> None:
    tree = _tree()

    assert list(tree.artifacts) == [ARTIFACT]
    gates = {"dev": "auto", "staging": "approval", "prod": "approval"}
    approvers = {"dev": {}, "staging": {"user/zuwarskej": 1}, "prod": {"user/zuwarskej": 1}}
    for stage, gate in gates.items():
        effective = tree.stage_for(stage, ARTIFACT)
        assert effective.gate == gate, stage
        assert dict(effective.approvers) == approvers[stage], stage
        assert effective.verify == ("smoke",), stage


def test_every_source_of_the_release_image_is_an_input() -> None:
    declaration = _tree().artifacts[ARTIFACT].model
    assert isinstance(declaration, v1alpha1.Artifact)
    globs = list(declaration.spec.source.inputs)

    def is_input(path: str) -> bool:
        return any(inputs.matches(glob, path.encode()) for glob in globs)

    sources = _sources((ROOT / DOCKERFILE).read_text(encoding="utf-8"))
    # Calibration: the parser read the Dockerfile's sources, a directory among them.
    assert sources, f"no COPY or ADD source was read from {DOCKERFILE}"
    assert any((ROOT / source).is_dir() for source in sources), sources

    outside: list[str] = []
    for source in sources:
        target = ROOT / source
        assert target.exists(), f"{source}: a source of {DOCKERFILE} that is not in the checkout"
        if target.is_dir():
            regular = sorted(
                path.relative_to(ROOT).as_posix()
                for path in target.rglob("*")
                if stat.S_ISREG(path.lstat().st_mode)
            )
            assert regular, f"{source}: a directory source that holds no regular file"
            outside += [path for path in regular if not is_input(path)]
        elif not is_input(source):
            outside.append(source)
    if not is_input(DOCKERFILE):
        outside.append(DOCKERFILE)

    assert outside == [], f"sources of the release image that no input matches: {outside}"

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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from gora.domain.release_repository import files, inputs, v1alpha1
from gora.domain.release_repository.tree import ReleaseTree, TreeEntry, load_tree

ROOT = Path(__file__).resolve().parents[2]
RELEASE_REPOSITORY = "github.com/gora-tools/gora"
DOCKERFILE = "deployments/docker/Dockerfile"
ARTIFACT = "gora"

COPY_OR_ADD = re.compile(r"^(?:COPY|ADD)\s+(?P<arguments>.+)$", re.IGNORECASE)
FROM_INSTRUCTION = re.compile(
    r"^FROM\s+(?:--\S+\s+)*(?P<image>\S+)(?:\s+AS\s+(?P<name>\S+))?$", re.IGNORECASE
)
# The stage the pushed image is: the target of the build action and of `make docker-build`
# (tests/unit/test_build_pins.py holds them equal). The stages it is built from are the ones
# whose sources are inputs; the others, the test stage, change no byte of it.
PUSHED_TARGET = "runtime"
ESCAPE_DIRECTIVE = re.compile(r"^\s*#\s*escape\s*=", re.IGNORECASE)


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
    """The instructions of a Dockerfile: comment lines dropped, continuations joined.

    ``# escape=`` changes the character that continues a line, and every join below would be
    wrong. BuildKit reads a directive only at the top of the file; a line elsewhere is a
    comment, and is refused too, so that nothing depends on where it stops looking.
    """
    instructions: list[str] = []
    current = ""
    for line in dockerfile.splitlines():
        if ESCAPE_DIRECTIVE.match(line):
            pytest.fail(f"the parser directive escape changes how lines continue: {line.strip()}")
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


def _refuse_mount_without_from(instruction: str) -> None:
    """``RUN --mount`` without ``from=`` reads the build context, which nothing here checks.

    Only the flags before the command are read. Another type of mount is refused as well:
    the one that needs none of the context is told apart by a rule this does not hold.
    """
    for word in instruction.split()[1:]:
        if not word.startswith("--"):
            break  # the flags end where the command starts
        name, _, value = word.partition("=")
        if name.lower() == "--mount" and "from" not in {
            option.partition("=")[0].lower() for option in value.split(",")
        }:
            pytest.fail(f"a RUN --mount without from= reads the build context: {instruction}")


@dataclass
class Stage:
    """One stage of a Dockerfile: its name (its index when it has none), the image or stage it
    starts from, and its instructions after the ``FROM``."""

    name: str
    base: str
    instructions: list[str]


def _stages(dockerfile: str) -> list[Stage]:
    """The stages of a Dockerfile, in order. A ``FROM`` this cannot resolve to an image or an
    earlier stage by its text is refused."""
    stages: list[Stage] = []
    for instruction in _instructions(dockerfile):
        if instruction.split()[0].upper() != "FROM":
            if stages:
                stages[-1].instructions.append(instruction)
            continue
        found = FROM_INSTRUCTION.match(instruction)
        if found is None or "$" in found["image"]:
            pytest.fail(f"a FROM this parser does not read: {instruction}")
        name = (found["name"] or str(len(stages))).lower()
        stages.append(Stage(name, found["image"].lower(), []))
    return stages


def _stage_sources(stage: Stage) -> list[str]:
    """The build-context sources of every ``COPY`` and ``ADD`` of a stage, a directory without
    its ``/``.

    What cannot be read as a plain path is refused, never skipped: a source this misses is a
    source nothing checks. The same goes for what reads the context some other way: a
    ``RUN --mount`` without ``from=``.
    """
    sources: list[str] = []
    for instruction in stage.instructions:
        if instruction.split()[0].upper() == "RUN":
            _refuse_mount_without_from(instruction)
            continue
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


def _references(stage: Stage) -> list[str]:
    """What a stage is built from, by name or index: the stage it starts ``FROM``, each
    ``COPY --from`` and each ``RUN --mount`` with ``from=``. An image is a name no stage has."""
    found = [stage.base]
    for instruction in stage.instructions:
        keyword = instruction.split()[0].upper()
        for word in instruction.split()[1:]:
            if not word.startswith("--"):
                break  # the flags end where the command starts
            name, _, value = word.partition("=")
            if keyword == "COPY" and name.lower() == "--from":
                found.append(value.lower())
            if keyword == "RUN" and name.lower() == "--mount":
                options = dict(option.partition("=")[::2] for option in value.split(","))
                if "from" in options:
                    found.append(options["from"].lower())
    return found


def _reached(stages: list[Stage], target: str) -> set[str]:
    """The stages the stage ``target`` is built from: itself and everything it reaches by
    ``FROM``, ``COPY --from`` or ``RUN --mount from=``, followed transitively. Each stage is
    read once, so a reference back to a stage already read (``test`` starts ``FROM runtime``
    and a ``runtime`` that copies from ``test``) ends the walk instead of continuing it."""
    names = {stage.name: stage for stage in stages}
    names |= {str(index): stage for index, stage in enumerate(stages)}
    if target not in names:
        pytest.fail(f"the Dockerfile has no stage {target}, which the pushed image is built from")
    reached: set[str] = set()
    todo = [names[target]]
    while todo:
        stage = todo.pop()
        if stage.name in reached:
            continue
        reached.add(stage.name)
        todo += [names[name] for name in _references(stage) if name in names]
    return reached


def _sources(dockerfile: str, target: str | None = None) -> list[str]:
    """The build-context sources of a Dockerfile: of every stage, or, given the stage
    ``target`` the pushed image is built as, of the stages it is built from. Every stage is
    read either way, so that what the parser refuses in one it refuses in all."""
    stages = _stages(dockerfile)
    reached = None if target is None else _reached(stages, target)
    sources: list[str] = []
    for stage in stages:
        found = _stage_sources(stage)
        if reached is None or stage.name in reached:
            sources += found
    return sources


def _outside_inputs(
    dockerfile: str,
    target: str,
    is_input: Callable[[str], bool],
    files: Callable[[str], list[str]],
) -> list[str]:
    """The files of the sources of the pushed image (``files`` lists one source's) that no
    input matches."""
    return [
        path
        for source in _sources(dockerfile, target)
        for path in files(source)
        if not is_input(path)
    ]


def test_the_release_files_of_the_checkout_load_and_give_the_stages_of_the_policy() -> None:
    tree = _tree()

    assert list(tree.artifacts) == [ARTIFACT]
    policy = tree.policy.model
    assert isinstance(policy, v1alpha1.Policy)
    # The list and its order, as the policy file gives them: the order is the order of promotion.
    assert [(stage.name, stage.gate) for stage in policy.spec.stages] == [
        ("dev", "auto"),
        ("staging", "approval"),
        ("prod", "approval"),
    ]
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

    def files(source: str) -> list[str]:
        """The regular files of one source, which must be in the checkout."""
        target = ROOT / source
        assert target.exists(), f"{source}: a source of {DOCKERFILE} that is not in the checkout"
        if not target.is_dir():
            return [source]
        regular = sorted(
            path.relative_to(ROOT).as_posix()
            for path in target.rglob("*")
            if stat.S_ISREG(path.lstat().st_mode)
        )
        assert regular, f"{source}: a directory source that holds no regular file"
        return regular

    dockerfile = (ROOT / DOCKERFILE).read_text(encoding="utf-8")
    sources = _sources(dockerfile, PUSHED_TARGET)
    # Calibration: the parser read the sources of the pushed image, a directory among them.
    assert sources, f"no COPY or ADD source was read from {DOCKERFILE}"
    assert any((ROOT / source).is_dir() for source in sources), sources
    # Calibration: a stage the pushed image is not built from is read too, and its sources are
    # left out. Its source that is no input shows the exclusion is the difference, not assumed.
    left_out = [source for source in _sources(dockerfile) if source not in sources]
    assert left_out, f"every stage of {DOCKERFILE} is one {PUSHED_TARGET} is built from"
    assert [file for source in left_out for file in files(source) if not is_input(file)], left_out

    outside = _outside_inputs(dockerfile, PUSHED_TARGET, is_input, files)
    if not is_input(DOCKERFILE):
        outside.append(DOCKERFILE)

    assert outside == [], f"sources of the release image that no input matches: {outside}"


# Dockerfiles the parser must refuse rather than read wrongly. Each is the smallest file that
# carries the construct, and the reason is a word of the refusal.
UNREAD_DOCKERFILES = [
    pytest.param("# escape=`\nFROM scratch\nCOPY a `\n  ./\n", "escape", id="escape-directive"),
    pytest.param("# ESCAPE = `\nFROM scratch\n", "escape", id="escape-directive-spelt-loosely"),
    pytest.param(
        "FROM scratch\nRUN --mount=type=bind,source=src,target=/src ls /src\n",
        "--mount",
        id="bind-mount-of-the-context",
    ),
    pytest.param(
        "FROM scratch\nRUN --mount=target=/src,source=src ls /src\n",
        "--mount",
        id="mount-without-a-type",
    ),
    pytest.param(
        "FROM scratch\nRUN --network=none --mount=type=bind,source=src,target=/src ls\n",
        "--mount",
        id="mount-after-another-flag",
    ),
    pytest.param(
        "FROM scratch\nRUN --mount=type=cache,target=/root/.cache true\n",
        "--mount",
        id="mount-of-another-type",
    ),
    pytest.param('FROM scratch\nCOPY ["a", "/b/"]\n', "JSON", id="json-form"),
    pytest.param("FROM scratch\nCOPY <<EOF /a\nbody\nEOF\n", "heredoc", id="heredoc-source"),
    pytest.param("FROM scratch\nADD https://example.com/a.tar /a\n", "URL", id="url-source"),
    pytest.param("FROM scratch\nCOPY src/*.py /dst/\n", "wildcard", id="wildcard-source"),
]


@pytest.mark.parametrize(("dockerfile", "reason"), UNREAD_DOCKERFILES)
def test_a_dockerfile_the_parser_cannot_read_is_refused(dockerfile: str, reason: str) -> None:
    with pytest.raises(pytest.fail.Exception, match=reason):
        _sources(dockerfile)


def test_a_mount_from_another_stage_reads_nothing_of_the_context() -> None:
    dockerfile = (
        "FROM scratch AS build\n"
        "FROM scratch\n"
        "RUN --mount=type=bind,from=build,source=/app,target=/app ls /app\n"
        "COPY src/ src/\n"
    )

    assert _sources(dockerfile) == ["src"]


# --- the sources of the pushed image are those of the stages it is built from -------------------

BUILD = "FROM scratch AS build\nCOPY src/ src/\n"
TEST = "FROM scratch AS test\nCOPY tests/ tests/\n"
RUNTIME = "FROM build AS runtime\n"


def _source_is_src(path: str) -> bool:
    return path == "src" or path.startswith("src/")


def _outside(dockerfile: str) -> list[str]:
    return _outside_inputs(dockerfile, "runtime", _source_is_src, lambda source: [source])


def test_a_runtime_that_copies_from_a_stage_with_a_source_that_is_no_input_is_found() -> None:
    """The same file without the ``COPY --from`` passes: the source is read only when the
    pushed image reaches its stage."""
    assert _outside(BUILD + TEST + RUNTIME + "COPY --from=test /app/tests /x\n") == ["tests"]
    assert _outside(BUILD + TEST + RUNTIME) == []


@pytest.mark.parametrize(
    "dockerfile",
    [
        pytest.param(BUILD + TEST + RUNTIME + "COPY --from=1 /t /t\n", id="by-index"),
        pytest.param(
            BUILD + TEST + RUNTIME + "RUN --mount=type=bind,from=test,source=/t,target=/t ls /t\n",
            id="mount",
        ),
        pytest.param(
            BUILD + TEST.replace("scratch AS test", "build AS test") + "FROM test AS runtime\n",
            id="from-a-stage-built-on-it",
        ),
        pytest.param(
            BUILD + TEST + "FROM test AS mid\n" + RUNTIME + "COPY --from=mid /t /t\n",
            id="transitive",
        ),
        pytest.param(
            BUILD + "FROM build AS runtime\nCOPY --from=test /t /t\n" + "FROM runtime AS test\n"
            "COPY tests/ tests/\n",
            id="cycle",
        ),
    ],
)
def test_every_way_the_pushed_image_is_built_from_a_stage_reads_its_sources(
    dockerfile: str,
) -> None:
    """A walk that ends on a stage it has read: ``cycle`` is what the red-first proof makes,
    ``test`` starting ``FROM runtime`` and ``runtime`` copying from ``test``."""
    assert _outside(dockerfile) == ["tests"]


def test_a_stage_the_pushed_image_is_not_built_from_is_still_read_whole() -> None:
    """What the parser refuses, it refuses in every stage: the left-out one included."""
    dockerfile = BUILD + RUNTIME + "FROM scratch AS test\nRUN --mount=target=/t,source=tests ls\n"
    with pytest.raises(pytest.fail.Exception, match="--mount"):
        _sources(dockerfile, "runtime")


def test_a_dockerfile_without_the_pushed_stage_is_refused() -> None:
    with pytest.raises(pytest.fail.Exception, match="no stage runtime"):
        _sources("FROM scratch AS other\n", "runtime")

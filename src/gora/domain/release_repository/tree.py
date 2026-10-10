"""The rules between the release files of one commit (ADR-026 sections 2, 3, 5, 9 and 14).

Pure: the entries of one commit in, a tree or problems out. ``load_tree`` checks each file on its
own (``files.parse``), then the policy, the declarations, the state files and the rejections
against each other, and reports every problem of the tree as ``InvalidReleaseTreeError``: the
file, the field path and a constant rule, never a value from a document. The code-owner file, and
the rules that compare two commits or need history or the host, are not here. No command uses it
yet.
"""

import itertools
import types
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from gora.domain.release.identifiers import SOURCE_REPOSITORY
from gora.domain.release_repository import files, inputs, v1alpha1, yaml12
from gora.domain.release_repository.files import (
    InvalidReleaseFileError,
    Location,
    Problem,
    ReleaseFile,
)
from gora.domain.release_repository.v1alpha1 import Problems

REGULAR = "100644"
# The other modes git gives a blob or a gitlink, each refused with its rule (section 3).
REFUSED_MODES = {
    "120000": "a symbolic link is not a release file",
    "100755": "an executable file is not a release file",
    "160000": "a gitlink is not a release file",
}
_POLICY = files.POLICY_PATH.encode("ascii")
_ROOTS = tuple(f"{root}/".encode("ascii") for root in files.LAYOUT_ROOTS)
CODEOWNERS = (".github", "CODEOWNERS")
# What the fixed components of an input glob never begin with in the release repository: a
# release file or a release directory (section 9).
RELEASE_PATHS = (
    (files.POLICY_PATH,),
    CODEOWNERS,
    *((root,) for root in files.LAYOUT_ROOTS),
)

RULE_POLICY_REQUIRED = "required: a release repository has one policy"
RULE_BOTH_NAMES = "is both an artifact name and a stage name"
RULE_NO_BUILDER = "is not a builder of the policy's spec.builders"
RULE_NOT_A_STAGE = "is not a stage of the policy's spec.stages"
RULE_AUTO_APPROVERS = (
    "must be absent: the stage is auto in the policy, and an auto stage names no approvers"
)
RULE_NO_REPORTER = "has no reporter in the policy's spec.reporters"
RULE_NO_APPROVERS = (
    "names no approvers for the stage at spec.stages[{index}] of the policy, which leaves them "
    "to the declarations"
)
RULE_EMPTY_PREFIX = (
    "must not be empty: an earlier declaration, in path order, of the same source repository "
    "has the empty tag prefix"
)
RULE_RELEASE_INPUT = "names a release file or a release directory, which is never an input"


@dataclass(frozen=True)
class TreeEntry:
    """One blob or gitlink of a commit, as git gives it (``ls-tree -r -z``, or the host's tree
    API): its path relative to the root, its mode, and its bytes, empty for a gitlink."""

    path: bytes
    mode: str
    data: bytes


class InvalidReleaseTreeError(ValueError):
    """A tree refused, with every problem of its files, in the order of their paths."""

    def __init__(self, problems: tuple[Problem, ...]) -> None:
        self.problems = problems
        super().__init__("\n".join(str(problem) for problem in problems))


@dataclass(frozen=True)
class EffectiveStage:
    """What a stage asks of one artifact: the policy's stage with the declaration's additions."""

    name: str
    gate: Literal["auto", "approval"]
    # The policy's groups in its order, then the declaration's new ones in theirs; for a group
    # both name, the higher number; empty for an auto stage.
    approvers: Mapping[str, int]
    # The policy's in its order, then the declaration's that the policy does not name.
    verify: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseTree:
    """The release files of one commit that hold, each alone and against each other.

    Every mapping is read-only, in path order.
    """

    policy: ReleaseFile
    artifacts: Mapping[str, ReleaseFile]
    states: Mapping[tuple[str, str], ReleaseFile]
    rejections: Mapping[tuple[str, str], ReleaseFile]

    def retiring_artifacts(self) -> frozenset[str]:
        """The artifacts with a state file and no declaration (section 14)."""
        return frozenset(artifact for _, artifact in self.states if artifact not in self.artifacts)

    def removed_stages(self) -> frozenset[str]:
        """The stage directories of ``state/`` that the policy does not name (section 14)."""
        stages = {stage.name for stage in _policy(self.policy).spec.stages}
        return frozenset(stage for stage, _ in self.states if stage not in stages)

    def stage_for(self, stage: str, artifact: str) -> EffectiveStage:
        """What ``stage`` asks of ``artifact`` (section 5); ``KeyError`` for a stage not in the
        policy or an artifact with no declaration."""
        entry = next((s for s in _policy(self.policy).spec.stages if s.name == stage), None)
        if entry is None:
            raise KeyError(stage)
        addition = _artifact(self.artifacts[artifact]).spec.stages.get(stage)
        approvers = dict(entry.approvers)
        verify = list(entry.verify)
        if addition is not None:
            for group, number in addition.approvers.items():
                approvers[group] = max(approvers.get(group, number), number)
            verify += [name for name in addition.verify if name not in verify]
        return EffectiveStage(stage, entry.gate, types.MappingProxyType(approvers), tuple(verify))


def is_layout_path(path: bytes) -> bool:
    """Whether a path of a commit is in the layout: ``release-policy.yaml``, or a path under
    ``artifacts/``, ``state/`` or ``rejections/``, compared as bytes."""
    return path == _POLICY or path.startswith(_ROOTS)


def load_tree(entries: Iterable[TreeEntry], *, release_repository: str) -> ReleaseTree:
    """The release files of one commit, or every problem of the tree.

    ``entries`` are every blob and gitlink of the commit whose path ``is_layout_path`` accepts,
    and nothing else; ``release_repository`` is the release repository's path on the host. A
    caller's error is a ``ValueError``. Each entry is checked on its own, stopping at its first
    refusal: its path, its mode, its file. Then the files that hold are checked against each
    other. Every problem, ordered by the path of its file, is one ``InvalidReleaseTreeError``.
    """
    if not SOURCE_REPOSITORY.is_valid(release_repository):
        raise ValueError(f"release_repository {SOURCE_REPOSITORY.rule}")
    refused: dict[bytes, tuple[Problem, ...]] = {}
    located: list[Location] = []
    parsed: dict[bytes, ReleaseFile] = {}
    for entry in _checked(entries):
        # Never strict and never backslashreplace: an undecodable byte is shown as \udcNN,
        # which no UTF-8 path can produce, and every name grammar is ASCII, so locate refuses.
        path = entry.path.decode("utf-8", "surrogateescape")
        try:
            location = files.locate(path)
        except InvalidReleaseFileError as exc:
            refused[entry.path] = exc.problems
            continue
        assert location is not None  # is_layout_path accepted the path
        located.append(location)
        if entry.mode != REGULAR:
            rule = REFUSED_MODES[entry.mode]
            refused[entry.path] = (Problem(path, yaml12.ROOT, None, None, rule),)
            continue
        try:
            parsed[entry.path] = files.parse(path, entry.data)
        except InvalidReleaseFileError as exc:
            refused[entry.path] = exc.problems
    if _POLICY not in parsed and _POLICY not in refused:
        problem = Problem(files.POLICY_PATH, yaml12.ROOT, None, None, RULE_POLICY_REQUIRED)
        refused[_POLICY] = (problem,)
    for key, pairs in _between(parsed, located, release_repository).items():
        document = parsed[key].document
        source = key.decode("ascii")  # located, so in the ASCII of the name grammars
        refused[key] = tuple(
            Problem(source, at, None, None, rule) for at, rule in files.in_order(document, pairs)
        )
    if refused:
        raise InvalidReleaseTreeError(
            tuple(problem for key in sorted(refused) for problem in refused[key])
        )
    return _tree(parsed)


def _checked(entries: Iterable[TreeEntry]) -> list[TreeEntry]:
    """The entries in path order; ``ValueError`` for an entry the caller must not pass."""
    ordered = sorted(entries, key=lambda entry: entry.path)
    for entry in ordered:
        if not is_layout_path(entry.path):
            raise ValueError("an entry is not in the layout of a release repository")
        if entry.mode != REGULAR and entry.mode not in REFUSED_MODES:
            raise ValueError("an entry's mode is not a blob's or a gitlink's")
    if any(earlier.path == later.path for earlier, later in itertools.pairwise(ordered)):
        raise ValueError("two entries have one path")
    return ordered


def _between(
    parsed: dict[bytes, ReleaseFile], located: list[Location], release_repository: str
) -> dict[bytes, Problems]:
    """The problems of the rules between files, by the file they are reported in.

    The rules that need the policy run only when it parsed.
    """
    policy = _policy(parsed[_POLICY]) if _POLICY in parsed else None
    both = _both_names(located, policy)
    empty_prefix: set[str] = set()  # the repositories of the declarations so far with one
    found: dict[bytes, Problems] = {}
    for key, release_file in parsed.items():
        model = release_file.model
        problems = _namespace(model, both)
        if isinstance(model, v1alpha1.Artifact):
            if policy is not None:
                problems += _additions(model, policy)
                problems += _approvers_left(model, policy)
            source = model.spec.source
            if source.tag_prefix == "":
                if source.repository in empty_prefix:
                    problems.append(("spec.source.tagPrefix", RULE_EMPTY_PREFIX))
                empty_prefix.add(source.repository)
            problems += _release_inputs(model, release_repository)
        if problems:
            found[key] = problems
    return found


def _both_names(located: list[Location], policy: v1alpha1.Policy | None) -> frozenset[str]:
    """The names that are both an artifact's and a stage's (section 3): from the policy's stages,
    when it parsed, and from every located path, whether or not its file holds."""
    stages = {stage.name for stage in policy.spec.stages} if policy is not None else set()
    artifacts: set[str] = set()
    for location in located:
        if location.stage is not None:
            stages.add(location.stage)
        if location.artifact is not None:
            artifacts.add(location.artifact)
    return frozenset(stages & artifacts)


def _namespace(model: object, both: frozenset[str]) -> Problems:
    """Rule 1, in a file that holds a name of both sets, at the field that holds it."""
    held: tuple[tuple[str, str], ...] = ()
    if isinstance(model, v1alpha1.Artifact):
        held = (("metadata.name", model.metadata.name),)
    elif isinstance(model, v1alpha1.StageState):
        metadata = model.metadata
        held = (("metadata.stage", metadata.stage), ("metadata.artifact", metadata.artifact))
    elif isinstance(model, v1alpha1.Rejection):
        held = (("metadata.artifact", model.metadata.artifact),)
    return [(at, RULE_BOTH_NAMES) for at, name in held if name in both]


def _additions(model: v1alpha1.Artifact, policy: v1alpha1.Policy) -> Problems:
    """Rules 2 to 4: a declaration names the policy's builders, adds to the policy's stages only,
    approvers only on an approval stage, and verifications that have a reporter (sections 4, 5)."""
    spec = model.spec
    problems: Problems = [
        (f"spec.builders[{index}]", RULE_NO_BUILDER)
        for index, name in enumerate(spec.builders)
        if name not in policy.spec.builders
    ]
    gates = {stage.name: stage.gate for stage in policy.spec.stages}
    for position, (name, addition) in enumerate(spec.stages.items(), start=1):
        at = v1alpha1.key("spec.stages", name, position)
        if name not in gates:
            problems.append((at, RULE_NOT_A_STAGE))
        elif gates[name] == "auto" and addition.approvers:
            problems.append((f"{at}.approvers", RULE_AUTO_APPROVERS))
        problems += [
            (f"{at}.verify[{index}]", RULE_NO_REPORTER)
            for index, verification in enumerate(addition.verify)
            if verification not in policy.spec.reporters
        ]
    return problems


def _approvers_left(model: v1alpha1.Artifact, policy: v1alpha1.Policy) -> Problems:
    """Rule 5: an approval stage whose policy entry names no approvers has the declaration's
    (sections 4, 5). prod, when its gate is approval, names its own: the policy's rule."""
    problems: Problems = []
    for index, stage in enumerate(policy.spec.stages):
        if stage.gate != "approval" or stage.approvers:
            continue
        addition = model.spec.stages.get(stage.name)
        if addition is None or not addition.approvers:
            problems.append(("spec.stages", RULE_NO_APPROVERS.format(index=index)))
    return problems


def _release_inputs(model: v1alpha1.Artifact, release_repository: str) -> Problems:
    """Rule 7: in a declaration of the release repository, no glob's fixed components name a
    release file or a release directory (section 9). Another repository's paths are its own."""
    source = model.spec.source
    if source.repository != release_repository:
        return []
    return [
        (f"spec.source.inputs[{index}]", RULE_RELEASE_INPUT)
        for index, glob in enumerate(source.inputs)
        if any(_begins_with(inputs.fixed_components(glob), path) for path in RELEASE_PATHS)
    ]


def _begins_with(components: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return components[: len(prefix)] == prefix


def _tree(parsed: dict[bytes, ReleaseFile]) -> ReleaseTree:
    artifacts: dict[str, ReleaseFile] = {}
    states: dict[tuple[str, str], ReleaseFile] = {}
    rejections: dict[tuple[str, str], ReleaseFile] = {}
    for release_file in parsed.values():
        model = release_file.model
        if isinstance(model, v1alpha1.Artifact):
            artifacts[model.metadata.name] = release_file
        elif isinstance(model, v1alpha1.StageState):
            states[(model.metadata.stage, model.metadata.artifact)] = release_file
        elif isinstance(model, v1alpha1.Rejection):
            rejections[(model.metadata.artifact, model.metadata.version)] = release_file
    return ReleaseTree(
        policy=parsed[_POLICY],
        artifacts=types.MappingProxyType(artifacts),
        states=types.MappingProxyType(states),
        rejections=types.MappingProxyType(rejections),
    )


def _policy(release_file: ReleaseFile) -> v1alpha1.Policy:
    model = release_file.model
    assert isinstance(model, v1alpha1.Policy)
    return model


def _artifact(release_file: ReleaseFile) -> v1alpha1.Artifact:
    model = release_file.model
    assert isinstance(model, v1alpha1.Artifact)
    return model

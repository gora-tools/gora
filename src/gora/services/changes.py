"""``gora changes``: the artifacts a commit of ``main`` is a candidate of (release-model section
11; ADR-022 section 4, ADR-023 section 3, ADR-026 section 9).

The one implementation the CLI calls and the API will, with the sequencing in one place
(ADR-008). It reads and writes nothing else: the head of ``main`` and the tags from the code
host, the release files and the history from a full local clone, never fetched (ADR-006), and
it opens the clone only after its first step.

Built for ``S == R``: the project, the release repository, is also the source repository of
every artifact the answer lists, so the head of its ``main`` is the head both are read at. A
source other than the project is the separate shape, decided and not built (ADR-026 section 9),
and is refused by name before anything is read.

In order:

1. A source other than the project is refused (``ChangesError``).
2. The clone is opened, the head of ``main`` asked of the host, and the commit asked about
   defaults to it.
3. L1, L5: the commit is the head or a first-parent commit of it, else ``RefusedError``.
4. The declarations at the head are loaded and checked as one tree (``InvalidReleaseTreeError``
   otherwise); the artifacts listed are those whose source is the source asked about.
5. Each artifact is judged by ``candidates.candidate``, against its newest registered candidate,
   the rc tag of the highest version the host holds under its tag prefix, or, while it has none,
   against the commit that declared it (``GitRepository.added_at``). A commit that is not its
   candidate leaves it out (L8, L10, L11 each mean "not a candidate here"); a newest rc tag that
   is not an annotated tag that records its candidate, or whose commit is off the first-parent
   chain of ``main``, is a refusal naming the artifact, and no older tag is used in its place.

No write, no journal: the command only reads (ADR-017). A caller's error, a value outside its
grammar, is a ``ValueError``; the CLI checks every value before it calls.
"""

import os

from pydantic import BaseModel, ConfigDict

from gora.adapters.git import GitRepository, NotFirstParentError
from gora.domain.release.candidates import (
    Declaration,
    RefusedError,
    Registered,
    candidate,
)
from gora.domain.release.tags import RecordError, newest_rc_tag, recorded
from gora.domain.release.versions import Version
from gora.domain.release_repository import v1alpha1
from gora.domain.release_repository.tree import is_layout_path, load_tree
from gora.domain.release_repository.v1alpha1 import Name, RcVersion, SourceCommit, SourceRepository
from gora.services.code_host import CodeHost

BRANCH = "main"
# The paths of the release files, whose entries are read at the head: the policy, and the
# declarations, the states and the rejections.
LAYOUT = ("release-policy.yaml", "artifacts", "state", "rejections")

SEPARATE_SHAPE = (
    "the source repository is not the release repository: a source other than the project "
    "is decided and not built (ADR-026 section 9)"
)
L1_NOT_FIRST_PARENT = "the commit is not a first-parent commit of main"
LIGHTWEIGHT = (
    "the newest rc tag is lightweight: GORA records a candidate in an annotated tag, and "
    "uses no older tag in its place"
)
NOT_ON_MAIN = (
    "the commit of the newest rc tag is not a first-parent commit of main, and no older tag "
    "is used in its place"
)


class ChangesError(Exception):
    """The service refuses: the separate shape, or a newest rc tag of an artifact that cannot be
    used. The reason names the condition and holds no value of the caller's."""


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RegisteredCandidate(_Result):
    version: RcVersion
    commit: SourceCommit


class ChangedArtifact(_Result):
    name: Name
    version: RcVersion
    registered: RegisteredCandidate | None


class Changes(_Result):
    """The artifacts a commit is a candidate of, in name order: the body of ``--output json``
    and of the API's ``GET /v1/projects/{p}/changes``, whose schema is
    ``contracts/schemas/api/v1/changes.json``."""

    project: SourceRepository
    source: SourceRepository
    commit: SourceCommit
    artifacts: list[ChangedArtifact]


def changes(
    *,
    project: str,
    source: str,
    commit: str | None,
    host: CodeHost,
    root: str | os.PathLike[str],
    git: str,
) -> Changes:
    """The artifacts ``commit`` is a candidate of, for the project ``project`` whose artifacts
    are built from ``source``, which is the project: ``host`` is its code host, ``root`` its
    full local clone and ``git`` the absolute name of the git to run. ``commit`` is a full
    object name, or None for the head of ``main`` as the host gives it."""
    if source != project:
        raise ChangesError(SEPARATE_SHAPE)
    clone = GitRepository(root, git=git)
    head = host.branch_head(BRANCH)
    commit = commit or head
    try:
        clone.first_parent(head, after=commit)
    except NotFirstParentError:
        raise RefusedError("L1", L1_NOT_FIRST_PARENT) from None
    entries = [
        entry
        for entry in clone.entries(head, LAYOUT)
        if is_layout_path(entry.path)  # whatever the clone's trees hold, the layout's only
    ]
    tree = load_tree(entries, release_repository=project)
    listed = []
    for name in sorted(tree.artifacts):
        model = tree.artifacts[name].model
        assert isinstance(model, v1alpha1.Artifact)  # a file of artifacts/ is a declaration
        if model.spec.source.repository != source:
            continue
        found = _changed(name, model, commit=commit, head=head, clone=clone, host=host)
        if found is not None:
            listed.append(found)
    return Changes(project=project, source=source, commit=commit, artifacts=listed)


def _changed(
    name: str,
    model: v1alpha1.Artifact,
    *,
    commit: str,
    head: str,
    clone: GitRepository,
    host: CodeHost,
) -> ChangedArtifact | None:
    """The artifact with its next version when ``commit`` is its candidate, else None."""
    spec = model.spec
    prefix = spec.source.tag_prefix
    declaration = Declaration(
        tuple(spec.source.inputs),
        Version.parse(spec.initial_version),
        spec.stable,
        in_release_repository=True,
    )
    tag_name = newest_rc_tag(host.tag_names(prefix), artifact=name, tag_prefix=prefix)
    if tag_name is None:
        # The first candidate: judged against the commit that declared the artifact.
        boundary = clone.added_at(head, f"artifacts/{name}/artifact.yaml")
        history = clone.first_parent(head, after=boundary)
        newest = None
        differ = None
    else:
        tag = host.tag(tag_name)
        if tag.message is None:
            raise ChangesError(f"{name}: {LIGHTWEIGHT}")
        try:
            record = recorded(tag_name, tag.message, artifact=name, tag_prefix=prefix)
        except RecordError as error:
            raise ChangesError(f"{name}: {error.reason}") from None
        try:
            since = clone.first_parent(head, after=tag.commit)
        except NotFirstParentError:
            raise ChangesError(f"{name}: {NOT_ON_MAIN}") from None
        newest = Registered(tag.commit, record.version)
        history = (clone.commit(tag.commit), *since)
        differ = clone.differ(tag.commit, commit)
    try:
        found = candidate(declaration, history, commit, registered=newest, differ=differ)
    except RefusedError:
        return None
    registered = (
        None
        if newest is None
        else RegisteredCandidate(version=str(newest.version), commit=newest.commit)
    )
    return ChangedArtifact(name=name, version=str(found.version), registered=registered)

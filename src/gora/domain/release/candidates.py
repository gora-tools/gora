"""Which commit is an artifact's candidate, and its version (ADR-022 section 4, ADR-024 section 1,
ADR-026 section 9, ADR-027 section 4; L8, L10, L11 of release-model section 10).

Pure: no git, no adapter, no command, no I/O. The caller reads the history of the source
repository's ``main`` and the paths each commit changed, and passes them; this module decides what
it is given. A commit that already carries the artifact's rc tag is answered by registration from
its tag before it asks here (ADR-024 section 1). A commit off the first parent of ``main`` (L1) is
refused by whoever reads git, before it asks here: the domain cannot see it.

``candidate`` takes the artifact's declaration as it stands at the head of the release
repository's ``main``; the ``history``, the first-parent commits of the source repository's
``main`` merged after the artifact was declared, oldest first and at least up to the requested
commit; the ``requested`` commit's object name; and, once the artifact has a registered
candidate, that ``registered`` candidate and ``differ``, the paths in which the requested commit's
tree differs from the registered candidate's. It answers a ``Candidate``, or refuses with a
``RefusedError``: a law and a reason, never a ``ValueError``, which is a caller's error.

A caller's errors come first, each a ``ValueError`` holding one fixed text, in this order, before
any refusal: no input in the declaration; a glob that ``inputs`` refuses, whatever the paths; a
final initial version; an object name, in the history or requested, that is not one; a commit
twice in the history; ``registered`` without ``differ``, or ``differ`` without ``registered``; a
final registered version; a registered candidate that is not a commit of the history.

Then, at the first that applies:

1. L8. The requested commit is not in the history: it is not a commit on ``main`` merged after the
   artifact's declaration, whether it is off ``main`` or was merged before.
2. With no registered candidate, the first candidate (below).
3. L10. The requested commit is the registered candidate's own commit, or comes before it.
4. A path of ``differ`` that no commit since the registered candidate, up to and including the
   requested one, changed is a caller's error: one history cannot give it. This comes before 5.
5. L8. No path of ``differ`` is an input: the artifact's inputs at the commit do not differ from
   those of its newest registered candidate. L8 compares the trees, so a commit that changes only
   release paths is a candidate when it carries a change of inputs whose build was never
   registered, and inputs changed and changed back are none.
6. The version is the registered candidate's, raised by the highest bump among the messages of the
   commits since it, up to and including the requested one, that changed an input, oldest first:
   a build that failed is counted by the next. ``stable`` leaves major version ``0`` here.

The first candidate. With no registered candidate L8 has no tree to compare with, so the requested
commit is judged by its own change (ADR-026 section 9), not by the history before it:

1. L8. It changes nothing but release paths, in the release repository, or changes nothing at all.
   In another repository every path is its own.
2. L11. The declaration is stable and its initial version is below ``1.0.0-rc``: asked both to
   take the initial version and to be at least ``1.0.0-rc``, registration refuses until a person
   raises ``initialVersion``.
3. Otherwise it takes the declaration's initial version, whatever its message says and whether or
   not it changes an input; its changes are its own commit (ADR-027 section 4).

Every message is value-free, as in ``versions``: no object name, version, path or glob of an
input. A caller's error states the rule.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from gora.domain.release.identifiers import SOURCE_COMMIT
from gora.domain.release.versions import Version, bump_of, change, next_version
from gora.domain.release_repository.inputs import input_paths, is_release_path

L8_NOT_AFTER_DECLARATION = (
    "the commit is not a first-parent commit of main merged after the artifact was declared"
)
L10_REGISTERED = "the commit is the artifact's newest registered candidate"
L10_OLDER = "the commit is older than the artifact's newest registered candidate"
L8_RELEASE_PATHS = (
    "the commit changes nothing but release paths, and the artifact has no registered candidate"
)
L8_SAME_INPUTS = (
    "the artifact's inputs at the commit do not differ from those of its newest registered "
    "candidate"
)
L11_STABLE_BELOW_ONE = (
    "the declaration is stable and its initial version is below 1.0.0-rc: the first candidate "
    "waits until initialVersion is at least 1.0.0-rc"
)

# A caller's errors.
NO_INPUT = "a declaration names at least one input"
INITIAL_FINAL = "the initial version is an rc version"
TWICE = "a commit appears once in the history"
DIFFER_WITHOUT_CANDIDATE = "the paths that differ are given exactly when a candidate is registered"
REGISTERED_FINAL = "the registered candidate's version is an rc version"
REGISTERED_ELSEWHERE = "the registered candidate is a commit of the history"
UNCHANGED_DIFFER = (
    "every path that differs from the registered candidate was changed by a commit since it"
)


@dataclass(frozen=True)
class Commit:
    """A commit of the source repository's ``main``, as git gives it."""

    sha: str  # the object name
    message: str  # the squash commit's subject and body
    # The paths it changed against its first parent, both sides of a rename.
    changed: frozenset[bytes]


@dataclass(frozen=True)
class Declaration:
    """What candidacy reads of a declaration at the head of the release repository's ``main``."""

    inputs: tuple[str, ...]
    initial: Version
    stable: bool
    in_release_repository: bool


@dataclass(frozen=True)
class Registered:
    """The artifact's newest registered candidate."""

    commit: str
    version: Version


@dataclass(frozen=True)
class Candidate:
    """A candidate: its commit, its version and the commits the version counts and the change
    log lists, oldest first."""

    commit: Commit
    version: Version
    changes: tuple[Commit, ...]


class RefusedError(Exception):
    """A commit is refused as a candidate by a law. Not a ``ValueError``: that is the caller's."""

    def __init__(self, law: str, reason: str) -> None:
        super().__init__(law, reason)  # so that it pickles
        self.law = law
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.law}: {self.reason}"


def candidate(
    declaration: Declaration,
    history: Sequence[Commit],
    requested: str,
    *,
    registered: Registered | None = None,
    differ: Iterable[bytes] | None = None,
) -> Candidate:
    """The candidate ``requested`` is, or a ``RefusedError`` naming the law that refuses it."""
    _check_declaration(declaration)
    position = _positions(history, requested)
    given = _given(registered, differ, position)
    if requested not in position:
        raise RefusedError("L8", L8_NOT_AFTER_DECLARATION)
    if given is None:
        return _first(declaration, history[position[requested]])
    return _later(declaration, history, position, requested, *given)


def _check_declaration(declaration: Declaration) -> None:
    if not declaration.inputs:
        raise ValueError(NO_INPUT)
    input_paths(declaration.inputs, (), in_release_repository=False)  # a glob it refuses raises
    if declaration.initial.final:
        raise ValueError(INITIAL_FINAL)


def _positions(history: Sequence[Commit], requested: str) -> dict[str, int]:
    """The place of each commit of the history, by its object name."""
    for sha in (*(commit.sha for commit in history), requested):
        if not SOURCE_COMMIT.is_valid(sha):
            raise ValueError(f"source commit {SOURCE_COMMIT.rule}")
    position: dict[str, int] = {}
    for index, commit in enumerate(history):
        if commit.sha in position:
            raise ValueError(TWICE)
        position[commit.sha] = index
    return position


def _given(
    registered: Registered | None, differ: Iterable[bytes] | None, position: dict[str, int]
) -> tuple[Registered, Iterable[bytes]] | None:
    """The registered candidate and the paths that differ, or None while there is none."""
    if registered is None and differ is None:
        return None
    if registered is None or differ is None:
        raise ValueError(DIFFER_WITHOUT_CANDIDATE)
    if registered.version.final:
        raise ValueError(REGISTERED_FINAL)
    if registered.commit not in position:
        raise ValueError(REGISTERED_ELSEWHERE)
    return registered, differ


def _first(declaration: Declaration, commit: Commit) -> Candidate:
    """The first candidate (ADR-026 section 9): judged by its own change."""
    only_release = declaration.in_release_repository and all(
        is_release_path(path) for path in commit.changed
    )
    if not commit.changed or only_release:
        raise RefusedError("L8", L8_RELEASE_PATHS)
    if declaration.stable and declaration.initial.major == 0:
        raise RefusedError("L11", L11_STABLE_BELOW_ONE)
    return Candidate(commit, declaration.initial, (commit,))


def _later(
    declaration: Declaration,
    history: Sequence[Commit],
    position: dict[str, int],
    requested: str,
    registered: Registered,
    differ: Iterable[bytes],
) -> Candidate:
    """A candidate after a registered one: L10, then L8 over the trees, then its version."""
    after, here = position[registered.commit], position[requested]
    if here == after:
        raise RefusedError("L10", L10_REGISTERED)
    if here < after:
        raise RefusedError("L10", L10_OLDER)
    since = history[after + 1 : here + 1]
    differing = frozenset(differ)
    if not differing <= frozenset(path for commit in since for path in commit.changed):
        raise ValueError(UNCHANGED_DIFFER)
    where = declaration.in_release_repository
    if not input_paths(declaration.inputs, differing, in_release_repository=where):
        raise RefusedError("L8", L8_SAME_INPUTS)
    changes = tuple(
        commit
        for commit in since
        if input_paths(declaration.inputs, commit.changed, in_release_repository=where)
    )
    bump = bump_of(change(commit.message) for commit in changes)
    version = next_version(registered.version, bump, stable=declaration.stable)
    return Candidate(history[here], version, changes)

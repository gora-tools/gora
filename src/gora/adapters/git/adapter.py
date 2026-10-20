"""Git adapter: read-only, local, bounded (ADR-006, ADR-018 section 4, ADR-026 section 9).

GORA reads the content of a repository, its commits, trees and blobs, from a local clone, and
its state, the head of ``main`` and the tags, from the code host (L10, L22): a clone is a cache of
content, addressed by object name. The adapter holds it to that address: every object it bases an
answer on, each commit, each tree on the way and each blob, is hashed and compared with its name
before it is used. git compares an object with its name only where it parses it with
``parse_object``: ``cat-file`` reads objects without it and ``rev-list`` walks with the check
skipped, so both read a loose object rewritten under its own name as it is (measured on git 2.43
and 2.56, and so do ``diff-tree`` and ``log``). An object that does not hash to its name is
refused, and so is one the clone lacks: "fetch it", and nothing is ever fetched. ``rev-list``
parses the first-parent chain of a head down to the ``after`` it is given, or to the root for an
``after`` off the chain, unverified, and prints the names; the adapter reads and hashes ``head``,
``after`` and the commits between them, and for an ``after`` off the chain one commit more before
it refuses. A shallow clone, a
clone git made with ``--filter`` (one holding a promisor pack) and a clone that borrows another
repository's objects are refused before anything is computed.

Git is the object store and nothing more. The adapter runs three commands and no other
(``Command``): ``rev-parse`` to ask whether the clone is shallow, ``cat-file --batch`` to read
objects, and ``rev-list --first-parent`` to propose a chain of commits, which the adapter then
verifies link by link from the commit objects it read itself. It parses commits and trees itself
and computes what a commit changed by comparing trees, so no setting of git's that shapes a
listing or a diff has anything to act on: on a clone that sets every setting listed in
``SETTINGS`` of ``tests/integration/test_git_adapter.py``, each shown there to change plain git's
output or no object read, the adapter's answers are those of a copy that sets none. The calls
that remain run with:

- an environment built from ``PATH`` alone, so no variable of the caller's (``GIT_DIR``,
  ``GIT_OBJECT_DIRECTORY``, ``GIT_CONFIG_PARAMETERS``) reaches git: no system or global
  configuration and no system attributes, no terminal prompt, no replace objects and a graft
  file that cannot exist, no lazy fetch, and ``GIT_ALLOW_PROTOCOL`` empty, so git allows no
  transport whatever a repository's ``protocol.<name>.allow`` says, and a promisor remote is
  never asked even by a git that ignores ``GIT_NO_LAZY_FETCH``;
- on the command line, ``core.commitGraph=false``, so ``rev-list`` proposes the chain the commit
  objects hold rather than a commit-graph out of step with them, which would be refused;
- the root of the file system as the working directory, so no relative entry of ``PATH``
  resolves inside the clone.

None of the three reads an index or a work tree, so none runs a program a repository names
(``core.fsmonitor``, filters, textconv, hooks): a test runs the adapter on a repository that
names one in every such place, and shows that the same configuration runs it for ``git status``.
The only program a repository can name for an object read is a promisor remote's, which no
transport reaches, and a test shows that too.

Two kinds of guard hold this, and each is held by its own kind of test. A guard that answers a
threat a test can build is shown needed: without it, a test turns red by what git then does
(a rewritten object read, a transport run, a lazy fetch tried, a graft followed). The rest
narrow what git is given, and on the git a test runs no answer depends on them: no system or
global configuration (the settings test sets ``SETTINGS`` in system and global files too, with
the two variables dropped, and the answers do not change), no system attributes, no terminal
prompt, no pager, and C messages. Those are held as a literal by one test of ``environment``,
so dropping one is a red test, and nothing is claimed for them beyond what they narrow.

It names the repository with ``--git-dir``, so git discovers none, and git checks who owns a
repository only when it discovers it: a checkout another user owns, as a CI container's usually
is, is read with no ``safe.directory`` opened, never ``*``. Construction refuses a ``.git`` that
is a file or a link, a ``commondir`` file, ``objects/info/alternates``, and a link anywhere under
``objects``: each lets a git directory read another repository's objects.

Paths are bytes throughout, taken from the trees and never decoded (L8): git prints
``a/\\303\\251`` for a path it quotes, and a quoted path would not match an input glob. A
commit's message is decoded as UTF-8 with the replacement character for a byte that is not, so a
commit whose ``encoding`` header names another encoding never blocks a computation; for an
encoding that agrees with ASCII, as Latin-1 does, the grammar of a Conventional Commit, which is
ASCII, reads the same.

A call raises only ``GitError``, with git's ``error:`` and ``fatal:`` lines when git failed; a
caller's error, an object name out of its grammar or a path no tree can hold, is a
``ValueError``. git runs one process at a time, each bounded by a timeout.
"""

import hashlib
import itertools
import os
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from gora.domain.release.candidates import Commit
from gora.domain.release.identifiers import INPUT_GLOB, SOURCE_COMMIT
from gora.domain.release_repository.inputs import WILDCARDS
from gora.domain.release_repository.tree import TreeEntry

TIMEOUT = 120.0
MESSAGE_LINES = 5
MESSAGE_LINE = 300

NO_GIT = "git is not installed: GORA reads a repository with the git program"
NOT_A_REPOSITORY = (
    "is not a git repository: no .git directory at its root, or a .git file or link that names "
    "another"
)
BORROWED = (
    "reads objects from another repository (commondir, alternates or a link under objects): "
    "GORA reads a clone of its own"
)
SHALLOW = "is a shallow clone: GORA needs the full history of main; fetch it with fetch-depth: 0"
PARTIAL = "is a partial clone: GORA needs every object of the history; clone it without --filter"
UNLISTABLE = "holds a directory GORA cannot list"
MISSING = "is not in the clone: fetch it before GORA reads it"
CORRUPT = "does not hash to its name: the clone's copy is not the object"
NOT_A_COMMIT = "is not a commit"
NOT_FIRST_PARENT = "is not a first-parent commit of the head"
FAILED = "git failed"
TIMED_OUT = "git did not finish in time"
UNREADABLE = "git gave output GORA cannot read"
MALFORMED = "is not an object GORA can read"

TREE = "40000"
GITLINK = "160000"
# A mode is read as git reads it (canon_mode): by its type, a file's by its owner's execute bit,
# and any type that is not a file, a link or a tree as a gitlink. So the old 100664 is 100644, a
# padded 040000 a tree, and 644 a gitlink, as `git ls-tree` shows them.
_TYPES = {0o040000: TREE, 0o120000: "120000"}


class GitError(RuntimeError):
    """A read of a local repository failed or was refused, with the reason."""


class Command(StrEnum):
    """The only git commands the adapter runs."""

    REV_PARSE = "rev-parse"
    REV_LIST = "rev-list"
    CAT_FILE = "cat-file"


@dataclass(frozen=True)
class _Commit:
    tree: str
    parents: tuple[str, ...]
    message: str


Tree = dict[bytes, tuple[str, str]]  # name: (mode, object name)


def environment() -> dict[str, str]:
    """The environment of every git call, built from ``PATH`` alone."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_GRAFT_FILE": os.path.join(os.devnull, "none"),
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_ALLOW_PROTOCOL": "",
    }


class GitRepository:
    """A local clone, read through the three commands of ``Command``."""

    def __init__(self, root: str | os.PathLike[str], *, git: str | None = None) -> None:
        # An absolute git is run as it is named; any other name is looked up on PATH.
        program = git if git and os.path.isabs(git) else shutil.which(git or "git")
        if program is None:
            raise GitError(NO_GIT)
        self._git = os.path.abspath(program)
        self._root = os.path.realpath(root)
        self._git_dir = os.path.join(self._root, ".git")
        self._commits: dict[str, _Commit] = {}
        self._trees: dict[str, Tree] = {}
        if os.path.islink(self._git_dir) or not os.path.isdir(self._git_dir):
            raise GitError(f"{self._root} {NOT_A_REPOSITORY}")
        objects = os.path.join(self._git_dir, "objects")
        if not os.path.lexists(objects):
            raise GitError(f"{self._root} {NOT_A_REPOSITORY}")
        try:
            borrowed = (
                os.path.lexists(os.path.join(self._git_dir, "commondir"))
                or os.path.lexists(os.path.join(objects, "info", "alternates"))
                or _holds_a_link(objects)
            )
            packs = os.path.join(objects, "pack")
            partial = os.path.isdir(packs) and any(
                name.endswith(".promisor") for name in os.listdir(packs)
            )
        except OSError as error:
            raise GitError(f"{self._root} {UNLISTABLE}: {error.strerror}") from None
        if borrowed:
            raise GitError(f"{self._root} {BORROWED}")
        shallow = self._run(Command.REV_PARSE, "--is-shallow-repository").rstrip(b"\n")
        if shallow != b"false":
            raise GitError(f"{self._root} {SHALLOW}")
        if partial:
            raise GitError(f"{self._root} {PARTIAL}")

    def argv(self, command: Command, *arguments: str) -> list[str]:
        """The full command line of one call: the whitelist is checked here."""
        if not isinstance(command, Command):
            raise ValueError("a git command of the adapter's whitelist")
        return [
            self._git,
            f"--git-dir={self._git_dir}",
            "-c",
            "core.commitGraph=false",
            command.value,
            *arguments,
        ]

    def require(self, *commits: str) -> None:
        """Each of ``commits`` is a commit the clone holds, by its full name, whose content
        hashes to it; ``GitError`` names the first that is not."""
        _check(commits)
        self._load_commits(commits)

    def commit(self, sha: str) -> Commit:
        """The commit ``sha`` with its message and the paths it changed against its first
        parent, or against nothing for a root commit."""
        _check((sha,))
        (commit,) = self._histories([sha])
        return commit

    def first_parent(self, head: str, *, after: str) -> tuple[Commit, ...]:
        """The first-parent commits of ``head`` after ``after``, oldest first, each with its
        message and the paths it changed against its first parent, both sides of a rename.
        ``after`` must be a first-parent commit of ``head``; ``head`` itself gives nothing. An
        ``after`` off the chain is refused once the oldest commit git proposes is read."""
        _check((head, after))
        self._load_commits((head, after))
        if head == after:
            return ()
        names = self._run(Command.REV_LIST, "--first-parent", head, f"^{after}").split()
        chain = [name.decode("ascii", "replace") for name in names]
        if not chain:
            raise GitError(f"{after} {NOT_FIRST_PARENT}")
        if chain[0] != head or not all(
            len(sha) == len(head) and SOURCE_COMMIT.is_valid(sha) for sha in chain
        ):
            raise GitError(f"rev-list: {UNREADABLE}")
        # The chain git proposed is verified from the commit objects: the oldest one's first
        # parent is ``after``, read before the rest so an ``after`` off the chain costs one
        # commit more, and each other one's first parent is the next.
        self._load_commits(chain[-1:])
        if self._commits[chain[-1]].parents[:1] != (after,):
            raise GitError(f"{after} {NOT_FIRST_PARENT}")
        self._load_commits(chain)
        for child, parent in itertools.pairwise(chain):
            if self._commits[child].parents[:1] != (parent,):
                raise GitError(f"rev-list: {UNREADABLE}")
        chain.reverse()
        return self._histories(chain)

    def differ(self, a: str, b: str) -> frozenset[bytes]:
        """The paths in which the trees of the commits ``a`` and ``b`` differ."""
        _check((a, b))
        self._load_commits((a, b))
        (changed,) = self._diffs([(self._commits[a].tree, self._commits[b].tree)])
        return changed

    def entries(self, commit: str, paths: Sequence[str]) -> tuple[TreeEntry, ...]:
        """The blobs and gitlinks of ``commit`` at or under ``paths``, in path order, each with
        its mode and bytes, empty for a gitlink. A path is what an input glob's fixed
        components spell (ADR-026 section 9), a release path among them: no empty, ``.`` or
        ``..`` component, so no path names the whole tree."""
        _check((commit,))
        if isinstance(paths, str):
            raise ValueError("paths: a sequence of paths, not one path")
        parts = [_components(path) for path in paths]
        self._load_commits((commit,))
        root = self._commits[commit].tree
        found: dict[bytes, tuple[str, str]] = {}
        # Walk down to each path, a level of every path at a time.
        walking = [(b"", components, root) for components in parts]
        under: list[tuple[bytes, str]] = []  # trees whose every leaf is wanted
        while walking:
            self._load_trees(tree for _, _, tree in walking)
            walking_next = []
            for prefix, components, tree in walking:
                entry = self._trees[tree].get(components[0])
                if entry is None:
                    continue
                mode, name = entry
                path = prefix + components[0]
                if len(components) > 1:
                    if mode == TREE:
                        walking_next.append((path + b"/", components[1:], name))
                elif mode == TREE:
                    under.append((path + b"/", name))
                else:
                    found[path] = entry
            walking = walking_next
        while under:
            self._load_trees(tree for _, tree in under)
            under_next = []
            for prefix, tree in under:
                for child, (mode, name) in self._trees[tree].items():
                    if mode == TREE:
                        under_next.append((prefix + child + b"/", name))
                    else:
                        found[prefix + child] = (mode, name)
            under = under_next
        blobs = sorted({name for mode, name in found.values() if mode != GITLINK})
        data = dict(self._read(blobs, "blob")) if blobs else {}
        return tuple(
            TreeEntry(path, mode, b"" if mode == GITLINK else data[name])
            for path, (mode, name) in sorted(found.items())
        )

    # --- objects, verified --------------------------------------------------------------------

    def _histories(self, chain: Sequence[str]) -> tuple[Commit, ...]:
        """Each commit of ``chain`` with its message and its change against its first parent."""
        self._load_commits(chain)
        parents = [self._commits[sha].parents[:1] for sha in chain]
        self._load_commits([parent for pair in parents for parent in pair])
        pairs = [
            (self._commits[first[0]].tree if first else None, self._commits[sha].tree)
            for sha, first in zip(chain, parents, strict=True)
        ]
        changes = self._diffs(pairs)
        return tuple(
            Commit(sha, self._commits[sha].message, changed)
            for sha, changed in zip(chain, changes, strict=True)
        )

    def _diffs(self, pairs: Sequence[tuple[str | None, str]]) -> list[frozenset[bytes]]:
        """The paths in which each pair of trees differs, every pair a level at a time; a
        missing old tree is the empty tree."""
        changed: list[set[bytes]] = [set() for _ in pairs]
        work: list[tuple[int, bytes, str | None, str | None]] = [
            (index, b"", old, new) for index, (old, new) in enumerate(pairs) if old != new
        ]
        while work:
            self._load_trees(tree for _, _, old, new in work for tree in (old, new) if tree)
            work_next: list[tuple[int, bytes, str | None, str | None]] = []
            for index, prefix, old, new in work:
                before = self._trees[old] if old else {}
                after = self._trees[new] if new else {}
                for name in sorted(before.keys() | after.keys()):
                    was, now = before.get(name), after.get(name)
                    if was == now:
                        continue
                    path = prefix + name
                    was_tree = was is not None and was[0] == TREE
                    now_tree = now is not None and now[0] == TREE
                    if (was is not None and not was_tree) or (now is not None and not now_tree):
                        changed[index].add(path)
                    if was_tree or now_tree:
                        work_next.append(
                            (
                                index,
                                path + b"/",
                                was[1] if was is not None and was_tree else None,
                                now[1] if now is not None and now_tree else None,
                            )
                        )
            work = work_next
        return [frozenset(paths) for paths in changed]

    def _load_commits(self, names: Iterable[str]) -> None:
        wanted = sorted({name for name in names if name not in self._commits})
        for name, body in self._read(wanted, "commit"):
            self._commits[name] = parse_commit(name, body)

    def _load_trees(self, names: Iterable[str | None]) -> None:
        wanted = sorted({name for name in names if name and name not in self._trees})
        for name, body in self._read(wanted, "tree"):
            self._trees[name] = parse_tree(name, body)

    def _read(self, names: Sequence[str], kind: str) -> list[tuple[str, bytes]]:
        """The bodies of the objects ``names``, each of type ``kind`` and hashing to its name."""
        if not names:
            return []
        lines = b"".join(f"{name}\n".encode("ascii") for name in names)
        found = parse_batch(self._run(Command.CAT_FILE, "--batch", stdin=lines), names)
        for name, (actual, body) in zip(names, found, strict=True):
            if actual != kind:
                raise GitError(f"{name} {NOT_A_COMMIT if kind == 'commit' else MALFORMED}")
            if object_name(actual, body, width=len(name)) != name:
                raise GitError(f"{name} {CORRUPT}")
        return [(name, body) for name, (_, body) in zip(names, found, strict=True)]

    def _run(self, command: Command, *arguments: str, stdin: bytes = b"") -> bytes:
        try:
            result = subprocess.run(
                self.argv(command, *arguments),
                input=stdin,
                capture_output=True,
                env=environment(),
                cwd=os.sep,
                timeout=TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise GitError(f"{command.value}: {TIMED_OUT}") from None
        except OSError as error:
            raise GitError(f"{command.value}: {FAILED}: {error.strerror}") from None
        if result.returncode != 0:
            lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
            reasons = [line for line in lines if line.startswith(("fatal:", "error:"))]
            # At most a few lines, each cut short: the text is git's, and the repository's paths
            # and names can be in it.
            shown = [line[:MESSAGE_LINE] for line in (reasons or lines[-1:])[:MESSAGE_LINES]]
            detail = f": {'; '.join(shown)}" if shown else ""
            raise GitError(f"{command.value}: {FAILED} ({result.returncode}){detail}")
        return result.stdout


def object_name(kind: str, body: bytes, *, width: int) -> str:
    """The name git gives an object of type ``kind`` holding ``body``: SHA-1 for a name
    ``width`` 40 hex digits long, SHA-256 for 64."""
    header = f"{kind} {len(body)}\0".encode("ascii")
    algorithm = hashlib.sha1 if width == 40 else hashlib.sha256
    return algorithm(header + body).hexdigest()


def parse_batch(output: bytes, names: Sequence[str]) -> list[tuple[str, bytes]]:
    """The type and body ``cat-file --batch`` gave for each of ``names``, in order."""
    found: list[tuple[str, bytes]] = []
    at = 0
    for name in names:
        end = output.find(b"\n", at)
        fields = output[at:end].split(b" ") if end >= 0 else []
        if fields[-1:] == [b"missing"]:
            raise GitError(f"{name} {MISSING}")
        if len(fields) != 3 or not fields[2].isdigit():
            raise GitError(f"cat-file: {UNREADABLE}")
        # git takes a name it lacks as an abbreviation of one it holds, and echoes that one.
        if fields[0] != name.encode("ascii"):
            raise GitError(f"{name} {MISSING}")
        size = int(fields[2])
        body = output[end + 1 : end + 1 + size]
        if output[end + 1 + size : end + 2 + size] != b"\n":
            raise GitError(f"cat-file: {UNREADABLE}")
        found.append((fields[1].decode("ascii", "replace"), body))
        at = end + 2 + size
    if at != len(output):
        raise GitError(f"cat-file: {UNREADABLE}")
    return found


def parse_commit(name: str, body: bytes) -> _Commit:
    """A commit object's tree, parents and message: its headers end at the first empty line,
    ``tree`` first, then each ``parent``; a header that goes on over lines does so with lines
    that begin with a space."""
    headers, separator, message = body.partition(b"\n\n")
    lines = (headers if separator else body.removesuffix(b"\n")).split(b"\n")
    tree = lines[0].removeprefix(b"tree ").decode("ascii", "replace")
    parents = []
    for line in lines[1:]:
        if not line.startswith(b"parent "):
            break
        parents.append(line.removeprefix(b"parent ").decode("ascii", "replace"))
    if not lines[0].startswith(b"tree ") or not all(
        len(sha) == len(name) and SOURCE_COMMIT.is_valid(sha) for sha in (tree, *parents)
    ):
        raise GitError(f"{name} {MALFORMED}")
    return _Commit(tree, tuple(parents), message.decode("utf-8", "replace"))


def parse_tree(name: str, body: bytes) -> Tree:
    """A tree object's entries: ``<mode> <name>\\0<raw object name>``, each mode as git reads it.

    Refused: what git refuses to read (a malformed mode, an empty name, an entry cut short),
    what git's fsck calls an error (entries out of git's order, by name with a tree's as if it
    ended in ``/``, and a name twice), and a name holding ``/``, which would make two entries of
    two trees one path. A name git's fsck only warns of, ``.``, ``..`` or ``.git``, is read as
    git reads it: a host keeps such histories, and a commit on ``main`` is never rewritten."""
    width = len(name) // 2
    entries: Tree = {}
    previous = b""
    at = 0
    while at < len(body):
        space = body.find(b" ", at)
        nul = body.find(b"\0", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 1 + width > len(body):
            raise GitError(f"{name} {MALFORMED}")
        mode = _mode(body[at:space])
        entry = body[space + 1 : nul]
        target = body[nul + 1 : nul + 1 + width].hex()
        key = entry + b"/" if mode == TREE else entry
        # The order refuses an empty name too, which sorts before any name: git does not read one.
        if mode is None or b"/" in entry or entry in entries or key <= previous:
            raise GitError(f"{name} {MALFORMED}")
        entries[entry] = (mode, target)
        previous = key
        at = nul + 1 + width
    return entries


def _mode(field: bytes) -> str | None:
    """The mode git reads from a tree entry's field of octal digits, or None for a field git
    refuses: an empty one, or one with a byte that is not an octal digit."""
    if not field or not all(0x30 <= byte <= 0x37 for byte in field):
        return None
    value = int(field, 8)
    if value & 0o170000 == 0o100000:
        return "100755" if value & 0o100 else "100644"
    return _TYPES.get(value & 0o170000, GITLINK)


def _components(path: str) -> list[bytes]:
    """The names of ``path``, which the input glob grammar accepts and holds no wildcard."""
    if INPUT_GLOB.problem(path) is not None or WILDCARDS & set(path):
        raise ValueError(f"a path with no *, ? or [, which otherwise {INPUT_GLOB.rule}")
    return [part.encode("ascii") for part in path.split("/")]


def _holds_a_link(directory: str) -> bool:
    """Whether ``directory`` is a link, or holds one at any depth: a linked ``objects``,
    ``pack`` or loose-object directory reads another repository's objects."""
    if os.path.islink(directory):
        return True

    def refuse(error: OSError) -> None:
        raise error

    for parent, directories, files in os.walk(directory, onerror=refuse, followlinks=False):
        if any(os.path.islink(os.path.join(parent, name)) for name in (*directories, *files)):
            return True
    return False


def _check(commits: Iterable[str]) -> None:
    for sha in commits:
        if not SOURCE_COMMIT.is_valid(sha):
            raise ValueError(f"commit {SOURCE_COMMIT.rule}")

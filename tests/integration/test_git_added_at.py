"""``GitRepository.added_at`` and ``NotFirstParentError`` on real repositories (ADR-006, ADR-026
section 9).

``added_at`` names the commit that added a file: the newest first-parent commit at which the path
names an entry that is not a tree while its first parent's tree has none, the root when the path
is in it. It is read from the trees, so each answer is compared with git's own: ``git log
--diff-filter=A``. The histories are built with ``git fast-import``, one process for hundreds of
commits, and the walk is held to batches of reads: the processes git runs are counted.
"""

import re
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from gora.adapters.git import adapter
from gora.adapters.git.adapter import Command, GitError, GitRepository, NotFirstParentError
from tests.integration.test_git_adapter import GIT, REAL_HEAD, REAL_HISTORY, Repo, text
from tests.integration.test_identifiers_against_git import hermetic_git_env

DECLARATION = "e49348a290e99c935d2762229e305e518c4f059f"  # GORA's #86, in the pinned bundle
ARTIFACT = "artifacts/gora/artifact.yaml"


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict[str, str]]:
    yield hermetic_git_env(tmp_path / "home")


def fast_import(repo: Repo, steps: Sequence[tuple[str, Mapping[str, bytes | None]]]) -> list[str]:
    """Each step a commit on main, the files it sets or, with None, deletes; the first-parent
    chain, oldest first. One git process, whatever the number of commits."""
    stream = bytearray()
    for index, (message, changes) in enumerate(steps):
        stream += b"commit refs/heads/main\n"
        stream += b"committer a <a@example.invalid> %d +0000\n" % (1760875200 + index)
        stream += b"data %d\n%s\n" % (len(message.encode()), message.encode())
        for path, content in changes.items():
            if content is None:
                stream += b"D %s\n" % path.encode()
            else:
                stream += b"M 100644 inline %s\ndata %d\n%s\n" % (
                    path.encode(),
                    len(content),
                    content,
                )
        stream += b"\n"
    repo.git("fast-import", "--quiet", stdin=bytes(stream))
    return repo.git("rev-list", "--first-parent", "--reverse", "main").decode().split()


class Counted:
    """The git processes the adapter starts from now on, and the objects its cat-file reads."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.processes = 0
        self.objects = 0
        run = subprocess.run

        def counting(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            self.processes += 1
            if Command.CAT_FILE in argv:
                self.objects += len(kwargs["input"].splitlines())
            return run(argv, **kwargs)

        monkeypatch.setattr(subprocess, "run", counting)


def long_history(repo: Repo, commits: int, added: int) -> list[str]:
    """``commits`` commits, each changing README.md; ``a/f.txt`` is added by the commit numbered
    ``added`` from the root, and changed by every tenth one after it."""
    steps: list[tuple[str, Mapping[str, bytes | None]]] = []
    for number in range(commits):
        changes: dict[str, bytes | None] = {"README.md": b"r%d" % number}
        if number == added or (number > added and number % 10 == 0):
            changes["a/f.txt"] = b"f%d" % number
        steps.append((f"chore: commit {number}", changes))
    return fast_import(repo, steps)


# --- what it names ---------------------------------------------------------------------------


def test_the_newest_commit_that_adds_the_file_is_the_boundary(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    chain = fast_import(
        repo,
        [
            ("chore: root", {"README.md": b"r"}),
            ("feat: add", {"a/f.txt": b"1"}),
            ("fix: change", {"a/f.txt": b"2"}),
            ("chore: other", {"README.md": b"r2"}),
            ("chore: delete", {"a/f.txt": None}),
            ("chore: other again", {"README.md": b"r3"}),
            ("feat: add it again", {"a/f.txt": b"3"}),
            ("chore: and another", {"README.md": b"r4"}),
        ],
    )
    clone = GitRepository(repo.root, git=GIT)
    # The declaration deleted and declared again: the newest declaration is the boundary.
    assert clone.added_at(chain[7], "a/f.txt") == chain[6]
    assert clone.added_at(chain[6], "a/f.txt") == chain[6]
    # Read at an older head, it is that head's own: the walk starts where it is told.
    assert clone.added_at(chain[3], "a/f.txt") == chain[1]
    assert clone.added_at(chain[2], "a/f.txt") == chain[1]


def test_a_path_in_the_root_commit_gives_the_root(tmp_path: Path, env: dict[str, str]) -> None:
    repo = Repo(tmp_path / "r", env)
    chain = fast_import(
        repo,
        [
            ("chore: root", {"a/f.txt": b"1", "README.md": b"r"}),
            ("fix: change", {"a/f.txt": b"2"}),
            ("fix: change again", {"a/f.txt": b"3"}),
        ],
    )
    clone = GitRepository(repo.root, git=GIT)
    assert clone.added_at(chain[2], "a/f.txt") == chain[0]
    assert clone.added_at(chain[0], "a/f.txt") == chain[0]
    assert clone.added_at(chain[2], "README.md") == chain[0]


def test_a_link_or_a_gitlink_is_an_entry_that_is_not_a_tree(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    c1 = repo.commit({b"README.md": text("r")}, b"chore: root\n")
    c2 = repo.commit({b"README.md": text("r"), b"l": ("120000", b"README.md")}, b"feat: link\n", c1)
    gitlink = ("160000", b"a" * 40)
    both = {b"README.md": text("r"), b"l": ("120000", b"README.md"), b"m": gitlink}
    c3 = repo.commit(both, b"feat: submodule\n", c2)
    c4 = repo.commit({**both, b"README.md": text("r2")}, b"chore: other\n", c3)
    clone = GitRepository(repo.root, git=GIT)
    assert clone.added_at(c4, "l") == c2
    assert clone.added_at(c4, "m") == c3


def test_a_file_that_became_a_directory_and_back_is_added_when_it_is_a_file_again(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    c1 = repo.commit({b"a": text("file")}, b"chore: root\n")
    c2 = repo.commit({b"a/b": text("inside")}, b"refactor: a is a directory\n", c1)
    c3 = repo.commit({b"a": text("file again")}, b"refactor: a is a file\n", c2)
    clone = GitRepository(repo.root, git=GIT)
    assert clone.added_at(c3, "a") == c3
    assert clone.added_at(c1, "a") == c1
    with pytest.raises(ValueError, match="a file, a link or a gitlink"):
        clone.added_at(c2, "a")  # at c2, a is a tree


def test_a_path_with_no_file_at_the_head_is_the_caller_s_error(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    chain = fast_import(
        repo,
        [
            ("chore: root", {"README.md": b"r", "a/f.txt": b"1"}),
            ("chore: delete", {"a/f.txt": None}),
        ],
    )
    clone = GitRepository(repo.root, git=GIT)
    for path in ("a/f.txt", "never.txt", "README.md/x", "a"):
        with pytest.raises(ValueError, match="a file, a link or a gitlink of the head"):
            clone.added_at(chain[1], path)
    assert clone.added_at(chain[0], "a/f.txt") == chain[0]  # calibration: it was there


@pytest.mark.parametrize("path", ["", "/a", "a/", "a//b", "a/./b", "../a", "a/*", "a/[b]", "a?"])
def test_a_path_that_entries_refuses_is_the_caller_s_error(
    tmp_path: Path, env: dict[str, str], path: str
) -> None:
    repo = Repo(tmp_path / "r", env)
    head = repo.commit({b"a": text("a")}, b"chore: root\n")
    clone = GitRepository(repo.root, git=GIT)
    with pytest.raises(ValueError, match="no \\*, \\? or \\["):
        clone.added_at(head, path)
    with pytest.raises(ValueError, match="no \\*, \\? or \\["):
        clone.entries(head, [path])  # the same refusal as entries makes, a path by its rules


@pytest.mark.parametrize("head", ["abc", "A" * 40, "a" * 41, ""])
def test_a_head_that_is_not_an_object_name_is_the_caller_s_error(
    tmp_path: Path, env: dict[str, str], head: str
) -> None:
    repo = Repo(tmp_path / "r", env)
    repo.commit({b"a": text("a")}, b"chore: root\n")
    with pytest.raises(ValueError, match=r"^commit must be"):
        GitRepository(repo.root, git=GIT).added_at(head, "a")


def test_the_answer_is_the_one_git_log_gives_on_a_sha256_repository(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r256", env, object_format="sha256")
    c1 = repo.commit({b"README.md": text("r")}, b"chore: root\n")
    c2 = repo.commit({b"README.md": text("r"), b"a/f": text("1")}, b"feat: add\n", c1)
    c3 = repo.commit({b"README.md": text("r2"), b"a/f": text("1")}, b"chore: other\n", c2)
    assert GitRepository(repo.root, git=GIT).added_at(c3, "a/f") == c2


# --- the walk is in batches, and stops at the boundary ------------------------------------------


def test_the_walk_reads_in_batches_not_one_process_to_a_commit(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(tmp_path / "r", env)
    chain = long_history(repo, commits=200, added=10)
    assert len(chain) == 200  # calibration: a history of 200 commits, the boundary 190 below
    clone = GitRepository(repo.root, git=GIT)
    counted = Counted(monkeypatch)
    assert clone.added_at(chain[-1], "a/f.txt") == chain[10]
    # The head, rev-list, and two batches (64, then 128) of a read of commits and of two levels
    # of trees: far below the commits it walked, and the same for twice the history.
    assert counted.processes == 2 + 2 * 3
    assert counted.processes < len(chain)


def test_the_processes_grow_with_the_batches_and_not_with_the_commits(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(tmp_path / "r", env)
    chain = long_history(repo, commits=1000, added=0)
    clone = GitRepository(repo.root, git=GIT)
    counted = Counted(monkeypatch)
    assert clone.added_at(chain[-1], "a/f.txt") == chain[0]  # the root, 999 commits below
    # 64, 128, 256, 512 and the 40 left: five batches, 2 + 5 * 3 processes, for 1000 commits.
    assert counted.processes == 2 + 5 * 3
    assert counted.processes < len(chain) / 50


def test_the_walk_stops_at_the_boundary(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(tmp_path / "r", env)
    chain = long_history(repo, commits=400, added=395)
    clone = GitRepository(repo.root, git=GIT)
    counted = Counted(monkeypatch)
    assert clone.added_at(chain[-1], "a/f.txt") == chain[395]
    # One batch: the head, the 65 commits of the first batch and their trees; not the 400.
    assert counted.processes == 2 + 3
    assert counted.objects < 1 + 65 + 2 * 65 + 1
    assert counted.objects < len(chain)


def test_the_walk_keeps_no_objects_it_walked_past(tmp_path: Path, env: dict[str, str]) -> None:
    repo = Repo(tmp_path / "r", env)
    chain = long_history(repo, commits=300, added=0)
    clone = GitRepository(repo.root, git=GIT)
    assert clone.added_at(chain[-1], "a/f.txt") == chain[0]
    # Only the head was loaded for itself; the rest of the walk is held in no cache.
    assert len(clone._commits) <= 1
    assert clone._trees == {}


# --- the chain git proposes is verified -------------------------------------------------------


def _proposing(clone: GitRepository, monkeypatch: pytest.MonkeyPatch, names: list[str]) -> None:
    run = clone._run

    def proposing(command: Command, *arguments: str, stdin: bytes = b"") -> bytes:
        if command is Command.REV_LIST:
            return "".join(f"{name}\n" for name in names).encode()
        return run(command, *arguments, stdin=stdin)

    monkeypatch.setattr(clone, "_run", proposing)


@pytest.mark.parametrize(
    "proposal",
    [
        [],  # nothing
        [3, 2],  # not from the head
        [3, 1],  # a commit skipped: c3's first parent is c2
        [3, 2, 0],  # a name that is no name
        [3, 2, -1],  # an abbreviated name
        [3, 3, 2, 1],  # a commit twice
        [3, 2, 1, 1],  # the root twice: its first parent is not itself
    ],
)
def test_a_chain_the_commits_do_not_link_is_refused(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch, proposal: list[int]
) -> None:
    """Commit n is cn, -n its name abbreviated, and 0 no name. The file is in the root only, so
    a walk that believed the proposal would answer: the refusal is the links' check."""
    repo = Repo(tmp_path / "r", env)
    chain = fast_import(
        repo,
        [("chore: one", {"a": b"1"}), ("chore: two", {"b": b"1"}), ("chore: three", {"c": b"1"})],
    )
    names = [chain[n - 1] if n > 0 else chain[-n - 1][:7] if n else "z" * 40 for n in proposal]
    clone = GitRepository(repo.root, git=GIT)
    _proposing(clone, monkeypatch, names)
    with pytest.raises(GitError, match=f"^rev-list: {adapter.UNREADABLE}$") as raised:
        clone.added_at(chain[2], "a")
    assert type(raised.value) is GitError


def test_a_proposal_that_stops_before_the_root_is_refused(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last commit of the proposal has a first parent: the chain is not the whole one."""
    repo = Repo(tmp_path / "r", env)
    chain = fast_import(
        repo, [("chore: one", {"a": b"1"}), ("chore: two", {"b": b"1"}), ("chore: 3", {"c": b"1"})]
    )
    clone = GitRepository(repo.root, git=GIT)
    _proposing(clone, monkeypatch, [chain[2], chain[1]])
    with pytest.raises(GitError, match=f"^rev-list: {adapter.UNREADABLE}$"):
        clone.added_at(chain[2], "a")


@pytest.mark.parametrize("kind", ["tree", "commit"])
def test_an_object_of_the_walk_rewritten_under_its_own_name_is_refused(
    tmp_path: Path, env: dict[str, str], kind: str
) -> None:
    repo = Repo(tmp_path / "r", env)
    c1 = repo.commit({b"a/f": text("1")}, b"chore: one\n")
    c2 = repo.commit({b"a/f": text("1"), b"b": text("1")}, b"chore: two\n", c1)
    c3 = repo.commit({b"a/f": text("1"), b"b": text("2")}, b"chore: three\n", c2)
    evil = repo.commit({b"a/f": text("evil")}, b"chore: evil\n", c1)
    name = {"tree": repo.tree(c2), "commit": c2}[kind]
    body = repo.git("cat-file", kind, repo.tree(evil) if kind == "tree" else evil)
    repo.substitute(name, kind, body)
    assert repo.git("cat-file", kind, name) == body  # calibration: git reads it as it is
    clone = GitRepository(repo.root, git=GIT)
    with pytest.raises(GitError, match=f"^{name} {re.escape(adapter.CORRUPT)}$"):
        clone.added_at(c3, "a/f")


def test_an_object_the_clone_lacks_is_refused(tmp_path: Path, env: dict[str, str]) -> None:
    repo = Repo(tmp_path / "r", env)
    repo.commit({b"a": text("1")}, b"chore: one\n")
    clone = GitRepository(repo.root, git=GIT)
    with pytest.raises(GitError, match=f"^{'f' * 40} {re.escape(adapter.MISSING)}$") as raised:
        clone.added_at("f" * 40, "a")
    assert type(raised.value) is GitError


# --- NotFirstParentError ----------------------------------------------------------------------


@pytest.fixture
def merged(tmp_path: Path, env: dict[str, str]) -> tuple[Repo, list[str]]:
    """c1, c2, a side branch from c2, c3, and a merge of the side branch into c3."""
    repo = Repo(tmp_path / "m", env)
    c1 = repo.commit({b"a": text("1")}, b"chore: one\n")
    c2 = repo.commit({b"a": text("2")}, b"fix: two\n", c1)
    side = repo.commit({b"a": text("2"), b"s": text("s")}, b"feat: side\n", c2)
    c3 = repo.commit({b"a": text("3")}, b"fix: three\n", c2)
    merge = repo.commit({b"a": text("3"), b"s": text("s")}, b"Merge side\n", c3, side)
    repo.branch(merge)
    return repo, [c1, c2, side, c3, merge]


def test_an_after_off_the_chain_is_a_not_first_parent_error_at_both_places(
    merged: tuple[Repo, list[str]],
) -> None:
    repo, (_c1, c2, side, c3, merge) = merged
    unrelated = repo.commit({b"u": text("u")}, b"chore: another root\n")
    clone = GitRepository(repo.root, git=GIT)
    # No commit between: after is a descendant of head. The oldest commit proposed does not have
    # after as its first parent: a side branch's commit, merged by the second parent, and
    # another root's.
    for head, after in ((c3, merge), (merge, side), (merge, unrelated), (c2, c3)):
        with pytest.raises(NotFirstParentError, match=f"^{after} {adapter.NOT_FIRST_PARENT}$"):
            clone.first_parent(head, after=after)
    # It is a GitError, and its message is the GitError's it replaces.
    assert issubclass(NotFirstParentError, GitError)
    with pytest.raises(GitError, match=f"^{side} {adapter.NOT_FIRST_PARENT}$"):
        clone.first_parent(merge, after=side)
    assert clone.first_parent(merge, after=c2)  # calibration: a commit on the chain is none


def test_every_other_git_error_stays_a_plain_git_error(
    merged: tuple[Repo, list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, (c1, _c2, _side, c3, merge) = merged
    clone = GitRepository(repo.root, git=GIT)
    failures: list[GitError] = []

    def plain(call: Any) -> None:
        with pytest.raises(GitError) as raised:
            call()
        failures.append(raised.value)

    plain(lambda: clone.first_parent(merge, after="f" * 40))  # an object the clone lacks
    plain(lambda: clone.first_parent("f" * 40, after=c1))
    plain(lambda: clone.require("f" * 40))
    plain(lambda: GitRepository(tmp_path / "no-repository", git=GIT))  # not a repository
    plain(lambda: GitRepository(_shallow(repo, tmp_path), git=GIT))  # a shallow clone
    _proposing(clone, monkeypatch, [c3])  # a proposal that does not start at the head
    plain(lambda: clone.first_parent(merge, after=c1))
    assert len(failures) == 6
    assert [type(failure) for failure in failures] == [GitError] * 6


def _shallow(repo: Repo, tmp_path: Path) -> Path:
    target = tmp_path / "shallow"
    repo.git("clone", "-q", "--depth", "1", f"file://{repo.root}", str(target))
    return target


# --- GORA's own history, as git gives it ------------------------------------------------------


@pytest.fixture(scope="module")
def real(tmp_path_factory: pytest.TempPathFactory) -> Repo:
    """The bundle of GORA's own ``main``, cloned whole."""
    root = tmp_path_factory.mktemp("real") / "gora"
    env = hermetic_git_env(root.parent / "home")
    subprocess.run(
        [GIT, "clone", "-q", "--no-checkout", str(REAL_HISTORY), str(root)],
        env=env,
        capture_output=True,
        check=True,
    )
    return Repo(root, env, init=False)


def test_gora_s_artifact_was_declared_by_its_pull_request_86(real: Repo) -> None:
    clone = GitRepository(real.root, git=GIT)
    assert clone.added_at(REAL_HEAD, ARTIFACT) == DECLARATION
    # Calibration: that commit is the one that holds the file and whose parent lacks it.
    real.git("cat-file", "-e", f"{DECLARATION}:{ARTIFACT}")  # raises when it is not there
    parent = real.git("rev-parse", f"{DECLARATION}^").decode().strip()
    absent = subprocess.run(
        [GIT, "cat-file", "-e", f"{parent}:{ARTIFACT}"],
        cwd=real.root,
        env=real.env,
        capture_output=True,
        check=False,
    )
    assert absent.returncode != 0


def test_the_answer_is_the_one_git_log_gives_for_every_tenth_file_of_gora_s_history(
    real: Repo,
) -> None:
    clone = GitRepository(real.root, git=GIT)
    listed = real.git("ls-tree", "-r", "--name-only", "-z", REAL_HEAD).split(b"\0")
    paths = sorted(path.decode() for path in listed if path)[::10]
    assert len(paths) > 10  # calibration: a sample, not a name
    for path in paths:
        added = real.git(
            "log", "--first-parent", "--diff-merges=first-parent", "--no-renames",
            "--diff-filter=A", "--format=%H", REAL_HEAD, "--", path,
        ).decode().split()  # fmt: skip
        assert added, path  # calibration: git finds an addition
        assert clone.added_at(REAL_HEAD, path) == added[0], path

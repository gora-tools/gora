"""The git adapter against the real git binary (ADR-006, ADR-026 section 9; L8).

What is held: the adapter reads a local clone through three commands and nothing else; it gives
a commit with its message and the paths it changed against its first parent, as bytes, both
sides of a rename; the first-parent commits of a head after a given one, oldest first, each the
same; the paths in which two trees differ; and the blobs of a tree with their modes. Every
object it reads hashes to its name, or it is refused: git reads a loose object rewritten under
its own name as it is, which each substitution test shows first. It refuses a shallow clone, an
object the clone lacks, a ``.git`` that names another repository, and a commit off the
first-parent chain; on a clone that sets every setting of ``SETTINGS``, with a replace ref, a
graft and a lying commit-graph, its answers are those of a clean copy; and no program a hostile
repository's configuration names runs, while the same configuration does run one for
``git status``.

Repositories are built with plumbing in a temporary directory, their paths written into an index
with ``update-index -z --index-info`` and never onto the disk, so no file system folds them; some
are not ASCII and one is not UTF-8. git runs with no user or system configuration, one process at
a time (TESTING rule 8). Expectations are written out literally, from what each commit was built
to change, and the history is also compared with ``git log``, another code path of git.

git is on every machine that runs this suite and in CI; a missing git fails the module rather
than skipping it, because a test that cannot run proves nothing.
"""

import functools
import os
import re
import shutil
import stat
import subprocess
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gora.adapters.git import adapter
from gora.adapters.git.adapter import Command, GitError, GitRepository
from gora.domain.release.candidates import Commit
from gora.domain.release_repository.tree import TreeEntry
from tests.integration.test_identifiers_against_git import GIT, hermetic_git_env

Files = Mapping[bytes, tuple[str, bytes]]

E_ACUTE = "é".encode()  # two bytes in UTF-8
NOT_UTF8 = b"a\xff"
QUOTED = b"src/" + E_ACUTE + b".py"  # git would print it as "src/\303\251.py" unless -z


class Repo:
    """A repository built with plumbing: every commit is a full snapshot of its files."""

    def __init__(
        self, root: Path, env: dict[str, str], *, object_format: str = "sha1", init: bool = True
    ) -> None:
        self.root = root
        self.env = {
            **env,
            "GIT_AUTHOR_NAME": "a",
            "GIT_AUTHOR_EMAIL": "a@example.invalid",
            "GIT_COMMITTER_NAME": "a",
            "GIT_COMMITTER_EMAIL": "a@example.invalid",
            "GIT_AUTHOR_DATE": "2026-10-19T12:00:00Z",
            "GIT_COMMITTER_DATE": "2026-10-19T12:00:00Z",
        }
        if init:
            root.mkdir(parents=True, exist_ok=True)
            self.git(
                "-c", "init.defaultBranch=main", "init", "-q", f"--object-format={object_format}"
            )

    def git(self, *args: str | bytes, stdin: bytes = b"", check: bool = True, **env: str) -> bytes:
        assert GIT is not None
        result = subprocess.run(
            [GIT, *args],
            cwd=self.root,
            env={**self.env, **env},
            input=stdin,
            capture_output=True,
            check=check,
        )
        return result.stdout

    def blob(self, data: bytes) -> str:
        return self.git("hash-object", "-w", "--stdin", stdin=data).decode().strip()

    def commit(self, files: Files, message: bytes, *parents: str) -> str:
        index = self.root / ".git" / "build-index"
        index.unlink(missing_ok=True)
        records = b""
        for path, (mode, data) in files.items():
            name = data.decode() if mode == "160000" else self.blob(data)
            records += f"{mode} {name}\t".encode() + path + b"\0"
        self.git("update-index", "-z", "--index-info", stdin=records, GIT_INDEX_FILE=str(index))
        tree = self.git("write-tree", GIT_INDEX_FILE=str(index)).decode().strip()
        flags = [arg for parent in parents for arg in ("-p", parent)]
        return self.git("commit-tree", tree, *flags, stdin=message).decode().strip()

    def branch(self, sha: str, name: str = "main") -> None:
        self.git("update-ref", f"refs/heads/{name}", sha)

    def tree(self, commit: str) -> str:
        return self.git("rev-parse", f"{commit}^{{tree}}").decode().strip()

    def substitute(self, name: str, kind: str, body: bytes) -> None:
        """Rewrite the loose object ``name`` to hold ``body``, under the same name."""
        path = self.root / ".git" / "objects" / name[:2] / name[2:]
        assert path.is_file()
        path.chmod(0o644)
        path.write_bytes(zlib.compress(f"{kind} {len(body)}\0".encode() + body))


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict[str, str]]:
    yield hermetic_git_env(tmp_path / "home")


def text(data: str) -> tuple[str, bytes]:
    return ("100644", data.encode())


# --- the history ---------------------------------------------------------------------------------


@pytest.fixture
def history(tmp_path: Path, env: dict[str, str]) -> tuple[Repo, list[str]]:
    """main: root, add, rename, mode, delete, an empty commit, a merge of a side branch."""
    repo = Repo(tmp_path / "r", env)
    first: dict[bytes, tuple[str, bytes]] = {b"README.md": text("r")}
    c1 = repo.commit(first, b"chore: root\n")
    second = {**first, QUOTED: text("e"), NOT_UTF8: text("x"), b"a/b.txt": text("b")}
    c2 = repo.commit(second, "feat(api): add é\n\nbody line\n".encode(), c1)
    third = {k: v for k, v in second.items() if k != b"a/b.txt"} | {b"c/b.txt": text("b")}
    c3 = repo.commit(third, b"refactor: move b\n", c2)
    fourth = {**third, b"README.md": ("100755", b"r")}
    c4 = repo.commit(fourth, b"fix: mode\n", c3)
    fifth = {k: v for k, v in fourth.items() if k != NOT_UTF8}
    c5 = repo.commit(fifth, b"fix!: drop a byte\n", c4)
    c6 = repo.commit(fifth, b"chore: nothing\n", c5)
    side = repo.commit({**fifth, b"side.txt": text("s")}, b"feat: side\n", c6)
    merged = {**fifth, b"side.txt": text("s"), b"m.txt": text("m")}
    c7 = repo.commit(merged, b"Merge side\n", c6, side)
    repo.branch(c7)
    return repo, [c1, c2, c3, c4, c5, c6, side, c7]


def test_the_first_parent_history_is_each_commit_s_message_and_changed_paths(
    history: tuple[Repo, list[str]],
) -> None:
    repo, (c1, c2, c3, c4, c5, c6, _side, c7) = history
    clone = GitRepository(repo.root)
    # A root commit changed every path it holds.
    assert clone.commit(c1) == Commit(c1, "chore: root\n", frozenset({b"README.md"}))
    assert clone.first_parent(c7, after=c1) == (
        Commit(
            c2,
            "feat(api): add é\n\nbody line\n",
            frozenset({QUOTED, NOT_UTF8, b"a/b.txt"}),
        ),
        Commit(c3, "refactor: move b\n", frozenset({b"a/b.txt", b"c/b.txt"})),
        Commit(c4, "fix: mode\n", frozenset({b"README.md"})),
        Commit(c5, "fix!: drop a byte\n", frozenset({NOT_UTF8})),
        Commit(c6, "chore: nothing\n", frozenset()),
        # A merge changed what its first parent lacks: the side's file and its own.
        Commit(c7, "Merge side\n", frozenset({b"side.txt", b"m.txt"})),
    )
    assert clone.commit(c7) == clone.first_parent(c7, after=c6)[0]


def test_the_history_after_a_commit_starts_after_it(history: tuple[Repo, list[str]]) -> None:
    repo, (_c1, _c2, _c3, _c4, c5, c6, _side, c7) = history
    clone = GitRepository(repo.root)
    assert [commit.sha for commit in clone.first_parent(c7, after=c5)] == [c6, c7]
    assert clone.first_parent(c7, after=c7) == ()


def test_the_history_always_starts_after_a_commit(history: tuple[Repo, list[str]]) -> None:
    """No call reads the whole history: a first candidate is judged by ``commit``."""
    repo, commits = history
    with pytest.raises(TypeError):
        GitRepository(repo.root).first_parent(commits[-1])  # type: ignore[call-arg]


def test_a_commit_off_the_first_parent_chain_is_refused(history: tuple[Repo, list[str]]) -> None:
    repo, commits = history
    c5, side, c7 = commits[4], commits[6], commits[7]
    unrelated = repo.commit({b"u": text("u")}, b"chore: another root\n")
    clone = GitRepository(repo.root)
    # A side branch's commit, another root's, and a descendant of the head.
    for head, after in ((c7, side), (c7, unrelated), (c5, c7)):
        with pytest.raises(GitError, match=f"^{after} {adapter.NOT_FIRST_PARENT}$"):
            clone.first_parent(head, after=after)


def test_an_after_off_the_chain_is_refused_after_one_commit_more_is_read(
    history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commits = history
    unrelated = repo.commit({b"u": text("u")}, b"chore: another root\n")
    read: list[int] = []
    run = subprocess.run

    def counting(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if Command.CAT_FILE in argv:
            read.append(len(kwargs["input"].splitlines()))
        return run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    with pytest.raises(GitError, match=adapter.NOT_FIRST_PARENT):
        GitRepository(repo.root).first_parent(commits[-1], after=unrelated)
    # The head and after, then the oldest commit git proposed: not the seven of the chain.
    assert sum(read) == 3


def test_a_proposal_of_names_of_another_length_is_refused(
    tmp_path: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Repo(tmp_path / "r256", env, object_format="sha256")
    c1 = repo.commit({b"x": text("1")}, b"feat: one\n")
    c2 = repo.commit({b"x": text("2")}, b"fix: two\n", c1)
    c3 = repo.commit({b"x": text("3")}, b"fix: three\n", c2)
    clone = GitRepository(repo.root)
    run = clone._run

    def proposing(command: Command, *arguments: str, stdin: bytes = b"") -> bytes:
        if command is Command.REV_LIST:  # a SHA-1-long prefix of c2, a valid name on its own
            return f"{c3}\n{c2[:40]}\n".encode()
        return run(command, *arguments, stdin=stdin)

    monkeypatch.setattr(clone, "_run", proposing)
    with pytest.raises(GitError, match=f"^rev-list: {adapter.UNREADABLE}$"):
        clone.first_parent(c3, after=c1)


def test_the_history_is_the_one_git_log_gives(history: tuple[Repo, list[str]]) -> None:
    """Another code path of git: ``log`` with names, against the adapter's own tree diff."""
    repo, commits = history
    head = commits[-1]
    raw = repo.git(
        "log", "--first-parent", "--reverse", "-z", "--no-renames", "--diff-merges=first-parent",
        "--name-only", "--format=%x01%H", head,
    )  # fmt: skip
    expected: dict[str, frozenset[bytes]] = {}
    for chunk in raw.split(b"\x01")[1:]:  # "<sha>\0", then "\n" and "<path>\0" for each path
        sha, _, names = chunk.partition(b"\0")
        paths = names.removeprefix(b"\n").split(b"\0")
        expected[sha.decode()] = frozenset(path for path in paths if path)
    clone = GitRepository(repo.root)
    read = (clone.commit(commits[0]), *clone.first_parent(head, after=commits[0]))
    assert {commit.sha: commit.changed for commit in read} == expected
    messages = repo.git("log", "--first-parent", "--reverse", "-z", "--format=%B", head)
    want = [m.decode("utf-8", "replace") for m in messages.split(b"\0") if m]
    assert [commit.message for commit in read] == want


def test_two_trees_differ_in_the_paths_either_changed(history: tuple[Repo, list[str]]) -> None:
    repo, (c1, c2, _c3, _c4, c5, c6, _side, c7) = history
    clone = GitRepository(repo.root)
    assert clone.differ(c6, c7) == frozenset({b"side.txt", b"m.txt"})
    assert clone.differ(c2, c5) == frozenset({b"a/b.txt", b"c/b.txt", b"README.md", NOT_UTF8})
    assert clone.differ(c5, c6) == frozenset()
    assert clone.differ(c1, c1) == frozenset()


def test_a_file_that_becomes_a_directory_changed_both(tmp_path: Path, env: dict[str, str]) -> None:
    repo = Repo(tmp_path / "r", env)
    c1 = repo.commit({b"a": text("1"), b"d/x": text("x"), b"d/e/y": text("y")}, b"feat: one\n")
    c2 = repo.commit({b"a/b": text("1"), b"d": text("x")}, b"feat: two\n", c1)
    clone = GitRepository(repo.root)
    changed = frozenset({b"a", b"a/b", b"d", b"d/x", b"d/e/y"})
    assert clone.differ(c1, c2) == changed
    assert clone.differ(c2, c1) == changed
    assert clone.commit(c2).changed == changed


@pytest.mark.parametrize(
    "proposal",
    [
        pytest.param([7, 6, 4, 5, 3, 2], id="out-of-order"),
        pytest.param([7, 6, 4, 3, 2], id="a-commit-skipped"),
        pytest.param([6, 5, 4, 3, 2], id="not-from-the-head"),
        pytest.param([7, -6, 5, 4, 3, 2], id="an-abbreviated-name"),
        pytest.param([7, 0, 5, 4, 3, 2], id="not-a-name"),
    ],
)
def test_a_chain_git_proposes_that_the_commits_do_not_link_is_refused(
    history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch, proposal: list[int]
) -> None:
    """``rev-list`` proposes, the commit objects decide: a proposal they do not link is refused,
    whatever made git give it. Commit n is cn, -n its name abbreviated, and 0 no name."""
    repo, commits = history
    chain = [c for c in commits if c != commits[6]]  # c1..c7, without the side branch
    names = [chain[n - 1] if n > 0 else chain[-n - 1][:7] if n else "z" * 40 for n in proposal]
    clone = GitRepository(repo.root)
    run = clone._run

    def proposing(command: Command, *arguments: str, stdin: bytes = b"") -> bytes:
        if command is Command.REV_LIST:
            return "".join(f"{name}\n" for name in names).encode()
        return run(command, *arguments, stdin=stdin)

    monkeypatch.setattr(clone, "_run", proposing)
    with pytest.raises(GitError, match=f"^rev-list: {adapter.UNREADABLE}$"):
        clone.first_parent(chain[6], after=chain[0])


@pytest.mark.parametrize("where", ["submodule-config", "worktree", "index", "core-worktree"])
def test_a_submodule_s_ignore_setting_hides_no_gitlink_change(
    tmp_path: Path, env: dict[str, str], where: str
) -> None:
    repo = Repo(tmp_path / "r", env)
    modules = b'[submodule "s"]\n\tpath = s\n'
    ignoring = modules + b"\tignore = all\n"
    c1 = repo.commit({b"s": ("160000", b"1" * 40), b".gitmodules": ("100644", modules)}, b"a\n")
    c2 = repo.commit({b"s": ("160000", b"2" * 40), b".gitmodules": ("100644", modules)}, b"b\n", c1)
    repo.branch(c2)
    if where == "submodule-config":
        repo.git("config", "submodule.s.ignore", "all")
    elif where == "worktree":
        (repo.root / ".gitmodules").write_bytes(ignoring)
    elif where == "index":
        repo.git(
            "update-index", "--add", "--cacheinfo", f"100644,{repo.blob(ignoring)},.gitmodules"
        )
    else:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / ".gitmodules").write_bytes(ignoring)
        repo.git("config", "core.worktree", str(elsewhere))
    # Calibration: plain diff-tree follows the setting and shows nothing.
    assert repo.git("diff-tree", "-r", "--name-only", c1, c2) == b""
    clone = GitRepository(repo.root)
    assert clone.differ(c1, c2) == frozenset({b"s"})
    assert clone.commit(c1).changed == frozenset({b"s", b".gitmodules"})
    assert [commit.changed for commit in clone.first_parent(c2, after=c1)] == [frozenset({b"s"})]


def _lie_in_the_commit_graph(repo: Repo, commit: str, parent: str) -> None:
    """Rewrite ``commit``'s first parent in the commit-graph file to ``parent``."""
    path = repo.root / ".git" / "objects" / "info" / "commit-graph"
    data = bytearray(path.read_bytes())
    assert data[:4] == b"CGPH"
    size = 20 if data[5] == 1 else 32
    chunks = {}
    for i in range(data[6] + 1):
        at = 8 + 12 * i
        chunks[bytes(data[at : at + 4])] = int.from_bytes(data[at + 4 : at + 12], "big")
    oids, cdat = chunks[b"OIDL"], chunks[b"CDAT"]
    count = (cdat - oids) // size
    names = [bytes(data[oids + i * size : oids + (i + 1) * size]).hex() for i in range(count)]
    entry = cdat + names.index(commit) * (size + 16) + size
    data[entry : entry + 4] = names.index(parent).to_bytes(4, "big")
    path.chmod(0o644)  # git writes it read-only, as it does every object file
    path.write_bytes(bytes(data))


def test_a_commit_graph_out_of_step_with_the_commits_changes_nothing(
    history: tuple[Repo, list[str]],
) -> None:
    repo, commits = history
    head = commits[-1]
    honest = GitRepository(repo.root).first_parent(head, after=commits[0])
    repo.git("commit-graph", "write", "--reachable")
    _lie_in_the_commit_graph(repo, commits[2], commits[0])
    # Calibration: plain git follows the lie and loses a commit.
    assert commits[1] not in repo.git("rev-list", "--first-parent", head).decode().split()
    assert GitRepository(repo.root).first_parent(head, after=commits[0]) == honest


def test_a_sha256_repository_is_read(tmp_path: Path, env: dict[str, str]) -> None:
    repo = Repo(tmp_path / "r256", env, object_format="sha256")
    c1 = repo.commit({b"x": text("1")}, b"feat: one\n")
    c2 = repo.commit({b"x": text("2")}, b"fix: two\n", c1)
    repo.branch(c2)
    clone = GitRepository(repo.root)
    assert len(c2) == 64
    assert clone.commit(c1) == Commit(c1, "feat: one\n", frozenset({b"x"}))
    assert clone.first_parent(c2, after=c1) == (Commit(c2, "fix: two\n", frozenset({b"x"})),)
    assert clone.entries(c2, ["x"]) == (TreeEntry(b"x", "100644", b"2"),)


def test_a_commit_object_s_other_headers_are_not_its_message(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    tree = repo.git("mktree", stdin=b"").decode().strip()
    body = (
        f"tree {tree}\n"
        "author a <a@example.invalid> 1792411200 +0000\n"
        "committer a <a@example.invalid> 1792411200 +0000\n"
        "encoding ISO-8859-1\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " \n"
        " iQ\n"
        " -----END PGP SIGNATURE-----\n"
        "\n"
    ).encode() + b"feat: signed \xff\n\nBREAKING CHANGE: no\n"
    sha = repo.git("hash-object", "-t", "commit", "-w", "--stdin", stdin=body).decode().strip()
    commit = GitRepository(repo.root).commit(sha)
    # A byte that is not UTF-8 is the replacement character; commit-tree would have re-encoded
    # it as Latin-1, so the object is written raw.
    assert commit.message == "feat: signed \ufffd\n\nBREAKING CHANGE: no\n"


# --- the tree ------------------------------------------------------------------------------------


def test_the_entries_under_paths_are_each_blob_and_gitlink_with_its_mode(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    gitlink = "1" * 40
    sha = repo.commit(
        {
            b"release-policy.yaml": text("p"),
            b"artifacts/api/artifact.yaml": text("a"),
            b"artifacts/" + E_ACUTE + b"/artifact.yaml": text("e"),
            b"artifacts/link": ("120000", b"../x"),
            b"artifacts/run": ("100755", b"#!"),
            b"artifacts/sub": ("160000", gitlink.encode()),
            b"state/dev/api.yaml": text("s"),
            b"src/main.py": text("m"),
            b"artifactsX/y": text("n"),
        },
        b"chore: tree\n",
    )
    clone = GitRepository(repo.root)
    assert clone.entries(sha, ["release-policy.yaml", "artifacts"]) == (
        TreeEntry(b"artifacts/api/artifact.yaml", "100644", b"a"),
        TreeEntry(b"artifacts/link", "120000", b"../x"),
        TreeEntry(b"artifacts/run", "100755", b"#!"),
        TreeEntry(b"artifacts/sub", "160000", b""),
        TreeEntry(b"artifacts/" + E_ACUTE + b"/artifact.yaml", "100644", b"e"),
        TreeEntry(b"release-policy.yaml", "100644", b"p"),
    )
    assert clone.entries(sha, ["artifacts/api", "src/main.py"]) == (
        TreeEntry(b"artifacts/api/artifact.yaml", "100644", b"a"),
        TreeEntry(b"src/main.py", "100644", b"m"),
    )
    # A path through a blob or a gitlink, or one the tree lacks, holds nothing.
    assert clone.entries(sha, ["src/main.py/x", "artifacts/sub/x", "nothing", "artifact"]) == ()
    assert clone.entries(sha, []) == ()
    # One path given as a string would be read as its letters.
    with pytest.raises(ValueError, match=r"^paths: a sequence of paths"):
        clone.entries(sha, "src")


@pytest.mark.parametrize(
    "path", [".", "", "artifacts/", "/artifacts", "a//b", "a/../b", "art*", ":(top)x", "é"]
)
def test_a_path_out_of_the_glob_grammar_is_the_caller_s_error(
    history: tuple[Repo, list[str]], path: str
) -> None:
    """One rule for a path, the input glob grammar without wildcards: ``.`` would be the whole
    tree."""
    repo, commits = history
    with pytest.raises(ValueError, match=r"^a path with no \*, \? or \["):
        GitRepository(repo.root).entries(commits[-1], [path])


# --- every object is what its name says --------------------------------------------------------


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize("kind", ["blob", "tree", "commit"])
def test_an_object_rewritten_under_its_own_name_is_refused(
    tmp_path: Path, env: dict[str, str], kind: str, object_format: str
) -> None:
    repo = Repo(tmp_path / "r", env, object_format=object_format)
    c1 = repo.commit({b"release-policy.yaml": text("honest")}, b"feat: one\n")
    c2 = repo.commit({b"release-policy.yaml": text("honest 2")}, b"feat: two\n", c1)
    evil = repo.commit({b"release-policy.yaml": text("evil")}, b"feat!: evil\n", c1)
    name = {
        "blob": repo.git("rev-parse", f"{c2}:release-policy.yaml").decode().strip(),
        "tree": repo.tree(c2),
        "commit": c2,
    }[kind]
    body = {
        "blob": b"evil",
        "tree": repo.git("cat-file", "tree", repo.tree(evil)),
        "commit": repo.git("cat-file", "commit", evil),
    }[kind]
    repo.substitute(name, kind, body)
    # Calibration: git reads the rewritten object as it is, under the real name.
    assert repo.git("cat-file", kind, name) == body
    clone = GitRepository(repo.root)
    corrupt = f"^{name} {re.escape(adapter.CORRUPT)}$"
    with pytest.raises(GitError, match=corrupt):
        clone.entries(c2, ["release-policy.yaml"])
    if kind != "blob":  # a commit's changes are read from its trees, never its blobs
        with pytest.raises(GitError, match=corrupt):
            clone.commit(c2)
        with pytest.raises(GitError, match=corrupt):
            clone.first_parent(c2, after=c1)
        with pytest.raises(GitError, match=corrupt):
            clone.differ(c1, c2)
    if kind == "commit":
        with pytest.raises(GitError, match=corrupt):
            clone.require(c2)


def test_a_packed_object_another_object_s_content_is_read_for_is_refused(
    tmp_path: Path, env: dict[str, str]
) -> None:
    """A pack's index rewritten so the name of one blob points at another's bytes."""
    repo = Repo(tmp_path / "r", env)
    c1 = repo.commit({b"p": text("honest")}, b"feat: one\n")
    c2 = repo.commit({b"p": text("evil__")}, b"feat: two\n", c1)
    repo.branch(c2)
    repo.git("repack", "-a", "-d", "-q")
    honest = repo.git("rev-parse", f"{c1}:p").decode().strip()
    evil = repo.git("rev-parse", f"{c2}:p").decode().strip()
    (index,) = (repo.root / ".git" / "objects" / "pack").glob("*.idx")
    data = bytearray(index.read_bytes())
    assert data[:8] == b"\xfftOc\x00\x00\x00\x02"  # version 2
    count = int.from_bytes(data[8 + 255 * 4 : 8 + 256 * 4], "big")
    names = [bytes(data[1032 + i * 20 : 1032 + (i + 1) * 20]).hex() for i in range(count)]
    offsets = 1032 + count * 20 + count * 4
    a, b = (offsets + names.index(name) * 4 for name in (honest, evil))
    data[a : a + 4], data[b : b + 4] = data[b : b + 4], data[a : a + 4]
    index.chmod(0o644)
    index.write_bytes(bytes(data))
    # Calibration: git reads the other blob's bytes under the honest name.
    assert repo.git("cat-file", "blob", honest) == b"evil__"
    with pytest.raises(GitError, match=f"^{honest} {re.escape(adapter.CORRUPT)}$"):
        GitRepository(repo.root).entries(c1, ["p"])


# Every form of a mode field: those git writes, those older tools wrote, padded, and types git
# reads as a gitlink; the adapter's reading of each is compared with git's.
MODE_FIELDS = (
    "100644", "100755", "120000", "40000", "160000", "100664", "100600", "100654", "100011",
    "100700", "100711", "0100644", "0120000", "040000", "040755", "0160000", "644", "1", "100",
    "20000", "140000", "170000", "37777700644",
)  # fmt: skip


@pytest.mark.parametrize("field", MODE_FIELDS)
def test_a_tree_s_mode_is_the_one_git_reads(
    tmp_path: Path, env: dict[str, str], field: str
) -> None:
    repo = Repo(tmp_path / "r", env)
    blob = repo.blob(b"x")
    body = f"{field} x\0".encode() + bytes.fromhex(blob)
    tree = repo.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", stdin=body)
    tree_name = tree.decode().strip()
    shown = repo.git("ls-tree", tree_name).decode().split(" ")[0]
    assert adapter.parse_tree(tree_name, body) == {b"x": (shown.lstrip("0") or "0", blob)}


@pytest.mark.parametrize("field", ["", "1x0644", "8"])
def test_a_mode_git_cannot_read_is_refused(tmp_path: Path, env: dict[str, str], field: str) -> None:
    repo = Repo(tmp_path / "r", env)
    body = f"{field} x\0".encode() + bytes.fromhex(repo.blob(b"x"))
    tree = repo.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", stdin=body)
    tree_name = tree.decode().strip()
    assert GIT is not None
    listed = subprocess.run(
        [GIT, "ls-tree", tree_name], cwd=repo.root, env=repo.env, capture_output=True, check=False
    )
    assert listed.returncode != 0  # Calibration: git refuses it too.
    with pytest.raises(GitError, match=f"^{tree_name} {adapter.MALFORMED}$"):
        adapter.parse_tree(tree_name, body)


# Trees as (mode, name) entries, each with whether git's fsck reports an error for it, which the
# test also asks fsck: fsck's verdict decides whether the adapter refuses the tree. A name holding
# / is the one tree the adapter refuses that fsck only warns of (fullPathname).
TREES = {
    "dot-names-in-order": ([("40000", b".."), ("40000", b".")], False),
    "dot-names-out-of-order": ([("40000", b"."), ("40000", b"..")], True),
    "a-file-before-a-tree-it-prefixes": ([("100644", b"a.b"), ("40000", b"a")], False),
    "a-tree-before-a-file-it-prefixes": ([("40000", b"a"), ("100644", b"a.b")], True),
    "a-name-twice": ([("100644", b"a"), ("100644", b"a")], True),
    "a-file-and-a-tree-of-one-name": ([("100644", b"a"), ("40000", b"a")], True),
    "dot-git": ([("100644", b".git")], False),
    "a-name-holding-a-slash": ([("100644", b"a/b")], False),
}


@pytest.mark.parametrize(("entries", "fsck_error"), TREES.values(), ids=TREES.keys())
def test_a_tree_is_refused_where_git_s_fsck_reports_an_error(
    tmp_path: Path, env: dict[str, str], entries: list[tuple[str, bytes]], fsck_error: bool
) -> None:
    repo = Repo(tmp_path / "r", env)
    blob, empty = repo.blob(b"x"), repo.git("mktree", stdin=b"").decode().strip()
    body = b"".join(
        mode.encode() + b" " + name + b"\0" + bytes.fromhex(empty if mode == "40000" else blob)
        for mode, name in entries
    )
    tree = repo.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", stdin=body)
    name = tree.decode().strip()
    assert GIT is not None
    fsck = subprocess.run(
        [GIT, "fsck", "--no-dangling"], cwd=repo.root, env=repo.env, capture_output=True
    )
    assert (fsck.returncode != 0) == fsck_error  # Calibration: fsck judges it so.
    refused = fsck_error or any(b"/" in entry for _, entry in entries)
    if refused:
        with pytest.raises(GitError, match=f"^{name} {adapter.MALFORMED}$"):
            adapter.parse_tree(name, body)
    else:
        assert list(adapter.parse_tree(name, body)) == [entry for _, entry in entries]


def test_a_tree_entry_naming_an_object_of_another_type_is_refused(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    c1 = repo.commit({b"x": text("1")}, b"feat: one\n")
    tree = repo.tree(c1)
    raw = b"100644 x\0" + bytes.fromhex(tree)  # "x" is a file, its object a tree
    crafted = repo.git("hash-object", "-t", "tree", "-w", "--literally", "--stdin", stdin=raw)
    sha = repo.git("commit-tree", crafted.decode().strip(), stdin=b"chore: crafted\n")
    commit = sha.decode().strip()
    with pytest.raises(GitError, match=f"^{tree} {adapter.MALFORMED}$"):
        GitRepository(repo.root).entries(commit, ["x"])


@dataclass(frozen=True)
class Setting:
    """A setting of git's, with a value other than its default. ``probe`` is a plain git command
    whose output the setting changes, on the ``rich`` repository (``C<n>`` is its commit n, from
    1); a setting no object read uses has no probe, and ``unread`` says why, from git-config."""

    key: str
    value: str
    probe: tuple[str, ...] = ()
    unread: str = ""


# The settings that shape a listing, a diff, a message or a blob as plain git gives it, one line
# each, so a setting git adds is one line here. Each one's effect on plain git is shown by its
# probe, or its reason is checked against the object reads; the commit-graph, replace refs and
# grafts change git's answers by what the repository holds, and the equality test sets them too.
# A setting that runs a program is in _hostile.
SETTINGS: tuple[Setting, ...] = (
    Setting("core.ignoreCase", "true", unread="matches the work tree's and the index's paths"),
    Setting("core.precomposeUnicode", "true", unread="on macOS, precomposes paths git is given"),
    Setting("core.quotePath", "false", ("log", "-1", "--name-only", "--format=", "C9")),
    Setting("i18n.commitEncoding", "ISO-8859-1", ("log", "-1", "--format=%s", "C9")),
    Setting("i18n.logOutputEncoding", "ISO-8859-1", ("log", "-1", "--format=%s", "C9")),
    Setting("color.ui", "always", ("log", "-1", "--stat", "C10")),
    Setting("color.diff", "always", ("log", "-1", "-p", "C9")),
    Setting("diff.relative", "true", ("-C", "dir", "log", "-1", "--name-only", "--format=", "C9")),
    Setting("diff.renames", "false", ("log", "-1", "--name-status", "--format=", "C3")),
    Setting("diff.ignoreSubmodules", "all", ("log", "-1", "--name-only", "--format=", "C10")),
    Setting("submodule.s.ignore", "all", ("diff-tree", "-r", "--name-only", "C9", "C10")),
    Setting("core.bigFileThreshold", "1", ("log", "-1", "-p", "--format=", "C9")),
    Setting("core.autocrlf", "true", ("cat-file", "--filters", "C9:dir/crlf.txt")),
    Setting("core.eol", "crlf", ("cat-file", "--filters", "C9:dir/crlf.txt")),
    Setting("core.abbrev", "4", ("log", "-1", "--oneline", "C10")),
    Setting("diff.noprefix", "true", ("log", "-1", "-p", "--format=", "C9")),
)


def _plain(repo: Repo, commits: list[str], probe: tuple[str, ...], *config: str) -> bytes:
    """A probe's output from plain git, with ``C<n>`` read as commit n of ``commits``."""
    named = [re.sub(r"\bC(\d+)", lambda m: commits[int(m.group(1)) - 1], arg) for arg in probe]
    return repo.git(*config, *named)


# What the adapter reads, as plain git reads it: a setting no object read uses leaves these alone.
OBJECT_READS: tuple[tuple[str, ...], ...] = (
    ("log", "--first-parent", "-z", "--format=%H%x00%B", "--name-status", "C10"),
    ("ls-tree", "-r", "-z", "C10"),
    ("diff-tree", "-r", "-z", "--name-status", "C1", "C10"),
    ("cat-file", "--batch-all-objects", "--batch"),
)


@pytest.mark.parametrize("setting", SETTINGS, ids=lambda setting: setting.key)
def test_each_setting_changes_plain_git_or_no_object_read_uses_it(
    rich: tuple[Repo, list[str]], setting: Setting
) -> None:
    repo, commits = rich
    config = ("-c", f"{setting.key}={setting.value}")
    if setting.probe:
        assert _plain(repo, commits, setting.probe, *config) != _plain(repo, commits, setting.probe)
    else:
        assert setting.unread
        for read in OBJECT_READS:
            assert _plain(repo, commits, read, *config) == _plain(repo, commits, read)


@pytest.fixture
def rich(tmp_path: Path, history: tuple[Repo, list[str]]) -> tuple[Repo, list[str]]:
    """The history and two commits more that give every setting something to act on: a gitlink
    and its .gitmodules, two paths equal but for case, a path in NFD, a text file with line
    endings and the attribute that makes it text, a blob under a subdirectory and that
    subdirectory in the work tree."""
    repo, commits = history
    head = commits[-1]
    files = {
        b"README.md": text("r"),
        b"readme.md": text("lower"),
        b"n" + "e\u0301".encode(): text("nfd"),
        b"s": ("160000", b"1" * 40),
        b".gitmodules": ("100644", b'[submodule "s"]\n\tpath = s\n'),
        b"dir/crlf.txt": text("a\nb\n"),
    }
    c8 = repo.commit(files, "feat: é\n".encode(), head)
    c9 = repo.commit({**files, b"s": ("160000", b"2" * 40)}, b"fix: bump s\n", c8)
    repo.branch(c9)
    (repo.root / ".git" / "info").mkdir(exist_ok=True)
    (repo.root / ".git" / "info" / "attributes").write_text("*.txt text\n")
    (repo.root / "dir").mkdir()
    return repo, [*commits, c8, c9]


def _answers(root: Path, commits: list[str]) -> tuple[object, ...]:
    clone = GitRepository(root)
    clone.require(*commits)
    first, side, head = commits[0], commits[6], commits[-1]
    return (
        clone.commit(first),
        clone.first_parent(head, after=first),
        tuple(clone.differ(a, b) for a in commits for b in (first, side, head)),
        tuple(clone.entries(sha, ["README.md", "dir", "s", "a", "c"]) for sha in commits),
    )


@pytest.mark.parametrize("level", ["repository", "every"])
def test_the_settings_that_change_git_s_answers_change_none_of_the_adapter_s(
    tmp_path: Path, rich: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    """``SETTINGS`` in the repository's configuration, or also in a system and a global file
    with the two variables that keep those out dropped: they narrow, and no answer needs them."""
    repo, commits = rich
    clean = tmp_path / "clean"
    shutil.copytree(repo.root, clean, symlinks=True)
    honest = _answers(clean, commits)
    for setting in SETTINGS:
        repo.git("config", setting.key, setting.value)
    if level == "every":
        files = {name: tmp_path / f"{name}.config" for name in ("system", "global")}
        for file in files.values():
            for setting in SETTINGS:
                repo.git("config", "--file", str(file), setting.key, setting.value)
        stat = ("log", "-1", "--stat", commits[-1])
        # Calibration: a global file reaches plain git.
        assert repo.git("-C", str(clean), *stat, GIT_CONFIG_GLOBAL=str(files["global"])) != (
            repo.git("-C", str(clean), *stat)
        )
        variables = adapter.environment()
        del variables["GIT_CONFIG_NOSYSTEM"]
        variables["GIT_CONFIG_SYSTEM"] = str(files["system"])
        variables["GIT_CONFIG_GLOBAL"] = str(files["global"])
        monkeypatch.setattr(adapter, "environment", lambda: dict(variables))
    repo.git("replace", commits[2], commits[0])
    (repo.root / ".git" / "info").mkdir(exist_ok=True)
    (repo.root / ".git" / "info" / "grafts").write_text(f"{commits[4]}\n")
    repo.git("commit-graph", "write", "--reachable", GIT_GRAFT_FILE=os.devnull)
    _lie_in_the_commit_graph(repo, commits[-1], commits[-3])
    # Calibration: plain git's answers on the two differ.
    log = ("log", "--first-parent", "--name-only", "--format=%H %s", commits[-1])
    assert repo.git(*log) != repo.git("-C", str(clean), *log)
    assert _answers(repo.root, commits) == honest


# --- a real history -------------------------------------------------------------------------------

# GORA's own main, pinned for good: 105 commits by first parent, merges and renames among them,
# and real trees. A bundle, because the clone the suite runs in has no history where it is
# exported (verify-export, verify-linux) or checked out shallow (CI). Its own history, since no
# foreign repository with these properties and a clear license is at hand; a connected project's
# history is the next one. What it lacks (gitlinks, links, odd modes) the tests above build.
REAL_HISTORY = Path(__file__).resolve().parents[1] / "fixtures" / "git" / "gora-main.bundle"
REAL_HEAD = "673e3c7f016d795748f93a724b8d0cff1a8ac7e4"


@pytest.fixture(scope="module")
def real(tmp_path_factory: pytest.TempPathFactory) -> tuple[Repo, list[str]]:
    """The bundle cloned into a temporary directory, and its first-parent commits, oldest
    first, as plain git gives them."""
    root = tmp_path_factory.mktemp("real") / "gora"
    env = hermetic_git_env(root.parent / "home")
    assert GIT is not None
    subprocess.run(
        [GIT, "clone", "-q", "--no-checkout", str(REAL_HISTORY), str(root)],
        env=env, capture_output=True, check=True,
    )  # fmt: skip
    repo = Repo(root, env, init=False)
    chain = repo.git("rev-list", "--first-parent", "--reverse", REAL_HEAD).decode().split()
    return repo, chain


def test_a_real_history_s_changes_are_the_ones_git_diff_tree_gives(
    real: tuple[Repo, list[str]],
) -> None:
    repo, chain = real
    assert chain[-1] == REAL_HEAD and len(chain) == 105  # Calibration: the history pinned.
    merges = repo.git("rev-list", "--first-parent", "--merges", REAL_HEAD).split()
    assert merges  # merge commits, each diffed against its first parent alone
    clone = GitRepository(repo.root)
    read = (clone.commit(chain[0]), *clone.first_parent(REAL_HEAD, after=chain[0]))
    assert [commit.sha for commit in read] == chain
    renamed = 0
    for commit, parent in zip(read, [None, *chain[:-1]], strict=True):
        against = ("--root", commit.sha) if parent is None else (parent, commit.sha)
        raw = repo.git("diff-tree", "-r", "-z", "--no-renames", "--name-status", *against)
        fields = raw.split(b"\0")[:-1]
        if parent is None:
            fields = fields[1:]  # --root names the commit first
        paths = frozenset(fields[1::2])
        assert commit.changed == paths, commit.sha
        renamed += bool(
            repo.git("diff-tree", "-r", "-M", "--diff-filter=R", "--name-only", *against)
        )
        message = repo.git("log", "-1", "-z", "--format=%B", commit.sha).removesuffix(b"\0")
        assert commit.message == message.decode("utf-8", "replace"), commit.sha
    assert renamed  # renames, both sides of each among the changed paths
    for sha in chain:
        listed = repo.git("diff-tree", "-r", "-z", "--name-only", sha, REAL_HEAD).split(b"\0")
        assert clone.differ(sha, REAL_HEAD) == frozenset(path for path in listed if path), sha


def test_a_real_history_s_trees_are_the_ones_git_ls_tree_gives(
    real: tuple[Repo, list[str]],
) -> None:
    repo, chain = real
    clone = GitRepository(repo.root)
    for sha in chain:
        top = repo.git("ls-tree", "-z", "--name-only", sha).split(b"\0")
        names = [name.decode() for name in top if name]
        listed = []
        for record in repo.git("ls-tree", "-r", "-z", sha).split(b"\0")[:-1]:
            info, _, path = record.partition(b"\t")
            mode, _, name = info.decode().split(" ")
            listed.append((path, mode, name))
        entries = clone.entries(sha, names)
        assert [(entry.path, entry.mode) for entry in entries] == [
            (path, mode) for path, mode, _ in sorted(listed)
        ], sha
        # Each blob's bytes are the object git names, read and hashed by the adapter.
        assert [adapter.object_name("blob", entry.data, width=40) for entry in entries] == [
            name for _, _, name in sorted(listed)
        ], sha


# --- refusals ------------------------------------------------------------------------------------


def test_an_object_the_clone_lacks_is_refused(history: tuple[Repo, list[str]]) -> None:
    repo, commits = history
    absent = "f" * 40
    clone = GitRepository(repo.root)
    with pytest.raises(GitError, match=f"^{absent} {adapter.MISSING}$"):
        clone.commit(absent)
    with pytest.raises(GitError, match=f"^{absent} {adapter.MISSING}$"):
        clone.first_parent(commits[-1], after=absent)
    with pytest.raises(GitError, match=f"^{absent} {adapter.MISSING}$"):
        clone.differ(commits[0], absent)
    with pytest.raises(GitError, match=f"^{absent} {adapter.MISSING}$"):
        clone.require(commits[0], absent)
    tree = repo.tree(commits[0])
    with pytest.raises(GitError, match=f"^{tree} {adapter.NOT_A_COMMIT}$"):
        clone.entries(tree, ["x"])


def test_a_commit_missing_between_the_two_is_refused_with_git_s_reason(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r", env)
    c1 = repo.commit({b"x": text("1")}, b"feat: one\n")
    c2 = repo.commit({b"x": text("2")}, b"fix: two\n", c1)
    c3 = repo.commit({b"x": text("3")}, b"fix: three\n", c2)
    (repo.root / ".git" / "objects" / c2[:2] / c2[2:]).unlink()
    with pytest.raises(GitError, match=r"^rev-list: git failed \(128\): ") as caught:
        GitRepository(repo.root).first_parent(c3, after=c1)
    assert c2 in str(caught.value)  # git's error: line names it, its fatal: line does not
    assert "fatal: " in str(caught.value)
    assert "hint" not in str(caught.value)


def test_an_abbreviated_name_is_not_the_commit_it_abbreviates(
    tmp_path: Path, env: dict[str, str]
) -> None:
    repo = Repo(tmp_path / "r256", env, object_format="sha256")
    sha = repo.commit({b"x": text("1")}, b"feat: one\n")
    short = sha[:40]
    with pytest.raises(GitError, match=f"^{short} {adapter.MISSING}$"):
        GitRepository(repo.root).commit(short)


@pytest.mark.parametrize("bad", ["main", "HEAD", "-n1", "f" * 39, "F" * 40, "--all"])
def test_a_name_that_is_not_an_object_name_is_the_caller_s_error(
    history: tuple[Repo, list[str]], bad: str
) -> None:
    repo, commits = history
    clone = GitRepository(repo.root)
    for call in (
        lambda: clone.commit(bad),
        lambda: clone.first_parent(bad, after=commits[0]),
        lambda: clone.first_parent(commits[-1], after=bad),
        lambda: clone.differ(commits[0], bad),
        lambda: clone.entries(bad, ["x"]),
        lambda: clone.require(bad),
    ):
        with pytest.raises(ValueError, match=r"^commit must be"):
            call()


def test_a_shallow_clone_is_refused(tmp_path: Path, history: tuple[Repo, list[str]]) -> None:
    repo, _ = history
    target = tmp_path / "shallow"
    repo.git("clone", "-q", "--depth", "1", f"file://{repo.root}", str(target))
    with pytest.raises(GitError, match=f"{adapter.SHALLOW}$"):
        GitRepository(target)


def test_a_partial_clone_is_refused(tmp_path: Path, history: tuple[Repo, list[str]]) -> None:
    repo, commits = history
    repo.git("config", "uploadpack.allowFilter", "true")
    repo.git("update-ref", "HEAD", commits[-1])
    target = tmp_path / "partial"
    repo.git(
        "clone", "-q", "--no-checkout", "--filter=blob:none", f"file://{repo.root}", str(target)
    )  # fmt: skip
    with pytest.raises(GitError, match=f"{adapter.PARTIAL}$"):
        GitRepository(target)
    # Calibration: plain git fetches what the partial clone lacks from its promisor remote.
    blob = repo.git("rev-parse", f"{commits[-1]}:README.md").decode().strip()
    assert repo.git("-C", str(target), "cat-file", "-p", blob) == b"r"


@pytest.mark.parametrize("removed", ["nothing", "GIT_NO_LAZY_FETCH", "GIT_ALLOW_PROTOCOL"])
@pytest.mark.parametrize("url", ["file", "ext"])
def test_a_promisor_remote_is_never_asked_for_what_the_clone_lacks(
    tmp_path: Path,
    history: tuple[Repo, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    removed: str,
) -> None:
    """A clone configured as partial without a promisor pack passes construction; the object it
    lacks is still a loud error, and no transport runs, even one the repository allows itself.
    Either variable alone stops the fetch: ``GIT_ALLOW_PROTOCOL`` for a git that ignores
    ``GIT_NO_LAZY_FETCH``, and the other way round. A git that ignores ``GIT_NO_LAZY_FETCH``,
    one before its 2024 security releases (2.39.4, 2.43.4, 2.45.1), fails this test."""
    repo, commits = history
    head = commits[-1]
    marker = tmp_path / "fetched"
    script = tmp_path / "upload.sh"
    script.write_text(f'#!/bin/sh\necho fetched >> {marker}\nexec git-upload-pack "$@"\n')
    script.chmod(0o755)
    target = Repo(tmp_path / "lazy", hermetic_git_env(tmp_path / "home2"))
    for sha in commits:
        body = repo.git("cat-file", "commit", sha)
        target.git("hash-object", "-t", "commit", "-w", "--stdin", stdin=body)
    for sha in commits:  # the trees, but no blob
        tree = repo.git("rev-parse", f"{sha}^{{tree}}").decode().strip()
        for line in repo.git("ls-tree", "-r", "-t", tree).decode().splitlines():
            _, kind, name = line.split("\t")[0].split(" ")
            if kind == "tree":
                body = repo.git("cat-file", "tree", name)
                target.git("hash-object", "-t", "tree", "-w", "--stdin", stdin=body)
        body = repo.git("cat-file", "tree", tree)
        target.git("hash-object", "-t", "tree", "-w", "--stdin", stdin=body)
    remote = f"file://{repo.root}" if url == "file" else f"ext::sh -c touch% {marker};false"
    target.git("config", "remote.origin.url", remote)
    target.git("config", "protocol.file.allow", "always")
    target.git("config", "protocol.ext.allow", "always")
    target.git("config", "remote.origin.promisor", "true")
    target.git("config", "remote.origin.uploadpack", str(script))
    target.git("config", "extensions.partialClone", "origin")
    target.git("config", "core.repositoryformatversion", "1")
    blob = repo.git("rev-parse", f"{head}:README.md").decode().strip()
    if removed != "nothing":
        variables = adapter.environment()
        del variables[removed]
        monkeypatch.setattr(adapter, "environment", lambda: dict(variables))
    with pytest.raises(GitError, match=f"^{blob} {re.escape(adapter.MISSING)}$"):
        GitRepository(target.root).entries(head, ["README.md"])
    assert not marker.exists()
    # Calibration: plain git asks the promisor remote, through the configured program.
    plain = target.git if url == "file" else functools.partial(target.git, check=False)
    plain("cat-file", "-p", blob)
    assert marker.exists()


def test_a_git_file_or_link_naming_another_repository_is_refused(
    tmp_path: Path, history: tuple[Repo, list[str]]
) -> None:
    repo, _ = history
    gitfile = tmp_path / "gitfile"
    gitfile.mkdir()
    (gitfile / ".git").write_text(f"gitdir: {repo.root / '.git'}\n")
    with pytest.raises(GitError, match=f"{adapter.NOT_A_REPOSITORY}$"):
        GitRepository(gitfile)
    link = tmp_path / "link"
    link.mkdir()
    (link / ".git").symlink_to(repo.root / ".git")
    with pytest.raises(GitError, match=f"{adapter.NOT_A_REPOSITORY}$"):
        GitRepository(link)
    with pytest.raises(GitError, match=f"{adapter.NOT_A_REPOSITORY}$"):
        GitRepository(tmp_path)
    no_objects = tmp_path / "no-objects"
    (no_objects / ".git").mkdir(parents=True)
    with pytest.raises(GitError, match=f"{adapter.NOT_A_REPOSITORY}$"):
        GitRepository(no_objects)


@pytest.mark.parametrize("how", ["commondir", "alternates", "objects-link", "pack-link"])
def test_a_git_directory_that_borrows_another_repository_s_objects_is_refused(
    tmp_path: Path, history: tuple[Repo, list[str]], how: str
) -> None:
    repo, commits = history
    other = Repo(tmp_path / "other", hermetic_git_env(tmp_path / "home2"))
    git_dir = other.root / ".git"
    if how == "commondir":
        (git_dir / "commondir").write_text(f"{repo.root / '.git'}\n")
    elif how == "alternates":
        (git_dir / "objects" / "info" / "alternates").write_text(f"{repo.root / '.git/objects'}\n")
    elif how == "objects-link":
        os.rename(git_dir / "objects", git_dir / "objects.old")
        (git_dir / "objects").symlink_to(repo.root / ".git" / "objects")
    else:
        repo.git("repack", "-a", "-d", "-q")
        os.rename(git_dir / "objects" / "pack", git_dir / "objects" / "pack.old")
        (git_dir / "objects" / "pack").symlink_to(repo.root / ".git" / "objects" / "pack")
    # Calibration: plain git reads the other repository's commits through it.
    assert other.git("cat-file", "-t", commits[-1]) == b"commit\n"
    with pytest.raises(GitError, match=f"{re.escape(adapter.BORROWED)}$"):
        GitRepository(other.root)


def test_no_git_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(GitError, match=f"^{adapter.NO_GIT}$"):
        GitRepository(tmp_path)


def test_a_git_named_without_a_directory_is_looked_up_on_path(
    tmp_path: Path, history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = history
    assert GIT is not None
    monkeypatch.chdir(tmp_path)  # where a git that is not on PATH would be taken from
    clone = GitRepository(repo.root, git=os.path.basename(GIT))
    assert clone.argv(Command.CAT_FILE)[0] == shutil.which(os.path.basename(GIT))


def test_git_s_message_is_bounded(
    history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commits = history
    clone = GitRepository(repo.root)
    noise = "".join(f"error: {i} {'x' * 400}\n" for i in range(20)).encode()

    def failing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 128, b"", noise)

    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(GitError) as caught:
        clone.commit(commits[0])
    lines = str(caught.value).removeprefix("cat-file: git failed (128): ").split("; ")
    assert [line[:9] for line in lines] == [f"error: {i} " for i in range(5)]
    assert all(len(line) == adapter.MESSAGE_LINE for line in lines)


def test_a_git_that_cannot_run_is_refused(history: tuple[Repo, list[str]], tmp_path: Path) -> None:
    repo, _ = history
    with pytest.raises(GitError, match=f"^rev-parse: {adapter.FAILED}: No such file or directory$"):
        GitRepository(repo.root, git=str(tmp_path / "nothing" / "git"))


def test_a_directory_that_cannot_be_listed_is_refused(
    history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory under objects that cannot be listed could hold a link, so it is refused."""
    repo, _ = history
    scandir = os.scandir

    def denied(path: str | os.PathLike[str]) -> object:
        if str(path).endswith(os.path.join("objects", "info")):
            raise PermissionError(13, "Permission denied", str(path))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", denied)
    unlistable = f"{re.escape(adapter.UNLISTABLE)}: Permission denied$"
    with pytest.raises(GitError, match=unlistable):
        GitRepository(repo.root)
    monkeypatch.setattr(os, "scandir", scandir)
    listdir = os.listdir

    def no_packs(path: str) -> list[str]:
        if path.endswith("pack"):
            raise PermissionError(13, "Permission denied", path)
        return listdir(path)

    monkeypatch.setattr(os, "listdir", no_packs)
    with pytest.raises(GitError, match=unlistable):
        GitRepository(repo.root)


def test_a_git_that_does_not_finish_is_stopped(
    history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commits = history
    clone = GitRepository(repo.root)
    timeouts: list[object] = []

    def hanging(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        timeouts.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hanging)
    with pytest.raises(GitError, match=f"^cat-file: {adapter.TIMED_OUT}$"):
        clone.commit(commits[0])
    assert timeouts == [adapter.TIMEOUT]


# --- history cannot be rewritten under the adapter -----------------------------------------------


def test_neither_a_replace_ref_nor_a_graft_rewrites_the_history(
    history: tuple[Repo, list[str]],
) -> None:
    repo, commits = history
    head = commits[-1]
    honest = GitRepository(repo.root).first_parent(head, after=commits[0])
    # Calibration: plain git follows both.
    repo.git("replace", commits[2], commits[0])
    assert len(repo.git("rev-list", "--first-parent", head).split()) < len(honest)
    repo.git("replace", "-d", commits[2])
    (repo.root / ".git" / "info").mkdir(exist_ok=True)
    (repo.root / ".git" / "info" / "grafts").write_text(f"{head}\n")
    assert len(repo.git("rev-list", "--first-parent", head).split()) == 1
    repo.git("replace", commits[2], commits[0])
    assert GitRepository(repo.root).first_parent(head, after=commits[0]) == honest


# --- a hostile repository runs nothing -----------------------------------------------------------


def _hostile(repo: Repo, marker: Path) -> None:
    """Every place a repository can name a program, each writing ``marker`` when run."""
    script = repo.root.parent / "evil.sh"
    script.write_text(f'#!/bin/sh\necho "$0 $*" >> {marker}\ncat\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    included = repo.root.parent / "included.config"
    included.write_text(f"[core]\n\tpager = {script}\n")
    with (repo.root / ".git" / "config").open("a") as config:
        config.write(
            "[core]\n"
            f"\tfsmonitor = {script}\n"
            f"\tpager = {script}\n"
            f"\tsshCommand = {script}\n"
            f"\taskPass = {script}\n"
            f"\teditor = {script}\n"
            f"\thooksPath = {repo.root / '.git' / 'hooks'}\n"
            "[diff]\n"
            f"\texternal = {script}\n"
            '[diff "evil"]\n'
            f"\ttextconv = {script}\n"
            '[filter "evil"]\n'
            f"\tsmudge = {script}\n"
            f"\tclean = {script}\n"
            "[credential]\n"
            f"\thelper = !{script}\n"
            "[gpg]\n"
            f"\tprogram = {script}\n"
            "[include]\n"
            f"\tpath = {included}\n"
        )
    (repo.root / ".git" / "info").mkdir(exist_ok=True)
    (repo.root / ".git" / "info" / "attributes").write_text("* diff=evil filter=evil\n")
    hooks = repo.root / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for hook in ("post-checkout", "post-merge", "reference-transaction", "pre-auto-gc"):
        (hooks / hook).write_text(script.read_text())
        (hooks / hook).chmod(0o755)


def test_a_hostile_repository_s_programs_run_for_git_but_never_for_the_adapter(
    tmp_path: Path, history: tuple[Repo, list[str]]
) -> None:
    repo, commits = history
    head, first = commits[-1], commits[0]
    repo.git("read-tree", head)  # an index, so that status reads one
    marker = tmp_path / "ran"
    _hostile(repo, marker)
    clone = GitRepository(repo.root)
    clone.commit(first)
    clone.first_parent(head, after=first)
    clone.differ(first, head)
    clone.entries(head, ["README.md", "src"])
    clone.require(first, head)
    assert not marker.exists()
    # Calibration: the same configuration does run a program for what the adapter never runs.
    repo.git("status", "--porcelain")
    repo.git("diff", "--textconv", first, head)
    assert marker.exists()


def test_the_adapter_runs_only_its_three_commands(history: tuple[Repo, list[str]]) -> None:
    repo, _ = history
    clone = GitRepository(repo.root)
    assert {command.value for command in Command} == {"rev-parse", "rev-list", "cat-file"}
    with pytest.raises(ValueError, match="whitelist"):
        clone.argv("status")  # type: ignore[arg-type]


def test_the_caller_s_git_variables_never_reach_git(
    tmp_path: Path, history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commits = history
    empty = Repo(tmp_path / "empty", hermetic_git_env(tmp_path / "home2"))
    monkeypatch.setenv("GIT_DIR", str(empty.root / ".git"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(empty.root / ".git" / "objects"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.commitgraph'='true'")
    clone = GitRepository(repo.root)
    assert [commit.sha for commit in clone.first_parent(commits[-1], after=commits[0])] == [
        sha for sha in commits[1:] if sha != commits[6]
    ]


def test_the_environment_holds_no_configuration_and_no_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.pager'='x'")
    assert adapter.environment() == {
        "PATH": os.environ["PATH"],
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


def test_git_runs_by_an_absolute_name_from_the_root_of_the_file_system(
    tmp_path: Path, history: tuple[Repo, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative entry of ``PATH`` names no program inside the clone or wherever GORA runs."""
    repo, commits = history
    assert GIT is not None
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "git").symlink_to(GIT)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f"bin{os.pathsep}{os.environ['PATH']}")
    clone = GitRepository(repo.root)
    assert clone.argv(Command.CAT_FILE)[0] == str(tmp_path / "bin" / "git")
    places: list[object] = []
    run = subprocess.run

    def spying(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        places.append(kwargs["cwd"])
        return run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", spying)
    clone.commit(commits[0])
    assert places == [os.sep, os.sep]  # the commit, then its tree


def test_a_repository_another_user_owns_is_read_with_nothing_opened(
    history: tuple[Repo, list[str]],
) -> None:
    """git checks ownership only when it discovers a repository; the adapter names its own."""
    repo, commits = history
    clone = GitRepository(repo.root)
    owner = {**adapter.environment(), "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}
    argv = clone.argv(Command.REV_LIST, "--first-parent", commits[-1])
    assert not any(str(arg).startswith("safe.directory") for arg in argv)
    assert subprocess.run(argv, env=owner, capture_output=True, check=False).returncode == 0
    # Calibration: the variable is live; a git that discovers the repository refuses it.
    assert GIT is not None
    found = subprocess.run(
        [GIT, "rev-list", "--first-parent", commits[-1]],
        cwd=repo.root,
        env=owner,
        capture_output=True,
        check=False,
    )
    assert found.returncode != 0
    assert b"dubious ownership" in found.stderr

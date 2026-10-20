"""The parsers of the git adapter, on objects and output git never writes (ADR-006).

What is held: an object's name is computed as git computes it, SHA-1 or SHA-256 by the length
of the name; output that is not the shape ``cat-file --batch`` writes is refused as ``GitError``,
never read as an answer; and a commit or a tree object out of its grammar is refused as
``MALFORMED``: a tree entry with no mode or one with a byte that is not an octal digit, a name
that is empty or holds ``/``, a name twice, entries out of git's order, an entry cut short; a
commit without its tree, or naming objects of another length. A mode, and a name such as ``.``
or ``..``, is read as git reads it. The adapter's own tests show the objects git does write and
take each verdict from git (``tests/integration/test_git_adapter.py``). Expectations are written
out literally.
"""

import pytest

from gora.adapters.git.adapter import (
    MALFORMED,
    MISSING,
    UNREADABLE,
    GitError,
    object_name,
    parse_batch,
    parse_commit,
    parse_tree,
)

A = "a" * 40
B = "b" * 40
C = "c" * 64
RAW_B = bytes.fromhex(B)


def test_an_object_s_name_is_git_s() -> None:
    # The empty blob, as git names it in each format.
    assert object_name("blob", b"", width=40) == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert object_name("blob", b"", width=64) == (
        "473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813"
    )


def test_a_well_formed_batch_is_read() -> None:
    assert parse_batch(f"{A} blob 3\nxyz\n{B} tree 0\n\n".encode(), [A, B]) == [
        ("blob", b"xyz"),
        ("tree", b""),
    ]


def test_a_missing_object_is_named() -> None:
    with pytest.raises(GitError, match=f"^{A} {MISSING}$"):
        parse_batch(f"{A} missing\n".encode(), [A])
    # A name git took as an abbreviation of another object's.
    with pytest.raises(GitError, match=f"^{A} {MISSING}$"):
        parse_batch(f"{B} blob 3\nxyz\n".encode(), [A])


@pytest.mark.parametrize(
    "output",
    [
        pytest.param(b"", id="nothing"),
        pytest.param(f"{A} blob 3\nxy".encode(), id="body-cut-short"),
        pytest.param(f"{A} blob 3\nxyzw\n".encode(), id="body-longer"),
        pytest.param(f"{A} blob 3\nxyz".encode(), id="no-final-newline"),
        pytest.param(f"{A} blob x\nxyz\n".encode(), id="size-not-a-number"),
        pytest.param(f"{A} 3\nxyz\n".encode(), id="two-fields"),
        pytest.param(f"{A} blob 3\nxyz\n{B} blob 0\n\n".encode(), id="more-than-asked"),
    ],
)
def test_a_batch_out_of_shape_is_refused(output: bytes) -> None:
    with pytest.raises(GitError, match=f"^cat-file: {UNREADABLE}$"):
        parse_batch(output, [A])


def test_a_body_not_followed_by_its_newline_is_refused() -> None:
    """The next record would still be found where it should be: only the newline is wrong."""
    with pytest.raises(GitError, match=f"^cat-file: {UNREADABLE}$"):
        parse_batch(f"{A} blob 3\nxyzX{B} blob 0\n\n".encode(), [A, B])


def test_a_well_formed_tree_is_read() -> None:
    body = b"40000 d\x00" + RAW_B + b"160000 s\x00" + RAW_B + b"100644 x y\x00" + RAW_B
    assert parse_tree(A, body) == {
        b"d": ("40000", B),
        b"s": ("160000", B),
        b"x y": ("100644", B),
    }
    # Names git's fsck only warns of are read, as git reads them.
    assert list(parse_tree(A, b"40000 ..\x00" + RAW_B + b"40000 .\x00" + RAW_B)) == [b"..", b"."]
    assert list(parse_tree(A, b"100644 .git\x00" + RAW_B)) == [b".git"]
    # git's order: a tree sorts as if its name ended in /, so "a.b" comes before the tree "a".
    assert list(parse_tree(A, b"100644 a.b\x00" + RAW_B + b"40000 a\x00" + RAW_B)) == [
        b"a.b",
        b"a",
    ]
    assert parse_tree(A, b"") == {}
    assert parse_tree(C, b"100755 x\x00" + bytes.fromhex(C)) == {b"x": ("100755", C)}


@pytest.mark.parametrize(
    ("field", "mode"),
    [
        (b"100664", "100644"),  # an old git's group-writable file
        (b"100744", "100755"),  # a file whose owner may run it
        (b"100600", "100644"),
        (b"100654", "100644"),  # only the owner's execute bit counts
        (b"100011", "100644"),
        (b"040000", "40000"),  # a tree's mode, padded
        (b"0160000", "160000"),
        (b"644", "160000"),  # no type: git reads anything not a file, link or tree as a gitlink
        (b"20000", "160000"),
    ],
)
def test_a_mode_is_read_as_git_reads_it(field: bytes, mode: str) -> None:
    assert parse_tree(A, field + b" x\x00" + RAW_B) == {b"x": (mode, B)}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"100644 x\x00" + RAW_B[:19], id="name-cut-short"),
        pytest.param(b"100644 x" + RAW_B, id="no-nul"),
        pytest.param(b"100644x\x00" + RAW_B, id="no-space"),
        pytest.param(b"100648 x\x00" + RAW_B, id="not-octal"),
        pytest.param(b" x\x00" + RAW_B, id="no-mode"),
        pytest.param(b"100644 b\x00" + RAW_B + b"100644 a\x00" + RAW_B, id="out-of-order"),
        pytest.param(b"40000 a\x00" + RAW_B + b"100644 a.b\x00" + RAW_B, id="a-tree-misplaced"),
        pytest.param(b"100644 \x00" + RAW_B, id="an-empty-name"),
        pytest.param(b"100644 a/b\x00" + RAW_B, id="a-slash"),
        pytest.param(b"100644 x\x00" + RAW_B + b"40000 x\x00" + RAW_B, id="a-name-twice"),
    ],
)
def test_a_tree_out_of_its_grammar_is_refused(body: bytes) -> None:
    with pytest.raises(GitError, match=f"^{A} {MALFORMED}$"):
        parse_tree(A, body)


def test_a_well_formed_commit_is_read() -> None:
    body = (
        f"tree {A}\nparent {B}\nparent {A}\nauthor a <a> 0 +0000\n"
        "gpgsig x\n parent " + B + "\n\nfeat: x\n\nparent " + B + "\n"
    ).encode()
    commit = parse_commit(A, body)
    assert (commit.tree, commit.parents) == (A, (B, A))
    assert commit.message == f"feat: x\n\nparent {B}\n"
    # No message at all.
    assert parse_commit(A, f"tree {A}\n".encode()).message == ""


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(f"parent {B}\ntree {A}\n\nx\n".encode(), id="tree-not-first"),
        pytest.param(b"\nx\n", id="no-headers"),
        pytest.param(f"{A}\n\nx\n".encode(), id="no-tree-keyword"),
        pytest.param(f"tree {A[:39]}\n\nx\n".encode(), id="a-short-tree"),
        pytest.param(f"tree {A}\nparent {B.upper()}\n\nx\n".encode(), id="an-upper-case-parent"),
        pytest.param(f"tree {C}\n\nx\n".encode(), id="a-tree-of-another-format"),
    ],
)
def test_a_commit_out_of_its_grammar_is_refused(body: bytes) -> None:
    with pytest.raises(GitError, match=f"^{A} {MALFORMED}$"):
        parse_commit(A, body)

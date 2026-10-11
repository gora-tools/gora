"""The input glob matcher (ADR-026 section 9).

Pure: a glob and a path in, an answer out. A glob means what git's ``glob`` pathspec magic means,
so ``git ls-files ':(glob)<glob>'`` lists exactly the paths ``matches`` accepts (tested against
the real git binary). git compares a pathspec literally first, so every glob matches the path it
spells and every path under it; a glob with ``*``, ``?`` or ``[`` also matches every whole path
its pattern matches. In the pattern ``*`` and ``?`` never match ``/``, a leading ``**/`` matches
zero or more leading components, ``/**/`` zero or more between two, a trailing ``/**`` one or
more below, and ``**`` alone every path. Matching is case-sensitive, and ``*`` matches a leading
dot. A path is the bytes git gives for it, relative to the repository's root and never decoded,
and matching is on those bytes, as git's is: ``?`` is one byte, a class matches one byte, so a
character outside ASCII takes as many ``?`` as its encoding has bytes. A glob is ASCII by its
grammar.

Where the source repository is the release repository, its release paths are removed from every
input set before matching (ADR-026 section 9): ``input_paths`` never returns one, whatever the
globs, so a commit that changes only release files changes no input.
"""

import functools
import re
from collections.abc import Iterable

from gora.domain.release.identifiers import INPUT_GLOB
from gora.domain.release_repository import files

WILDCARDS = frozenset("*?[")
# Any run of bytes but /, and one or more whole components.
_COMPONENTS = "(?:[^/]+/)*"
# The release paths as the components of a path in bytes, which is what a path is.
_RELEASE_COMPONENTS = tuple(
    tuple(component.encode("ascii") for component in path) for path in files.RELEASE_PATHS
)


def matches(glob: str, path: bytes) -> bool:
    """Whether ``glob`` matches ``path``; ``ValueError`` for a glob ``INPUT_GLOB`` refuses."""
    pattern = _compiled(glob)
    spelled = glob.encode("ascii")
    if path == spelled or path.startswith(spelled + b"/"):
        return True
    return pattern is not None and pattern.fullmatch(path) is not None


def is_release_path(path: bytes) -> bool:
    """Whether the components of ``path`` begin with those of a release path (section 9).

    The same relation by which ``tree`` refuses a glob whose fixed components begin with a
    release path: the release file itself, any path under a release directory, a release
    directory's own name, and a path under a release file's name.
    """
    components = tuple(path.split(b"/"))
    return any(components[: len(release)] == release for release in _RELEASE_COMPONENTS)


def input_paths(
    globs: Iterable[str], paths: Iterable[bytes], *, in_release_repository: bool
) -> frozenset[bytes]:
    """The paths some glob matches, a release path removed first in the release repository.

    Every glob is checked before any path is read, so a glob ``INPUT_GLOB`` refuses is a
    ``ValueError`` whatever the paths are, an empty iterable included.
    """
    checked = tuple(globs)
    for glob in checked:
        _check(glob)
    return frozenset(
        path
        for path in paths
        if not (in_release_repository and is_release_path(path))
        and any(matches(glob, path) for glob in checked)
    )


def fixed_components(glob: str) -> tuple[str, ...]:
    """The components before the first one with ``*``, ``?`` or ``[``."""
    _check(glob)
    fixed: list[str] = []
    for component in glob.split("/"):
        if WILDCARDS & set(component):
            break
        fixed.append(component)
    return tuple(fixed)


def _check(glob: str) -> None:
    problem = INPUT_GLOB.problem(glob)
    if problem is not None:
        raise ValueError(f"not an input glob: {problem}")


@functools.lru_cache(maxsize=1024)
def _compiled(glob: str) -> re.Pattern[bytes] | None:
    """The pattern of a glob with a wildcard, on bytes; None for a glob that is a path.

    ``re.DOTALL``: a path may hold any byte but ``/`` and NUL, a newline included.
    """
    _check(glob)
    if not WILDCARDS & set(glob):
        return None
    components = glob.split("/")
    if components == ["**"]:
        return re.compile(b".+", re.DOTALL)
    parts: list[str] = []
    last = len(components) - 1
    for index, component in enumerate(components):
        if component != "**":
            parts.append(_component(component) + ("" if index == last else "/"))
        elif index == last:
            parts.append(".+")  # a trailing /**: one or more components below
        else:
            parts.append(_COMPONENTS)  # a leading **/ or a /**/: zero or more components
    return re.compile("".join(parts).encode("ascii"), re.DOTALL)


def _component(component: str) -> str:
    """One component: ``*`` and ``?`` within it, a class as written, the rest literal."""
    out: list[str] = []
    at = 0
    while at < len(component):
        char = component[at]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")  # one byte
        elif char == "[":
            end = component.index("]", at)
            # Members are letters, digits, . and _, and ranges: none is special in a re class.
            out.append(component[at : end + 1])
            at = end
        else:
            out.append(re.escape(char))
        at += 1
    return "".join(out)

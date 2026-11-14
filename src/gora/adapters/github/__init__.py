"""GitHub tool adapter: a writer for promotion PRs, and a reader of the repository's state."""

from gora.adapters.github._http import GitHubError
from gora.adapters.github.adapter import GitHubAdapter, PullRequest
from gora.adapters.github.reader import GitHubReader, TagKind, TagObject, TagRef

__all__ = [
    "GitHubAdapter",
    "GitHubError",
    "GitHubReader",
    "PullRequest",
    "TagKind",
    "TagObject",
    "TagRef",
]

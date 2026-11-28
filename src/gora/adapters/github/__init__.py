"""GitHub tool adapter: a writer for promotion PRs, and a reader of the repository's state."""

from gora.adapters.github._http import GITHUB_TOKEN_SHAPES, GitHubError
from gora.adapters.github.adapter import GitHubAdapter, PullRequest
from gora.adapters.github.reader import GitHubReader

__all__ = [
    "GITHUB_TOKEN_SHAPES",
    "GitHubAdapter",
    "GitHubError",
    "GitHubReader",
    "PullRequest",
]

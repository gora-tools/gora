"""GitHub tool adapter (bounded operations for promotion PRs)."""

from gora.adapters.github.adapter import GitHubAdapter, GitHubError, PullRequest

__all__ = ["GitHubAdapter", "GitHubError", "PullRequest"]

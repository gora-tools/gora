"""GitHub adapter — bounded operations for promotion PRs.

Exposes only the narrow set needed to open a deterministic promotion PR: read a
ref/file sha, create a branch, commit a file, open a PR, read PR status. It does
NOT merge, and it does not proxy arbitrary API calls (ADR-002, ADR-006). Every
mutating operation is audited (ADR-006, ADR-013).
"""

import base64
from typing import Any

import httpx
from pydantic import BaseModel

from gora.audit.logger import AuditLogger

GITHUB_API = "https://api.github.com"


class PullRequest(BaseModel):
    """Minimal, typed view of a GitHub pull request."""

    number: int
    url: str
    state: str
    head: str
    base: str
    title: str


class GitHubError(RuntimeError):
    """A GitHub API call returned an error status."""


class GitHubAdapter:
    """Narrow, audited GitHub operations for the promotion-PR workflow."""

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        base_url: str = GITHUB_API,
        timeout: float = 10.0,
        audit: AuditLogger | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise ValueError("GitHub token is required")
        self._repo = repo
        self._base_url = base_url.rstrip("/")
        self._audit = audit or AuditLogger()
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    # --- read-only ---

    def get_ref_sha(self, branch: str) -> str:
        """Return the commit sha a branch points at."""
        data = self._request("GET", f"/repos/{self._repo}/git/ref/heads/{branch}")
        return str(data["object"]["sha"])

    def get_file_sha(self, path: str, branch: str) -> str | None:
        """Return the blob sha of a file on a branch, or None if it does not exist."""
        resp = self._raw("GET", f"/repos/{self._repo}/contents/{path}", params={"ref": branch})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise GitHubError(f"GET contents/{path} -> {resp.status_code}: {resp.text}")
        return str(resp.json()["sha"])

    def get_pull_request_status(self, number: int) -> PullRequest:
        """Read the current state of a pull request."""
        data = self._request("GET", f"/repos/{self._repo}/pulls/{number}")
        return self._to_pr(data)

    def find_open_pull_request(self, head: str) -> PullRequest | None:
        """Return the open PR whose head is ``head``, or None (for idempotency)."""
        owner = self._repo.split("/", 1)[0]
        resp = self._raw(
            "GET",
            f"/repos/{self._repo}/pulls",
            params={"head": f"{owner}:{head}", "state": "open"},
        )
        if resp.status_code >= 400:
            raise GitHubError(f"GET pulls -> {resp.status_code}: {resp.text}")
        items = resp.json()
        if not items:
            return None
        return self._to_pr(items[0])

    # --- mutations (audited) ---

    def create_branch(self, name: str, from_sha: str) -> None:
        """Create a branch at a commit sha."""
        self._request(
            "POST",
            f"/repos/{self._repo}/git/refs",
            {"ref": f"refs/heads/{name}", "sha": from_sha},
        )
        self._audit.record("github.create_branch", repo=self._repo, branch=name, sha=from_sha)

    def commit_file(
        self,
        path: str,
        content: str,
        branch: str,
        message: str,
        *,
        sha: str | None = None,
    ) -> str:
        """Create or update a file on a branch; returns the commit sha."""
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        data = self._request("PUT", f"/repos/{self._repo}/contents/{path}", body)
        commit_sha = str(data["commit"]["sha"])
        self._audit.record(
            "github.commit_file",
            repo=self._repo,
            path=path,
            branch=branch,
            commit=commit_sha,
        )
        return commit_sha

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
        """Open a pull request. GORA opens; it never merges (ADR-013)."""
        data = self._request(
            "POST",
            f"/repos/{self._repo}/pulls",
            {"head": head, "base": base, "title": title, "body": body},
        )
        pr = self._to_pr(data)
        self._audit.record(
            "github.create_pull_request",
            repo=self._repo,
            number=pr.number,
            head=head,
            base=base,
        )
        return pr

    # --- helpers ---

    def _to_pr(self, data: dict[str, Any]) -> PullRequest:
        return PullRequest(
            number=int(data["number"]),
            url=str(data.get("html_url", "")),
            state=str(data.get("state", "")),
            head=str(data.get("head", {}).get("ref", "")),
            base=str(data.get("base", {}).get("ref", "")),
            title=str(data.get("title", "")),
        )

    def _raw(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._client.request(method, f"{self._base_url}{path}", json=json, params=params)

    def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        resp = self._raw(method, path, json=json)
        if resp.status_code >= 400:
            raise GitHubError(f"{method} {path} -> {resp.status_code}: {resp.text}")
        data: dict[str, Any] = resp.json()
        return data

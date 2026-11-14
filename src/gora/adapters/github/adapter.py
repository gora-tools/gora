"""GitHub adapter — bounded operations for promotion PRs.

Exposes only the narrow set needed to open a deterministic promotion PR: read a file sha,
create a branch, commit a file, open a PR, read PR status. It does NOT merge, and it does not
proxy arbitrary API calls (ADR-002, ADR-006). Every mutating operation is audited (ADR-006,
ADR-013). It is the writer, GORA's App (L22); the repository's state, the head of a branch and
its tags, is read by ``GitHubReader``, which has no write.

Both ride on one HTTP core (``_http``), which holds the bounds of every request: a deadline, a
cap on the body, no redirect followed, no retry. The boundary is the adapter's: whatever goes
wrong in a call, a transport error, a URL httpx refuses or a response that is not the expected
JSON, leaves it as ``GitHubError``, whose message names the operation and holds no token and no
query string. A value placed in a URL path is percent-encoded, except ``/`` in a ref name and
in a content path, which GitHub reads literally. Construction refuses a caller's error with
``ValueError``: a repository the repository grammar refuses (ADR-003), or an empty token. A call
raises only ``GitHubError``.
"""

import base64
from typing import Any

import httpx
from pydantic import BaseModel

from gora.adapters.github._http import (
    GITHUB_API,
    HttpCore,
    checked_repository,
    read_field,
    segment,
)
from gora.audit.logger import AuditLogger


class PullRequest(BaseModel):
    """Minimal, typed view of a GitHub pull request."""

    number: int
    url: str
    state: str
    head: str
    base: str
    title: str


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
        self._repo = checked_repository(repo)
        self._core = HttpCore(token, base_url=base_url, timeout=timeout, client=client)
        self._audit = audit or AuditLogger()

    # --- read-only ---

    def get_file_sha(self, path: str, branch: str) -> str | None:
        """Return the blob sha of a file on a branch, or None if it does not exist."""
        url = f"/repos/{self._repo}/contents/{segment(path)}"
        answer = self._core.send("GET", url, params={"ref": branch}, missing_ok=True)
        if answer.status == 404:
            return None
        return read_field(f"GET {url}", lambda: str(answer.json()["sha"]))

    def get_pull_request_status(self, number: int) -> PullRequest:
        """Read the current state of a pull request."""
        path = f"/repos/{self._repo}/pulls/{int(number)}"
        data = self._core.json_object("GET", path)
        return read_field(f"GET {path}", lambda: self._to_pr(data))

    def find_open_pull_request(self, head: str) -> PullRequest | None:
        """Return the open PR whose head is ``head``, or None (for idempotency)."""
        owner = self._repo.split("/", 1)[0]
        path = f"/repos/{self._repo}/pulls"
        answer = self._core.send("GET", path, params={"head": f"{owner}:{head}", "state": "open"})
        items = read_field(f"GET {path}", answer.json)
        if not items:
            return None
        return read_field(f"GET {path}", lambda: self._to_pr(items[0]))

    # --- mutations (audited) ---

    def create_branch(self, name: str, from_sha: str) -> None:
        """Create a branch at a commit sha."""
        self._core.json_object(
            "POST",
            f"/repos/{self._repo}/git/refs",
            json={"ref": f"refs/heads/{name}", "sha": from_sha},
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
        url = f"/repos/{self._repo}/contents/{segment(path)}"
        data = self._core.json_object("PUT", url, json=body)
        commit_sha = read_field(f"PUT {url}", lambda: str(data["commit"]["sha"]))
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
        path = f"/repos/{self._repo}/pulls"
        data = self._core.json_object(
            "POST", path, json={"head": head, "base": base, "title": title, "body": body}
        )
        pr = read_field(f"POST {path}", lambda: self._to_pr(data))
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

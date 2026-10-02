"""GitHub adapter — bounded operations for promotion PRs.

Exposes only the narrow set needed to open a deterministic promotion PR: read a
ref/file sha, create a branch, commit a file, open a PR, read PR status. It does
NOT merge, and it does not proxy arbitrary API calls (ADR-002, ADR-006). Every
mutating operation is audited (ADR-006, ADR-013).

The boundary is the adapter's: whatever goes wrong in a call, a transport error, a URL
httpx refuses or a response that is not the expected JSON, leaves it as ``GitHubError``,
whose message names the operation and holds no token and no query string. A value placed
in a URL path is percent-encoded, except ``/`` in a ref name and in a content path, which
GitHub reads literally. The repository is not: ``owner/name`` is checked by the CLI's
grammar before an adapter exists (ADR-003).
"""

import base64
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

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
    """A GitHub API call failed: an error status, a transport error, or a bad response."""


def _segment(value: str) -> str:
    """A value placed in a URL path, ``/`` kept: GitHub reads it in refs and content paths."""
    return quote(value, safe="/")


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
        path = f"/repos/{self._repo}/git/ref/heads/{_segment(branch)}"
        data = self._request("GET", path)
        return _field(f"GET {path}", lambda: str(data["object"]["sha"]))

    def get_file_sha(self, path: str, branch: str) -> str | None:
        """Return the blob sha of a file on a branch, or None if it does not exist."""
        url = f"/repos/{self._repo}/contents/{_segment(path)}"
        resp = self._raw("GET", url, params={"ref": branch})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise GitHubError(f"GET contents/{_segment(path)} -> {resp.status_code}: {resp.text}")
        return _field(f"GET {url}", lambda: str(resp.json()["sha"]))

    def get_pull_request_status(self, number: int) -> PullRequest:
        """Read the current state of a pull request."""
        path = f"/repos/{self._repo}/pulls/{int(number)}"
        data = self._request("GET", path)
        return _field(f"GET {path}", lambda: self._to_pr(data))

    def find_open_pull_request(self, head: str) -> PullRequest | None:
        """Return the open PR whose head is ``head``, or None (for idempotency)."""
        owner = self._repo.split("/", 1)[0]
        path = f"/repos/{self._repo}/pulls"
        resp = self._raw("GET", path, params={"head": f"{owner}:{head}", "state": "open"})
        if resp.status_code >= 400:
            raise GitHubError(f"GET pulls -> {resp.status_code}: {resp.text}")
        items = _field(f"GET {path}", resp.json)
        if not items:
            return None
        return _field(f"GET {path}", lambda: self._to_pr(items[0]))

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
        url = f"/repos/{self._repo}/contents/{_segment(path)}"
        data = self._request("PUT", url, body)
        commit_sha = _field(f"PUT {url}", lambda: str(data["commit"]["sha"]))
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
        data = self._request(
            "POST", path, {"head": head, "base": base, "title": title, "body": body}
        )
        pr = _field(f"POST {path}", lambda: self._to_pr(data))
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
        """One request. Anything httpx raises leaves as GitHubError naming ``method path``.

        The message is built from the operation and the exception's type alone. Nothing
        httpx wrote goes into it, so neither the URL with its query nor a header with the
        token can; the original stays chained as ``__cause__`` for a debugger.
        """
        operation = f"{method} {path}"
        try:
            return self._client.request(method, f"{self._base_url}{path}", json=json, params=params)
        except httpx.InvalidURL as exc:
            raise GitHubError(f"{operation} failed: invalid URL") from exc
        except httpx.HTTPError as exc:
            raise GitHubError(f"{operation} failed: {type(exc).__name__}") from exc

    def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        resp = self._raw(method, path, json=json)
        if resp.status_code >= 400:
            raise GitHubError(f"{method} {path} -> {resp.status_code}: {resp.text}")
        data = _field(f"{method} {path}", resp.json)
        if not isinstance(data, dict):
            raise GitHubError(f"{method} {path} failed: unexpected response")
        return data


def _field[T](operation: str, read: Callable[[], T]) -> T:
    """Read a value out of a response; a body that is not the expected JSON is a GitHubError."""
    try:
        return read()
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise GitHubError(f"{operation} failed: unexpected response") from exc

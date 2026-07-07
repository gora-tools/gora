"""Tests for the promotion-PR service (fake adapter — no network)."""

from pathlib import Path

import pytest
import yaml

from gora.adapters.github import GitHubError, PullRequest
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import ReleaseManifest
from gora.services.promotion_pr import PromotionPRService

DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
IMAGE = "ghcr.io/test/service"


def _seed(path: Path, *, with_candidate: bool = True) -> None:
    candidate = (
        ReleaseCandidate(version="1.2.0-rc.1", image=IMAGE, digest=DIGEST)
        if with_candidate
        else None
    )
    ReleaseManifest(release="1.2.0", candidate=candidate).to_yaml(path)


class FakeAdapter:
    def __init__(
        self, *, branch_exists: bool = False, existing_pr: PullRequest | None = None
    ) -> None:
        self.calls: list[str] = []
        self._branch_exists = branch_exists
        self._existing_pr = existing_pr
        self.committed_content: str | None = None

    def get_ref_sha(self, branch: str) -> str:
        self.calls.append("get_ref_sha")
        if branch.startswith("gora/promote-") and not self._branch_exists:
            raise GitHubError("404 Not Found")
        return "basesha"

    def create_branch(self, name: str, from_sha: str) -> None:
        self.calls.append("create_branch")

    def get_file_sha(self, path: str, branch: str) -> str | None:
        self.calls.append("get_file_sha")
        return "filesha"

    def commit_file(
        self, path: str, content: str, branch: str, message: str, *, sha: str | None = None
    ) -> str:
        self.calls.append("commit_file")
        self.committed_content = content
        return "commitsha"

    def find_open_pull_request(self, head: str) -> PullRequest | None:
        self.calls.append("find_open_pull_request")
        return self._existing_pr

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
        self.calls.append("create_pull_request")
        return PullRequest(number=42, url="url", state="open", head=head, base=base, title=title)


def test_open_promotion_pr_happy_path(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    fake = FakeAdapter()

    result = PromotionPRService(fake, manifest_path=path).open_promotion_pr("dev")

    assert result.created is True
    assert result.pull_request.number == 42
    assert result.branch == "gora/promote-dev-1.2.0-rc.1"
    assert {"create_branch", "commit_file", "create_pull_request"} <= set(fake.calls)

    # The committed manifest is the PROMOTED one: dev stage pinned to the digest.
    committed = yaml.safe_load(fake.committed_content)
    assert committed["stages"]["dev"]["digest"] == DIGEST
    assert committed["stages"]["dev"]["version"] == "1.2.0-rc.1"


def test_idempotent_reuses_existing_pr(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    existing = PullRequest(
        number=9, url="u", state="open", head="gora/promote-dev-1.2.0-rc.1", base="main", title="t"
    )
    fake = FakeAdapter(existing_pr=existing)

    result = PromotionPRService(fake, manifest_path=path).open_promotion_pr("dev")

    assert result.created is False
    assert result.pull_request.number == 9
    assert "create_pull_request" not in fake.calls


def test_reuses_existing_branch(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    fake = FakeAdapter(branch_exists=True)

    PromotionPRService(fake, manifest_path=path).open_promotion_pr("dev")

    assert "create_branch" not in fake.calls


def test_no_candidate_raises(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path, with_candidate=False)

    with pytest.raises(ValueError, match="active release candidate"):
        PromotionPRService(FakeAdapter(), manifest_path=path).open_promotion_pr("dev")

"""Tests for the promotion-PR service (fake adapter — no network)."""

from pathlib import Path

import pytest
import yaml

from gora.adapters.github import GitHubError, PullRequest
from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import ContractViolationError, ReleaseManifest
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


# --- idempotency: the key is (stage, version), and it fixes the digest (ADR-013) ------------


class StatefulGitHub:
    """A repository that remembers the branches, files and pull requests made on it."""

    def __init__(self) -> None:
        self.branches: dict[str, str] = {"main": "basesha"}
        self.files: dict[tuple[str, str], str] = {}
        self.pulls: list[PullRequest] = []
        self.calls: list[str] = []

    def get_ref_sha(self, branch: str) -> str:
        if branch not in self.branches:
            raise GitHubError("404 Not Found")
        return self.branches[branch]

    def create_branch(self, name: str, from_sha: str) -> None:
        self.calls.append(f"create_branch {name}")
        self.branches[name] = from_sha

    def get_file_sha(self, path: str, branch: str) -> str | None:
        return "blob" if (path, branch) in self.files else None

    def commit_file(
        self, path: str, content: str, branch: str, message: str, *, sha: str | None = None
    ) -> str:
        self.calls.append(f"commit_file {branch}")
        self.files[(path, branch)] = content
        return "commitsha"

    def find_open_pull_request(self, head: str) -> PullRequest | None:
        return next((pr for pr in self.pulls if pr.head == head and pr.state == "open"), None)

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
        self.calls.append(f"create_pull_request {head}")
        pr = PullRequest(
            number=len(self.pulls) + 1, url="u", state="open", head=head, base=base, title=title
        )
        self.pulls.append(pr)
        return pr


def test_a_second_open_for_the_same_stage_reuses_the_pull_request(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    github = StatefulGitHub()
    service = PromotionPRService(github, manifest_path=path)

    first = service.open_promotion_pr("dev")
    second = service.open_promotion_pr("dev")

    assert (first.created, second.created) == (True, False)
    assert second.pull_request.number == first.pull_request.number
    assert len(github.pulls) == 1
    assert second.branch == first.branch == "gora/promote-dev-1.2.0-rc.1"
    assert (
        (second.version, second.digest) == (first.version, first.digest) == ("1.2.0-rc.1", DIGEST)
    )
    assert [c for c in github.calls if c.startswith("create")] == [
        "create_branch gora/promote-dev-1.2.0-rc.1",
        "create_pull_request gora/promote-dev-1.2.0-rc.1",
    ]
    committed = yaml.safe_load(github.files[("release-manifest.yaml", first.branch)])
    assert committed["stages"]["dev"] == {"version": "1.2.0-rc.1", "digest": DIGEST}


def test_another_stage_is_another_pull_request(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    github = StatefulGitHub()
    service = PromotionPRService(github, manifest_path=path)

    dev = service.open_promotion_pr("dev")
    staging = service.open_promotion_pr("staging")

    assert dev.branch != staging.branch
    assert [pr.number for pr in github.pulls] == [1, 2]


def test_the_branch_key_cannot_name_two_digests(tmp_path: Path) -> None:
    """Under ADR-003 a version maps to one digest, so (stage, version) fixes (stage, digest).

    The re-cut that would give one branch name two digests is refused before any PR exists.
    """
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    manifest = ReleaseManifest.from_yaml(path)
    other = "sha256:" + "f" * 64

    with pytest.raises(ContractViolationError):
        manifest.cut_candidate(ReleaseCandidate(version="1.2.0-rc.1", image=IMAGE, digest=other))


def test_the_result_names_the_image(tmp_path: Path) -> None:
    path = tmp_path / "release-manifest.yaml"
    _seed(path)
    result = PromotionPRService(StatefulGitHub(), manifest_path=path).open_promotion_pr("dev")
    assert result.image == IMAGE

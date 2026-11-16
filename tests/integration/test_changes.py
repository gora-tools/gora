"""``gora changes``, for a project whose code and release files share one repository (S == R):
the service and the command, on repositories built with git's plumbing and on GORA's own history
(release-model section 11; ADR-023 section 3, ADR-026 section 9).

The code host is a fake that answers from a table and records what it was asked; the clone is a
real one, read by the real git. CLI tests replace ``code_host`` in the CLI module (TESTING rule
6), except where the refusal of ``code_host`` itself is the thing under test: it refuses before
any request, and the hermetic guard fails a test that reaches the network.

A project is the release repository ``github.com/acme/mono``. Its history, the one most tests
share: ``c1`` the policy and a README; ``c2`` declares the artifact ``api`` (inputs ``src/**``);
``c3`` adds ``src/main.py``; ``c4`` changes the policy only; ``c5`` adds ``src/extra.py``; ``c6``
changes the README only; ``c7`` changes ``src/main.py``.
"""

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

import gora.transport.cli.commands as commands
from gora.adapters.git import GitError
from gora.adapters.git import adapter as git_adapter
from gora.api.schemas import schema
from gora.config.settings import Settings, get_settings
from gora.domain.release.candidates import Candidate, Commit, RefusedError
from gora.domain.release.tags import NO_TRAILER, Tag, rc_tag_message
from gora.domain.release.versions import Version
from gora.services import changes as service
from gora.services.changes import SEPARATE_SHAPE, Changes
from gora.services.code_host import CodeHost
from gora.transport.cli.commands import app
from tests.integration.test_git_adapter import GIT, REAL_HEAD, REAL_HISTORY, Repo, text
from tests.integration.test_identifiers_against_git import hermetic_git_env

# A policy that loads, the example of the release files' contract: this module also runs in the
# release image's test stage, which holds ``tests/`` and no ``contracts/``.
POLICY = b"""\
apiVersion: gora.tools/v1alpha1
kind: Policy
spec:
  stages:
    - name: dev
      gate: auto
      verify: [deploy, integration]
    - name: qa
      gate: approval
      approvers: { team/qa: 1 }
      verify: [deploy, e2e]
    - name: staging
      gate: approval
      approvers: { team/qa: 1 }
      verify: [deploy, smoke]
    - name: prod
      gate: approval
      approvers: { team/release-owners: 1 }
      verify: [deploy, smoke]
  reporters:
    deploy: app/argocd-notifications
    integration: app/cally-ci
    e2e: app/e2e-runner
    contract: app/cally-ci
    smoke: app/cally-ci
  builders:
    cally-ci:
      issuer: https://token.actions.githubusercontent.com
      identity: https://github.com/acme/cally-ci/.github/workflows/build.yml@refs/heads/main
  attesters:
    gora-pipeline:
      publicKey: |
        -----BEGIN PUBLIC KEY-----
        MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAELGtggmOKP9/RPvCPL885nKEbcAIr
        83jV0RAw6Ff2l8xqVoRxHu7StO9I8LU8kSEzbLU6D9loC+Ns3jwfcAvabg==
        -----END PUBLIC KEY-----
  requests:
    promote:  { dev: [team/developers], qa: [team/qa], staging: [team/qa],
                prod: [team/release-owners] }
    rollback: { dev: [team/developers, team/release-owners],
                qa: [team/qa, team/release-owners],
                staging: [team/qa, team/release-owners], prod: [team/release-owners] }
    reject:   [team/qa, team/release-owners]
    migrate:  [team/release-owners]
  evidence:
    sbom: { format: cyclonedx, minVersion: "1.6" }
  branchAgeDays: 30
"""

PROJECT = "github.com/acme/mono"
SENTINEL = "fake-sentinel-github-token-changes-7d1b"
DIGEST = "sha256:" + "ab" * 32
POLICY_DIGEST = "sha256:" + "cd" * 32
SKIPPED = "0" * 40

runner = CliRunner()


# --- a project's history --------------------------------------------------------------------


def declaration(
    name: str,
    *,
    prefix: str = "",
    inputs: Sequence[str] = ("src/**",),
    repository: str = PROJECT,
    initial: str = "0.1.0-rc",
) -> bytes:
    listed = ", ".join(json.dumps(glob) for glob in inputs)
    return (
        "apiVersion: gora.tools/v1alpha1\n"
        "kind: Artifact\n"
        "metadata:\n"
        f"  name: {name}\n"
        "spec:\n"
        f"  image: ghcr.io/acme/{name}\n"
        "  source:\n"
        f"    repository: {repository}\n"
        f'    tagPrefix: "{prefix}"\n'
        f"    inputs: [{listed}]\n"
        "  builders: [cally-ci]\n"
        f"  initialVersion: {initial}\n"
        "  stable: false\n"
    ).encode()


class Timeline:
    """A first-parent history built commit by commit, each a full snapshot of its files."""

    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.files: dict[bytes, tuple[str, bytes]] = {}
        self.chain: list[str] = []

    def commit(
        self, message: str, put: Mapping[str, bytes] | None = None, drop: Sequence[str] = ()
    ) -> str:
        for path in drop:
            del self.files[path.encode()]
        for path, data in (put or {}).items():
            self.files[path.encode()] = ("100644", data)
        sha = self.repo.commit(dict(self.files), f"{message}\n".encode(), *self.chain[-1:])
        self.chain.append(sha)
        self.repo.branch(sha)
        return sha


@dataclass
class History:
    """The shared history: ``c[n]`` is the commit ``cn``."""

    timeline: Timeline
    c: dict[int, str] = field(default_factory=dict)

    @property
    def repo(self) -> Repo:
        return self.timeline.repo

    @property
    def root(self) -> Path:
        return self.timeline.repo.root


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return hermetic_git_env(tmp_path / "home")


@pytest.fixture
def history(tmp_path: Path, env: dict[str, str]) -> History:
    line = Timeline(Repo(tmp_path / "mono", env))
    found = History(line)
    found.c[1] = line.commit(
        "chore: start", {"release-policy.yaml": POLICY, "README.md": b"readme"}
    )
    found.c[2] = line.commit(
        "feat: declare api", {"artifacts/api/artifact.yaml": declaration("api")}
    )
    found.c[3] = line.commit("feat: the first source", {"src/main.py": b"main 1"})
    found.c[4] = line.commit(
        "chore: tune the policy",
        {"release-policy.yaml": POLICY.replace(b"branchAgeDays: 30", b"branchAgeDays: 31")},
    )
    found.c[5] = line.commit("feat: more source", {"src/extra.py": b"extra"})
    found.c[6] = line.commit("docs: the readme", {"README.md": b"readme 2"})
    found.c[7] = line.commit("fix: the first source", {"src/main.py": b"main 2"})
    return found


def annotated(name: str, commit: str, version: str, *, artifact: str = "api") -> Tag:
    """The annotated tag GORA writes for the candidate of ``commit``."""
    changed = Commit(commit, "feat: a change", frozenset({b"src/main.py"}))
    message = rc_tag_message(
        Candidate(changed, Version.parse(version), (changed,)),
        artifact=artifact,
        image=f"ghcr.io/acme/{artifact}",
        digest=DIGEST,
        policy_digest=POLICY_DIGEST,
    )
    return Tag(name, commit, message)


class FakeHost:
    """A code host that answers from a table and records every question."""

    def __init__(self, head: str, tags: Sequence[Tag] = ()) -> None:
        self.head = head
        self.tags = {tag.name: tag for tag in tags}
        self.names: list[str] = []  # names the host matched that are not version tags
        self.calls: list[str] = []

    def repository_id(self) -> str:
        self.calls.append("repository_id")
        return "1"

    def branch_head(self, branch: str) -> str:
        self.calls.append(f"branch_head {branch}")
        assert branch == "main"
        return self.head

    def tag_names(self, tag_prefix: str) -> tuple[str, ...]:
        self.calls.append(f"tag_names {tag_prefix}")
        names = [*self.tags, *self.names]
        return tuple(name for name in names if name.startswith(f"{tag_prefix}v"))

    def tag(self, name: str) -> Tag:
        self.calls.append(f"tag {name}")
        return self.tags[name]


host_port: CodeHost = FakeHost("0" * 40)  # the fake serves the port: mypy checks it here


# --- running the command ---------------------------------------------------------------------


@dataclass
class Run:
    code: int
    out: str
    err: str

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


class Gora:
    """``gora`` run in a directory, with ``code_host`` replaced by a fake or left as it is."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.asked: list[str] = []  # the repositories ``code_host`` was asked to read
        monkeypatch.setenv("GORA_GIT", GIT)
        monkeypatch.setenv("GORA_GITHUB_TOKEN", SENTINEL)
        monkeypatch.setenv("COLUMNS", "1000")  # Typer's error box does not wrap a message

    def __call__(
        self,
        *args: str,
        host: CodeHost | None,
        cwd: Path,
        project: str | None = PROJECT,
    ) -> Run:
        def fake(repository: str, settings: Settings, **_: object) -> CodeHost:
            self.asked.append(repository)
            assert host is not None
            return host

        if host is not None:
            self.monkeypatch.setattr(commands, "code_host", fake)
        return self.real(*args, cwd=cwd, project=project)

    def real(self, *args: str, cwd: Path, project: str | None = PROJECT) -> Run:
        self.monkeypatch.chdir(cwd)
        get_settings.cache_clear()
        flag = ["-p", project] if project is not None else []
        result = runner.invoke(app, [*flag, "changes", *args])
        run = Run(result.exit_code, result.stdout, result.stderr)
        # The token is in no output and no error (TESTING: threat 15).
        assert SENTINEL not in run.out + run.err + repr(result.exception)
        return run


@pytest.fixture
def gora(monkeypatch: pytest.MonkeyPatch) -> Gora:
    return Gora(monkeypatch)


@pytest.fixture
def elsewhere(tmp_path: Path) -> Path:
    """A directory that is no clone: a command that opened one would fail here."""
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    return nowhere


# --- 1. the project ---------------------------------------------------------------------------


def test_the_flag_wins_over_the_setting(
    gora: Gora, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GORA_PROJECT", "github.com/other/elsewhere")
    host = FakeHost(history.c[7])

    run = gora("--output", "json", host=host, cwd=history.root)

    assert run.code == 0, run.err
    assert gora.asked == [PROJECT]
    assert json.loads(run.out)["project"] == PROJECT


def test_the_setting_is_the_project_when_there_is_no_flag(
    gora: Gora, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GORA_PROJECT", PROJECT)
    host = FakeHost(history.c[7])

    run = gora("--output", "json", host=host, cwd=history.root, project=None)

    assert run.code == 0, run.err
    assert gora.asked == [PROJECT]
    assert json.loads(run.out)["project"] == PROJECT


def test_no_project_is_a_usage_error_that_names_both_ways_to_give_one(
    gora: Gora, elsewhere: Path
) -> None:
    host = FakeHost("0" * 40)

    run = gora(host=host, cwd=elsewhere, project=None)

    assert run.code == 2
    assert "-p/--project" in run.err and "GORA_PROJECT" in run.err
    assert run.out == ""
    assert gora.asked == [] and host.calls == []


@pytest.mark.parametrize(
    ("project", "token"),
    [
        pytest.param("github.com/Acme/mono", SENTINEL, id="upper-case"),
        pytest.param("github.com/acme/mono.git", SENTINEL, id="dot-git"),
        pytest.param("github.com/acme", SENTINEL, id="no-name"),
        pytest.param("acme/mono", SENTINEL, id="no-host"),
        pytest.param("/acme/mono", SENTINEL, id="empty-host"),
        pytest.param("gitlab.com/acme/mono", SENTINEL, id="a-host-with-no-entry"),
        pytest.param(PROJECT, "", id="an-empty-token"),
    ],
)
def test_a_project_code_host_refuses_is_a_usage_error_with_its_message(
    gora: Gora, elsewhere: Path, monkeypatch: pytest.MonkeyPatch, project: str, token: str
) -> None:
    from gora.adapters.code_hosts import code_host

    monkeypatch.setenv("GORA_GITHUB_TOKEN", token)
    with pytest.raises(ValueError) as refused:
        code_host(project, Settings())  # the refusal itself, as the command must pass it on

    run = gora.real(cwd=elsewhere, project=project)

    # Exit 2, not the exit 1 a clone that is not one would give: nothing is opened first.
    assert run.code == 2
    assert str(refused.value) in run.err
    assert run.out == ""


# --- 2. a source other than the project ----------------------------------------------------


@pytest.mark.parametrize(
    "source", ["github.com/acme/other", "github.com/Acme/Mono", "not-a-repository", ""]
)
def test_a_source_other_than_the_project_is_refused_before_anything_is_read(
    gora: Gora, elsewhere: Path, source: str
) -> None:
    host = FakeHost("0" * 40)

    run = gora("--source", source, host=host, cwd=elsewhere)

    assert run.code == 1
    assert SEPARATE_SHAPE in run.err
    if source:  # the message names the condition, no value
        assert source not in run.err.replace(SEPARATE_SHAPE, "")
    assert run.out == ""
    assert host.calls == []  # no request, and in a directory that is no clone: none opened


def test_the_source_is_the_project_by_default_and_may_be_given_as_it(
    gora: Gora, history: History
) -> None:
    host = FakeHost(history.c[3])
    default = gora("--output", "json", host=host, cwd=history.root)
    given = gora("--source", PROJECT, "--output", "json", host=host, cwd=history.root)
    assert default.code == given.code == 0, default.err + given.err
    assert default.out == given.out
    assert json.loads(default.out)["source"] == PROJECT


def test_the_service_refuses_a_source_that_is_not_the_project_first(tmp_path: Path) -> None:
    host = FakeHost("0" * 40)
    with pytest.raises(service.ChangesError, match=re.escape(SEPARATE_SHAPE)):
        service.changes(
            project=PROJECT,
            source="github.com/acme/other",
            commit=None,
            host=host,
            root=tmp_path / "not-a-clone",
            git=GIT,
        )
    assert host.calls == []


# --- 3. the COMMIT ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "commit",
    [
        pytest.param("abc1234", id="abbreviated"),
        pytest.param("A" * 40, id="upper-case-hex"),
        pytest.param("a" * 41, id="41-digits"),
        pytest.param("a" * 63, id="63-digits"),
        pytest.param("g" * 40, id="not-hex"),
        pytest.param("main", id="a-branch"),
        pytest.param("", id="empty"),
    ],
)
def test_a_commit_that_is_not_a_full_object_name_is_a_usage_error(
    gora: Gora, elsewhere: Path, commit: str
) -> None:
    host = FakeHost("0" * 40)

    run = gora(commit, host=host, cwd=elsewhere)

    assert run.code == 2
    assert "COMMIT must be a git object name" in run.err
    if commit:
        assert commit not in run.err
    assert gora.asked == [] and host.calls == []  # no request, no clone: the grammar comes first


def test_the_project_is_checked_before_the_commit(gora: Gora, elsewhere: Path) -> None:
    run = gora("abc", host=FakeHost("0" * 40), cwd=elsewhere, project=None)
    assert run.code == 2 and "GORA_PROJECT" in run.err and "COMMIT must be" not in run.err


# --- 4. a commit that is not on the first-parent chain of main ---------------------------------

L1 = "L1: the commit is not a first-parent commit of main"


def test_a_commit_off_the_first_parent_chain_of_the_head_is_refused_by_l1(
    gora: Gora, history: History
) -> None:
    repo, files, c = history.repo, dict(history.timeline.files), history.c
    unmerged = repo.commit({**files, b"src/side.py": text("s")}, b"feat: a branch\n", c[5])
    merged = repo.commit({**files, b"src/side2.py": text("s")}, b"feat: a merged branch\n", c[6])
    head = repo.commit({**files, b"src/side2.py": text("s")}, b"Merge side2\n", c[7], merged)
    host = FakeHost(head)

    for commit in (unmerged, merged):  # a branch's commit, and one the second parent merged
        run = gora(commit, host=host, cwd=history.root)
        assert (run.code, run.err.splitlines(), run.out) == (1, [L1], "")
    assert gora(c[7], host=host, cwd=history.root).code == 0  # calibration: the first parent


def test_a_commit_on_main_before_the_newest_registered_candidate_is_not_listed(
    gora: Gora, history: History
) -> None:
    c = history.c
    host = FakeHost(
        c[7], [annotated("v0.1.0-rc", c[3], "0.1.0-rc"), annotated("v0.2.0-rc", c[5], "0.2.0-rc")]
    )

    for commit in (c[1], c[2], c[3], c[4]):
        run = gora(commit, host=host, cwd=history.root)
        assert (run.code, run.out, run.err) == (0, "", ""), commit


# --- 5. the clone ---------------------------------------------------------------------------


def test_a_head_the_clone_lacks_is_refused_with_the_adapter_s_message(
    gora: Gora, history: History
) -> None:
    run = gora(host=FakeHost("f" * 40), cwd=history.root)
    assert (run.code, run.err.splitlines(), run.out) == (
        1,
        [f"{'f' * 40} {git_adapter.MISSING}"],
        "",
    )


def test_a_shallow_clone_is_refused_and_nothing_is_fetched(
    gora: Gora, history: History, tmp_path: Path
) -> None:
    target = tmp_path / "shallow"
    history.repo.git("clone", "-q", "--depth", "1", f"file://{history.root}", str(target))
    probe = [GIT, "cat-file", "-e", history.c[3]]
    assert subprocess.run(probe, cwd=target, env=history.repo.env, check=False).returncode != 0

    run = gora(host=FakeHost(history.c[7]), cwd=target)

    assert (run.code, run.err.splitlines()) == (
        1,
        [f"{os.path.realpath(target)} {git_adapter.SHALLOW}"],
    )
    assert subprocess.run(probe, cwd=target, env=history.repo.env, check=False).returncode != 0


def test_a_directory_that_is_no_clone_is_refused_and_the_host_is_not_asked(
    gora: Gora, elsewhere: Path
) -> None:
    host = FakeHost("0" * 40)
    run = gora(host=host, cwd=elsewhere)
    assert (run.code, run.err.splitlines()) == (
        1,
        [f"{os.path.realpath(elsewhere)} {git_adapter.NOT_A_REPOSITORY}"],
    )
    assert host.calls == []  # the clone is opened before the head is asked


def test_the_clone_is_the_working_directory_and_is_never_searched_for_upward(
    gora: Gora, history: History
) -> None:
    inside = history.root / "src"
    inside.mkdir()
    run = gora(host=FakeHost(history.c[7]), cwd=inside)
    assert (run.code, run.err.splitlines()) == (
        1,
        [f"{os.path.realpath(inside)} {git_adapter.NOT_A_REPOSITORY}"],
    )


# --- 6. an invalid release tree --------------------------------------------------------------


def test_an_invalid_release_tree_at_the_head_is_one_problem_to_a_line(
    gora: Gora, history: History
) -> None:
    broken = history.timeline.commit(
        "fix: break two declarations",
        {
            "artifacts/api/artifact.yaml": declaration("api").replace(b"  stable: false\n", b""),
            "artifacts/web/artifact.yaml": declaration("web", prefix="web/") + b"extra: 1\n",
        },
    )

    run = gora(host=FakeHost(broken), cwd=history.root)

    assert run.code == 1 and run.out == ""
    lines = run.err.splitlines()
    assert len(lines) == 2, run.err  # one problem in each of two files, each on its own line
    assert lines[0].startswith("artifacts/api/artifact.yaml")
    assert lines[1].startswith("artifacts/web/artifact.yaml")
    assert "\\x0a" not in run.err and "\\n" not in run.err


# --- 7. the newest rc tag cannot be used ----------------------------------------------------


def _older(history: History) -> Tag:
    return annotated("v0.1.0-rc", history.c[3], "0.1.0-rc")


def test_the_older_tag_alone_lists_the_artifact(gora: Gora, history: History) -> None:
    """Calibration for the refusals below: were an older tag used in place of the newest, the
    answer would be this one."""
    run = gora(host=FakeHost(history.c[7], [_older(history)]), cwd=history.root)
    assert (run.code, run.lines) == (0, ["api 0.2.0-rc"])


@pytest.mark.parametrize("case", ["lightweight", "no-record", "off-main"])
def test_a_newest_rc_tag_that_cannot_be_used_is_refused_and_no_older_tag_takes_its_place(
    gora: Gora, history: History, case: str
) -> None:
    c = history.c
    side = history.repo.commit({b"src/side.py": text("s")}, b"feat: a branch\n", c[5])
    newest = {
        "lightweight": Tag("v0.2.0-rc", c[5], None),
        "no-record": Tag("v0.2.0-rc", c[5], "A message that records no candidate.\n"),
        "off-main": annotated("v0.2.0-rc", side, "0.2.0-rc"),
    }[case]
    reason = {
        "lightweight": service.LIGHTWEIGHT,
        "no-record": NO_TRAILER,
        "off-main": service.NOT_ON_MAIN,
    }[case]

    run = gora(host=FakeHost(c[7], [_older(history), newest]), cwd=history.root)

    assert (run.code, run.out, run.err.splitlines()) == (1, "", [f"api: {reason}"])


# --- 8. the first candidate -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("commit", "listed"),
    [
        pytest.param(1, [], id="before-the-declaration"),
        pytest.param(2, [], id="the-declarations-own-commit"),
        pytest.param(3, ["api 0.1.0-rc"], id="after-it-changing-sources"),
        pytest.param(4, [], id="changing-only-release-paths"),
        pytest.param(5, ["api 0.1.0-rc"], id="another-change-of-sources"),
        pytest.param(6, ["api 0.1.0-rc"], id="more-than-release-files-whatever-the-inputs"),
        pytest.param(7, ["api 0.1.0-rc"], id="the-head"),
    ],
)
def test_the_first_candidate_is_a_commit_after_the_declaration_that_changes_more_than_release_files(
    gora: Gora, history: History, commit: int, listed: list[str]
) -> None:
    run = gora(history.c[commit], host=FakeHost(history.c[7]), cwd=history.root)
    assert (run.code, run.lines, run.err) == (0, listed, "")


def test_the_head_is_the_default_commit(gora: Gora, history: History) -> None:
    host = FakeHost(history.c[7])
    run = gora(host=host, cwd=history.root)
    assert (run.code, run.lines) == (0, ["api 0.1.0-rc"])
    assert host.calls[0] == "branch_head main"


def test_a_declaration_deleted_and_declared_again_is_judged_from_the_newest(
    gora: Gora, history: History
) -> None:
    line, c = history.timeline, history.c
    line.commit("chore: retire api", drop=["artifacts/api/artifact.yaml"])
    again = line.commit(
        "feat: declare api again", {"artifacts/api/artifact.yaml": declaration("api")}
    )
    after = line.commit("feat: its source", {"src/new.py": b"new"})
    host = FakeHost(after)

    def at(commit: str) -> list[str]:
        return gora(commit, host=host, cwd=history.root).lines

    assert at(after) == ["api 0.1.0-rc"]
    assert at(again) == []  # the newest declaration's own commit
    assert at(c[7]) == []  # merged before it, though after the first declaration


# --- 9. a later candidate -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("commit", "listed"),
    [
        pytest.param(1, [], id="before-the-declaration"),
        pytest.param(3, [], id="the-newest-registered-candidate-itself"),
        pytest.param(4, [], id="a-release-path-only"),
        pytest.param(5, ["api 0.2.0-rc"], id="a-feat-minor"),
        pytest.param(6, ["api 0.2.0-rc"], id="readme-only-but-the-inputs-differ-from-the-tag-s"),
        pytest.param(7, ["api 0.2.0-rc"], id="the-highest-bump-since-the-tag-counts"),
    ],
)
def test_a_later_candidate_is_listed_when_its_inputs_differ_from_the_registered_ones(
    gora: Gora, history: History, commit: int, listed: list[str]
) -> None:
    host = FakeHost(history.c[7], [_older(history)])
    run = gora(history.c[commit], host=host, cwd=history.root)
    assert (run.code, run.lines, run.err) == (0, listed, "")


@pytest.mark.parametrize(
    ("commit", "listed"),
    [
        pytest.param(5, [], id="the-newest-registered-candidate-itself"),
        pytest.param(6, [], id="inputs-as-the-newest-registered-candidate-s"),
        pytest.param(7, ["api 0.2.1-rc"], id="a-fix-patch"),
    ],
)
def test_a_commit_is_judged_against_the_newest_registered_candidate(
    gora: Gora, history: History, commit: int, listed: list[str]
) -> None:
    c = history.c
    tags = [_older(history), annotated("v0.2.0-rc", c[5], "0.2.0-rc")]
    run = gora(c[commit], host=FakeHost(c[7], tags), cwd=history.root)
    assert (run.code, run.lines, run.err) == (0, listed, "")


def test_a_later_candidate_names_the_candidate_it_follows(gora: Gora, history: History) -> None:
    c = history.c
    tags = [_older(history), annotated("v0.2.0-rc", c[5], "0.2.0-rc")]

    run = gora("--output", "json", host=FakeHost(c[7], tags), cwd=history.root)

    assert json.loads(run.out)["artifacts"] == [
        {
            "name": "api",
            "version": "0.2.1-rc",
            "registered": {"version": "0.2.0-rc", "commit": c[5]},
        }
    ]


# --- two artifacts, and a source that is not the project's ----------------------------------


@pytest.fixture
def two(tmp_path: Path, env: dict[str, str]) -> History:
    """``api`` (prefix ``v``), ``web`` (prefix ``web/``) and ``lib``, built from another
    repository; c3 changes the sources of all three, c4 the web's only."""
    line = Timeline(Repo(tmp_path / "two", env))
    found = History(line)
    found.c[1] = line.commit("chore: start", {"release-policy.yaml": POLICY})
    found.c[2] = line.commit(
        "feat: declare three artifacts",
        {
            "artifacts/api/artifact.yaml": declaration("api"),
            "artifacts/lib/artifact.yaml": declaration(
                "lib", repository="github.com/acme/other", inputs=["lib/**"]
            ),
            "artifacts/web/artifact.yaml": declaration("web", prefix="web/", inputs=["web/**"]),
        },
    )
    found.c[3] = line.commit(
        "feat: all sources",
        {"src/a.py": b"a", "web/index.html": b"w", "lib/x.py": b"l"},
    )
    found.c[4] = line.commit("fix: the web only", {"web/index.html": b"w2"})
    return found


def test_the_artifacts_are_listed_in_name_order_and_only_those_the_project_builds(
    gora: Gora, two: History
) -> None:
    run = gora(two.c[3], host=FakeHost(two.c[4]), cwd=two.root)
    assert (run.code, run.lines) == (0, ["api 0.1.0-rc", "web 0.1.0-rc"])  # no lib: not S == R


def test_each_artifact_reads_the_tags_under_its_own_prefix(gora: Gora, two: History) -> None:
    c = two.c
    host = FakeHost(
        c[4],
        [
            annotated("v0.1.0-rc", c[3], "0.1.0-rc"),
            annotated("web/v0.1.0-rc", c[3], "0.1.0-rc", artifact="web"),
        ],
    )
    run = gora(host=host, cwd=two.root)
    assert (run.code, run.lines) == (0, ["web 0.1.1-rc"])  # a fix of the web's input only
    assert "tag_names " in host.calls and "tag_names web/" in host.calls


def test_a_name_under_the_prefix_that_is_no_rc_tag_is_ignored(gora: Gora, two: History) -> None:
    c = two.c
    host = FakeHost(c[4], [annotated("v0.1.0-rc", c[3], "0.1.0-rc")])
    host.names = ["vnext", "v0.2.0-rc.1", "v1.0.0"]  # what the host matched under ``v``
    run = gora(host=host, cwd=two.root)
    assert (run.code, run.err) == (0, "")
    assert "tag v0.1.0-rc" in host.calls
    assert [
        call for call in host.calls if call.startswith("tag v") and call != "tag v0.1.0-rc"
    ] == []


# --- 13. the body of --output json ----------------------------------------------------------


def schema_errors(body: object) -> list[str]:
    """What the published schema of ``changes`` finds wrong with ``body``: the schema generated
    from the model, which ``tests/unit/test_api_schemas.py`` holds equal, byte for byte, to
    ``contracts/schemas/api/v1/changes.json``."""
    return [error.message for error in Draft202012Validator(schema("Changes")).iter_errors(body)]


def test_the_json_body_is_exactly_the_result_model_on_one_line(
    gora: Gora, history: History
) -> None:
    c = history.c
    run = gora("--output", "json", c[3], host=FakeHost(c[7]), cwd=history.root)

    assert run.code == 0 and run.err == ""
    assert run.out == (
        f'{{"project":"{PROJECT}","source":"{PROJECT}","commit":"{c[3]}",'
        '"artifacts":[{"name":"api","version":"0.1.0-rc","registered":null}]}\n'
    )
    assert schema_errors(json.loads(run.out)) == []


def test_the_json_body_of_a_later_candidate_and_of_none_validate_against_the_schema(
    gora: Gora, history: History
) -> None:
    c = history.c
    host = FakeHost(c[7], [annotated("v0.2.0-rc", c[5], "0.2.0-rc")])
    later = gora("--output", "json", host=host, cwd=history.root)
    none = gora("--output", "json", c[6], host=host, cwd=history.root)

    assert json.loads(later.out)["artifacts"][0]["registered"] == {
        "version": "0.2.0-rc",
        "commit": c[5],
    }
    assert json.loads(none.out)["artifacts"] == [] and none.lines == [none.out.rstrip("\n")]
    for run in (later, none):
        assert schema_errors(json.loads(run.out)) == []
        assert Changes.model_validate_json(run.out).model_dump_json() + "\n" == run.out


def test_the_json_body_is_deterministic_and_the_text_is_empty_when_nothing_is_listed(
    gora: Gora, two: History
) -> None:
    host = FakeHost(two.c[4])
    first = gora("--output", "json", host=host, cwd=two.root)
    second = gora("--output", "json", host=host, cwd=two.root)
    assert first.out == second.out
    assert [a["name"] for a in json.loads(first.out)["artifacts"]] == ["api", "web"]
    nothing = gora(two.c[2], host=host, cwd=two.root)
    assert (nothing.code, nothing.out, nothing.err) == (0, "", "")


@pytest.mark.parametrize("output", ["xml", "JSON", "Text", ""])
def test_an_output_that_is_not_text_or_json_is_a_usage_error(
    gora: Gora, history: History, output: str
) -> None:
    host = FakeHost(history.c[7])
    run = gora("--output", output, host=host, cwd=history.root)
    assert run.code == 2 and run.out == ""
    assert host.calls == []


def test_the_schema_refuses_what_the_model_would(history: History) -> None:
    c = history.c[3]
    good = {
        "project": PROJECT,
        "source": PROJECT,
        "commit": c,
        "artifacts": [{"name": "api", "version": "0.1.0-rc", "registered": None}],
    }
    assert schema_errors(good) == []  # calibration: each defect below is the only thing wrong
    for defect in (
        {**good, "extra": 1},
        {k: v for k, v in good.items() if k != "source"},
        {**good, "commit": c[:7]},
        {**good, "project": "github.com/Acme/mono"},
        {**good, "artifacts": [{"name": "API", "version": "0.1.0-rc", "registered": None}]},
        {**good, "artifacts": [{"name": "api", "version": "0.1.0", "registered": None}]},
        {**good, "artifacts": [{"name": "api", "version": "0.1.0-rc"}]},
        {**good, "artifacts": [{"name": "api", "version": "0.1.0-rc", "registered": {}}]},
    ):
        assert schema_errors(defect) != [], defect
        with pytest.raises(ValueError):
            Changes.model_validate(defect)


# --- 14. GORA's own history -----------------------------------------------------------------

OWN = "github.com/gora-tools/gora"
DECLARATION = "e49348a290e99c935d2762229e305e518c4f059f"


@pytest.fixture(scope="module")
def own(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """GORA's own ``main``, the bundle, cloned whole."""
    root = tmp_path_factory.mktemp("own") / "gora"
    subprocess.run(
        [GIT, "clone", "-q", "--no-checkout", str(REAL_HISTORY), str(root)],
        env=hermetic_git_env(root.parent / "home"),
        capture_output=True,
        check=True,
    )
    return root


def test_gora_s_own_history_lists_gora_at_the_first_candidate_and_nothing_at_its_declaration(
    gora: Gora, own: Path
) -> None:
    host = FakeHost(REAL_HEAD)  # a host that holds no tag
    at_head = gora("--output", "json", REAL_HEAD, host=host, cwd=own, project=OWN)
    declared = gora("--output", "json", DECLARATION, host=host, cwd=own, project=OWN)

    assert at_head.code == 0, at_head.err
    assert at_head.out == (
        f'{{"project":"{OWN}","source":"{OWN}","commit":"{REAL_HEAD}",'
        '"artifacts":[{"name":"gora","version":"0.2.1-rc","registered":null}]}\n'
    )
    assert declared.code == 0, declared.err
    assert declared.out == (
        f'{{"project":"{OWN}","source":"{OWN}","commit":"{DECLARATION}","artifacts":[]}}\n'
    )
    for run in (at_head, declared):
        assert schema_errors(json.loads(run.out)) == []


def test_the_service_gives_the_same_answer_as_the_command(own: Path) -> None:
    found = service.changes(
        project=OWN, source=OWN, commit=None, host=FakeHost(REAL_HEAD), root=own, git=GIT
    )
    assert found.commit == REAL_HEAD
    assert [(a.name, a.version, a.registered) for a in found.artifacts] == [
        ("gora", "0.2.1-rc", None)
    ]
    # A head older than the commit asked about: the commit is not on the head's chain.
    before = subprocess.run(
        [GIT, "-C", str(own), "rev-parse", f"{DECLARATION}^"],
        env=hermetic_git_env(own.parent / "home"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    with pytest.raises(RefusedError, match=f"^{re.escape(L1)}$"):
        service.changes(
            project=OWN, source=OWN, commit=REAL_HEAD, host=FakeHost(before), root=own, git=GIT
        )
    with pytest.raises(GitError):
        service.changes(
            project=OWN, source=OWN, commit=None, host=FakeHost("2" * 40), root=own, git=GIT
        )


# --- 15. the token --------------------------------------------------------------------------


def test_the_token_is_sent_to_the_host_and_is_in_no_output_or_error(
    gora: Gora, history: History, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx2

    from tests.audit_fakes import intercept_transport

    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(401, json={"message": "Bad credentials", "status": "401"})

    intercept_transport(monkeypatch, answer)

    run = gora.real(cwd=history.root)  # ``Gora.real`` asserts the sentinel is in no output

    assert run.code == 1 and run.out == ""
    assert seen and SENTINEL in seen[0].headers["authorization"]  # calibration: it was used
    assert run.err.strip() != ""


# --- 16. no journal -------------------------------------------------------------------------


def test_changes_creates_no_audit_journal(
    gora: Gora, history: History, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "fresh"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("GORA_AUDIT_LOG")
    host = FakeHost(history.c[7])

    listed = gora(host=host, cwd=history.root)
    refused = gora("--source", "github.com/acme/other", host=host, cwd=history.root)

    assert (listed.code, refused.code) == (0, 1)
    assert list(home.iterdir()) == []

    monkeypatch.setenv("GORA_AUDIT_LOG", str(tmp_path / "never" / "j.jsonl"))
    assert gora(host=host, cwd=history.root).code == 0
    assert not (tmp_path / "never").exists()

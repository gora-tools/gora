"""The gate-change check (ADR-0016 rule 7), tested from the workflow file it runs in.

The classifier is the ``classify`` step of ``.github/workflows/gate-change.yml``, a
``shell: python`` step. These tests load that step's source from the file and run it
against path lists, patches and event snapshots, so what is tested is what the base branch
runs. ``gh`` is replaced by a fake: no test reaches GitHub.
"""

import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "gate-change.yml"
LABEL = "gate-change-reviewed"
# Spelled in two parts so this file's own lines never carry a suppression.
NOQA = "# " + "noqa"
TYPE_IGNORE = "type" + ": ignore"
HEAD = "a" * 40
NEWER = "b" * 40

NOT_GATE = "no gate files changed"
REVIEWED = f"gate files changed, reviewed (label {LABEL})"
UNREVIEWED = f"gate files changed: review, then add label {LABEL}"
UNCLASSIFIED = "gate-change could not classify the files"


def _workflow() -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _workflow()["jobs"]["gate-change"]["steps"]
    return steps


def _classify_step() -> dict[str, Any]:
    (step,) = [s for s in _steps() if s.get("id") == "classify"]
    return step


@pytest.fixture(scope="module")
def check() -> types.ModuleType:
    step = _classify_step()
    assert step["shell"] == "python"
    module = types.ModuleType("gate_change")
    exec(compile(step["run"], str(WORKFLOW), "exec"), module.__dict__)
    return module


def _file(name: str, patch: str | None = "@@ -1 +1 @@\n-a\n+b", **extra: Any) -> dict[str, Any]:
    return {"filename": name, "patch": patch, "additions": 1, **extra}


def _decide(
    check: types.ModuleType,
    files: list[dict[str, Any]] | None,
    *,
    action: str = "opened",
    labels: set[str] | None = None,
    sha: str = HEAD,
    live: str | None = HEAD,
) -> tuple[bool, str, str]:
    decision = check.decide(action, set() if labels is None else labels, sha, live, files)
    return (decision.post, decision.state, decision.description)


# --- which paths and lines make a gate change ---------------------------------------------

GATE_PATHS = [
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/gate-change.yml",
    "Makefile",
    "GNUmakefile",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "tests/conftest.py",
    "tests/unit/conftest.py",
    "deployments/docker/Dockerfile",
    "pytest.ini",
    "src/gora/ruff.toml",
    "setup.cfg",
    "mypy.ini",
]

ORDINARY_PATHS = [
    "src/gora/domain/release/manifest.py",
    "tests/unit/test_manifest.py",
    "tests/audit_fakes.py",
    "docs/adr/0016-a-gate-must-prove-it-can-fail.md",
    "README.md",
    "TESTING.md",
    ".github/CODEOWNERS",
    "deployments/docker/README.md",
    "docs/Makefile.md",
]


@pytest.mark.parametrize("path", GATE_PATHS)
def test_a_gate_defining_path_is_a_gate_change(check: types.ModuleType, path: str) -> None:
    assert check.gate_paths([_file(path)]) == [path]
    assert _decide(check, [_file(path)]) == (True, "failure", UNREVIEWED)
    assert f"`{path}`" in "\n".join(check.details([_file(path)]))


@pytest.mark.parametrize("path", ORDINARY_PATHS)
def test_an_ordinary_path_is_not(check: types.ModuleType, path: str) -> None:
    assert check.gate_paths([_file(path)]) == []
    assert _decide(check, [_file(path)]) == (True, "success", NOT_GATE)


def test_a_rename_counts_under_its_old_name(check: types.ModuleType) -> None:
    moved = _file("docs/old-makefile.txt", previous_filename="Makefile", status="renamed")
    assert check.gate_paths([moved]) == ["Makefile"]


@pytest.mark.parametrize("marker", [NOQA, f"{NOQA}: E501", TYPE_IGNORE, f"{TYPE_IGNORE}[arg-type]"])
def test_an_added_suppression_in_any_file_is_a_gate_change(
    check: types.ModuleType, marker: str
) -> None:
    patch = "@@ -10,3 +10,4 @@ def f():\n x = 1\n-y = 2\n+y = 2  #\n+z = f()  " + marker + "\n w"
    files = [_file("src/gora/services/notifier.py", patch)]
    found = check.suppressions(files)
    assert len(found) == 1
    assert found[0].startswith("src/gora/services/notifier.py:12: z = f()")
    assert _decide(check, files) == (True, "failure", UNREVIEWED)


def test_a_removed_or_unchanged_suppression_is_not(check: types.ModuleType) -> None:
    patch = f"@@ -1,3 +1,2 @@\n-a = 1  {NOQA}\n b = 2  {TYPE_IGNORE}\n c = 3"
    assert check.suppressions([_file("src/gora/main.py", patch)]) == []


def test_line_numbers_follow_the_hunks(check: types.ModuleType) -> None:
    patch = (
        "@@ -1,2 +1,2 @@\n-a\n+b\n c\n"
        "\\ No newline at end of file\n"
        f"@@ -40,2 +41,3 @@\n d\n-e\n+f\n+g  {NOQA}"
    )
    assert check.suppressions([_file("src/x.py", patch)]) == [f"src/x.py:43: g  {NOQA}"]


def test_added_lines_the_api_did_not_send_are_not_assumed_clean(check: types.ModuleType) -> None:
    big = _file("src/gora/big.py", patch=None, additions=5000)
    binary = _file("docs/diagram.png", patch=None, additions=0)
    assert check.suppressions([big, binary]) == [
        "src/gora/big.py: no patch from the API, added lines unread"
    ]


# --- the decision: what is posted on which head ----------------------------------------------


def test_a_reviewed_gate_change_is_success(check: types.ModuleType) -> None:
    files = [_file("Makefile"), _file("src/gora/main.py")]
    for action in ("opened", "reopened", "labeled", "unlabeled"):
        assert _decide(check, files, action=action, labels={LABEL, "bug"}) == (
            True,
            "success",
            REVIEWED,
        )
    assert _decide(check, files, labels={"bug"}) == (True, "failure", UNREVIEWED)


def test_a_push_is_unreviewed_even_when_the_payload_lists_the_label(
    check: types.ModuleType,
) -> None:
    files = [_file("uv.lock")]
    assert _decide(check, files, action="synchronize", labels={LABEL}) == (
        True,
        "failure",
        UNREVIEWED,
    )
    # A push that changes no gate file needs no review.
    assert _decide(check, [_file("README.md")], action="synchronize") == (True, "success", NOT_GATE)


def test_a_stale_snapshot_posts_nothing(check: types.ModuleType) -> None:
    for files in ([_file("Makefile")], [_file("README.md")], None):
        assert _decide(check, files, live=NEWER, labels={LABEL}) == (
            False,
            "superseded",
            f"superseded by {NEWER}",
        )


@pytest.mark.parametrize(
    "files",
    [None, [{"patch": "@@ -1 +1 @@\n+x"}], [{"filename": None}], ["Makefile"]],
    ids=["unread", "no-filename", "null-filename", "not-an-object"],
)
def test_files_it_cannot_classify_are_an_error(check: types.ModuleType, files: Any) -> None:
    assert _decide(check, files) == (True, "error", UNCLASSIFIED)


def test_unreadable_labels_or_live_head_are_an_error(check: types.ModuleType) -> None:
    files = [_file("README.md")]
    assert check.decide("opened", None, HEAD, HEAD, files) == (True, "error", UNCLASSIFIED)
    assert _decide(check, files, live=None) == (True, "error", UNCLASSIFIED)
    assert _decide(check, files, live="not-a-sha") == (True, "error", UNCLASSIFIED)


@pytest.mark.parametrize("sha", ["", "a" * 39, "A" * 40, HEAD + "\n", "g" * 40, f"{HEAD} x"])
def test_a_payload_head_that_is_not_a_sha_posts_nothing(check: types.ModuleType, sha: str) -> None:
    post, state, _ = _decide(check, [_file("README.md")], sha=sha, live=sha)
    assert (post, state) == (False, "error")


# --- main: the API calls, in order, through a fake gh -----------------------------------------


class FakeGh:
    """Answers gh api calls from a table and records every call."""

    def __init__(
        self,
        *,
        live: str = HEAD,
        files: list[dict[str, Any]] | None = None,
        live_labels: tuple[str, ...] = (),
        fail: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.live, self.live_labels, self.fail = live, live_labels, fail
        self.files = [_file("README.md")] if files is None else files

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        endpoint = next(a for a in args if a.startswith("repos/"))
        if self.fail and self.fail in endpoint:
            raise RuntimeError(f"gh api {endpoint} failed: HTTP 502")
        if endpoint.endswith("/labels"):
            return "".join(f"{name}\n" for name in self.live_labels)
        if "/files" in endpoint:
            return "".join(json.dumps(f) + "\n" for f in self.files)
        if "/statuses/" in endpoint or "-X" in args:
            return "{}"
        return self.live + "\n"

    def statuses(self) -> list[dict[str, str]]:
        posted = []
        for args in self.calls:
            if any("/statuses/" in a for a in args):
                fields = [a for a in args if "=" in a and not a.startswith("repos/")]
                posted.append(dict(f.split("=", 1) for f in fields) | {"sha": args[0][-40:]})
        return posted


@pytest.fixture
def run(
    check: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[int, FakeGh, str]]:
    summary = tmp_path / "summary.md"

    def go(fake: FakeGh, *, action: str = "opened", labels: str = "[]") -> tuple[int, FakeGh, str]:
        env = {
            "REPO": "owner/repo",
            "PR": "46",
            "ACTION": action,
            "HEAD_SHA": HEAD,
            "PAYLOAD_LABELS": labels,
            "RUN_URL": "https://github.com/owner/repo/actions/runs/1",
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(check, "gh", fake)
        status = check.main()
        return status, fake, summary.read_text(encoding="utf-8")

    return go


def test_main_posts_pending_then_the_result_on_the_payload_head(
    run: Callable[..., tuple[int, FakeGh, str]],
) -> None:
    status, fake, summary = run(FakeGh(files=[_file("Makefile")]), labels=json.dumps([LABEL]))
    assert status == 0
    assert [(s["state"], s["description"], s["sha"]) for s in fake.statuses()] == [
        ("pending", "checking gate files", HEAD),
        ("success", REVIEWED, HEAD),
    ]
    assert all(s["context"] == "gate-change" for s in fake.statuses())
    assert all(s["target_url"].endswith("/actions/runs/1") for s in fake.statuses())
    assert "`Makefile`" in summary


def test_main_fails_an_unreviewed_gate_change(run: Callable[..., tuple[int, FakeGh, str]]) -> None:
    status, fake, _ = run(FakeGh(files=[_file("uv.lock")]))
    assert status == 1
    assert fake.statuses()[-1]["state"] == "failure"


def test_main_on_a_push_removes_the_label_and_ignores_it_in_the_payload(
    run: Callable[..., tuple[int, FakeGh, str]],
) -> None:
    fake = FakeGh(files=[_file("uv.lock")], live_labels=(LABEL, "bug"))
    status, fake, _ = run(fake, action="synchronize", labels=json.dumps([LABEL]))
    assert status == 1
    assert ("-X", "DELETE", f"repos/owner/repo/issues/46/labels/{LABEL}") in fake.calls
    assert fake.statuses()[-1]["state"] == "failure"


def test_main_posts_nothing_for_a_superseded_head(
    run: Callable[..., tuple[int, FakeGh, str]],
) -> None:
    status, fake, summary = run(FakeGh(live=NEWER, files=[_file("Makefile")]))
    assert status == 0
    assert fake.statuses() == []
    assert f"superseded by {NEWER}" in summary


def test_main_posts_error_when_the_files_cannot_be_read(
    run: Callable[..., tuple[int, FakeGh, str]],
) -> None:
    status, fake, summary = run(FakeGh(fail="/files"))
    assert status == 1
    assert [s["state"] for s in fake.statuses()] == ["pending", "error"]
    assert fake.statuses()[-1]["description"] == UNCLASSIFIED
    assert "HTTP 502" in summary


# --- the workflow itself ---------------------------------------------------------------------


def test_the_workflow_is_defined_on_the_base_and_runs_nothing_from_the_pr() -> None:
    workflow = _workflow()
    trigger: dict[str, Any] = workflow[True]  # YAML 1.1 reads the bare key `on` as true
    assert list(trigger) == ["pull_request_target"]
    assert trigger["pull_request_target"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "labeled",
        "unlabeled",
    ]
    assert workflow["permissions"] == {
        "pull-requests": "write",
        "statuses": "write",
        "contents": "read",
    }
    steps = _steps()
    assert [s for s in steps if "uses" in s] == []  # no checkout, no action at all
    assert "${{" not in "".join(s["run"] for s in steps)  # event data reaches run via env only


def test_the_snapshot_comes_from_the_event_payload() -> None:
    env = _classify_step()["env"]
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert env["PAYLOAD_LABELS"] == "${{ toJSON(github.event.pull_request.labels.*.name) }}"
    assert env["ACTION"] == "${{ github.event.action }}"

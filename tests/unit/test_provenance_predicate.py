"""The provenance predicate of GORA's own build holds the form GORA accepts and keeps the
token secret (ADR-018 section 3; TESTING rule 5).

``.github/actions/provenance-predicate/predicate.py`` is the one generator of the predicate that
``build.yml`` attests with ``cosign attest --type slsaprovenance1``. It reads the claims of the
run's OIDC token and writes the ``workflow/v1`` predicate that ``gora.domain.evidence.provenance``
reads. These tests run the file as the action does, through ``sys.executable`` with nothing in
the environment but ``PATH``, offline (TESTING rule 6), on a token made here.

The token is **synthetic**: a JWT whose payload holds the documented claims of a GitHub Actions
run (https://docs.github.com/actions/reference/security/oidc), made at run time from the claims
below, with a header and a signature that are not a real signature of anything. No token is
committed, since the Run gate's scan refuses a token-shaped string. The first real claims come
from the first live run of ``build.yml`` on ``main`` (its merge), and the next pull request
commits the statement that run made as a fixture; until then the form is held against GORA's own
reader and against the statement GitHub's toolkit wrote for another project
(``tests/fixtures/provenance/vaultwarden.sigstore.json``). The path through the token endpoint,
which needs the network, is not run here: only what it refuses before it connects.

Each statement is shown red on a defect that turns it red (ADR-016 rule 1): a field missing or
added, a token printed, a claim accepted that GORA's form refuses.
"""

import ast
import base64
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from gora.domain.evidence.certificate import Certificate
from gora.domain.evidence.provenance import (
    BuildLevel,
    EvidenceError,
    Expected,
    read_statement,
    verify,
)
from gora.domain.release_repository.v1alpha1 import IdentitySigner

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "provenance-predicate"
SCRIPT = ACTION / "predicate.py"
FIXTURE = ROOT / "tests" / "fixtures" / "provenance" / "vaultwarden.sigstore.json"

ISSUER = "https://token.actions.githubusercontent.com"
BUILDER = "https://github.com/gora-tools/gora/.github/workflows/build.yml@refs/heads/main"
COMMIT = "58a980f850464f7d9cdc99a765df36f0a3308b91"
DIGEST = "a" * 64

# The documented claims of a push to main that called build.yml from candidate.yml. Synthetic.
CLAIMS: dict[str, str] = {
    "repository": "gora-tools/gora",
    "repository_id": "1234567890",
    "repository_owner_id": "9876543",
    "ref": "refs/heads/main",
    "sha": COMMIT,
    "event_name": "push",
    "runner_environment": "github-hosted",
    "workflow_ref": "gora-tools/gora/.github/workflows/candidate.yml@refs/heads/main",
    "job_workflow_ref": "gora-tools/gora/.github/workflows/build.yml@refs/heads/main",
    "run_id": "12345678901",
    "run_attempt": "1",
}

# What the claims above make, every field written out and in the order of the fixture.
EXPECTED_PREDICATE: dict[str, Any] = {
    "buildDefinition": {
        "buildType": "https://actions.github.io/buildtypes/workflow/v1",
        "externalParameters": {
            "workflow": {
                "ref": "refs/heads/main",
                "repository": "https://github.com/gora-tools/gora",
                "path": ".github/workflows/candidate.yml",
            }
        },
        "internalParameters": {
            "github": {
                "event_name": "push",
                "repository_id": "1234567890",
                "repository_owner_id": "9876543",
                "runner_environment": "github-hosted",
            }
        },
        "resolvedDependencies": [
            {
                "uri": "git+https://github.com/gora-tools/gora@refs/heads/main",
                "digest": {"gitCommit": COMMIT},
            }
        ],
    },
    "runDetails": {
        "builder": {"id": BUILDER},
        "metadata": {
            "invocationId": "https://github.com/gora-tools/gora/actions/runs/12345678901/attempts/1"
        },
    },
}


# --- running the file as the action does ----------------------------------------------------


def _b64(data: bytes) -> str:
    """base64url without padding, as a JWT writes each of its parts."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def make_token(claims: Mapping[str, object]) -> str:
    """A JWT-shaped token over ``claims``: a header, the payload and a signature that signs
    nothing."""
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    signature = _b64(b"synthetic signature bytes: nothing is signed with them")
    return f"{header}.{payload}.{signature}"


def _environment(**extra: str) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **extra}


class Run:
    """One run of predicate.py: its exit code, its two streams and the file it was asked to
    write, if it wrote one."""

    def __init__(self, done: subprocess.CompletedProcess[str], output: Path) -> None:
        self.code = done.returncode
        self.stdout = done.stdout
        self.stderr = done.stderr
        self.output = output

    @property
    def written(self) -> str | None:
        return self.output.read_text(encoding="utf-8") if self.output.exists() else None

    @property
    def everything(self) -> str:
        """What a reader of the run can see: both streams and the file."""
        return self.stdout + self.stderr + (self.written or "")


def run_with_token(token: str, tmp_path: Path) -> Run:
    """The script on ``token`` from a file, as the test path of the action reads it."""
    token_file = tmp_path / "token"
    token_file.write_text(token + "\n", encoding="utf-8")
    return _run(["--token-file", str(token_file)], tmp_path, _environment())


def _run(options: list[str], tmp_path: Path, environment: dict[str, str]) -> Run:
    output = tmp_path / "predicate.json"
    done = subprocess.run(
        [sys.executable, str(SCRIPT), *options, str(output)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return Run(done, output)


def assert_keeps_the_token_secret(token: str, run: Run, *, whole_parts: bool = True) -> None:
    """The token, and each part of it, is in neither stream nor in the file."""
    seen = run.everything
    assert token == "" or token not in seen
    if whole_parts:
        for part in token.split("."):
            if len(part) >= 8:
                assert part not in seen


def _the_statement(predicate: str, *, name: str = "ghcr.io/gora-tools/gora") -> bytes:
    """The predicate wrapped as ``cosign attest`` v3.1.5 writes it: an in-toto Statement v0.1
    whose predicate type is SLSA provenance v1, one subject, an image at a digest."""
    return json.dumps(
        {
            "_type": "https://in-toto.io/Statement/v0.1",
            "predicateType": "https://slsa.dev/provenance/v1",
            "subject": [{"name": name, "digest": {"sha256": DIGEST}}],
            "predicate": json.loads(predicate),
        }
    ).encode()


def _expected(builder: str = BUILDER) -> Expected:
    return Expected(
        image="ghcr.io/gora-tools/gora",
        digest=f"sha256:{DIGEST}",
        repository="github.com/gora-tools/gora",
        repository_id=CLAIMS["repository_id"],
        commit=COMMIT,
        builders=(IdentitySigner.model_validate({"issuer": ISSUER, "identity": builder}),),
    )


def _certificate(run_workflow: str, builder: str = BUILDER) -> Certificate:
    """The certificate Fulcio would issue from the same claims: ``.18`` is the workflow that
    ran, ``.13``, ``.14`` and ``.15`` the commit, the ref and the repository."""
    return Certificate(
        issuer=ISSUER,
        identity=builder,
        source_commit=COMMIT,
        source_ref="refs/heads/main",
        repository_id=CLAIMS["repository_id"],
        build_config=run_workflow,
        runner_environment="github-hosted",
    )


# --- the predicate is exactly the form ------------------------------------------------------


def test_the_predicate_is_every_field_of_the_form_and_no_other(tmp_path: Path) -> None:
    run = run_with_token(make_token(CLAIMS), tmp_path)
    assert run.code == 0, run.stderr
    # Compared as text: the keys in the order of the fixture, on one line.
    assert run.written == json.dumps(EXPECTED_PREDICATE, separators=(",", ":")) + "\n"
    assert run.written is not None
    assert len(run.written.splitlines()) == 1


def _skeleton(node: Any) -> Any:
    """The keys of a document, in order, down to its strings."""
    if isinstance(node, dict):
        return {key: _skeleton(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_skeleton(item) for item in node]
    assert isinstance(node, str)
    return "string"


def test_the_predicate_has_the_keys_in_the_order_of_the_statement_a_toolkit_wrote(
    tmp_path: Path,
) -> None:
    """The fixture is a statement GitHub's toolkit wrote, not one written here: our predicate
    has its fields, its depth and its order."""
    bundle = json.loads(FIXTURE.read_text(encoding="utf-8"))
    written = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"], validate=True))
    run = run_with_token(make_token(CLAIMS), tmp_path)
    assert run.written is not None
    ours = json.loads(run.written)
    toolkit = written["predicate"]
    assert json.dumps(_skeleton(ours)) == json.dumps(_skeleton(toolkit))
    assert ours["buildDefinition"]["buildType"] == toolkit["buildDefinition"]["buildType"]


def test_the_predicate_wrapped_as_cosign_writes_it_is_accepted_by_the_reader(
    tmp_path: Path,
) -> None:
    run = run_with_token(make_token(CLAIMS), tmp_path)
    assert run.written is not None
    statement = read_statement(_the_statement(run.written))
    assert statement.name == "ghcr.io/gora-tools/gora"
    assert statement.digest == f"sha256:{DIGEST}"
    assert statement.provenance.builder == BUILDER
    assert statement.provenance.commit == COMMIT


def test_a_build_called_from_another_workflow_is_level_3(tmp_path: Path) -> None:
    """build.yml called from candidate.yml: the builder is not the workflow that ran."""
    run = run_with_token(make_token(CLAIMS), tmp_path)
    assert run.written is not None
    ran = "https://github.com/gora-tools/gora/.github/workflows/candidate.yml@refs/heads/main"
    verified = verify(_the_statement(run.written), _certificate(ran), _expected())
    assert verified.level is BuildLevel.L3
    assert verified.statement.provenance.reusable


def test_a_build_that_is_the_workflow_that_ran_is_level_2(tmp_path: Path) -> None:
    """The same file as the one that ran: not a reusable workflow, so not level 3."""
    claims = {**CLAIMS, "workflow_ref": CLAIMS["job_workflow_ref"]}
    run = run_with_token(make_token(claims), tmp_path)
    assert run.written is not None
    ran = "https://github.com/gora-tools/gora/.github/workflows/build.yml@refs/heads/main"
    verified = verify(_the_statement(run.written), _certificate(ran), _expected())
    assert verified.level is BuildLevel.L2
    assert not verified.statement.provenance.reusable


def test_a_predicate_from_other_claims_is_not_accepted_for_this_build(tmp_path: Path) -> None:
    """The binding is checked against the claims: a commit that is not the image's is refused."""
    run = run_with_token(make_token({**CLAIMS, "sha": "b" * 40}), tmp_path)
    assert run.written is not None
    ran = "https://github.com/gora-tools/gora/.github/workflows/candidate.yml@refs/heads/main"
    with pytest.raises(EvidenceError, match="gitCommit"):
        verify(_the_statement(run.written), _certificate(ran), _expected())


# --- what is refused, and that the refusal names the claim and never a value ----------------

SENTINEL_EVENT = "sentinel-event"
SENTINEL_BRANCH = "refs/heads/sentinel-branch"
SENTINEL_RUNNER = "sentinel-runner"


def _without(name: str) -> dict[str, Any]:
    return {key: value for key, value in CLAIMS.items() if key != name}


# (id, the claims, the claim the message names, a value the message must not show)
REFUSALS: list[tuple[str, dict[str, Any], str, str | None]] = [
    *[(f"missing-{name}", _without(name), name, None) for name in CLAIMS],
    *[(f"not-a-string-{name}", {**CLAIMS, name: 7}, name, None) for name in CLAIMS],
    *[(f"empty-{name}", {**CLAIMS, name: ""}, name, None) for name in CLAIMS],
    ("event-not-push", {**CLAIMS, "event_name": SENTINEL_EVENT}, "event_name", SENTINEL_EVENT),
    (
        "event-workflow-dispatch",
        {**CLAIMS, "event_name": "workflow_dispatch"},
        "event_name",
        "workflow_dispatch",
    ),
    ("ref-not-main", {**CLAIMS, "ref": SENTINEL_BRANCH}, "ref", SENTINEL_BRANCH),
    ("ref-a-tag", {**CLAIMS, "ref": "refs/tags/v1.0.0"}, "ref", "refs/tags/v1.0.0"),
    (
        "runner-unknown",
        {**CLAIMS, "runner_environment": SENTINEL_RUNNER},
        "runner_environment",
        SENTINEL_RUNNER,
    ),
    (
        "workflow-of-another-repository",
        {**CLAIMS, "workflow_ref": "other/repo/.github/workflows/x.yml@refs/heads/main"},
        "workflow_ref",
        "other/repo",
    ),
    (
        "workflow-without-a-ref",
        {**CLAIMS, "workflow_ref": "gora-tools/gora/.github/workflows/x.yml"},
        "workflow_ref",
        None,
    ),
    (
        "workflow-without-a-path",
        {**CLAIMS, "workflow_ref": "gora-tools/gora/@refs/heads/main"},
        "workflow_ref",
        None,
    ),
]


@pytest.mark.parametrize(
    ("claims", "named", "shown"),
    [pytest.param(*case[1:], id=case[0]) for case in REFUSALS],
)
def test_a_claim_the_form_would_refuse_writes_nothing_and_names_the_claim(
    claims: dict[str, Any], named: str, shown: str | None, tmp_path: Path
) -> None:
    token = make_token(claims)
    run = run_with_token(token, tmp_path)
    assert run.code == 1, run.stderr
    assert run.written is None
    assert f"claim {named}" in run.stderr
    assert run.stdout == ""
    if shown is not None:
        assert shown not in run.everything
    assert_keeps_the_token_secret(token, run)


def test_every_claim_the_predicate_is_made_of_is_refused_when_missing() -> None:
    """The refusals above cover each claim, and the claims are the eleven the spec names."""
    assert sorted(CLAIMS) == sorted(
        [
            "event_name",
            "job_workflow_ref",
            "ref",
            "repository",
            "repository_id",
            "repository_owner_id",
            "run_attempt",
            "run_id",
            "runner_environment",
            "sha",
            "workflow_ref",
        ]
    )
    assert {case[2] for case in REFUSALS} >= set(CLAIMS)


def test_a_self_hosted_runner_is_accepted(tmp_path: Path) -> None:
    run = run_with_token(make_token({**CLAIMS, "runner_environment": "self-hosted"}), tmp_path)
    assert run.code == 0, run.stderr
    assert run.written is not None
    assert (
        json.loads(run.written)["buildDefinition"]["internalParameters"]["github"][
            "runner_environment"
        ]
        == "self-hosted"
    )


# --- the token is read as a JWT's payload and nothing else ----------------------------------


def _padded_with_both_alphabet_marks() -> dict[str, Any]:
    """Claims whose payload, in base64url without padding, holds ``-`` and ``_`` and needs
    padding: the characters base64 writes ``+`` and ``/`` for."""
    for filler in range(200):
        claims = {**CLAIMS, "note": "~?>?>~?>" * 4 + "x" * filler}
        encoded = _b64(json.dumps(claims).encode())
        if "-" in encoded and "_" in encoded and len(encoded) % 4 != 0:
            return claims
    raise AssertionError("no claims found whose payload holds - and _ and needs padding")


def test_a_token_without_padding_and_with_dash_and_underscore_is_read(tmp_path: Path) -> None:
    claims = _padded_with_both_alphabet_marks()
    token = make_token(claims)
    payload = token.split(".")[1]
    assert "=" not in payload
    assert "-" in payload
    assert "_" in payload
    run = run_with_token(token, tmp_path)
    assert run.code == 0, run.stderr
    assert run.written == json.dumps(EXPECTED_PREDICATE, separators=(",", ":")) + "\n"


def test_a_payload_with_its_padding_is_read(tmp_path: Path) -> None:
    claims = _padded_with_both_alphabet_marks()
    header, payload, signature = make_token(claims).split(".")
    padded = payload + "=" * (-len(payload) % 4)
    assert padded != payload
    run = run_with_token(f"{header}.{padded}.{signature}", tmp_path)
    assert run.code == 0, run.stderr


def test_the_header_and_the_signature_are_never_read(tmp_path: Path) -> None:
    """Only the payload is decoded: a header and a signature that are not base64 at all pass."""
    payload = make_token(CLAIMS).split(".")[1]
    run = run_with_token(f"not a header!.{payload}.not a signature!", tmp_path)
    assert run.code == 0, run.stderr


def _standard_alphabet_payload() -> str:
    """A payload that is base64, but with ``+`` and ``/``: not base64url."""
    claims = _padded_with_both_alphabet_marks()
    standard = base64.b64encode(json.dumps(claims).encode()).decode("ascii").rstrip("=")
    assert "+" in standard or "/" in standard
    return standard


def _part(data: bytes) -> str:
    return _b64(data)


HEADER = _part(b'{"alg":"none"}')
SIGNATURE = _part(b"synthetic signature bytes")

# (id, a token that is not the payload of a run's claims)
MALFORMED: list[tuple[str, str]] = [
    ("not-json", f"{HEADER}.{_part(b'this is not json')}.{SIGNATURE}"),
    ("a-json-array", f"{HEADER}.{_part(b'[1, 2, 3]')}.{SIGNATURE}"),
    ("a-json-string", f"{HEADER}.{_part(b'"a string"')}.{SIGNATURE}"),
    ("a-json-number", f"{HEADER}.{_part(b'42')}.{SIGNATURE}"),
    ("null", f"{HEADER}.{_part(b'null')}.{SIGNATURE}"),
    ("not-utf-8", f"{HEADER}.{_part(bytes([0xFF, 0xFE, 0xFD]))}.{SIGNATURE}"),
    ("two-parts", f"{HEADER}.{_part(b'{}')}"),
    ("one-part", _part(b"{}")),
    ("four-parts", f"{HEADER}.{_part(b'{}')}.{SIGNATURE}.{SIGNATURE}"),
    ("empty-payload", f"{HEADER}..{SIGNATURE}"),
    ("empty-header", f".{_part(b'{}')}.{SIGNATURE}"),
    ("empty-signature", f"{HEADER}.{_part(b'{}')}."),
    ("characters-of-no-alphabet", f"{HEADER}.!!!!.{SIGNATURE}"),
    ("one-character-too-many", f"{HEADER}.{_part(b'{}')}A.{SIGNATURE}"),
    ("standard-alphabet", f"{HEADER}.{_standard_alphabet_payload()}.{SIGNATURE}"),
    ("empty-token", ""),
]


@pytest.mark.parametrize("token", [pytest.param(case[1], id=case[0]) for case in MALFORMED])
def test_a_token_that_is_not_three_parts_of_which_the_middle_is_a_json_object_is_refused(
    token: str, tmp_path: Path
) -> None:
    run = run_with_token(token, tmp_path)
    assert run.code == 1, run.stderr
    assert run.written is None
    assert run.stdout == ""
    assert run.stderr.startswith("predicate: ")
    assert "Traceback" not in run.stderr
    assert_keeps_the_token_secret(token, run, whole_parts=False)
    # The payload, the part that holds the claims, is never shown, whatever it was.
    parts = token.split(".")
    if len(parts) == 3 and len(parts[1]) >= 8:
        assert parts[1] not in run.everything


def test_a_token_file_that_is_not_there_is_refused(tmp_path: Path) -> None:
    run = _run(["--token-file", str(tmp_path / "missing")], tmp_path, _environment())
    assert run.code == 1
    assert run.written is None


def test_an_output_that_cannot_be_written_is_refused_and_the_token_is_not_shown(
    tmp_path: Path,
) -> None:
    token = make_token(CLAIMS)
    token_file = tmp_path / "token"
    token_file.write_text(token, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--token-file", str(token_file), str(tmp_path / "no" / "x")],
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 1
    assert token not in done.stdout + done.stderr
    assert token.split(".")[1] not in done.stdout + done.stderr


# --- the token is never printed, in the success or in any refusal ---------------------------


def test_the_token_and_its_parts_are_in_no_stream_and_not_in_the_file_on_success(
    tmp_path: Path,
) -> None:
    token = make_token(CLAIMS)
    run = run_with_token(token, tmp_path)
    assert run.code == 0, run.stderr
    header, payload, signature = token.split(".")
    assert run.stdout + run.stderr == ""
    assert run.written is not None
    for secret in (token, header, payload, signature):
        assert secret not in run.everything


def test_the_decoded_payload_is_not_printed_either(tmp_path: Path) -> None:
    """Whatever the claims hold that the predicate does not use stays out of every stream and of
    the file, so a printed payload shows."""
    claims = {**CLAIMS, "actor": "sentinel-actor", "job_workflow_sha": "sentinel-job-sha"}
    run = run_with_token(make_token(claims), tmp_path)
    assert run.code == 0, run.stderr
    assert "sentinel-actor" not in run.everything
    assert "sentinel-job-sha" not in run.everything


# --- the token from the Actions environment, as far as it is reached offline ----------------


def test_without_the_runner_s_variables_nothing_is_written(tmp_path: Path) -> None:
    run = _run([], tmp_path, _environment())
    assert run.code == 1
    assert run.written is None
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in run.stderr
    assert "id-token: write" in run.stderr


def test_a_request_url_that_is_not_https_is_refused_before_any_connection(tmp_path: Path) -> None:
    """The request token is a secret as the OIDC token is: it is never shown, nor is the URL."""
    request_value = "sentinel-request-value"
    url = "http://sentinel-host.invalid/token?sentinel-query"
    run = _run(
        [],
        tmp_path,
        _environment(
            ACTIONS_ID_TOKEN_REQUEST_URL=url, ACTIONS_ID_TOKEN_REQUEST_TOKEN=request_value
        ),
    )
    assert run.code == 1
    assert run.written is None
    assert "https" in run.stderr
    for hidden in (request_value, "sentinel-host", "sentinel-query"):
        assert hidden not in run.everything


# --- the action runs that file and nothing else ---------------------------------------------


def test_the_action_runs_predicate_py_with_bash_and_nothing_else() -> None:
    action = yaml.safe_load((ACTION / "action.yml").read_text(encoding="utf-8"))
    assert list(action["inputs"]) == ["output"]
    assert action["inputs"]["output"]["required"] is True
    assert action["runs"]["using"] == "composite"
    (step,) = action["runs"]["steps"]
    assert step["shell"] == "bash"
    assert step["run"] == 'python3 "$GITHUB_ACTION_PATH/predicate.py" "$OUTPUT"'
    # Every value reaches the script through env, never through an expression in the script.
    assert "${{" not in step["run"]
    assert step["env"] == {"OUTPUT": "${{ inputs.output }}"}
    assert sorted(path.name for path in ACTION.iterdir()) == ["action.yml", "predicate.py"]


def test_the_script_imports_the_standard_library_alone() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imported
    assert imported <= set(sys.stdlib_module_names)

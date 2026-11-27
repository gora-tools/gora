"""Write the SLSA provenance predicate of this run, in the form GORA accepts.

The one generator of the predicate that ``build.yml`` attests (ADR-018 section 3): the
``workflow/v1`` build type GitHub's own toolkit writes from the claims of a run's OIDC token, and
that ``gora.domain.evidence.provenance`` reads, field for field and no field more. ``cosign
attest --type slsaprovenance1`` wraps the file in a statement and signs it.

Standard library only, on the runner's python3. The token comes from the Actions environment
(``ACTIONS_ID_TOKEN_REQUEST_URL`` and ``ACTIONS_ID_TOKEN_REQUEST_TOKEN``) or, for the test, from a
file named by ``--token-file``; both go through the same decoding and the same predicate. Only the
payload of the token is read: its header and its signature are never decoded, and the token
proves nothing here that the signer's certificate does not state again, since Fulcio issues that
certificate from the same claims (ADR-018 section 3).

A predicate GORA's form would refuse is not written: a claim that is missing or not a string, an
event that is not ``push``, a ref that is not ``refs/heads/main`` and a runner that is neither
GitHub-hosted nor self-hosted end the run with exit 1 and a message that names the claim and never
a value.

The token is never printed: not to standard output or error, not in a message, not in an
exception and not in the file, and nothing of the token endpoint's answer is shown. It is held in
memory and dropped once its payload is read. Every message here is a fixed text.
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"
HOST = "https://github.com"
EVENT = "push"
MAIN = "refs/heads/main"
RUNNERS = ("github-hosted", "self-hosted")
# The claims the predicate is made of, and nothing else of the token is read.
CLAIMS = (
    "repository",
    "repository_id",
    "repository_owner_id",
    "ref",
    "sha",
    "event_name",
    "runner_environment",
    "workflow_ref",
    "job_workflow_ref",
    "run_id",
    "run_attempt",
)
BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")
TIMEOUT = 30


class RefusalError(Exception):
    """A reason to write nothing; its text is fixed and holds no value of the token."""


def payload(token: str) -> dict[str, Any]:
    """The claims of ``token``: its middle part, base64url with its padding added, as JSON."""
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise RefusalError("the token is not three dot-separated parts")
    middle = parts[1]
    if BASE64URL.fullmatch(middle) is None:
        raise RefusalError("the token's payload is not base64url")
    middle = middle.rstrip("=")
    try:
        raw = base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4))
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        # binascii.Error, UnicodeDecodeError and JSONDecodeError are ValueErrors: none is shown,
        # since the text of an exception can quote what it could not decode.
        raise RefusalError("the token's payload is not UTF-8 JSON") from None
    if not isinstance(document, dict):
        raise RefusalError("the token's payload is not a JSON object")
    return document


def claims(document: dict[str, Any]) -> dict[str, str]:
    """The claims the predicate needs, each a string, the run's own and no other."""
    found: dict[str, str] = {}
    for name in CLAIMS:
        value = document.get(name)
        if not isinstance(value, str) or not value:
            raise RefusalError(f"claim {name} is missing or is not a string")
        found[name] = value
    if found["event_name"] != EVENT:
        raise RefusalError(f"claim event_name must be {EVENT}: a build of a commit pushed to main")
    if found["ref"] != MAIN:
        raise RefusalError(f"claim ref must be {MAIN}: a build on main")
    if found["runner_environment"] not in RUNNERS:
        raise RefusalError("claim runner_environment must be github-hosted or self-hosted")
    return found


def workflow_path(found: dict[str, str]) -> str:
    """The file of the workflow that ran: ``workflow_ref`` is ``<repository>/<path>@<ref>``."""
    prefix = found["repository"] + "/"
    if not found["workflow_ref"].startswith(prefix):
        raise RefusalError("claim workflow_ref is not <repository>/<path>@<ref>")
    path, at, _ = found["workflow_ref"].removeprefix(prefix).partition("@")
    if not at or not path:
        raise RefusalError("claim workflow_ref is not <repository>/<path>@<ref>")
    return path


def predicate(found: dict[str, str]) -> dict[str, Any]:
    """The predicate, in the key order of the fixture a toolkit wrote
    (``tests/fixtures/provenance/vaultwarden.sigstore.json``)."""
    repository = f"{HOST}/{found['repository']}"
    return {
        "buildDefinition": {
            "buildType": BUILD_TYPE,
            "externalParameters": {
                "workflow": {
                    "ref": found["ref"],
                    "repository": repository,
                    "path": workflow_path(found),
                }
            },
            "internalParameters": {
                "github": {
                    "event_name": found["event_name"],
                    "repository_id": found["repository_id"],
                    "repository_owner_id": found["repository_owner_id"],
                    "runner_environment": found["runner_environment"],
                }
            },
            "resolvedDependencies": [
                {
                    "uri": f"git+{repository}@{found['ref']}",
                    "digest": {"gitCommit": found["sha"]},
                }
            ],
        },
        "runDetails": {
            "builder": {"id": f"{HOST}/{found['job_workflow_ref']}"},
            "metadata": {
                "invocationId": (
                    f"{repository}/actions/runs/{found['run_id']}/attempts/{found['run_attempt']}"
                )
            },
        },
    }


def fetch() -> str:
    """The run's OIDC token, from the endpoint the runner names. Nothing of the answer is shown."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    secret = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not url or not secret:
        raise RefusalError(
            "ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN are not set:"
            " the job needs id-token: write"
        )
    if not url.startswith("https://"):
        raise RefusalError("ACTIONS_ID_TOKEN_REQUEST_URL is not an https URL")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"bearer {secret}",
            "Accept": "application/json; api-version=2.0",
            "User-Agent": "gora-provenance-predicate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            answer = json.load(response)
    except (OSError, ValueError):
        raise RefusalError("the token endpoint did not answer with JSON") from None
    value = answer.get("value") if isinstance(answer, dict) else None
    if not isinstance(value, str) or not value:
        raise RefusalError("the token endpoint's answer holds no token")
    return value


def read_token(token_file: str | None) -> str:
    if token_file is None:
        return fetch()
    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        raise RefusalError("the token file cannot be read") from None


def run(token_file: str | None, output: str) -> None:
    found = claims(payload(read_token(token_file)))
    text = json.dumps(predicate(found), separators=(",", ":")) + "\n"
    try:
        Path(output).write_text(text, encoding="utf-8")
    except OSError:
        raise RefusalError("the output file cannot be written") from None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write the provenance predicate of this run.")
    parser.add_argument("--token-file", help="read the token from this file, for a test")
    parser.add_argument("output", help="the file to write the predicate to")
    arguments = parser.parse_args(argv)
    try:
        run(arguments.token_file, arguments.output)
    except RefusalError as refusal:
        sys.stderr.write(f"predicate: {refusal}\n")
        return 1
    except Exception:
        # Whatever it is, the text of an exception may quote the token: show none of it.
        sys.stderr.write("predicate: failed\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""A pull request body is the facts of the change, fit to be the body of the squash commit
(ADR-022 section 2).

The squash commit takes the pull request body as its body, GORA reads that body when it computes
versions, and a merged body cannot change. ``scripts/gate/pr-body.sh`` refuses a body over 4096
bytes (Dependabot's is exempt from the size by actor, its login and account type, never by text),
a line that opens a fenced block or holds ``<details``, the prefix of a token, a path or an
address of the machine that wrote the body, and a control character other than tab, carriage
return and line feed; it names the line and the rule, and never prints the body.
``.github/workflows/pr-title.yml`` runs it in the job ``pr-title``, after the title check.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gate" / "pr-body.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-title.yml"

LIMIT = 4096
PERSON = ("zuwarskej", "User")
DEPENDABOT = ("dependabot[bot]", "Bot")

FACTS = "Refuse an empty stage.\n\nThe stage loop took an empty name for the last stage.\n"


def built(*parts: str) -> str:
    """A shape put together here, so that none of them is a literal in the repository: the
    workspace scan refuses a token's, a machine path's and an address's."""
    return "".join(parts)


def address(*octets: str) -> str:
    return ".".join(octets)


def check(*args: str) -> tuple[int, str]:
    """The validator, run as CI runs it: its exit code and its output."""
    done = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode, done.stdout + done.stderr


def check_body(body: str, number: int = 94, author: tuple[str, str] = PERSON) -> tuple[int, str]:
    return check(body, str(number), *author)


def at_line(text: str, number: int = 3, eol: str = "\n") -> str:
    """A body whose line ``number`` is ``text``, and whose other lines conform."""
    lines = ["Refuse an empty stage."] * (number - 1) + [text, "The end."]
    return eol.join(lines)


LOOPBACK = address("127", "0", "0", "1")

CONFORMING = {
    "empty": "",
    "facts": FACTS,
    "facts-with-crlf": FACTS.replace("\n", "\r\n"),
    "a-tab": "Refuse\tan empty stage",
    "a-lone-carriage-return": "Refuse an empty\rstage",
    "no-trailing-newline": "Refuse an empty stage.",
    "a-breaking-change-footer": FACTS + "\nBREAKING CHANGE: stage names are lower case\n",
    "limit-in-ascii": "x" * LIMIT,
    "limit-in-two-byte-characters": "é" * (LIMIT // 2),
    "fence-indented-by-four-spaces": "Refuse:\n\n    " + "`" * 3 + "\n    code\n    " + "`" * 3,
    "tilde-fence-indented-by-four-spaces": "    " + "~" * 3 + "\n    code",
    "fence-indented-by-a-tab": "\t" + "`" * 3 + "\n\tcode",
    "fence-inside-a-line": "Refuse " + "`" * 3 + "an empty stage" + "`" * 3 + " and " + "~" * 3,
    "two-backticks": "``code``",
    "details-as-a-word": "The details of the stage are in the model.",
    "another-tag": "<detail> and <summary> and <detailed>",
    "a-version": "Bump uv from 0.12.22 to 0.12.23 and python to 3.12.7.",
    "five-numbers-with-dots": "Release " + address("1", "2", "3", "4", "5") + " is old.",
    "three-numbers-with-dots": "Release " + address("1", "2", "3") + " is old.",
    "an-octet-over-255": "Not an address: " + address("256", "1", "1", "1"),
    "a-digit-in-front-of-an-address": "Not an address: 9" + address("10", "0", "0", "1"),
    "the-loopback-address": "The server listens on " + LOOPBACK + ".",
    "the-loopback-address-and-a-port": "Listens on " + LOOPBACK + ":8080 only.",
    "the-loopback-address-twice": LOOPBACK + " and " + LOOPBACK,
    "a-home-directory-without-a-path": "Reads the Users table and " + built("/", "Users") + ".",
    "another-path": "Reads /usr/local/bin and ~/work and /homework/x.",
    "the-prefix-of-a-github-token-without-its-underscore": built("gh", "p", "a"),
    "the-prefix-of-a-github-token-and-nothing": "Starts with "
    + built("gh", "p", "_")
    + " then ends.",
    "github-pat-without-its-underscore": built("github", "_pat") + " is a prefix.",
    "a-slack-prefix-of-another-kind": built("xo", "xz", "-", "1") + " and " + built("xapp", "1"),
    "an-access-key-id-one-character-short": built("AKI", "A", "A" * 15),
    "an-access-key-id-in-lower-case": built("aki", "a", "a" * 16),
    "arn-of-another-partition": built("ar", "n", ":", "gcp"),
}


@pytest.mark.parametrize("author", [PERSON, DEPENDABOT], ids=["person", "dependabot"])
@pytest.mark.parametrize("body", CONFORMING.values(), ids=CONFORMING.keys())
def test_a_body_of_facts_passes(body: str, author: tuple[str, str]) -> None:
    code, output = check_body(body, author=author)
    assert code == 0, output
    assert "conforms" in output


GATE_OUTPUT = {
    "backtick-fence": "`" * 3,
    "backtick-fence-with-a-language": "`" * 3 + "bash",
    "four-backticks": "`" * 4,
    "tilde-fence": "~" * 3,
    "fence-indented-by-one-space": " " + "`" * 3,
    "fence-indented-by-three-spaces": "   " + "~" * 3 + "text",
    "details": "<details>",
    "details-with-an-attribute": "<details open>",
    "details-in-upper-case": "<DETAILS>",
    "details-in-a-line": "Facts <details><summary>output</summary>",
}
TOKEN_SHAPES = {
    "github-personal": built("gh", "p", "_", "a"),
    "github-oauth": built("gh", "o", "_", "a"),
    "github-user-to-server": built("gh", "u", "_", "A"),
    "github-server-to-server": built("gh", "s", "_", "0"),
    "github-refresh": built("gh", "r", "_", "z"),
    "github-fine-grained": built("github", "_pat", "_", "11A"),
    "slack-bot": built("xo", "xb", "-", "1"),
    "slack-access": built("xo", "xa", "-", "1"),
    "slack-user": built("xo", "xp", "-", "1"),
    "slack-app-level": built("xapp", "-", "1"),
    "aws-access-key-id": built("AK", "IA", "A" * 16),
    "aws-temporary-access-key-id": built("AS", "IA", "0" * 16),
    "aws-access-key-id-longer": built("AK", "IA", "B" * 20),
    "aws-resource-name": built("ar", "n", ":", "aws", ":", "iam"),
    "token-in-a-sentence": "Set the variable to " + built("gh", "p", "_", "a", "b") + " to run.",
}
MACHINE = {
    "a-home-directory-of-macos": built("/", "Users", "/", "someone"),
    "a-home-directory-of-linux": built("/", "home", "/", "someone"),
    "a-private-directory": built("/", "private", "/", "tmp"),
    "a-path-in-a-sentence": "Ran in "
    + built("/", "Users", "/", "someone", "/", "work")
    + " today.",
    "an-address": address("10", "0", "0", "1"),
    "an-address-at-the-end-of-a-sentence": "Seen at " + address("192", "168", "1", "20") + ".",
    "an-address-in-brackets": "[" + address("0", "0", "0", "0") + "]",
    "an-address-with-a-port": address("203", "0", "113", "9") + ":443",
    "an-address-after-the-loopback": LOOPBACK + " and " + address("172", "16", "0", "5"),
    "an-address-before-the-loopback": address("8", "8", "8", "8") + "," + LOOPBACK,
    "the-loopback-address-with-a-longer-last-octet": address("127", "0", "0", "10"),
    "an-address-of-the-loopback-network": address("127", "0", "0", "2"),
    "an-address-with-255": address("255", "255", "255", "255"),
}
CONTROL = {
    "escape": "colour \x1b[31m red",
    "bell": "bell \x07",
    "backspace": "back\x08space",
    "vertical-tab": "vertical\x0btab",
    "form-feed": "form\x0cfeed",
    "delete": "delete\x7f",
    "start-of-heading": "\x01",
}
REFUSED = (
    [("gate-output", text) for text in GATE_OUTPUT.values()]
    + [("token-shape", text) for text in TOKEN_SHAPES.values()]
    + [("machine", text) for text in MACHINE.values()]
    + [("control", text) for text in CONTROL.values()]
)
REFUSED_IDS = [*GATE_OUTPUT, *TOKEN_SHAPES, *MACHINE, *CONTROL]


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("author", [PERSON, DEPENDABOT], ids=["person", "dependabot"])
@pytest.mark.parametrize(("rule", "text"), REFUSED, ids=REFUSED_IDS)
def test_a_body_with_a_refused_line_fails_naming_the_line_and_the_rule(
    rule: str, text: str, author: tuple[str, str], eol: str
) -> None:
    """For every author, Dependabot's included: only the size is exempt."""
    code, output = check_body(at_line(text, 3, eol), author=author)
    assert code == 1, output
    assert f"line 3 [{rule}]" in output
    assert "line 1 [" not in output
    assert "line 2 [" not in output


def test_each_refusal_is_named_and_a_line_can_hold_several() -> None:
    both = built("gh", "p", "_", "a") + " in " + built("/", "Users", "/", "x")
    body = "`" * 3 + "\nfine\n" + both + "\n" + address("10", "0", "0", "1") + "\x1b\n"
    code, output = check_body(body)
    assert code == 1, output
    for expected in (
        "line 1 [gate-output]",
        "line 3 [token-shape]",
        "line 3 [machine]",
        "line 4 [machine]",
        "line 4 [control]",
    ):
        assert expected in output
    assert "line 2 [" not in output


@pytest.mark.parametrize(
    ("body", "size"),
    [("x" * (LIMIT + 1), 4097), ("é" * (LIMIT // 2) + "x", 4097), ("x" * 10000, 10000)],
    ids=["one-over", "two-byte-characters-count-as-two", "far-over"],
)
def test_a_body_over_4096_bytes_fails(body: str, size: int) -> None:
    code, output = check_body(body)
    assert code == 1, output
    assert "[size]" in output
    assert f"{size} bytes" in output
    assert "line 1 [" not in output


def test_the_size_is_counted_in_bytes_with_the_line_ends() -> None:
    """A CRLF line end is two bytes of the body, as the squash commit takes it."""
    body = "\r\n".join(["x"] * 1366)  # 1366 + 1365 * 2 = 4096
    assert len(body.encode()) == LIMIT
    assert check_body(body)[0] == 0
    assert check_body(body + "x")[0] == 1


@pytest.mark.parametrize("body", ["x" * 10000, "line\n" * 5000, "é" * 5000])
def test_dependabot_s_body_is_exempt_from_the_size_by_actor(body: str) -> None:
    code, output = check_body(body, 80, DEPENDABOT)
    assert code == 0, output
    assert "no size limit" in output


@pytest.mark.parametrize(
    "author",
    [PERSON, ("dependabot[bot]", "User"), ("dependabot", "Bot"), ("gora-release[bot]", "Bot")],
    ids=["person", "dependabot-login-as-user", "dependabot-without-bot", "another-bot"],
)
def test_anyone_else_is_held_to_the_size(author: tuple[str, str]) -> None:
    code, output = check_body("x" * (LIMIT + 1), 80, author)
    assert code == 1, output
    assert "[size]" in output
    assert "no size limit" not in output


def test_dependabot_s_body_with_a_token_shape_is_refused() -> None:
    body = "x" * 10000 + "\n" + built("gh", "p", "_", "a")
    code, output = check_body(body, 80, DEPENDABOT)
    assert code == 1, output
    assert "line 2 [token-shape]" in output
    assert "[size]" not in output


def test_a_short_body_passes_by_its_size_not_by_the_exemption() -> None:
    code, output = check_body(FACTS, 80, PERSON)
    assert code == 0, output
    assert f"{len(FACTS)} bytes" in output  # the size check passed it, and said so
    assert "no size limit" not in output  # not the exemption
    code, output = check_body(FACTS, 80, DEPENDABOT)
    assert code == 0, output
    assert "no size limit" in output  # calibration: the exemption has its own words
    assert " bytes" not in output


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("body",),
        ("body", "94", "zuwarskej"),
        ("body", "94", "zuwarskej", "User", "x"),
        ("body", "", "zuwarskej", "User"),
        ("body", "0", "zuwarskej", "User"),
        ("body", "094", "zuwarskej", "User"),
        ("body", "-1", "zuwarskej", "User"),
        ("body", "9x", "zuwarskej", "User"),
    ],
    ids=[
        "none",
        "body-only",
        "three",
        "five",
        "empty",
        "zero",
        "leading-zero",
        "negative",
        "text",
    ],
)
def test_a_wrong_call_is_refused(args: tuple[str, ...]) -> None:
    assert check(*args)[0] == 64


MARKER = "text-of-the-body-that-must-not-come-back"


@pytest.mark.parametrize("author", [PERSON, DEPENDABOT], ids=["person", "dependabot"])
@pytest.mark.parametrize(
    "body",
    [
        MARKER,
        "::error::" + MARKER,
        "fine\n::add-mask::" + MARKER,
        "`" * 3 + "\n::warning::" + MARKER,
        "::error::" + MARKER + " " + built("gh", "p", "_", "a"),
        MARKER + " " + built("/", "Users", "/", "x") + " " + address("10", "0", "0", "1"),
        "::error::" + MARKER + "\x1b[31m",
        "::error::" + MARKER + "x" * LIMIT,
    ],
    ids=[
        "plain",
        "workflow-command",
        "mask-on-a-line",
        "fence-then-command",
        "command-and-token",
        "marker-with-machine",
        "command-with-control",
        "command-over-the-size",
    ],
)
def test_the_body_is_never_printed(body: str, author: tuple[str, str]) -> None:
    """A line of output that starts with ``::`` is a workflow command to the runner."""
    _, output = check_body(body, author=author)
    assert "::" not in output
    assert MARKER not in output


# --- the workflow runs it on every pull request, with the body as data ---------------------


def _workflow() -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document


def test_the_workflow_runs_when_a_body_can_change() -> None:
    document = _workflow()
    # YAML 1.1 reads the key `on` as true.
    on: dict[str, Any] = document["on"] if "on" in document else document[True]
    assert "edited" in on["pull_request"]["types"]  # an edit of the body is `edited`
    assert "synchronize" in on["pull_request"]["types"]
    assert document["name"] == "PR title and body"


def test_the_job_keeps_its_name_and_checks_the_body_after_the_title() -> None:
    """``pr-title`` is the context ``protect-main`` requires, in gora-infra: a new name is a
    change in two repositories."""
    (job,) = _workflow()["jobs"].values()
    assert job["name"] == "pr-title"
    runs = [step for step in job["steps"] if "run" in step]
    assert [step["run"].split()[0] for step in runs] == [
        "scripts/gate/pr-title.sh",
        "scripts/gate/pr-body.sh",
    ]


def test_the_workflow_hands_the_body_to_the_script_as_data() -> None:
    """The body, the number and the author reach the script through the environment, never in
    a script's text; the author is the event's, which a body cannot change."""
    (job,) = _workflow()["jobs"].values()
    runs = [step for step in job["steps"] if "run" in step]
    assert runs[1]["run"] == 'scripts/gate/pr-body.sh "$BODY" "$NUMBER" "$AUTHOR" "$AUTHOR_TYPE"'
    assert runs[1]["env"] == {
        "BODY": "${{ github.event.pull_request.body }}",
        "NUMBER": "${{ github.event.pull_request.number }}",
        "AUTHOR": "${{ github.event.pull_request.user.login }}",
        "AUTHOR_TYPE": "${{ github.event.pull_request.user.type }}",
    }
    assert [step["run"] for step in runs if "${{" in step["run"]] == []

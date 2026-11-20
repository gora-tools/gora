"""A pull request body is the facts of the change, fit to be the body of the squash commit
(ADR-022 section 2, which states the rules).

``scripts/gate/pr-body.sh`` reads a body line by line and refuses a body over 4096 bytes, gate
output, the prefix of a credential, a trace of the machine that wrote it and a control or hiding
character; it names the line and the rule, and never prints the body. A body whose author is
``dependabot[bot]`` with the account type ``Bot`` is GitHub's and is not checked.
``.github/workflows/pr-title.yml`` runs it in the job ``pr-title``, after the title check.

A token shape, an address and a character outside ASCII are put together here, from parts or
with ``chr``: none is a literal in the repository (the workspace scan refuses the first two).
"""

import re
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
    """A shape put together here, so that none of them is a literal in the repository."""
    return "".join(parts)


def address(*octets: str) -> str:
    return ".".join(octets)


def v6(*groups: str) -> str:
    """An IPv6 address from its groups; an empty group makes the ``::``."""
    return ":".join(groups)


def between(character: int) -> str:
    """A character outside ASCII, or a control one, inside a word."""
    return "word" + chr(character) + "word"


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
PIN = address("0", "11", "0", "1")  # the shellcheck-py pin: four numbers, the first 0
FENCE = "`" * 3
# The characters the control rule refuses besides the ASCII controls, as ADR-022 section 2 lists
# them: the C1 controls, the soft hyphen, the characters that hide or reorder text, and the tag
# characters.
REFUSED_RANGES = [
    (0x80, 0x9F),
    (0xAD, 0xAD),
    (0x200B, 0x200F),
    (0x2028, 0x202E),
    (0x2060, 0x206F),
    (0xFEFF, 0xFEFF),
    (0xE0000, 0xE007F),
]
# The characters next to those ranges, and outside them: a no-break space, the neighbours of the
# soft hyphen, a hair space, a hyphen, a narrow no-break space, the ends of the blocks 2060 and
# FEFF, and the characters on each side of the tag block.
OUTSIDE_THE_BLOCKS = [
    0xA0,
    0xAC,
    0xAE,
    0x200A,
    0x2010,
    0x2027,
    0x202F,
    0x205F,
    0x2057,
    0x2070,
    0xFEFE,
    0xFF00,
    0xDFFFF,
    0xE0080,
    0xE00FF,
    0xE0100,
]

CONFORMING = {
    "empty": "",
    "facts": FACTS,
    "facts-with-crlf": FACTS.replace("\n", "\r\n"),
    "a-tab": "Refuse\tan empty stage",
    "a-lone-carriage-return": "Refuse an empty\rstage",
    "no-trailing-newline": "Refuse an empty stage.",
    "a-breaking-change-footer": FACTS + "\nBREAKING CHANGE: stage names are lower case\n",
    "limit-in-ascii": "x" * LIMIT,
    "limit-in-two-byte-characters": chr(0xE9) * (LIMIT // 2),
    "an-accented-letter": "caf" + chr(0xE9),
    "an-emoji": "Done " + chr(0x1F600),
    "an-em-dash": "A fact " + chr(0x2014) + " and its cause",
    "a-no-break-space": "a" + chr(0xA0) + "b",
    "fence-indented-by-four-spaces": "Refuse:\n\n    " + FENCE + "\n    code\n    " + FENCE,
    "tilde-fence-indented-by-four-spaces": "    " + "~" * 3 + "\n    code",
    "fence-indented-by-a-tab": "\t" + FENCE + "\n\tcode",
    "fence-inside-a-line": "Refuse " + FENCE + "an empty stage" + FENCE + " and " + "~" * 3,
    "two-backticks": "``code``",
    "details-as-a-word": "The details of the stage are in the model.",
    "another-tag": "<detail> and <summary> and <detailed>",
    "a-version": "Bump uv from 0.12.22 to 0.12.23 and python to 3.12.7.",
    "a-version-of-four-numbers-that-starts-with-0": "Bump shellcheck-py to " + PIN + ".",
    "an-address-in-the-0-network": address("0", "0", "0", "0"),
    "a-version-after-a-letter": "Tag v" + address("1", "2", "3", "4") + " is old.",
    "five-numbers-with-dots": "Release " + address("1", "2", "3", "4", "5") + " is old.",
    "three-numbers-with-dots": "Release " + address("1", "2", "3") + " is old.",
    "an-octet-over-255": "Not an address: " + address("256", "1", "1", "1"),
    "a-digit-in-front-of-an-address": "Not an address: 9" + address("10", "0", "0", "1"),
    "the-loopback-address": "The server listens on " + LOOPBACK + ".",
    "the-loopback-address-and-a-port": "Listens on " + LOOPBACK + ":8080 only.",
    "the-loopback-address-twice": LOOPBACK + " and " + LOOPBACK,
    "the-loopback-network": address("127", "0", "0", "2")
    + " and "
    + address("127", "0", "0", "10"),
    "the-loopback-address-of-ipv6": "Binds " + v6("", "", "1") + " only.",
    "a-test-id": "x.py::test",
    "a-time": "At 12:34:56 sharp.",
    "a-word-before-a-hex-run-and-a-double-colon": "mod::beef",
    "a-hex-run-and-a-double-colon-then-a-word": "Names like ab::cdxyz are code.",
    "an-ipv6-shape-with-no-group-before-the-colons": "Binds " + v6("", "", "2") + " only.",
    "an-ipv6-shape-with-no-group-after-the-colons": "Seen at " + v6("fe80", "", "") + ".",
    "a-python-slice": "Use x[::2] to skip every other item.",
    "a-home-directory-without-a-path": "Reads the Users table and " + built("/", "Users") + ".",
    "a-temporary-directory-without-a-slash": "Cleans `" + built("/", "tmp") + "` first.",
    "another-path": "Reads /usr/local/bin and ~/work and /homework/x.",
    "the-prefix-of-a-github-token-without-its-underscore": built("gh", "p", "a"),
    "the-prefix-of-a-github-token-and-nothing": "Starts with "
    + built("gh", "p", "_")
    + " then ends.",
    "github-pat-without-its-underscore": built("github", "_pat") + " is a prefix.",
    "github-pat-in-prose": "a `" + built("github", "_pat", "_") + "` prefix",
    "a-word-that-ends-in-a-prefix": built("hi", "gh", "s_", "ok"),
    "a-slack-prefix-of-another-kind": built("xo", "xz", "-", "1") + " and " + built("xapp", "1"),
    "an-access-key-id-one-character-short": built("AKI", "A", "A" * 15),
    "an-access-key-id-in-lower-case": built("aki", "a", "a" * 16),
    "arn-of-another-partition": built("ar", "n", ":", "gcp"),
    "arn-prefix-without-its-colon": built("ar", "n", ":", "aws") + " alone",
    "a-certificate-header": built("-" * 5, "BEGIN CERTIFICATE"),
    **{f"outside-the-hiding-blocks-{code:x}": between(code) for code in OUTSIDE_THE_BLOCKS},
}


@pytest.mark.parametrize("body", CONFORMING.values(), ids=CONFORMING.keys())
def test_a_body_of_facts_passes(body: str) -> None:
    code, output = check_body(body)
    assert code == 0, output
    assert "conforms" in output


GATE_OUTPUT = {
    "backtick-fence": FENCE,
    "backtick-fence-with-a-language": FENCE + "bash",
    "four-backticks": "`" * 4,
    "tilde-fence": "~" * 3,
    "fence-indented-by-one-space": " " + FENCE,
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
    "aws-resource-name-of-another-partition": built("ar", "n", ":", "aws", "-cn", ":", "iam"),
    "private-key": built("-" * 5, "BEGIN ", "RSA ", "PRIVATE", " KEY"),
    "private-key-without-a-type": built("-" * 5, "BEGIN ", "PRIVATE", " KEY"),
    "private-key-of-openssh": built("-" * 5, "BEGIN ", "OPENSSH ", "PRIVATE", " KEY"),
    "token-in-a-sentence": "Set the variable to " + built("gh", "p", "_", "a", "b") + " to run.",
    "token-after-an-equals-sign": "token=" + built("gh", "p", "_", "a"),
    "token-in-backticks": "`" + built("gh", "s", "_", "a") + "`",
}
MACHINE = {
    "a-home-directory-of-macos": built("/", "Users", "/", "someone"),
    "a-home-directory-of-linux": built("/", "home", "/", "someone"),
    "the-home-directory-of-root": built("/", "root", "/", "x"),
    "a-private-directory": built("/", "private", "/", "tmp"),
    "a-temporary-directory": built("/", "tmp", "/", "x"),
    "a-temporary-directory-of-macos": built("/", "var", "/", "folders", "/", "x"),
    "a-path-in-a-sentence": "Ran in "
    + built("/", "Users", "/", "someone", "/", "work")
    + " today.",
    "an-address": address("10", "0", "0", "1"),
    "a-public-address": address("8", "8", "8", "8"),
    "an-address-and-a-port": address("192", "168", "1", "1") + ":8080",
    "an-address-at-the-end-of-a-sentence": "Seen at " + address("192", "168", "1", "20") + ".",
    "an-address-in-brackets": "[" + address("8", "8", "4", "4") + "]",
    "an-address-after-an-equals-sign": "host=" + address("10", "1", "2", "3"),
    "an-address-in-a-url": "http://" + address("10", "1", "2", "3") + "/x",
    "an-address-with-a-port": address("203", "0", "113", "9") + ":443",
    "an-address-after-the-loopback": LOOPBACK + " and " + address("172", "16", "0", "5"),
    "an-address-before-the-loopback": address("8", "8", "8", "8") + "," + LOOPBACK,
    "an-address-with-255": address("255", "255", "255", "255"),
    "an-address-of-the-lowest-first-octet": address("1", "0", "0", "1"),
    "an-address-just-below-127": address("126", "1", "1", "1"),
    "an-address-just-above-127": address("128", "1", "1", "1"),
    "an-address-of-a-lower-hundred": address("100", "64", "0", "1"),
    "an-address-of-the-110s": address("110", "1", "1", "1"),
    "an-address-of-the-120s": address("120", "1", "1", "1"),
    "an-address-of-the-130s": address("139", "1", "1", "1"),
    "an-ipv6-address": v6("fe80", "", "1"),
    "an-ipv6-address-with-two-groups-before-the-colons": v6("2001", "db8", "", "1"),
    "an-ipv6-address-in-full": v6("2001", "db8", "0", "0", "0", "0", "0", "1"),
    "an-ipv6-address-in-brackets": "[" + v6("2001", "db8", "", "1") + "]:443",
    "an-ipv6-address-in-a-sentence": "Seen at " + v6("fe80", "", "a1") + ".",
}
CONTROL = {
    "escape": "colour \x1b[31m red",
    "bell": "bell \x07",
    "backspace": "back\x08space",
    "vertical-tab": "vertical\x0btab",
    "form-feed": "form\x0cfeed",
    "delete": "delete\x7f",
    "start-of-heading": "\x01",
    "the-first-c1-control": between(0x80),
    "next-line": between(0x85),
    "the-last-c1-control": between(0x9F),
    "soft-hyphen": between(0xAD),
    "zero-width-space": between(0x200B),
    "zero-width-non-joiner": between(0x200C),
    "zero-width-joiner": between(0x200D),
    "left-to-right-mark": between(0x200E),
    "right-to-left-mark": between(0x200F),
    "line-separator": between(0x2028),
    "paragraph-separator": between(0x2029),
    "left-to-right-embedding": between(0x202A),
    "right-to-left-override": between(0x202E),
    "word-joiner": between(0x2060),
    "first-isolate": between(0x2066),
    "the-last-of-the-block-2060": between(0x206F),
    "byte-order-mark": between(0xFEFF),
    "the-first-tag-character": between(0xE0000),
    "tag-language-tag": between(0xE0001),
    "tag-latin-capital-letter-a": between(0xE0041),
    "the-last-tag-character": between(0xE007F),
}
REFUSED = (
    [("gate-output", text) for text in GATE_OUTPUT.values()]
    + [("token-shape", text) for text in TOKEN_SHAPES.values()]
    + [("machine", text) for text in MACHINE.values()]
    + [("control", text) for text in CONTROL.values()]
)
REFUSED_IDS = [*GATE_OUTPUT, *TOKEN_SHAPES, *MACHINE, *CONTROL]


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize(("rule", "text"), REFUSED, ids=REFUSED_IDS)
def test_a_body_with_a_refused_line_fails_naming_the_line_and_the_rule(
    rule: str, text: str, eol: str
) -> None:
    code, output = check_body(at_line(text, 3, eol))
    assert code == 1, output
    assert f"line 3 [{rule}]" in output
    assert "line 1 [" not in output
    assert "line 2 [" not in output


def test_exactly_the_listed_characters_are_refused_as_control() -> None:
    """Every character of every range, each on a line of its own, is refused; the characters
    next to the ranges are not."""
    refused = [code for low, high in REFUSED_RANGES for code in range(low, high + 1)]
    code, output = check_body("\n".join(between(character) for character in refused))
    assert code == 1, output
    named = {int(number) for number in re.findall(r"line ([0-9]+) \[control\]", output)}
    assert named == set(range(1, len(refused) + 1))
    code, output = check_body("\n".join(between(character) for character in OUTSIDE_THE_BLOCKS))
    assert code == 0, output


def test_each_refusal_is_named_and_a_line_can_hold_several() -> None:
    both = built("gh", "p", "_", "a") + " in " + built("/", "Users", "/", "x")
    body = FENCE + "\nfine\n" + both + "\n" + address("10", "0", "0", "1") + "\x1b\n"
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
    [
        ("x" * (LIMIT + 1), 4097),
        (chr(0xE9) * (LIMIT // 2) + "x", 4097),
        ("x" * 10000, 10000),
    ],
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


# --- Dependabot's body is GitHub's: it is not checked, by author and account type ----------

BODY_OF_DEPENDABOT = "<details>\n" + FENCE + "\n" + "x" * 10000 + "\n" + FENCE + "\n</details>"


def test_dependabot_s_body_with_details_a_fence_and_10000_bytes_passes() -> None:
    code, output = check_body(BODY_OF_DEPENDABOT, 80, DEPENDABOT)
    assert code == 0, output
    assert "GitHub's" in output
    assert "not checked" in output


@pytest.mark.parametrize(("rule", "text"), REFUSED, ids=REFUSED_IDS)
def test_nothing_in_dependabot_s_body_is_checked(rule: str, text: str) -> None:
    code, output = check_body(at_line(text), 80, DEPENDABOT)
    assert code == 0, f"{rule}: {output}"
    assert "not checked" in output


@pytest.mark.parametrize(
    "author",
    [PERSON, ("dependabot[bot]", "User"), ("dependabot", "Bot"), ("gora-release[bot]", "Bot")],
    ids=["person", "dependabot-login-as-user", "dependabot-without-bot", "another-bot"],
)
def test_anyone_else_is_checked(author: tuple[str, str]) -> None:
    """A person whose login reads ``dependabot[bot]`` is a ``User``: the type is the event's."""
    code, output = check_body(BODY_OF_DEPENDABOT, 80, author)
    assert code == 1, output
    assert "[size]" in output
    assert "line 1 [gate-output]" in output
    assert "not checked" not in output


def test_a_short_body_is_checked_and_says_its_size_and_dependabot_s_is_not() -> None:
    code, output = check_body(FACTS, 80, PERSON)
    assert code == 0, output
    assert f"{len(FACTS)} bytes" in output  # the checks passed it, and said so
    assert "not checked" not in output
    code, output = check_body(FACTS, 80, DEPENDABOT)
    assert code == 0, output
    assert "not checked" in output  # calibration: the exemption has its own words
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
        FENCE + "\n::warning::" + MARKER,
        "::error::" + MARKER + " " + built("gh", "p", "_", "a"),
        MARKER + " " + built("/", "Users", "/", "x") + " " + address("10", "0", "0", "1"),
        "::error::" + MARKER + " " + v6("fe80", "", "1"),
        "::error::" + MARKER + "\x1b[31m",
        "::error::" + MARKER + chr(0x202E),
        "::error::" + MARKER + "x" * LIMIT,
    ],
    ids=[
        "plain",
        "workflow-command",
        "mask-on-a-line",
        "fence-then-command",
        "command-and-token",
        "marker-with-machine",
        "command-and-ipv6",
        "command-with-control",
        "command-with-override",
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


def test_the_body_is_checked_when_the_title_fails() -> None:
    """A failed title step must not hide the body's result; the job still fails, so the required
    check stays red. A cancelled run checks nothing."""
    (job,) = _workflow()["jobs"].values()
    runs = [step for step in job["steps"] if "run" in step]
    assert "if" not in runs[0]
    assert runs[1]["if"] == "${{ !cancelled() }}"


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

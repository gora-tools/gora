"""The identifier grammar (ADR-003): what each grammar accepts and refuses, at its bounds.

Expectations are written out literally, never derived from the code under test. A value is
valid when the grammar's checker says so; the model types, the CLI callbacks and the
published schema all use the same checker or the same pattern.
"""

import random
import re
import string
from collections.abc import Callable

import pytest

from gora.domain.release.identifiers import (
    BRANCH,
    DIGEST,
    GRAMMARS,
    IMAGE,
    RELEASE_LINE,
    REPO,
    REPO_DOT_NAME,
    STAGE,
    VERSION,
    VERSION_BUILD_METADATA,
    VERSION_LOCK,
    Grammar,
    release_line_of,
)

HEX64 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# OCI distribution-spec tag grammar: a GORA version becomes an image tag.
OCI_TAG = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}")
# The shape Slack renders as an emoji in a fallback text (ADR-015 residual).
EMOJI_SHAPE = re.compile(r":[a-z0-9_+'.-]+:")

# Every character class a grammar might be asked about: what a terminal acts on, what
# markup reads, what a URL or a path treats specially, and what looks like ASCII but is not.
FORBIDDEN_ANYWHERE = [
    " ", "\t", "\n", "\r", "\x00", "\x1b", "\x7f", "\x85", "\u200b", "\u202e", "\ufeff",
    "[", "]", "<", ">", "*", "`", "~", "&", "?", "#", "%", "\\", "'", '"', "|", "!", "$",
    "\u0661",  # ARABIC-INDIC DIGIT ONE: a digit to Python's \d, not to ECMA-262's
    "\uff41",  # FULLWIDTH LATIN SMALL LETTER A
    "\u0430",  # CYRILLIC SMALL LETTER A
    "\U0001f600",
]  # fmt: skip


# --- version --------------------------------------------------------------------------------

VALID_VERSIONS = [
    "0.0.0",
    "0.0.4",
    "1.2.3",
    "10.20.30",
    "1.1.2-prerelease",
    "1.0.0-alpha",
    "1.0.0-alpha.beta",
    "1.0.0-alpha.1",
    "1.0.0-alpha.0valid",
    "1.0.0-alpha-a.b-c-somethinglong",
    "1.0.0-rc.1",
    "1.2.3-beta",
    "10.2.3-DEV-SNAPSHOT",
    "1.2.3-SNAPSHOT-123",
    "1.0.0-0A.is.legal",
    "1.0.0--",
    "1.0.0-x-y-z.--",
    "2.0.0-rc.1",
    "999999999.999999999.999999999",
    "1.2.0-x-y.1",
    # "lock" as an identifier is fine; only a version ending in ".lock" is not a git ref.
    "1.2.0-lock",
    "1.2.0-a.lockx",
    "1.2.0-a.lock.1",
    "1.2.0-a.a-lock",
]

INVALID_VERSIONS = [
    "",
    "1",
    "1.2",
    "1.2.3.4",
    "v1.2.3",
    "01.1.1",
    "1.01.1",
    "1.1.01",
    "1.2.3-0123",
    "1.2.3-0123.0123",
    "1.0.0-alpha_beta",
    "1.0.0-alpha..",
    "1.0.0-alpha..1",
    "1.0.0-",
    "1.0.0-.1",
    "-1.0.0",
    "1.2.3-rc.1\n",
    "1.2.3 ",
    " 1.2.3",
    "\u0661.0.0",
    # Build metadata: valid SemVer, not a GORA version.
    "1.0.0+b1",
    "1.0.0-rc.1+build.1",
    "1.0.0+0.build.1-rc.10000aaa-kk-0.1",
    "1.0.0+",
    # Ends in ".lock": git refuses the tag v<version> and the promotion branch.
    "1.2.0-a.lock",
    "1.2.0-rc.1.lock",
    "1.0.0-0.lock",
]


@pytest.mark.parametrize("value", VALID_VERSIONS)
def test_version_accepts_semver_without_build_metadata(value: str) -> None:
    assert VERSION.problem(value) is None


@pytest.mark.parametrize("value", INVALID_VERSIONS)
def test_version_refuses(value: str) -> None:
    assert VERSION.problem(value) is not None


@pytest.mark.parametrize("value", ["1.0.0+b1", "1.0.0-rc.1+build.1", "1.0.0+"])
def test_build_metadata_is_named_as_the_reason(value: str) -> None:
    assert VERSION.problem(value) == VERSION_BUILD_METADATA


@pytest.mark.parametrize("value", ["1.2.0-a.lock", "1.2.0-rc.1.lock", "1.0.0-0.lock"])
def test_a_version_ending_in_dot_lock_is_refused_with_its_reason(value: str) -> None:
    """git refuses the tag v<version> and the promotion branch that would end in .lock."""
    assert VERSION.problem(value) == VERSION_LOCK


def test_a_version_that_is_wrong_before_its_plus_gets_the_general_rule() -> None:
    assert VERSION.problem("01.0.0+b1") == VERSION.rule


def test_version_length_bound() -> None:
    longest = "1.0.0-" + "a" * 122
    assert len(longest) == 128
    assert VERSION.problem(longest) is None
    assert VERSION.problem(longest + "a") is not None


@pytest.mark.parametrize("char", FORBIDDEN_ANYWHERE)
def test_version_refuses_every_forbidden_character(char: str) -> None:
    for at in ("1.0.0-rc" + char, "1.0.0-" + char + "rc", char + "1.0.0"):
        assert VERSION.problem(at) is not None, repr(at)


def _random_version(rng: random.Random) -> str:
    def number() -> str:
        return str(rng.choice([0, rng.randint(1, 9), rng.randint(10, 10**9)]))

    def identifier() -> str:
        if rng.random() < 0.4:
            return number()
        alphabet = string.ascii_letters + string.digits + "-"
        body = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        return body + rng.choice(string.ascii_letters + "-")

    core = f"{number()}.{number()}.{number()}"
    if rng.random() < 0.3:
        return core
    return core + "-" + ".".join(identifier() for _ in range(rng.randint(1, 4)))


def _generated(make: Callable[[random.Random], str], n: int = 2000) -> list[str]:
    rng = random.Random(20261002)
    return [make(rng) for _ in range(n)]


def test_every_accepted_version_is_a_valid_oci_tag() -> None:
    accepted = [v for v in VALID_VERSIONS + _generated(_random_version) if VERSION.is_valid(v)]
    assert len(accepted) > 1500  # calibration: the generator produces valid versions
    assert [v for v in accepted if OCI_TAG.fullmatch(v) is None] == []


def test_the_oci_tag_grammar_refuses_a_plus() -> None:
    # Calibration of the oracle above: SemVer build metadata is not a tag.
    assert OCI_TAG.fullmatch("1.0.0+b1") is None


# --- release line ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0.0.0", "1.2.0", "10.20.30", "0.2.0"])
def test_release_line_accepts_a_core(value: str) -> None:
    assert RELEASE_LINE.problem(value) is None


@pytest.mark.parametrize(
    "value",
    ["", "1.2", "1.2.0-rc.1", "1.2.0+b", "01.2.0", "1.02.0", "v1.2.0", "2026.Q2", "1.2.0\n"],
)
def test_release_line_refuses(value: str) -> None:
    assert RELEASE_LINE.problem(value) is not None


@pytest.mark.parametrize(
    ("version", "line"),
    [
        ("1.2.0-rc.3", "1.2.0"),
        ("1.2.0", "1.2.0"),
        ("1.2.0-x-y.1", "1.2.0"),  # a hyphen inside the pre-release does not move the cut
        ("10.0.0-0.1", "10.0.0"),
    ],
)
def test_release_line_of(version: str, line: str) -> None:
    assert release_line_of(version) == line


@pytest.mark.parametrize("version", ["1.2.0+b1", "1.2", "x"])
def test_release_line_of_refuses_what_is_not_a_version(version: str) -> None:
    with pytest.raises(ValueError, match="not a version"):
        release_line_of(version)


# --- stage ----------------------------------------------------------------------------------

VALID_STAGES = ["dev", "prod", "a", "eu-west-1", "staging2", "a" * 63, "x-y", "a1"]
INVALID_STAGES = [
    "",
    "Dev",
    "DEV",
    "1dev",
    "-dev",
    "dev-",
    "dev_eu",
    "dev.eu",
    "dev/eu",
    "dev[eu]",
    "[/x]",
    "a" * 64,
    "dev\n",
    "d\u0435v",  # CYRILLIC SMALL LETTER IE
]


@pytest.mark.parametrize("value", VALID_STAGES)
def test_stage_accepts_a_dns_label(value: str) -> None:
    assert STAGE.problem(value) is None


@pytest.mark.parametrize("value", INVALID_STAGES)
def test_stage_refuses(value: str) -> None:
    assert STAGE.problem(value) is not None


@pytest.mark.parametrize("char", [*FORBIDDEN_ANYWHERE, ":", "/", ".", "_", "+", "@", "A"])
def test_stage_refuses_every_forbidden_character(char: str) -> None:
    assert STAGE.problem(f"de{char}v") is not None


# --- digest ---------------------------------------------------------------------------------


def test_digest_accepts_sha256_and_64_lowercase_hex() -> None:
    assert DIGEST.problem(f"sha256:{HEX64}") is None


@pytest.mark.parametrize(
    "value",
    [
        f"sha256:{HEX64[:63]}",  # 63 hex digits
        f"sha256:{HEX64}a",  # 65
        f"sha256:{HEX64} ",  # trailing junk
        f"sha256:{HEX64}\n",  # trailing newline
        f"sha256:{HEX64}x",
        f"sha256:{HEX64.upper()}",  # uppercase hex
        f"sha256:{HEX64[:-1]}A",
        f"SHA256:{HEX64}",
        f"sha512:{HEX64}",
        f"sha256:{HEX64[:-1]}g",
        HEX64,
        f" sha256:{HEX64}",
        f"xsha256:{HEX64}",
        "sha256:",
        "sha256:abc",
        "",
    ],
    ids=[
        "63-hex",
        "65-hex",
        "trailing-space",
        "trailing-newline",
        "trailing-letter",
        "uppercase",
        "one-uppercase",
        "uppercase-algorithm",
        "other-algorithm",
        "non-hex",
        "no-algorithm",
        "leading-space",
        "leading-junk",
        "empty-hex",
        "short",
        "empty",
    ],
)
def test_digest_refuses(value: str) -> None:
    assert DIGEST.problem(value) is not None


# --- image ----------------------------------------------------------------------------------

VALID_IMAGES = [
    "ghcr.io/zuwarskejes-labs/gora",
    "ghcr.io/test/service",
    "docker.io/library/nginx",
    "localhost/x",
    "localhost:5000/x",
    "registry:5000/a/b",
    "REGISTRY.example.com/a",  # a host is not a path component: distribution allows case
    "a.b/c__d",
    "a.b/c---d",
    "a.b/c.d_e",
    "a-b.example.com:443/team/sub/service",
    "1.2.3.4:5000/x",
]
INVALID_IMAGES = [
    "",
    "nginx",
    "library/nginx",  # no registry host: an implicit docker.io is not allowed
    "team/service",
    "ghcr.io",
    "ghcr.io/",
    "ghcr.io//x",
    "ghcr.io/x/",
    "ghcr.io/Org/x",  # uppercase path component
    "ghcr.io/o/s:1.0.0",  # a tag
    "ghcr.io/o/s@sha256:" + HEX64,  # a digest
    "https://ghcr.io/o/s",
    "ghcr.io/o/-s",
    "ghcr.io/o/s-",
    "ghcr.io/o/s.",
    "ghcr.io/o/s___t",
    "ghcr.io/o/s..t",
    "-ghcr.io/o/s",
    "ghcr-.io/o/s",
    "ghcr.io:/o/s",
    "ghcr.io:x/o/s",
    "[::1]:5000/x",  # IPv6 hosts are not part of GORA's grammar
    "LOCALHOST/x",
    "ghcr.io/o/s\n",
]


@pytest.mark.parametrize("value", VALID_IMAGES)
def test_image_accepts_a_fully_qualified_repository(value: str) -> None:
    assert IMAGE.problem(value) is None


@pytest.mark.parametrize("value", INVALID_IMAGES)
def test_image_refuses(value: str) -> None:
    assert IMAGE.problem(value) is not None


def test_image_length_bound() -> None:
    prefix = "ghcr.io/"
    longest = prefix + "a" * (255 - len(prefix))
    assert IMAGE.problem(longest) is None
    assert IMAGE.problem(longest + "a") is not None


@pytest.mark.parametrize("char", [*FORBIDDEN_ANYWHERE, ":", "@", "+", "A"])
def test_image_refuses_every_forbidden_character_in_the_path(char: str) -> None:
    assert IMAGE.problem(f"ghcr.io/o/s{char}x") is not None


# --- repository -----------------------------------------------------------------------------

VALID_REPOS = [
    "o/r",
    "zuwarskejes-labs/gora",
    "Owner-1/repo.name_x-y",
    "a" * 39 + "/" + "b" * 100,
    "o/.github",
    "o/..x",
]
INVALID_REPOS = [
    "",
    "o",
    "/r",
    "o/",
    "o/r/x",
    "o//r",
    "-o/r",
    "a" * 40 + "/r",
    "o/" + "b" * 101,
    "o/.",
    "o/..",
    "o_x/r",
    "o.x/r",
    "o/r?x=1",
    "o/r#x",
    "o/r%2e",
    "o\x01/r",
    "o/r\n",
]


@pytest.mark.parametrize("value", VALID_REPOS)
def test_repo_accepts_owner_slash_name(value: str) -> None:
    assert REPO.problem(value) is None


@pytest.mark.parametrize("value", INVALID_REPOS)
def test_repo_refuses(value: str) -> None:
    assert REPO.problem(value) is not None


# --- branch ---------------------------------------------------------------------------------

VALID_BRANCHES = [
    "main",
    "release/1.2",
    "feat/manifest-contract",
    "gora/promote-dev-1.2.0-rc.1",
    "a_b",
    "x" * 255,
    "v1.2.3",
    "a.lockx",
    "a-x",
    "head",
    "a/HEAD",
    "HEAD/a",
    "HEADx",
]
INVALID_BRANCHES = [
    "",
    "a..b",
    "a//b",
    "/main",
    "main/",
    "main.",
    "main.lock",
    "a.lock/b",
    ".hidden",
    "a/.hidden",
    "x" * 256,
    "a b",
    "a~b",
    "a^b",
    "a:b",
    "a?b",
    "a*b",
    "a[b",
    "a\\b",
    "a@{b",
    "a\x01b",
    "main\n",
    # git check-ref-format --branch: no leading dash, not HEAD; "@" is refused by the charset.
    "-x",
    "--x",
    "-",
    "HEAD",
    "@",
]


@pytest.mark.parametrize("value", VALID_BRANCHES)
def test_branch_accepts_a_git_branch_name(value: str) -> None:
    assert BRANCH.problem(value) is None


@pytest.mark.parametrize("value", INVALID_BRANCHES)
def test_branch_refuses(value: str) -> None:
    assert BRANCH.problem(value) is not None


# --- every grammar --------------------------------------------------------------------------

SAMPLES: dict[str, tuple[list[str], list[str]]] = {
    VERSION.name: (VALID_VERSIONS, INVALID_VERSIONS),
    RELEASE_LINE.name: (["0.0.0", "1.2.0"], ["1.2", "1.2.0-rc.1"]),
    STAGE.name: (VALID_STAGES, INVALID_STAGES),
    DIGEST.name: ([f"sha256:{HEX64}"], [f"sha256:{HEX64[:63]}", f"sha256:{HEX64}a"]),
    IMAGE.name: (VALID_IMAGES, INVALID_IMAGES),
    REPO.name: (VALID_REPOS, INVALID_REPOS),
    BRANCH.name: (VALID_BRANCHES, INVALID_BRANCHES),
}


def test_every_grammar_has_samples() -> None:
    assert sorted(SAMPLES) == sorted(g.name for g in GRAMMARS)


# The only reasons a grammar gives: each is a constant, so none can carry the value.
REASONS = {VERSION.name: {VERSION_BUILD_METADATA, VERSION_LOCK}, REPO.name: {REPO_DOT_NAME}}


@pytest.mark.parametrize("grammar", GRAMMARS, ids=lambda g: g.name)
def test_a_refusal_is_one_of_the_grammar_s_constant_reasons(grammar: Grammar) -> None:
    valid, invalid = SAMPLES[grammar.name]
    reasons = {grammar.problem(value) for value in valid + invalid}
    assert reasons <= {None, grammar.rule, *REASONS.get(grammar.name, set())}
    assert grammar.rule in reasons  # calibration: the refused samples were refused


# Constructs outside the common subset of Python re and ECMA-262, or that mean different
# things in the two: named groups, lookbehind, \A \Z, possessive and atomic groups, the
# Unicode-dependent classes, and inline flags.
NOT_PORTABLE = re.compile(r"\(\?P|\(\?<|\(\?>|\\[AZzdDwWsSbB]|[*+?}][+]|\(\?[aiLmsux]")


@pytest.mark.parametrize("grammar", GRAMMARS, ids=lambda g: g.name)
def test_every_pattern_is_in_the_portable_subset_and_anchored(grammar: Grammar) -> None:
    assert NOT_PORTABLE.search(grammar.pattern) is None, grammar.pattern
    assert grammar.pattern.startswith("^") and grammar.pattern.endswith("$")
    re.compile(grammar.pattern)


def test_the_portability_check_registers_what_it_forbids() -> None:
    for construct in [r"(?P<x>a)", r"(?<=a)b", r"\d", r"a\Z", r"a++", r"(?>a)", r"(?i)a"]:
        assert NOT_PORTABLE.search(construct) is not None, construct


# --- no accepted value can form an emoji shortcode (ADR-015) --------------------------------


def _random_image(rng: random.Random) -> str:
    def label() -> str:
        return "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(5))

    host = rng.choice(["localhost", f"{label()}.io", f"{label()}:{rng.randint(1, 65535)}"])
    if rng.random() < 0.3:
        host += f":{rng.randint(1, 65535)}" if "." in host else ""
    sep = rng.choice([".", "_", "__", "-", "---"])
    path = "/".join(f"{label()}{sep}{label()}" for _ in range(rng.randint(1, 3)))
    return f"{host}/{path}"


def _random_from(alphabet: str, rng: random.Random, n: int = 20) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, n)))


GENERATORS: dict[str, Callable[[random.Random], str]] = {
    "version": _random_version,
    "image": _random_image,
    "digest": lambda rng: "sha256:" + "".join(rng.choice("0123456789abcdef") for _ in range(64)),
    "stage": lambda rng: (
        rng.choice(string.ascii_lowercase)
        + _random_from(string.ascii_lowercase + string.digits + "-", rng, 8)
    ),
    "branch": lambda rng: _random_from(string.ascii_letters + "._/-:", rng),
    "repo": lambda rng: _random_from("ab-", rng, 5) + "/" + _random_from("a._-:", rng, 8),
}


def _mutations(value: str, rng: random.Random) -> list[str]:
    """The value with a colon, or a colon-delimited word, put at random places."""
    out = []
    for insert in (":", ":x:", ":rocket:", "::", ":+1:", ":a.b:"):
        at = rng.randint(0, len(value))
        out.append(value[:at] + insert + value[at:])
    return out


def test_no_accepted_value_contains_an_emoji_shortcode() -> None:
    rng = random.Random(20261002)
    candidates = ["localhost:5000/x", "a.b:443/c", f"sha256:{HEX64}"]
    for make in GENERATORS.values():
        for _ in range(500):
            value = make(rng)
            candidates.append(value)
            candidates += _mutations(value, rng)
    accepted = [v for v in candidates for g in GRAMMARS if g.is_valid(v)]
    assert len(accepted) > 1000  # calibration: the property was tested on real values
    assert any(":" in v for v in accepted)  # values with a colon were among them
    assert [v for v in accepted if EMOJI_SHAPE.search(v)] == []


def test_the_shortcode_check_registers_one() -> None:
    assert EMOJI_SHAPE.search("a:rocket:b") is not None
    assert EMOJI_SHAPE.search("ghcr.io:5000/x") is None

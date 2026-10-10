"""The identifier grammar (ADR-003): what each grammar accepts and refuses, at its bounds.

Expectations are written out literally, never derived from the code under test. A value is
valid when the grammar's checker says so; the model types, the CLI callbacks and the
published schema all use the same checker or the same pattern.
"""

import random
import re
import string
import sys
import unicodedata
from collections.abc import Callable

import pytest

from gora.domain.release.identifiers import (
    BRANCH,
    DIGEST,
    FINAL_VERSION,
    GLOB_CLASS,
    GLOB_DOT_COMPONENT,
    GLOB_DOUBLE_STAR,
    GLOB_DOUBLE_STAR_TWICE,
    GLOB_EDGE_SLASH,
    GLOB_EMPTY_COMPONENT,
    GRAMMARS,
    IMAGE,
    INPUT_GLOB,
    NAME,
    OIDC_ISSUER,
    PRINCIPAL,
    RC_VERSION,
    RELEASE_LINE,
    REPO,
    REPO_DOT_NAME,
    REPORTER,
    SIGNER_IDENTITY,
    SOURCE_COMMIT,
    SOURCE_REPOSITORY,
    SOURCE_REPOSITORY_DOT_GIT,
    SOURCE_REPOSITORY_DOT_NAME,
    STAGE,
    URL_HOST_LENGTH,
    URL_PORT_RANGE,
    VERSION,
    VERSION_BUILD_METADATA,
    VERSION_LOCK,
    Grammar,
    release_line_of,
)
from gora.domain.release_repository.formats import (
    FORMATS,
    PUBLIC_KEY,
    REASON,
    REASON_ASTRAL,
    REASON_BMP,
    REASON_INVISIBLE,
    SBOM_VERSION,
    SBOM_VERSION_FLOOR,
    TAG_PREFIX,
    TIMESTAMP,
    TIMESTAMP_DATE,
)
from tests.unit.test_release_keys import ARMOR as KEY_ARMOR_FIXTURES
from tests.unit.test_release_keys import ED25519, P256, P256_OTHER
from tests.unit.test_release_keys import UNSUPPORTED as KEY_UNSUPPORTED_FIXTURES

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


# --- the identifiers of the release files (ADR-026) -----------------------------------------------

COMMIT = "9fceb02d0ae598e95dc970b74767f19372d61af8"

VALID_RC_VERSIONS = [
    "0.0.0-rc",
    "0.1.0-rc",
    "1.2.3-rc",
    "10.20.30-rc",
    "999999999.999999999.999999999-rc",
    "1" + "0" * 120 + ".0.0-rc",  # 128 characters
]
INVALID_RC_VERSIONS = [
    "",
    "1.2.3",
    "1.2.3-rc.1",
    "1.2.3-rc1",
    "1.2.3-RC",
    "1.2.3-beta",
    "1.2-rc",
    "01.2.3-rc",
    "1.02.3-rc",
    "v1.2.3-rc",
    "1.2.3-rc+b",
    "1.2.3-rc\n",
    " 1.2.3-rc",
    "1" + "0" * 121 + ".0.0-rc",  # 129 characters
]
VALID_FINAL_VERSIONS = ["0.0.0", "1.1.0", "10.20.30", "1" + "0" * 123 + ".0.0"]
INVALID_FINAL_VERSIONS = [
    "",
    "1.2",
    "1.2.3-rc",
    "1.2.3+b",
    "01.0.0",
    "v1.0.0",
    "1.0.0\n",
    "1.0.0.lock",
    "1" + "0" * 124 + ".0.0",  # 129 characters
]
VALID_PRINCIPALS = [
    "team/qa",
    "team/release-owners",
    "team/api_qa",
    "team/0x",
    "team/a-",
    "team/" + "a" * 100,
    "user/octocat",
    "user/a-b-c",
    "user/a1",
    "user/" + "a" * 39,
]
INVALID_PRINCIPALS = [
    "",
    "qa",
    "team/",
    "team/-qa",
    "team/_qa",
    "team/QA",
    "team/q a",
    "team/qa/x",
    "team/qa\n",
    "team/" + "a" * 101,
    "user/",
    "user/a--b",
    "user/-a",
    "user/a-",
    "user/a_b",
    "user/Octocat",
    "user/" + "a" * 40,
    "user/a@example.com",
    "app/x",
    "group/qa",
]
VALID_REPORTERS = ["app/argocd-notifications", "app/a", "app/1x", "app/a-", "app/" + "a" * 34]
INVALID_REPORTERS = [
    "",
    "app/",
    "app/-a",
    "app/A",
    "app/a_b",
    "app/a/b",
    "app/" + "a" * 35,
    "team/x",
    "argocd",
]
VALID_SOURCE_REPOSITORIES = [
    "github.com/acme/cally-api",
    "github.com/a/b",
    "github.com/a/.github",
    "github.com/a/..x",
    "github.com/a/x.gitx",
    "github.com/a-b/c_d.e",
    "github.com/" + "a" * 39 + "/" + "b" * 100,
]
INVALID_SOURCE_REPOSITORIES = [
    "",
    "github.com/a/.",
    "github.com/a/..",
    "github.com/a/x.git",
    "github.com/A/b",
    "github.com/a/B",
    "gitlab.com/a/b",
    "https://github.com/a/b",
    "github.com/a",
    "github.com/a/b/c",
    "github.com/-a/b",
    "github.com/a-/b",
    "github.com/a--b/c",
    "github.com/" + "a" * 40 + "/b",
    "github.com/a/" + "b" * 101,
    "github.com/a/b\n",
    "acme/cally-api",
]
VALID_SOURCE_COMMITS = [COMMIT, "0" * 40, "f" * 64]
INVALID_SOURCE_COMMITS = [
    "",
    "a" * 39,
    "a" * 41,
    "a" * 63,
    "a" * 65,
    "A" * 40,
    "g" * 40,
    COMMIT + "\n",
]
VALID_INPUT_GLOBS = [
    "src",
    "src/**",
    "**",
    "**/foo",
    "a/**/b",
    "*.py",
    "src/*.py",
    "pom.xml",
    "Dockerfile",
    ".hidden",
    "a?b",
    "[ab]",
    "[a-z]x",
    "[A-Z0-9._]",
    "[a-a]",
    "a@b+c=d,e~f%g#h",
    "db/**/V*__*.sql",
    "x" * 512,
]
INVALID_INPUT_GLOBS = [
    "",
    "a b",
    "a\\b",
    "a:b",
    "!src/**",
    "[!a]",
    "[^a]",
    "{a,b}",
    "a\n",
    "x" * 513,
    "/src",
    "src/",
    "a//b",
    ".",
    "a/./b",
    "a/../b",
    "a**",
    "**a",
    "***",
    "a/**/**/b",
    "]",
    "[]",
    "[a",
    "[a-]",
    "[-a]",
    "[--a]",
    "[a-c-e]",
    "[c-a]",
    "[a/b]",
]
GLOB_STRUCTURE = [
    ("/src", GLOB_EDGE_SLASH),
    ("src/", GLOB_EDGE_SLASH),
    ("/", GLOB_EDGE_SLASH),
    ("a//b", GLOB_EMPTY_COMPONENT),
    (".", GLOB_DOT_COMPONENT),
    ("..", GLOB_DOT_COMPONENT),
    ("a/./b", GLOB_DOT_COMPONENT),
    ("a/..", GLOB_DOT_COMPONENT),
    ("a**", GLOB_DOUBLE_STAR),
    ("**a", GLOB_DOUBLE_STAR),
    ("a/***/b", GLOB_DOUBLE_STAR),
    ("**/**", GLOB_DOUBLE_STAR_TWICE),
    ("a/**/**/b", GLOB_DOUBLE_STAR_TWICE),
    ("]", GLOB_CLASS),
    ("a]b", GLOB_CLASS),
    ("[", GLOB_CLASS),
    ("[ab", GLOB_CLASS),
    ("[a/b]", GLOB_CLASS),
    ("[]", GLOB_CLASS),
    ("[]a]", GLOB_CLASS),
    ("[a[b]", GLOB_CLASS),
    ("[-a]", GLOB_CLASS),
    ("[a-]", GLOB_CLASS),
    ("[--a]", GLOB_CLASS),
    ("[a-c-e]", GLOB_CLASS),
    ("[c-a]", GLOB_CLASS),
    ("[9-0]", GLOB_CLASS),
    ("[a-Z]", GLOB_CLASS),
    ("[A-z]", GLOB_CLASS),
    ("[a-9]", GLOB_CLASS),
    ("[@]", GLOB_CLASS),
    ("[*]", GLOB_CLASS),
    ("[?]", GLOB_CLASS),
]
HOST_253 = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])
HOST_254 = HOST_253 + "d"
VALID_OIDC_ISSUERS = [
    "https://token.actions.githubusercontent.com",
    "https://accounts.google.com",
    "https://oauth2.sigstore.dev/auth",
    "https://a",
    "https://a/",
    "https://a/b/c",
    "https://a/b/",
    "https://a:1",
    "https://a:65535/x",
    f"https://{HOST_253}",
]
INVALID_OIDC_ISSUERS = [
    "",
    "http://a",
    "https://",
    "https://A.com",
    "https://a:0",
    "https://a:01",
    "https://a:65536",
    "https://a:123456",
    "https://a//b",
    "https://a/b c",
    "https://-a",
    "https://a-",
    "https://a.",
    "https://a?x",
    "https://a#x",
    "https://user@a",
    "https://a/b:c",
    "https://a\n",
    f"https://{HOST_254}",
]
VALID_SIGNER_IDENTITIES = [
    "https://github.com/acme/cally-ci/.github/workflows/build.yml@refs/heads/main",
    "https://gitlab.com/acme/x//.gitlab-ci.yml@refs/heads/main",
    "https://a/",
    "https://a:443/x",
    "https://a/%40x~y+z=w,v",
    f"https://{HOST_253}/x",
    "https://a/" + "x" * 1014,  # 1024 characters
]
INVALID_SIGNER_IDENTITIES = [
    "",
    "https://a",
    "a@example.com",
    "mailto:a@example.com",
    "spiffe://a/b",
    "http://a/x",
    "https://a/b*c",
    "https://a/b\\c",
    "https://a/x:y",
    "https://a:y/x",
    "https://a:65536/x",
    "https://A/x",
    "https://a/x y",
    "https://a/x\n",
    f"https://{HOST_254}/x",
    "https://a/" + "x" * 1015,
]


def test_a_name_has_the_stage_grammar() -> None:
    assert (NAME.pattern, NAME.max_length) == (STAGE.pattern, STAGE.max_length)


@pytest.mark.parametrize(
    ("grammar", "value", "reason"),
    [
        (SOURCE_REPOSITORY, "github.com/a/.", SOURCE_REPOSITORY_DOT_NAME),
        (SOURCE_REPOSITORY, "github.com/a/..", SOURCE_REPOSITORY_DOT_NAME),
        (SOURCE_REPOSITORY, "github.com/a/x.git", SOURCE_REPOSITORY_DOT_GIT),
        (OIDC_ISSUER, "https://a:65536", URL_PORT_RANGE),
        (OIDC_ISSUER, "https://a:99999/x", URL_PORT_RANGE),
        (OIDC_ISSUER, f"https://{HOST_254}", URL_HOST_LENGTH),
        (SIGNER_IDENTITY, "https://a:65536/x", URL_PORT_RANGE),
        (SIGNER_IDENTITY, f"https://{HOST_254}/x", URL_HOST_LENGTH),
    ],
    ids=lambda x: x if isinstance(x, str) and len(x) < 40 else None,
)
def test_extra_rules_name_their_reason(grammar: Grammar, value: str, reason: str) -> None:
    assert grammar.problem(value) == reason


@pytest.mark.parametrize(("glob", "reason"), GLOB_STRUCTURE)
def test_the_input_glob_structure_rules_one_by_one(glob: str, reason: str) -> None:
    assert INPUT_GLOB.problem(glob) == reason


@pytest.mark.parametrize(
    "glob", ["a b", "a\\b", "a:b", "!src/**", "[!a]", "[^a]", "{a,b}", "a{b", "a^b", "C:\\x"]
)
def test_the_input_glob_pattern_refuses_other_syntaxes(glob: str) -> None:
    """No negation, negated class, brace expansion or Windows path: the pattern refuses them."""
    assert INPUT_GLOB.problem(glob) == INPUT_GLOB.rule


@pytest.mark.parametrize(
    ("grammar", "longest"),
    [
        (PRINCIPAL, "team/" + "a" * 100),
        (PRINCIPAL, "user/" + "a" * 39),
        (REPORTER, "app/" + "a" * 34),
        (SOURCE_REPOSITORY, "github.com/" + "a" * 39 + "/" + "b" * 100),
        (INPUT_GLOB, "x" * 512),
        (SIGNER_IDENTITY, "https://a/" + "x" * 1014),
        (OIDC_ISSUER, "https://a/" + "x" * 502),
    ],
    ids=lambda x: x if isinstance(x, str) and len(x) < 20 else None,
)
def test_new_grammar_length_bounds(grammar: Grammar, longest: str) -> None:
    assert len(longest) == grammar.max_length or grammar is PRINCIPAL
    assert grammar.problem(longest) is None
    assert grammar.problem(longest + "a") is not None


@pytest.mark.parametrize(
    ("grammar", "longest", "over"),
    [
        (RC_VERSION, "1" + "0" * 120 + ".0.0-rc", "1" + "0" * 121 + ".0.0-rc"),
        (FINAL_VERSION, "1" + "0" * 123 + ".0.0", "1" + "0" * 124 + ".0.0"),
    ],
    ids=["rc", "final"],
)
def test_a_version_of_valid_shape_one_over_128_is_refused(
    grammar: Grammar, longest: str, over: str
) -> None:
    """The bound is the length alone: the longer version has the shape the pattern accepts."""
    assert (len(longest), len(over)) == (128, 129) == (grammar.max_length, grammar.max_length + 1)
    assert grammar.problem(longest) is None
    assert re.fullmatch(grammar.pattern, over) is not None
    assert grammar.problem(over) == grammar.rule


# --- every grammar --------------------------------------------------------------------------

SAMPLES: dict[str, tuple[list[str], list[str]]] = {
    VERSION.name: (VALID_VERSIONS, INVALID_VERSIONS),
    RELEASE_LINE.name: (["0.0.0", "1.2.0"], ["1.2", "1.2.0-rc.1"]),
    STAGE.name: (VALID_STAGES, INVALID_STAGES),
    DIGEST.name: ([f"sha256:{HEX64}"], [f"sha256:{HEX64[:63]}", f"sha256:{HEX64}a"]),
    IMAGE.name: (VALID_IMAGES, INVALID_IMAGES),
    REPO.name: (VALID_REPOS, INVALID_REPOS),
    BRANCH.name: (VALID_BRANCHES, INVALID_BRANCHES),
    NAME.name: (VALID_STAGES, INVALID_STAGES),
    RC_VERSION.name: (VALID_RC_VERSIONS, INVALID_RC_VERSIONS),
    FINAL_VERSION.name: (VALID_FINAL_VERSIONS, INVALID_FINAL_VERSIONS),
    PRINCIPAL.name: (VALID_PRINCIPALS, INVALID_PRINCIPALS),
    REPORTER.name: (VALID_REPORTERS, INVALID_REPORTERS),
    SOURCE_REPOSITORY.name: (VALID_SOURCE_REPOSITORIES, INVALID_SOURCE_REPOSITORIES),
    SOURCE_COMMIT.name: (VALID_SOURCE_COMMITS, INVALID_SOURCE_COMMITS),
    INPUT_GLOB.name: (VALID_INPUT_GLOBS, INVALID_INPUT_GLOBS),
    OIDC_ISSUER.name: (VALID_OIDC_ISSUERS, INVALID_OIDC_ISSUERS),
    SIGNER_IDENTITY.name: (VALID_SIGNER_IDENTITIES, INVALID_SIGNER_IDENTITIES),
}


@pytest.mark.parametrize("grammar", GRAMMARS, ids=lambda g: g.name)
def test_every_grammar_accepts_and_refuses_its_samples(grammar: Grammar) -> None:
    valid, invalid = SAMPLES[grammar.name]
    assert [v for v in valid if grammar.problem(v) is not None] == []
    assert [v for v in invalid if grammar.problem(v) is None] == []


def test_every_grammar_has_samples() -> None:
    assert sorted(SAMPLES) == sorted(g.name for g in GRAMMARS)


# The only reasons a grammar gives: each is a constant, so none can carry the value.
REASONS = {
    VERSION.name: {VERSION_BUILD_METADATA, VERSION_LOCK},
    REPO.name: {REPO_DOT_NAME},
    SOURCE_REPOSITORY.name: {SOURCE_REPOSITORY_DOT_NAME, SOURCE_REPOSITORY_DOT_GIT},
    INPUT_GLOB.name: {reason for _, reason in GLOB_STRUCTURE},
    OIDC_ISSUER.name: {URL_HOST_LENGTH, URL_PORT_RANGE},
    SIGNER_IDENTITY.name: {URL_HOST_LENGTH, URL_PORT_RANGE},
}


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


@pytest.mark.parametrize("grammar", [*GRAMMARS, *FORMATS], ids=lambda g: g.name)
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
    "identity": lambda rng: (
        f"https://{_random_from(string.ascii_lowercase, rng, 6)}.io:{rng.randint(1, 65535)}/"
        + _random_from(string.ascii_letters + "._~%@+=,/-", rng, 30)
    ),
    "issuer": lambda rng: (
        f"https://{_random_from(string.ascii_lowercase, rng, 6)}:{rng.randint(1, 65535)}"
        + _random_from(string.ascii_lowercase + "/", rng, 10).replace("//", "/x/").rstrip("/")
    ),
    "glob": lambda rng: _random_from(string.ascii_lowercase + "._@+=,~%#*?/-", rng, 15),
    "principal": lambda rng: (
        rng.choice(["team/", "user/"]) + _random_from(string.ascii_lowercase + "-", rng, 12)
    ),
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
    candidates = ["localhost:5000/x", "a.b:443/c", f"sha256:{HEX64}", "https://a.io:443/x"]
    for make in GENERATORS.values():
        for _ in range(500):
            value = make(rng)
            candidates.append(value)
            candidates += _mutations(value, rng)
    accepted = [v for v in candidates for g in GRAMMARS if g.is_valid(v)]
    assert len(accepted) > 1000  # calibration: the property was tested on real values
    assert any(":" in v for v in accepted)  # values with a colon were among them
    for grammar in (SIGNER_IDENTITY, OIDC_ISSUER, INPUT_GLOB, PRINCIPAL):
        assert sum(grammar.is_valid(v) for v in candidates) > 100, grammar.name
    assert [v for v in accepted if EMOJI_SHAPE.search(v)] == []


def test_the_shortcode_check_registers_one() -> None:
    assert EMOJI_SHAPE.search("a:rocket:b") is not None
    assert EMOJI_SHAPE.search("ghcr.io:5000/x") is None


# --- field formats (not identifiers) --------------------------------------------------------------

VALID_TIMESTAMPS = ["2026-10-09T12:00:00Z", "2024-02-29T23:59:59Z", "0001-01-01T00:00:00Z"]
INVALID_TIMESTAMPS = [
    "",
    "2026-10-09T12:00:00",
    "2026-10-09T12:00:00.5Z",
    "2026-10-09T12:00:00+00:00",
    "2026-10-09 12:00:00Z",
    "2026-10-09t12:00:00z",
    "2026-13-01T00:00:00Z",
    "2026-10-32T00:00:00Z",
    "2026-10-09T24:00:00Z",
    "2026-10-09T12:60:00Z",
    "2026-10-09T12:00:60Z",
    "2026-10-09T12:00:00Z\n",
]
UNREAL_DATES = ["2026-02-30T00:00:00Z", "2025-02-29T00:00:00Z", "2026-04-31T00:00:00Z"]


@pytest.mark.parametrize("value", VALID_TIMESTAMPS)
def test_timestamp_accepts(value: str) -> None:
    assert TIMESTAMP.problem(value) is None


@pytest.mark.parametrize("value", INVALID_TIMESTAMPS)
def test_timestamp_refuses(value: str) -> None:
    assert TIMESTAMP.problem(value) is not None


@pytest.mark.parametrize("value", UNREAL_DATES)
def test_timestamp_refuses_a_date_that_does_not_exist(value: str) -> None:
    assert TIMESTAMP.problem(value) == TIMESTAMP_DATE


VALID_SBOM_VERSIONS = ["1.6", "1.7", "1.10", "2.0", "999.999"]
SBOM_BELOW_FLOOR = ["1.5", "1.0", "1.1"]
INVALID_SBOM_VERSIONS = ["", "0.9", "1", "1.6.0", "01.6", "1.06", "1000.0", "v1.6", "1.6\n"]


@pytest.mark.parametrize("value", VALID_SBOM_VERSIONS)
def test_sbom_version_accepts_1_6_and_above(value: str) -> None:
    assert SBOM_VERSION.problem(value) is None


@pytest.mark.parametrize("value", SBOM_BELOW_FLOOR)
def test_sbom_version_below_the_floor_names_it(value: str) -> None:
    assert SBOM_VERSION.problem(value) == SBOM_VERSION_FLOOR


@pytest.mark.parametrize("value", INVALID_SBOM_VERSIONS)
def test_sbom_version_refuses(value: str) -> None:
    assert SBOM_VERSION.problem(value) == SBOM_VERSION.rule


VALID_TAG_PREFIXES = ["", "api/", "a/", "a" * 63 + "/"]
INVALID_TAG_PREFIXES = ["api", "/", "Api/", "api//", "a" * 64 + "/", "1api/", "api-/"]


@pytest.mark.parametrize("value", VALID_TAG_PREFIXES)
def test_tag_prefix_accepts(value: str) -> None:
    assert TAG_PREFIX.problem(value) is None


@pytest.mark.parametrize("value", INVALID_TAG_PREFIXES)
def test_tag_prefix_refuses(value: str) -> None:
    assert TAG_PREFIX.problem(value) is not None


VALID_REASONS = [
    "login fails on Safari",
    "x",
    "a b",
    "d\u00e9faut d'affichage",
    "x" * 500,
    "\U0001f600",
]
INVALID_REASONS = [
    "",
    " x",
    "x ",
    " ",
    "a\nb",
    "a\tb",
    "a\rb",
    "a\x00b",
    "a\x7fb",
    "a\x85b",
    "a\xa0b",
    "a\xadb",
    "a\u200bb",
    "a\u200eb",
    "a\u202eb",
    "a\u2028b",
    "a\u2066b",
    "a\u3000b",
    "a\ufeffb",
    "x" * 501,
]


@pytest.mark.parametrize("value", VALID_REASONS)
def test_reason_accepts_one_line(value: str) -> None:
    assert REASON.problem(value) is None


@pytest.mark.parametrize("value", INVALID_REASONS)
def test_reason_refuses(value: str) -> None:
    assert REASON.problem(value) == REASON.rule


ASTRAL_INVISIBLE = [0x110BD, 0x13430, 0x1D173, 0xE0001, 0xE0041, 0xE007F]


@pytest.mark.parametrize("code", ASTRAL_INVISIBLE)
def test_reason_refuses_an_astral_format_character_by_its_extra(code: int) -> None:
    assert REASON.problem(f"a{chr(code)}b") == REASON_INVISIBLE


REASON_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Zs"}


def _excluded(code: int) -> bool:
    """Whether the fixed tables exclude a code point from a reason."""
    if code > 0xFFFF:
        return any(low <= code <= high for low, high in REASON_ASTRAL)
    return re.fullmatch(f"[{REASON_BMP}]", chr(code)) is not None


def test_the_reason_tables_are_the_invisible_categories_of_unicode_15() -> None:
    """The tables are fixed in the code; this checks them against Python's own Unicode data.

    On Unicode 15.0 they must be exactly the categories, U+0020 aside. On another version
    only what holds forever is checked: every character of the tables is still in them.
    """
    in_tables = {code for code in range(sys.maxunicode + 1) if _excluded(code)}
    in_categories = {
        code
        for code in range(sys.maxunicode + 1)
        if unicodedata.category(chr(code)) in REASON_CATEGORIES and code != 0x20
    }
    assert len(in_tables) > 200  # calibration: the tables were read
    assert {c for c in in_tables if unicodedata.category(chr(c)) not in REASON_CATEGORIES} == set()
    if unicodedata.unidata_version == "15.0.0":
        assert in_tables == in_categories


def test_the_public_key_pattern_refuses_what_is_not_pem_armor() -> None:
    assert PUBLIC_KEY.problem("not a key") == PUBLIC_KEY.rule
    assert (
        PUBLIC_KEY.problem("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n\n")
        == PUBLIC_KEY.rule
    )


# The formats' samples, as SAMPLES for the grammars. A key's samples are its fixtures without the
# final LF, which the pattern allows, so they read the same in Python re and ECMA-262; the armor
# with two final LFs is about the final LF itself, and test_release_keys holds it.
_KEYS_VALID = [P256, P256_OTHER, ED25519]
_KEYS_INVALID = [
    *KEY_UNSUPPORTED_FIXTURES.values(),
    *(key for name, key in KEY_ARMOR_FIXTURES.items() if name != "two-final-lfs"),
    "not a key",
]
FORMAT_SAMPLES: dict[str, tuple[list[str], list[str]]] = {
    TIMESTAMP.name: (VALID_TIMESTAMPS, INVALID_TIMESTAMPS + UNREAL_DATES),
    SBOM_VERSION.name: (VALID_SBOM_VERSIONS, SBOM_BELOW_FLOOR + INVALID_SBOM_VERSIONS),
    TAG_PREFIX.name: (VALID_TAG_PREFIXES, INVALID_TAG_PREFIXES),
    REASON.name: (VALID_REASONS, INVALID_REASONS + [f"a{chr(c)}b" for c in ASTRAL_INVISIBLE]),
    PUBLIC_KEY.name: (
        [key.removesuffix("\n") for key in _KEYS_VALID],
        [key.removesuffix("\n") for key in _KEYS_INVALID],
    ),
}


def test_every_format_has_samples() -> None:
    assert sorted(FORMAT_SAMPLES) == sorted(f.name for f in FORMATS)


@pytest.mark.parametrize("grammar", FORMATS, ids=lambda g: g.name)
def test_every_format_accepts_and_refuses_its_samples(grammar: Grammar) -> None:
    valid, invalid = FORMAT_SAMPLES[grammar.name]
    assert [v for v in valid if grammar.problem(v) is not None] == []
    assert [v for v in invalid if grammar.problem(v) is None] == []

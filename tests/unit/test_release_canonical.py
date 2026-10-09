"""Canonical writing, RFC 8785 and the policy digest (ADR-026 sections 10 and 15).

The expected bytes are written out literally, never derived from the code under test. Every
document the writer accepts reads back through the strict loader as itself.
"""

import pytest

from gora.domain.release_repository import canonical, yaml12

HEX = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
COMMIT = "9fceb02d0ae598e95dc970b74767f19372d61af8"
SCHEMAS = "https://gora.tools/schemas/v1alpha1"


def round_trip(document: dict[str, object]) -> object:
    return yaml12.load(canonical.dump(document).encode("utf-8"), source="written.yaml")


# The examples of ADR-026 sections 6 and 7.


def test_writes_a_stage_state() -> None:
    document: dict[str, object] = {
        "apiVersion": "gora.tools/v1alpha1",
        "kind": "StageState",
        "metadata": {"stage": "qa", "artifact": "api"},
        "spec": {
            "image": "ghcr.io/acme/cally-api",
            "digest": f"sha256:{HEX}",
            "version": "1.4.0-rc",
            "sourceCommit": COMMIT,
            "evidence": {
                "vsa": f"sha256:{HEX}",
                "policy": f"sha256:{HEX}",
                "result": "passed",
                "time": "2026-10-09T12:00:00Z",
            },
        },
        "writtenBy": "0.3.0",
    }
    written = canonical.dump(document, modeline=f"{SCHEMAS}/stage-state.json")
    assert written == (
        "# yaml-language-server: $schema=https://gora.tools/schemas/v1alpha1/stage-state.json\n"
        "apiVersion: gora.tools/v1alpha1\n"
        "kind: StageState\n"
        "metadata:\n"
        "  stage: qa\n"
        "  artifact: api\n"
        "spec:\n"
        "  image: ghcr.io/acme/cally-api\n"
        f"  digest: sha256:{HEX}\n"
        "  version: 1.4.0-rc\n"
        f"  sourceCommit: {COMMIT}\n"
        "  evidence:\n"
        f"    vsa: sha256:{HEX}\n"
        f"    policy: sha256:{HEX}\n"
        "    result: passed\n"
        '    time: "2026-10-09T12:00:00Z"\n'
        "writtenBy: 0.3.0\n"
    )
    assert yaml12.load(written.encode("utf-8"), source="state/qa/api.yaml") == document


def test_writes_a_rejection() -> None:
    document: dict[str, object] = {
        "apiVersion": "gora.tools/v1alpha1",
        "kind": "Rejection",
        "metadata": {"artifact": "api", "version": "1.1.0"},
        "spec": {"digest": f"sha256:{HEX}", "reason": "login fails on Safari"},
        "writtenBy": "0.3.0",
    }
    written = canonical.dump(document, modeline=f"{SCHEMAS}/rejection.json")
    assert written == (
        "# yaml-language-server: $schema=https://gora.tools/schemas/v1alpha1/rejection.json\n"
        "apiVersion: gora.tools/v1alpha1\n"
        "kind: Rejection\n"
        "metadata:\n"
        "  artifact: api\n"
        "  version: 1.1.0\n"
        "spec:\n"
        f"  digest: sha256:{HEX}\n"
        '  reason: "login fails on Safari"\n'
        "writtenBy: 0.3.0\n"
    )
    assert yaml12.load(written.encode("utf-8"), source="rejections/api/1.1.0.yaml") == document


def test_writes_lists_empty_collections_and_scalars() -> None:
    document: dict[str, object] = {
        "items": [
            {"name": "dev", "gate": {"kind": "auto"}, "checks": ["deploy", "smoke"]},
            {"name": "qa"},
            {},
            "plain",
            7,
            None,
        ],
        "empty": {},
        "none": [],
        "flags": {"on": True, "off": False, "count": -3, "unset": None},
    }
    assert canonical.dump(document) == (
        "items:\n"
        "  - name: dev\n"
        "    gate:\n"
        "      kind: auto\n"
        "    checks:\n"
        "      - deploy\n"
        "      - smoke\n"
        "  - name: qa\n"
        "  - {}\n"
        "  - plain\n"
        "  - 7\n"
        "  - null\n"
        "empty: {}\n"
        "none: []\n"
        "flags:\n"
        '  "on": true\n'
        '  "off": false\n'
        "  count: -3\n"
        "  unset: null\n"
    )
    assert round_trip(document) == document


def test_writes_an_empty_document() -> None:
    assert canonical.dump({}) == "{}\n"
    assert canonical.dump({}, modeline="x") == "# yaml-language-server: $schema=x\n{}\n"


def test_writes_the_same_bytes_every_time() -> None:
    document: dict[str, object] = {"b": [{"c": "no"}], "a": "1:30"}
    assert len({canonical.dump(document) for _ in range(5)}) == 1


# Strings: plain only when YAML 1.2 and YAML 1.1 both read the text back as that string.


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("no", '"no"'),
        ("on", '"on"'),
        ("yes", '"yes"'),
        ("y", '"y"'),
        ("true", '"true"'),
        ("null", '"null"'),
        ("~", '"~"'),
        ("010", '"010"'),
        ("10", '"10"'),
        ("1.5", '"1.5"'),
        ("1e3", '"1e3"'),
        ("1:30", '"1:30"'),
        ("1_000", '"1_000"'),
        ("0b11", '"0b11"'),
        ("0x1F", '"0x1F"'),
        ("2026-10-09", '"2026-10-09"'),
        ("2026-10-09T12:00:00Z", '"2026-10-09T12:00:00Z"'),
        ("a:", '"a:"'),
        ("a: b", '"a: b"'),
        ("-x", '"-x"'),
        ("", '""'),
        ("api", "api"),
        ("1.4.0-rc", "1.4.0-rc"),
        ("0.3.0", "0.3.0"),
        ("sha256:abc", "sha256:abc"),
        ("team/qa", "team/qa"),
        ("user@host", "user@host"),
        ("+x", "+x"),
        ("https://gora.tools/x", "https://gora.tools/x"),
    ],
)
def test_quotes_a_string_any_reader_would_read_otherwise(value: str, written: str) -> None:
    assert canonical.dump({"k": value}) == f"k: {written}\n"
    assert canonical.dump({value: 1}) == f"{written}: 1\n"


AWKWARD = [
    "no",
    "010",
    "1:30",
    "a: b",
    "#x",
    "- x",
    "",
    " leading",
    "trailing ",
    "null",
    "line one\nline two\n",
    "tab\there",
    "nel\x85here",
    "ls\u2028here",
    "del\x7fhere",
    "c1\x9bhere",
    "nonchar\ufffe\uffff",
    "emoji \U0001f600",
    "Кириллица",
    'quote " and \\ backslash',
    "c0 \x00\x01\x1b\x1f",
    "...",
    "---",
    "[x]",
    "{x}",
    "*x",
    "&x",
    "!x",
    "%x",
    "@x",
    "`x",
    "'x'",
    "x #y",
    "<<",
    "=",
]


@pytest.mark.parametrize("value", AWKWARD)
def test_round_trips_an_awkward_string(value: str) -> None:
    document: dict[str, object] = {value: value, "list": [value, {value: value}]}
    assert round_trip(document) == document


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("\x85", '"\\u0085"'),
        ("\x7f", '"\\u007f"'),
        ("\x9f", '"\\u009f"'),
        ("\ufffe", '"\\ufffe"'),
        ("\x1f", '"\\u001f"'),
        ("\n", '"\\n"'),
        ("\u2028", '"\u2028"'),
        ("é", '"é"'),
    ],
)
def test_escapes_what_yaml_cannot_print(value: str, written: str) -> None:
    assert canonical.dump({"k": value}) == f"k: {written}\n"


# What the writer refuses.


@pytest.mark.parametrize(
    "document",
    [
        {"a": 1.5},
        {"a": {"b": [0.0]}},
        {"a": [[1]]},
        {"a": [[]]},
        {"a": (1, 2)},
        {"a": {1: "x"}},
    ],
)
def test_refuses_what_no_release_file_holds(document: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        canonical.dump(document)


def test_refuses_a_document_that_is_not_a_mapping() -> None:
    with pytest.raises(TypeError):
        canonical.dump([1])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "document",
    [{"a": 2**53}, {"a": -(2**53)}, {"a": "\ud800"}, {"\udc00": 1}, {"a": "x" * 262144}],
)
def test_refuses_what_the_loader_would_refuse(document: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        canonical.dump(document)


# RFC 8785.


def test_jcs_sorts_members_by_utf16_code_units() -> None:
    # RFC 8785 section 3.2.3: the property names, then the order the RFC gives.
    value = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }
    assert canonical.jcs(value).decode("utf-8") == (
        '{"\\r":"Carriage Return",'
        '"1":"One",'
        '"\u0080":"Control",'
        '"ö":"Latin Small Letter O With Diaeresis",'
        '"€":"Euro Sign",'
        '"\U0001f600":"Emoji: Grinning Face",'
        '"דּ":"Hebrew Letter Dalet With Dagesh"}'
    )


C0_ESCAPES = [
    "\\u0000",
    "\\u0001",
    "\\u0002",
    "\\u0003",
    "\\u0004",
    "\\u0005",
    "\\u0006",
    "\\u0007",
    "\\b",
    "\\t",
    "\\n",
    "\\u000b",
    "\\f",
    "\\r",
    "\\u000e",
    "\\u000f",
    "\\u0010",
    "\\u0011",
    "\\u0012",
    "\\u0013",
    "\\u0014",
    "\\u0015",
    "\\u0016",
    "\\u0017",
    "\\u0018",
    "\\u0019",
    "\\u001a",
    "\\u001b",
    "\\u001c",
    "\\u001d",
    "\\u001e",
    "\\u001f",
]


@pytest.mark.parametrize("code", range(32))
def test_jcs_escapes_every_c0_control(code: int) -> None:
    assert canonical.jcs(chr(code)) == f'"{C0_ESCAPES[code]}"'.encode()


def test_jcs_escapes_only_quote_backslash_and_c0() -> None:
    assert canonical.jcs('"\\/\x7f\x85\u2028é\U0001f600') == (
        '"\\"\\\\/\x7f\x85\u2028é\U0001f600"'.encode()
    )


def test_jcs_serialises_nested_values_without_whitespace() -> None:
    value = {"b": [1, True, None, {"d": "x", "c": []}], "a": {}, "e": False, "f": -0}
    assert canonical.jcs(value) == b'{"a":{},"b":[1,true,null,{"c":[],"d":"x"}],"e":false,"f":0}'


@pytest.mark.parametrize("value", [1.5, {"a": [0.0]}, (1,), {1: 1}])
def test_jcs_refuses_what_no_release_file_holds(value: object) -> None:
    with pytest.raises(TypeError):
        canonical.jcs(value)


@pytest.mark.parametrize("value", [2**53, "\ud800"])
def test_jcs_refuses_what_rfc_8785_cannot_carry(value: object) -> None:
    with pytest.raises(ValueError):
        canonical.jcs(value)


# The policy digest.

POLICY_BLOCK = b"""\
# The project's policy.
apiVersion: gora.tools/v1alpha1
kind: Policy
spec:
  stages:
    - name: dev   # first
      gate: auto
    - name: prod
      gate: approval
      approvers: [team/release-owners]
  initial: 010
"""

POLICY_FLOW = b"""\
{kind: "Policy", 'apiVersion': gora.tools/v1alpha1,
 spec: {initial: 10, stages: [{gate: 'auto', name: dev},
   {approvers: ["team/release-owners"], name: "prod", gate: approval}]}}
"""

ARTIFACT = b"""\
apiVersion: gora.tools/v1alpha1
kind: Artifact
metadata:
  name: api
spec:
  inputs: ["src/**"]
"""


def digest(policy: bytes, artifact: bytes = ARTIFACT) -> str:
    return canonical.policy_digest(
        yaml12.load(policy, source="release-policy.yaml"),
        yaml12.load(artifact, source="artifacts/api/artifact.yaml"),
    )


def test_policy_digest_ignores_comments_order_style_and_quoting() -> None:
    assert digest(POLICY_BLOCK) == digest(POLICY_FLOW)
    assert digest(POLICY_BLOCK).startswith("sha256:")
    assert len(digest(POLICY_BLOCK)) == len("sha256:") + 64


def test_policy_digest_changes_with_one_value() -> None:
    changed = POLICY_BLOCK.replace(b"gate: auto", b"gate: approval")
    assert digest(changed) != digest(POLICY_BLOCK)
    assert digest(POLICY_BLOCK, ARTIFACT.replace(b"src/**", b"lib/**")) != digest(POLICY_BLOCK)


def test_policy_digest_is_taken_over_the_pair_in_order() -> None:
    policy = yaml12.load(POLICY_BLOCK, source="release-policy.yaml")
    artifact = yaml12.load(ARTIFACT, source="artifacts/api/artifact.yaml")
    assert canonical.policy_digest(policy, artifact) != canonical.policy_digest(artifact, policy)
    assert canonical.policy_digest({}, None) == (
        # printf '[{},null]' | shasum -a 256
        "sha256:f430e673d93ac3f880c8b552c72b8d1e23c514e35f65d778f856f6f67f1263b8"
    )

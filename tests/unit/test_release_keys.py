"""The structural check of a signer's public key (ADR-027 section 7, ADR-026 section 3).

The keys below were generated once with openssl, outside the repository, and only their public
PEM text is here: no private key was ever written into the tree. The forms are written out:
P-256 and Ed25519 accepted, every other key refused as unsupported, a P-256 point off the curve,
the Ed25519 encodings RFC 8032 refuses and the points of small order, and the armor variants.
"""

import base64

import pytest
from pydantic import ValidationError

from gora.domain.release_repository import keys
from gora.domain.release_repository.formats import PUBLIC_KEY
from gora.domain.release_repository.keys import (
    KEY_ARMOR,
    KEY_BASE64,
    KEY_ED25519_ENCODING,
    KEY_ED25519_SMALL_ORDER,
    KEY_P256_POINT,
    KEY_UNSUPPORTED,
)
from gora.domain.release_repository.v1alpha1 import KeySigner

P256 = """\
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAELGtggmOKP9/RPvCPL885nKEbcAIr
83jV0RAw6Ff2l8xqVoRxHu7StO9I8LU8kSEzbLU6D9loC+Ns3jwfcAvabg==
-----END PUBLIC KEY-----
"""
P256_OTHER = """\
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAELXfx5L8Wqo8sY+7Jx0wINa0D0VNQ
oV7XaPZNhRrPi15lWceGBmuRhSGZp2fLIyTPvGP6TP+3XY6VMX7lx0lSWQ==
-----END PUBLIC KEY-----
"""
ED25519 = """\
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA58M0zQ4wtkbIBhSCReLKyEFdIU28xMTKdTWVxafXmTY=
-----END PUBLIC KEY-----
"""
P256_COMPRESSED = """\
-----BEGIN PUBLIC KEY-----
MDkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDIgADHd9tGaIIvPsAT0cqtHP2r+dDN8L5
dZAEzrXFlHAkNos=
-----END PUBLIC KEY-----
"""
P384 = """\
-----BEGIN PUBLIC KEY-----
MHYwEAYHKoZIzj0CAQYFK4EEACIDYgAEjk86PhvoGFjDlaKBa+3a67LGbYdi1DU3
Qs+QWQCDPKkA2PtWaFXAUMCb7OfVAZlalhqVOq8WxuoNsCseuO/7XtaKi4LKfhCB
xL9OSF4bPEv7pDDzsVzZhl27J0DKCM3X
-----END PUBLIC KEY-----
"""
ED448 = """\
-----BEGIN PUBLIC KEY-----
MEMwBQYDK2VxAzoAOFOrIVhHRxU/SMeIpe5QHvkEvyg3lYBMQDNEExRVN6O4kDgY
uL9kohFuNt96tqRttSTvM0/YQUWA
-----END PUBLIC KEY-----
"""
RSA_2048 = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu06EdjyxNDrTEI6gfWU6
OdCLqvSUeGS5npx9oizF/GfoHTnIeIgfG+IRDs4uRipAR3wQZz2QyUTnBeblW8cA
apFegcW7xdR/4LCXDPuCFQ1iXrsJOHB4asItMs+C5bSt3yYhd11ZtgR9Bb8SHoc4
+1jZC4xeHODxDhf82Vtua+AFlsqczQUA07UM2ORx1q6vLt5cUJweqBKlz5HSpsjG
moNWDp+z+G+pKsp9tLdjELDqUiRJokJ7bCNkfETuI79bM+BRGro2MM2rPrGI36Nc
f1msYkSV/1V2H20cQcj6OMyG62etdtDu30RIxd5Wz3wBck4+7dJSHu6biCigrzJs
IQIDAQAB
-----END PUBLIC KEY-----
"""
RSA_4096 = """\
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEArHuls7g6b5AtktCDVZTx
eljCX5LS3bOzEIU+RlwU9qJbIYGY+Lk6vvDrdGFB8R90/OssxMSxbwaUwGc05nHV
hFRMR6oJQ9RkjAzRl3QYe+BuHBNGNxiM8agzU+LZbkn8rm4qfeyyKandV0JcIfxF
M2xsPfiV5jQ6UP8hOCc/3nWKSvFL8SI+R8VBibl9lz/0gX+BwNKzXSM4A6m+EF9q
i9esnrAPamqD1YQW9BkAvpmcz2cVrdrCEB9jS4XnGP7mj8IjLovBQpRbVWbmtl0L
Kx9/UwC9hTkScocJZiLZEkGedwy2LK87Wioh87gfp0bGeTs25AstWU/sLNAmPmiP
fXQS2WAT+3S2vImk7aMhJbTV0ARioiYXExn3H7kTjFFER99m618HbDJ7+7HvhutS
Tuwnjgu7otBYS3mEM81fYlWngBIebtcrvuLC21yp/1Dcgadj8+QvJiqbNBOveVbO
xjxZVGKloVCU7E0MocDGR5+pFtR1LNAeFu/Gug28ky4gVsM0c5kVDkNfKFaWrqyt
PsZPfRIpfv7D25iRL0tptrBLdR2nVrFrO7Q+zXK6zQQWt6ObAqC83hzUj1/Vza+U
TcXSpO7MU483mSH2FQo2iOQjrafPMvH782Uk6uygoQ0FvcYiMRUnNgA9uejblFNr
+xOXFWkjtBznihpuNSwD6wcCAwEAAQ==
-----END PUBLIC KEY-----
"""

P256_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d03010703420004")
ED25519_PREFIX = bytes.fromhex("302a300506032b6570032100")
P256_P = bytes.fromhex("ffffffff00000001000000000000000000000000ffffffffffffffffffffffff")


def pem(der: bytes, width: int = 64) -> str:
    """RFC 7468 armor around ``der``, lines of ``width``, with the final LF; strict at 64."""
    text = base64.b64encode(der).decode("ascii")
    lines = [text[at : at + width] for at in range(0, len(text), width)]
    return "\n".join(["-----BEGIN PUBLIC KEY-----", *lines, "-----END PUBLIC KEY-----"]) + "\n"


def der(key: str) -> bytes:
    return base64.b64decode("".join(key.strip().split("\n")[1:-1]))


def ed25519(encoding_hex: str) -> str:
    return pem(ED25519_PREFIX + bytes.fromhex(encoding_hex))


def test_the_fixtures_are_the_forms_they_claim() -> None:
    """Calibration: the DER of each fixture starts as its kind does."""
    assert der(P256).startswith(P256_PREFIX) and len(der(P256)) == 91
    assert der(ED25519).startswith(ED25519_PREFIX) and len(der(ED25519)) == 44
    assert der(P256_COMPRESSED)[26] in (2, 3)  # 0x02 or 0x03: a compressed point
    assert pem(der(P256)) == P256  # the fixtures are strict armor


@pytest.mark.parametrize("key", [P256, P256_OTHER, ED25519], ids=["p256", "p256-other", "ed25519"])
@pytest.mark.parametrize("final_lf", [True, False], ids=["block", "block-chomped"])
def test_a_supported_key_is_accepted_with_or_without_the_final_lf(key: str, final_lf: bool) -> None:
    text = key if final_lf else key.removesuffix("\n")
    assert keys.problem(text) is None
    assert PUBLIC_KEY.problem(text) is None
    assert KeySigner.model_validate({"publicKey": text}).public_key == text


UNSUPPORTED = {
    "p256-compressed": P256_COMPRESSED,
    "p384": P384,
    "ed448": ED448,
    "rsa-2048": RSA_2048,
    "rsa-4096": RSA_4096,
}


@pytest.mark.parametrize("name", sorted(UNSUPPORTED))
def test_another_key_is_refused_as_unsupported(name: str) -> None:
    key = UNSUPPORTED[name]
    assert len(key) <= PUBLIC_KEY.max_length  # refused for its kind, not its length
    assert keys.problem(key) == KEY_UNSUPPORTED
    assert PUBLIC_KEY.problem(key) == KEY_UNSUPPORTED
    with pytest.raises(ValidationError) as refused:
        KeySigner.model_validate({"publicKey": key})
    assert [e["msg"] for e in refused.value.errors()] == [KEY_UNSUPPORTED]


def test_a_p256_point_off_the_curve_is_refused() -> None:
    point = bytearray(der(P256))
    point[-1] ^= 0x01
    assert keys.problem(pem(bytes(point))) == KEY_P256_POINT


@pytest.mark.parametrize("coordinate", ["x", "y"])
def test_a_p256_coordinate_at_or_above_p_is_refused(coordinate: str) -> None:
    x, y = der(P256)[27:59], der(P256)[59:]
    point = P256_P + y if coordinate == "x" else x + P256_P
    assert keys.problem(pem(P256_PREFIX + point)) == KEY_P256_POINT


@pytest.mark.parametrize(
    "encoding",
    [
        "ed" + "ff" * 30 + "7f",  # y = p
        "ee" + "ff" * 30 + "7f",  # y = p + 1
        "ff" * 31 + "7f",  # y = 2^255 - 1
        "01" + "00" * 30 + "80",  # y = 1, x = 0 with the sign bit set
        "ec" + "ff" * 31,  # y = p - 1, x = 0 with the sign bit set
        "02" + "00" * 31,  # y = 2: no x on the curve
    ],
)
def test_an_ed25519_encoding_rfc_8032_refuses_is_refused(encoding: str) -> None:
    assert keys.problem(ed25519(encoding)) == KEY_ED25519_ENCODING


@pytest.mark.parametrize(
    "encoding",
    [
        "01" + "00" * 31,  # y = 1: the neutral element
        "ec" + "ff" * 30 + "7f",  # y = p - 1: order 2
        "00" * 32,  # y = 0: order 4
        "00" * 31 + "80",  # y = 0, the other sign
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",  # order 8
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",  # order 8
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
    ],
)
def test_an_ed25519_point_of_small_order_is_refused(encoding: str) -> None:
    assert keys.problem(ed25519(encoding)) == KEY_ED25519_SMALL_ORDER


_LINES = P256.split("\n")
# Each variant of the armor, and the one reason it is refused for.
ARMOR_TABLE = [
    ("crlf", P256.replace("\n", "\r\n"), KEY_ARMOR),
    ("line-of-76", pem(der(P256), width=76), KEY_ARMOR),
    (
        "short-middle-line",
        "\n".join([_LINES[0], _LINES[1][:60], _LINES[1][60:] + _LINES[2], *_LINES[3:]]),
        KEY_ARMOR,
    ),
    ("wrong-label", P256.replace("PUBLIC KEY", "EC PUBLIC KEY"), KEY_ARMOR),
    ("space-at-line-end", P256.replace(_LINES[1], _LINES[1] + " "), KEY_ARMOR),
    ("two-final-lfs", P256 + "\n", KEY_ARMOR),
    ("leading-lf", "\n" + P256, KEY_ARMOR),
    ("no-end", "\n".join(_LINES[:3]) + "\n", KEY_ARMOR),
    ("two-blocks", P256 + P256, KEY_ARMOR),
    ("padding-inside", P256.replace(_LINES[1][-4:], _LINES[1][-4:-1] + "="), KEY_ARMOR),
]
ARMOR = {name: text for name, text, _ in ARMOR_TABLE}


@pytest.mark.parametrize(("text", "reason"), [row[1:] for row in ARMOR_TABLE], ids=list(ARMOR))
def test_an_armor_variant_is_refused_for_its_reason(text: str, reason: str) -> None:
    assert keys.problem(text) == reason


def test_the_line_of_76_fixture_has_one() -> None:
    assert [len(line) for line in ARMOR["line-of-76"].split("\n")[1:-2]] == [76, 48]


def test_non_canonical_padding_bits_are_refused() -> None:
    """The last base64 character before ``=`` carries bits the DER does not hold."""
    text = ED25519.replace("mTY=", "mTZ=")
    assert base64.b64decode("".join(text.strip().split("\n")[1:-1])) == der(ED25519)
    assert keys.problem(text) == KEY_BASE64


def test_same_key_compares_der_bytes() -> None:
    assert keys.same_key(P256, P256.removesuffix("\n"))
    assert not keys.same_key(P256, P256_OTHER)
    assert not keys.same_key(P256, ED25519)
    with pytest.raises(ValueError, match="not a public key"):
        keys.same_key(P256, "x")


def test_no_refusal_echoes_the_key() -> None:
    for key in [*UNSUPPORTED.values(), *ARMOR.values()]:
        reason = keys.problem(key)
        assert reason is not None
        body = [line.strip() for line in key.split("\n") if "-----" not in line]
        assert [line for line in body if len(line) > 8 and line in reason] == []

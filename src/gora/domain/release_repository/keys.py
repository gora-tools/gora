"""The structural check of a signer's public key (ADR-027 section 7, ADR-026 section 3).

Pure: text in, a constant reason out, the key never echoed. A key is strict RFC 7468 PEM of a
SubjectPublicKeyInfo in one of exactly two forms: an ECDSA P-256 key as an uncompressed point
on the curve, or an Ed25519 key that decodes as RFC 8032 section 5.1.3 says and is not of small
order. Anything else is an unsupported key. This is a check of form only: verifying a
signature is not done here.
"""

import base64
import binascii
import re

BEGIN = "-----BEGIN PUBLIC KEY-----"
END = "-----END PUBLIC KEY-----"
LINE = 64
_BASE64_TEXT = re.compile("[A-Za-z0-9+/]+=*")

KEY_ARMOR = (
    "must be one strict PEM public key (RFC 7468): -----BEGIN PUBLIC KEY-----, base64 lines of "
    "64 characters but the last (1 to 64), padding only at the end, -----END PUBLIC KEY-----, "
    "LF line ends and at most one final LF"
)
KEY_BASE64 = "must be canonical base64: it re-encodes to the same text"
KEY_UNSUPPORTED = (
    "must be an ECDSA P-256 key as an uncompressed point, or an Ed25519 key: no other key is "
    "supported"
)
KEY_P256_POINT = "must be a point on the P-256 curve"
KEY_ED25519_ENCODING = "must be an Ed25519 key that decodes as RFC 8032 section 5.1.3 says"
KEY_ED25519_SMALL_ORDER = "must not be an Ed25519 key of small order"

# SubjectPublicKeyInfo prefixes: id-ecPublicKey with prime256v1 and an uncompressed point (04),
# and id-Ed25519 (RFC 8410).
_P256_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d03010703420004")
_P256_LENGTH = 91
_ED25519_PREFIX = bytes.fromhex("302a300506032b6570032100")
_ED25519_LENGTH = 44

# P-256 (SEC 2): y^2 = x^3 - 3x + b over GF(p).
_P256_P = 2**256 - 2**224 + 2**192 + 2**96 - 1
_P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B

# Ed25519 (RFC 8032 section 5.1): -x^2 + y^2 = 1 + d x^2 y^2 over GF(p).
_ED_P = 2**255 - 19
_ED_D = (-121665 * pow(121666, -1, _ED_P)) % _ED_P
_ED_SQRT_M1 = pow(2, (_ED_P - 1) // 4, _ED_P)


def problem(text: str) -> str | None:
    """Why ``text`` is not a supported public key, without the key; None when it is one."""
    der = _der(text)
    if isinstance(der, str):
        return der
    if len(der) == _P256_LENGTH and der.startswith(_P256_PREFIX):
        return _p256_problem(der[len(_P256_PREFIX) :])
    if len(der) == _ED25519_LENGTH and der.startswith(_ED25519_PREFIX):
        return _ed25519_problem(der[len(_ED25519_PREFIX) :])
    return KEY_UNSUPPORTED


def same_key(a: str, b: str) -> bool:
    """Whether two valid keys are one key: their DER bytes are equal."""
    der_a, der_b = _der(a), _der(b)
    if isinstance(der_a, str) or isinstance(der_b, str):
        raise ValueError("not a public key")
    return der_a == der_b


def _der(text: str) -> bytes | str:
    """The DER bytes of a strict PEM block, or the reason it is not one."""
    body = text.removesuffix("\n")
    lines = body.split("\n")
    if len(lines) < 3 or lines[0] != BEGIN or lines[-1] != END:
        return KEY_ARMOR
    data = lines[1:-1]
    if any(len(line) != LINE for line in data[:-1]) or not 1 <= len(data[-1]) <= LINE:
        return KEY_ARMOR
    encoded = "".join(data)
    if _BASE64_TEXT.fullmatch(encoded) is None:
        return KEY_ARMOR
    try:
        der = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return KEY_BASE64
    if base64.b64encode(der).decode("ascii") != encoded:
        return KEY_BASE64
    return der


def _p256_problem(point: bytes) -> str | None:
    x = int.from_bytes(point[:32], "big")
    y = int.from_bytes(point[32:], "big")
    p = _P256_P
    if x >= p or y >= p or (y * y - (x * x * x - 3 * x + _P256_B)) % p != 0:
        return KEY_P256_POINT
    return None


def _ed25519_problem(encoded: bytes) -> str | None:
    point = _ed25519_decode(encoded)
    if point is None:
        return KEY_ED25519_ENCODING
    eight = point
    for _ in range(3):
        eight = _ed25519_add(eight, eight)
    if eight == (0, 1):
        return KEY_ED25519_SMALL_ORDER
    return None


def _ed25519_decode(encoded: bytes) -> tuple[int, int] | None:
    """RFC 8032 section 5.1.3: the point an encoding names, or None when it fails to decode."""
    p = _ED_P
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= p:
        return None
    u = (y * y - 1) % p
    v = (_ED_D * y * y + 1) % p
    x = (u * pow(v, 3, p) * pow(u * pow(v, 7, p), (p - 5) // 8, p)) % p
    if (v * x * x - u) % p != 0:
        if (v * x * x + u) % p != 0:
            return None
        x = (x * _ED_SQRT_M1) % p
    if x == 0 and sign == 1:
        return None
    if x % 2 != sign:
        x = p - x
    return x, y


def _ed25519_add(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    """Point addition on edwards25519; complete, since d is not a square."""
    p = _ED_P
    (x1, y1), (x2, y2) = a, b
    t = _ED_D * x1 * x2 * y1 * y2 % p
    x3 = (x1 * y2 + x2 * y1) * pow(1 + t, -1, p) % p
    y3 = (y1 * y2 + x1 * x2) * pow(1 - t, -1, p) % p
    return x3, y3

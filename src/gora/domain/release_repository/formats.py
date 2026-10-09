"""The field formats of the release files that are not identifiers (ADR-026 sections 4 to 7).

Pure: no I/O. Each is a ``Grammar``, published in the schemas, and kept out of ``GRAMMARS``:
they are not identifiers, and a time (``12:00:00``) holds the ``:name:`` shape by nature. Every
pattern is in the common subset of Python ``re`` and ECMA-262, as the identifiers' are; what a
pattern cannot express is the format's ``extra``, stated in its rule.
"""

import datetime

from gora.domain.release.identifiers import Grammar
from gora.domain.release_repository import keys

TIMESTAMP_DATE = "must be a real date"


def _real_date(value: str) -> str | None:
    try:
        datetime.date.fromisoformat(value[:10])
    except ValueError:
        return TIMESTAMP_DATE
    return None


TIMESTAMP = Grammar(
    name="timestamp",
    pattern=(
        r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
        r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
    ),
    max_length=20,
    rule=(
        "must be an RFC 3339 time in UTC with whole seconds, YYYY-MM-DDTHH:MM:SSZ, on a real date"
    ),
    extra=_real_date,
)

SBOM_FLOOR = (1, 6)
SBOM_VERSION_FLOOR = "must be at least 1.6, compared as numbers"


def _sbom_floor(value: str) -> str | None:
    major, _, minor = value.partition(".")
    return SBOM_VERSION_FLOOR if (int(major), int(minor)) < SBOM_FLOOR else None


SBOM_VERSION = Grammar(
    name="SBOM version",
    pattern=r"^[1-9][0-9]{0,2}\.(?:0|[1-9][0-9]{0,2})$",
    max_length=7,
    rule=(
        "must be a CycloneDX version MAJOR.MINOR without leading zeros, at least 1.6 compared "
        "as numbers (1.10 is above 1.6)"
    ),
    extra=_sbom_floor,
)

TAG_PREFIX = Grammar(
    name="tag prefix",
    pattern=r"^(?:[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?/)?$",
    max_length=64,
    rule="must be empty, or the artifact's name followed by /",
)

# The characters a reason never holds: the categories Cc, Cf, Zl, Zp, and Zs other than U+0020,
# in Unicode 15.0, Python 3.12's. Fixed here and never read from unicodedata, so every version
# of GORA accepts the same reasons (a rejection never changes, L16). The BMP characters are in
# the pattern; the astral ones are the extra's, since an ECMA-262 class without the u flag
# cannot name them.
REASON_BMP = (
    r"\x00-\x1f\x7f-\xa0\xad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u1680\u180e"
    r"\u2000-\u200f\u2028-\u202f\u205f-\u2064\u2066-\u206f\u3000\ufeff\ufff9-\ufffb"
)
REASON_ASTRAL = (
    (0x110BD, 0x110BD),
    (0x110CD, 0x110CD),
    (0x13430, 0x1343F),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0001, 0xE0001),
    (0xE0020, 0xE007F),
)
REASON_INVISIBLE = (
    "must not hold a control, format, separator or space character other than the ASCII space"
)


def _reason_astral(value: str) -> str | None:
    for char in value:
        code = ord(char)
        if code > 0xFFFF and any(low <= code <= high for low, high in REASON_ASTRAL):
            return REASON_INVISIBLE
    return None


REASON = Grammar(
    name="reason",
    pattern=rf"^[^\x20{REASON_BMP}](?:[^{REASON_BMP}]*[^\x20{REASON_BMP}])?$",
    max_length=500,
    rule=(
        "must be one line of 1 to 500 characters with no space at either end, and no control, "
        "format, line or paragraph separator, or space other than U+0020 (categories Cc, Cf, Zl, "
        "Zp, Zs in Unicode 15.0, a fixed table)"
    ),
    extra=_reason_astral,
)

PUBLIC_KEY = Grammar(
    name="public key",
    pattern=r"^-----BEGIN PUBLIC KEY-----\n[A-Za-z0-9+/=\n]+-----END PUBLIC KEY-----\n?$",
    max_length=1024,
    rule=(
        "must be a strict PEM public key (RFC 7468) of an ECDSA P-256 key, an uncompressed point "
        "on the curve, or of an Ed25519 key that decodes (RFC 8032) and is not of small order"
    ),
    extra=keys.problem,
)

FORMATS = (TIMESTAMP, SBOM_VERSION, TAG_PREFIX, REASON, PUBLIC_KEY)

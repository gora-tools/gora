"""Showing untrusted text literally — one rule for every output (CLI, Slack).

A leaf module: it imports nothing from gora, so any layer may use it.
"""

import unicodedata

# Unicode general categories a terminal acts on, or renders as nothing, rather than
# shows: controls (Cc: C0, DEL, C1), format characters (Cf: bidi controls, zero-width
# characters, the BOM, the soft hyphen, tags) and the line and paragraph separators.
_INVISIBLE = frozenset({"Cc", "Cf", "Zl", "Zp"})


def escape(char: str) -> str:
    """One character as visible shows it."""
    if char == "\\":
        return "\\\\"
    if unicodedata.category(char) not in _INVISIBLE:
        return char
    code = ord(char)
    if code < 0x100:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def visible(text: str) -> str:
    """The text with every invisible character shown as an escape.

    ``\\xNN`` below U+0100, ``\\uNNNN`` up to U+FFFF, ``\\UNNNNNNNN`` above, lowercase hex;
    a backslash is shown as ``\\\\``, so every escape reads back to one character.
    """
    return "".join(map(escape, text))

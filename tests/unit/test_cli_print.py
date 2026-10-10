"""The CLI's printer, given hostile text directly (TESTING, "Integration").

``_print`` is the one route every line the CLI prints takes. The matrix in
tests/integration/test_cli_literal_output.py runs the values ``gora version`` shows through
the whole command; here the printer itself is held to the same guarantee with every hostile
value: each part is printed literally, on one line, whatever it contains.
"""

import io
import re

import pytest
from rich.console import Console

import gora.transport.cli.commands as commands
from tests.integration.test_cli_literal_output import HOSTILE, _raw_controls, _shown

SGR = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def screen(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Both consoles on one narrow, colour terminal; what they wrote is returned."""
    out = io.StringIO()
    console = Console(
        file=out,
        width=40,
        force_terminal=True,
        color_system="standard",
        soft_wrap=True,
        markup=False,
        emoji=False,
        highlight=False,
    )
    monkeypatch.setattr(commands, "console", console)
    monkeypatch.setattr(commands, "err_console", console)
    return out


def _lines(screen: io.StringIO) -> list[str]:
    text = screen.getvalue()
    assert "\x1b[" in text  # calibration: the console really styles what it prints
    plain = SGR.sub("", text)
    assert _raw_controls(plain) == [], plain
    return plain.split("\n")[:-1]


@pytest.mark.parametrize("value", [pytest.param(v, id=k) for k, v in HOSTILE.items()])
def test_print_shows_every_part_literally_on_one_line(value: str, screen: io.StringIO) -> None:
    commands._print("before ", (value, "bold"), " after", style="red")

    assert _lines(screen) == [f"before {_shown(value)} after"]

"""The CLI's two printers, given hostile text directly (TESTING, "Integration").

A manifest no longer carries markup, controls or 150-character stage names into ``status``:
the identifier grammar refuses them on load. The printers are still the one route every
line takes, and paths, exception texts and remote bodies still reach them, so the guarantee
the matrix in tests/integration/test_cli_literal_output.py used to hold through manifest
content is held here: every part is printed literally, on one line, and a table stays
aligned whatever its cells contain.
"""

import io
import re

import pytest
from rich.cells import cell_len
from rich.console import Console

import gora.transport.cli.commands as commands
from gora.domain.release.manifest import StageState
from tests.integration.test_cli_literal_output import HOSTILE, _raw_controls, _shown

SGR = re.compile(r"\x1b\[[0-9;]*m")
DIGEST = "sha256:" + "a" * 64


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


@pytest.mark.parametrize("value", [pytest.param(v, id=k) for k, v in HOSTILE.items()])
def test_the_stage_table_stays_aligned_whatever_its_cells_hold(
    value: str, screen: io.StringIO
) -> None:
    # model_construct: the grammar would refuse these values, and only the printer is tested.
    stages = {
        value: StageState.model_construct(version=value, digest=DIGEST),
        "dev": StageState.model_construct(version="1.2.0", digest=DIGEST),
    }

    commands._print_stages(stages)

    lines = _lines(screen)
    assert lines[0] == "Stages"
    header, hostile, dev = lines[1:]
    assert hostile.startswith(f"{_shown(value)}  ")
    assert f"  {_shown(value)}  " in hostile

    # Every row puts its digest in the same terminal column as the header puts "Digest";
    # columns are cells, so a wide character counts twice.
    def column(line: str, text: str) -> int:
        return cell_len(line[: line.index(text)])

    assert column(hostile, DIGEST) == column(dev, DIGEST) == column(header, "Digest")

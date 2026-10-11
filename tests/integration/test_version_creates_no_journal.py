"""``gora version`` never creates the audit journal (ADR-0017; README, "Audit").

The journal exists because a mutating command ran. A command that only reads must leave no
trace: no state directory under HOME, and not the directory GORA_AUDIT_LOG names.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gora.transport.cli.commands import app

runner = CliRunner()


def test_version_creates_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "fresh"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("GORA_AUDIT_LOG")

    assert runner.invoke(app, ["version"]).exit_code == 0

    assert list(home.iterdir()) == []


def test_version_does_not_create_the_override_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GORA_AUDIT_LOG", str(tmp_path / "never" / "j.jsonl"))

    assert runner.invoke(app, ["version"]).exit_code == 0

    assert not (tmp_path / "never").exists()

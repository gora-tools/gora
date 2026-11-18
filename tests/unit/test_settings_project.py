"""``GORA_PROJECT``: the project a command acts on is a setting like any other (ADR-017, ADR-023
section 3). It is read from the environment and from nowhere else, and nothing is guessed: no
default, no value taken from a clone, a remote or the working directory."""

from pathlib import Path

import pytest

from gora.config.settings import Settings

PROJECT = "github.com/acme/mono"


def test_there_is_no_default_project() -> None:
    assert Settings().project == ""


def test_the_project_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GORA_PROJECT", PROJECT)
    assert Settings().project == PROJECT


def test_the_project_is_read_from_no_file_and_no_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(f"GORA_PROJECT={PROJECT}\n")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text('[remote "origin"]\n\turl = https://github.com/acme/mono.git\n')
    monkeypatch.chdir(tmp_path)

    assert Settings().project == ""


def test_the_project_is_not_the_organisation_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GORA_GITHUB_ORG`` is another setting: neither stands for the other."""
    monkeypatch.setenv("GORA_GITHUB_ORG", "acme")
    assert Settings().project == ""
    monkeypatch.setenv("GORA_PROJECT", PROJECT)
    assert Settings().github_org == "acme"

"""Settings read the process environment and nothing else (ADR-0017).

A ``.env`` in the working directory was the root cause of the 2026-10-01 Slack incident.
The regression runs in a child process: the conftest guard neutralises ``env_file`` in-process,
as a belt, so a test in the pytest process could not see it come back.
"""

import re
import subprocess
import sys
from pathlib import Path

import gora

SRC = Path(gora.__file__).parent
PRINT_LOG_LEVEL = "from gora.config.settings import Settings; print(Settings().log_level)"


def log_level_in_child(cwd: Path, extra_env: dict[str, str]) -> str:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "PYTHONPATH": str(SRC.parent), **extra_env}
    run = subprocess.run(
        [sys.executable, "-c", PRINT_LOG_LEVEL],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stdout.strip()


def test_a_dotenv_file_in_the_working_directory_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("GORA_LOG_LEVEL=DEBUG\n")

    assert log_level_in_child(tmp_path, {}) == "INFO"


def test_the_same_value_from_the_environment_takes_effect(tmp_path: Path) -> None:
    # Calibration: the child does read GORA_LOG_LEVEL, so the INFO above means the file
    # was ignored, not that the setting is unreadable.
    (tmp_path / ".env").write_text("GORA_LOG_LEVEL=DEBUG\n")

    assert log_level_in_child(tmp_path, {"GORA_LOG_LEVEL": "WARNING"}) == "WARNING"


def test_no_other_default_file_is_read(tmp_path: Path) -> None:
    config = tmp_path / ".config" / "gora"
    config.mkdir(parents=True)
    for name in ("config", "config.toml", "config.yaml", "settings.env", ".env"):
        (config / name).write_text("GORA_LOG_LEVEL=DEBUG\n")
    (tmp_path / "gora.env").write_text("GORA_LOG_LEVEL=DEBUG\n")

    extra = {"XDG_CONFIG_HOME": str(tmp_path / ".config")}
    assert log_level_in_child(tmp_path, extra) == "INFO"


def test_the_source_names_no_settings_file() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if re.search(r"env_file|dotenv|secrets_dir", path.read_text())
    ]
    assert offenders == []

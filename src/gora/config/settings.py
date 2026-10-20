"""GORA application settings."""

import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Global application configuration.

    Read from the process environment only (``GORA_`` prefix). No file is read: an
    environment file in the working directory is ignored, so what a command does never
    depends on the directory it was started in (ADR-0017).
    """

    # GitHub
    github_org: str = "gora-tools"

    # Slack (ADR-015) — optional; disabled unless GORA_SLACK_ENABLED=true
    slack_enabled: bool = False
    slack_bot_token: str = ""  # xoxb-… (post messages + commands)
    slack_app_token: str = ""  # xapp-… (Socket Mode, incoming — later)
    slack_signing_secret: str = ""  # verify HTTP-mode requests
    slack_channel: str = ""  # default notification channel id

    # Logging
    log_level: str = "INFO"

    # git (ADR-006): the program the Git adapter runs, by an absolute name, never looked up
    # on PATH. The release image installs it here (deployments/docker/Dockerfile).
    git: str = "/usr/bin/git"

    model_config = {"env_prefix": "GORA_", "extra": "ignore"}

    @field_validator("git")
    @classmethod
    def _git_is_absolute(cls, value: str) -> str:
        if not os.path.isabs(value):
            raise ValueError("GORA_GIT must be an absolute path")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance, read once from the environment.

    Cached so a long-running process (the API) does not re-read the environment
    on every request. Tests reset the cache via an autouse fixture (conftest).
    """
    return Settings()

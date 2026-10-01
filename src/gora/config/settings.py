"""GORA application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Global application configuration.

    Read from the process environment only (``GORA_`` prefix). No file is read: an
    environment file in the working directory is ignored, so what a command does never
    depends on the directory it was started in (ADR-0017).
    """

    # GitHub
    github_token: str = ""
    github_org: str = "zuwarskejes-labs"

    # Slack (ADR-015) — optional; disabled unless GORA_SLACK_ENABLED=true
    slack_enabled: bool = False
    slack_bot_token: str = ""  # xoxb-… (post messages + commands)
    slack_app_token: str = ""  # xapp-… (Socket Mode, incoming — later)
    slack_signing_secret: str = ""  # verify HTTP-mode requests
    slack_channel: str = ""  # default notification channel id

    # Logging
    log_level: str = "INFO"

    # GORA
    manifest_repo: str = ""
    manifest_path: Path = Path("release-manifest.yaml")

    model_config = {"env_prefix": "GORA_", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance, read once from the environment.

    Cached so a long-running process (the API) does not re-read the environment
    on every request. Tests reset the cache via an autouse fixture (conftest).
    """
    return Settings()

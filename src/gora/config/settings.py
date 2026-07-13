"""GORA application settings."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Global application configuration.

    Loaded from environment variables and .env file.
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

    model_config = {"env_prefix": "GORA_", "env_file": ".env", "extra": "ignore"}


def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()

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

    # Logging
    log_level: str = "INFO"

    # GORA
    manifest_repo: str = ""
    manifest_path: Path = Path("release-manifest.yaml")

    model_config = {"env_prefix": "GORA_", "env_file": ".env", "extra": "ignore"}


def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()

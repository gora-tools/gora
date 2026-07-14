"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest

from gora.config.settings import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Read settings fresh in every test — get_settings is lru_cached."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

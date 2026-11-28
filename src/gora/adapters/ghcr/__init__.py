"""GHCR tool adapter: a read-only registry adapter for public packages."""

from gora.adapters.ghcr.adapter import GhcrRegistry, RegistryError

__all__ = ["GhcrRegistry", "RegistryError"]

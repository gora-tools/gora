"""Promotion logic.

Re-exports PromotionResult and provides promotion helpers.
Core promotion logic lives in ReleaseManifest.promote() and .rollback().
"""

from gora.domain.release.manifest import PromotionResult

__all__ = ["PromotionResult"]

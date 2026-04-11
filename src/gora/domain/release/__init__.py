"""Release domain — manifest, candidate, promotion models."""

from gora.domain.release.candidate import ReleaseCandidate
from gora.domain.release.manifest import ManifestVersion, ReleaseManifest, StageState
from gora.domain.release.promotion import PromotionResult

__all__ = [
    "ManifestVersion",
    "PromotionResult",
    "ReleaseCandidate",
    "ReleaseManifest",
    "StageState",
]

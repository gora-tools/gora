"""Release Candidate model.

An RC maps 1:1 to an immutable artifact digest.
It is the current testable candidate for release.
See ADR-003 for the full RC model specification.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ReleaseCandidate(BaseModel):
    """A release candidate — a concrete, immutable artifact selected for validation.

    Rules (from ADR-003):
    - RC MUST map 1:1 to an immutable artifact digest
    - Only one active RC exists at a time per release line
    - A new RC supersedes the previous active RC
    - RC validation and final promotion MUST refer to the same digest
    """

    version: str = Field(
        ...,
        description="Release identity, e.g. '1.2.0-rc.3'",
        examples=["1.2.0-rc.3"],
    )
    image: str = Field(
        ...,
        description="Full image reference without tag/digest",
        examples=["ghcr.io/zuwarskejes-labs/gora"],
    )
    digest: str = Field(
        ...,
        description="Immutable artifact digest (sha256)",
        pattern=r"^sha256:[a-f0-9]{64}$",
        examples=["sha256:abc123def456..."],
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when RC was cut",
    )

    @property
    def image_ref(self) -> str:
        """Full image reference with digest (pull-safe)."""
        return f"{self.image}@{self.digest}"

    @property
    def tagged_ref(self) -> str:
        """Image reference with version tag (human-readable)."""
        return f"{self.image}:{self.version}"

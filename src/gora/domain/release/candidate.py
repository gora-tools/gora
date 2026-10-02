"""Release Candidate model.

An RC maps 1:1 to an immutable artifact digest.
It is the current testable candidate for release.
See ADR-003 for the full RC model specification.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from gora.domain.release.identifiers import Digest, Image, Version


class ReleaseCandidate(BaseModel):
    """A release candidate — a concrete, immutable artifact selected for validation.

    Rules (from ADR-003):
    - RC MUST map 1:1 to an immutable artifact digest
    - Only one active RC exists at a time per release line
    - A new RC supersedes the previous active RC
    - RC validation and final promotion MUST refer to the same digest

    Every field is checked against the identifier grammar (ADR-003). ``created_at`` is
    what the manifest says: absent stays absent, on load and on write. ``cut-rc`` sets it.
    """

    model_config = ConfigDict(extra="forbid")

    version: Version = Field(
        ...,
        description="Release identity, e.g. '1.2.0-rc.3'",
        examples=["1.2.0-rc.3"],
    )
    image: Image = Field(
        ...,
        description="Full image reference without tag/digest",
        examples=["ghcr.io/zuwarskejes-labs/gora"],
    )
    digest: Digest = Field(
        ...,
        description="Immutable artifact digest (sha256)",
        examples=["sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    )
    created_at: datetime | None = Field(
        default=None,
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

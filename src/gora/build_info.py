"""Which GORA is running: the source commit it was built from, and the version it runs as.

The artifact does not carry its version (L9, ADR-022 section 3): one image is first
``X.Y.Z-rc.N`` and then ``X.Y.Z``. It carries its source commit, which the release image sets
as ``GORA_REVISION`` from its build; that is the authority. The version is informational: a
deployment sets it as ``GORA_VERSION``.
"""

import os
from collections.abc import Mapping
from typing import NamedTuple

UNKNOWN = "unknown"
NOT_SET = "not set"


class BuildInfo(NamedTuple):
    revision: str
    version: str


def build_info(environ: Mapping[str, str] = os.environ) -> BuildInfo:
    """The revision and the version from ``environ``; an empty value counts as absent."""
    return BuildInfo(
        revision=environ.get("GORA_REVISION") or UNKNOWN,
        version=environ.get("GORA_VERSION") or NOT_SET,
    )

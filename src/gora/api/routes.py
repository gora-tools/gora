"""Read-only HTTP endpoints over the release manifest (ADR-014).

Thin transport: reads the same Git-native manifest the CLI uses. No release logic
here. Mutating endpoints (behind auth) and `/history` come in later slices.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from gora.config.settings import Settings, get_settings
from gora.domain.release.manifest import ReleaseManifest, UnsupportedManifestError

router = APIRouter()


def manifest_path(settings: Annotated[Settings, Depends(get_settings)]) -> Path:
    return settings.manifest_path


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/status")
def status(path: Annotated[Path, Depends(manifest_path)]) -> dict[str, Any]:
    """Current release state (candidate + stages) from the manifest.

    A manifest this engine cannot represent is a 500 with the reason in ``detail``:
    the request is valid and no change to it would help, so it is not a 4xx, and
    the condition holds until the engine or the manifest changes, so it is not a 503.
    """
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no manifest at {path}")
    try:
        manifest = ReleaseManifest.from_yaml(path)
    except UnsupportedManifestError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    data: dict[str, Any] = manifest.model_dump(mode="json", exclude_none=True)
    return data

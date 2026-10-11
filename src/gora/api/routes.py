"""HTTP routes. Today only the liveness probe; the API of release-model section 11 is decided,
not built.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}

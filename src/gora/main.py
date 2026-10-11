"""FastAPI application entry point: GORA's API surface (ADR-014).

Run with `make run-api` (uvicorn gora.main:app). Transports stay thin; no release logic
lives here.
"""

from fastapi import FastAPI

from gora.api.routes import router
from gora.build_info import build_info


def create_app() -> FastAPI:
    app = FastAPI(title="GORA", version=build_info().version)
    app.include_router(router)
    return app


app = create_app()

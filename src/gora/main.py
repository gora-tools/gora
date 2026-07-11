"""FastAPI application entry point — GORA's read/API surface (ADR-014).

Run with `make run-api` (uvicorn gora.main:app). Transports stay thin: the API
reads the same Git-native manifest the CLI uses; no release logic lives here.
"""

from fastapi import FastAPI

from gora import __version__
from gora.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="GORA", version=__version__)
    app.include_router(router)
    return app


app = create_app()

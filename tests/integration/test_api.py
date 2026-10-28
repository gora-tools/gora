"""Integration tests for the HTTP API (no network — TestClient)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gora.main import app, create_app


def test_healthz() -> None:
    with TestClient(app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_the_api_process_attaches_no_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "api-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("GORA_AUDIT_LOG")

    # The lifespan runs inside ``with``: startup on entry, shutdown on exit.
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert logging.getLogger("gora.audit").handlers == []

    assert logging.getLogger("gora.audit").handlers == []
    assert list(home.iterdir()) == []


def test_a_test_client_runs_the_lifespan_on_entry_and_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The calibration of the two tests above: their lifespan is not skipped. Wraps the app's
    own lifespan, whatever it does, and shows when the wrapper's two halves run."""
    fresh: FastAPI = create_app()
    original = fresh.router.lifespan_context
    events: list[str] = []

    @asynccontextmanager
    async def recording(application: FastAPI) -> AsyncIterator[None]:
        events.append("startup")
        async with original(application):
            yield
        events.append("shutdown")

    monkeypatch.setattr(fresh.router, "lifespan_context", recording)

    client = TestClient(fresh)
    assert client.get("/healthz").status_code == 200
    assert events == []  # without ``with``, no lifespan

    with client:
        assert events == ["startup"]
        assert client.get("/healthz").status_code == 200
        assert events == ["startup"]
    assert events == ["startup", "shutdown"]

"""Integration tests for the HTTP API (no network — TestClient)."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gora.main import app


def test_healthz() -> None:
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_the_api_process_attaches_no_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "api-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.delenv("GORA_AUDIT_LOG")

    assert TestClient(app).get("/healthz").status_code == 200

    assert logging.getLogger("gora.audit").handlers == []
    assert list(home.iterdir()) == []

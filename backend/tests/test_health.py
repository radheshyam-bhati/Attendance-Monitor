"""Smoke tests for the initial backend foundation."""

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.main import app


def test_health_check() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_initialization_creates_sqlite_file(tmp_path) -> None:
    database_path = tmp_path / "attendance.db"

    initialize_database(f"sqlite:///{database_path}")

    assert database_path.exists()
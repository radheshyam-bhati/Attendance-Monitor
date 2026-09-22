"""Tests for FastAPI endpoints."""

import pytest
from datetime import date, datetime
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from app.main import app
from app.models import CheckResult, SubjectAttendanceRecord


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_settings():
    with patch("app.main.get_settings_instance") as mock:
        settings = MagicMock()
        settings.database_url = "sqlite:///:memory:"
        settings.get_check_time.return_value = "16:30"
        settings.get_timezone.return_value = "Asia/Kolkata"
        mock.return_value = settings
        yield settings


@pytest.fixture
def mock_runtime_settings():
    with patch("app.main.get_runtime_settings") as mock:
        runtime = MagicMock()
        runtime.get.return_value = None
        runtime.set = MagicMock()
        runtime.load_into_settings = MagicMock()
        mock.return_value = runtime
        yield runtime


class TestHealthEndpoints:
    def test_health_check(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_status_endpoint(self, client, mock_settings, mock_runtime_settings):
        with patch("app.main.get_scheduler", return_value=None):
            response = client.get("/api/status")
            assert response.status_code == 200
            data = response.json()
            assert "scheduler_running" in data
            assert "check_time" in data
            assert "timezone" in data


class TestScheduleEndpoints:
    def test_get_schedule(self, client, mock_settings, mock_runtime_settings):
        with patch("app.main.get_scheduler", return_value=None):
            response = client.get("/api/schedule")
            assert response.status_code == 200
            data = response.json()
            assert "check_time" in data
            assert "timezone" in data
            assert "next_scheduled_check" in data

    def test_update_schedule_valid(self, client, mock_settings, mock_runtime_settings):
        with patch("app.main.get_scheduler", return_value=None):
            response = client.put(
                "/api/schedule",
                json={"check_time": "17:00", "timezone": "America/New_York"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["check_time"] == "17:00"
            assert data["timezone"] == "America/New_York"

    def test_update_schedule_invalid_time(self, client, mock_settings, mock_runtime_settings):
        response = client.put(
            "/api/schedule",
            json={"check_time": "25:00", "timezone": "Asia/Kolkata"}
        )
        assert response.status_code == 400

    def test_update_schedule_invalid_format(self, client, mock_settings, mock_runtime_settings):
        response = client.put(
            "/api/schedule",
            json={"check_time": "5:00 PM", "timezone": "Asia/Kolkata"}
        )
        assert response.status_code == 400


class TestHistoryEndpoints:
    def test_get_history_empty(self, client, mock_settings):
        with patch("app.main.create_session_factory") as mock_factory:
            mock_session = MagicMock()
            mock_factory.return_value = lambda: mock_session
            mock_session.scalars.return_value.all.return_value = []

            response = client.get("/api/history")
            assert response.status_code == 200
            assert response.json() == []

    def test_get_latest_empty(self, client, mock_settings):
        with patch("app.main.create_session_factory") as mock_factory:
            mock_session = MagicMock()
            mock_factory.return_value = lambda: mock_session
            mock_session.scalars.return_value.first.return_value = None

            response = client.get("/api/latest")
            assert response.status_code == 200
            assert response.json() is None


class TestCheckNowEndpoint:
    @pytest.mark.asyncio
    async def test_check_now_success(self, client, mock_settings, mock_runtime_settings):
        mock_check_result = CheckResult(
            id=1,
            check_date=date(2026, 9, 22),
            checked_at=datetime(2026, 9, 22, 16, 30),
            attendance_date=date(2026, 9, 22),
            batch="Batch A",
            present_count=2,
            absent_count=0,
            email_status="sent",
            subjects=[
                SubjectAttendanceRecord(id=1, subject_name="Math", period=1, status="PRESENT"),
                SubjectAttendanceRecord(id=2, subject_name="Physics", period=2, status="PRESENT"),
            ],
        )

        with patch("app.main.run_attendance_check", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = mock_check_result
            response = client.post("/api/check-now")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["check_date"] == "2026-09-22"
        assert len(data["subjects"]) == 2

    @pytest.mark.asyncio
    async def test_check_now_failure(self, client, mock_settings, mock_runtime_settings):
        with patch("app.main.run_attendance_check", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = Exception("Browser not authenticated")
            response = client.post("/api/check-now")

        assert response.status_code == 500


class TestGmailEndpoints:
    def test_gmail_status(self, client, mock_settings):
        with patch("app.main.create_notifier") as mock_notifier_factory:
            mock_notifier = MagicMock()
            mock_notifier.is_authorized.return_value = True
            mock_notifier.get_authorized_email.return_value = "test@example.com"
            mock_notifier_factory.return_value = mock_notifier

            response = client.get("/api/gmail-status")
            assert response.status_code == 200
            data = response.json()
            assert data["authorized"] is True
            assert data["email"] == "test@example.com"

    def test_gmail_authorize_info(self, client, mock_settings):
        with patch("app.main.create_notifier") as mock_notifier_factory:
            mock_notifier = MagicMock()
            mock_notifier.is_authorized.return_value = False
            mock_notifier_factory.return_value = mock_notifier

            response = client.post("/api/gmail/authorize")
            assert response.status_code == 200
            data = response.json()
            assert "message" in data
            assert data["authorized"] is False


class TestDashboardEndpoint:
    def test_dashboard_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "PWIOI Attendance Monitor" in response.text
"""Tests for Gmail notifier."""

import pytest
from datetime import date
from unittest.mock import patch, MagicMock, PropertyMock

from app.notifier import GmailNotifier, create_notifier
from app.models import TodayAttendance, SubjectAttendance, AttendanceStatus
from app.config import Settings


class TestGmailNotifier:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock(spec=Settings)
        settings.google_client_secret_file = "client_secret.json"
        settings.gmail_token_file = "data/gmail_token.json"
        return settings

    @pytest.fixture
    def notifier(self, mock_settings):
        return GmailNotifier(mock_settings)

    @pytest.fixture
    def sample_attendance(self):
        return TodayAttendance(
            attendance_date=date(2026, 9, 22),
            batch="Batch A",
            present_count=2,
            absent_count=1,
            subjects=[
                SubjectAttendance(subject_name="Mathematics", status=AttendanceStatus.PRESENT, period=1),
                SubjectAttendance(subject_name="Physics", status=AttendanceStatus.PRESENT, period=2),
                SubjectAttendance(subject_name="Chemistry", status=AttendanceStatus.ABSENT, period=3),
            ],
        )

    def test_is_authorized_no_token_file(self, notifier):
        with patch("app.notifier.Path.exists", return_value=False):
            assert notifier.is_authorized() is False

    def test_is_authorized_valid_token(self, notifier):
        mock_creds = MagicMock()
        mock_creds.valid = True

        with patch("app.notifier.Path.exists", return_value=True), \
             patch("app.notifier.Credentials.from_authorized_user_file", return_value=mock_creds):
            assert notifier.is_authorized() is True

    def test_is_authorized_expired_with_refresh(self, notifier):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token"

        with patch("app.notifier.Path.exists", return_value=True), \
             patch("app.notifier.Credentials.from_authorized_user_file", return_value=mock_creds), \
             patch("app.notifier.Request"):
            assert notifier.is_authorized() is True

    def test_is_authorized_expired_no_refresh(self, notifier):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = None

        with patch("app.notifier.Path.exists", return_value=True), \
             patch("app.notifier.Credentials.from_authorized_user_file", return_value=mock_creds):
            assert notifier.is_authorized() is False

    def test_load_credentials_success(self, notifier):
        mock_creds = MagicMock()
        mock_creds.valid = True

        with patch("app.notifier.Path.exists", return_value=True), \
             patch("app.notifier.Credentials.from_authorized_user_file", return_value=mock_creds):
            creds = notifier._load_credentials()
            assert creds == mock_creds

    def test_load_credentials_refresh_expired(self, notifier):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token"

        with patch("app.notifier.Path.exists", return_value=True), \
             patch("app.notifier.Credentials.from_authorized_user_file", return_value=mock_creds), \
             patch("app.notifier.Request") as mock_request, \
             patch("app.notifier.Path.write_text") as mock_write:
            creds = notifier._load_credentials()
            mock_creds.refresh.assert_called_once_with(mock_request.return_value)
            mock_write.assert_called_once()

    def test_load_credentials_no_file(self, notifier):
        with patch("app.notifier.Path.exists", return_value=False):
            creds = notifier._load_credentials()
            assert creds is None

    def test_create_message(self, notifier):
        message = notifier._create_message("to@example.com", "Test Subject", "Test Body")
        assert "raw" in message
        # Verify it's base64 encoded
        import base64
        decoded = base64.urlsafe_b64decode(message["raw"]).decode()
        assert "To: to@example.com" in decoded
        assert "Subject: Test Subject" in decoded
        assert "Test Body" in decoded

    def test_format_subject(self, notifier, sample_attendance):
        subject = notifier._format_subject(sample_attendance)
        assert "Attendance Report" in subject
        assert "22 September 2026" in subject

    def test_format_report(self, notifier, sample_attendance):
        report = notifier._format_report(sample_attendance)
        assert "Attendance Report" in report
        assert "22 September 2026" in report
        assert "Mathematics" in report
        assert "Physics" in report
        assert "Chemistry" in report
        assert "✅ PRESENT" in report
        assert "🔴 ABSENT" in report
        assert "Present: 2" in report
        assert "Absent: 1" in report
        assert "Not Marked: 0" in report

    def test_format_report_empty_subjects(self, notifier):
        attendance = TodayAttendance(
            attendance_date=date(2026, 9, 22),
            subjects=[],
        )
        report = notifier._format_report(attendance)
        assert "No classes scheduled for today" in report

    def test_status_icons(self, notifier):
        assert notifier._status_icon(AttendanceStatus.PRESENT) == "✅"
        assert notifier._status_icon(AttendanceStatus.ABSENT) == "🔴"
        assert notifier._status_icon(AttendanceStatus.NOT_MARKED) == "🟡"
        assert notifier._status_icon(AttendanceStatus.UNKNOWN) == "❓"

    @pytest.mark.asyncio
    async def test_send_report_not_authorized(self, notifier, sample_attendance):
        with patch.object(notifier, "is_authorized", return_value=False):
            success, error = await notifier.send_attendance_report(sample_attendance, "to@example.com")
            assert success is False
            assert "not authorized" in error.lower()

    @pytest.mark.asyncio
    async def test_send_report_no_service(self, notifier, sample_attendance):
        with patch.object(notifier, "is_authorized", return_value=True), \
             patch.object(notifier, "_get_service", return_value=None):
            success, error = await notifier.send_attendance_report(sample_attendance, "to@example.com")
            assert success is False
            assert "not authorized" in error.lower()

    @pytest.mark.asyncio
    async def test_send_report_success(self, notifier, sample_attendance):
        mock_service = MagicMock()
        mock_service.users().messages().send().execute.return_value = {"id": "msg123"}

        with patch.object(notifier, "is_authorized", return_value=True), \
             patch.object(notifier, "_get_service", return_value=mock_service):
            success, error = await notifier.send_attendance_report(sample_attendance, "to@example.com")
            assert success is True
            assert error is None
            mock_service.users().messages().send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_report_http_error(self, notifier, sample_attendance):
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 403
        mock_service.users().messages().send().execute.side_effect = HttpError(mock_resp, b"Forbidden")

        with patch.object(notifier, "is_authorized", return_value=True), \
             patch.object(notifier, "_get_service", return_value=mock_service):
            success, error = await notifier.send_attendance_report(sample_attendance, "to@example.com")
            assert success is False
            assert "Gmail API error" in error


class TestCreateNotifier:
    def test_factory(self):
        settings = MagicMock(spec=Settings)
        notifier = create_notifier(settings)
        assert isinstance(notifier, GmailNotifier)
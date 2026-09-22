"""Tests for attendance check workflow."""

import pytest
from datetime import date, datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

from app.workflow import AttendanceWorkflow, run_attendance_check, AttendanceCheckError
from app.models import TodayAttendance, SubjectAttendance, AttendanceStatus, CheckResult
from app.config import Settings


class TestAttendanceWorkflow:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock(spec=Settings)
        settings.database_url = "sqlite:///:memory:"
        settings.log_level = "INFO"
        return settings

    @pytest.fixture
    def mock_browser(self):
        browser = AsyncMock()
        browser.start = AsyncMock()
        browser.close = AsyncMock()
        browser.open_page = AsyncMock()
        return browser

    @pytest.fixture
    def mock_portal(self):
        portal = MagicMock()
        portal.open_attendance_page = AsyncMock()
        portal.is_login_page.return_value = False
        portal.is_attendance_page.return_value = True
        portal.current_url.return_value = "https://app.pwioi.club/dashboard/student/attendance"
        return portal

    @pytest.fixture
    def mock_notifier(self):
        notifier = MagicMock()
        notifier.is_authorized.return_value = False
        notifier.get_authorized_email.return_value = None
        notifier.send_attendance_report = AsyncMock(return_value=(False, "Not authorized"))
        return notifier

    @pytest.fixture
    def sample_attendance(self):
        return TodayAttendance(
            attendance_date=date(2026, 9, 22),
            batch="Batch A",
            present_count=2,
            absent_count=0,
            subjects=[
                SubjectAttendance(subject_name="Mathematics", status=AttendanceStatus.PRESENT, period=1),
                SubjectAttendance(subject_name="Physics", status=AttendanceStatus.PRESENT, period=2),
            ],
        )

    @pytest.mark.asyncio
    async def test_run_check_success(
        self, mock_settings, mock_browser, mock_portal, mock_notifier, sample_attendance
    ):
        with patch("app.workflow.BrowserManager", return_value=mock_browser), \
             patch("app.workflow.PWIOIPortal", return_value=mock_portal), \
             patch("app.workflow.GmailNotifier", return_value=mock_notifier), \
             patch("app.workflow.parse_attendance", new_callable=AsyncMock) as mock_parse, \
             patch("app.workflow.initialize_database") as mock_init_db:

            mock_parse.return_value = sample_attendance

            # Mock database session
            mock_session_factory = MagicMock()
            mock_session = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_session.get.return_value = CheckResult(
                id=1,
                check_date=date(2026, 9, 22),
                checked_at=datetime.now(),
                attendance_date=date(2026, 9, 22),
                batch="Batch A",
                present_count=2,
                absent_count=0,
                email_status="pending",
            )

            with patch("app.workflow.create_session_factory", return_value=mock_session_factory):
                workflow = AttendanceWorkflow(mock_settings)
                result = await workflow.run_check(manual=True)

            assert result is not None
            assert result.attendance_date == date(2026, 9, 22)
            assert len(result.subjects) == 2

    @pytest.mark.asyncio
    async def test_run_check_not_authenticated(
        self, mock_settings, mock_browser, mock_portal, mock_notifier
    ):
        mock_portal.is_login_page.return_value = True

        with patch("app.workflow.BrowserManager", return_value=mock_browser), \
             patch("app.workflow.PWIOIPortal", return_value=mock_portal), \
             patch("app.workflow.GmailNotifier", return_value=mock_notifier), \
             patch("app.workflow.initialize_database"):

            workflow = AttendanceWorkflow(mock_settings)
            with pytest.raises(AttendanceCheckError, match="Not authenticated"):
                await workflow.run_check(manual=True)

    @pytest.mark.asyncio
    async def test_run_check_not_attendance_page(
        self, mock_settings, mock_browser, mock_portal, mock_notifier
    ):
        mock_portal.is_login_page.return_value = False
        mock_portal.is_attendance_page.return_value = False

        with patch("app.workflow.BrowserManager", return_value=mock_browser), \
             patch("app.workflow.PWIOIPortal", return_value=mock_portal), \
             patch("app.workflow.GmailNotifier", return_value=mock_notifier), \
             patch("app.workflow.initialize_database"):

            workflow = AttendanceWorkflow(mock_settings)
            with pytest.raises(AttendanceCheckError, match="Unexpected page"):
                await workflow.run_check(manual=True)

    @pytest.mark.asyncio
    async def test_run_check_parse_error(
        self, mock_settings, mock_browser, mock_portal, mock_notifier
    ):
        from app.attendance import AttendanceParseError

        with patch("app.workflow.BrowserManager", return_value=mock_browser), \
             patch("app.workflow.PWIOIPortal", return_value=mock_portal), \
             patch("app.workflow.GmailNotifier", return_value=mock_notifier), \
             patch("app.workflow.parse_attendance", new_callable=AsyncMock) as mock_parse, \
             patch("app.workflow.initialize_database"):

            mock_parse.side_effect = AttendanceParseError("Could not find attendance table")

            workflow = AttendanceWorkflow(mock_settings)
            with pytest.raises(AttendanceCheckError, match="Failed to parse attendance"):
                await workflow.run_check(manual=True)

    @pytest.mark.asyncio
    async def test_run_check_with_gmail_authorized(
        self, mock_settings, mock_browser, mock_portal, mock_notifier, sample_attendance
    ):
        mock_notifier.is_authorized.return_value = True
        mock_notifier.get_authorized_email.return_value = "student@example.com"
        mock_notifier.send_attendance_report.return_value = (True, None)

        with patch("app.workflow.BrowserManager", return_value=mock_browser), \
             patch("app.workflow.PWIOIPortal", return_value=mock_portal), \
             patch("app.workflow.GmailNotifier", return_value=mock_notifier), \
             patch("app.workflow.parse_attendance", new_callable=AsyncMock) as mock_parse, \
             patch("app.workflow.initialize_database"):

            mock_parse.return_value = sample_attendance

            mock_session_factory = MagicMock()
            mock_session = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_check = CheckResult(
                id=1,
                check_date=date(2026, 9, 22),
                checked_at=datetime.now(),
                attendance_date=date(2026, 9, 22),
                batch="Batch A",
                present_count=2,
                absent_count=0,
                email_status="pending",
            )
            mock_session.get.return_value = mock_check

            with patch("app.workflow.create_session_factory", return_value=mock_session_factory):
                workflow = AttendanceWorkflow(mock_settings)
                result = await workflow.run_check(manual=True)

            mock_notifier.send_attendance_report.assert_called_once()
            assert result.email_status == "sent"

    @pytest.mark.asyncio
    async def test_run_check_gmail_failure(
        self, mock_settings, mock_browser, mock_portal, mock_notifier, sample_attendance
    ):
        mock_notifier.is_authorized.return_value = True
        mock_notifier.get_authorized_email.return_value = "student@example.com"
        mock_notifier.send_attendance_report.return_value = (False, "API error")

        with patch("app.workflow.BrowserManager", return_value=mock_browser), \
             patch("app.workflow.PWIOIPortal", return_value=mock_portal), \
             patch("app.workflow.GmailNotifier", return_value=mock_notifier), \
             patch("app.workflow.parse_attendance", new_callable=AsyncMock) as mock_parse, \
             patch("app.workflow.initialize_database"):

            mock_parse.return_value = sample_attendance

            mock_session_factory = MagicMock()
            mock_session = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_check = CheckResult(
                id=1,
                check_date=date(2026, 9, 22),
                checked_at=datetime.now(),
                attendance_date=date(2026, 9, 22),
                batch="Batch A",
                present_count=2,
                absent_count=0,
                email_status="pending",
            )
            mock_session.get.return_value = mock_check

            with patch("app.workflow.create_session_factory", return_value=mock_session_factory):
                workflow = AttendanceWorkflow(mock_settings)
                result = await workflow.run_check(manual=True)

            assert result.email_status == "failed"


class TestRunAttendanceCheckConvenience:
    @pytest.mark.asyncio
    async def test_convenience_function(self):
        mock_settings = MagicMock(spec=Settings)
        mock_settings.database_url = "sqlite:///:memory:"

        with patch("app.workflow.AttendanceWorkflow") as mock_workflow_class:
            mock_workflow = AsyncMock()
            mock_workflow.run_check.return_value = CheckResult(
                id=1,
                check_date=date(2026, 9, 22),
                attendance_date=date(2026, 9, 22),
            )
            mock_workflow_class.return_value = mock_workflow

            result = await run_attendance_check(mock_settings, manual=True)

            assert result.id == 1
            mock_workflow.run_check.assert_called_once_with(manual=True)
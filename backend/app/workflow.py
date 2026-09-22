"""Attendance check workflow orchestration.

This module coordinates the complete attendance check process:
1. Open authenticated browser session
2. Navigate to PWIOI attendance page
3. Parse today's attendance data
4. Store results in database
5. Generate and send email report via Gmail
6. Update check result with email status
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from playwright.async_api import Page

from .attendance import AttendanceParseError, parse_attendance
from .browser import BrowserManager
from .config import Settings
from .database import create_session_factory
from .models import (
    AttendanceStatus,
    CheckResult,
    SubjectAttendanceRecord,
    TodayAttendance,
)
from .notifier import GmailNotifier
from .portal import PWIOIPortal

logger = logging.getLogger(__name__)


class AttendanceCheckError(Exception):
    """Raised when the attendance check workflow fails."""

    pass


class AttendanceWorkflow:
    """Orchestrates the complete attendance check process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._browser = BrowserManager(settings)
        self._portal = PWIOIPortal(settings)
        self._notifier = GmailNotifier(settings)
        self._session_factory = create_session_factory(settings.database_url)

    async def run_check(self, *, manual: bool = False) -> CheckResult:
        """Run the complete attendance check workflow.

        Args:
            manual: If True, this is a manual check (not scheduled).

        Returns:
            The CheckResult database record.
        """
        logger.info("Starting attendance check workflow (manual=%s)", manual)

        # Initialize database
        from .database import initialize_database
        initialize_database(self._settings.database_url)

        # Start browser
        await self._browser.start()

        check_result = None
        try:
            # Step 1: Open attendance page with authentication check
            page = await self._browser.open_page()
            await self._portal.open_attendance_page(page)

            # Check if we're on login page
            if self._portal.is_login_page(page):
                error_msg = "Not authenticated: browser session expired or not logged in"
                logger.error(error_msg)
                check_result = await self._store_failed_check(error_msg)
                raise AttendanceCheckError(error_msg)

            if not self._portal.is_attendance_page(page):
                error_msg = f"Unexpected page after navigation: {page.url}"
                logger.error(error_msg)
                check_result = await self._store_failed_check(error_msg)
                raise AttendanceCheckError(error_msg)

            # Step 2: Parse attendance data
            logger.info("Parsing attendance from portal")
            attendance = await parse_attendance(page)

            # Step 3: Store check result in database
            check_result = await self._store_check_result(attendance)

            # Step 4: Send email report if authorized
            if self._notifier.is_authorized():
                recipient = self._notifier.get_authorized_email()
                if recipient:
                    logger.info("Sending attendance report to %s", recipient)
                    success, error = await self._notifier.send_attendance_report(attendance, recipient)
                    await self._update_email_status(check_result.id, success, error)
                else:
                    logger.warning("Gmail authorized but could not determine recipient email")
                    await self._update_email_status(check_result.id, False, "Could not determine recipient email")
            else:
                logger.info("Gmail not authorized; skipping email delivery")
                await self._update_email_status(check_result.id, False, "Gmail not authorized")

            # Refresh check_result to get updated email status
            check_result = await self._get_check_result(check_result.id)

            logger.info("Attendance check workflow completed successfully")
            return check_result

        except AttendanceParseError as exc:
            error_msg = f"Failed to parse attendance: {exc}"
            logger.error(error_msg)
            if check_result is None:
                check_result = await self._store_failed_check(error_msg)
            else:
                await self._update_check_error(check_result.id, error_msg)
            raise AttendanceCheckError(error_msg) from exc

        except Exception as exc:  # noqa: BLE001
            error_msg = f"Attendance check workflow failed: {exc}"
            logger.exception(error_msg)
            if check_result is None:
                check_result = await self._store_failed_check(error_msg)
            else:
                await self._update_check_error(check_result.id, error_msg)
            raise AttendanceCheckError(error_msg) from exc

        finally:
            await self._browser.close()

    async def _store_check_result(self, attendance: TodayAttendance) -> CheckResult:
        """Store the check result and subject records in the database."""
        session = self._session_factory()
        try:
            check_result = CheckResult(
                check_date=date.today(),
                checked_at=datetime.utcnow(),
                attendance_date=attendance.attendance_date,
                batch=attendance.batch,
                present_count=attendance.present_count,
                absent_count=attendance.absent_count,
                email_status="pending",
            )

            for subj in attendance.subjects:
                record = SubjectAttendanceRecord(
                    subject_name=subj.subject_name,
                    period=subj.period,
                    status=subj.status.value,
                    raw_status=subj.raw_status,
                    source=subj.source,
                )
                check_result.subjects.append(record)

            session.add(check_result)
            session.commit()
            session.refresh(check_result)
            logger.info("Stored check result (id=%d, date=%s, subjects=%d)",
                       check_result.id, check_result.attendance_date, len(check_result.subjects))
            return check_result
        except Exception as exc:
            session.rollback()
            logger.exception("Failed to store check result: %s", exc)
            raise
        finally:
            session.close()

    async def _store_failed_check(self, error_msg: str) -> CheckResult:
        """Store a failed check result."""
        session = self._session_factory()
        try:
            check_result = CheckResult(
                check_date=date.today(),
                checked_at=datetime.utcnow(),
                attendance_date=date.today(),  # Best effort
                email_status="failed",
                error_message=error_msg,
            )
            session.add(check_result)
            session.commit()
            session.refresh(check_result)
            return check_result
        finally:
            session.close()

    async def _update_email_status(self, check_id: int, success: bool, error: Optional[str]) -> None:
        """Update the email status of a check result."""
        session = self._session_factory()
        try:
            check_result = session.get(CheckResult, check_id)
            if check_result:
                check_result.email_status = "sent" if success else "failed"
                if success:
                    check_result.email_sent_at = datetime.utcnow()
                else:
                    check_result.error_message = error
                session.commit()
                logger.info("Updated email status for check %d: %s", check_id, check_result.email_status)
        except Exception as exc:
            session.rollback()
            logger.exception("Failed to update email status: %s", exc)
        finally:
            session.close()

    async def _update_check_error(self, check_id: int, error_msg: str) -> None:
        """Update the error message of a check result."""
        session = self._session_factory()
        try:
            check_result = session.get(CheckResult, check_id)
            if check_result:
                check_result.email_status = "failed"
                check_result.error_message = error_msg
                session.commit()
        finally:
            session.close()

    async def _get_check_result(self, check_id: int) -> Optional[CheckResult]:
        """Get a check result by ID."""
        session = self._session_factory()
        try:
            return session.get(CheckResult, check_id)
        finally:
            session.close()


async def run_attendance_check(settings: Settings, *, manual: bool = False) -> CheckResult:
    """Convenience function to run the attendance check workflow."""
    workflow = AttendanceWorkflow(settings)
    return await workflow.run_check(manual=manual)
"""Gmail API integration for sending attendance reports.

This module handles Google OAuth authorization and Gmail API email delivery.
All credentials and tokens are stored locally and excluded from source control.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import Settings
from .models import AttendanceStatus, TodayAttendance

logger = logging.getLogger(__name__)


# Gmail API scope - only send permission needed
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailNotifier:
    """Handles Gmail OAuth authorization and email sending via Gmail API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credentials: Optional[Credentials] = None
        self._service = None

    def _get_token_path(self) -> Path:
        """Get the path to the stored Gmail OAuth token."""
        return Path(self._settings.gmail_token_file).resolve()

    def _get_client_secret_path(self) -> Path:
        """Get the path to the Google OAuth client secret file."""
        return Path(self._settings.google_client_secret_file).resolve()

    def is_authorized(self) -> bool:
        """Check if valid Gmail authorization exists."""
        token_path = self._get_token_path()
        if not token_path.exists():
            return False
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            return creds.valid or (creds.expired and creds.refresh_token is not None)
        except Exception as exc:
            logger.debug("Failed to load credentials: %s", exc)
            return False

    async def authorize(self) -> bool:
        """Run the OAuth flow to authorize Gmail access.

        Opens a browser window for the user to grant permission.
        Stores the token locally for future use.
        """
        client_secret_path = self._get_client_secret_path()
        if not client_secret_path.exists():
            logger.error(
                "Google client secret file not found at %s. "
                "Download it from Google Cloud Console and place it at %s",
                client_secret_path,
                client_secret_path,
            )
            return False

        token_path = self._get_token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret_path), GMAIL_SCOPES
            )
            # Run local server on random port for OAuth callback
            creds = flow.run_local_server(port=0, open_browser=True)

            # Save credentials
            token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Gmail authorization saved to %s", token_path)
            self._credentials = creds
            return True
        except Exception as exc:
            logger.error("Gmail authorization failed: %s", exc)
            return False

    def _load_credentials(self) -> Optional[Credentials]:
        """Load and refresh credentials if needed."""
        if self._credentials and self._credentials.valid:
            return self._credentials

        token_path = self._get_token_path()
        if not token_path.exists():
            return None

        try:
            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            if not creds.valid and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Save refreshed token
                token_path.write_text(creds.to_json(), encoding="utf-8")
                logger.debug("Refreshed Gmail access token")
            self._credentials = creds
            return creds
        except Exception as exc:
            logger.error("Failed to load/refresh Gmail credentials: %s", exc)
            return None

    def _get_service(self):
        """Get or create the Gmail API service."""
        if self._service is None:
            creds = self._load_credentials()
            if creds is None:
                return None
            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _create_message(self, to: str, subject: str, body: str) -> dict:
        """Create a MIME email message."""
        try:
            body.encode("ascii")
            message = MIMEText(body, "plain")
        except UnicodeEncodeError:
            message = MIMEText(body, "plain", "utf-8")
        message["To"] = to
        message["Subject"] = subject
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        return {"raw": raw_message}



    async def send_attendance_report(self, attendance: TodayAttendance, recipient_email: str) -> tuple[bool, Optional[str]]:
        """Send the attendance report via Gmail API.

        Returns (success, error_message).
        """
        service = self._get_service()
        if service is None:
            return False, "Gmail not authorized. Run authorization first."

        subject = self._format_subject(attendance)
        body = self._format_report(attendance)

        try:
            message = self._create_message(recipient_email, subject, body)
            sent = service.users().messages().send(userId="me", body=message).execute()
            logger.info("Attendance report sent via Gmail API (message_id=%s)", sent.get("id"))
            return True, None
        except HttpError as exc:
            error_msg = f"Gmail API error: {exc}"
            logger.error(error_msg)
            return False, error_msg
        except Exception as exc:
            error_msg = f"Failed to send email: {exc}"
            logger.error(error_msg)
            return False, error_msg

    def _format_subject(self, attendance: TodayAttendance) -> str:
        """Format the email subject line."""
        date_str = attendance.attendance_date.strftime("%d %B %Y")
        return f"PWIOI Attendance Report — {date_str}"

    def _format_report(self, attendance: TodayAttendance) -> str:
        """Format the attendance report as plain text email body."""
        date_str = attendance.attendance_date.strftime("%d %B %Y")
        lines = [
            "Attendance Report",
            date_str,
            "",
            "Today's Attendance",
            "━" * 40,
            "",
        ]

        if not attendance.subjects:
            lines.append("No classes scheduled for today (no attendance records found).")
        else:
            # Group by period for cleaner display
            from collections import defaultdict
            by_period = defaultdict(list)
            for subj in attendance.subjects:
                period = subj.period if subj.period is not None else 0
                by_period[period].append(subj)

            for period in sorted(by_period.keys()):
                period_label = f"Period {period}" if period > 0 else "Unscheduled"
                lines.append(period_label)
                for subj in by_period[period]:
                    status_icon = self._status_icon(subj.status)
                    lines.append(f"  {subj.subject_name}")
                    lines.append(f"  {status_icon} {subj.status.value}")
                lines.append("")

        lines.append("━" * 40)
        lines.append("")
        lines.append("Summary")

        present = sum(1 for s in attendance.subjects if s.status == AttendanceStatus.PRESENT)
        absent = sum(1 for s in attendance.subjects if s.status == AttendanceStatus.ABSENT)
        not_marked = sum(1 for s in attendance.subjects if s.status == AttendanceStatus.NOT_MARKED)
        unknown = sum(1 for s in attendance.subjects if s.status == AttendanceStatus.UNKNOWN)

        lines.append(f"Present: {present}")
        lines.append(f"Absent: {absent}")
        lines.append(f"Not Marked: {not_marked}")
        if unknown:
            lines.append(f"Unknown: {unknown}")

        lines.append("")
        lines.append(f"Checked at: {datetime.now().strftime('%H:%M %Z')}")
        lines.append("")
        lines.append("─" * 40)
        lines.append("This is an automated report from your PWIOI Attendance Monitor.")
        lines.append("Data source: PWIOI Student Attendance Portal")
        lines.append("")

        return "\n".join(lines)

    def _status_icon(self, status: "AttendanceStatus") -> str:
        """Get the icon for an attendance status."""
        from .models import AttendanceStatus
        icons = {
            AttendanceStatus.PRESENT: "✅",
            AttendanceStatus.ABSENT: "🔴",
            AttendanceStatus.NOT_MARKED: "🟡",
            AttendanceStatus.UNKNOWN: "❓",
        }
        return icons.get(status, "❓")

    def get_authorized_email(self) -> Optional[str]:
        """Get the email address associated with the authorized Gmail account."""
        creds = self._load_credentials()
        if creds is None:
            return None
        try:
            service = self._get_service()
            if service:
                profile = service.users().getProfile(userId="me").execute()
                return profile.get("emailAddress")
        except Exception as exc:
            logger.debug("Could not fetch Gmail profile: %s", exc)
        return None


def create_notifier(settings: Settings) -> GmailNotifier:
    """Factory function to create a GmailNotifier instance."""
    return GmailNotifier(settings)
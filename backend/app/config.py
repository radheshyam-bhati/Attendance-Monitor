"""Application settings loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the attendance monitor.

    There is deliberately no timetable, no subject list, and no attendance
    threshold here: the PWIOI portal is the single source of truth for what
    was scheduled and what the attendance status is.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    pwioi_attendance_url: str = "https://app.pwioi.club/dashboard/student/attendance"
    browser_profile_dir: Path = Path("browser-profile")
    database_url: str = "sqlite:///./data/attendance.db"

    #: Daily check time (24h "HH:MM") in ``timezone``. Default: 4:30 PM IST.
    check_time: str = "16:30"
    timezone: str = "Asia/Kolkata"

    #: Google OAuth client secret downloaded from Google Cloud Console.
    #: Never committed; see README for how to obtain it.
    google_client_secret_file: Path = Path("client_secret.json")
    #: Stored Gmail OAuth authorization (created by `run.py --authorize-gmail`).
    #: Never committed; the token grants Gmail send access and must stay local.
    gmail_token_file: Path = Path("data/gmail_token.json")

    log_level: str = "INFO"
    headless: bool = False

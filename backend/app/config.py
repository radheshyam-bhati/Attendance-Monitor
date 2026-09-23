"""Application settings loaded from environment variables with runtime overrides."""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from .database import create_session_factory


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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._runtime_overrides = {}

    def get_check_time(self) -> str:
        """Get check time, checking runtime override first."""
        return self._runtime_overrides.get("check_time", self.check_time)

    def get_timezone(self) -> str:
        """Get timezone, checking runtime override first."""
        return self._runtime_overrides.get("timezone", self.timezone)

    def set_runtime_override(self, key: str, value: str) -> None:
        """Set a runtime override for a setting."""
        self._runtime_overrides[key] = value


# Database-backed settings store for runtime configuration
class RuntimeSettings:
    """Manage runtime-configurable settings stored in database."""

    SETTINGS_TABLE = "runtime_settings"

    def __init__(self, database_url: str):
        self._session_factory = create_session_factory(database_url)
        self._ensure_table()

    def _ensure_table(self):
        """Create the runtime_settings table if it doesn't exist."""
        engine = self._session_factory.kw["bind"]
        with engine.connect() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self.SETTINGS_TABLE} (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a runtime setting value."""
        engine = self._session_factory.kw["bind"]
        with engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT value FROM {self.SETTINGS_TABLE} WHERE key = :key"),
                {"key": key}
            ).fetchone()
            return result[0] if result else default

    def set(self, key: str, value: str) -> None:
        """Set a runtime setting value (upsert)."""
        engine = self._session_factory.kw["bind"]
        with engine.connect() as conn:
            conn.execute(text(f"""
                INSERT INTO {self.SETTINGS_TABLE} (key, value, updated_at)
                VALUES (:key, :value, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = :value, updated_at = CURRENT_TIMESTAMP
            """), {"key": key, "value": value})
            conn.commit()

    def load_into_settings(self, settings: Settings) -> None:
        """Load all runtime overrides into a Settings instance."""
        check_time = self.get("check_time")
        if check_time:
            settings.set_runtime_override("check_time", check_time)

        timezone = self.get("timezone")
        if timezone:
            settings.set_runtime_override("timezone", timezone)


# Global settings instance (initialized at startup)
_settings_instance: Optional[Settings] = None
_runtime_settings: Optional[RuntimeSettings] = None


def get_settings_instance() -> Settings:
    """Get the global settings instance, creating if needed."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def get_runtime_settings() -> RuntimeSettings:
    """Get the global runtime settings instance."""
    global _runtime_settings
    if _runtime_settings is None:
        settings = get_settings_instance()
        _runtime_settings = RuntimeSettings(settings.database_url)
    return _runtime_settings


def init_settings_with_runtime(database_url: str) -> Settings:
    """Initialize settings with runtime overrides from database."""
    global _settings_instance, _runtime_settings
    _settings_instance = get_settings_instance()
    _runtime_settings = RuntimeSettings(database_url)
    _runtime_settings.load_into_settings(_settings_instance)
    return _settings_instance
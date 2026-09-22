"""Tests for configuration and runtime settings."""

import pytest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

from app.config import Settings, RuntimeSettings, get_settings_instance, init_settings_with_runtime


class TestSettings:
    def test_default_values(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            assert settings.pwioi_attendance_url == "https://app.pwioi.club/dashboard/student/attendance"
            assert settings.check_time == "16:30"
            assert settings.timezone == "Asia/Kolkata"
            assert settings.log_level == "INFO"
            assert settings.headless is False

    def test_env_override(self):
        with patch.dict(os.environ, {
            "PWIOI_ATTENDANCE_URL": "https://custom.url/attendance",
            "CHECK_TIME": "17:00",
            "TIMEZONE": "America/New_York",
            "LOG_LEVEL": "DEBUG",
            "HEADLESS": "true",
        }, clear=True):
            settings = Settings()
            assert settings.pwioi_attendance_url == "https://custom.url/attendance"
            assert settings.check_time == "17:00"
            assert settings.timezone == "America/New_York"
            assert settings.log_level == "DEBUG"
            assert settings.headless is True

    def test_runtime_overrides(self):
        settings = Settings()
        assert settings.get_check_time() == "16:30"
        assert settings.get_timezone() == "Asia/Kolkata"

        settings.set_runtime_override("check_time", "17:00")
        settings.set_runtime_override("timezone", "UTC")

        assert settings.get_check_time() == "17:00"
        assert settings.get_timezone() == "UTC"

        # Original values unchanged
        assert settings.check_time == "16:30"
        assert settings.timezone == "Asia/Kolkata"


class TestRuntimeSettings:
    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        return engine, conn

    def test_init_creates_table(self, mock_engine):
        engine, conn = mock_engine
        with patch("app.config.create_session_factory") as mock_factory:
            mock_factory.return_value.kw = {"bind": engine}
            runtime = RuntimeSettings("sqlite:///test.db")

        # Verify CREATE TABLE was executed
        conn.execute.assert_called()

    def test_get_existing_key(self, mock_engine):
        engine, conn = mock_engine
        conn.execute.return_value.fetchone.return_value = ("17:00",)

        with patch("app.config.create_session_factory") as mock_factory:
            mock_factory.return_value.kw = {"bind": engine}
            runtime = RuntimeSettings("sqlite:///test.db")
            value = runtime.get("check_time")

        assert value == "17:00"
        conn.execute.assert_called()

    def test_get_missing_key(self, mock_engine):
        engine, conn = mock_engine
        conn.execute.return_value.fetchone.return_value = None

        with patch("app.config.create_session_factory") as mock_factory:
            mock_factory.return_value.kw = {"bind": engine}
            runtime = RuntimeSettings("sqlite:///test.db")
            value = runtime.get("nonexistent", "default")

        assert value == "default"

    def test_set_key(self, mock_engine):
        engine, conn = mock_engine

        with patch("app.config.create_session_factory") as mock_factory:
            mock_factory.return_value.kw = {"bind": engine}
            runtime = RuntimeSettings("sqlite:///test.db")
            runtime.set("check_time", "17:00")

        conn.execute.assert_called()
        conn.commit.assert_called()

    def test_load_into_settings(self, mock_engine):
        engine, conn = mock_engine
        conn.execute.return_value.fetchone.side_effect = [
            ("17:00",),  # check_time
            ("UTC",),    # timezone
        ]

        with patch("app.config.create_session_factory") as mock_factory:
            mock_factory.return_value.kw = {"bind": engine}
            runtime = RuntimeSettings("sqlite:///test.db")

        settings = Settings()
        runtime.load_into_settings(settings)

        assert settings.get_check_time() == "17:00"
        assert settings.get_timezone() == "UTC"


class TestGlobalSettings:
    def test_get_settings_instance_singleton(self):
        with patch("app.config.Settings") as mock_settings_class:
            mock_instance = MagicMock()
            mock_settings_class.return_value = mock_instance

            # First call creates instance
            s1 = get_settings_instance()
            # Second call returns same instance
            s2 = get_settings_instance()

            assert s1 == s2
            assert mock_settings_class.call_count == 1

    def test_init_settings_with_runtime(self):
        with patch("app.config.RuntimeSettings") as mock_runtime_class, \
             patch("app.config.get_settings_instance") as mock_get_settings:

            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings
            mock_settings.database_url = "sqlite:///test.db"

            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            result = init_settings_with_runtime("sqlite:///test.db")

            assert result == mock_settings
            mock_runtime.load_into_settings.assert_called_once_with(mock_settings)
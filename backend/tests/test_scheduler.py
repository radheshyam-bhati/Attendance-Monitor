"""Tests for scheduler functionality."""

import pytest
from datetime import time
from unittest.mock import patch, MagicMock, AsyncMock, call

from app.scheduler import AttendanceScheduler, create_scheduler
from app.config import Settings


class TestAttendanceScheduler:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock(spec=Settings)
        settings.check_time = "16:30"
        settings.timezone = "Asia/Kolkata"
        return settings

    @pytest.fixture
    def mock_check_func(self):
        return AsyncMock()

    def test_parse_check_time_valid(self, mock_settings):
        scheduler = AttendanceScheduler(mock_settings, AsyncMock())
        # Test via internal method
        t = scheduler._parse_check_time("16:30")
        assert t == time(16, 30)

        t = scheduler._parse_check_time("09:05")
        assert t == time(9, 5)

        t = scheduler._parse_check_time("00:00")
        assert t == time(0, 0)

        t = scheduler._parse_check_time("23:59")
        assert t == time(23, 59)

    def test_parse_check_time_invalid_defaults(self, mock_settings):
        scheduler = AttendanceScheduler(mock_settings, AsyncMock())
        t = scheduler._parse_check_time("invalid")
        assert t == time(16, 30)

        t = scheduler._parse_check_time("25:00")
        assert t == time(16, 30)

        t = scheduler._parse_check_time("12:60")
        assert t == time(16, 30)

    def test_scheduler_initialization(self, mock_settings, mock_check_func):
        scheduler = AttendanceScheduler(mock_settings, mock_check_func)
        assert scheduler._settings == mock_settings
        assert scheduler._check_func == mock_check_func
        assert scheduler._scheduler is None
        assert scheduler._check_time == time(16, 30)

    @pytest.mark.asyncio
    async def test_start_scheduler(self, mock_settings, mock_check_func):
        with patch("app.scheduler.AsyncIOScheduler") as mock_scheduler_class:
            mock_scheduler = MagicMock()
            mock_scheduler_class.return_value = mock_scheduler

            scheduler = AttendanceScheduler(mock_settings, mock_check_func)
            scheduler.start()

            mock_scheduler_class.assert_called_once()
            mock_scheduler.add_job.assert_called_once()
            mock_scheduler.start.assert_called_once()
            assert scheduler._scheduler == mock_scheduler

    @pytest.mark.asyncio
    async def test_shutdown_scheduler(self, mock_settings, mock_check_func):
        with patch("app.scheduler.AsyncIOScheduler") as mock_scheduler_class:
            mock_scheduler = MagicMock()
            mock_scheduler_class.return_value = mock_scheduler

            scheduler = AttendanceScheduler(mock_settings, mock_check_func)
            scheduler.start()
            scheduler.shutdown(wait=True)

            mock_scheduler.shutdown.assert_called_once_with(wait=True)
            assert scheduler._scheduler is None

    @pytest.mark.asyncio
    async def test_get_next_run_time(self, mock_settings, mock_check_func):
        with patch("app.scheduler.AsyncIOScheduler") as mock_scheduler_class:
            mock_scheduler = MagicMock()
            mock_job = MagicMock()
            mock_job.next_run_time = MagicMock()
            mock_job.next_run_time.isoformat.return_value = "2026-09-22T16:30:00+05:30"
            mock_scheduler.get_job.return_value = mock_job
            mock_scheduler_class.return_value = mock_scheduler

            scheduler = AttendanceScheduler(mock_settings, mock_check_func)
            scheduler.start()

            next_run = scheduler.get_next_run_time()
            assert next_run == "2026-09-22T16:30:00+05:30"

    @pytest.mark.asyncio
    async def test_get_next_run_time_no_scheduler(self, mock_settings, mock_check_func):
        scheduler = AttendanceScheduler(mock_settings, mock_check_func)
        next_run = scheduler.get_next_run_time()
        assert next_run is None

    @pytest.mark.asyncio
    async def test_is_running(self, mock_settings, mock_check_func):
        with patch("app.scheduler.AsyncIOScheduler") as mock_scheduler_class:
            mock_scheduler = MagicMock()
            mock_scheduler.running = True
            mock_scheduler_class.return_value = mock_scheduler

            scheduler = AttendanceScheduler(mock_settings, mock_check_func)
            assert scheduler.is_running is False  # Not started yet

            scheduler.start()
            assert scheduler.is_running is True

    @pytest.mark.asyncio
    async def test_trigger_now(self, mock_settings, mock_check_func):
        with patch("app.scheduler.AsyncIOScheduler") as mock_scheduler_class:
            mock_scheduler = MagicMock()
            mock_scheduler_class.return_value = mock_scheduler

            scheduler = AttendanceScheduler(mock_settings, mock_check_func)
            scheduler.start()

            await scheduler.trigger_now()

            mock_check_func.assert_called_once()

    @pytest.mark.asyncio
    async def test_wrapped_check_catches_exceptions(self, mock_settings, mock_check_func):
        mock_check_func.side_effect = Exception("Test error")

        with patch("app.scheduler.AsyncIOScheduler") as mock_scheduler_class:
            mock_scheduler = MagicMock()
            mock_scheduler_class.return_value = mock_scheduler

            scheduler = AttendanceScheduler(mock_settings, mock_check_func)
            scheduler.start()

            # Should not raise
            await scheduler._wrapped_check()

            mock_check_func.assert_called_once()


class TestCreateScheduler:
    def test_factory_function(self, mock_settings):
        check_func = AsyncMock()
        scheduler = create_scheduler(mock_settings, check_func)

        assert isinstance(scheduler, AttendanceScheduler)
        assert scheduler._settings == mock_settings
        assert scheduler._check_func == check_func


class TestLifespanScheduler:
    @pytest.mark.asyncio
    async def test_lifespan_context_manager(self, mock_settings, mock_check_func):
        with patch("app.scheduler.create_scheduler") as mock_create:
            mock_scheduler = AsyncMock()
            mock_scheduler.start = MagicMock()
            mock_scheduler.shutdown = MagicMock()
            mock_create.return_value = mock_scheduler

            from app.scheduler import lifespan_scheduler

            async with lifespan_scheduler(mock_settings, mock_check_func) as scheduler:
                assert scheduler == mock_scheduler
                mock_scheduler.start.assert_called_once()

            mock_scheduler.shutdown.assert_called_once()
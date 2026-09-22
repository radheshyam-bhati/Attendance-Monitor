"""APScheduler-based daily attendance check scheduler.

Runs the attendance check at the configured time (default 4:30 PM Asia/Kolkata).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import time
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

from .config import Settings

logger = logging.getLogger(__name__)


class AttendanceScheduler:
    """Manages the scheduled daily attendance check job."""

    def __init__(self, settings: Settings, check_func: Callable[[], None]) -> None:
        self._settings = settings
        self._check_func = check_func
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._timezone = timezone(settings.timezone)
        self._check_time = self._parse_check_time(settings.check_time)

    def _parse_check_time(self, time_str: str) -> time:
        """Parse HH:MM time string into time object."""
        try:
            hour, minute = map(int, time_str.split(":"))
            return time(hour, minute)
        except Exception as exc:
            logger.warning("Invalid check_time %r, defaulting to 16:30: %s", time_str, exc)
            return time(16, 30)

    def start(self) -> None:
        """Start the scheduler and schedule the daily job."""
        if self._scheduler is not None:
            logger.warning("Scheduler already started")
            return

        self._scheduler = AsyncIOScheduler(timezone=self._timezone)
        trigger = CronTrigger(
            hour=self._check_time.hour,
            minute=self._check_time.minute,
            timezone=self._timezone,
        )

        self._scheduler.add_job(
            self._wrapped_check,
            trigger=trigger,
            id="daily_attendance_check",
            name="Daily PWIOI Attendance Check",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        self._scheduler.start()
        logger.info(
            "Scheduler started. Daily check scheduled for %s %s",
            self._check_time.strftime("%H:%M"),
            self._settings.timezone,
        )

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=wait)
            self._scheduler = None
            logger.info("Scheduler shut down")

    def get_next_run_time(self) -> Optional[str]:
        """Get the next scheduled run time as ISO string."""
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job("daily_attendance_check")
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    async def _wrapped_check(self) -> None:
        """Wrapper to catch and log exceptions from the check function."""
        logger.info("Starting scheduled attendance check")
        try:
            await self._check_func()
            logger.info("Scheduled attendance check completed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduled attendance check failed: %s", exc)

    async def trigger_now(self) -> None:
        """Manually trigger the attendance check immediately."""
        logger.info("Manual attendance check triggered")
        await self._wrapped_check()

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._scheduler is not None and self._scheduler.running


def create_scheduler(settings: Settings, check_func: Callable[[], None]) -> AttendanceScheduler:
    """Factory function to create an AttendanceScheduler instance."""
    return AttendanceScheduler(settings, check_func)


@asynccontextmanager
async def lifespan_scheduler(settings: Settings, check_func: Callable[[], None]):
    """Async context manager for scheduler lifecycle (for FastAPI lifespan)."""
    scheduler = create_scheduler(settings, check_func)
    scheduler.start()
    try:
        yield scheduler
    finally:
        scheduler.shutdown()
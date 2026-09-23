"""FastAPI application for PWIOI Attendance Monitor."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .config import Settings, get_runtime_settings, get_settings_instance
from .database import create_session_factory
from .models import CheckResult, SubjectAttendanceRecord
from .notifier import create_notifier
from .scheduler import AttendanceScheduler, lifespan_scheduler
from .workflow import run_attendance_check


logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AttendanceScheduler] = None


def get_scheduler() -> Optional[AttendanceScheduler]:
    return _scheduler


def get_settings() -> Settings:
    return get_settings_instance()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for scheduler."""
    global _scheduler
    settings = get_settings_instance()
    session_factory = create_session_factory(settings.database_url)

    async def scheduled_check():
        from .workflow import run_attendance_check
        try:
            await run_attendance_check(settings, manual=False)
        except Exception as exc:
            logger.exception("Scheduled check failed: %s", exc)

    async with lifespan_scheduler(settings, scheduled_check) as scheduler:
        _scheduler = scheduler
        yield
    _scheduler = None


app = FastAPI(
    title="PWIOI Attendance Monitor",
    description="Automated attendance monitoring and Gmail reporting for PWIOI students",
    version="1.0.0",
    lifespan=lifespan,
)

# Templates and static files
templates = Jinja2Templates(directory="frontend/templates")
try:
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
except RuntimeError:
    pass  # Directory doesn't exist yet


# Pydantic response models
class SubjectResponse(BaseModel):
    subject_name: str
    period: Optional[int]
    status: str
    raw_status: Optional[str]


class CheckResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    check_date: date
    checked_at: str
    attendance_date: date
    batch: Optional[str]
    present_count: Optional[int]
    absent_count: Optional[int]
    email_status: str
    email_sent_at: Optional[str]
    error_message: Optional[str]
    subjects: list[SubjectResponse]


class CheckSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    check_date: date
    attendance_date: date
    batch: Optional[str]
    present_count: Optional[int]
    absent_count: Optional[int]
    email_status: str
    subject_count: int


class ScheduleUpdateRequest(BaseModel):
    check_time: str
    timezone: str


def _to_response(check: CheckResult) -> CheckResultResponse:
    """Convert CheckResult to CheckResultResponse."""
    return CheckResultResponse(
        id=check.id,
        check_date=check.check_date,
        checked_at=check.checked_at.isoformat() if check.checked_at else "",
        attendance_date=check.attendance_date,
        batch=check.batch,
        present_count=check.present_count,
        absent_count=check.absent_count,
        email_status=check.email_status,
        email_sent_at=check.email_sent_at.isoformat() if check.email_sent_at else None,
        error_message=check.error_message,
        subjects=[
            SubjectResponse(
                subject_name=s.subject_name,
                period=s.period,
                status=s.status,
                raw_status=s.raw_status,
            )
            for s in check.subjects
        ],
    )


def _to_summary(check: CheckResult) -> CheckSummaryResponse:
    """Convert CheckResult to CheckSummaryResponse."""
    return CheckSummaryResponse(
        id=check.id,
        check_date=check.check_date,
        attendance_date=check.attendance_date,
        batch=check.batch,
        present_count=check.present_count,
        absent_count=check.absent_count,
        email_status=check.email_status,
        subject_count=len(check.subjects),
    )


# API Endpoints

@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/status")
def get_status(settings: Settings = Depends(get_settings)) -> dict:
    """Get application status including scheduler info."""
    scheduler = get_scheduler()
    next_run = scheduler.get_next_run_time() if scheduler else None
    return {
        "scheduler_running": scheduler.is_running if scheduler else False,
        "next_scheduled_check": next_run,
        "check_time": settings.get_check_time(),
        "timezone": settings.get_timezone(),
        "gmail_authorized": False,  # Will be updated by frontend via separate call
    }


@app.get("/api/schedule")
def get_schedule(settings: Settings = Depends(get_settings)) -> dict:
    """Get the current schedule configuration."""
    scheduler = get_scheduler()
    next_run = scheduler.get_next_run_time() if scheduler else None
    return {
        "check_time": settings.get_check_time(),
        "timezone": settings.get_timezone(),
        "next_scheduled_check": next_run,
    }


@app.put("/api/schedule")
def update_schedule(
    schedule_data: ScheduleUpdateRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Update check time and timezone schedule settings."""
    try:
        parts = schedule_data.check_time.split(":")
        if len(parts) != 2:
            raise ValueError
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and len(parts[0]) == 2 and len(parts[1]) == 2):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid check_time format. Expected HH:MM in 24h format.")

    try:
        import pytz
        if schedule_data.timezone not in pytz.all_timezones:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid timezone")

    runtime = get_runtime_settings()
    runtime.set("check_time", schedule_data.check_time)
    runtime.set("timezone", schedule_data.timezone)
    settings.set_runtime_override("check_time", schedule_data.check_time)
    settings.set_runtime_override("timezone", schedule_data.timezone)

    scheduler = get_scheduler()
    next_run = scheduler.get_next_run_time() if scheduler else None
    return {
        "check_time": schedule_data.check_time,
        "timezone": schedule_data.timezone,
        "next_scheduled_check": next_run,
    }


@app.get("/api/history", response_model=list[CheckSummaryResponse])
def get_history(
    limit: int = 30,
    offset: int = 0,
    settings: Settings = Depends(get_settings),
) -> list[CheckSummaryResponse]:
    """Get attendance check history."""
    session_factory = create_session_factory(settings.database_url)
    session = session_factory()
    try:
        stmt = (
            select(CheckResult)
            .order_by(desc(CheckResult.check_date), desc(CheckResult.checked_at))
            .limit(limit)
            .offset(offset)
        )
        results = session.scalars(stmt).all()
        return [_to_summary(r) for r in results]
    finally:
        session.close()


@app.get("/api/history/{check_id}", response_model=CheckResultResponse)
def get_check_detail(
    check_id: int,
    settings: Settings = Depends(get_settings),
) -> CheckResultResponse:
    """Get detailed check result by ID."""
    session_factory = create_session_factory(settings.database_url)
    session = session_factory()
    try:
        check = session.get(CheckResult, check_id)
        if check is None:
            raise HTTPException(status_code=404, detail="Check result not found")
        return _to_response(check)
    finally:
        session.close()


@app.get("/api/latest", response_model=Optional[CheckResultResponse])
def get_latest_check(settings: Settings = Depends(get_settings)) -> Optional[CheckResultResponse]:
    """Get the most recent check result."""
    session_factory = create_session_factory(settings.database_url)
    session = session_factory()
    try:
        stmt = select(CheckResult).order_by(desc(CheckResult.checked_at)).limit(1)
        check = session.scalars(stmt).first()
        return _to_response(check) if check else None
    finally:
        session.close()


@app.post("/api/check-now", response_model=CheckResultResponse)
async def trigger_check_now(settings: Settings = Depends(get_settings)) -> CheckResultResponse:
    """Manually trigger an attendance check."""
    logger.info("Manual check triggered via API")
    try:
        result = await run_attendance_check(settings, manual=True)
        return _to_response(result)
    except Exception as exc:
        logger.exception("Manual check failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/gmail-status")
def gmail_status(settings: Settings = Depends(get_settings)) -> dict:
    """Check Gmail authorization status."""
    notifier = create_notifier(settings)
    authorized = notifier.is_authorized()
    email = notifier.get_authorized_email() if authorized else None
    return {
        "authorized": authorized,
        "email": email,
    }


@app.post("/api/gmail/authorize")
async def authorize_gmail(settings: Settings = Depends(get_settings)) -> dict:
    """Initiate Gmail OAuth authorization (returns URL for manual flow)."""
    notifier = create_notifier(settings)
    # This is a placeholder - actual OAuth requires user interaction
    # The CLI command `run.py --authorize-gmail` should be used instead
    return {
        "message": "Use `python run.py --authorize-gmail` from the command line to authorize Gmail",
        "authorized": notifier.is_authorized(),
    }



# Dashboard HTML endpoint

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Serve the main dashboard page."""
    return templates.TemplateResponse(request=request, name="dashboard.html")
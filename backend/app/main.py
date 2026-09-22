"""FastAPI application for PWIOI Attendance Monitor."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .config import Settings
from .database import create_session_factory
from .models import CheckResult, SubjectAttendanceRecord
from .scheduler import AttendanceScheduler, lifespan_scheduler
from .workflow import run_attendance_check

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AttendanceScheduler] = None


def get_scheduler() -> Optional[AttendanceScheduler]:
    return _scheduler


def get_settings() -> Settings:
    return Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for scheduler."""
    global _scheduler
    settings = get_settings()
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

    class Config:
        from_attributes = True


class CheckSummaryResponse(BaseModel):
    id: int
    check_date: date
    attendance_date: date
    batch: Optional[str]
    present_count: Optional[int]
    absent_count: Optional[int]
    email_status: str
    subject_count: int

    class Config:
        from_attributes = True


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
def get_status(settings: Settings = get_settings()) -> dict:
    """Get application status including scheduler info."""
    scheduler = get_scheduler()
    next_run = scheduler.get_next_run_time() if scheduler else None
    return {
        "scheduler_running": scheduler.is_running if scheduler else False,
        "next_scheduled_check": next_run,
        "check_time": settings.check_time,
        "timezone": settings.timezone,
        "gmail_authorized": False,  # Will be updated by frontend via separate call
    }


@app.get("/api/history", response_model=list[CheckSummaryResponse])
def get_history(
    limit: int = 30,
    offset: int = 0,
    settings: Settings = get_settings(),
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
    settings: Settings = get_settings(),
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
def get_latest_check(settings: Settings = get_settings()) -> Optional[CheckResultResponse]:
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
async def trigger_check_now(settings: Settings = get_settings()) -> CheckResultResponse:
    """Manually trigger an attendance check."""
    logger.info("Manual check triggered via API")
    try:
        result = await run_attendance_check(settings, manual=True)
        return _to_response(result)
    except Exception as exc:
        logger.exception("Manual check failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/gmail-status")
def gmail_status(settings: Settings = get_settings()) -> dict:
    """Check Gmail authorization status."""
    from .notifier import create_notifier
    notifier = create_notifier(settings)
    authorized = notifier.is_authorized()
    email = notifier.get_authorized_email() if authorized else None
    return {
        "authorized": authorized,
        "email": email,
    }


@app.post("/api/gmail/authorize")
async def authorize_gmail(settings: Settings = get_settings()) -> dict:
    """Initiate Gmail OAuth authorization (returns URL for manual flow)."""
    from .notifier import create_notifier
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
    return templates.TemplateResponse("dashboard.html", {"request": request})
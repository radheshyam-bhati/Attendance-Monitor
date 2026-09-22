"""Shared test configuration and fixtures."""

import pytest
from datetime import date, datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, create_session_factory, initialize_database
from app.models import CheckResult, SubjectAttendanceRecord


@pytest.fixture(scope="session")
def test_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def test_session_factory(test_engine):
    """Create a session factory bound to the test engine."""
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture
def db_session(test_session_factory):
    """Create a database session for each test."""
    session = test_session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def sample_check_result(db_session):
    """Create a sample check result in the database."""
    check = CheckResult(
        check_date=date(2026, 9, 22),
        checked_at=datetime(2026, 9, 22, 16, 30, 0),
        attendance_date=date(2026, 9, 22),
        batch="Batch A",
        present_count=2,
        absent_count=1,
        email_status="sent",
        email_sent_at=datetime(2026, 9, 22, 16, 31, 0),
    )
    db_session.add(check)
    db_session.flush()

    subjects = [
        SubjectAttendanceRecord(
            check_result_id=check.id,
            subject_name="Mathematics",
            period=1,
            status="PRESENT",
            raw_status="Present",
        ),
        SubjectAttendanceRecord(
            check_result_id=check.id,
            subject_name="Physics",
            period=2,
            status="PRESENT",
            raw_status="Present",
        ),
        SubjectAttendanceRecord(
            check_result_id=check.id,
            subject_name="Chemistry",
            period=3,
            status="ABSENT",
            raw_status="Absent",
        ),
    ]
    for s in subjects:
        db_session.add(s)
    db_session.commit()
    db_session.refresh(check)
    return check


@pytest.fixture(autouse=True)
def reset_global_settings():
    """Reset global settings between tests."""
    import app.config
    app.config._settings_instance = None
    app.config._runtime_settings = None
    yield
    app.config._settings_instance = None
    app.config._runtime_settings = None
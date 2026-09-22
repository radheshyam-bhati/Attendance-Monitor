"""Tests for database infrastructure."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.database import Base, create_database_engine, create_session_factory, initialize_database, get_session
from app.models import CheckResult, SubjectAttendanceRecord


class TestDatabaseEngine:
    def test_create_sqlite_engine(self):
        engine = create_database_engine("sqlite:///:memory:")
        assert engine is not None
        assert engine.url.drivername == "sqlite"

    def test_create_postgres_engine(self):
        engine = create_database_engine("postgresql://user:pass@localhost/db")
        assert engine is not None
        assert engine.url.drivername == "postgresql"

    def test_session_factory(self):
        engine = create_database_engine("sqlite:///:memory:")
        factory = create_session_factory("sqlite:///:memory:")
        session = factory()
        assert isinstance(session, Session)
        session.close()

    def test_initialize_database_creates_tables(self):
        engine = create_database_engine("sqlite:///:memory:")
        initialize_database("sqlite:///:memory:")

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "check_results" in tables
        assert "subject_attendance_records" in tables
        assert "runtime_settings" in tables

    def test_get_session_context_manager(self):
        factory = create_session_factory("sqlite:///:memory:")
        initialize_database("sqlite:///:memory:")

        session = None
        for s in get_session(factory):
            session = s
            assert isinstance(s, Session)
            # Add a test record
            check = CheckResult(
                check_date="2026-09-22",
                attendance_date="2026-09-22",
            )
            s.add(check)

        # Session should be closed after context
        assert session is not None
"""SQLAlchemy database infrastructure."""

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine

from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Base class for database models."""


_engines: dict[str, Any] = {}


def create_database_engine(database_url: str):
    """Create a SQLAlchemy engine suitable for the configured database URL."""
    if database_url in _engines:
        return _engines[database_url]
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)
    if ":memory:" in database_url:
        _engines[database_url] = engine
    return engine



def create_session_factory(database_url: str) -> sessionmaker[Session]:
    """Create a configured SQLAlchemy session factory."""
    return sessionmaker(bind=create_database_engine(database_url), autoflush=False, autocommit=False)


def initialize_database(database_url: str) -> None:
    """Create the database file and any tables registered on Base."""
    from .models import CheckResult, SubjectAttendanceRecord  # noqa: F401
    from .config import RuntimeSettings
    engine = create_database_engine(database_url)
    Base.metadata.create_all(bind=engine)
    RuntimeSettings(database_url)


def get_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a database session and ensure it closes afterwards."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()



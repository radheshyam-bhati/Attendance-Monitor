"""SQLAlchemy database infrastructure."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Base class for future database models."""


def create_database_engine(database_url: str):
    """Create a SQLAlchemy engine suitable for the configured database URL."""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    """Create a configured SQLAlchemy session factory."""
    return sessionmaker(bind=create_database_engine(database_url), autoflush=False, autocommit=False)


def initialize_database(database_url: str) -> None:
    """Create the database file and any future tables registered on Base."""
    engine = create_database_engine(database_url)
    Base.metadata.create_all(bind=engine)


def get_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a database session and ensure it closes afterwards."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


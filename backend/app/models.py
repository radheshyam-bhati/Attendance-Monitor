"""Domain models for the Today Attendance page and database persistence.

These are Pydantic models describing what the read-only parser extracts from
the PWIOI portal, plus SQLAlchemy models for history storage.
"""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

__all__ = [
    "Base",
    "AttendanceStatus",
    "SubjectAttendance",
    "TodayAttendance",
    "CheckResult",
    "SubjectAttendanceRecord",
]


class AttendanceStatus(str, Enum):
    """Normalized attendance statuses.

    Only explicitly detected statuses become PRESENT or ABSENT. Unrecognized
    or missing statuses must map to NOT_MARKED or UNKNOWN — never to ABSENT.
    """

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    NOT_MARKED = "NOT_MARKED"
    UNKNOWN = "UNKNOWN"


class SubjectAttendance(BaseModel):
    """Attendance state for a single subject shown on the page."""

    subject_name: str = Field(..., min_length=1, description="Subject name exactly as shown on the portal.")
    status: AttendanceStatus = Field(..., description="Normalized status for this subject.")
    period: int | None = Field(default=None, description="Period/order number if the portal shows one.")
    raw_status: str | None = Field(default=None, description="Original status text before normalization.")
    source: str = Field(default="portal", description="Where this record was read from.")


class TodayAttendance(BaseModel):
    """Structured snapshot of the Today Attendance page."""

    attendance_date: date = Field(..., description="Attendance date as displayed by the portal.")
    batch: str | None = Field(default=None, description="Batch label if the portal shows one.")
    present_count: int | None = Field(default=None, description="Present count shown on the page; None if absent from the DOM.")
    absent_count: int | None = Field(default=None, description="Absent count shown on the page; None if absent from the DOM.")
    subjects: list[SubjectAttendance] = Field(default_factory=list, description="Per-subject attendance rows.")


# ---------------------------------------------------------------------------
# SQLAlchemy Database Models
# ---------------------------------------------------------------------------


class CheckResult(Base):
    """Record of a single attendance check run."""

    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    batch: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    present_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    absent_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationship to subject records
    subjects: Mapped[list["SubjectAttendanceRecord"]] = relationship(
        back_populates="check_result", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("check_date", "attendance_date", name="uq_check_date_attendance_date"),
    )

    def __repr__(self) -> str:
        return f"<CheckResult(id={self.id}, check_date={self.check_date}, email_status={self.email_status})>"


class SubjectAttendanceRecord(Base):
    """Per-subject attendance record linked to a CheckResult."""

    __tablename__ = "subject_attendance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_result_id: Mapped[int] = mapped_column(ForeignKey("check_results.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_name: Mapped[str] = mapped_column(String(200), nullable=False)
    period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_status: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="portal")

    check_result: Mapped["CheckResult"] = relationship(back_populates="subjects")

    def __repr__(self) -> str:
        return f"<SubjectAttendanceRecord(subject={self.subject_name}, status={self.status})>"
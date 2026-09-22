"""Tests for attendance models and status normalization."""

import pytest
from datetime import date

from app.models import (
    AttendanceStatus,
    SubjectAttendance,
    TodayAttendance,
    CheckResult,
    SubjectAttendanceRecord,
)


class TestAttendanceStatus:
    def test_status_values(self):
        assert AttendanceStatus.PRESENT == "PRESENT"
        assert AttendanceStatus.ABSENT == "ABSENT"
        assert AttendanceStatus.NOT_MARKED == "NOT_MARKED"
        assert AttendanceStatus.UNKNOWN == "UNKNOWN"


class TestSubjectAttendance:
    def test_create_minimal(self):
        subj = SubjectAttendance(
            subject_name="Mathematics",
            status=AttendanceStatus.PRESENT,
        )
        assert subj.subject_name == "Mathematics"
        assert subj.status == AttendanceStatus.PRESENT
        assert subj.period is None
        assert subj.raw_status is None
        assert subj.source == "portal"

    def test_create_full(self):
        subj = SubjectAttendance(
            subject_name="Physics",
            status=AttendanceStatus.ABSENT,
            period=2,
            raw_status="Absent",
            source="portal",
        )
        assert subj.period == 2
        assert subj.raw_status == "Absent"


class TestTodayAttendance:
    def test_create_with_subjects(self):
        subjects = [
            SubjectAttendance(subject_name="Math", status=AttendanceStatus.PRESENT, period=1),
            SubjectAttendance(subject_name="Physics", status=AttendanceStatus.NOT_MARKED, period=2),
        ]
        today = TodayAttendance(
            attendance_date=date(2026, 9, 22),
            batch="Batch A",
            present_count=1,
            absent_count=0,
            subjects=subjects,
        )
        assert today.attendance_date == date(2026, 9, 22)
        assert today.batch == "Batch A"
        assert len(today.subjects) == 2


class TestCheckResult:
    def test_create_check_result(self):
        check = CheckResult(
            check_date=date(2026, 9, 22),
            checked_at="2026-09-22T16:30:00",
            attendance_date=date(2026, 9, 22),
            batch="Batch A",
            present_count=2,
            absent_count=1,
            email_status="sent",
        )
        assert check.check_date == date(2026, 9, 22)
        assert check.email_status == "sent"
        assert check.subjects == []

    def test_add_subject_records(self):
        check = CheckResult(
            check_date=date(2026, 9, 22),
            attendance_date=date(2026, 9, 22),
        )
        record1 = SubjectAttendanceRecord(
            subject_name="Math",
            period=1,
            status="PRESENT",
        )
        record2 = SubjectAttendanceRecord(
            subject_name="Physics",
            period=2,
            status="ABSENT",
        )
        check.subjects.append(record1)
        check.subjects.append(record2)
        assert len(check.subjects) == 2
        assert check.subjects[0].subject_name == "Math"
        assert check.subjects[1].status == "ABSENT"
"""Tests for attendance parsing and status normalization."""

import pytest
from datetime import date

from app.attendance import (
    AttendanceParseError,
    normalize_status,
    normalize_date_value,
    AttendanceStatus,
    _subject_from_cells,
    _extract_subjects,
)


class TestNormalizeStatus:
    def test_present_variations(self):
        assert normalize_status("Present") == AttendanceStatus.PRESENT
        assert normalize_status("PRESENT") == AttendanceStatus.PRESENT
        assert normalize_status("present") == AttendanceStatus.PRESENT
        assert normalize_status("Attended") == AttendanceStatus.PRESENT
        assert normalize_status("Marked Present") == AttendanceStatus.PRESENT

    def test_absent_variations(self):
        assert normalize_status("Absent") == AttendanceStatus.ABSENT
        assert normalize_status("ABSENT") == AttendanceStatus.ABSENT
        assert normalize_status("absent") == AttendanceStatus.ABSENT
        assert normalize_status("Marked Absent") == AttendanceStatus.ABSENT

    def test_not_marked_variations(self):
        assert normalize_status("Not Marked") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Not Marked Yet") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Not Yet Marked") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Unmarked") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Pending") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Awaited") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Upcoming") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Not Available") == AttendanceStatus.NOT_MARKED
        assert normalize_status("No Class") == AttendanceStatus.NOT_MARKED
        assert normalize_status("Not Scheduled") == AttendanceStatus.NOT_MARKED
        assert normalize_status("N/A") == AttendanceStatus.NOT_MARKED
        assert normalize_status("NA") == AttendanceStatus.NOT_MARKED
        assert normalize_status("-") == AttendanceStatus.NOT_MARKED
        assert normalize_status("--") == AttendanceStatus.NOT_MARKED
        assert normalize_status("---") == AttendanceStatus.NOT_MARKED

    def test_unknown_status(self):
        assert normalize_status("Unknown") == AttendanceStatus.UNKNOWN
        assert normalize_status("Random Text") == AttendanceStatus.UNKNOWN
        assert normalize_status("") == AttendanceStatus.NOT_MARKED
        assert normalize_status(None) == AttendanceStatus.UNKNOWN
        assert normalize_status("   ") == AttendanceStatus.NOT_MARKED

    def test_case_insensitive(self):
        assert normalize_status("pReSeNt") == AttendanceStatus.PRESENT
        assert normalize_status("AbSeNt") == AttendanceStatus.ABSENT
        assert normalize_status("NoT MaRkEd") == AttendanceStatus.NOT_MARKED

    def test_status_prefix_stripping(self):
        assert normalize_status("Status: Present") == AttendanceStatus.PRESENT
        assert normalize_status("Status - Absent") == AttendanceStatus.ABSENT
        assert normalize_status("Status:Not Marked") == AttendanceStatus.NOT_MARKED


class TestNormalizeDateValue:
    def test_iso_format(self):
        assert normalize_date_value("2026-09-22") == date(2026, 9, 22)
        assert normalize_date_value("2026-1-5") == date(2026, 1, 5)

    def test_numeric_day_first(self):
        # DD/MM/YYYY format
        assert normalize_date_value("22/09/2026") == date(2026, 9, 22)
        assert normalize_date_value("22-09-2026") == date(2026, 9, 22)
        assert normalize_date_value("22.09.2026") == date(2026, 9, 22)
        assert normalize_date_value("5/1/2026") == date(2026, 1, 5)

    def test_month_name_formats(self):
        assert normalize_date_value("22 September 2026") == date(2026, 9, 22)
        assert normalize_date_value("22 Sep 2026") == date(2026, 9, 22)
        assert normalize_date_value("September 22, 2026") == date(2026, 9, 22)
        assert normalize_date_value("Sep 22, 2026") == date(2026, 9, 22)
        assert normalize_date_value("22nd September 2026") == date(2026, 9, 22)

    def test_embedded_in_text(self):
        assert normalize_date_value("Attendance for 2026-09-22") == date(2026, 9, 22)
        assert normalize_date_value("Date: 22/09/2026") == date(2026, 9, 22)
        assert normalize_date_value("Today is 22 September 2026") == date(2026, 9, 22)

    def test_invalid_format_raises(self):
        with pytest.raises(AttendanceParseError):
            normalize_date_value("invalid date")
        with pytest.raises(AttendanceParseError):
            normalize_date_value("")


class TestSubjectFromCells:
    def test_basic_subject_with_period_and_status(self):
        cells = ["1", "Mathematics", "Present"]
        subj = _subject_from_cells(cells)
        assert subj is not None
        assert subj.subject_name == "Mathematics"
        assert subj.period == 1
        assert subj.status == AttendanceStatus.PRESENT

    def test_subject_without_period(self):
        cells = ["Mathematics", "Present"]
        subj = _subject_from_cells(cells)
        assert subj is not None
        assert subj.subject_name == "Mathematics"
        assert subj.period is None
        assert subj.status == AttendanceStatus.PRESENT

    def test_subject_not_marked(self):
        cells = ["2", "Physics", "Not Marked"]
        subj = _subject_from_cells(cells)
        assert subj is not None
        assert subj.subject_name == "Physics"
        assert subj.period == 2
        assert subj.status == AttendanceStatus.NOT_MARKED

    def test_header_row_returns_subject(self):
        # Header-like rows without recognized status words become subjects with UNKNOWN
        cells = ["Subject", "Status"]
        subj = _subject_from_cells(cells)
        assert subj is not None
        assert subj.subject_name == "Subject"
        assert subj.status == AttendanceStatus.UNKNOWN

    def test_empty_cells_returns_none(self):
        assert _subject_from_cells([]) is None
        assert _subject_from_cells(["", ""]) is None

    def test_single_status_cell_returns_none(self):
        cells = ["Present"]
        subj = _subject_from_cells(cells)
        assert subj is None

    def test_status_with_colon_prefix(self):
        cells = ["1", "Chemistry", "Status: Present"]
        subj = _subject_from_cells(cells)
        assert subj is not None
        assert subj.subject_name == "Chemistry"
        assert subj.status == AttendanceStatus.PRESENT
        assert subj.raw_status == "Present"

    def test_absent_status(self):
        cells = ["1", "English", "Absent"]
        subj = _subject_from_cells(cells)
        assert subj is not None
        assert subj.status == AttendanceStatus.ABSENT

    def test_unknown_status(self):
        cells = ["1", "Biology", "Unknown Status"]
        subj = _subject_from_cells(cells)
        assert subj is not None
        assert subj.status == AttendanceStatus.UNKNOWN
        # raw_status is None for UNKNOWN status (only set for recognized statuses)
        assert subj.raw_status is None
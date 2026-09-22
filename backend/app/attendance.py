"""Read-only parsing of the PWIOI "Today Attendance" page.

This module extracts structured attendance data from the rendered DOM of the
manually authenticated portal page. It is strictly read-only: it never clicks,
types, or submits anything.

Selector strategy
-----------------
No selector in this module is guessed from a screenshot. The defaults below
describe the *conventions* the parser understands; they must be validated
against the real DOM with ``python run.py --inspect``. After the live
inspection, paste the confirmed selectors into ``PAGE_SPECIFIC_SELECTORS``
(they take priority over every default) and record them in
``artifacts/DOM_NOTES.md``.

Safety rules encoded here
-------------------------
- Only explicitly recognized status words become PRESENT or ABSENT.
- Unrecognized statuses map to UNKNOWN, "not marked" style words map to
  NOT_MARKED. Missing or unparseable data is never reported as ABSENT.
- Required elements that cannot be found raise :class:`AttendanceParseError`
  instead of producing fabricated attendance data.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .models import AttendanceStatus, SubjectAttendance, TodayAttendance

logger = logging.getLogger(__name__)


class AttendanceParseError(Exception):
    """Raised when the attendance page cannot be parsed into trusted data.

    This must never be converted into a synthetic attendance result.
    """


# ---------------------------------------------------------------------------
# Selector conventions (to be confirmed against the real DOM)
# ---------------------------------------------------------------------------

#: Selectors discovered during live DOM inspection (highest priority).
#: TODO: populate from artifacts/DOM_NOTES.md after running `run.py --inspect`.
PAGE_SPECIFIC_SELECTORS: tuple[str, ...] = ()

DATE_SELECTORS: tuple[str, ...] = (
    ".attendance-date",
    "[data-attendance-date]",
    "[data-testid='attendance-date']",
)

BATCH_SELECTORS: tuple[str, ...] = (
    ".batch",
    ".batch-info",
    "[data-batch]",
    "[data-testid='batch']",
)

SUMMARY_SELECTORS: tuple[str, ...] = (
    "#summary-present",
    "#summary-absent",
    ".summary-present",
    ".summary-absent",
    "[data-testid='present-count']",
    "[data-testid='absent-count']",
)

#: Candidate rows for per-subject attendance. Comma unions are deduplicated
#: in document order, mirroring Playwright behaviour.
ROW_SELECTOR = "table tbody tr, tbody tr, table tr, tr, [role='row']"

#: Section titles that plausibly introduce the detailed attendance table.
#: Used as *hints* only; the parser never trusts titles as data.
SECTION_TITLES: tuple[str, ...] = (
    "Detailed Attendance",
    "Today Attendance",
    "Attendance Details",
)

#: Row texts that explicitly declare an empty attendance list for today.
EMPTY_MARKERS: tuple[str, ...] = (
    "no subject",
    "no records",
    "no attendance",
    "no class",
    "nothing to show",
)

PAGE_WAIT_TIMEOUT_MS = 10_000
_TITLE_WAIT_TIMEOUT_MS = 3_000

# ---------------------------------------------------------------------------
# Status normalization
# ---------------------------------------------------------------------------

PRESENT_WORDS: frozenset[str] = frozenset({"present", "attended", "marked present"})
ABSENT_WORDS: frozenset[str] = frozenset({"absent", "marked absent"})
NOT_MARKED_WORDS: frozenset[str] = frozenset(
    {
        "not marked",
        "not marked yet",
        "not yet marked",
        "unmarked",
        "pending",
        "awaited",
        "upcoming",
        "not available",
        "no class",
        "not scheduled",
        "n/a",
        "na",
        "-",
        "--",
        "---",
    }
)

STATUS_WORD_MAP: dict[str, AttendanceStatus] = {
    word: AttendanceStatus.PRESENT for word in PRESENT_WORDS
} | {word: AttendanceStatus.ABSENT for word in ABSENT_WORDS} | {
    word: AttendanceStatus.NOT_MARKED for word in NOT_MARKED_WORDS
}


def normalize_status(raw: str | None) -> AttendanceStatus:
    """Normalize a raw status string into an :class:`AttendanceStatus`.

    Unknown or unrecognizable values map to UNKNOWN — never to ABSENT.
    """
    if raw is None:
        return AttendanceStatus.UNKNOWN
    text = re.sub(r"\s+", " ", raw.replace("\u00a0", " ")).strip().lower()
    text = re.sub(r"^status\s*[:\-]\s*", "", text).strip().rstrip(":").strip()
    if not text or set(text) <= {"-", "\u2013", "\u2014"}:
        return AttendanceStatus.NOT_MARKED
    return STATUS_WORD_MAP.get(text, AttendanceStatus.UNKNOWN)


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ISO_DATE_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(?P<a>\d{1,2})[/.-](?P<b>\d{1,2})[/.-](?P<year>\d{4})\b")
_DAY_MONTH_YEAR_RE = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?[\s,]+(?P<mon>[A-Za-z]{3,9})\.?,?[\s,]+(?P<year>\d{4})\b"
)
_MONTH_DAY_YEAR_RE = re.compile(
    r"\b(?P<mon>[A-Za-z]{3,9})\.?,?[\s,]+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?[\s,]+(?P<year>\d{4})\b"
)


def normalize_date_value(raw: str) -> date:
    """Parse a human-readable date string into :class:`datetime.date`.

    Supported shapes: ``YYYY-MM-DD``, ``DD/MM/YYYY`` (also ``-`` and ``.``
    separators), ``17 September 2025``, ``September 17, 2025``.

    Numeric dates are interpreted day-first (DD/MM/YYYY) when both readings
    are valid, matching the portal's Indian locale. The live inspection step
    must confirm the actual displayed format.
    """
    text = re.sub(r"\s+", " ", raw.replace("\u00a0", " ")).strip()

    match = _ISO_DATE_RE.search(text)
    if match:
        try:
            return date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            pass

    match = _NUMERIC_DATE_RE.search(text)
    if match:
        first, second, year = (
            int(match.group("a")),
            int(match.group("b")),
            int(match.group("year")),
        )
        try:  # Day-first (DD/MM/YYYY), the portal's expected locale.
            return date(year, second, first)
        except ValueError:
            pass
        try:  # Month-first fallback for unambiguous MM/DD values.
            return date(year, first, second)
        except ValueError:
            pass

    for pattern in (_DAY_MONTH_YEAR_RE, _MONTH_DAY_YEAR_RE):
        match = pattern.search(text)
        if match:
            month = _MONTHS.get(match.group("mon").lower()[:3])
            if month is not None:
                try:
                    return date(int(match.group("year")), month, int(match.group("day")))
                except ValueError:
                    pass

    raise AttendanceParseError(f"Unrecognized attendance date format: {raw!r}")


def _try_parse_date_text(text: str) -> date | None:
    """Return a parsed date if ``text`` contains one, else ``None``."""
    if not text or len(text) > 300:
        return None
    try:
        return normalize_date_value(text)
    except AttendanceParseError:
        return None


# ---------------------------------------------------------------------------
# Minimal structural typing for Playwright-like objects
# ---------------------------------------------------------------------------


class LocatorLike(Protocol):
    """The subset of the Playwright Locator API the parser relies on."""

    @property
    def first(self) -> "LocatorLike": ...

    def locator(self, selector: str) -> "LocatorLike": ...

    def get_by_text(self, text: str, *, exact: bool = False) -> "LocatorLike": ...

    async def count(self) -> int: ...

    async def all(self) -> list["LocatorLike"]: ...

    async def inner_text(self) -> str: ...

    async def wait_for(self, *, timeout: float | None = None) -> None: ...


class PageLike(Protocol):
    """The subset of the Playwright Page API the parser relies on."""

    url: str

    def locator(self, selector: str) -> LocatorLike: ...

    def get_by_text(self, text: str, *, exact: bool = False) -> LocatorLike: ...

    async def content(self) -> str: ...


# ---------------------------------------------------------------------------
# Small read helpers (used by both the parser and the DOM inspector)
# ---------------------------------------------------------------------------


def _clean_text(value: str) -> str:
    """Collapse whitespace (including non-breaking spaces) in extracted text."""
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


class ArtifactCaptureMixin:
    """Shared behavior for objects that save debugging artifacts."""

    #: Populated by the concrete class with its Playwright page (or fixture page).
    page: Any = None

    async def save_artifacts(self, stem: str) -> tuple[Path, Path | None]:
        """Persist page HTML (always) and a screenshot (when supported)."""
        artifacts_dir = Path("artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        html_path = artifacts_dir / f"{stem}.html"
        html_path.write_text(await self.page.content(), encoding="utf-8")
        logger.info("Saved page HTML artifact: %s", html_path)

        screenshot_path: Path | None = artifacts_dir / f"{stem}.png"
        try:
            screenshot = self.page.screenshot  # type: ignore[attr-defined]
            await screenshot(path=str(screenshot_path), full_page=True)
            logger.info("Saved screenshot artifact: %s", screenshot_path)
        except AttributeError:
            logger.info("Screenshot unsupported for this page type; skipped.")
            screenshot_path = None
        except Exception as exc:  # noqa: BLE001 - screenshots are best-effort
            logger.warning("Screenshot could not be captured: %s", exc)
            screenshot_path = None
        return html_path, screenshot_path

    async def log_locator_inventory(self) -> list[dict[str, Any]]:
        """Log a short inventory of candidate selectors found on the page."""
        inventory: list[dict[str, Any]] = []
        candidates = {
            "tables": "table",
            "rows": "tr",
            "sections": "section",
            "data-testid": "[data-testid]",
        }
        for label, css in candidates.items():
            try:
                count = await self.page.locator(css).count()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Inventory selector %r failed: %s", css, exc)
                count = -1
            inventory.append({"selector": css, "label": label, "count": count})
            logger.info("Locator inventory: %-12s %-20s count=%s", label, css, count)
        return inventory


async def _safe_text(locator: LocatorLike) -> str:
    """Read an element's text, returning '' (and logging) on failure."""
    try:
        return _clean_text(await locator.inner_text())
    except Exception as exc:  # noqa: BLE001 - missing elements are expected here
        logger.debug("Could not read element text: %s", exc)
        return ""


async def _optional_text(scope: LocatorLike, css: str) -> str | None:
    """Read the first element matching ``css`` without failing when absent."""
    locator = scope.locator(css).first
    try:
        if await locator.count() == 0:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Selector %r could not be counted: %s", css, exc)
        return None
    text = await _safe_text(locator)
    return text or None


# ---------------------------------------------------------------------------
# Waiting for dynamically loaded content
# ---------------------------------------------------------------------------


async def wait_for_attendance_content(
    page: PageLike, timeout_ms: int = PAGE_WAIT_TIMEOUT_MS
) -> None:
    """Wait until something row-like (or a section title) appears.

    This only optimizes timing for dynamically rendered pages; every extractor
    still fails with a clear error if its data is genuinely missing.
    """
    for css in (*PAGE_SPECIFIC_SELECTORS, "table", "[role='row']"):
        try:
            await page.locator(css).first.wait_for(timeout=timeout_ms)
            logger.info("Attendance content detected via %r.", css)
            return
        except Exception as exc:  # noqa: BLE001 - timeout means "try next hint"
            logger.debug("Attendance marker %r did not appear: %s", css, exc)

    for title in SECTION_TITLES:
        try:
            await page.get_by_text(title, exact=True).first.wait_for(
                timeout=_TITLE_WAIT_TIMEOUT_MS
            )
            logger.info("Attendance section title detected: %r.", title)
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("Section title %r did not appear: %s", title, exc)

    logger.warning(
        "No attendance content markers were detected; parsing will still be attempted."
    )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


async def _find_detail_section(page: PageLike) -> LocatorLike | None:
    """Locate the element that wraps the detailed attendance table, if any."""
    for title in SECTION_TITLES:
        hint = page.get_by_text(title, exact=True).first
        try:
            if await hint.count() == 0:
                continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("Section title lookup %r failed: %s", title, exc)
            continue
        section = page.locator(
            f"xpath=//*[normalize-space(text())='{title}']"
            "/ancestor-or-self::*[self::section or self::div or self::table or self::main][1]"
        ).first
        try:
            if await section.count() > 0:
                logger.info("Detailed attendance section located via title %r.", title)
                return section
        except Exception as exc:  # noqa: BLE001
            logger.debug("Section scoping for %r failed: %s", title, exc)

    for css in (*PAGE_SPECIFIC_SELECTORS, "main", "section"):
        candidate = page.locator(css).first
        try:
            if await candidate.count() > 0:
                logger.info("Attendance section located via selector %r.", css)
                return candidate
        except Exception as exc:  # noqa: BLE001
            logger.debug("Section selector %r failed: %s", css, exc)

    logger.warning("No dedicated attendance section found; using the whole page.")
    return None


async def _row_cells(row: LocatorLike) -> list[str]:
    """Return the visible cell texts of one attendance row.

    Empty cells are kept as '' placeholders so callers retain positions; the
    subject builder ignores blanks itself.
    """
    for css in ("td", "[role='cell']"):
        cells = await row.locator(css).all()
        if cells:
            texts = [await _safe_text(cell) for cell in cells]
            if any(texts):
                return texts
    text = await _safe_text(row)
    if not text:
        return []
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    return parts or [text]


def _subject_from_cells(cells: list[str]) -> SubjectAttendance | None:
    """Build a :class:`SubjectAttendance` from one row's cell texts.

    Returns ``None`` for rows that carry no subject information (headers,
    legends, empty placeholders).
    """
    texts = [text for text in (_clean_text(c) for c in cells) if text]
    if not texts:
        return None

    status_index: int | None = None
    status = AttendanceStatus.UNKNOWN
    raw_status: str | None = None
    for index in range(len(texts) - 1, -1, -1):
        candidate = re.sub(r"^status\s*[:\-]\s*", "", texts[index], flags=re.IGNORECASE)
        normalized = normalize_status(candidate)
        if normalized is not AttendanceStatus.UNKNOWN:
            status_index = index
            status = normalized
            raw_status = candidate or None
            break

    name_candidates = [text for i, text in enumerate(texts) if i != status_index]
    if len(name_candidates) > 1 and name_candidates[0].isdigit():
        period = int(name_candidates.pop(0))
    else:
        period = None
    name = name_candidates[0].strip(" :") if name_candidates else None

    if not name:
        # A single status-like cell (e.g. a legend or summary row) is not a subject.
        return None

    return SubjectAttendance(
        subject_name=name,
        status=status,
        period=period,
        raw_status=raw_status,
        source="portal",
    )


async def _extract_subjects(
    scope: LocatorLike,
) -> tuple[list[SubjectAttendance], bool]:
    """Extract subject rows from ``scope``.

    Returns the subjects and whether an explicit "no subjects" marker was
    seen (which distinguishes a legitimate empty day from a parse failure).
    """
    try:
        rows = await scope.locator(ROW_SELECTOR).all()
    except Exception as exc:  # noqa: BLE001
        raise AttendanceParseError("Unable to search the page for attendance rows.") from exc

    subjects: list[SubjectAttendance] = []
    empty_marker_seen = False
    for row in rows:
        try:
            if await row.locator("th").count() > 0:
                continue  # Header row.
            cells = await _row_cells(row)
            row_text = _clean_text(await row.inner_text()).lower()
        except Exception as exc:  # noqa: BLE001
            raise AttendanceParseError("Unable to read one of the attendance rows.") from exc

        # Header-like rows are sometimes rendered with <td> instead of <th>.
        if {"subject", "status"} <= {cell.lower() for cell in cells}:
            logger.debug("Skipping header-like row (cells=%r).", cells)
            continue

        if len(cells) <= 1 and any(marker in row_text for marker in EMPTY_MARKERS):
            empty_marker_seen = True
            continue

        subject = _subject_from_cells(cells)
        if subject is not None:
            subjects.append(subject)
    return subjects, empty_marker_seen


_PRESENT_VALUE_RE = re.compile(
    r"Present\b[\s:=#-]{0,6}(\d{1,3})(?!\d)(?!\s*%)", re.IGNORECASE
)
_ABSENT_VALUE_RE = re.compile(
    r"Absent\b[\s:=#-]{0,6}(\d{1,3})(?!\d)(?!\s*%)", re.IGNORECASE
)
_PRESENT_VALUE_BEFORE_RE = re.compile(
    r"(\d{1,3})(?!\d)(?!\s*%)[\s:=#-]{0,6}Present\b", re.IGNORECASE
)
_ABSENT_VALUE_BEFORE_RE = re.compile(
    r"(\d{1,3})(?!\d)(?!\s*%)[\s:=#-]{0,6}Absent\b", re.IGNORECASE
)


def _counts_from_text(text: str) -> tuple[int | None, int | None]:
    """Extract (present, absent) counts from a text snippet, if present."""
    present: int | None = None
    absent: int | None = None

    def _usable(value: int) -> bool:
        # Values above 99 are almost certainly percentages or IDs, not counts.
        return 0 <= value <= 99

    if (match := _PRESENT_VALUE_RE.search(text)) and _usable(int(match.group(1))):
        present = int(match.group(1))
    elif (match := _PRESENT_VALUE_BEFORE_RE.search(text)) and _usable(int(match.group(1))):
        present = int(match.group(1))
    if (match := _ABSENT_VALUE_RE.search(text)) and _usable(int(match.group(1))):
        absent = int(match.group(1))
    elif (match := _ABSENT_VALUE_BEFORE_RE.search(text)) and _usable(int(match.group(1))):
        absent = int(match.group(1))
    return present, absent


async def _extract_summary_counts(
    page: PageLike, section: LocatorLike | None
) -> tuple[int | None, int | None]:
    """Extract the page's Present/Absent summary counts, if displayed."""
    scopes: list[LocatorLike] = [page] if section is None else [section, page]

    present: int | None = None
    absent: int | None = None
    for css in (*PAGE_SPECIFIC_SELECTORS, *SUMMARY_SELECTORS):
        for scope in scopes:
            text = await _optional_text(scope, css)
            if text:
                found_present, found_absent = _counts_from_text(text)
                if present is None:
                    present = found_present
                if absent is None:
                    absent = found_absent
            if present is not None and absent is not None:
                return present, absent

    for css in ("th", "td", "p", "span", "h1", "h2", "h3", "h4", "div"):
        for scope in scopes:
            try:
                elements = await scope.locator(css).all()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Count scan selector %r failed: %s", css, exc)
                continue
            for element in elements:
                found_present, found_absent = _counts_from_text(await _safe_text(element))
                if present is None:
                    present = found_present
                if absent is None:
                    absent = found_absent
                if present is not None and absent is not None:
                    return present, absent

    body_text = await _safe_text(page.locator("body").first)
    if body_text:
        found_present, found_absent = _counts_from_text(body_text)
        if present is None:
            present = found_present
        if absent is None:
            absent = found_absent

    if present is None or absent is None:
        logger.warning(
            "Summary counts are incomplete on this page (present=%s, absent=%s).",
            present,
            absent,
        )
    return present, absent


async def _extract_attendance_date(
    page: PageLike, section: LocatorLike | None
) -> date:
    """Extract the attendance date displayed by the portal (source of truth)."""
    scopes: list[LocatorLike] = [page] if section is None else [section, page]

    for css in (*PAGE_SPECIFIC_SELECTORS, *DATE_SELECTORS):
        for scope in scopes:
            text = await _optional_text(scope, css)
            parsed = _try_parse_date_text(text) if text else None
            if parsed is not None:
                logger.info("Attendance date discovered: %s (selector %r).", parsed, css)
                return parsed

    # Fall back to scanning elements in document order, cheapest/most specific first.
    for css in ("th", "td", "p", "span", "h1", "h2", "h3", "h4", "div"):
        for scope in scopes:
            try:
                elements = await scope.locator(css).all()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Date scan selector %r failed: %s", css, exc)
                continue
            for element in elements:
                parsed = _try_parse_date_text(await _safe_text(element))
                if parsed is not None:
                    logger.info("Attendance date discovered: %s (element <%s>).", parsed, css)
                    return parsed

    body_text = await _safe_text(page.locator("body").first)
    parsed = _try_parse_date_text(body_text)
    if parsed is not None:
        return parsed

    raise AttendanceParseError(
        "Unable to locate the attendance date on the page. "
        "The portal's displayed date is required and must not be guessed."
    )


async def _extract_batch(page: PageLike, section: LocatorLike | None) -> str | None:
    """Extract the batch label; optional, so missing data yields None."""
    batch_re = re.compile(
        r"\bBatch\b[\s:#=-]{0,6}([A-Za-z0-9][A-Za-z0-9/_-]{0,30})", re.IGNORECASE
    )
    scopes: list[LocatorLike] = [page] if section is None else [section, page]

    for css in (*PAGE_SPECIFIC_SELECTORS, *BATCH_SELECTORS):
        for scope in scopes:
            text = await _optional_text(scope, css)
            if text and len(text) <= 60:
                match = batch_re.search(text)
                return (match.group(1).rstrip(".,") if match else text).strip()

    for css in ("th", "td", "p", "span", "h1", "h2", "h3", "h4", "div"):
        for scope in scopes:
            try:
                elements = await scope.locator(css).all()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Batch scan selector %r failed: %s", css, exc)
                continue
            for element in elements:
                match = batch_re.search(await _safe_text(element))
                if match:
                    return match.group(1).rstrip(".,")
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def parse_attendance(page: PageLike) -> TodayAttendance:
    """Read today's attendance from the rendered portal page (read-only).

    Raises :class:`AttendanceParseError` when required data is missing; it
    never fabricates values and never classifies failures as ABSENT.
    """
    logger.info("Attendance parser started (url=%s).", page.url)
    await wait_for_attendance_content(page)

    section = await _find_detail_section(page)
    scope = page if section is None else section
    subjects, empty_marker_seen = await _extract_subjects(scope)

    if not subjects and not empty_marker_seen:
        raise AttendanceParseError(
            "Unable to locate the Detailed Attendance table: "
            "no subject rows were found on the page."
        )

    attendance_date = await _extract_attendance_date(page, section)
    batch = await _extract_batch(page, section)
    present_count, absent_count = await _extract_summary_counts(page, section)

    if not subjects:
        logger.warning("Portal explicitly reports no subject rows for today.")
    if present_count is None or absent_count is None:
        logger.warning(
            "Summary counts incomplete (present=%s, absent=%s); leaving them unset.",
            present_count,
            absent_count,
        )

    result = TodayAttendance(
        attendance_date=attendance_date,
        batch=batch,
        present_count=present_count,
        absent_count=absent_count,
        subjects=subjects,
    )
    logger.info(
        "Parser completed: date=%s batch=%s subjects=%d present=%s absent=%s",
        result.attendance_date,
        result.batch,
        len(result.subjects),
        result.present_count,
        result.absent_count,
    )
    return result


async def inspect_attendance(page: PageLike) -> TodayAttendance:
    """Alias of :func:`parse_attendance` kept for the original module naming."""
    return await parse_attendance(page)


async def parse_attendance_html(
    html: str, *, url: str = "file://fixture/today_attendance.html"
) -> TodayAttendance:
    """Parse saved HTML with the exact same parser used on the live page.

    This is a development/test helper backed by ``app._fixture_dom``, a tiny
    in-memory DOM that mimics the Playwright API subset used above. The live
    parser path never touches it.
    """
    from ._fixture_dom import page_from_html  # noqa: PLC0415 - test/dev-only

    return await parse_attendance(page_from_html(html, url=url))


def summarize_attendance(result: TodayAttendance) -> dict[str, Any]:
    """Return a compact JSON-friendly summary used by CLI output."""
    return result.model_dump(mode="json")

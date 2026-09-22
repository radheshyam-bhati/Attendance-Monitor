"""Live DOM inspection for the PWIOI attendance page (development tool).

This module discovers real selectors from the manually authenticated portal
page. It never automates login, never types into the page, and never submits
anything — navigation and artifact saving only.

Typical use (see ``run.py --inspect``)::

    inspector = PortalDomInspector(page)
    if await inspector.ensure_authenticated():
        paths = await inspector.save_artifacts("today_attendance")
        summary = await inspector.summarize_dom()

The generated summary is intended to be pasted into
``artifacts/DOM_NOTES.md`` so the parser can be pinned to verified selectors.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .portal import PWIOIPortal

logger = logging.getLogger(__name__)

#: Extra selectors probed by the inspector (superset of the parser's defaults).
#: TODO: after confirming on the live page, keep only the verified ones.
INSPECTOR_PROBE_SELECTORS: tuple[str, ...] = (
    "table",
    "[role='row']",
    "[role='cell']",
    "[role='table']",
    "[data-testid]",
    "[data-attendance-date]",
    "[data-batch]",
    ".attendance-date",
    ".batch",
    ".summary-present",
    ".summary-absent",
    "#summary-present",
    "#summary-absent",
    "main",
    "section",
)

ARTIFACTS_DIR = Path("artifacts")
DOM_NOTES_PATH = ARTIFACTS_DIR / "DOM_NOTES.md"
PAGE_LOAD_TIMEOUT_MS = 20_000
_AUTH_PROMPT_LINES = (
    "",
    "=" * 64,
    "You are not logged in to PWIOI.",
    "Please log into PWIOI manually in the opened browser window.",
    "Do NOT share credentials with this tool — it will never ask for them.",
    "Press <Enter> here once you are logged in and the attendance page is visible...",
    "=" * 64,
)


def _host_of(url: str) -> str:
    return urlparse(url).netloc


class PortalDomInspector:
    """Read-only inspector for the real attendance page DOM."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._portal = PWIOIPortal.using_page(page)

    async def ensure_authenticated(
        self,
        prompt: Callable[[], None] | None = None,
        wait: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        """Navigate to the attendance page and wait for a manual login if needed.

        ``prompt`` and ``wait`` are injectable for headless/test use; by default
        the prompt prints to the terminal and the wait reads one line from it.
        Returns ``True`` when the browser is on the attendance page.
        """
        logger.info("Opening attendance page for inspection: %s", self._portal.attendance_url)
        await self._portal.open_attendance_page(self.page)

        if self._portal.is_login_page(self.page):
            prompt = prompt or (lambda: print("\n".join(_AUTH_PROMPT_LINES), flush=True))

            async def _default_wait() -> None:
                await self._wait_for_manual_login()

            wait = wait or _default_wait
            prompt()
            await wait()
            logger.info("Resuming inspection after manual login; current URL: %s", self.page.url)

        if not self._portal.is_attendance_page(self.page):
            logger.error(
                "Expected the attendance page after login but the browser is on: %s",
                self.page.url,
            )
            return False

        logger.info("Authenticated: attendance page is open.")
        await self._wait_for_content()
        return True

    async def _wait_for_manual_login(self) -> None:
        """Block until the browser reaches the attendance route or the user gives up."""
        import asyncio

        target_path = urlparse(self._portal.attendance_url).path.rstrip("/")
        while True:
            await asyncio.sleep(1.0)
            if _host_of(self.page.url) != _host_of(self._portal.attendance_url):
                continue
            if urlparse(self.page.url).path.rstrip("/") == target_path:
                await self.page.wait_for_load_state("domcontentloaded")
                return

    async def _wait_for_content(self) -> None:
        """Give a client-rendered page a chance to finish drawing its content."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.info("Network did not go idle within %d ms; continuing.", PAGE_LOAD_TIMEOUT_MS)

    async def save_artifacts(self, stem: str = "today_attendance") -> dict[str, Path]:
        """Save the rendered HTML and a full-page screenshot under ``artifacts/``."""
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        saved: dict[str, Path] = {}

        html_path = ARTIFACTS_DIR / f"{stem}.html"
        html_path.write_text(await self.page.content(), encoding="utf-8")
        saved["html"] = html_path
        logger.info("Saved DOM snapshot: %s", html_path)

        screenshot_path = ARTIFACTS_DIR / f"{stem}.png"
        try:
            await self.page.screenshot(path=str(screenshot_path), full_page=True)
            saved["screenshot"] = screenshot_path
            logger.info("Saved screenshot: %s", screenshot_path)
        except (PlaywrightError, OSError) as exc:
            logger.warning("Screenshot could not be captured: %s", exc)

        return saved

    async def summarize_dom(self) -> dict[str, Any]:
        """Probe the live DOM and summarize the structures that were found.

        Every entry records what was actually observed — nothing is invented.
        Values that could not be observed are reported as ``None``.
        """
        summary: dict[str, Any] = {
            "url": self.page.url,
            "title": None,
            "probes": {},
            "tables": [],
            "candidates": {},
        }

        try:
            summary["title"] = await self.page.title()
        except PlaywrightError as exc:
            logger.debug("Could not read the page title: %s", exc)

        for css in INSPECTOR_PROBE_SELECTORS:
            try:
                summary["probes"][css] = await self.page.locator(css).count()
            except PlaywrightError as exc:
                summary["probes"][css] = f"error: {exc}"
            if str(summary["probes"][css]).startswith("error"):
                logger.warning("Probe %r failed: %s", css, summary["probes"][css])

        summary["tables"] = await self._describe_tables()

        summary["candidates"] = {
            "attendance_date": await self._first_texts(".attendance-date, [data-attendance-date], [data-testid='attendance-date']"),
            "batch": await self._first_texts(".batch, .batch-info, [data-batch], [data-testid='batch']"),
            "present_count": await self._first_texts("#summary-present, .summary-present, [data-testid='present-count']"),
            "absent_count": await self._first_texts("#summary-absent, .summary-absent, [data-testid='absent-count']"),
        }

        logger.info("DOM summary: %s", json.dumps(summary, indent=2, default=str))
        return summary

    async def _first_texts(self, css: str, limit: int = 5) -> list[str]:
        """Return the visible texts of the first few elements matching ``css``."""
        texts: list[str] = []
        try:
            elements = await self.page.locator(css).all()
        except PlaywrightError as exc:
            logger.debug("Candidate selector %r failed: %s", css, exc)
            return texts
        for element in elements[:limit]:
            try:
                text = " ".join((await element.inner_text()).split())
            except PlaywrightError:
                continue
            if text:
                texts.append(text)
        return texts

    async def _describe_tables(self, limit: int = 3) -> list[dict[str, Any]]:
        """Describe the first few tables: dimensions, headers, sample rows."""
        descriptions: list[dict[str, Any]] = []
        try:
            tables = await self.page.locator("table").all()
        except PlaywrightError as exc:
            logger.debug("Table probing failed: %s", exc)
            return descriptions

        for position, table in enumerate(tables[:limit]):
            description: dict[str, Any] = {"table_index": position, "headers": [], "row_count": None, "sample_rows": []}
            try:
                headers = await table.locator("th").all()
                description["headers"] = [" ".join((await h.inner_text()).split()) for h in headers]
                rows = await table.locator("tr").all()
                description["row_count"] = len(rows)
                for row in rows[1:4]:  # Skip the header row; keep three samples.
                    cells = await row.locator("th, td").all()
                    description["sample_rows"].append([" ".join((await c.inner_text()).split()) for c in cells])
            except PlaywrightError as exc:
                description["error"] = str(exc)
            descriptions.append(description)

        logger.info(
            "Found %d table(s); first table headers: %s",
            len(tables),
            descriptions[0]["headers"] if descriptions else "n/a",
        )
        return descriptions

    async def write_dom_notes(
        self,
        summary: dict[str, Any],
        *,
        artifacts: dict[str, Path] | None = None,
        path: Path = DOM_NOTES_PATH,
    ) -> Path:
        """Write a ``DOM_NOTES.md`` scaffold pre-filled with observed evidence.

        Selector conclusions must be completed manually from the recorded
        evidence — this tool records what was observed, it does not decide.
        """
        artifacts = artifacts or {}
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        probes = "\n".join(f"| `{css}` | {count} |" for css, count in summary["probes"].items())
        candidates = "\n".join(
            f"| {name} | `{css_hint}` | {values or '—'} |"
            for name, css_hint, values in (
                ("Attendance date", ".attendance-date, [data-attendance-date], …", summary["candidates"].get("attendance_date")),
                ("Batch", ".batch, .batch-info, [data-batch], …", summary["candidates"].get("batch")),
                ("Present count", "#summary-present, .summary-present, …", summary["candidates"].get("present_count")),
                ("Absent count", "#summary-absent, .summary-absent, …", summary["candidates"].get("absent_count")),
            )
        )

        lines = [
            "# PWIOI Attendance Page — DOM Notes",
            "",
            f"> Generated by `run.py --inspect` on {generated_at}.",
            "> Evidence below was observed on the live page. Selectors marked",
            "> 'TODO (confirm)' must be verified by a human before production use.",
            "",
            "## 1. URL inspected",
            "",
            f"- URL at capture time: `{summary.get('url', 'unknown')}`",
            f"- Page title: `{summary.get('title')}`",
            "",
            "## 2. Main page structure",
            "",
            "| Probe selector | Match count |",
            "| --- | --- |",
            probes or "| (none) | — |",
            "",
            "## Tables",
            "",
        ]

        for table in summary["tables"]:
            lines += [
                f"### Table #{table.get('table_index', 0)}",
                "",
                f"- Headers: {table.get('headers')}",
                f"- Row count: {table.get('row_count')}",
                f"- Sample rows: {table.get('sample_rows')}",
            ]
            if table.get("error"):
                lines.append(f"- Error: {table['error']}")
            lines.append("")

        lines += [
            "## Candidate selectors",
            "",
            "| Field | Selector hint | Observed text(s) |",
            "| --- | --- | --- |",
            candidates or "| (none) | — | — |",
            "",
            "## Confirmed selectors (complete after human review)",
            "",
            "| Field | Confirmed selector | Notes |",
            "| --- | --- | --- |",
            "| Attendance date | TODO (confirm) | |",
            "| Batch | TODO (confirm) | |",
            "| Present count | TODO (confirm) | |",
            "| Absent count | TODO (confirm) | |",
            "| Subject row | TODO (confirm) | |",
            "| Subject name | TODO (confirm) | |",
            "| Status | TODO (confirm) | |",
            "",
            "## Observations",
            "",
            "- TODO: describe dynamic loading behaviour (immediate HTML, JS-rendered, network-loaded).",
            "- TODO: note any iframe / shadow-DOM boundaries if present.",
            "- TODO: list any selectors that should NOT be used (e.g. nth-child, generated class names).",
            "",
            "## Artifacts saved",
            "",
        ]
        for label, artifact_path in artifacts.items():
            lines.append(f"- {label}: `{artifact_path}`")
        lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Wrote DOM notes: %s", path)
        return path

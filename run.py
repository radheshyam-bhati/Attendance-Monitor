"""Entry point for the PWIOI attendance monitor (development stage).

Usage:
    python run.py                     # open a persistent, manually authenticated browser
    python run.py --inspect           # inspect the attendance page DOM and save artifacts
    python run.py --check-now         # parse today's attendance and print it (read-only)
    python run.py --authorize-gmail   # authorize Gmail API access (OAuth flow)
    python run.py --run-check         # run full attendance check workflow (parse + store + email)

This tool never automates login, never stores credentials, and never writes to
the PWIOI portal. Authentication is always performed manually by the user in
the opened Chromium window.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.attendance import AttendanceParseError, parse_attendance  # noqa: E402
from app.browser import BrowserManager  # noqa: E402
from app.config import Settings  # noqa: E402
from app.database import initialize_database  # noqa: E402
from app.notifier import create_notifier  # noqa: E402
from app.portal import PWIOIPortal  # noqa: E402
from app.portal_inspector import PortalDomInspector  # noqa: E402
from app.workflow import run_attendance_check  # noqa: E402

logger = logging.getLogger("attendance_monitor")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PWIOI attendance monitor (read-only).")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--inspect", action="store_true", help="inspect the attendance page DOM and save artifacts")
    modes.add_argument("--check-now", action="store_true", help="parse today's attendance and print it (read-only)")
    modes.add_argument("--authorize-gmail", action="store_true", help="authorize Gmail API access via OAuth")
    modes.add_argument("--run-check", action="store_true", help="run full attendance check workflow (parse + store + email)")
    return parser.parse_args(argv)


async def _open_authenticated_page(settings: Settings, browser: BrowserManager):
    """Open the attendance route, prompting for manual login when required."""
    page = await browser.open_page()
    portal = PWIOIPortal(settings)
    await portal.open_attendance_page(page)
    logger.info("Current URL: %s", portal.current_url(page))

    if portal.is_login_page(page):
        print(
            "\n" + "=" * 64,
            "You are not logged in to PWIOI.",
            "Please log into PWIOI manually in the opened browser window.",
            "Press <Enter> here once you are logged in...",
            "=" * 64,
            sep="\n",
            flush=True,
        )
        await asyncio.to_thread(input)
        logger.info("Resuming after manual login; current URL: %s", portal.current_url(page))

    if not portal.is_attendance_page(page):
        logger.warning("The browser is not on the attendance page (url=%s).", portal.current_url(page))
    return page, portal


async def run_default(settings: Settings, browser: BrowserManager) -> None:
    """Keep a manually authenticated browser open for inspection."""
    await _open_authenticated_page(settings, browser)
    logger.info("Browser remains open for inspection. Press Ctrl+C to close it.")
    await asyncio.Event().wait()


async def run_inspect(settings: Settings, browser: BrowserManager) -> int:
    """Inspect the live DOM, save artifacts, and print a selector summary."""
    page, _ = await _open_authenticated_page(settings, browser)
    inspector = PortalDomInspector(page)

    if not await inspector.ensure_authenticated():
        logger.error("Inspection aborted: the attendance page is not reachable.")
        return 1

    artifacts = await inspector.save_artifacts("today_attendance")
    summary = await inspector.summarize_dom()
    notes_path = await inspector.write_dom_notes(summary, artifacts=artifacts)

    print("\n--- Inspection summary ---")
    print(f"URL: {summary.get('url')}")
    print(f"Title: {summary.get('title')}")
    probes = summary.get("probes", {})
    for css, count in probes.items():
        print(f"  {count:>4} × {css}")
    print(f"\nArtifacts: {', '.join(str(p) for p in artifacts.values())}")
    print(f"DOM notes: {notes_path}")
    print("Review artifacts/DOM_NOTES.md and confirm selectors before production use.")
    return 0


async def run_check_now(settings: Settings, browser: BrowserManager) -> int:
    """Parse today's attendance from the live page and print it (read-only)."""
    page, _ = await _open_authenticated_page(settings, browser)

    try:
        result = await parse_attendance(page)
    except AttendanceParseError as exc:
        logger.error("Attendance parsing failed: %s", exc)
        logger.error(
            "No attendance data was produced. Run `python run.py --inspect` to "
            "capture artifacts and confirm selectors."
        )
        return 1

    print("\n--- Today's attendance (read-only parse) ---")
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


async def run_authorize_gmail(settings: Settings) -> int:
    """Run the Gmail OAuth authorization flow."""
    notifier = create_notifier(settings)
    print("\n--- Gmail Authorization ---")
    print("This will open a browser window for you to grant Gmail send permission.")
    print("The authorization token will be stored locally at:", settings.gmail_token_file)

    success = await notifier.authorize()
    if success:
        email = notifier.get_authorized_email()
        print(f"\n✅ Gmail authorized successfully for: {email}")
        return 0
    else:
        print("\n❌ Gmail authorization failed. Check logs for details.")
        return 1


async def run_full_check(settings: Settings, browser: BrowserManager) -> int:
    """Run the full attendance check workflow (parse + store + email)."""
    print("\n--- Running full attendance check ---")
    try:
        result = await run_attendance_check(settings, manual=True)
    except Exception as exc:
        logger.error("Attendance check failed: %s", exc)
        return 1

    print("\n--- Attendance Check Result ---")
    print(f"Check ID: {result.id}")
    print(f"Date: {result.attendance_date}")
    print(f"Batch: {result.batch or 'N/A'}")
    print(f"Present: {result.present_count or 'N/A'}")
    print(f"Absent: {result.absent_count or 'N/A'}")
    print(f"Subjects checked: {len(result.subjects)}")
    print(f"Email status: {result.email_status}")
    if result.email_sent_at:
        print(f"Email sent at: {result.email_sent_at}")
    if result.error_message:
        print(f"Error: {result.error_message}")

    for subj in result.subjects:
        status_icons = {"PRESENT": "✅", "ABSENT": "🔴", "NOT_MARKED": "🟡", "UNKNOWN": "❓"}
        icon = status_icons.get(subj.status, "❓")
        period = f"Period {subj.period} - " if subj.period else ""
        print(f"  {icon} {period}{subj.subject_name}: {subj.status}")

    return 0


async def main(argv: list[str] | None = None) -> int:
    """Load config, initialize the database, and dispatch the requested mode."""
    args = _parse_args(argv)
    settings = Settings()
    _configure_logging(settings.log_level)

    initialize_database(settings.database_url)
    browser = BrowserManager(settings)

    try:
        await browser.start()

        if args.authorize_gmail:
            await browser.close()  # Not needed for Gmail auth
            return await run_authorize_gmail(settings)

        if args.inspect:
            return await run_inspect(settings, browser)
        if args.check_now:
            return await run_check_now(settings, browser)
        if args.run_check:
            return await run_full_check(settings, browser)

        await run_default(settings, browser)
        return 0
    finally:
        await browser.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        logger.info("Interrupted by user; browser closed.")
        sys.exit(130)
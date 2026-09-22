"""Safe, read-only navigation helpers for the PWIOI student portal."""

from urllib.parse import urlparse

from playwright.async_api import Page

from .config import Settings

LOGIN_URL = "https://app.pwioi.club/auth/student/login"
ATTENDANCE_URL = "https://app.pwioi.club/dashboard/student/attendance"


class PWIOIPortal:
    """Navigate to known portal routes without interacting with login controls."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        attendance_url: str | None = None,
    ) -> None:
        """Build the portal helper from settings, an explicit URL, or defaults."""
        if attendance_url is not None:
            self.attendance_url = attendance_url
        elif settings is not None:
            self.attendance_url = settings.pwioi_attendance_url
        else:
            self.attendance_url = ATTENDANCE_URL

    @classmethod
    def using_page(cls, page: Page) -> "PWIOIPortal":
        """Infer the attendance route from the page's current origin.

        Handy for development tools that receive a page already opened on the
        portal host; falls back to the canonical URL when the origin is unknown.
        """
        parsed = urlparse(page.url)
        if parsed.netloc:
            return cls(attendance_url=f"{parsed.scheme}://{parsed.netloc}{urlparse(ATTENDANCE_URL).path}")
        return cls()

    async def open_attendance_page(self, page: Page) -> None:
        """Open the configured attendance route; authentication remains user-controlled."""
        await page.goto(self.attendance_url, wait_until="domcontentloaded")

    @staticmethod
    def current_url(page: Page) -> str:
        """Return the browser's current URL."""
        return page.url

    @staticmethod
    def is_login_page(page: Page) -> bool:
        """Identify the known login route from the current URL only."""
        return urlparse(page.url).path.rstrip("/") == urlparse(LOGIN_URL).path.rstrip("/")

    def is_attendance_page(self, page: Page) -> bool:
        """Identify the configured attendance route from the current URL only."""
        return urlparse(page.url).path.rstrip("/") == urlparse(self.attendance_url).path.rstrip("/")


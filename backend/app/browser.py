"""Persistent, user-controlled Playwright browser management."""

from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .config import Settings


class BrowserManager:
    """Own a Chromium persistent context without automating authentication."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> BrowserContext:
        """Launch Chromium with a reusable local profile."""
        if self._context is not None:
            return self._context

        profile_dir = Path(self._settings.browser_profile_dir).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=self._settings.headless,
        )
        return self._context

    def get_context(self) -> BrowserContext:
        """Return the active persistent context."""
        if self._context is None:
            raise RuntimeError("Browser has not been started.")
        return self._context

    async def open_page(self) -> Page:
        """Return an existing page or open a new one in the persistent context."""
        context = self.get_context()
        return context.pages[0] if context.pages else await context.new_page()

    async def close(self) -> None:
        """Close Chromium and its Playwright process cleanly."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


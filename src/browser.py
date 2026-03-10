"""Playwright persistent browser context lifecycle management."""

import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from src.config import AppConfig
from src.constants import NAVIGATION_TIMEOUT_MS, PAGE_LOAD_TIMEOUT_MS, USER_AGENT, VIEWPORT

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages a persistent Playwright browser context."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None

    async def start(self) -> BrowserContext:
        """Launch browser with persistent context to preserve cookies/localStorage."""
        if self._context:
            return self._context

        logger.info("Starting browser (headless=%s)", self.config.headless)

        data_dir = Path(self.config.browser_data_dir).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(data_dir),
            channel="chrome",
            headless=self.config.headless,
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
            accept_downloads=False,
            ignore_https_errors=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-http2",
                "--disable-quic",
            ],
        )

        self._context.set_default_timeout(PAGE_LOAD_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

        logger.info("Browser started with persistent context at %s", data_dir)
        return self._context

    async def new_page(self) -> Page:
        """Create a new page in the persistent context."""
        ctx = await self.start()
        page = await ctx.new_page()
        return page

    async def get_page(self) -> Page:
        """Get an existing page or create one."""
        ctx = await self.start()
        if ctx.pages:
            return ctx.pages[0]
        return await ctx.new_page()

    async def close(self) -> None:
        """Shut down the browser context and Playwright."""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.warning("Error closing browser context: %s", e)
            self._context = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("Error stopping Playwright: %s", e)
            self._playwright = None

        logger.info("Browser closed")

    async def restart(self) -> BrowserContext:
        """Close and re-launch the browser."""
        logger.info("Restarting browser...")
        await self.close()
        return await self.start()

    @property
    def is_running(self) -> bool:
        return self._context is not None

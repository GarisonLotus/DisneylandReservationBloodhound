"""Full 4-page reservation booking flow with screenshots at each step."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

from src.auth import AuthManager
from src.browser import BrowserManager
from src.config import AppConfig
from src.constants import (
    DISNEY_RESERVATIONS_URL,
    ELEMENT_WAIT_TIMEOUT_MS,
    SCREENSHOT_DIR,
)
from src.models import BookingTarget, Park
from src.selectors import (
    CALENDAR_DAY_BY_DATE,
    CONFIRMATION_NUMBER,
    CONFIRMATION_SUCCESS,
    LOADING_SPINNER,
    PARK_FILTER_DCA,
    PARK_FILTER_DISNEYLAND,
    PARTY_CONTINUE_BUTTON,
    PARTY_MEMBER_CHECKBOX,
    PARTY_MEMBER_ROW,
    PARTY_SELECT_ALL,
    REVIEW_ACKNOWLEDGE_CHECKBOX,
    REVIEW_CONFIRM_BUTTON,
    REVIEW_SUMMARY,
)

logger = logging.getLogger(__name__)


class BookingError(Exception):
    """Raised when booking flow fails."""


class ReservationBooker:
    """Automates the full 4-page Disney reservation booking flow."""

    def __init__(
        self,
        config: AppConfig,
        auth_manager: AuthManager,
        browser_manager: BrowserManager,
    ):
        self.config = config
        self.auth = auth_manager
        self.browser = browser_manager
        self._screenshot_dir = Path(SCREENSHOT_DIR)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def book(self, target: BookingTarget) -> Optional[str]:
        """Execute the full booking flow. Returns confirmation number or None."""
        page = await self.browser.get_page()

        try:
            await self.auth.ensure_authenticated(page)

            # Page 1: Select party members
            await self._select_party(page, target)
            await self._screenshot(page, "01_party_selected")

            # Page 2: Select date and park
            await self._select_date_and_park(page, target)
            await self._screenshot(page, "02_date_park_selected")

            # Page 3: Review reservation
            await self._review_reservation(page, target)
            await self._screenshot(page, "03_review")

            # Page 4: Confirm and get confirmation number
            confirmation = await self._confirm_booking(page)
            await self._screenshot(page, "04_confirmation")

            return confirmation

        except Exception as e:
            await self._screenshot(page, "error_booking")
            logger.error("Booking failed: %s", e)
            raise BookingError(str(e)) from e

    async def _select_party(self, page: Page, target: BookingTarget) -> None:
        """Page 1: Navigate to reservation page and select party members."""
        logger.info("Step 1: Selecting party (%d guests)...", target.party_size)

        await page.goto(DISNEY_RESERVATIONS_URL, wait_until="networkidle")
        await self._wait_for_loading(page)

        # Try "Select All" first if party size matches
        select_all = await page.query_selector(PARTY_SELECT_ALL)
        if select_all:
            await select_all.click()
            await page.wait_for_timeout(500)
        else:
            # Select individual party members
            checkboxes = await page.query_selector_all(PARTY_MEMBER_CHECKBOX)
            if not checkboxes:
                # Fallback: try selecting rows
                rows = await page.query_selector_all(PARTY_MEMBER_ROW)
                if not rows:
                    raise BookingError("Could not find party member selection elements")
                checkboxes = rows

            # Select up to party_size members
            for i, checkbox in enumerate(checkboxes):
                if i >= target.party_size:
                    break
                is_checked = await checkbox.is_checked() if hasattr(checkbox, 'is_checked') else False
                if not is_checked:
                    await checkbox.click()
                    await page.wait_for_timeout(200)

        # Click continue
        continue_btn = await page.wait_for_selector(
            PARTY_CONTINUE_BUTTON,
            timeout=ELEMENT_WAIT_TIMEOUT_MS,
        )
        await continue_btn.click()
        await self._wait_for_loading(page)

        logger.info("Party selection complete")

    async def _select_date_and_park(self, page: Page, target: BookingTarget) -> None:
        """Page 2: Select the target date on the calendar and choose park."""
        logger.info("Step 2: Selecting date %s and park...", target.date)

        await self._wait_for_loading(page)

        # Select park filter if specific park requested
        if target.park != Park.EITHER:
            await self._select_park_filter(page, target.park)
            await page.wait_for_timeout(500)

        # Navigate to correct month
        from src.monitor import AvailabilityMonitor
        monitor = AvailabilityMonitor(self.config, self.auth, self.browser)
        await monitor._navigate_to_month(page, target.date)

        # Click the target date
        date_selector = CALENDAR_DAY_BY_DATE.format(date=target.date)
        date_el = await page.wait_for_selector(date_selector, timeout=ELEMENT_WAIT_TIMEOUT_MS)
        if not date_el:
            raise BookingError(f"Could not find date element for {target.date}")

        # Verify it's available before clicking
        is_disabled = await date_el.is_disabled()
        if is_disabled:
            raise BookingError(f"Date {target.date} appears to be unavailable (disabled)")

        await date_el.click()
        await self._wait_for_loading(page)

        # Click continue/next to proceed to review
        continue_btn = await page.wait_for_selector(
            PARTY_CONTINUE_BUTTON,  # Same "Continue" / "Next" button pattern
            timeout=ELEMENT_WAIT_TIMEOUT_MS,
        )
        await continue_btn.click()
        await self._wait_for_loading(page)

        logger.info("Date and park selection complete")

    async def _select_park_filter(self, page: Page, park: Park) -> None:
        """Click the park filter button for the target park."""
        selector = (
            PARK_FILTER_DISNEYLAND if park == Park.DISNEYLAND else PARK_FILTER_DCA
        )
        try:
            filter_btn = await page.wait_for_selector(selector, timeout=5000)
            if filter_btn:
                await filter_btn.click()
                logger.debug("Selected park filter: %s", park.value)
        except Exception:
            logger.debug("Park filter button not found, proceeding without filter")

    async def _review_reservation(self, page: Page, target: BookingTarget) -> None:
        """Page 3: Review the reservation details before confirming."""
        logger.info("Step 3: Reviewing reservation...")

        await self._wait_for_loading(page)

        # Wait for review summary to appear
        try:
            await page.wait_for_selector(REVIEW_SUMMARY, timeout=ELEMENT_WAIT_TIMEOUT_MS)
        except Exception:
            logger.warning("Review summary element not found, proceeding anyway")

        # Check acknowledgement checkbox if present
        try:
            ack_checkbox = await page.query_selector(REVIEW_ACKNOWLEDGE_CHECKBOX)
            if ack_checkbox:
                is_checked = await ack_checkbox.is_checked()
                if not is_checked:
                    await ack_checkbox.click()
                    await page.wait_for_timeout(300)
        except Exception:
            pass

        logger.info("Review complete, ready to confirm")

    async def _confirm_booking(self, page: Page) -> Optional[str]:
        """Page 4: Click confirm and extract confirmation number."""
        logger.info("Step 4: Confirming reservation...")

        # Click the confirm/book button
        confirm_btn = await page.wait_for_selector(
            REVIEW_CONFIRM_BUTTON,
            timeout=ELEMENT_WAIT_TIMEOUT_MS,
        )
        await confirm_btn.click()

        # Wait for confirmation page
        await self._wait_for_loading(page)

        try:
            await page.wait_for_selector(CONFIRMATION_SUCCESS, timeout=ELEMENT_WAIT_TIMEOUT_MS)
        except Exception:
            logger.warning("Confirmation success element not found")

        # Extract confirmation number
        confirmation_number = None
        try:
            conf_el = await page.wait_for_selector(CONFIRMATION_NUMBER, timeout=5000)
            if conf_el:
                confirmation_number = (await conf_el.inner_text()).strip()
                logger.info("Confirmation number: %s", confirmation_number)
        except Exception:
            logger.warning("Could not extract confirmation number")

        # If no specific element found, try to get text from the page
        if not confirmation_number:
            try:
                page_text = await page.inner_text("body")
                # Look for common confirmation patterns
                import re
                match = re.search(r'confirmation[:\s#]*([A-Z0-9]{6,})', page_text, re.IGNORECASE)
                if match:
                    confirmation_number = match.group(1)
            except Exception:
                pass

        return confirmation_number

    async def _wait_for_loading(self, page: Page) -> None:
        """Wait for any loading spinners to disappear."""
        try:
            await page.wait_for_selector(
                LOADING_SPINNER,
                state="hidden",
                timeout=ELEMENT_WAIT_TIMEOUT_MS,
            )
        except Exception:
            pass  # Spinner may not be present
        await page.wait_for_timeout(300)

    async def _screenshot(self, page: Page, name: str) -> None:
        """Take a timestamped screenshot for audit trail."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{name}.png"
        filepath = self._screenshot_dir / filename
        try:
            await page.screenshot(path=str(filepath), full_page=True)
            logger.info("Screenshot saved: %s", filepath)
        except Exception as e:
            logger.warning("Screenshot failed: %s", e)

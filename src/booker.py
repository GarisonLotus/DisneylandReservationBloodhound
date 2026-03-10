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
    BOOK_RESERVATION_BUTTON,
    CALENDAR_DAY_BY_DATE,
    LOADING_SPINNER,
    PARK_FILTER_DCA,
    PARK_FILTER_DISNEYLAND,
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
        if self.config.debug_images:
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

        await page.goto(DISNEY_RESERVATIONS_URL, wait_until="domcontentloaded")
        await self._wait_for_loading(page)

        # Click "Book Theme Park Reservation" to start the booking flow
        try:
            book_btn = await page.wait_for_selector(BOOK_RESERVATION_BUTTON, timeout=ELEMENT_WAIT_TIMEOUT_MS)
            if book_btn:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=ELEMENT_WAIT_TIMEOUT_MS):
                    await book_btn.click()
                await self._wait_for_loading(page)
        except Exception:
            logger.debug("Book reservation button not found, may already be on booking page")

        # Select configured party members by name
        party_names = self.config.party_members
        first_name = party_names[0]

        # Wait for JS to render party member content
        await page.wait_for_timeout(3000)

        # Party content may be in an iframe — search all frames
        target_frame = page
        all_frames = [page] + page.frames
        for frame in all_frames:
            try:
                locator = frame.locator(f'text=/{first_name}/i')
                count = await locator.count()
                if count > 0:
                    target_frame = frame
                    break
            except Exception:
                continue
        else:
            raise BookingError(f"Party member '{first_name}' not found on page or in any iframe")

        for name in party_names:
            name_locator = target_frame.locator(f'text=/{name}/i').first
            count = await name_locator.count()
            if count == 0:
                raise BookingError(f"Party member not found on page: {name}")

            handle = await name_locator.element_handle()
            clicked = await handle.evaluate('''(el) => {
                let node = el;
                while (node) {
                    if (node.tagName && node.tagName.toLowerCase() === 'com-checkbox') {
                        if (node.shadowRoot) {
                            const input = node.shadowRoot.querySelector('input[type="checkbox"]');
                            if (input) { input.click(); return 'input'; }
                            const indicator = node.shadowRoot.querySelector(
                                '.checkbox-indicator, .checkbox-icon, [role="checkbox"], .check'
                            );
                            if (indicator) { indicator.click(); return 'indicator'; }
                        }
                        node.click();
                        return 'element';
                    }
                    if (!node.parentElement && node.getRootNode() !== document) {
                        node = node.getRootNode().host;
                    } else {
                        node = node.parentElement;
                    }
                }
                el.click();
                return 'direct';
            }''')
            logger.info("Selected party member: %s (click method: %s)", name, clicked)

        await page.wait_for_timeout(500)

        # Click Next button
        next_clicked = False
        for selector in [
            'button:visible:has-text("Next")',
            'com-button:has-text("Next")',
            '[role="button"]:has-text("Next")',
        ]:
            try:
                btn = target_frame.locator(selector).first
                if await btn.count() > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=ELEMENT_WAIT_TIMEOUT_MS):
                        await btn.click(force=True)
                    next_clicked = True
                    break
            except Exception:
                continue

        if not next_clicked:
            raise BookingError("Could not find Next button on party selection page")

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

        # Navigate to correct month (uses Playwright locators to pierce Shadow DOM)
        from src.monitor import AvailabilityMonitor
        monitor = AvailabilityMonitor(self.config, self.auth, self.browser)
        await monitor._navigate_to_month(page, target.date)

        # Click the target date using Playwright locator (pierces Shadow DOM)
        date_locator = page.locator(CALENDAR_DAY_BY_DATE.format(date=target.date))
        try:
            await date_locator.wait_for(state="visible", timeout=ELEMENT_WAIT_TIMEOUT_MS)
        except Exception:
            raise BookingError(f"Could not find date element for {target.date}")

        # Verify it's available before clicking
        aria_disabled = await date_locator.get_attribute('aria-disabled')
        has_disabled = await date_locator.get_attribute('disabled')
        if aria_disabled == 'true' or has_disabled is not None:
            raise BookingError(f"Date {target.date} appears to be unavailable (disabled)")

        await date_locator.click()
        await self._wait_for_loading(page)
        await page.wait_for_timeout(1000)

        # After clicking a date, a "Select a Park" section appears below the calendar.
        # We must click a specific park card before the Next button will work.
        await self._select_park_card(page, target.park)
        await self._screenshot(page, "02a_park_selected")

        # Click Next button to proceed to review page
        next_clicked = False
        for selector in [
            'button:visible:has-text("Next")',
            'com-button:has-text("Next")',
            '[role="button"]:has-text("Next")',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=ELEMENT_WAIT_TIMEOUT_MS):
                        await btn.click(force=True)
                    next_clicked = True
                    break
            except Exception:
                continue

        if not next_clicked:
            raise BookingError("Could not find Next button on calendar page")

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

    async def _select_park_card(self, page: Page, park: Park) -> None:
        """Select a park from the 'Select a Park' section that appears after clicking a date.

        After clicking a date, Disney shows park cards with name, hours, and a
        'Select' link. We use JS to walk shadow roots, find all 'Select' links,
        then click the one whose parent card contains the target park name.
        """
        # Wait for the "Select a Park" heading to appear
        try:
            await page.locator('text=/Select a Park/i').first.wait_for(
                state="visible", timeout=ELEMENT_WAIT_TIMEOUT_MS
            )
        except Exception:
            logger.warning("'Select a Park' section not found, proceeding anyway")
            return

        # Give park cards time to fully render
        await page.wait_for_timeout(1500)

        # Determine preferred park name
        if park == Park.DISNEYLAND:
            park_name = "Disneyland Park"
        elif park == Park.CALIFORNIA_ADVENTURE:
            park_name = "Disney California Adventure"
        else:
            park_name = "Disneyland Park"  # Default preference for EITHER

        # Use JS to find "Select" links within park cards (handles Shadow DOM)
        clicked = await page.evaluate('''(parkName) => {
            function walkShadow(root, callback) {
                callback(root);
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) walkShadow(el.shadowRoot, callback);
                });
            }

            // Collect all leaf elements with exact text "Select"
            const selectLinks = [];
            walkShadow(document, (root) => {
                root.querySelectorAll('*').forEach(el => {
                    if (el.children.length === 0 &&
                        el.textContent.trim().toLowerCase() === 'select') {
                        selectLinks.push(el);
                    }
                });
            });

            if (selectLinks.length === 0) return 'no-select-links';

            // For each "Select" link, walk up to check if parent card has the park name
            for (const link of selectLinks) {
                let container = link;
                for (let i = 0; i < 15; i++) {
                    if (!container) break;
                    if (container.textContent &&
                        container.textContent.toLowerCase().includes(parkName.toLowerCase())) {
                        link.click();
                        return 'matched:' + parkName;
                    }
                    if (!container.parentElement && container.getRootNode() !== document) {
                        container = container.getRootNode().host;
                    } else {
                        container = container.parentElement;
                    }
                }
            }

            // Fallback: click the first "Select" link
            selectLinks[0].click();
            return 'fallback-first';
        }''', park_name)

        if clicked and clicked.startswith('no-'):
            raise BookingError("Could not find any 'Select' links in park cards")

        logger.info("Selected park card: %s (result: %s)", park_name, clicked)
        await page.wait_for_timeout(500)

    async def _review_reservation(self, page: Page, target: BookingTarget) -> None:
        """Page 3: Review the reservation details and accept terms.

        The review page ('Confirm Your Selections') has a Terms & Conditions
        checkbox that must be checked before the Confirm button will work.
        The checkbox is a <com-checkbox> with Shadow DOM.
        """
        logger.info("Step 3: Reviewing reservation...")

        await self._wait_for_loading(page)

        # Wait for the review page heading
        try:
            await page.locator('text=/Confirm Your Selections/i').first.wait_for(
                state="visible", timeout=ELEMENT_WAIT_TIMEOUT_MS
            )
        except Exception:
            logger.warning("Review page heading not found, proceeding anyway")

        # Check the Terms and Conditions checkbox (Shadow DOM <com-checkbox>)
        try:
            tc_locator = page.locator('text=/I have read and agree/i').first
            if await tc_locator.count() > 0:
                handle = await tc_locator.element_handle()
                clicked = await handle.evaluate('''(el) => {
                    let node = el;
                    while (node) {
                        if (node.tagName && node.tagName.toLowerCase() === 'com-checkbox') {
                            if (node.shadowRoot) {
                                const input = node.shadowRoot.querySelector('input[type="checkbox"]');
                                if (input) { input.click(); return 'input'; }
                                const indicator = node.shadowRoot.querySelector(
                                    '.checkbox-indicator, .checkbox-icon, [role="checkbox"], .check'
                                );
                                if (indicator) { indicator.click(); return 'indicator'; }
                            }
                            node.click();
                            return 'element';
                        }
                        if (!node.parentElement && node.getRootNode() !== document) {
                            node = node.getRootNode().host;
                        } else {
                            node = node.parentElement;
                        }
                    }
                    el.click();
                    return 'direct';
                }''')
                logger.info("Terms checkbox clicked (method: %s)", clicked)
            else:
                logger.warning("Terms checkbox text not found")
        except Exception as e:
            logger.warning("Could not click Terms checkbox: %s", e)

        await page.wait_for_timeout(500)
        logger.info("Review complete, ready to confirm")

    async def _confirm_booking(self, page: Page) -> Optional[str]:
        """Page 4: Click confirm and extract confirmation number.

        The Confirm button may be a <com-button> or styled button. Must avoid
        matching the hidden OneTrust 'Confirm My Choices' cookie consent button.
        """
        logger.info("Step 4: Confirming reservation...")

        # Click the Confirm button.
        # The button may be a <com-button>, <button>, <a>, or other custom element.
        # Must avoid the hidden OneTrust "Confirm My Choices" cookie consent button.
        confirm_clicked = False

        # Strategy 1: Playwright locators (pierce Shadow DOM automatically)
        for locator_fn in [
            lambda: page.get_by_role("button", name="Confirm"),
            lambda: page.get_by_text("Confirm", exact=True),
            lambda: page.locator('com-button:has-text("Confirm")'),
        ]:
            try:
                loc = locator_fn()
                count = await loc.count()
                for i in range(count):
                    el = loc.nth(i)
                    # Skip OneTrust cookie consent button
                    class_attr = await el.get_attribute("class") or ""
                    if "onetrust" in class_attr:
                        continue
                    # Skip if hidden
                    if not await el.is_visible():
                        continue
                    await el.click()
                    confirm_clicked = True
                    logger.info("Clicked confirm button (Playwright locator)")
                    break
            except Exception:
                continue
            if confirm_clicked:
                break

        # Strategy 2: JS walkShadow — find ANY visible element with exact text "Confirm"
        if not confirm_clicked:
            try:
                clicked = await page.evaluate('''() => {
                    function walkShadow(root, cb) {
                        cb(root);
                        root.querySelectorAll('*').forEach(el => {
                            if (el.shadowRoot) walkShadow(el.shadowRoot, cb);
                        });
                    }
                    let found = false;
                    walkShadow(document, (root) => {
                        if (found) return;
                        root.querySelectorAll('*').forEach(el => {
                            if (found) return;
                            if (el.children.length > 3) return;
                            const text = el.textContent?.trim();
                            if (text === 'Confirm' &&
                                !el.classList?.contains('onetrust-close-btn-handler') &&
                                el.getBoundingClientRect().width > 20) {
                                el.click();
                                found = true;
                            }
                        });
                    });
                    return found;
                }''')
                if clicked:
                    confirm_clicked = True
                    logger.info("Clicked confirm button via JS walkShadow")
            except Exception:
                pass

        if not confirm_clicked:
            raise BookingError("Could not click the Confirm button")

        # Wait for confirmation page
        await self._wait_for_loading(page)
        await page.wait_for_timeout(2000)

        # Take a screenshot of the result page
        await self._screenshot(page, "04a_post_confirm")

        # Extract confirmation number from the confirmation page.
        # Format: "Confirmation Number: 07729126388729600" (pure digits)
        import re
        confirmation_number = None

        # Strategy 1: Playwright locator for "Confirmation Number" text
        try:
            loc = page.locator('text=/Confirmation Number/i').first
            if await loc.count() > 0:
                text = await loc.inner_text()
                logger.info("Found confirmation text: %s", text)
                match = re.search(r'(\d{8,})', text)
                if match:
                    confirmation_number = match.group(1)
        except Exception:
            pass

        # Strategy 2: JS walkShadow for "Confirmation Number" followed by digits
        if not confirmation_number:
            try:
                confirmation_number = await page.evaluate('''() => {
                    function walkShadow(root, cb) {
                        cb(root);
                        root.querySelectorAll('*').forEach(el => {
                            if (el.shadowRoot) walkShadow(el.shadowRoot, cb);
                        });
                    }
                    let result = null;
                    walkShadow(document, (root) => {
                        if (result) return;
                        root.querySelectorAll('*').forEach(el => {
                            if (result) return;
                            const text = el.textContent || '';
                            const match = text.match(/confirmation\\s+number[:\\s]*?(\\d{8,})/i);
                            if (match) {
                                result = match[1];
                            }
                        });
                    });
                    return result;
                }''')
                if confirmation_number:
                    logger.info("Confirmation number (JS): %s", confirmation_number)
            except Exception:
                pass

        if confirmation_number:
            logger.info("Confirmation number: %s", confirmation_number)
        else:
            logger.warning("Could not extract confirmation number")

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
        """Take a timestamped screenshot for audit trail (only if DEBUG_IMAGES=true)."""
        if not self.config.debug_images:
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{name}.png"
        filepath = self._screenshot_dir / filename
        try:
            await page.screenshot(path=str(filepath), full_page=True)
            logger.info("Screenshot saved: %s", filepath)
        except Exception as e:
            logger.warning("Screenshot failed: %s", e)

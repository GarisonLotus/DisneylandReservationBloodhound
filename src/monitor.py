"""Availability monitoring via API-first approach with browser fallback."""

import logging
from datetime import datetime
from typing import Optional

import aiohttp
from playwright.async_api import Page

from src.auth import AuthManager
from src.browser import BrowserManager
from src.config import AppConfig
from src.constants import (
    DISNEY_CALENDAR_API_URL,
    DISNEY_RESERVATIONS_URL,
    ELEMENT_WAIT_TIMEOUT_MS,
    PARK_IDS,
)
from src.models import AvailabilityResult, AvailabilityStatus, BookingTarget, Park
from src.selectors import (
    BOOK_RESERVATION_BUTTON,
    CALENDAR_CONTAINER,
    CALENDAR_DAY_AVAILABLE,
    CALENDAR_DAY_BY_DATE,
    LOADING_SPINNER,
    PARTY_NEXT_BUTTON,
)

logger = logging.getLogger(__name__)


class AvailabilityMonitor:
    """Checks reservation availability using API calls with browser fallback."""

    def __init__(
        self,
        config: AppConfig,
        auth_manager: AuthManager,
        browser_manager: BrowserManager,
    ):
        self.config = config
        self.auth = auth_manager
        self.browser = browser_manager
        self._on_calendar_page = False

    async def check_availability(self, target: BookingTarget) -> list[AvailabilityResult]:
        """Check availability for the target date/park. Returns results for each park checked."""
        # Try API first (faster, lighter)
        if self.auth.token:
            try:
                results = await self._check_via_api(target)
                if results and all(r.status != AvailabilityStatus.ERROR for r in results):
                    return results
                logger.debug("API check returned errors, falling back to browser")
            except Exception as e:
                logger.debug("API check failed (%s), falling back to browser", e)

        # Browser fallback
        return await self._check_via_browser(target)

    async def _check_via_api(self, target: BookingTarget) -> list[AvailabilityResult]:
        """Check availability via Disney's calendar API endpoint."""
        token = self.auth.token
        if not token:
            return [AvailabilityResult(
                date=target.date,
                park=target.park,
                status=AvailabilityStatus.ERROR,
                source="api",
                message="No auth token available",
            )]

        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Accept": "application/json",
        }

        parks_to_check = self._get_parks_to_check(target.park)
        results = []

        async with aiohttp.ClientSession() as session:
            for park in parks_to_check:
                park_id = PARK_IDS.get(park.value, "")
                params = {
                    "segment": "ap",  # DISCOVERY: confirm segment parameter
                    "parkId": park_id,
                    "date": target.date,
                    "partySize": str(target.party_size),
                }

                try:
                    async with session.get(
                        DISNEY_CALENDAR_API_URL,
                        headers=headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 401:
                            logger.warning("API returned 401 - token expired")
                            self.auth.clear_token()
                            return [AvailabilityResult(
                                date=target.date,
                                park=park,
                                status=AvailabilityStatus.ERROR,
                                source="api",
                                message="Token expired (401)",
                            )]

                        if resp.status == 429:
                            logger.warning("API returned 429 - rate limited")
                            return [AvailabilityResult(
                                date=target.date,
                                park=park,
                                status=AvailabilityStatus.ERROR,
                                source="api",
                                message="Rate limited (429)",
                            )]

                        if resp.status != 200:
                            results.append(AvailabilityResult(
                                date=target.date,
                                park=park,
                                status=AvailabilityStatus.ERROR,
                                source="api",
                                message=f"HTTP {resp.status}",
                            ))
                            continue

                        data = await resp.json()
                        available = self._parse_api_availability(data, target.date)
                        results.append(AvailabilityResult(
                            date=target.date,
                            park=park,
                            status=AvailabilityStatus.AVAILABLE if available else AvailabilityStatus.UNAVAILABLE,
                            source="api",
                        ))
                except aiohttp.ClientError as e:
                    results.append(AvailabilityResult(
                        date=target.date,
                        park=park,
                        status=AvailabilityStatus.ERROR,
                        source="api",
                        message=str(e),
                    ))

        return results

    def _parse_api_availability(self, data: dict, target_date: str) -> bool:
        """Parse the API response to determine if the target date is available.

        DISCOVERY: the actual response schema needs to be confirmed from network traffic.
        This implementation handles common patterns.
        """
        # Pattern 1: list of date objects
        if isinstance(data, list):
            for entry in data:
                if entry.get("date") == target_date:
                    return entry.get("available", False)
            return False

        # Pattern 2: nested under a key
        calendar = data.get("calendar", data.get("availability", data.get("dates", [])))
        if isinstance(calendar, list):
            for entry in calendar:
                if entry.get("date") == target_date:
                    return entry.get("available", False)

        # Pattern 3: date as key
        if target_date in data:
            entry = data[target_date]
            if isinstance(entry, bool):
                return entry
            if isinstance(entry, dict):
                return entry.get("available", False)

        return False

    async def _check_via_browser(self, target: BookingTarget) -> list[AvailabilityResult]:
        """Check availability by loading the reservation calendar in the browser.

        First check: full navigation flow (reservations → party → calendar).
        Subsequent checks: stay on calendar page and click "Refresh Calendar".
        """
        page = await self.browser.get_page()
        parks_to_check = self._get_parks_to_check(target.park)
        results = []

        try:
            if self._on_calendar_page and "select-date" in page.url:
                # Subsequent check — refresh the calendar in place
                await self._refresh_calendar(page)
            else:
                # First check — full navigation flow
                await self._navigate_to_calendar(page)

            # Extract and log the "Availability as of..." timestamp
            await self._log_availability_timestamp(page)

            # Read availability from the calendar
            results = await self._read_calendar_availability(page, target, parks_to_check)

        except Exception as e:
            logger.error("Browser availability check failed: %s", e)
            self._on_calendar_page = False
            results.append(AvailabilityResult(
                date=target.date,
                park=target.park,
                status=AvailabilityStatus.ERROR,
                source="browser",
                message=str(e),
            ))

        return results

    async def _navigate_to_calendar(self, page: Page) -> None:
        """Full navigation: reservations list → party selection → calendar page."""
        await self.auth.ensure_authenticated(page)
        await page.goto(DISNEY_RESERVATIONS_URL, wait_until="domcontentloaded")

        # Click "Book Theme Park Reservation" to reach the booking flow
        try:
            book_btn = await page.wait_for_selector(BOOK_RESERVATION_BUTTON, timeout=ELEMENT_WAIT_TIMEOUT_MS)
            if book_btn:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=ELEMENT_WAIT_TIMEOUT_MS):
                    await book_btn.click()
                logger.info("Navigated to booking page: %s", page.url)
        except Exception as e:
            logger.debug("Book reservation button navigation: %s", e)

        # Select party members and click Next
        if "select-party" in page.url:
            await self._select_party_members(page)

        # Wait for calendar page to load
        await self._wait_for_calendar(page)

    async def _refresh_calendar(self, page: Page) -> None:
        """Click 'Refresh Calendar' link to reload availability data in place."""
        logger.debug("Refreshing calendar...")
        try:
            refresh_link = page.locator('text=/Refresh Calendar/i').first
            if await refresh_link.count() > 0:
                await refresh_link.click()
                # Wait for calendar data to reload
                await page.wait_for_timeout(2000)
                # Wait for any loading indicators to clear
                try:
                    await page.wait_for_selector(
                        LOADING_SPINNER, state="hidden", timeout=ELEMENT_WAIT_TIMEOUT_MS,
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(1000)
            else:
                logger.warning("Refresh Calendar link not found, doing full navigation")
                self._on_calendar_page = False
                await self._navigate_to_calendar(page)
        except Exception as e:
            logger.warning("Calendar refresh failed (%s), doing full navigation", e)
            self._on_calendar_page = False
            await self._navigate_to_calendar(page)

    async def _wait_for_calendar(self, page: Page) -> None:
        """Wait for the calendar/date selection page to fully load."""
        from pathlib import Path
        from src.constants import SCREENSHOT_DIR

        try:
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)  # let JS render
            await page.locator('text=/Select a Date/i').first.wait_for(timeout=ELEMENT_WAIT_TIMEOUT_MS)
            self._on_calendar_page = True
            logger.info("Calendar page loaded: %s", page.url)
        except Exception:
            self._on_calendar_page = False
            if self.config.debug_images:
                Path(SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
                screenshot_path = f"{SCREENSHOT_DIR}/calendar_not_found.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.warning(
                    "Calendar page not found. Screenshot saved to %s. URL: %s",
                    screenshot_path, page.url,
                )
            raise Exception("Calendar page not found")

    async def _log_availability_timestamp(self, page: Page) -> None:
        """Extract and log the 'Availability as of...' timestamp from the calendar page."""
        try:
            ts_locator = page.locator('text=/Availability as of/i').first
            if await ts_locator.count() > 0:
                ts_text = await ts_locator.inner_text()
                logger.info("Calendar data: %s", ts_text.strip())
            else:
                # Try extracting via JS from shadow DOM
                ts_text = await page.evaluate('''() => {
                    function walkShadow(root) {
                        const els = root.querySelectorAll ? root.querySelectorAll('*') : [];
                        for (const el of els) {
                            const text = el.textContent || '';
                            if (text.includes('Availability as of') && el.children.length === 0) {
                                return text.trim();
                            }
                            if (el.shadowRoot) {
                                const found = walkShadow(el.shadowRoot);
                                if (found) return found;
                            }
                        }
                        return null;
                    }
                    return walkShadow(document);
                }''')
                if ts_text:
                    logger.info("Calendar data: %s", ts_text)
                else:
                    logger.debug("Could not find availability timestamp on page")
        except Exception:
            logger.debug("Could not extract availability timestamp")

    async def _read_calendar_availability(
        self, page: Page, target: BookingTarget, parks_to_check: list[Park],
    ) -> list[AvailabilityResult]:
        """Read availability for the target date from the calendar DOM."""
        from pathlib import Path
        from src.constants import SCREENSHOT_DIR
        if self.config.debug_images:
            Path(SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/calendar_page.png", full_page=True)

        results = []

        # Check availability using <com-calendar-date> elements.
        # The slotted version (slot="YYYY-MM-DD") has real availability info.
        # Class values: "all" (either park), "blocked" (blocked out), "noInfo" (no data)
        date_locator = page.locator(
            f'com-calendar-date[slot="{target.date}"]'
        )
        date_count = await date_locator.count()
        logger.info("Found %d com-calendar-date elements for %s", date_count, target.date)

        if date_count == 0:
            logger.warning("Target date %s not found on calendar", target.date)
            for park in parks_to_check:
                results.append(AvailabilityResult(
                    date=target.date,
                    park=park,
                    status=AvailabilityStatus.UNAVAILABLE,
                    source="browser",
                    message="Date not found on calendar",
                ))
        else:
            handle = await date_locator.first.element_handle()
            date_info = await handle.evaluate('''(el) => {
                return {
                    class: el.className || '',
                    date: el.getAttribute('date'),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    ariaDisabled: el.getAttribute('aria-disabled'),
                    disabled: el.hasAttribute('disabled'),
                    unavailable: el.hasAttribute('unavailable'),
                };
            }''')
            logger.info("Date %s info: %s", target.date, date_info)

            css_class = date_info.get('class', '')
            aria_label = date_info.get('ariaLabel', '').lower()
            is_disabled = date_info.get('disabled', False)
            is_unavailable = date_info.get('unavailable', False)

            # Determine per-park availability from the class and aria-label
            available_parks = set()
            if not is_disabled and not is_unavailable and css_class != 'noInfo':
                if css_class == 'all' or 'either park' in aria_label:
                    available_parks = {Park.DISNEYLAND, Park.CALIFORNIA_ADVENTURE}
                elif 'disneyland park' in aria_label and 'california' not in aria_label:
                    available_parks = {Park.DISNEYLAND}
                elif 'california adventure' in aria_label:
                    available_parks = {Park.CALIFORNIA_ADVENTURE}
                elif css_class not in ('blocked', 'noInfo', ''):
                    # Unknown class but not blocked — assume available
                    available_parks = {Park.DISNEYLAND, Park.CALIFORNIA_ADVENTURE}

            for park in parks_to_check:
                is_available = park in available_parks
                results.append(AvailabilityResult(
                    date=target.date,
                    park=park,
                    status=AvailabilityStatus.AVAILABLE if is_available else AvailabilityStatus.UNAVAILABLE,
                    source="browser",
                ))

        return results

    async def _select_party_members(self, page: Page) -> None:
        """Select configured party members by name and click Next."""
        from pathlib import Path
        from src.constants import SCREENSHOT_DIR

        party_names = self.config.party_members
        logger.info("Selecting party members: %s", party_names)
        logger.info("Current URL: %s", page.url)

        # Wait for page content to render
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(3000)  # allow JS to render

        # Screenshot to see what's on the page before searching for names
        if self.config.debug_images:
            Path(SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/party_page.png", full_page=True)
            logger.info("Saved party page screenshot to %s/party_page.png", SCREENSHOT_DIR)

        # The party content may be in an iframe — find the right frame
        target_frame = page
        first_name = party_names[0]

        # Check all frames (main page + iframes) for party member names
        all_frames = [page] + page.frames
        for frame in all_frames:
            try:
                # Use case-insensitive regex to match names (Disney shows ALL CAPS)
                locator = frame.locator(f'text=/{first_name}/i')
                count = await locator.count()
                if count > 0:
                    target_frame = frame
                    frame_name = frame.name or frame.url
                    logger.info("Found party members in frame: %s", frame_name)
                    break
            except Exception:
                continue
        else:
            # No frame had the name — save debug info and raise
            if self.config.debug_images:
                await page.screenshot(path=f"{SCREENSHOT_DIR}/party_name_not_found.png", full_page=True)
            frame_urls = [f.url for f in all_frames]
            logger.error(
                "Party member '%s' not found in any frame. Frames: %s. URL: %s",
                first_name, frame_urls, page.url,
            )
            raise Exception(f"Party member '{first_name}' not found on page or in any iframe")

        # Disney uses <com-checkbox> custom web components with Shadow DOM.
        # Playwright's text locator finds the <h3> inside, but inner_text() on
        # the com-checkbox returns empty (slotted content). Strategy:
        # 1. Find the <h3> name element via Playwright's shadow-piercing locator
        # 2. Get element handle → walk up DOM to parent com-checkbox
        # 3. Click the checkbox input inside its shadow root
        for name in party_names:
            name_locator = target_frame.locator(f'text=/{name}/i').first
            count = await name_locator.count()
            if count == 0:
                logger.warning("Party member not found on page: %s", name)
                continue

            handle = await name_locator.element_handle()
            clicked = await handle.evaluate('''(el) => {
                // Walk up from the <h3> to find the parent <com-checkbox>
                let node = el;
                while (node) {
                    if (node.tagName && node.tagName.toLowerCase() === 'com-checkbox') {
                        // Try clicking the actual input inside shadow DOM
                        if (node.shadowRoot) {
                            const input = node.shadowRoot.querySelector('input[type="checkbox"]');
                            if (input) {
                                input.click();
                                return 'input';
                            }
                            // Try any clickable indicator element
                            const indicator = node.shadowRoot.querySelector(
                                '.checkbox-indicator, .checkbox-icon, [role="checkbox"], .check'
                            );
                            if (indicator) {
                                indicator.click();
                                return 'indicator';
                            }
                        }
                        // Fallback: click the com-checkbox itself
                        node.click();
                        return 'element';
                    }
                    // Cross shadow DOM boundary if needed
                    if (!node.parentElement && node.getRootNode() !== document) {
                        node = node.getRootNode().host;
                    } else {
                        node = node.parentElement;
                    }
                }
                // Last resort: click the original element with force
                el.click();
                return 'direct';
            }''')
            logger.info("Selected party member: %s (click method: %s)", name, clicked)

        await page.wait_for_timeout(500)  # let selections register

        # Take a screenshot to verify selections
        if self.config.debug_images:
            await page.screenshot(path=f"{SCREENSHOT_DIR}/party_selected.png", full_page=True)

        # Click Next button — also inside Shadow DOM custom components.
        # Use Playwright's shadow-piercing locator to find a visible button.
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
                    logger.info("Proceeded to next page: %s", page.url)
                    break
            except Exception as e:
                logger.debug("Next button selector '%s' failed: %s", selector, e)
                continue

        if not next_clicked:
            if self.config.debug_images:
                # Screenshot to debug what the Next button looks like
                await page.screenshot(path=f"{SCREENSHOT_DIR}/next_button_not_found.png", full_page=True)
            logger.warning("Could not find Next button. URL: %s", page.url)

    async def _navigate_to_month(self, page: Page, target_date: str) -> None:
        """Navigate the calendar to the month containing the target date.

        Uses Playwright locators (not query_selector) to pierce Shadow DOM.
        First checks if the target date element is already visible, then
        falls back to reading month aria-labels and clicking next.
        """
        from src.selectors import CALENDAR_NEXT_MONTH

        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        target_month_year = target_dt.strftime("%B %Y")  # e.g., "March 2026"

        for _ in range(12):  # max 12 months forward
            # Shortcut: if the target date element is already on the page, we're done
            date_locator = page.locator(f'com-calendar-date[slot="{target_date}"]')
            if await date_locator.count() > 0:
                logger.debug("Target date %s already visible on calendar", target_date)
                return

            # Check month labels using Playwright locator (pierces Shadow DOM)
            month_labels = page.locator('.month[aria-label]')
            count = await month_labels.count()
            for i in range(count):
                aria_label = await month_labels.nth(i).get_attribute('aria-label')
                if aria_label and target_month_year.lower() in aria_label.lower():
                    logger.debug("Found target month: %s", aria_label)
                    return

            # Click next month using locator (pierces Shadow DOM)
            try:
                next_btn = page.locator(CALENDAR_NEXT_MONTH).first
                if await next_btn.count() > 0:
                    await next_btn.click()
                    await page.wait_for_timeout(500)
                else:
                    break
            except Exception:
                break

        logger.warning("Could not navigate to month: %s", target_month_year)

    async def _is_day_available(self, page: Page, day_el, target_date: str) -> bool:
        """Determine if a calendar day element represents an available date.

        DISCOVERY: the exact attributes/classes marking availability need live-site confirmation.
        """
        # Check data-available attribute
        available_attr = await day_el.get_attribute("data-available")
        if available_attr is not None:
            return available_attr.lower() == "true"

        # Check aria-disabled
        disabled_attr = await day_el.get_attribute("aria-disabled")
        if disabled_attr is not None:
            return disabled_attr.lower() != "true"

        # Check CSS classes for common patterns
        class_attr = await day_el.get_attribute("class") or ""
        unavailable_classes = ["unavailable", "disabled", "blocked", "sold-out"]
        for cls in unavailable_classes:
            if cls in class_attr.lower():
                return False

        # If element is a button and not disabled, assume available
        tag = await day_el.evaluate("el => el.tagName.toLowerCase()")
        if tag == "button":
            is_disabled = await day_el.is_disabled()
            return not is_disabled

        return False

    def _get_parks_to_check(self, park: Park) -> list[Park]:
        if park == Park.EITHER:
            return [Park.DISNEYLAND, Park.CALIFORNIA_ADVENTURE]
        return [park]

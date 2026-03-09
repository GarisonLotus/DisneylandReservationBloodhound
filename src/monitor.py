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
    CALENDAR_CONTAINER,
    CALENDAR_DAY_AVAILABLE,
    CALENDAR_DAY_BY_DATE,
    LOADING_SPINNER,
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
        """Check availability by loading the reservation calendar in the browser."""
        page = await self.browser.get_page()
        parks_to_check = self._get_parks_to_check(target.park)
        results = []

        try:
            await self.auth.ensure_authenticated(page)
            await page.goto(DISNEY_RESERVATIONS_URL, wait_until="networkidle")

            # Wait for calendar to load
            try:
                await page.wait_for_selector(
                    CALENDAR_CONTAINER,
                    timeout=ELEMENT_WAIT_TIMEOUT_MS,
                )
            except Exception:
                logger.warning("Calendar container not found on page")
                return [AvailabilityResult(
                    date=target.date,
                    park=target.park,
                    status=AvailabilityStatus.ERROR,
                    source="browser",
                    message="Calendar not found on page",
                )]

            # Wait for loading spinners to disappear
            try:
                await page.wait_for_selector(
                    LOADING_SPINNER,
                    state="hidden",
                    timeout=ELEMENT_WAIT_TIMEOUT_MS,
                )
            except Exception:
                pass  # Spinner may not exist

            # Navigate to the correct month if needed
            await self._navigate_to_month(page, target.date)

            # Check for the target date
            for park in parks_to_check:
                date_selector = CALENDAR_DAY_BY_DATE.format(date=target.date)
                day_el = await page.query_selector(date_selector)

                if day_el:
                    # Check if the day element indicates availability
                    is_available = await self._is_day_available(page, day_el, target.date)
                    results.append(AvailabilityResult(
                        date=target.date,
                        park=park,
                        status=AvailabilityStatus.AVAILABLE if is_available else AvailabilityStatus.UNAVAILABLE,
                        source="browser",
                    ))
                else:
                    results.append(AvailabilityResult(
                        date=target.date,
                        park=park,
                        status=AvailabilityStatus.UNKNOWN,
                        source="browser",
                        message="Date element not found in calendar",
                    ))

        except Exception as e:
            logger.error("Browser availability check failed: %s", e)
            results.append(AvailabilityResult(
                date=target.date,
                park=target.park,
                status=AvailabilityStatus.ERROR,
                source="browser",
                message=str(e),
            ))

        return results

    async def _navigate_to_month(self, page: Page, target_date: str) -> None:
        """Navigate the calendar to the month containing the target date."""
        from src.selectors import CALENDAR_MONTH_LABEL, CALENDAR_NEXT_MONTH

        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        target_month_year = target_dt.strftime("%B %Y")  # e.g., "April 2026"

        for _ in range(12):  # max 12 months forward
            try:
                month_label_el = await page.query_selector(CALENDAR_MONTH_LABEL)
                if month_label_el:
                    label_text = await month_label_el.inner_text()
                    if target_month_year.lower() in label_text.lower():
                        return
            except Exception:
                pass

            # Click next month
            try:
                next_btn = await page.query_selector(CALENDAR_NEXT_MONTH)
                if next_btn:
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

"""Polling loop with exponential backoff and safety shutoff."""

import asyncio
import logging
import random
from typing import Optional

from src.auth import AuthManager, CaptchaError
from src.booker import BookingError, ReservationBooker
from src.browser import BrowserManager
from src.config import AppConfig
from src.constants import (
    BACKOFF_BASE,
    BACKOFF_JITTER_SECONDS,
    BACKOFF_MAX_SECONDS,
    MAX_CONSECUTIVE_ERRORS,
)
from src.models import AvailabilityStatus, BookingTarget
from src.monitor import AvailabilityMonitor
from src.notifications import NotificationManager

logger = logging.getLogger(__name__)


class Scheduler:
    """Orchestrates the polling loop with error handling and backoff."""

    def __init__(
        self,
        config: AppConfig,
        browser_manager: BrowserManager,
        auth_manager: AuthManager,
        monitor: AvailabilityMonitor,
        booker: ReservationBooker,
        notifier: NotificationManager,
        target: BookingTarget,
    ):
        self.config = config
        self.browser = browser_manager
        self.auth = auth_manager
        self.monitor = monitor
        self.booker = booker
        self.notifier = notifier
        self.target = target

        self._consecutive_errors = 0
        self._running = False
        self._total_checks = 0

    async def run(self) -> None:
        """Main polling loop."""
        self._running = True
        logger.info("Starting scheduler in '%s' mode", self.config.mode)
        logger.info("Target: %s", self.target)
        logger.info("Poll interval: %ds", self.config.poll_interval_seconds)

        while self._running:
            try:
                await self._poll_cycle()
                self._consecutive_errors = 0
                await self._sleep(self.config.poll_interval_seconds)

            except CaptchaError:
                logger.warning("CAPTCHA detected - pausing for manual intervention")
                await self.notifier.notify_captcha()
                # Wait longer for manual CAPTCHA solving
                await self._sleep(120)

            except Exception as e:
                self._consecutive_errors += 1
                logger.error(
                    "Poll cycle error (%d/%d): %s",
                    self._consecutive_errors,
                    MAX_CONSECUTIVE_ERRORS,
                    e,
                )

                if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical("Safety shutoff: %d consecutive errors", self._consecutive_errors)
                    await self.notifier.notify_shutoff(self._consecutive_errors)
                    self.stop()
                    break

                # Exponential backoff
                backoff = self._calculate_backoff()
                logger.info("Backing off for %.0fs before retry", backoff)

                # Try browser restart on repeated errors
                if self._consecutive_errors >= 3:
                    logger.info("Attempting browser restart after %d errors", self._consecutive_errors)
                    try:
                        await self.browser.restart()
                    except Exception as restart_err:
                        logger.error("Browser restart failed: %s", restart_err)

                await self._sleep(backoff)

        logger.info("Scheduler stopped. Total checks: %d", self._total_checks)

    async def _poll_cycle(self) -> None:
        """Single availability check cycle."""
        self._total_checks += 1
        logger.info("Check #%d - looking for availability...", self._total_checks)

        # Proactive token refresh
        if self.auth.needs_token_refresh():
            logger.info("Token refresh needed, re-authenticating...")
            page = await self.browser.get_page()
            await self.auth.ensure_authenticated(page)

        results = await self.monitor.check_availability(self.target)

        available = [r for r in results if r.is_available]
        errors = [r for r in results if r.status == AvailabilityStatus.ERROR]

        if errors:
            for err in errors:
                logger.warning("Check error: %s", err.message)

        if available:
            logger.info("AVAILABILITY FOUND!")
            await self.notifier.notify_availability(self.target, results)

            if self.config.mode == "book":
                await self._attempt_booking()
            else:
                logger.info("Monitor mode - not auto-booking. Check notifications!")
        else:
            logger.info("No availability found for %s", self.target)

    async def _attempt_booking(self) -> None:
        """Attempt to book the reservation."""
        logger.info("Attempting to book reservation...")

        for attempt in range(1, self.config.max_retries + 1):
            try:
                confirmation = await self.booker.book(self.target)
                if confirmation:
                    logger.info("BOOKING SUCCESSFUL! Confirmation: %s", confirmation)
                    await self.notifier.notify_booking_success(self.target, confirmation)
                else:
                    logger.info("BOOKING COMPLETED (no confirmation number captured)")
                    await self.notifier.notify_booking_success(self.target)

                self.stop()
                return

            except BookingError as e:
                logger.warning("Booking attempt %d/%d failed: %s", attempt, self.config.max_retries, e)
                if attempt < self.config.max_retries:
                    await self._sleep(5)

        await self.notifier.notify_error(
            "Booking Failed",
            f"All {self.config.max_retries} booking attempts failed for {self.target}",
        )

    def _calculate_backoff(self) -> float:
        """Calculate exponential backoff with jitter."""
        backoff = min(
            BACKOFF_BASE ** self._consecutive_errors,
            BACKOFF_MAX_SECONDS,
        )
        jitter = random.uniform(0, BACKOFF_JITTER_SECONDS)
        return backoff + jitter

    async def _sleep(self, seconds: float) -> None:
        """Sleep for the given duration, checking for stop signal."""
        if not self._running:
            return
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            self._running = False

    def stop(self) -> None:
        """Signal the scheduler to stop after the current cycle."""
        logger.info("Scheduler stop requested")
        self._running = False

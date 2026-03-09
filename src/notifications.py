"""Notification channels: terminal output, macOS native notifications, Discord webhook."""

import json
import logging
from datetime import datetime
from typing import Optional

import aiohttp

from src.config import AppConfig
from src.constants import PARK_DISPLAY_NAMES
from src.models import AvailabilityResult, BookingTarget

logger = logging.getLogger(__name__)


class NotificationManager:
    """Sends notifications through configured channels."""

    def __init__(self, config: AppConfig):
        self.config = config

    async def notify_availability(self, target: BookingTarget, results: list[AvailabilityResult]) -> None:
        """Send notifications for availability found."""
        available_results = [r for r in results if r.is_available]
        if not available_results:
            return

        for result in available_results:
            park_name = PARK_DISPLAY_NAMES.get(result.park.value, result.park.value)
            title = "Disneyland Reservation Available!"
            message = (
                f"{park_name} has availability on {result.date} "
                f"for {target.party_size} guest(s)!"
            )

            self._notify_terminal(title, message)

            if self.config.enable_macos_notifications:
                await self._notify_macos(title, message)

            if self.config.enable_discord_notifications:
                await self._notify_discord(title, message, result)

    async def notify_booking_success(self, target: BookingTarget, confirmation: str = "") -> None:
        """Send notifications for a successful booking."""
        park_name = PARK_DISPLAY_NAMES.get(target.park.value, target.park.value)
        title = "Reservation Booked!"
        message = (
            f"Successfully booked {park_name} on {target.date} "
            f"for {target.party_size} guest(s)."
        )
        if confirmation:
            message += f" Confirmation: {confirmation}"

        self._notify_terminal(title, message)

        if self.config.enable_macos_notifications:
            await self._notify_macos(title, message)

        if self.config.enable_discord_notifications:
            await self._notify_discord_booking(title, message, target, confirmation)

    async def notify_error(self, title: str, message: str) -> None:
        """Send error notifications through all channels."""
        self._notify_terminal(f"ERROR: {title}", message)

        if self.config.enable_macos_notifications:
            await self._notify_macos(f"Error: {title}", message)

        if self.config.enable_discord_notifications:
            await self._notify_discord_error(title, message)

    async def notify_captcha(self) -> None:
        """Alert user that CAPTCHA needs manual solving."""
        title = "CAPTCHA Detected"
        message = "Manual intervention required - CAPTCHA blocking login. Open the browser window to solve it."

        self._notify_terminal(title, message)

        if self.config.enable_macos_notifications:
            await self._notify_macos(title, message)

        if self.config.enable_discord_notifications:
            await self._notify_discord_error(title, message)

    async def notify_shutoff(self, error_count: int) -> None:
        """Alert user that safety shutoff has been triggered."""
        title = "Safety Shutoff Activated"
        message = f"Monitoring stopped after {error_count} consecutive errors. Check logs and restart manually."

        self._notify_terminal(title, message)

        if self.config.enable_macos_notifications:
            await self._notify_macos(title, message)

        if self.config.enable_discord_notifications:
            await self._notify_discord_error(title, message)

    # -- Channel Implementations --

    def _notify_terminal(self, title: str, message: str) -> None:
        """Print notification to terminal with formatting."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        border = "=" * 60
        print(f"\n{border}")
        print(f"  {title}")
        print(f"  {message}")
        print(f"  [{timestamp}]")
        print(f"{border}\n")

    async def _notify_macos(self, title: str, message: str) -> None:
        """Send macOS native notification via osascript."""
        import asyncio

        try:
            script = (
                f'display notification "{self._escape_applescript(message)}" '
                f'with title "{self._escape_applescript(title)}" '
                f'sound name "Glass"'
            )
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            logger.warning("macOS notification failed: %s", e)

    def _escape_applescript(self, text: str) -> str:
        """Escape special characters for AppleScript strings."""
        return text.replace("\\", "\\\\").replace('"', '\\"')

    async def _notify_discord(
        self, title: str, message: str, result: AvailabilityResult
    ) -> None:
        """Send availability notification to Discord webhook."""
        park_name = PARK_DISPLAY_NAMES.get(result.park.value, result.park.value)
        embed = {
            "title": title,
            "description": message,
            "color": 0x00FF00,  # Green
            "fields": [
                {"name": "Park", "value": park_name, "inline": True},
                {"name": "Date", "value": result.date, "inline": True},
                {"name": "Source", "value": result.source, "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self._send_discord_webhook({"embeds": [embed]})

    async def _notify_discord_booking(
        self, title: str, message: str, target: BookingTarget, confirmation: str
    ) -> None:
        """Send booking success notification to Discord."""
        park_name = PARK_DISPLAY_NAMES.get(target.park.value, target.park.value)
        embed = {
            "title": title,
            "description": message,
            "color": 0x0099FF,  # Blue
            "fields": [
                {"name": "Park", "value": park_name, "inline": True},
                {"name": "Date", "value": target.date, "inline": True},
                {"name": "Party Size", "value": str(target.party_size), "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        if confirmation:
            embed["fields"].append({"name": "Confirmation", "value": confirmation, "inline": False})
        await self._send_discord_webhook({"embeds": [embed]})

    async def _notify_discord_error(self, title: str, message: str) -> None:
        """Send error notification to Discord."""
        embed = {
            "title": f"Error: {title}",
            "description": message,
            "color": 0xFF0000,  # Red
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self._send_discord_webhook({"embeds": [embed]})

    async def _send_discord_webhook(self, payload: dict) -> None:
        """Send a payload to the configured Discord webhook URL."""
        if not self.config.discord_webhook_url:
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.discord_webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status not in (200, 204):
                        logger.warning("Discord webhook returned %d", resp.status)
        except Exception as e:
            logger.warning("Discord notification failed: %s", e)

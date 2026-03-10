"""Load and validate configuration from environment variables."""

import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.models import Park


class DateRangeError(Exception):
    """Raised when TARGET_DATE is outside the valid reservation window."""

    def __init__(self, message: str, target_date: str, max_date: date | None = None):
        super().__init__(message)
        self.target_date = target_date
        self.max_date = max_date


@dataclass(frozen=True)
class AppConfig:
    disney_email: str
    disney_password: str
    target_date: str
    target_park: Park
    party_members: list[str]
    mode: str  # "monitor" or "book"

    @property
    def party_size(self) -> int:
        return len(self.party_members)
    poll_interval_seconds: int
    discord_webhook_url: str
    enable_macos_notifications: bool
    enable_discord_notifications: bool
    headless: bool
    browser_data_dir: str
    token_refresh_minutes: int
    max_retries: int
    log_level: str
    debug_images: bool


def load_config(env_path: str | None = None) -> AppConfig:
    """Load config from .env file and validate all required fields."""
    load_dotenv(env_path or ".env")

    errors: list[str] = []

    email = os.getenv("DISNEY_EMAIL", "")
    password = os.getenv("DISNEY_PASSWORD", "")
    if not email:
        errors.append("DISNEY_EMAIL is required")
    if not password:
        errors.append("DISNEY_PASSWORD is required")

    target_date = os.getenv("TARGET_DATE", "")
    if not target_date:
        errors.append("TARGET_DATE is required")
    else:
        try:
            parsed = datetime.strptime(target_date, "%Y-%m-%d").date()
            today = date.today()
            max_date = today + timedelta(days=90)
            if parsed < today:
                raise DateRangeError(
                    f"Your target date ({target_date}) is in the past.",
                    target_date=target_date,
                )
            elif parsed > max_date:
                raise DateRangeError(
                    f"Your target date ({target_date}) is more than 90 days away. "
                    f"Disneyland only allows reservations up to 90 days in advance.",
                    target_date=target_date,
                    max_date=max_date,
                )
        except DateRangeError:
            raise
        except ValueError:
            errors.append(f"TARGET_DATE must be YYYY-MM-DD format, got: {target_date}")

    park_str = os.getenv("TARGET_PARK", "disneyland")
    try:
        target_park = Park.from_str(park_str)
    except ValueError:
        errors.append(f"TARGET_PARK must be one of: disneyland, california_adventure, either. Got: {park_str}")
        target_park = Park.DISNEYLAND

    party_members_str = os.getenv("PARTY_MEMBERS", "")
    party_members = [name.strip() for name in party_members_str.split(",") if name.strip()]
    if not party_members:
        errors.append("PARTY_MEMBERS is required (comma-separated names)")

    mode = os.getenv("MODE", "monitor").lower()
    if mode not in ("monitor", "book"):
        errors.append(f"MODE must be 'monitor' or 'book', got: {mode}")

    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    if poll_interval < 15:
        errors.append("POLL_INTERVAL_SECONDS must be at least 15")

    discord_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    enable_macos = os.getenv("ENABLE_MACOS_NOTIFICATIONS", "true").lower() == "true"
    enable_discord = os.getenv("ENABLE_DISCORD_NOTIFICATIONS", "false").lower() == "true"

    if enable_discord and not discord_url:
        errors.append("DISCORD_WEBHOOK_URL is required when ENABLE_DISCORD_NOTIFICATIONS=true")

    headless = os.getenv("HEADLESS", "true").lower() == "true"
    browser_data_dir = os.getenv("BROWSER_DATA_DIR", "browser_data")
    token_refresh = int(os.getenv("TOKEN_REFRESH_MINUTES", "12"))
    max_retries = int(os.getenv("MAX_RETRIES", "3"))
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    debug_images = os.getenv("DEBUG_IMAGES", "false").lower() == "true"

    if errors:
        print("Configuration errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # Ensure browser data dir exists
    Path(browser_data_dir).mkdir(parents=True, exist_ok=True)

    return AppConfig(
        disney_email=email,
        disney_password=password,
        target_date=target_date,
        target_park=target_park,
        party_members=party_members,
        mode=mode,
        poll_interval_seconds=poll_interval,
        discord_webhook_url=discord_url,
        enable_macos_notifications=enable_macos,
        enable_discord_notifications=enable_discord,
        headless=headless,
        browser_data_dir=browser_data_dir,
        token_refresh_minutes=token_refresh,
        max_retries=max_retries,
        log_level=log_level,
        debug_images=debug_images,
    )


def setup_logging(level: str) -> None:
    """Configure logging with consistent format."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

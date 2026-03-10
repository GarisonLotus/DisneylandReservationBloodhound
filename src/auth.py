"""Login flow, auth token capture via network intercepts, and token caching."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, Response

from src.browser import BrowserManager
from src.config import AppConfig
from src.constants import DISNEY_LOGIN_URL, DISNEY_RESERVATIONS_URL, LOGIN_TIMEOUT_MS
from src.models import TokenInfo
from src.selectors import (
    CAPTCHA_INDICATORS,
    LOGGED_IN_INDICATORS,
    LOGIN_EMAIL_CONTINUE_BUTTON,
    LOGIN_EMAIL_INPUT,
    LOGIN_ERROR,
    LOGIN_IFRAME,
    LOGIN_PASSWORD_INPUT,
    LOGIN_SUBMIT_BUTTON,
)

logger = logging.getLogger(__name__)

TOKEN_CACHE_FILE = ".token_cache.json"


class AuthError(Exception):
    """Raised for authentication failures."""


class CaptchaError(Exception):
    """Raised when CAPTCHA is detected and manual intervention is needed."""


class AuthManager:
    """Handles Disney login and auth token lifecycle."""

    def __init__(self, config: AppConfig, browser_manager: BrowserManager):
        self.config = config
        self.browser = browser_manager
        self._token: Optional[TokenInfo] = None
        self._load_cached_token()

    @property
    def token(self) -> Optional[TokenInfo]:
        if self._token and not self._token.is_expired:
            return self._token
        return None

    def _load_cached_token(self) -> None:
        """Load token from disk cache if available and not expired."""
        cache_path = Path(TOKEN_CACHE_FILE)
        if not cache_path.exists():
            return
        try:
            data = json.loads(cache_path.read_text())
            token = TokenInfo(
                access_token=data["access_token"],
                captured_at=datetime.fromisoformat(data["captured_at"]),
                expires_in_seconds=data.get("expires_in_seconds", 900),
            )
            if not token.is_expired:
                self._token = token
                logger.info("Loaded cached token (age: %.1f min)", token.age_minutes())
            else:
                logger.debug("Cached token is expired, discarding")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug("Could not load token cache: %s", e)

    def _save_token_cache(self) -> None:
        """Persist token to disk for cross-restart reuse."""
        if not self._token:
            return
        data = {
            "access_token": self._token.access_token,
            "captured_at": self._token.captured_at.isoformat(),
            "expires_in_seconds": self._token.expires_in_seconds,
        }
        Path(TOKEN_CACHE_FILE).write_text(json.dumps(data))
        logger.debug("Token cached to disk")

    def clear_token(self) -> None:
        """Clear cached token (e.g., on 401)."""
        self._token = None
        cache_path = Path(TOKEN_CACHE_FILE)
        if cache_path.exists():
            cache_path.unlink()
        logger.info("Token cleared")

    async def _capture_token_from_response(self, response: Response) -> None:
        """Network intercept handler: capture auth tokens from API responses."""
        url = response.url
        if "token" not in url and "auth" not in url and "login" not in url:
            return

        try:
            if response.status == 200:
                body = await response.json()
                access_token = body.get("access_token") or body.get("data", {}).get("token", {}).get("access_token")
                if access_token:
                    expires_in = body.get("expires_in", 900)
                    self._token = TokenInfo(
                        access_token=access_token,
                        expires_in_seconds=int(expires_in),
                    )
                    self._save_token_cache()
                    logger.info("Captured auth token (expires in %ds)", expires_in)
        except Exception as e:
            logger.debug("Could not parse token from %s: %s", url, e)

    async def _check_already_logged_in(self, page: Page) -> bool:
        """Check if any logged-in indicator is present on the page."""
        for selector in LOGGED_IN_INDICATORS:
            try:
                el = await page.query_selector(selector)
                if el:
                    logger.info("Already logged in (found %s)", selector)
                    return True
            except Exception:
                continue
        return False

    async def _detect_captcha(self, page: Page) -> bool:
        """Check if CAPTCHA is blocking the login."""
        for selector in CAPTCHA_INDICATORS:
            try:
                el = await page.query_selector(selector)
                if el:
                    return True
            except Exception:
                continue
        return False

    async def authenticate(self, page: Page) -> None:
        """Perform full login flow via browser automation."""
        logger.info("Starting authentication flow...")

        # Set up network intercept for token capture
        page.on("response", self._capture_token_from_response)

        await page.goto(DISNEY_LOGIN_URL, wait_until="domcontentloaded")

        # Check if already logged in (persistent context may have valid session)
        if await self._check_already_logged_in(page):
            return

        # Disney login may be inside an iframe (OneID)
        login_frame = page
        try:
            iframe_el = await page.wait_for_selector(LOGIN_IFRAME, timeout=5000)
            if iframe_el:
                frame = await iframe_el.content_frame()
                if frame:
                    login_frame = frame
                    logger.debug("Switched to login iframe")
        except Exception:
            logger.debug("No login iframe found, using main page")

        # Step 1: Enter email
        await login_frame.wait_for_selector(LOGIN_EMAIL_INPUT, timeout=LOGIN_TIMEOUT_MS)
        await login_frame.fill(LOGIN_EMAIL_INPUT, self.config.disney_email)

        # Step 2: Click continue to advance to password step
        await login_frame.click(LOGIN_EMAIL_CONTINUE_BUTTON)

        # Step 3: Wait for password field and fill it
        await login_frame.wait_for_selector(LOGIN_PASSWORD_INPUT, timeout=LOGIN_TIMEOUT_MS)
        await login_frame.fill(LOGIN_PASSWORD_INPUT, self.config.disney_password)

        # Check for CAPTCHA before submitting
        if await self._detect_captcha(page):
            raise CaptchaError(
                "CAPTCHA detected on login page. Please solve it manually in the browser window."
            )

        # Submit
        await login_frame.click(LOGIN_SUBMIT_BUTTON)

        # Wait for navigation to complete (login redirect)
        try:
            await page.wait_for_url("**/entry-reservation/**", timeout=LOGIN_TIMEOUT_MS)
            logger.info("Login successful - redirected to reservations page")
        except Exception:
            # Check for error messages
            try:
                error_el = await page.query_selector(LOGIN_ERROR)
                if error_el:
                    error_text = await error_el.inner_text()
                    raise AuthError(f"Login failed: {error_text}")
            except AuthError:
                raise
            except Exception:
                pass

            # Check CAPTCHA again
            if await self._detect_captcha(page):
                raise CaptchaError("CAPTCHA appeared after login attempt.")

            # Check if we landed on a logged-in page anyway
            if await self._check_already_logged_in(page):
                logger.info("Login successful (detected via indicator)")
                return

            raise AuthError("Login failed: did not redirect to reservations page")

    async def ensure_authenticated(self, page: Page) -> None:
        """Ensure we have a valid session, re-authenticating if needed."""
        # If we have a valid token and it's not too old, skip re-auth
        if self._token and not self._token.is_expired:
            token_age = self._token.age_minutes()
            if token_age < self.config.token_refresh_minutes:
                logger.debug("Token still valid (age: %.1f min)", token_age)
                return

        # Try navigating to reservation page to check session
        current_url = page.url
        if "entry-reservation" not in current_url:
            await page.goto(DISNEY_RESERVATIONS_URL, wait_until="domcontentloaded")

        if await self._check_already_logged_in(page):
            return

        # Need to re-authenticate
        logger.info("Session expired, re-authenticating...")
        await self.authenticate(page)

    def needs_token_refresh(self) -> bool:
        """Check if the token should be proactively refreshed."""
        if not self._token:
            return True
        return self._token.age_minutes() >= self.config.token_refresh_minutes

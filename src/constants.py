"""URLs, park identifiers, timeouts, and other constants."""

# Base URLs
DISNEY_LOGIN_URL = "https://disneyland.disney.go.com/login"
DISNEY_RESERVATIONS_URL = "https://disneyland.disney.go.com/entry-reservation/"
DISNEY_CALENDAR_API_URL = "https://disneyland.disney.go.com/availability-calendar/api/calendar"  # DISCOVERY: confirm exact endpoint path

# Park identifiers used in the reservation system
# DISCOVERY: confirm these IDs from network traffic on the reservation page
PARK_IDS = {
    "disneyland": "330339",
    "california_adventure": "336894",
}

PARK_DISPLAY_NAMES = {
    "disneyland": "Disneyland Park",
    "california_adventure": "Disney California Adventure",
}

# Timeouts (milliseconds for Playwright)
PAGE_LOAD_TIMEOUT_MS = 30_000
ELEMENT_WAIT_TIMEOUT_MS = 10_000
NAVIGATION_TIMEOUT_MS = 45_000
LOGIN_TIMEOUT_MS = 60_000

# Polling defaults
DEFAULT_POLL_INTERVAL = 60  # seconds
MIN_POLL_INTERVAL = 15      # seconds
MAX_POLL_INTERVAL = 600     # seconds

# Backoff settings
BACKOFF_BASE = 2.0
BACKOFF_MAX_SECONDS = 300
BACKOFF_JITTER_SECONDS = 5

# Safety
MAX_CONSECUTIVE_ERRORS = 10  # shutoff threshold
SCREENSHOT_DIR = "screenshots"

# Browser
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1280, "height": 800}

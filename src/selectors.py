"""Centralized CSS/XPath selectors for all Disney reservation pages.

Selectors marked with DISCOVERY need confirmation against the live site.
Update these values after inspecting the actual DOM structure.
"""

# =============================================================================
# Login Page
# =============================================================================

LOGIN_EMAIL_INPUT = 'input[type="email"]'  # DISCOVERY: confirm selector
LOGIN_EMAIL_CONTINUE_BUTTON = 'button[type="submit"]'  # DISCOVERY: "Continue" after email entry
LOGIN_PASSWORD_INPUT = 'input[type="password"]'  # DISCOVERY: confirm selector
LOGIN_SUBMIT_BUTTON = 'button[type="submit"]'  # DISCOVERY: confirm selector
LOGIN_IFRAME = 'iframe[id*="oneid"]'  # DISCOVERY: Disney uses OneID iframe for login

# Button to start a new reservation from the existing reservations list page
BOOK_RESERVATION_BUTTON = 'text=Book Theme Park Reservation'

# Post-login indicators (any of these means we're logged in)
LOGGED_IN_INDICATORS = [
    '[data-testid="user-greeting"]',  # DISCOVERY
    'a[href*="profile"]',  # DISCOVERY
    'button[aria-label*="Account"]',  # DISCOVERY
]

# CAPTCHA detection
CAPTCHA_INDICATORS = [
    'iframe[src*="captcha"]',
    'iframe[src*="recaptcha"]',
    '[class*="captcha"]',
    '#captcha',
]

# Login error messages
LOGIN_ERROR = '[data-testid="error-message"], .error-message, [role="alert"]'  # DISCOVERY

# =============================================================================
# Reservation Entry Page (Party Selection - Page 1)
# =============================================================================

# Party member checkboxes - each member row has a checkbox and the member's name
PARTY_MEMBER_CHECKBOX = 'input[type="checkbox"]'
# Label/text containing member name, used to find the right checkbox
PARTY_MEMBER_LABEL = 'label'
PARTY_NEXT_BUTTON = 'text=Next'

# =============================================================================
# Calendar / Park Selection (Page 2)
# =============================================================================

# Calendar navigation — uses <com-calendar-button> custom elements
CALENDAR_CONTAINER = '#calendarWrapper'  # CONFIRMED
CALENDAR_MONTH_LABEL = '.month[aria-label]'  # CONFIRMED: div.month with aria-label="March 2026"
CALENDAR_NEXT_MONTH = 'com-calendar-button#nextArrow'  # CONFIRMED
CALENDAR_PREV_MONTH = 'com-calendar-button#prevArrow'  # CONFIRMED

# Day cells — <com-calendar-date> custom web components with Shadow DOM
# Each date has TWO copies: a shadow DOM base (class="noInfo") and a
# slotted interactive version (has slot="YYYY-MM-DD") with real availability.
# Use slot= attribute to find the interactive version.
# Class values: "all" (either park), "blocked" (blocked out), "noInfo" (no data)
# aria-label contains: "Either Park Available", "Blocked Out", park-specific text
CALENDAR_DAY_BY_DATE = 'com-calendar-date[slot="{date}"]'  # CONFIRMED: {date} = YYYY-MM-DD
CALENDAR_DAY_AVAILABLE = 'com-calendar-date[aria-disabled="false"]'  # CONFIRMED
CALENDAR_DAY_UNAVAILABLE = 'com-calendar-date.blocked'  # CONFIRMED

# Park selection on calendar page — DISCOVERY (not yet confirmed)
PARK_FILTER_DISNEYLAND = '[data-testid="park-filter-disneyland"]'  # DISCOVERY
PARK_FILTER_DCA = '[data-testid="park-filter-dca"]'  # DISCOVERY

# =============================================================================
# Review Page (Page 3) — UNUSED: handled inline in booker.py
# =============================================================================
# The review page ("Confirm Your Selections") uses:
#   - Heading: text=/Confirm Your Selections/i
#   - T&C checkbox: <com-checkbox> found via text=/I have read and agree/i
#     -> walk up to com-checkbox -> click shadow .check indicator
#   - Confirm button: get_by_role("button", name="Confirm")
#     -> must filter out OneTrust "Confirm My Choices" (class onetrust-close-btn-handler)
# These DISCOVERY selectors below are NOT used by booker.py:
REVIEW_SUMMARY = '[data-testid="reservation-summary"]'  # UNUSED
REVIEW_DATE_DISPLAY = '[data-testid="reservation-date"]'  # UNUSED
REVIEW_PARK_DISPLAY = '[data-testid="reservation-park"]'  # UNUSED
REVIEW_PARTY_DISPLAY = '[data-testid="reservation-party"]'  # UNUSED
REVIEW_CONFIRM_BUTTON = 'button:has-text("Confirm"), button:has-text("Book")'  # UNUSED
REVIEW_ACKNOWLEDGE_CHECKBOX = 'input[type="checkbox"][name*="acknowledge"]'  # UNUSED

# =============================================================================
# Confirmation Page (Page 4) — UNUSED: handled inline in booker.py
# =============================================================================
# The confirmation page shows "Your Theme Park Reservation is Confirmed!"
# Confirmation number is pure digits (e.g., 07729126388729600)
# Extracted via regex: confirmation\s+number[:\s]*?(\d{8,})
# These DISCOVERY selectors below are NOT used by booker.py:
CONFIRMATION_SUCCESS = '[data-testid="confirmation-success"]'  # UNUSED
CONFIRMATION_NUMBER = '[data-testid="confirmation-number"]'  # UNUSED
CONFIRMATION_DETAILS = '[data-testid="confirmation-details"]'  # UNUSED

# =============================================================================
# Generic / Shared
# =============================================================================

LOADING_SPINNER = '[data-testid="loading"], .spinner, [aria-busy="true"]'
ERROR_BANNER = '[role="alert"], .error-banner, [data-testid="error"]'
MODAL_CLOSE = 'button[aria-label="Close"], button:has-text("Close")'

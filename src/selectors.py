"""Centralized CSS/XPath selectors for all Disney reservation pages.

Selectors marked with DISCOVERY need confirmation against the live site.
Update these values after inspecting the actual DOM structure.
"""

# =============================================================================
# Login Page
# =============================================================================

LOGIN_EMAIL_INPUT = 'input[type="email"]'  # DISCOVERY: confirm selector
LOGIN_PASSWORD_INPUT = 'input[type="password"]'  # DISCOVERY: confirm selector
LOGIN_SUBMIT_BUTTON = 'button[type="submit"]'  # DISCOVERY: confirm selector
LOGIN_IFRAME = 'iframe[id*="oneid"]'  # DISCOVERY: Disney uses OneID iframe for login

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

# Party member checkboxes/selectors
PARTY_MEMBER_CHECKBOX = 'input[type="checkbox"][name*="guest"]'  # DISCOVERY
PARTY_MEMBER_ROW = '[data-testid="guest-row"]'  # DISCOVERY
PARTY_SELECT_ALL = '[data-testid="select-all"]'  # DISCOVERY
PARTY_CONTINUE_BUTTON = 'button:has-text("Continue"), button:has-text("Next")'  # DISCOVERY

# =============================================================================
# Calendar / Park Selection (Page 2)
# =============================================================================

# Calendar navigation
CALENDAR_CONTAINER = '[data-testid="availability-calendar"]'  # DISCOVERY
CALENDAR_MONTH_LABEL = '.calendar-month-label, [data-testid="month-label"]'  # DISCOVERY
CALENDAR_NEXT_MONTH = 'button[aria-label*="next"], button:has-text(">")'  # DISCOVERY
CALENDAR_PREV_MONTH = 'button[aria-label*="prev"], button:has-text("<")'  # DISCOVERY

# Day cells - availability states
# DISCOVERY: these selectors depend on how Disney marks available vs blocked dates
CALENDAR_DAY_AVAILABLE = '[data-testid="calendar-day"][data-available="true"]'  # DISCOVERY
CALENDAR_DAY_UNAVAILABLE = '[data-testid="calendar-day"][data-available="false"]'  # DISCOVERY
CALENDAR_DAY_SELECTED = '[data-testid="calendar-day"][aria-selected="true"]'  # DISCOVERY

# To click a specific date, format with the day number
CALENDAR_DAY_BY_DATE = 'button[data-date="{date}"]'  # DISCOVERY: {date} = YYYY-MM-DD

# Park selection on calendar page
PARK_FILTER_DISNEYLAND = '[data-testid="park-filter-disneyland"]'  # DISCOVERY
PARK_FILTER_DCA = '[data-testid="park-filter-dca"]'  # DISCOVERY

# =============================================================================
# Review Page (Page 3)
# =============================================================================

REVIEW_SUMMARY = '[data-testid="reservation-summary"]'  # DISCOVERY
REVIEW_DATE_DISPLAY = '[data-testid="reservation-date"]'  # DISCOVERY
REVIEW_PARK_DISPLAY = '[data-testid="reservation-park"]'  # DISCOVERY
REVIEW_PARTY_DISPLAY = '[data-testid="reservation-party"]'  # DISCOVERY
REVIEW_CONFIRM_BUTTON = 'button:has-text("Confirm"), button:has-text("Book")'  # DISCOVERY
REVIEW_ACKNOWLEDGE_CHECKBOX = 'input[type="checkbox"][name*="acknowledge"]'  # DISCOVERY

# =============================================================================
# Confirmation Page (Page 4)
# =============================================================================

CONFIRMATION_SUCCESS = '[data-testid="confirmation-success"]'  # DISCOVERY
CONFIRMATION_NUMBER = '[data-testid="confirmation-number"]'  # DISCOVERY
CONFIRMATION_DETAILS = '[data-testid="confirmation-details"]'  # DISCOVERY

# =============================================================================
# Generic / Shared
# =============================================================================

LOADING_SPINNER = '[data-testid="loading"], .spinner, [aria-busy="true"]'
ERROR_BANNER = '[role="alert"], .error-banner, [data-testid="error"]'
MODAL_CLOSE = 'button[aria-label="Close"], button:has-text("Close")'

# Disneyland Reservation Bloodhound

## Project Overview
Python automation tool using Playwright to monitor and auto-book Disneyland Resort theme park reservations. Two modes: monitor (notify only) and book (auto-reserve).

## Architecture
- **Entry point**: `main.py` (argparse CLI, async wiring, signal handling)
- **Source**: `src/` package with focused modules
- **Config**: `.env` file loaded via `python-dotenv` into frozen `AppConfig` dataclass
- **Browser**: Playwright persistent context (`launch_persistent_context`) for session persistence
- **Auth**: Login via browser automation, token captured from network intercepts, cached to disk
- **Monitoring**: API-first checks (fast), browser fallback with calendar refresh optimization
- **Booking**: 4-page flow automated with screenshots at each step
- **Notifications**: Terminal + macOS osascript + Discord webhooks

## Key Files
| File | Purpose |
|---|---|
| `src/models.py` | Data classes: Park, AvailabilityResult, TokenInfo, BookingTarget |
| `src/constants.py` | URLs, park IDs, timeouts |
| `src/selectors.py` | CSS selectors (calendar CONFIRMED; review/confirm handled inline in booker.py) |
| `src/config.py` | Env loading, validation, AppConfig |
| `src/browser.py` | BrowserManager - Playwright lifecycle |
| `src/auth.py` | AuthManager - login, token capture/cache |
| `src/monitor.py` | AvailabilityMonitor - API + browser checking, `_navigate_to_month()` |
| `src/notifications.py` | NotificationManager - terminal/macOS/Discord |
| `src/booker.py` | ReservationBooker - full 4-page booking flow (CONFIRMED WORKING) |
| `src/scheduler.py` | Scheduler - polling loop, backoff, error handling |

## Commands
```bash
# Run (after setup)
python main.py
python main.py --mode book --headless false

# Install dependencies
pip install -r requirements.txt
playwright install chromium
```

## Key Patterns
- **Frozen dataclass config**: Immutable after load, CLI overrides create new instance
- **Token lifecycle**: Captured from network -> cached to `.token_cache.json` -> TTL-based refresh
- **Error escalation**: Retry -> backoff -> browser restart -> safety shutoff (10 errors)
- **Screenshots**: Timestamped PNGs in `screenshots/` directory for booking audit trail
- **walkShadow()**: JS pattern to traverse all shadow roots when vanilla DOM queries fail
- **OneTrust avoidance**: Cookie consent buttons have class `onetrust-close-btn-handler` — always filter

## Disney Site Shadow DOM (Critical)
Disney's booking pages use custom web components with Shadow DOM extensively:
- `<com-checkbox>`, `<com-button>`, `<com-calendar-date>`, `<com-calendar-button>`, etc.
- `document.querySelectorAll()` / `page.query_selector()` do NOT pierce Shadow DOM — returns empty
- `page.inner_text()` / `page.inner_text("body")` returns only footer text (main content is in Shadow DOM)
- Playwright's locator engine (`text=`, CSS selectors, `get_by_role`) DOES pierce Shadow DOM automatically
- To interact via JS: use Playwright locator to find element -> `element_handle()` -> `handle.evaluate()` to walk DOM
- `<com-checkbox>` has `disable-checkbox-label-click` attr — must click the `.check` indicator inside shadow root
- Working checkbox click: find text via `text=/Name/i` -> walk up to `com-checkbox` -> `shadowRoot.querySelector('.check').click()` (returns 'indicator')
- Names displayed in ALL CAPS via CSS — always use case-insensitive regex `text=/Name/i`

## Disney Login Flow
- Two-step: email -> click Continue (`button[type="submit"]`) -> password field appears -> submit
- Login may be inside OneID iframe (`iframe[id*="oneid"]`)
- Must use `channel="chrome"` (system Chrome) — Playwright's bundled Chromium gets blocked by TLS fingerprint
- `--disable-http2` and `--disable-quic` flags required (Disney's HTTP/2 is broken server-side)
- Always use `wait_until="domcontentloaded"` not `"networkidle"` (Disney's heavy site causes timeouts)

## Disney Reservation Flow (All Steps Confirmed - Successfully Tested)
1. `/entry-reservation/` — existing reservations list
2. Click `text=Book Theme Park Reservation` with `expect_navigation`
3. `/entry-reservation/add/select-party/` — party selection
   - `<com-checkbox>` elements for each party member
   - Click via JS: find name text -> walk up to `com-checkbox` -> click shadow `.check` indicator
   - Click "Next" button to proceed (multi-selector: `button:visible`, `com-button`, `[role="button"]`)
4. `/entry-reservation/add/select-date/` — calendar with availability dots
   - Use `_navigate_to_month()` with Playwright locators (NOT `query_selector`) to find target month
   - Click `com-calendar-date[slot="YYYY-MM-DD"]` for the target date
   - "Select a Park" section appears below with park cards + "Select" links
   - Use JS `walkShadow` to find "Select" links, match by parent card containing park name
   - Park cards: "Disney California Adventure Park" (left), "Disneyland Park" (right)
   - Click "Next" to proceed to review
5. `/entry-reservation/add/review/` — "Confirm Your Selections" review page
   - Shows: Reservation Date, Selected Park, Your Party, Email, Terms & Conditions
   - T&C checkbox: `<com-checkbox>` — find via `text=/I have read and agree/i`, walk up, click shadow `.check`
   - "Confirm" button: use `get_by_role("button", name="Confirm")` with OneTrust filter
   - MUST avoid hidden OneTrust `onetrust-close-btn-handler` "Confirm My Choices" button
6. Confirmation page — "Your Theme Park Reservation is Confirmed!"
   - Confirmation Number: pure-digit string (e.g., `07729126388729600`)
   - Extract with regex `confirmation\s+number[:\s]*?(\d{8,})`

## Monitoring Flow (Confirmed)
- **First check**: Full navigation — auth -> `/entry-reservation/` -> click "Book Theme Park Reservation" -> select party -> Next -> calendar page
- **Subsequent checks**: Stay on calendar page, click "Refresh Calendar" link to reload availability data in place
- Tracks `_on_calendar_page` flag; resets to full navigation on any error
- Logs "Availability as of M/D/YYYY, H:MM:SS PM" timestamp from the calendar page
- "Refresh Calendar" link found via `text=/Refresh Calendar/i` Playwright locator

## Calendar DOM Structure (Confirmed)
- `<com-calendar-date>` custom web component with Shadow DOM
- Each date appears TWICE: a shadow DOM base (`class="noInfo"`, always disabled) and a slotted interactive version (`slot="YYYY-MM-DD"`)
- Use `com-calendar-date[slot="YYYY-MM-DD"]` to find the real interactive element
- Class values on slotted element:
  - `"all"` = Either Park Available
  - `"blocked"` = Blocked Out (also has `disabled`, `unavailable` attrs)
  - `"noInfo"` = No data (shadow DOM base copy, ignore)
- `aria-label` contains human-readable text: "Either Park Available", "Blocked Out", etc.
- `aria-disabled="false"` = clickable/available, `"true"` = not clickable
- Inner structure: `<div class="number">11<svg class="slash">...</svg></div>` + `<div id="slotWrapper">` (availability dots)
- Calendar container: `#calendarWrapper` -> `div.comCalendar` -> `com-calendar-base#calendar0` (first month)
- Navigation: `com-calendar-button#prevArrow` / `com-calendar-button#nextArrow`
- Month label: `div.month[aria-label="March 2026"]` with `id="monthContainer-0"`
- Two months displayed side by side: `calendar0` (current) and `calendar1` (next)

## Browser Config
- System Chrome via `channel="chrome"` — required to avoid bot detection
- Chrome flags: `--disable-blink-features=AutomationControlled`, `--disable-dev-shm-usage`, `--disable-http2`, `--disable-quic`
- User agent: Chrome 133 on macOS
- Persistent context at `browser_data/` — delete this dir to fix corrupted session state

## Bugs Fixed (Reference)
- `_navigate_to_month()`: was using `page.query_selector()` (doesn't pierce Shadow DOM) — fixed to use `page.locator()`
- Park card selection: `text=/Disneyland Park/i` matched calendar legend text — fixed with JS walkShadow to find "Select" links
- T&C checkbox: DISCOVERY selector with `query_selector` — fixed with same Shadow DOM click pattern as party selection
- Confirm button: `button:has-text("Confirm")` matched hidden OneTrust cookie button — fixed with `get_by_role` + visibility/class filters
- Confirmation number: regex captured "Number" or "Details" instead of digits — fixed with `\d{8,}` pattern

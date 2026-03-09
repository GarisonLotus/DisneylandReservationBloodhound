# Disneyland Reservation Bloodhound

## Project Overview
Python automation tool using Playwright to monitor and auto-book Disneyland Resort theme park reservations. Two modes: monitor (notify only) and book (auto-reserve).

## Architecture
- **Entry point**: `main.py` (argparse CLI, async wiring, signal handling)
- **Source**: `src/` package with focused modules
- **Config**: `.env` file loaded via `python-dotenv` into frozen `AppConfig` dataclass
- **Browser**: Playwright persistent context (`launch_persistent_context`) for session persistence
- **Auth**: Login via browser automation, token captured from network intercepts, cached to disk
- **Monitoring**: API-first availability checks (fast), browser fallback (reliable)
- **Booking**: 4-page flow automated with screenshots at each step
- **Notifications**: Terminal + macOS osascript + Discord webhooks

## Key Files
| File | Purpose |
|---|---|
| `src/models.py` | Data classes: Park, AvailabilityResult, TokenInfo, BookingTarget |
| `src/constants.py` | URLs, park IDs, timeouts |
| `src/selectors.py` | All CSS/XPath selectors (DISCOVERY markers for unconfirmed ones) |
| `src/config.py` | Env loading, validation, AppConfig |
| `src/browser.py` | BrowserManager - Playwright lifecycle |
| `src/auth.py` | AuthManager - login, token capture/cache |
| `src/monitor.py` | AvailabilityMonitor - API + browser checking |
| `src/notifications.py` | NotificationManager - terminal/macOS/Discord |
| `src/booker.py` | ReservationBooker - 4-page booking flow |
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
- **DISCOVERY markers**: Selectors/endpoints that need live-site confirmation
- **Frozen dataclass config**: Immutable after load, CLI overrides create new instance
- **Token lifecycle**: Captured from network → cached to `.token_cache.json` → TTL-based refresh
- **Error escalation**: Retry → backoff → browser restart → safety shutoff (10 errors)
- **Screenshots**: Timestamped PNGs in `screenshots/` directory for booking audit trail

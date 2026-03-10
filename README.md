# Disneyland Reservation Bloodhound

A Python automation tool that monitors the Disneyland Resort reservation system for target date availability and automatically books reservations when slots open up.

## Features

- **Two modes**: Monitor (notifications only) or Book (auto-reserve)
- **API-first checking**: Uses captured auth tokens for lightweight availability checks, falls back to browser scraping
- **Persistent browser sessions**: Preserves cookies/localStorage across restarts to minimize re-logins
- **Multi-channel notifications**: Terminal, macOS native (osascript), Discord webhooks
- **Resilient polling**: Exponential backoff, browser restart on errors, safety shutoff after 10 consecutive failures
- **Debug screenshots**: Optional screenshots at each booking step (`DEBUG_IMAGES=true`)

## Installation

```bash
# Clone and enter the project
cd DisneylandReservationBloodhound

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (uses system Chrome, but Chromium is needed as fallback)
playwright install chromium
```

### Prerequisites

- **Python 3.11+**
- **Google Chrome** installed at the default location (`/Applications/Google Chrome.app` on macOS). Playwright uses your system Chrome via `channel="chrome"` to avoid bot detection by Disney's servers.

## Configuration

```bash
# Copy the example config
cp .env.example .env

# Edit with your credentials and preferences
```

### Required Settings

| Variable | Description |
|---|---|
| `DISNEY_EMAIL` | Your Disney account email |
| `DISNEY_PASSWORD` | Your Disney account password |
| `TARGET_DATE` | Date to monitor (YYYY-MM-DD) |
| `PARTY_MEMBERS` | Comma-separated names (e.g., `Mickey Mouse,Minnie Mouse`) |

### Optional Settings

| Variable | Default | Description |
|---|---|---|
| `TARGET_PARK` | `disneyland` | `disneyland`, `california_adventure`, or `either` |
| `MODE` | `monitor` | `monitor` (notify) or `book` (auto-book) |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between checks |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook for notifications |
| `ENABLE_MACOS_NOTIFICATIONS` | `true` | macOS native notifications |
| `ENABLE_DISCORD_NOTIFICATIONS` | `false` | Send to Discord |
| `HEADLESS` | `true` | Run browser headless |
| `TOKEN_REFRESH_MINUTES` | `12` | Auth token refresh interval |
| `MAX_RETRIES` | `3` | Booking retry attempts |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `DEBUG_IMAGES` | `false` | Save screenshots to `screenshots/` directory |

## Usage

```bash
# Monitor mode (default) - notifies when availability found
python main.py

# Book mode - auto-books when availability found
python main.py --mode book

# Override settings via CLI
python main.py --date 2026-04-15 --park disneyland

# Visible browser for debugging
python main.py --headless false

# Custom poll interval
python main.py --interval 30
```

## How It Works

1. **Authentication**: Logs into Disney's site using Playwright with system Chrome, captures auth tokens via network intercepts
2. **Monitoring**: First check navigates the full booking flow to the calendar page. Subsequent checks click "Refresh Calendar" to reload availability data in place (much faster). Falls back to full navigation on errors.
3. **Notification**: Sends alerts through terminal, macOS, and/or Discord when availability found
4. **Booking** (book mode): Navigates the full reservation flow automatically:
   - Select party members (Shadow DOM checkbox interaction)
   - Choose date on calendar, select park from park cards
   - Accept Terms & Conditions on review page
   - Click Confirm and capture confirmation number

## Troubleshooting

### `ERR_HTTP2_PROTOCOL_ERROR` or navigation timeouts

Disney's servers intermittently break HTTP/2 connections. The browser is configured with `--disable-http2` to force HTTP/1.1. If you still see timeouts:

1. **Delete `browser_data/`** to clear corrupted session state:
   ```bash
   rm -rf browser_data/
   ```
2. **Run in visible mode** to confirm login works:
   ```bash
   python main.py --headless false
   ```
3. **Ensure Google Chrome is installed** — Playwright's bundled Chromium has a detectable TLS fingerprint that Disney may block.

## Selector Status

The full booking flow has been confirmed working end-to-end. Calendar selectors use CSS selectors in `src/selectors.py`. Review page and confirmation page interactions are handled inline in `src/booker.py` using Playwright locators and JS shadow DOM traversal (the DISCOVERY selectors in `selectors.py` for these pages are unused). Login selectors in `selectors.py` are still marked DISCOVERY but login is handled directly in `src/auth.py`.

## Disclaimer

This tool is for personal use only. Use responsibly and in accordance with Disney's Terms of Service. The authors are not responsible for any consequences of using this tool, including account restrictions.

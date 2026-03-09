# Disneyland Reservation Bloodhound

A Python automation tool that monitors the Disneyland Resort reservation system for target date availability and automatically books reservations when slots open up.

## Features

- **Two modes**: Monitor (notifications only) or Book (auto-reserve)
- **API-first checking**: Uses captured auth tokens for lightweight availability checks, falls back to browser scraping
- **Persistent browser sessions**: Preserves cookies/localStorage across restarts to minimize re-logins
- **Multi-channel notifications**: Terminal, macOS native (osascript), Discord webhooks
- **Resilient polling**: Exponential backoff, browser restart on errors, safety shutoff after 10 consecutive failures
- **Audit trail**: Screenshots at each booking step

## Installation

```bash
# Clone and enter the project
cd DisneylandReservationBloodhound

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

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

### Optional Settings

| Variable | Default | Description |
|---|---|---|
| `TARGET_PARK` | `disneyland` | `disneyland`, `california_adventure`, or `either` |
| `PARTY_SIZE` | `2` | Number of guests |
| `MODE` | `monitor` | `monitor` (notify) or `book` (auto-book) |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between checks |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook for notifications |
| `ENABLE_MACOS_NOTIFICATIONS` | `true` | macOS native notifications |
| `ENABLE_DISCORD_NOTIFICATIONS` | `false` | Send to Discord |
| `HEADLESS` | `true` | Run browser headless |
| `TOKEN_REFRESH_MINUTES` | `12` | Auth token refresh interval |
| `MAX_RETRIES` | `3` | Booking retry attempts |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Usage

```bash
# Monitor mode (default) - notifies when availability found
python main.py

# Book mode - auto-books when availability found
python main.py --mode book

# Override settings via CLI
python main.py --date 2026-04-15 --park disneyland --party-size 4

# Visible browser for debugging
python main.py --headless false

# Custom poll interval
python main.py --interval 30
```

## How It Works

1. **Authentication**: Logs into Disney's site using Playwright, captures auth tokens via network intercepts
2. **Monitoring**: Checks availability via API (using captured token) with browser fallback
3. **Notification**: Sends alerts through terminal, macOS, and/or Discord when availability found
4. **Booking** (book mode): Navigates the 4-page reservation flow automatically:
   - Select party members
   - Choose date and park on calendar
   - Review reservation details
   - Confirm booking

## DISCOVERY Markers

Several selectors and API endpoints are marked with `DISCOVERY` comments — these need to be confirmed against the live Disney site, as the DOM structure and API schema may change. Run with `--headless false` and inspect the page to update these values.

## Disclaimer

This tool is for personal use only. Use responsibly and in accordance with Disney's Terms of Service. The authors are not responsible for any consequences of using this tool, including account restrictions.

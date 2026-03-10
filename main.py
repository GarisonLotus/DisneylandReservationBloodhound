"""CLI entry point for the Disneyland Reservation Bloodhound."""

import argparse
import asyncio
import getpass
import logging
import os
import select
import signal
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from src.auth import AuthError, AuthManager, CaptchaError
from src.booker import ReservationBooker
from src.browser import BrowserManager
from src.config import AppConfig, DateRangeError, load_config, setup_logging
from src.models import BookingTarget, Park
from src.monitor import AvailabilityMonitor
from src.notifications import NotificationManager
from src.scheduler import Scheduler

logger = logging.getLogger(__name__)


def _prompt(label: str, default: str = "") -> str:
    """Prompt the user for input with an optional default."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{_AMBER}  {label}{suffix}: {_RESET}").strip()
    return value or default


def _prompt_choice(label: str, choices: list[str], default: str) -> str:
    """Prompt the user to pick from a list of choices."""
    choices_str = " | ".join(choices)
    while True:
        value = _prompt(f"{label} ({choices_str})", default)
        if value in choices:
            return value
        print(f"  Invalid choice. Pick one of: {choices_str}")


def run_setup_wizard(env_path: Path) -> None:
    """Interactively gather settings and write a .env file."""
    print(f"\n{_BRIGHT}  ── First-Time Setup ──{_RESET}\n")
    print(f"{_DIM}{_AMBER}  No .env file found. Let's set one up.\n{_RESET}")

    # --- Required ---
    print(f"{_BRIGHT}  Required{_RESET}")
    email = _prompt("Disney account email")
    while not email:
        print("  Email is required.")
        email = _prompt("Disney account email")

    password = getpass.getpass(f"{_AMBER}  Disney account password: {_RESET}")
    while not password:
        print("  Password is required.")
        password = getpass.getpass(f"{_AMBER}  Disney account password: {_RESET}")

    target_date = ""
    while True:
        target_date = _prompt("Target date (YYYY-MM-DD)")
        if not target_date:
            print("  Date is required.")
            continue
        try:
            parsed = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            print("  Invalid format. Use YYYY-MM-DD (e.g. 2026-04-15).")
            continue
        today = date.today()
        if parsed < today:
            print("  That date is in the past.")
            continue
        max_date = today + timedelta(days=90)
        if parsed > max_date:
            print(f"  Disneyland only allows reservations up to 90 days out (latest: {max_date.isoformat()}).")
            continue
        break

    party_members = _prompt("Party member names (comma-separated)")
    while not party_members or not any(n.strip() for n in party_members.split(",")):
        print("  At least one party member is required.")
        party_members = _prompt("Party member names (comma-separated)")

    # --- Optional ---
    print(f"\n{_BRIGHT}  Optional (press Enter for defaults){_RESET}")
    target_park = _prompt_choice("Target park", ["disneyland", "california_adventure", "either"], "disneyland")
    mode = _prompt_choice("Mode", ["monitor", "book"], "monitor")
    poll_interval = _prompt("Poll interval in seconds", "60")
    headless = _prompt_choice("Run browser headless", ["true", "false"], "true")

    # Discord
    discord_url = _prompt("Discord webhook URL (blank to skip)")
    enable_discord = "true" if discord_url else "false"

    # Write .env
    content = f"""\
# Disney account credentials
DISNEY_EMAIL={email}
DISNEY_PASSWORD={password}

# Target reservation details
TARGET_DATE={target_date}
TARGET_PARK={target_park}
PARTY_MEMBERS={party_members}

# Operating mode
MODE={mode}
POLL_INTERVAL_SECONDS={poll_interval}

# Notifications
DISCORD_WEBHOOK_URL={discord_url}
ENABLE_MACOS_NOTIFICATIONS=true
ENABLE_DISCORD_NOTIFICATIONS={enable_discord}

# Browser settings
HEADLESS={headless}
BROWSER_DATA_DIR=browser_data

# Auth settings
TOKEN_REFRESH_MINUTES=12

# Reliability
MAX_RETRIES=3
LOG_LEVEL=INFO

# Debugging
DEBUG_IMAGES=false
"""
    env_path.write_text(content)
    print(f"\n{_BRIGHT}  ✔ Saved to {env_path}{_RESET}")
    print(f"{_DIM}{_AMBER}  You can edit this file later to change settings.\n{_RESET}")


def _prompt_new_date() -> str:
    """Prompt the user for a valid target date within the 90-day window."""
    while True:
        new_date = _prompt("New target date (YYYY-MM-DD)")
        if not new_date:
            print("  Date is required.")
            continue
        try:
            parsed = datetime.strptime(new_date, "%Y-%m-%d").date()
        except ValueError:
            print("  Invalid format. Use YYYY-MM-DD (e.g. 2026-04-15).")
            continue
        today = date.today()
        if parsed < today:
            print("  That date is in the past.")
            continue
        max_date = today + timedelta(days=90)
        if parsed > max_date:
            print(f"  Too far out. Latest allowed: {max_date.isoformat()}")
            continue
        return new_date


def _update_env_date(env_path: Path, new_date: str) -> None:
    """Replace the TARGET_DATE line in an existing .env file."""
    import re
    text = env_path.read_text()
    text = re.sub(
        r"^TARGET_DATE=.*$",
        f"TARGET_DATE={new_date}",
        text,
        flags=re.MULTILINE,
    )
    env_path.write_text(text)
    # Update the process environment so load_dotenv picks up the new value
    # (load_dotenv does not override existing env vars by default)
    os.environ["TARGET_DATE"] = new_date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Disneyland Reservation Bloodhound - Monitor and auto-book theme park reservations",
    )
    parser.add_argument(
        "--mode",
        choices=["monitor", "book"],
        default=None,
        help="Operating mode: 'monitor' (notify only) or 'book' (auto-book). Overrides .env.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Target date (YYYY-MM-DD). Overrides .env.",
    )
    parser.add_argument(
        "--park",
        choices=["disneyland", "california_adventure", "either"],
        default=None,
        help="Target park. Overrides .env.",
    )
    parser.add_argument(
        "--headless",
        choices=["true", "false"],
        default=None,
        help="Run browser headless. Overrides .env.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Poll interval in seconds. Overrides .env.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env file (default: .env in current directory).",
    )
    return parser.parse_args()


def apply_cli_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    """Apply CLI argument overrides to the config. Returns a new AppConfig."""
    overrides = {}
    if args.mode is not None:
        overrides["mode"] = args.mode
    if args.date is not None:
        overrides["target_date"] = args.date
    if args.park is not None:
        overrides["target_park"] = Park.from_str(args.park)
    if args.headless is not None:
        overrides["headless"] = args.headless.lower() == "true"
    if args.interval is not None:
        overrides["poll_interval_seconds"] = args.interval

    if not overrides:
        return config

    # Create new frozen dataclass with overrides
    fields = {f.name: getattr(config, f.name) for f in config.__dataclass_fields__.values()}
    fields.update(overrides)
    return AppConfig(**fields)


def _start_quit_listener(loop: asyncio.AbstractEventLoop, stop_callback):
    """Start a background thread that watches for 'q' keypress to quit.

    Puts the terminal in cbreak mode (single-char reads, no echo) so we can
    detect 'q' without the user pressing Enter.  Terminal settings are restored
    when the thread exits.
    """
    if not sys.stdin.isatty():
        return None, None

    try:
        import termios
        import tty
    except ImportError:
        return None, None  # not available on this platform

    old_settings = termios.tcgetattr(sys.stdin)
    stop_event = threading.Event()

    def _listen():
        try:
            tty.setcbreak(sys.stdin.fileno())
            while not stop_event.is_set():
                if select.select([sys.stdin], [], [], 0.5)[0]:
                    ch = sys.stdin.read(1)
                    if ch.lower() == "q":
                        print(f"\n{_AMBER}  Quitting...{_RESET}")
                        loop.call_soon_threadsafe(stop_callback)
                        break
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except Exception:
                pass

    t = threading.Thread(target=_listen, daemon=True, name="quit-listener")
    t.start()
    return stop_event, old_settings


async def async_main(config: AppConfig) -> None:
    """Async entry point: wire up components and run the scheduler."""
    browser_manager = BrowserManager(config)
    auth_manager = AuthManager(config, browser_manager)
    monitor = AvailabilityMonitor(config, auth_manager, browser_manager)
    booker = ReservationBooker(config, auth_manager, browser_manager)
    notifier = NotificationManager(config)

    target = BookingTarget(
        date=config.target_date,
        park=config.target_park,
        party_size=config.party_size,
    )

    scheduler = Scheduler(
        config=config,
        browser_manager=browser_manager,
        auth_manager=auth_manager,
        monitor=monitor,
        booker=booker,
        notifier=notifier,
        target=target,
    )

    # Handle Ctrl+C gracefully
    loop = asyncio.get_running_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        scheduler.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    quit_stop_event = None
    quit_old_settings = None

    try:
        # Start browser
        await browser_manager.start()

        # Initial authentication
        page = await browser_manager.get_page()
        await auth_manager.authenticate(page)

        # Start quit listener now that interactive setup is done
        print(f"{_DIM}{_AMBER}  Press q to quit at any time.{_RESET}\n")
        quit_stop_event, quit_old_settings = _start_quit_listener(loop, signal_handler)

        # Run the scheduler
        await scheduler.run()

    finally:
        # Stop the quit listener and restore terminal settings
        if quit_stop_event:
            quit_stop_event.set()
        if quit_old_settings:
            try:
                import termios
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, quit_old_settings)
            except Exception:
                pass
        await browser_manager.close()
        logger.info("Shutdown complete")


# ANSI color codes — 1970s amber terminal aesthetic
_AMBER = "\033[33m"
_BRIGHT = "\033[93m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_BOLD = "\033[1m"

BANNER = f"""{_BRIGHT}\
                          (  ) (@@) ( )  (@)  ()    @@    O
                     (@@@)  (  )
                 (    )
              (@@@@)
            (   )
        ====        ________                ___________
    _D _|  |_______/        \\__I_I_____===__|___________|
     |(_)---  |   H\\________/ |   |        =|___ ___|
     /     |  |   H  |  |     |   |         ||_| |_||
    |      |  |   H  |__--------------------| [___] |
    | ________|___H__/__|_____/[][]~\\_______|       |
    |/ |   |-----------I_____I [][] []  D   |=======|__
  {_AMBER}__{_BRIGHT}/ =| o |=-~~\\  /~~\\  /~~\\  /~~\\ ____Y___________|__{_AMBER}__{_BRIGHT}
 {_AMBER}|{_BRIGHT}/-=|___|=    ||    ||    ||    |_____/~\\___/        {_AMBER}|{_BRIGHT}
  {_AMBER}\\_/{_BRIGHT}   \\_/  \\__/  \\__/  \\__/  \\__/      \\_/            {_AMBER}\\/{_RESET}
{_DIM}{_AMBER}\
  ╔══════════════════════════════════════════════════════╗
  ║{_RESET}{_AMBER}  D I S N E Y L A N D   R A I L R O A D            {_DIM}║
  ║{_RESET}{_AMBER}  R E S E R V A T I O N   B L O O D H O U N D     {_DIM}║
  ╚══════════════════════════════════════════════════════╝{_RESET}
"""

DISCLAIMER = f"""\
{_DIM}{_AMBER}========================================================================
                         DISCLAIMER
========================================================================{_RESET}
{_AMBER}
This software is licensed under the MIT License. See the LICENSE file
for full terms. Key provisions:

  - This software is provided "AS IS", WITHOUT WARRANTY OF ANY KIND,
    express or implied. In no event shall the authors or copyright
    holders be liable for any claim, damages, or other liability
    arising from the use of this software.

By proceeding, you additionally acknowledge and agree that:

  1. You use this tool entirely at your own risk. The author(s) accept
     no responsibility for any damages, account restrictions, financial
     losses, or other consequences arising from its use.

  2. This tool automates interactions with The Walt Disney Company's
     websites and services. You represent that you have read,
     understood, and agree to be bound by all applicable Walt Disney
     Company Terms of Use, including any terms and conditions
     presented during the reservation process.

  3. You are solely responsible for ensuring your use of this tool
     complies with all applicable laws, regulations, and The Walt
     Disney Company's Terms of Use.
{_DIM}
========================================================================{_RESET}
"""


def confirm_disclaimer() -> None:
    """Display banner and disclaimer, require user confirmation before proceeding."""
    print(BANNER)
    print(DISCLAIMER)
    try:
        response = input(f"{_AMBER}Do you accept these terms? (yes/no): {_RESET}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)

    if response not in ("yes", "y"):
        print("You must accept the terms to use this tool. Exiting.")
        sys.exit(0)

    print()


def main() -> None:
    args = parse_args()

    env_path = Path(args.env_file or ".env")

    # Show banner + disclaimer first (always)
    confirm_disclaimer()

    # If no .env file, run the interactive setup wizard
    if not env_path.exists():
        run_setup_wizard(env_path)

    # Load config, handling date range errors interactively
    while True:
        try:
            config = load_config(str(env_path))
            break
        except DateRangeError as e:
            today = date.today()
            max_date = today + timedelta(days=90)
            print(f"\n{_AMBER}  {e}{_RESET}")
            print(f"{_DIM}{_AMBER}  Reservations are available from "
                  f"{today.isoformat()} to {max_date.isoformat()}.{_RESET}\n")
            try:
                answer = input(f"{_AMBER}  Would you like to pick a new date? (yes/no): {_RESET}").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                sys.exit(0)
            if answer not in ("yes", "y"):
                print("  Exiting. Update TARGET_DATE in your .env file and try again.")
                sys.exit(1)
            new_date = _prompt_new_date()
            _update_env_date(env_path, new_date)
            print(f"\n{_BRIGHT}  ✔ Updated TARGET_DATE to {new_date} in {env_path}{_RESET}\n")

    config = apply_cli_overrides(config, args)

    setup_logging(config.log_level)

    logger.info("Disneyland Reservation Bloodhound")
    logger.info("Mode: %s | Target: %s on %s | Party: %d",
                config.mode, config.target_park.value, config.target_date, config.party_size)

    try:
        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        print("\nShutting down...")
    except (AuthError, CaptchaError) as e:
        print(f"\n{_AMBER}  Authentication failed: {e}{_RESET}\n")
        print(f"{_DIM}{_AMBER}  Suggestions:")
        print(f"    1. Double-check your credentials in {env_path}")
        print(f"    2. Delete {env_path} and re-run to use the setup wizard")
        print(f"    3. Try --headless false to watch the login flow")
        print(f"    4. Delete browser_data/ to clear cached session state{_RESET}\n")
        sys.exit(1)
    except Exception as e:
        logger.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

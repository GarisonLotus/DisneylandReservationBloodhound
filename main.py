"""CLI entry point for the Disneyland Reservation Bloodhound."""

import argparse
import asyncio
import logging
import signal
import sys

from src.auth import AuthManager
from src.booker import ReservationBooker
from src.browser import BrowserManager
from src.config import AppConfig, load_config, setup_logging
from src.models import BookingTarget, Park
from src.monitor import AvailabilityMonitor
from src.notifications import NotificationManager
from src.scheduler import Scheduler

logger = logging.getLogger(__name__)


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

    try:
        # Start browser
        await browser_manager.start()

        # Initial authentication
        page = await browser_manager.get_page()
        await auth_manager.authenticate(page)

        # Run the scheduler
        await scheduler.run()

    finally:
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
    config = load_config(args.env_file)
    config = apply_cli_overrides(config, args)

    confirm_disclaimer()

    setup_logging(config.log_level)

    logger.info("Disneyland Reservation Bloodhound")
    logger.info("Mode: %s | Target: %s on %s | Party: %d",
                config.mode, config.target_park.value, config.target_date, config.party_size)

    try:
        asyncio.run(async_main(config))
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        logger.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

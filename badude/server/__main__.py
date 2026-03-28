"""Entry point: python -m badude.server --config config.json"""

import argparse
import json
import logging
import sys
import threading
import time

from .. import protocol
from .dns_server import DNSServer
from .scraper import fetch_latest
from .store import MessageStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "domain": "example.com",
    "secret": "changeme",
    "bind": "0.0.0.0",
    "port": 5553,
    "db": "badude.db",
    "retention_hours": 24,
    "scrape_interval_seconds": 300,
    "channels": [],
}


def _scraper_loop(store: MessageStore, channel: str, interval: int) -> None:
    """Periodically scrape a Telegram channel, only storing new messages."""
    logger.info("Starting scraper for channel: %s (interval: %ds)", channel, interval)
    while True:
        try:
            messages = fetch_latest(channel)
            if messages:
                new_count = store.insert_new_messages(channel, messages)
                if new_count > 0:
                    logger.info(
                        "Stored %d new messages from %s (%d total scraped)",
                        new_count, channel, len(messages),
                    )
                else:
                    logger.debug("No new messages from %s", channel)
            else:
                logger.warning("No messages scraped from %s", channel)
        except Exception:
            logger.exception("Error scraping %s", channel)
        time.sleep(interval)


def _cleanup_loop(store: MessageStore, interval: int = 3600) -> None:
    """Periodically clean up expired messages."""
    while True:
        time.sleep(interval)
        removed = store.cleanup_expired()
        if removed:
            logger.info("Cleaned up %d expired messages", removed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Badude DNS tunnel server")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to JSON config file",
    )
    parser.add_argument("--domain", type=str, help="DNS domain")
    parser.add_argument("--secret", type=str, help="Shared secret")
    parser.add_argument("--bind", type=str, help="Bind address")
    parser.add_argument("--port", type=int, help="Listen port")
    parser.add_argument("--db", type=str, help="SQLite database path")
    parser.add_argument(
        "--channels", type=str, nargs="+", help="Telegram channels to scrape"
    )
    args = parser.parse_args()

    config = dict(DEFAULT_CONFIG)
    if args.config:
        with open(args.config) as f:
            config.update(json.load(f))

    # CLI args override config file
    if args.domain:
        config["domain"] = args.domain
    if args.secret:
        config["secret"] = args.secret
    if args.bind:
        config["bind"] = args.bind
    if args.port:
        config["port"] = args.port
    if args.db:
        config["db"] = args.db
    if args.channels:
        config["channels"] = args.channels

    if not config["channels"]:
        logger.error("No channels configured. Use --channels or config file.")
        sys.exit(1)

    if config["secret"] == "changeme":
        logger.warning("Using default secret! Set a proper secret for production.")

    key = protocol.derive_key(config["secret"])
    store = MessageStore(db_path=config["db"], retention_hours=config["retention_hours"])
    logger.info("Database: %s", config["db"])

    # Start scraper threads
    for channel in config["channels"]:
        t = threading.Thread(
            target=_scraper_loop,
            args=(store, channel, config["scrape_interval_seconds"]),
            daemon=True,
        )
        t.start()

    # Start cleanup thread
    t = threading.Thread(target=_cleanup_loop, args=(store,), daemon=True)
    t.start()

    # Start DNS server (blocking)
    server = DNSServer(
        store=store,
        domain=config["domain"],
        key=key,
        bind=config["bind"],
        port=config["port"],
    )
    server.run()


if __name__ == "__main__":
    main()

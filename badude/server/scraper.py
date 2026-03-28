"""Scrape public Telegram channels via t.me/s/CHANNEL HTML pages."""

import logging
import re

import requests
from bs4 import BeautifulSoup

from .store import Message

logger = logging.getLogger(__name__)

TELEGRAM_URL = "https://t.me/s/{channel}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _parse_messages(html: str, channel: str) -> list[Message]:
    """Parse Telegram channel page HTML and extract messages."""
    soup = BeautifulSoup(html, "html.parser")
    messages = []
    for widget in soup.select(".tgme_widget_message_wrap"):
        msg_div = widget.select_one(".tgme_widget_message")
        if not msg_div:
            continue
        data_post = msg_div.get("data-post", "")
        if "/" not in data_post:
            continue
        try:
            msg_id = int(data_post.split("/")[1])
        except (IndexError, ValueError):
            continue

        # Extract text
        text_el = msg_div.select_one(".js-message_text")
        text = text_el.get_text(separator="\n").strip() if text_el else ""

        # Extract date
        date = ""
        time_el = msg_div.select_one("time")
        if time_el:
            date = time_el.get("datetime", "")

        # Extract views
        views = ""
        views_el = msg_div.select_one(".tgme_widget_message_views")
        if views_el:
            views = views_el.get_text(strip=True)

        messages.append(
            Message(
                msg_id=msg_id,
                channel=channel,
                text=text,
                date=date,
                views=views,
            )
        )
    return messages


def fetch_latest(channel: str) -> list[Message]:
    """Fetch the latest messages from a public Telegram channel."""
    url = TELEGRAM_URL.format(channel=channel)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return []
    return _parse_messages(resp.text, channel)


def fetch_before(channel: str, before_id: int) -> list[Message]:
    """Fetch older messages before a given message ID."""
    url = TELEGRAM_URL.format(channel=channel)
    params = {"before": before_id}
    try:
        resp = requests.get(
            url, params=params, headers={"User-Agent": USER_AGENT}, timeout=15
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch %s before %d: %s", url, before_id, e)
        return []
    return _parse_messages(resp.text, channel)

"""Scrape public Telegram channels via t.me/s/CHANNEL HTML pages."""

import logging
import re

import requests
from bs4 import BeautifulSoup, Tag

from .store import Message

logger = logging.getLogger(__name__)

TELEGRAM_URL = "https://t.me/s/{channel}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Tags allowed in message HTML (safe subset from Telegram's rendering)
_SAFE_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "br", "em", "strong", "del"}
_SAFE_ATTRS = {"a": ["href"]}


def _sanitize_html(el: Tag) -> str:
    """Extract inner HTML keeping only safe formatting tags."""
    parts = []
    for child in el.children:
        if isinstance(child, str):
            # Escape text nodes
            parts.append(
                child.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
        elif isinstance(child, Tag):
            tag = child.name.lower()
            if tag == "br":
                parts.append("<br>")
            elif tag in _SAFE_TAGS:
                attrs = ""
                if tag == "a" and child.get("href"):
                    href = child["href"]
                    if href.startswith(("http://", "https://", "tg://")):
                        attrs = f' href="{href}" target="_blank" rel="noopener"'
                inner = _sanitize_html(child) if child.contents else ""
                parts.append(f"<{tag}{attrs}>{inner}</{tag}>")
            else:
                # Unknown tag: keep text content only
                parts.append(_sanitize_html(child) if child.contents else child.get_text())
    return "".join(parts)


def _parse_page(html: str, channel: str) -> tuple[str, list[Message]]:
    """Parse page HTML. Returns (display_name, messages)."""
    soup = BeautifulSoup(html, "html.parser")

    # Extract channel display name
    display_name = channel
    name_el = soup.select_one(".tgme_channel_info_header_title")
    if name_el:
        display_name = name_el.get_text(strip=True)

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

        # Extract text (plain) and html (formatted)
        text_el = msg_div.select_one(".js-message_text")
        text = text_el.get_text(separator="\n").strip() if text_el else ""
        html_content = _sanitize_html(text_el).strip() if text_el else ""

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

        # Extract author name (per-message, may differ for forwarded)
        author = ""
        author_el = msg_div.select_one(".tgme_widget_message_owner_name")
        if author_el:
            author = author_el.get_text(strip=True)

        messages.append(
            Message(
                msg_id=msg_id,
                channel=channel,
                text=text,
                date=date,
                views=views,
                author=author,
                html=html_content,
            )
        )
    return display_name, messages


# Cache channel display names
_channel_names: dict[str, str] = {}


def get_channel_display_name(channel: str) -> str:
    return _channel_names.get(channel, channel)


def fetch_latest(channel: str) -> list[Message]:
    """Fetch the latest messages from a public Telegram channel."""
    url = TELEGRAM_URL.format(channel=channel)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return []
    display_name, messages = _parse_page(resp.text, channel)
    _channel_names[channel] = display_name
    return messages


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
    _, messages = _parse_page(resp.text, channel)
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

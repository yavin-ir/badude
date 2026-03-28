"""HTTP server with API endpoints for the Telegram-like web UI."""

import json
import logging
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from .dns_client import DNSTunnelClient

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = os.path.join(
    os.path.expanduser("~"), ".badude", "settings.json"
)


def _static_dir() -> str:
    """Resolve static file directory, handling PyInstaller bundles."""
    if getattr(sys, "_MEIPASS", None):
        return os.path.join(sys._MEIPASS, "static")
    return os.path.join(os.path.dirname(__file__), "static")


def _load_settings(path: str) -> dict | None:
    """Load settings from JSON file. Returns None if not found."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_settings(path: str, settings: dict) -> None:
    """Save settings to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)


def _client_from_settings(settings: dict) -> DNSTunnelClient:
    """Create a DNSTunnelClient from a settings dict."""
    return DNSTunnelClient(
        server=settings.get("server", "127.0.0.1"),
        port=int(settings.get("port", 5553)),
        domain=settings.get("domain", "example.com"),
        secret=settings.get("secret", "changeme"),
        dns_resolver=settings.get("dns_resolver", ""),
        dns_resolver_port=int(settings.get("dns_resolver_port", 53)),
    )


class WebHandler(BaseHTTPRequestHandler):
    dns_client: DNSTunnelClient | None = None
    settings_path: str = DEFAULT_SETTINGS_PATH

    def log_message(self, format, *args):
        logger.info(format, *args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, content_type: str) -> None:
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_file(
                os.path.join(_static_dir(), "index.html"), "text/html; charset=utf-8"
            )
        elif path == "/api/channels":
            if self.dns_client is None:
                self._send_json({"error": "not configured"}, 503)
                return
            result = self.dns_client.request({"a": "ch"})
            if result is None:
                self._send_json({"error": "dns request failed"}, 502)
            else:
                self._send_json(result)
        elif path == "/api/messages":
            if self.dns_client is None:
                self._send_json({"error": "not configured"}, 503)
                return
            qs = parse_qs(parsed.query)
            channel = qs.get("channel", [None])[0]
            if not channel:
                self._send_json({"error": "channel required"}, 400)
                return
            action: dict = {"a": "ms", "c": channel}
            before = qs.get("before", [None])[0]
            if before:
                action["b"] = int(before)
            limit = qs.get("limit", [None])[0]
            if limit:
                action["l"] = int(limit)
            result = self.dns_client.request(action)
            if result is None:
                self._send_json({"error": "dns request failed"}, 502)
            else:
                self._send_json(result)
        elif path == "/api/settings":
            if self.dns_client:
                self._send_json({
                    "server": self.dns_client.server,
                    "port": self.dns_client.port,
                    "domain": self.dns_client.domain,
                    "dns_resolver": self.dns_client.dns_resolver,
                    "dns_resolver_port": self.dns_client.dns_resolver_port,
                    "configured": True,
                })
            else:
                self._send_json({"configured": False})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/settings":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                settings = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, 400)
                return

            WebHandler.dns_client = _client_from_settings(settings)

            # Persist to disk
            _save_settings(self.settings_path, settings)

            logger.info(
                "Settings saved: server=%s:%s domain=%s dns_resolver=%s",
                settings.get("server"),
                settings.get("port"),
                settings.get("domain"),
                settings.get("dns_resolver") or "(direct)",
            )
            self._send_json({"ok": True})
        else:
            self.send_error(404)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    dns_client: DNSTunnelClient | None = None,
    settings_path: str = DEFAULT_SETTINGS_PATH,
) -> None:
    """Start the HTTP server."""
    WebHandler.settings_path = settings_path

    # If no client from CLI args, try loading saved settings
    if dns_client is None:
        saved = _load_settings(settings_path)
        if saved:
            dns_client = _client_from_settings(saved)
            logger.info("Loaded saved settings from %s", settings_path)

    WebHandler.dns_client = dns_client
    server = HTTPServer((host, port), WebHandler)
    logger.info("Web server listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("Web server stopped")

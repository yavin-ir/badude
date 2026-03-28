"""Entry point: python -m badude.client --listen 127.0.0.1:8080"""

import argparse
import logging
import webbrowser

from .dns_client import DNSTunnelClient
from .web import run_server, DEFAULT_SETTINGS_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Badude DNS tunnel client")
    parser.add_argument(
        "--listen",
        type=str,
        default="127.0.0.1:8080",
        help="HTTP listen address (default: 127.0.0.1:8080)",
    )
    parser.add_argument("--server", type=str, help="DNS server address")
    parser.add_argument("--port", type=int, default=5553, help="DNS server port")
    parser.add_argument("--domain", type=str, help="DNS tunnel domain")
    parser.add_argument("--secret", type=str, help="Shared secret")
    parser.add_argument(
        "--dns-resolver",
        type=str,
        default="",
        help="DNS resolver to relay queries through (e.g. 8.8.8.8). "
        "If empty, queries go directly to --server",
    )
    parser.add_argument(
        "--dns-resolver-port",
        type=int,
        default=53,
        help="DNS resolver port (default: 53)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_SETTINGS_PATH,
        help=f"Settings file path (default: {DEFAULT_SETTINGS_PATH})",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Don't open browser automatically"
    )
    args = parser.parse_args()

    # Parse listen address
    if ":" in args.listen:
        host, port_str = args.listen.rsplit(":", 1)
        http_port = int(port_str)
    else:
        host = args.listen
        http_port = 8080

    # Create DNS client if server args provided
    dns_client = None
    if args.server and args.domain and args.secret:
        dns_client = DNSTunnelClient(
            server=args.server,
            port=args.port,
            domain=args.domain,
            secret=args.secret,
            dns_resolver=args.dns_resolver,
            dns_resolver_port=args.dns_resolver_port,
        )
        resolver_info = (
            f" via resolver {args.dns_resolver}:{args.dns_resolver_port}"
            if args.dns_resolver
            else " (direct)"
        )
        logger.info(
            "DNS tunnel: %s:%d domain=%s%s",
            args.server, args.port, args.domain, resolver_info,
        )
    else:
        logger.info(
            "No DNS server configured via CLI. "
            "Will try saved settings or use the UI settings panel."
        )

    # Open browser
    if not args.no_browser:
        import threading

        def open_browser():
            import time
            time.sleep(0.5)
            webbrowser.open(f"http://{host}:{http_port}")

        threading.Thread(target=open_browser, daemon=True).start()

    run_server(
        host=host,
        port=http_port,
        dns_client=dns_client,
        settings_path=args.config,
    )


if __name__ == "__main__":
    main()

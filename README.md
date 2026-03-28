# Badude

Read public Telegram channels over a DNS tunnel.

A server scrapes Telegram channels and serves the content through DNS TXT queries/responses. A client provides a local web UI that communicates with the server entirely over DNS — no direct HTTP connection between client and server.

```
Browser ──HTTP──> badude-client :8080 ──DNS UDP──> badude-server :5553 ──HTTPS──> t.me/s/CHANNEL
          (local only)                  (tunnel)                        (scraping)
```

## How It Works

- **Server** periodically scrapes public Telegram channel pages (`t.me/s/CHANNEL`), stores messages in SQLite, and listens for DNS TXT queries on a UDP port.
- **Client** runs a local HTTP server with a Telegram-like web UI. When the user views channels/messages, the client sends encrypted DNS queries to the server and decrypts the responses.
- **Protocol** uses AES-256-GCM encryption with a shared secret. Requests are base32-encoded into DNS query names. Responses are chunked into DNS TXT records with a polling mechanism for multi-chunk responses.

## Install

```bash
# Server
pip install dnslib cryptography requests beautifulsoup4

# Client
pip install dnslib cryptography
```

## Usage

### Server

```bash
python -m badude.server \
    --domain tunnel.example.com \
    --secret my-shared-secret \
    --channels durov telegram \
    --port 5553 \
    --db badude.db
```

Or with a config file:

```bash
python -m badude.server --config config.json
```

```json
{
    "domain": "tunnel.example.com",
    "secret": "my-shared-secret",
    "bind": "0.0.0.0",
    "port": 5553,
    "db": "badude.db",
    "retention_hours": 24,
    "scrape_interval_seconds": 300,
    "channels": ["durov", "telegram"]
}
```

### Client

```bash
python -m badude.client \
    --server 1.2.3.4 \
    --domain tunnel.example.com \
    --secret my-shared-secret \
    --listen 127.0.0.1:8080
```

Opens a browser to `http://127.0.0.1:8080` with the web UI. All DNS settings can also be configured from the settings panel in the UI and are persisted to `~/.badude/settings.json`.

To route queries through a DNS resolver instead of connecting directly:

```bash
python -m badude.client \
    --server 1.2.3.4 \
    --domain tunnel.example.com \
    --secret my-shared-secret \
    --dns-resolver 8.8.8.8
```

### Server Options

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | | JSON config file path |
| `--domain` | `example.com` | DNS tunnel domain |
| `--secret` | `changeme` | Shared encryption secret |
| `--bind` | `0.0.0.0` | Bind address |
| `--port` | `5553` | UDP listen port |
| `--db` | `badude.db` | SQLite database path |
| `--channels` | | Telegram channels to scrape |

### Client Options

| Flag | Default | Description |
|------|---------|-------------|
| `--listen` | `127.0.0.1:8080` | Local HTTP address |
| `--server` | | DNS server IP |
| `--port` | `5553` | DNS server port |
| `--domain` | | DNS tunnel domain |
| `--secret` | | Shared encryption secret |
| `--dns-resolver` | | DNS resolver to relay queries through (e.g. `8.8.8.8`) |
| `--dns-resolver-port` | `53` | DNS resolver port |
| `--config` | `~/.badude/settings.json` | Settings file path |
| `--no-browser` | | Don't auto-open browser |

## Building Executables

```bash
pip install pyinstaller

# Client (GUI, no console window)
pyinstaller client.spec

# Server (console)
pyinstaller server.spec
```

Produces `dist/badude-client` and `dist/badude-server`.

## Project Structure

```
badude/
├── protocol.py          # AES-256-GCM encryption, key derivation
├── dns_codec.py         # Base32 DNS encoding, TXT record handling
├── server/
│   ├── __main__.py      # Server entry point
│   ├── dns_server.py    # UDP DNS listener, request dispatch
│   ├── scraper.py       # Telegram channel HTML scraper
│   └── store.py         # SQLite message store
└── client/
    ├── __main__.py      # Client entry point
    ├── dns_client.py    # DNS tunnel client, chunk reassembly
    ├── web.py           # Local HTTP server and API
    └── static/
        └── index.html   # Telegram-like dark theme SPA
```

## DNS Tunnel Protocol

**Request** (client to server): `JSON → AES-GCM encrypt → prepend request_id → base32 → DNS labels → TXT query`

**Response** (server to client): `JSON → AES-GCM encrypt → chunk into ~900 byte pieces → TXT RDATA`

Multi-chunk responses use polling: client sends `p<request_id_hex><chunk_index_hex>.<domain>` queries to fetch remaining chunks.

Encryption: shared secret → SHA-256 → AES-256-GCM key. Each message carries its own random 12-byte nonce.

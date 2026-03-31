"""UDP DNS server that handles tunnel requests and serves chunked responses."""

import json
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import protocol, dns_codec
from .store import MessageStore

MAX_WORKERS = 64

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class ChunkCache:
    """Cache chunked responses so clients can poll for remaining chunks."""

    def __init__(self, ttl: int = protocol.CHUNK_CACHE_TTL):
        self._lock = threading.Lock()
        self._cache: dict[bytes, tuple[list[bytes], float]] = {}
        self._ttl = ttl

    def store(self, req_id: bytes, chunks: list[bytes]) -> None:
        with self._lock:
            self._cache[req_id] = (chunks, time.time())

    def get(self, req_id: bytes, chunk_idx: int) -> bytes | None:
        with self._lock:
            entry = self._cache.get(req_id)
            if entry is None:
                return None
            chunks, ts = entry
            if time.time() - ts > self._ttl:
                del self._cache[req_id]
                return None
            if chunk_idx < 0 or chunk_idx >= len(chunks):
                return None
            return chunks[chunk_idx]

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            expired = [k for k, (_, ts) in self._cache.items() if now - ts > self._ttl]
            for k in expired:
                del self._cache[k]


def _chunk_response(req_id: bytes, key: bytes, response_json: bytes) -> list[bytes]:
    """Encrypt and chunk a response into DNS TXT-sized pieces.

    Each chunk: chunk_count(1) + chunk_index(1) + request_id(4) + nonce(12) + ciphertext + tag(16)
    """
    encrypted = protocol.encrypt(key, response_json)
    # Max payload per chunk after header
    header_len = protocol.CHUNK_HEADER_LEN + protocol.REQ_ID_LEN
    max_payload = protocol.MAX_TXT_RDATA - header_len
    # Split encrypted data into chunks
    parts = []
    for i in range(0, len(encrypted), max_payload):
        parts.append(encrypted[i : i + max_payload])
    if not parts:
        parts = [b""]
    chunk_count = len(parts)
    chunks = []
    for idx, part in enumerate(parts):
        chunk = bytes([chunk_count, idx]) + req_id + part
        chunks.append(chunk)
    return chunks


class DNSServer:
    def __init__(
        self,
        store: MessageStore,
        domain: str,
        key: bytes,
        bind: str = "0.0.0.0",
        port: int = 5553,
    ):
        self.store = store
        self.domain = domain.rstrip(".")
        self.key = key
        self.bind = bind
        self.port = port
        self.chunk_cache = ChunkCache()
        self._sock: socket.socket | None = None
        self._running = False
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def _handle_action(self, action: dict) -> dict:
        """Dispatch an action request and return the response dict."""
        a = action.get("a")
        if a == "p":
            return {"p": "ok"}
        elif a == "ch":
            from .scraper import get_channel_display_name
            channels = self.store.get_channels()
            for ch in channels:
                ch["d"] = get_channel_display_name(ch["n"])
            return {"ch": channels}
        elif a == "ms":
            channel = action.get("c", "")
            before = action.get("b")
            limit = action.get("l", 3)
            if before is not None:
                before = int(before)
            limit = min(int(limit), 50)
            messages = self.store.get_messages(channel, before, limit)
            return {"ms": messages}
        else:
            return {"error": "unknown action"}

    def _handle_query(self, query_wire: bytes, addr: tuple) -> bytes:
        """Process a DNS query and return the wire-format response."""
        from dnslib import DNSRecord

        try:
            request = DNSRecord.parse(query_wire)
        except Exception:
            logger.warning("[%s] Failed to parse DNS query", addr[0])
            return None

        qname = str(request.q.qname).rstrip(".")
        logger.info("[%s] Query: %s", addr[0], qname)

        # Check if it's a poll query
        poll = dns_codec.is_poll_query(qname, self.domain)
        if poll is not None:
            req_id, chunk_idx = poll
            chunk = self.chunk_cache.get(req_id, chunk_idx)
            if chunk is not None:
                logger.info("[%s] Poll chunk %d for %s", addr[0], chunk_idx, req_id.hex())
                return dns_codec.build_dns_response(query_wire, chunk)
            else:
                logger.warning("[%s] Poll miss chunk %d for %s", addr[0], chunk_idx, req_id.hex())
                return dns_codec.build_dns_error_response(query_wire)

        # Regular data query - decode payload
        try:
            payload = dns_codec.decode_query_name(qname, self.domain)
        except (ValueError, Exception) as e:
            logger.warning("[%s] Decode failed: %s", addr[0], e)
            return dns_codec.build_dns_error_response(query_wire)

        if len(payload) < protocol.NONCE_LEN + protocol.TAG_LEN:
            logger.warning("[%s] Payload too short (%d bytes)", addr[0], len(payload))
            return dns_codec.build_dns_error_response(query_wire)

        # req_id = first 4 bytes of nonce (no separate prefix)
        req_id = payload[: protocol.REQ_ID_LEN]
        encrypted_data = payload

        # Decrypt request
        try:
            plaintext = protocol.decrypt(self.key, encrypted_data)
        except Exception as e:
            logger.warning("[%s] Decryption failed: %s", addr[0], e)
            return dns_codec.build_dns_error_response(query_wire)

        # Parse JSON action
        try:
            action = json.loads(plaintext)
        except json.JSONDecodeError as e:
            logger.warning("[%s] Invalid JSON: %s", addr[0], e)
            return dns_codec.build_dns_error_response(query_wire)

        logger.info("[%s] Action: %s → %d bytes response", addr[0], action, len(plaintext))

        # Handle action
        response_data = self._handle_action(action)
        response_json = json.dumps(response_data, separators=(",", ":")).encode("utf-8")

        # Chunk the response
        chunks = _chunk_response(req_id, self.key, response_json)
        logger.info("[%s] Response: %d bytes, %d chunk(s)", addr[0], len(response_json), len(chunks))

        # Cache all chunks for polling
        if len(chunks) > 1:
            self.chunk_cache.store(req_id, chunks)

        # Return first chunk
        return dns_codec.build_dns_response(query_wire, chunks[0])

    def _serve_thread(self, query_wire: bytes, addr: tuple) -> None:
        try:
            response_wire = self._handle_query(query_wire, addr)
            if response_wire is not None:
                self._sock.sendto(response_wire, addr)
        except Exception:
            logger.exception("Error handling query from %s", addr)

    def run(self) -> None:
        """Start the DNS server (blocking)."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.bind, self.port))
        self._running = True
        logger.info("DNS server listening on %s:%d", self.bind, self.port)
        logger.info("Domain: %s", self.domain)

        # Periodic cache cleanup
        def cleanup_loop():
            while self._running:
                time.sleep(protocol.CHUNK_CACHE_TTL)
                self.chunk_cache.cleanup()

        t = threading.Thread(target=cleanup_loop, daemon=True)
        t.start()

        try:
            while self._running:
                try:
                    data, addr = self._sock.recvfrom(4096)
                    logger.debug("[%s] Received %d bytes", addr[0], len(data))
                except OSError:
                    break
                self._pool.submit(self._serve_thread, data, addr)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self._pool.shutdown(wait=False)
            self._sock.close()
            logger.info("DNS server stopped")

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

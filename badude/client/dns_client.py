"""DNS tunnel client: send requests and receive chunked responses over DNS."""

import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import protocol, dns_codec

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

DEFAULT_TIMEOUT = 10
RESOLVER_TIMEOUT = 15
MAX_RETRIES = 2
REQUEST_ATTEMPTS = 3  # retry entire request with fresh nonce


class DNSTunnelClient:
    def __init__(
        self,
        server: str = "127.0.0.1",
        port: int = 5553,
        domain: str = "example.com",
        secret: str = "changeme",
        dns_resolver: str = "",
        dns_resolver_port: int = 53,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.server = server
        self.port = port
        self.domain = domain.rstrip(".")
        self.secret = secret
        self.key = protocol.derive_key(secret)
        self.dns_resolver = dns_resolver.strip()
        self.dns_resolver_port = dns_resolver_port
        # Longer timeout when going through a resolver (extra hops)
        self.timeout = timeout if not self.dns_resolver else max(timeout, RESOLVER_TIMEOUT)

    @property
    def _target(self) -> tuple[str, int]:
        """Where to send DNS packets: resolver if set, otherwise direct to server."""
        if self.dns_resolver:
            return (self.dns_resolver, self.dns_resolver_port)
        return (self.server, self.port)

    def _send_recv(self, query_wire: bytes) -> bytes:
        """Send a DNS query and receive the response via UDP."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        logger.debug("Sending DNS query to %s (timeout=%s)", self._target, self.timeout)
        try:
            sock.sendto(query_wire, self._target)
            data, _ = sock.recvfrom(65535)
            logger.debug("Received DNS response: %d bytes", len(data))
            return data
        finally:
            sock.close()

    def _send_recv_validated(self, query_wire: bytes) -> bytes | None:
        """Send/receive with retry on timeout and SERVFAIL."""
        from dnslib import DNSRecord

        for attempt in range(MAX_RETRIES):
            try:
                data = self._send_recv(query_wire)
            except socket.timeout:
                logger.warning("Timeout (attempt %d/%d)", attempt + 1, MAX_RETRIES)
                continue
            except OSError as e:
                logger.error("Network error: %s", e)
                return None

            # Check for SERVFAIL / other error rcodes — retry those
            try:
                resp = DNSRecord.parse(data)
                rcode = resp.header.rcode
                if rcode == 2:  # SERVFAIL
                    logger.warning(
                        "SERVFAIL (attempt %d/%d)", attempt + 1, MAX_RETRIES
                    )
                    continue
                if rcode == 5:  # REFUSED
                    logger.warning(
                        "REFUSED (attempt %d/%d)", attempt + 1, MAX_RETRIES
                    )
                    continue
            except Exception:
                pass

            return data
        return None

    def _do_request(self, plaintext: bytes) -> dict | None:
        """Single attempt: encrypt, send, collect chunks, decrypt."""
        from dnslib import DNSRecord

        # Encrypt with fresh nonce each attempt (→ different query name)
        encrypted = protocol.encrypt(self.key, plaintext)

        # Encode as DNS query
        qname = dns_codec.encode_query_name(encrypted, self.domain)
        query_wire = dns_codec.build_dns_query(qname)

        # Send and receive first response
        response_wire = self._send_recv_validated(query_wire)
        if response_wire is None:
            return None

        # Parse TXT records
        txt_parts = dns_codec.parse_dns_response(response_wire)
        if not txt_parts:
            try:
                resp = DNSRecord.parse(response_wire)
                logger.warning(
                    "Empty response: rcode=%s flags=%s answers=%d",
                    resp.header.rcode, hex(resp.header.bitmap), len(resp.rr),
                )
            except Exception:
                logger.warning("Could not parse DNS response (%d bytes)", len(response_wire))
            return None

        first_chunk = txt_parts[0]
        if len(first_chunk) < protocol.CHUNK_HEADER_LEN + protocol.REQ_ID_LEN:
            logger.warning("Response chunk too short")
            return None

        chunk_count = first_chunk[0]
        chunk_index = first_chunk[1]
        resp_req_id = first_chunk[2 : 2 + protocol.REQ_ID_LEN]
        chunk_data = first_chunk[2 + protocol.REQ_ID_LEN :]

        # Collect all chunks - poll sequentially with small delay to avoid detection
        all_chunks = {chunk_index: chunk_data}

        # Poll for remaining chunks one at a time (sequential, not parallel)
        if chunk_count > 1:
            for idx in range(1, chunk_count):
                # Small delay between poll queries to avoid detection
                time.sleep(0.5)
                poll_qname = dns_codec.encode_poll_query_name(
                    resp_req_id, idx, self.domain
                )
                poll_wire = dns_codec.build_dns_query(poll_qname)
                poll_resp = self._send_recv_validated(poll_wire)
                if poll_resp is None:
                    logger.warning("Failed to poll chunk %d/%d", idx, chunk_count)
                    return None
                poll_parts = dns_codec.parse_dns_response(poll_resp)
                if not poll_parts:
                    logger.warning("Failed to poll chunk %d/%d", idx, chunk_count)
                    return None
                poll_chunk = poll_parts[0]
                if len(poll_chunk) < protocol.CHUNK_HEADER_LEN + protocol.REQ_ID_LEN:
                    logger.warning("Poll chunk %d too short", idx)
                    return None
                all_chunks[idx] = poll_chunk[2 + protocol.REQ_ID_LEN:]

        # Reassemble encrypted data
        encrypted_data = b""
        for i in range(chunk_count):
            if i not in all_chunks:
                logger.warning("Missing chunk %d", i)
                return None
            encrypted_data += all_chunks[i]

        # Decrypt
        try:
            decrypted = protocol.decrypt(self.key, encrypted_data)
        except Exception as e:
            logger.warning("Decryption failed: %s", e)
            return None

        try:
            return json.loads(decrypted)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON response: %s", e)
            return None

    def request(self, action: dict) -> dict | None:
        """Send a request action dict and return the response dict.

        Retries the entire request with a fresh nonce on failure,
        which generates a different DNS query name (bypasses resolver cache).
        """
        plaintext = json.dumps(action, separators=(",", ":")).encode("utf-8")

        for attempt in range(REQUEST_ATTEMPTS):
            result = self._do_request(plaintext)
            if result is not None:
                return result
            if attempt < REQUEST_ATTEMPTS - 1:
                logger.info(
                    "Request failed, retrying with fresh nonce (%d/%d)...",
                    attempt + 2, REQUEST_ATTEMPTS,
                )

        logger.error("Request failed after %d attempts", REQUEST_ATTEMPTS)
        return None

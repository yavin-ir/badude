"""DNS tunnel client: send requests and receive chunked responses over DNS."""

import json
import logging
import socket

from .. import protocol, dns_codec

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5
MAX_RETRIES = 2


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
        self.timeout = timeout if not self.dns_resolver else max(timeout, 10)

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
        try:
            sock.sendto(query_wire, self._target)
            data, _ = sock.recvfrom(4096)
            return data
        finally:
            sock.close()

    def _send_recv_retry(self, query_wire: bytes) -> bytes | None:
        """Send/receive with retry logic."""
        for attempt in range(MAX_RETRIES):
            try:
                return self._send_recv(query_wire)
            except socket.timeout:
                logger.warning("Timeout (attempt %d/%d)", attempt + 1, MAX_RETRIES)
            except OSError as e:
                logger.error("Network error: %s", e)
                break
        return None

    def request(self, action: dict) -> dict | None:
        """Send a request action dict and return the response dict.

        action examples:
          {"a": "ch"}                           - list channels
          {"a": "ms", "c": "durov", "l": 10}    - get messages
        """
        # Serialize and encrypt (req_id is derived from nonce, first 4 bytes)
        plaintext = json.dumps(action, separators=(",", ":")).encode("utf-8")
        encrypted = protocol.encrypt(self.key, plaintext)
        payload = encrypted

        # Encode as DNS query
        qname = dns_codec.encode_query_name(payload, self.domain)
        query_wire = dns_codec.build_dns_query(qname)

        # Send and receive first response
        response_wire = self._send_recv_retry(query_wire)
        if response_wire is None:
            return None

        # Parse TXT records
        txt_parts = dns_codec.parse_dns_response(response_wire)
        if not txt_parts:
            from dnslib import DNSRecord
            try:
                resp = DNSRecord.parse(response_wire)
                logger.error(
                    "DNS response: rcode=%s flags=%s answers=%d qname=%s",
                    resp.header.rcode, hex(resp.header.bitmap), len(resp.rr),
                    resp.q.qname,
                )
            except Exception:
                logger.error("Could not parse DNS response (%d bytes)", len(response_wire))
            logger.error("No TXT records in response")
            return None

        first_chunk = txt_parts[0]
        if len(first_chunk) < protocol.CHUNK_HEADER_LEN + protocol.REQ_ID_LEN:
            logger.error("Response chunk too short")
            return None

        chunk_count = first_chunk[0]
        chunk_index = first_chunk[1]
        resp_req_id = first_chunk[2 : 2 + protocol.REQ_ID_LEN]
        chunk_data = first_chunk[2 + protocol.REQ_ID_LEN :]

        # Collect all chunks
        all_chunks = {chunk_index: chunk_data}

        # Poll for remaining chunks if needed
        if chunk_count > 1:
            for idx in range(chunk_count):
                if idx in all_chunks:
                    continue
                poll_qname = dns_codec.encode_poll_query_name(
                    resp_req_id, idx, self.domain
                )
                poll_wire = dns_codec.build_dns_query(poll_qname)
                poll_resp = self._send_recv_retry(poll_wire)
                if poll_resp is None:
                    logger.error("Failed to poll chunk %d", idx)
                    return None
                poll_parts = dns_codec.parse_dns_response(poll_resp)
                if not poll_parts:
                    logger.error("No TXT in poll response for chunk %d", idx)
                    return None
                poll_chunk = poll_parts[0]
                if len(poll_chunk) < protocol.CHUNK_HEADER_LEN + protocol.REQ_ID_LEN:
                    continue
                p_data = poll_chunk[2 + protocol.REQ_ID_LEN :]
                all_chunks[idx] = p_data

        # Reassemble encrypted data
        encrypted_data = b""
        for i in range(chunk_count):
            if i not in all_chunks:
                logger.error("Missing chunk %d", i)
                return None
            encrypted_data += all_chunks[i]

        # Decrypt
        try:
            plaintext = protocol.decrypt(self.key, encrypted_data)
        except Exception as e:
            logger.error("Decryption failed: %s", e)
            return None

        try:
            return json.loads(plaintext)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON response: %s", e)
            return None

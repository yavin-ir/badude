"""DNS query/response encoding and decoding using base32 and dnslib."""

import base64

from dnslib import DNSRecord, DNSHeader, DNSQuestion, QTYPE, RR, TXT

from . import protocol

# Use base32 without padding, lowercase for DNS compatibility
_B32 = base64.b32encode
_B32D = base64.b32decode


def _b32_encode(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").rstrip("=").lower()


def _b32_decode(s: str) -> bytes:
    s = s.upper()
    # Add padding
    pad = (8 - len(s) % 8) % 8
    s += "=" * pad
    return base64.b32decode(s)


def encode_query_name(payload: bytes, domain: str) -> str:
    """Encode payload into a DNS query name: base32 labels + domain suffix.

    payload is: request_id(4) + encrypted_data
    Returns FQDN like: <label1>.<label2>.<domain>
    """
    encoded = _b32_encode(payload)
    # Split into labels of max 63 chars
    labels = []
    for i in range(0, len(encoded), protocol.MAX_LABEL_LEN):
        labels.append(encoded[i : i + protocol.MAX_LABEL_LEN])
    labels.append(domain)
    return ".".join(labels)


def decode_query_name(qname: str, domain: str) -> bytes:
    """Decode a DNS query name back to payload bytes.

    Strips the domain suffix, joins remaining labels, base32 decodes.
    Case-insensitive domain matching (DNS is case-insensitive).
    """
    qname = qname.rstrip(".")
    domain = domain.rstrip(".")
    if not qname.lower().endswith("." + domain.lower()):
        raise ValueError(f"query name {qname!r} does not end with domain {domain!r}")
    prefix = qname[: -(len(domain) + 1)]
    encoded = prefix.replace(".", "")
    return _b32_decode(encoded)


def encode_poll_query_name(req_id: bytes, chunk_idx: int, domain: str) -> str:
    """Encode a poll query for fetching additional chunks.

    Format: p<req_id_hex><chunk_idx_hex>.<domain>
    """
    label = "p" + req_id.hex() + format(chunk_idx, "02x")
    return label + "." + domain


def is_poll_query(qname: str, domain: str) -> tuple[bytes, int] | None:
    """Check if a query name is a poll query. Returns (req_id, chunk_idx) or None."""
    qname = qname.rstrip(".")
    domain = domain.rstrip(".")
    if not qname.lower().endswith("." + domain.lower()):
        return None
    prefix = qname[: -(len(domain) + 1)]
    # Poll queries have no dots in prefix and start with 'p'
    if "." in prefix or not prefix.startswith("p"):
        return None
    hex_part = prefix[1:]
    if len(hex_part) != protocol.REQ_ID_LEN * 2 + 2:
        return None
    try:
        req_id = bytes.fromhex(hex_part[: protocol.REQ_ID_LEN * 2])
        chunk_idx = int(hex_part[protocol.REQ_ID_LEN * 2 :], 16)
        return (req_id, chunk_idx)
    except ValueError:
        return None


def build_dns_query(qname: str) -> bytes:
    """Build a DNS TXT query in wire format using dnslib."""
    q = DNSRecord(
        DNSHeader(rd=1),
        q=DNSQuestion(qname, QTYPE.TXT),
    )
    return q.pack()


def build_dns_response(query_wire: bytes, txt_data: bytes) -> bytes:
    """Build a DNS TXT response for a given query, with txt_data as TXT RDATA."""
    request = DNSRecord.parse(query_wire)
    reply = request.reply()
    qname = str(request.q.qname)
    # Split into <=255 byte character-strings per RFC 1035 TXT format
    chunks = [txt_data[i : i + 255] for i in range(0, len(txt_data), 255)]
    if not chunks:
        chunks = [b""]
    reply.add_answer(
        RR(qname, QTYPE.TXT, rdata=TXT(chunks), ttl=protocol.RESPONSE_TTL)
    )
    return reply.pack()


def build_dns_error_response(query_wire: bytes) -> bytes:
    """Build a DNS error response as NOERROR with empty answer.

    Uses NOERROR (rcode=0) instead of NXDOMAIN to prevent resolvers
    from negative-caching and blocking future queries to the domain.
    """
    request = DNSRecord.parse(query_wire)
    reply = request.reply()
    reply.header.rcode = 0
    return reply.pack()


def parse_dns_response(wire: bytes) -> list[bytes]:
    """Parse a DNS response and extract TXT record data as list of bytes.

    Each TXT RR may contain multiple character-strings; we concatenate them
    into one bytes object per RR (reversing the 255-byte splitting).
    """
    response = DNSRecord.parse(wire)
    results = []
    for rr in response.rr:
        if rr.rtype == QTYPE.TXT:
            # Concatenate all character-strings in this TXT RR
            results.append(b"".join(bytes(part) for part in rr.rdata.data))
    return results

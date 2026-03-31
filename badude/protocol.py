"""AES-256-GCM encryption/decryption and shared constants for the DNS tunnel protocol."""

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_LEN = 12
TAG_LEN = 16
REQ_ID_LEN = 4
CHUNK_HEADER_LEN = 2  # chunk_count(1) + chunk_index(1)
MAX_TXT_RDATA = 1400  # fit within EDNS0 4096-byte limit with room for overhead
MAX_LABEL_LEN = 63
MAX_NAME_LEN = 253
RESPONSE_TTL = 1  # short TTL so resolvers cache poll queries briefly
CHUNK_CACHE_TTL = 120  # longer TTL for slow resolver round-trips


def derive_key(secret: str) -> bytes:
    """Derive a 256-bit AES key from a shared secret using SHA-256."""
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt plaintext with AES-256-GCM. Returns nonce + ciphertext + tag."""
    nonce = os.urandom(NONCE_LEN)
    aesgcm = AESGCM(key)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ct_and_tag


def decrypt(key: bytes, data: bytes) -> bytes:
    """Decrypt AES-256-GCM data (nonce + ciphertext + tag). Returns plaintext."""
    if len(data) < NONCE_LEN + TAG_LEN:
        raise ValueError("ciphertext too short")
    nonce = data[:NONCE_LEN]
    ct_and_tag = data[NONCE_LEN:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct_and_tag, None)


def generate_request_id() -> bytes:
    """Generate a random 4-byte request ID.

    Note: in practice, req_id is derived from nonce[:4] of the encrypted
    payload, so this is only used for backward compatibility or testing.
    """
    return os.urandom(REQ_ID_LEN)

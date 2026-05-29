"""
🔐 Coordination Crypto Module — OWL + Self

AES-256-GCM encryption for COORDINATION.md messages.
HMAC-SHA256 signing for message authentication.

Protocol:
  - Each message block is encrypted with AES-256-GCM (random 12-byte nonce).
  - The nonce + ciphertext + GCM tag are serialized as: nonce (12 bytes) || ciphertext+tag.
  - Each signed message is prefixed with HMAC-SHA256 (32 bytes) of the encrypted blob.
  - Final format: HMAC (32 bytes) || nonce (12 bytes) || ciphertext+tag
  - Stored in COORDINATION.md as base64 inside fenced blocks:
      ```enc
      <base64 data>
      ```
  - Backward compatible: plaintext messages remain readable during transition.

Environment:
  COORDINATION_KEY — 32-byte key, base64-encoded in env var, never in repo.

Usage:
  from coordination_crypto import encrypt_message, decrypt_message
  blob = encrypt_message("secret text")
  text = decrypt_message(blob)
"""

import base64
import hashlib
import hmac
import os
import sys
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

_ENV_VAR = "COORDINATION_KEY"


def _get_key() -> bytes:
    """Load the shared 32-byte key from env var (base64-encoded)."""
    raw = os.environ.get(_ENV_VAR)
    if not raw:
        raise EnvironmentError(
            f"{_ENV_VAR} not set. Generate one with:\n"
            "  python3 -c \"import base64,os; print(base64.b64encode(os.urandom(32)).decode())\""
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise ValueError(f"{_ENV_VAR} must decode to exactly 32 bytes, got {len(key)}")
    return key


def generate_key_b64() -> str:
    """Generate a new random 32-byte key, return base64-encoded."""
    return base64.b64encode(os.urandom(32)).decode()


# ---------------------------------------------------------------------------
# AES-256-GCM Encrypt / Decrypt
# ---------------------------------------------------------------------------

def _aes_encrypt(plaintext: str, key: bytes) -> bytes:
    """Encrypt plaintext with AES-256-GCM. Returns nonce (12) || ciphertext+tag."""
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def _aes_decrypt(blob: bytes, key: bytes) -> str:
    """Decrypt nonce (12) || ciphertext+tag. Returns plaintext string."""
    if len(blob) < 13:
        raise ValueError("Ciphertext too short")
    nonce = blob[:12]
    ct = blob[12:]
    aesgcm = AESGCM(key)
    pt = aesgcm.decrypt(nonce, ct, None)
    return pt.decode("utf-8")


# ---------------------------------------------------------------------------
# HMAC-SHA256 Sign / Verify
# ---------------------------------------------------------------------------

def _sign(data: bytes, key: bytes) -> bytes:
    """Return 32-byte HMAC-SHA256 of data."""
    return hmac.new(key, data, hashlib.sha256).digest()


def _verify(data: bytes, signature: bytes, key: bytes) -> bool:
    """Constant-time verify of HMAC-SHA256."""
    expected = hmac.new(key, data, hashlib.sha256).digest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# High-level: encrypt_message / decrypt_message (encrypt-then-sign)
# ---------------------------------------------------------------------------

def encrypt_message(plaintext: str, agent: str = "OWL") -> str:
    """
    Encrypt + sign a message. Returns a single base64 string for COORDINATION.md.

    Format: base64( HMAC(32) || nonce(12) || ciphertext+tag )
    Prepends agent label + timestamp in plaintext before encrypting.
    """
    key = _get_key()
    import json, datetime

    envelope = json.dumps({
        "agent": agent,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "msg": plaintext,
    })

    encrypted_blob = _aes_encrypt(envelope, key)           # nonce || ct+tag
    mac = _sign(encrypted_blob, key)                       # HMAC over encrypted blob
    full = mac + encrypted_blob                             # HMAC || nonce || ct+tag
    return base64.b64encode(full).decode()


def decrypt_message(b64_text: str) -> dict:
    """
    Verify + decrypt a base64-encoded message from COORDINATION.md.
    Returns dict with keys: agent, ts, msg
    """
    key = _get_key()
    full = base64.b64decode(b64_text.strip())

    mac = full[:32]
    encrypted_blob = full[32:]

    if not _verify(encrypted_blob, mac, key):
        raise ValueError("HMAC verification failed — message tampered or wrong key")

    envelope_json = _aes_decrypt(encrypted_blob, key)
    import json
    return json.loads(envelope_json)


# ---------------------------------------------------------------------------
# COORDINATION.md helpers — read/write encrypted message blocks
# ---------------------------------------------------------------------------

_ENC_BLOCK_START = "```enc"
_ENC_BLOCK_END = "```"


def read_encrypted_blocks(coord_path: str) -> list[dict]:
    """
    Parse a COORDINATION.md file and extract all ```enc ... ``` blocks.
    Returns a list of decrypted message dicts (agent, ts, msg).
    Falls back gracefully if COORDINATION_KEY is not set (returns empty list).
    """
    if not os.path.isfile(coord_path):
        return []

    try:
        _get_key()  # validate key exists
    except EnvironmentError:
        return []

    with open(coord_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = []
    i = 0
    while i < len(content):
        start = content.find(_ENC_BLOCK_START, i)
        if start == -1:
            break
        end = content.find(_ENC_BLOCK_END, start + len(_ENC_BLOCK_START))
        if end == -1:
            break
        b64_data = content[start + len(_ENC_BLOCK_START):end].strip()
        if b64_data:
            try:
                msg = decrypt_message(b64_data)
                blocks.append(msg)
            except Exception as e:
                blocks.append({"agent": "UNKNOWN", "ts": "", "msg": f"[decrypt error: {e}]"})
        i = end + len(_ENC_BLOCK_END)
    return blocks


def append_encrypted_message(coord_path: str, plaintext: str, agent: str = "OWL") -> str:
    """
    Encrypt a message and append it as a ```enc ... ``` block to COORDINATION.md.
    Returns the base64 string written (for verification).
    """
    if not os.path.isfile(coord_path):
        raise FileNotFoundError(coord_path)

    b64 = encrypt_message(plaintext, agent=agent)
    block = f"\n{_ENC_BLOCK_START}\n{b64}\n{_ENC_BLOCK_END}\n"

    with open(coord_path, "a", encoding="utf-8") as f:
        f.write(block)
    return b64


# ---------------------------------------------------------------------------
# CLI — quick self-test when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("🔐 Coordination Crypto — self-test\n")

    # 1. Generate a test key
    test_key = generate_key_b64()
    print(f"  Test key (base64): {test_key[:20]}...")
    os.environ[_ENV_VAR] = test_key

    # 2. Encrypt a message
    test_msg = "Hello from OWL — secure channel test"
    b64 = encrypt_message(test_msg, agent="OWL")
    print(f"  Encrypted (base64): {b64[:40]}... ({len(b64)} chars)")

    # 3. Decrypt the message
    result = decrypt_message(b64)
    assert result["msg"] == test_msg
    assert result["agent"] == "OWL"
    print(f"  Decrypted: agent={result['agent']} ts={result['ts']} msg={result['msg']}")
    print("  ✅ AES-256-GCM + HMAC-SHA256 encrypt/decrypt verified")

    # 4. Tamper detection
    import copy
    tampered = bytearray(base64.b64decode(b64))
    tampered[40] ^= 0xFF  # flip a bit in the ciphertext
    try:
        decrypt_message(base64.b64encode(bytes(tampered)).decode())
        print("  ❌ Tamper detection FAILED")
    except ValueError:
        print("  ✅ Tamper correctly detected (HMAC verify failed)")

    # 5. COORDINATION.md round-trip
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Test Coordination\n")
        tmp_path = f.name

    append_encrypted_message(tmp_path, "Test message 1", agent="OWL")
    append_encrypted_message(tmp_path, "Reply from Self", agent="Self")
    blocks = read_encrypted_blocks(tmp_path)
    assert len(blocks) == 2
    assert blocks[0]["msg"] == "Test message 1"
    assert blocks[0]["agent"] == "OWL"
    assert blocks[1]["msg"] == "Reply from Self"
    assert blocks[1]["agent"] == "Self"
    os.unlink(tmp_path)
    print("  ✅ COORDINATION.md read/write round-trip verified")

    print("\n🔐 All self-tests PASSED ✅")

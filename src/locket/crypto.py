"""Encryption for `secret`-level memories (spec §9.3): the server never sees plaintext.

Pluggable and OPTIONAL. The primitives live in the `cryptography` package, declared as
the extra `locket-memory[secret]`; without it, secret memories stay rejected (the prior
behaviour), so the core keeps its zero-dependency promise.

Scheme (a sealed box to one or more X25519 recipients):
  - a fresh random 32-byte data key (DEK) encrypts the plaintext with ChaCha20-Poly1305;
  - one ephemeral X25519 keypair; for each recipient, ECDH(eph_priv, recip_pub) → HKDF-SHA256
    → a wrap key that ChaCha20-Poly1305-wraps the DEK;
  - the container stores the ephemeral public key once plus each recipient's wrapped DEK,
    so any one recipient's identity can open it.

This is Locket's own container (`locket-enc-v1`), not yet age wire-format; cross-implementation
age interop is a tracked follow-up (SPEC §9.3 names age). The security property that matters for
the cloud — secret bodies are unreadable by the server/Gitea — holds today.
"""
from __future__ import annotations

import base64
import json
import os
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from dataclasses import dataclass

CONTAINER_VERSION = "locket-enc-v1"
RECIPIENTS_PATH = ".pmf/recipients.toml"


class EncryptionUnavailable(RuntimeError):
    """Raised when secret encryption is requested but `cryptography` isn't installed."""


def _crypto():
    """Lazy import so the core stays dependency-free; raises a clear error if missing."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric.x25519 import (  # noqa: F401
            X25519PrivateKey, X25519PublicKey)
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305  # noqa: F401
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: F401
        import cryptography.hazmat.primitives.hashes as _h
        return True
    except ImportError as e:
        raise EncryptionUnavailable(
            "secret 级别记忆需要加密支持：pip install 'locket-memory[secret]'") from e


def available() -> bool:
    try:
        _crypto()
        return True
    except EncryptionUnavailable:
        return False


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def generate_identity() -> tuple[str, str]:
    """Return (public_key, private_key) as base64 strings. Keep the private key secret."""
    _crypto()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (Encoding, NoEncryption,
                                                              PrivateFormat, PublicFormat)
    priv = X25519PrivateKey.generate()
    pub = priv.public_key()
    pub_raw = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    priv_raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return _b64e(pub_raw), _b64e(priv_raw)


def _hkdf(shared: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=CONTAINER_VERSION.encode("ascii")).derive(shared)


def encrypt(plaintext: bytes, recipient_pubkeys: list[str]) -> bytes:
    """Seal `plaintext` to each recipient (base64 X25519 public key). Returns a JSON container."""
    _crypto()
    from cryptography.hazmat.primitives.asymmetric.x25519 import (X25519PrivateKey,
                                                                  X25519PublicKey)
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    if not recipient_pubkeys:
        raise ValueError("没有加密接收者（.pmf/recipients.toml 为空）")
    dek = ChaCha20Poly1305.generate_key()
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    wraps = []
    for pub_b64 in recipient_pubkeys:
        recip = X25519PublicKey.from_public_bytes(_b64d(pub_b64))
        shared = eph.exchange(recip)
        wrapkey = _hkdf(shared, salt=eph_pub)
        nonce = os.urandom(12)
        wrapped = ChaCha20Poly1305(wrapkey).encrypt(nonce, dek, None)
        wraps.append({"to": pub_b64, "nonce": _b64e(nonce), "dek": _b64e(wrapped)})
    pnonce = os.urandom(12)
    body = ChaCha20Poly1305(dek).encrypt(pnonce, plaintext, None)
    container = {"v": CONTAINER_VERSION, "epk": _b64e(eph_pub),
                 "recipients": wraps, "nonce": _b64e(pnonce), "ct": _b64e(body)}
    return json.dumps(container, ensure_ascii=False).encode("utf-8")


def decrypt(container_bytes: bytes, private_key: str) -> bytes:
    """Open a container with one recipient's base64 X25519 private key."""
    _crypto()
    from cryptography.hazmat.primitives.asymmetric.x25519 import (X25519PrivateKey,
                                                                  X25519PublicKey)
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    c = json.loads(container_bytes.decode("utf-8"))
    if c.get("v") != CONTAINER_VERSION:
        raise ValueError(f"未知的加密容器版本：{c.get('v')}")
    priv = X25519PrivateKey.from_private_bytes(_b64d(private_key))
    epk = X25519PublicKey.from_public_bytes(_b64d(c["epk"]))
    shared = priv.exchange(epk)
    wrapkey = _hkdf(shared, salt=_b64d(c["epk"]))
    dek = None
    for w in c["recipients"]:
        try:
            dek = ChaCha20Poly1305(wrapkey).decrypt(_b64d(w["nonce"]), _b64d(w["dek"]), None)
            break
        except Exception:  # noqa: BLE001 — wrong recipient slot; try the next
            continue
    if dek is None:
        raise ValueError("这把私钥不是任何接收者，无法解密")
    return ChaCha20Poly1305(dek).decrypt(_b64d(c["nonce"]), _b64d(c["ct"]), None)


def is_encrypted(body: str | bytes) -> bool:
    data = body if isinstance(body, bytes) else body.encode("utf-8")
    return CONTAINER_VERSION.encode("ascii") in data[:64]


@dataclass
class Recipients:
    keys: list[str]

    @classmethod
    def load(cls, toml_bytes: bytes | None) -> "Recipients":
        """Parse .pmf/recipients.toml. Accepts either form:
            recipients = ["<pub1>", "<pub2>"]
        or  [[recipient]] name=.. key=..
        """
        if not toml_bytes:
            return cls([])
        data = tomllib.loads(toml_bytes.decode("utf-8"))
        keys: list[str] = list(data.get("recipients") or [])
        for r in data.get("recipient") or []:
            if isinstance(r, dict) and r.get("key"):
                keys.append(str(r["key"]))
        return cls(keys)

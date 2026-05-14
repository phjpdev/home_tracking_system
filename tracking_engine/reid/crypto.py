"""AES-GCM encryption for face embeddings at rest (GDPR Article 9 safeguard).

Key file format
---------------
A single 32-byte (256-bit) raw key, file mode 0600. Generate with:

    python -m tracking_engine.reid.crypto --generate /etc/tracking-engine/secret.key

On read, embeddings are returned as plain ``np.ndarray(float32)``.
On write, the helper encrypts to a `bytes` blob with the format:

    nonce(12) | ciphertext | tag(16)

If ``cryptography`` is not installed, the helpers raise; the gallery
falls back to plaintext storage with a loud warning so the operator
notices.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np


def _require_aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: WPS433
        return AESGCM
    except ImportError as exc:
        raise RuntimeError(
            "AES-GCM requires the 'cryptography' package: pip install cryptography"
        ) from exc


class FaceEmbeddingCrypto:
    """Wraps an AES-GCM cipher loaded from a 32-byte raw key file."""

    NONCE_BYTES = 12

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("face embedding key must be 32 bytes (256-bit AES key)")
        AESGCM = _require_aesgcm()
        self._aes = AESGCM(key)

    @classmethod
    def from_key_path(cls, path: Path) -> "FaceEmbeddingCrypto":
        with open(path, "rb") as fh:
            data = fh.read()
        return cls(data)

    def encrypt(self, vec: np.ndarray) -> bytes:
        if vec.dtype != np.float32:
            vec = vec.astype(np.float32, copy=False)
        body = vec.tobytes()
        nonce = os.urandom(self.NONCE_BYTES)
        ct = self._aes.encrypt(nonce, body, associated_data=b"face_embedding_v1")
        return nonce + ct

    def decrypt(self, blob: bytes, dim: int) -> np.ndarray:
        if len(blob) < self.NONCE_BYTES + 16:
            raise ValueError("ciphertext too short")
        nonce, ct = blob[: self.NONCE_BYTES], blob[self.NONCE_BYTES :]
        pt = self._aes.decrypt(nonce, ct, associated_data=b"face_embedding_v1")
        vec = np.frombuffer(pt, dtype=np.float32)
        if vec.size != dim:
            raise ValueError(f"decrypted vector size {vec.size} != expected {dim}")
        return vec.copy()


def maybe_load_crypto(key_path: Optional[Path]) -> Optional[FaceEmbeddingCrypto]:
    """Best-effort loader: returns None if disabled / missing / unsupported."""
    if key_path is None:
        return None
    if not Path(key_path).is_file():
        print(
            f"[reid-crypto] face encryption key not found at {key_path}; "
            "face embeddings will be stored UNENCRYPTED. "
            "Generate one with: python -m tracking_engine.reid.crypto --generate "
            f"{key_path}",
            file=sys.stderr,
        )
        return None
    try:
        return FaceEmbeddingCrypto.from_key_path(Path(key_path))
    except Exception as exc:
        print(f"[reid-crypto] failed to load key: {exc}", file=sys.stderr)
        return None


def _generate_key(path: Path) -> int:
    if path.exists():
        print(f"[reid-crypto] refusing to overwrite existing key at {path}", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    with open(path, "wb") as fh:
        fh.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"[reid-crypto] wrote 32-byte AES-256 key to {path} (mode 0600)")
    return 0


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Manage face-embedding encryption key.")
    ap.add_argument("--generate", type=Path, help="path to new key file")
    args = ap.parse_args()
    if args.generate:
        return _generate_key(args.generate)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Authenticated encryption for backup archives (``*.tar.gz.enc``).

A backup holds ``secret.key`` and ``owner.token``; encrypting it makes the file safe to put on a USB
stick or a cloud drive. Format (version 1), all integers big-endian::

    magic        8   b"FOSBKENC"
    version      1   1
    kdf          1   1 = scrypt
    log2(N)      1   scrypt cost (default 17 = 128 MiB)
    r            1   scrypt block size (default 8)
    p            1   scrypt parallelism (default 1)
    cipher       1   1 = AES-256-GCM
    chunk size   4   plaintext bytes per chunk (default 1 MiB)
    salt        16   random, per file
    nonce base   8   random, per file
    key check   16   HMAC-SHA256 over the 42 bytes above (truncated): tells "wrong passphrase" apart
                     from "damaged file" without revealing anything an attacker could not already
                     test against the first chunk
    chunks...        each: AES-256-GCM(plaintext chunk) + 16-byte tag

Key: ``scrypt(NFKC(passphrase), salt)`` -> 64 bytes: 32 for AES-256-GCM, 32 for the key check.
Chunking is the STREAM construction: nonce = nonce base || 32-bit chunk counter, and the additional
authenticated data of every chunk is the whole header plus a flag that is 1 only for the last chunk.
That authenticates the header (KDF parameters cannot be downgraded), the order of the chunks and the
end of the file: dropping, repeating, swapping or appending chunks, truncating the file, or changing any
byte fails authentication. All chunks except the last hold exactly ``chunk size`` bytes; memory use is
one chunk, so an archive of any size streams.

Nothing here touches the disk or the passphrase beyond the file objects it is given.
"""

from __future__ import annotations

import io
import os
import struct
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"FOSBKENC"
VERSION = 1
KDF_SCRYPT = 1
CIPHER_AES256GCM = 1
DEFAULT_LOG2_N = 17
DEFAULT_R = 8
DEFAULT_P = 1
DEFAULT_CHUNK = 1024 * 1024
TAG_LEN = 16
CHECK_LEN = 16
_FIXED = struct.Struct(">8sBBBBBBI16s8s")
HEADER_LEN = _FIXED.size + CHECK_LEN
MIN_PASSPHRASE_CHARS = 8

# Bounds applied to a header read from a file (a hostile file must not make us allocate gigabytes).
_MAX_CHUNK = 64 * 1024 * 1024
_MIN_CHUNK = 1024
_MAX_SCRYPT_BYTES = 1 << 30  # 128 * r * N


class CryptoFailure(Exception):
    """Encryption/decryption failed. ``code`` is one of: ``bad_format``, ``wrong_passphrase``,
    ``tampered``, ``unsupported``."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def is_encrypted_header(first_bytes: bytes) -> bool:
    return first_bytes[: len(MAGIC)] == MAGIC


def _passphrase_bytes(passphrase: str) -> bytes:
    return unicodedata.normalize("NFKC", passphrase).encode("utf-8")


def _derive(passphrase: str, salt: bytes, log2_n: int, r: int, p: int) -> tuple[bytes, bytes]:
    material = Scrypt(salt=salt, length=64, n=1 << log2_n, r=r, p=p).derive(
        _passphrase_bytes(passphrase)
    )
    return material[:32], material[32:]


def _key_check(mac_key: bytes, fixed_header: bytes) -> bytes:
    mac = hmac.HMAC(mac_key, hashes.SHA256())
    mac.update(b"foundry-backup-key-check\x00" + fixed_header)
    return mac.finalize()[:CHECK_LEN]


def _nonce(base: bytes, counter: int) -> bytes:
    if counter >= 1 << 32:
        raise CryptoFailure("The archive is too large for this format", "unsupported")
    return base + counter.to_bytes(4, "big")


class EncryptWriter(io.RawIOBase):
    """Write-only file object: plaintext in, an encrypted stream out to ``sink``.

    Call :meth:`finish` once everything is written (it writes the final chunk); it does not close
    ``sink``."""

    def __init__(
        self,
        sink,
        passphrase: str,
        *,
        log2_n: int = DEFAULT_LOG2_N,
        r: int = DEFAULT_R,
        p: int = DEFAULT_P,
        chunk_size: int = DEFAULT_CHUNK,
    ) -> None:
        super().__init__()
        _check_params(log2_n, r, p, chunk_size)
        self._sink = sink
        self._chunk = chunk_size
        salt, self._base = os.urandom(16), os.urandom(8)
        key, mac_key = _derive(passphrase, salt, log2_n, r, p)
        fixed = _FIXED.pack(
            MAGIC, VERSION, KDF_SCRYPT, log2_n, r, p, CIPHER_AES256GCM, chunk_size, salt, self._base
        )
        self._header = fixed + _key_check(mac_key, fixed)
        self._aead = AESGCM(key)
        self._buf = bytearray()
        self._counter = 0
        self._finished = False
        sink.write(self._header)

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        if self._finished:
            raise ValueError("write to a finished encrypted stream")
        n = len(data)
        self._buf += data
        # Keep one full chunk back: whether it is the last one is only known at finish().
        while len(self._buf) > self._chunk:
            self._emit(bytes(self._buf[: self._chunk]), final=False)
            del self._buf[: self._chunk]
        return n

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._emit(bytes(self._buf), final=True)
        self._buf.clear()

    def _emit(self, plain: bytes, *, final: bool) -> None:
        aad = self._header + (b"\x01" if final else b"\x00")
        self._sink.write(self._aead.encrypt(_nonce(self._base, self._counter), plain, aad))
        self._counter += 1


class DecryptReader(io.RawIOBase):
    """Read-only file object over an encrypted stream; yields plaintext, one authenticated chunk at
    a time. Raises :class:`CryptoFailure` on a wrong passphrase (at construction) or any damage
    (when the damaged chunk is reached). Call :meth:`drain` after the consumer is done so the end of
    the stream -- and with it truncation and appended data -- is authenticated too."""

    def __init__(self, source, passphrase: str) -> None:
        super().__init__()
        header = _read_up_to(source, HEADER_LEN)
        if len(header) < HEADER_LEN or not is_encrypted_header(header):
            raise CryptoFailure("This is not an encrypted Foundry AI OS backup", "bad_format")
        fixed = header[: _FIXED.size]
        _, version, kdf, log2_n, r, p, cipher, chunk, salt, base = _FIXED.unpack(fixed)
        if version != VERSION or kdf != KDF_SCRYPT or cipher != CIPHER_AES256GCM:
            raise CryptoFailure(
                f"Unsupported backup encryption format (version {version}); "
                "use a newer Foundry AI OS to read it",
                "unsupported",
            )
        try:
            _check_params(log2_n, r, p, chunk)
        except CryptoFailure as exc:
            raise CryptoFailure(
                "The backup's encryption header is not valid (damaged or modified)", "tampered"
            ) from exc
        key, mac_key = _derive(passphrase, salt, log2_n, r, p)
        expected = _key_check(mac_key, fixed)
        if not _consteq(expected, header[_FIXED.size :]):
            raise CryptoFailure(
                "Wrong passphrase (or the backup's header is damaged). Nothing was restored.",
                "wrong_passphrase",
            )
        self._source = source
        self._header = header
        self._base = base
        self._aead = AESGCM(key)
        self._block = chunk + TAG_LEN
        self._counter = 0
        self._plain = memoryview(b"")
        self._current = _read_up_to(source, self._block)
        self._done = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        out = memoryview(buffer).cast("B")
        while len(self._plain) == 0:
            if self._done:
                return 0
            self._advance()
        n = min(len(out), len(self._plain))
        out[:n] = self._plain[:n]
        self._plain = self._plain[n:]
        return n

    def _advance(self) -> None:
        current = self._current
        if len(current) < TAG_LEN:
            raise CryptoFailure(
                "The encrypted backup is truncated. Nothing was restored.", "tampered"
            )
        following = _read_up_to(self._source, self._block)
        final = not following
        aad = self._header + (b"\x01" if final else b"\x00")
        try:
            plain = self._aead.decrypt(_nonce(self._base, self._counter), current, aad)
        except InvalidTag:
            raise CryptoFailure(
                "The encrypted backup failed authentication: it is damaged or has been modified. "
                "Nothing was restored.",
                "tampered",
            ) from None
        self._counter += 1
        self._plain = memoryview(plain)
        self._current = following
        self._done = final

    def drain(self) -> None:
        """Read (and so authenticate) everything that is left."""
        scratch = bytearray(DEFAULT_CHUNK)
        while self.readinto(scratch):
            pass


def _check_params(log2_n: int, r: int, p: int, chunk: int) -> None:
    if not (10 <= log2_n <= 24 and 1 <= r <= 32 and 1 <= p <= 16):
        raise CryptoFailure("Invalid scrypt parameters", "bad_format")
    if 128 * r * (1 << log2_n) > _MAX_SCRYPT_BYTES:
        raise CryptoFailure("The scrypt cost in this file is unreasonably high", "bad_format")
    if not (_MIN_CHUNK <= chunk <= _MAX_CHUNK):
        raise CryptoFailure("Invalid chunk size", "bad_format")


def _read_up_to(source, n: int) -> bytes:
    """Like ``read(n)`` but keeps reading until ``n`` bytes or end of file."""
    parts: list[bytes] = []
    got = 0
    while got < n:
        piece = source.read(n - got)
        if not piece:
            break
        parts.append(piece)
        got += len(piece)
    return parts[0] if len(parts) == 1 else b"".join(parts)


def _consteq(a: bytes, b: bytes) -> bool:
    import hmac as _hmac

    return _hmac.compare_digest(a, b)

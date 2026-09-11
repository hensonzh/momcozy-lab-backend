from __future__ import annotations

import base64
import hashlib
import hmac
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

_PASSWORD_HASH = PasswordHash.recommended()


PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    return _PASSWORD_HASH.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    if encoded_hash and encoded_hash.startswith("$argon2"):
        try:
            return _PASSWORD_HASH.verify(password, encoded_hash)
        except (ValueError, TypeError, UnknownHashError):
            return False
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded_hash.split("$", 3)
        iterations = int(iterations_raw)
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (TypeError, ValueError):
        return False

    if algorithm != PBKDF2_ALGORITHM or not 1 <= iterations <= 2_000_000:
        return False

    actual = _pbkdf2(password=password, salt=salt, iterations=iterations)
    return hmac.compare_digest(actual, expected)


def _pbkdf2(*, password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


# Comparable work for unknown identities prevents a fast account-existence oracle.
DUMMY_PASSWORD_HASH = hash_password("non-user-dummy-password")

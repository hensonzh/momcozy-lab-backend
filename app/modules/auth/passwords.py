from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _pbkdf2(password=password, salt=salt, iterations=PBKDF2_ITERATIONS)
    return "$".join(
        [
            PBKDF2_ALGORITHM,
            str(PBKDF2_ITERATIONS),
            _b64(salt),
            _b64(digest),
        ]
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded_hash.split("$", 3)
        iterations = int(iterations_raw)
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (TypeError, ValueError):
        return False

    if algorithm != PBKDF2_ALGORITHM or iterations <= 0:
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

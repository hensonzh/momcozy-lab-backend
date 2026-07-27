from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import ExpiredSignatureError, InvalidTokenError

from ...core.errors import ApiError
from ...core.settings import Settings, load_auth_jwt_private_key
from .current_user import CurrentUser


ACCESS_TOKEN_TTL = timedelta(minutes=15)
JWT_SIGNING_ALGORITHM = "RS256"
AUTH_JWT_TOKEN_VERSION = 1
MAX_TOKEN_ID_LENGTH = 255
MAX_AUTHORITY_VALUES = 64
MAX_AUTHORITY_VALUE_LENGTH = 128
REQUIRED_ACCESS_TOKEN_CLAIMS = [
    "iss",
    "aud",
    "sub",
    "sid",
    "jti",
    "iat",
    "exp",
    "token_version",
    "roles",
    "permissions",
]


@dataclass(frozen=True)
class JwtKeyMaterial:
    private_key: rsa.RSAPrivateKey
    public_key: rsa.RSAPublicKey
    public_jwk: dict[str, str]
    kid: str


def authenticate_access_token(token: str, settings: Settings) -> CurrentUser:
    _require_auth_configuration(settings)
    material = _jwt_key_material(settings.auth_jwt_private_key_b64)

    try:
        header = pyjwt.get_unverified_header(token)
        if (
            header.get("alg") != JWT_SIGNING_ALGORITHM
            or header.get("typ") != "JWT"
            or header.get("kid") != material.kid
        ):
            raise InvalidTokenError("Access token header is invalid")
        payload = pyjwt.decode(
            token,
            material.public_key,
            algorithms=[JWT_SIGNING_ALGORITHM],
            issuer=settings.auth_jwt_issuer,
            audience=settings.auth_jwt_product_audience,
            options={"require": REQUIRED_ACCESS_TOKEN_CLAIMS},
        )
    except ExpiredSignatureError as exc:
        raise ApiError(code="authentication_required", message="Access token expired.", status=401) from exc
    except InvalidTokenError as exc:
        raise ApiError(code="authentication_required", message="Access token is invalid.", status=401) from exc

    subject = _required_string_claim(
        payload.get("sub"),
        message="Access token subject is invalid.",
    )
    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise ApiError(code="authentication_required", message="Access token subject is invalid.", status=401) from exc
    session = _required_string_claim(
        payload.get("sid"),
        message="Access token session is invalid.",
    )
    try:
        session_id = UUID(session)
    except ValueError as exc:
        raise ApiError(code="authentication_required", message="Access token session is invalid.", status=401) from exc
    token_id = _required_string_claim(
        payload.get("jti"),
        message="Access token is invalid.",
        max_length=MAX_TOKEN_ID_LENGTH,
    )
    token_version = payload.get("token_version")
    if (
        not token_id
        or type(token_version) is not int
        or token_version != AUTH_JWT_TOKEN_VERSION
    ):
        raise ApiError(code="authentication_required", message="Access token is invalid.", status=401)
    roles = _string_array_claim(payload, "roles")
    permissions = _string_array_claim(payload, "permissions")

    return CurrentUser(
        user_id=user_id,
        subject=subject,
        session_id=str(session_id),
        token_id=token_id,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
    )


def issue_access_token(
    *,
    user_id: UUID,
    session_id: UUID,
    settings: Settings,
    roles: frozenset[str] = frozenset(),
    permissions: frozenset[str] = frozenset(),
    clock: Callable[[], datetime] | None = None,
) -> tuple[str, int]:
    _require_auth_configuration(settings)
    material = _jwt_key_material(settings.auth_jwt_private_key_b64)

    now = (clock or _utcnow)()
    expires_at = now + ACCESS_TOKEN_TTL
    payload: dict[str, Any] = {
        "iss": settings.auth_jwt_issuer,
        "aud": [
            settings.auth_jwt_product_audience,
            settings.auth_jwt_runtime_audience,
        ],
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": f"at_{uuid4().hex}",
        "roles": sorted(roles),
        "permissions": sorted(permissions),
        "iat": now,
        "exp": expires_at,
        "token_version": AUTH_JWT_TOKEN_VERSION,
    }

    token = pyjwt.encode(
        payload,
        material.private_key,
        algorithm=JWT_SIGNING_ALGORITHM,
        headers={"kid": material.kid, "typ": "JWT"},
    )
    return token, int(ACCESS_TOKEN_TTL.total_seconds())


def public_jwks(settings: Settings) -> dict[str, list[dict[str, str]]]:
    _require_auth_configuration(settings)
    material = _jwt_key_material(settings.auth_jwt_private_key_b64)
    return {"keys": [dict(material.public_jwk)]}


def jwt_key_id(settings: Settings) -> str:
    _require_auth_configuration(settings)
    return _jwt_key_material(settings.auth_jwt_private_key_b64).kid


@lru_cache(maxsize=8)
def _jwt_key_material(private_key_b64: str) -> JwtKeyMaterial:
    private_key = load_auth_jwt_private_key(private_key_b64)
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    exponent = _base64url_uint(public_numbers.e)
    modulus = _base64url_uint(public_numbers.n)
    thumbprint_input = json.dumps(
        {"e": exponent, "kty": "RSA", "n": modulus},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    kid = _base64url(hashlib.sha256(thumbprint_input).digest())
    return JwtKeyMaterial(
        private_key=private_key,
        public_key=public_key,
        public_jwk={
            "alg": JWT_SIGNING_ALGORITHM,
            "e": exponent,
            "kid": kid,
            "kty": "RSA",
            "n": modulus,
            "use": "sig",
        },
        kid=kid,
    )


def _require_auth_configuration(settings: Settings) -> None:
    if not all(
        (
            settings.auth_jwt_private_key_b64,
            settings.auth_jwt_issuer,
            settings.auth_jwt_product_audience,
            settings.auth_jwt_runtime_audience,
        )
    ):
        raise ApiError(
            code="auth_not_configured",
            message="Authentication is not configured.",
            status=500,
        )


def _base64url_uint(value: int) -> str:
    width = max(1, (value.bit_length() + 7) // 8)
    return _base64url(value.to_bytes(width, "big"))


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _string_array_claim(payload: dict[str, Any], claim: str) -> list[str]:
    value = payload.get(claim)
    if not isinstance(value, list) or len(value) > MAX_AUTHORITY_VALUES:
        raise ApiError(code="authentication_required", message="Access token is invalid.", status=401)
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ApiError(code="authentication_required", message="Access token is invalid.", status=401)
        authority = item.strip()
        if not authority or len(authority) > MAX_AUTHORITY_VALUE_LENGTH:
            raise ApiError(code="authentication_required", message="Access token is invalid.", status=401)
        normalized.append(authority)
    return normalized


def _required_string_claim(
    value: Any,
    *,
    message: str,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise ApiError(code="authentication_required", message=message, status=401)
    normalized = value.strip()
    if not normalized or (max_length is not None and len(normalized) > max_length):
        raise ApiError(code="authentication_required", message=message, status=401)
    return normalized


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

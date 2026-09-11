from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import jwt
from functools import lru_cache


GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


@dataclass(frozen=True)
class GoogleClaims:
    subject: str
    email: str
    email_verified: bool
    display_name: str = ""
    avatar_url: str = ""


class GoogleTokenVerifier(Protocol):
    async def verify(self, *, id_token: str, audience: str) -> GoogleClaims: ...


class GoogleOidcTokenVerifier:
    async def verify(self, *, id_token: str, audience: str) -> GoogleClaims:
        return await asyncio.to_thread(self._verify_sync, id_token, audience)

    @staticmethod
    def _verify_sync(id_token: str, audience: str) -> GoogleClaims:
        client = _google_jwks()
        signing_key = client.get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, signing_key, algorithms=["RS256"], audience=audience, issuer=list(GOOGLE_ISSUERS),
            options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        subject, email = str(claims.get("sub") or "").strip(), str(claims.get("email") or "").strip().lower()
        if not subject or not email or claims.get("email_verified") is not True:
            raise ValueError("Google identity is not verified.")
        return GoogleClaims(subject=subject, email=email, email_verified=True)


@lru_cache(maxsize=1)
def _google_jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", timeout=5, lifespan=300)

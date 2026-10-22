"""Product-side short-lived, purpose-bound service identity for specialty jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import jwt

SpecialtyPurpose = Literal["stool.analyze"]
SpecialtyMarket = Literal["CN", "US"]
AUDIENCE = "momcozy-stool-specialty-v1"
ISSUER = "momcozy-product-backend"


def mint_specialty_service_token(
    *, secret: str, analysis_id: UUID, market: SpecialtyMarket, purpose: SpecialtyPurpose,
    now: datetime | None = None,
) -> str:
    if len(secret.encode("utf-8")) < 32 or market not in ("CN", "US") or purpose != "stool.analyze":
        raise ValueError("Invalid specialty service identity configuration")
    issued = now or datetime.now(timezone.utc)
    if issued.tzinfo is None:
        raise ValueError("Service token requires an aware timestamp")
    issued_at = int(issued.timestamp())
    return jwt.encode({
        "iss": ISSUER, "aud": AUDIENCE, "sub": "product-backend",
        "iat": issued_at, "exp": issued_at + int(timedelta(seconds=60).total_seconds()),
        "jti": str(uuid4()), "purpose": purpose, "market": market,
        "analysis_id": str(analysis_id),
    }, secret, algorithm="HS256")

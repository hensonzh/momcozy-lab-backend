from __future__ import annotations

import base64
import hashlib
import json
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient

from app.factory import create_app
from app.modules.auth import issue_access_token
from tests.auth_key_material import auth_settings


def test_jwks_exposes_only_the_rs256_public_key_without_authentication() -> None:
    settings = auth_settings()
    client = TestClient(create_app(settings))

    response = client.get("/.well-known/jwks.json")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=300"
    document = response.json()
    assert len(document["keys"]) == 1
    jwk = document["keys"][0]
    assert set(jwk) == {"alg", "e", "kid", "kty", "n", "use"}
    assert jwk["alg"] == "RS256"
    assert jwk["kty"] == "RSA"
    assert jwk["use"] == "sig"
    assert response.headers["ETag"] == f'"{jwk["kid"]}"'


def test_jwks_kid_is_the_rfc7638_thumbprint_and_validates_issued_tokens() -> None:
    settings = auth_settings()
    client = TestClient(create_app(settings))
    jwk = client.get("/.well-known/jwks.json").json()["keys"][0]
    canonical = json.dumps(
        {"e": jwk["e"], "kty": jwk["kty"], "n": jwk["n"]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    expected_kid = base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).rstrip(b"=").decode()
    token, _expires_in = issue_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
    )

    header = jwt.get_unverified_header(token)
    payload = jwt.decode(
        token,
        jwt.algorithms.RSAAlgorithm.from_jwk(jwk),
        algorithms=["RS256"],
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_product_audience,
        options={
            "require": [
                "iss",
                "aud",
                "sub",
                "sid",
                "jti",
                "iat",
                "exp",
                "token_version",
            ]
        },
    )

    assert jwk["kid"] == expected_kid
    assert header["kid"] == expected_kid
    assert payload["token_version"] == 1

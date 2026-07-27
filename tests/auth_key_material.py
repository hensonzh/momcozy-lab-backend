from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.settings import Settings


def _private_key_b64(*, key_size: int) -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(private_key_pem).decode("ascii")


TEST_RSA_PRIVATE_KEY_B64 = _private_key_b64(key_size=2048)
TEST_RSA_1024_PRIVATE_KEY_B64 = _private_key_b64(key_size=1024)


def auth_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "auth_jwt_private_key_b64": TEST_RSA_PRIVATE_KEY_B64,
        "auth_jwt_issuer": "https://auth.test.momcozy.invalid",
        "auth_jwt_product_audience": "momcozy-product-api",
        "auth_jwt_runtime_audience": "momcozy-agent-runtime",
    }
    values.update(overrides)
    return Settings(**values)

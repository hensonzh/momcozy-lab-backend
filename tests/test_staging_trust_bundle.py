from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_calls_public_agent_over_verified_https() -> None:
    compose = (ROOT / "docker-compose.deploy.yml").read_text()
    template = (ROOT / "env/staging.env.example").read_text()
    assert "MOMCOZY_BACKEND_TRUST_BUNDLE_FILE" in compose
    assert "SSL_CERT_FILE: /run/momcozy/trust-bundle.pem" in compose
    assert "AGENT_RUNTIME_URL=https://agent-test.lute-momcozylab.luteos.cloud:8443" in template
    assert "MOMCOZY_BACKEND_TRUST_BUNDLE_FILE=/opt/momcozy-lab-staging/shared/trust-bundle.pem" in template
    assert "- ${MOMCOZY_BACKEND_TRUST_BUNDLE_FILE" in compose.split("  notification-worker:", 1)[1]


def test_b_uses_system_ca_without_reusing_a_internal_ca() -> None:
    compose = (ROOT / "docker-compose.us-east-uat.yml").read_text()
    template = (ROOT / "env/us-east-uat.env.example").read_text()
    assert "MOMCOZY_BACKEND_TRUST_BUNDLE_FILE=/etc/ssl/certs/ca-certificates.crt" in template
    assert "SSL_CERT_FILE: /run/momcozy/trust-bundle.pem" in compose
    assert "${MOMCOZY_BACKEND_TRUST_BUNDLE_FILE:-/etc/ssl/certs/ca-certificates.crt}" in compose

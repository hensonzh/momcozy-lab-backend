"""B ingress must use its own domains, loopback upstream and public TLS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "deploy/us-east-uat/nginx-backend.conf"


def test_b_backend_ingress_isolated_from_a() -> None:
    text = SITE.read_text()
    host = "backend-us-dev.lute-momcozylab.luteos.cloud"
    assert f"server_name {host};" in text
    assert "listen 443 ssl" in text
    assert "127.0.0.1:8101" in text
    assert f"/etc/letsencrypt/live/{host}/fullchain.pem" in text
    assert f"/etc/letsencrypt/live/{host}/privkey.pem" in text
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text
    assert "location ^~ /v1/model-assets/" in text and "access_log off;" in text
    assert "location = /v1/realtime-voice-session" in text
    assert "proxy_set_header Upgrade $http_upgrade;" in text
    assert "proxy_buffering off;" in text
    for forbidden in (
        "backend-test.lute-momcozylab", "agent-test.lute-momcozylab",
        "/opt/momcozy-lab-staging", "listen 8443", "0.0.0.0:8101",
        "ssl_verify off", "location /app/", "ssl_certificate /etc/nginx/tls/momcozy-lab-staging",
    ):
        assert forbidden not in text

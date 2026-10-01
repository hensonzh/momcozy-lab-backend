"""B ingress must use its own domains, loopback upstream and public TLS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "deploy/us-east-uat/nginx-backend.conf"


def test_b_backend_ingress_isolated_from_a() -> None:
    text = SITE.read_text()
    host = "backend-us-dev.lute-momcozylab.luteos.cloud"
    assert f"server_name {host};" in text
    assert "listen 443 ssl" in text
    assert "127.0.0.1:8001" in text
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
        "/opt/momcozy-lab-staging", "listen 8443", "0.0.0.0:8001",
        "ssl_verify off", "location /app/", "ssl_certificate /etc/nginx/tls/momcozy-lab-staging",
    ):
        assert forbidden not in text


def test_b_http_acme_ingress_isolated_and_denies_other_traffic() -> None:
    text = (ROOT / "deploy/us-east-uat/nginx-http-acme.conf").read_text()
    assert "listen 80 default_server;" in text
    assert "listen 80;" in text
    assert "backend-us-dev.lute-momcozylab.luteos.cloud" in text
    assert "agent-us-dev.lute-momcozylab.luteos.cloud" in text
    assert "location ^~ /.well-known/acme-challenge/" in text
    assert "root /var/www/momcozy-b-acme;" in text
    assert "try_files $uri =404;" in text
    assert "return 444;" in text
    assert "listen 443" not in text
    assert "proxy_pass" not in text


def test_b_certificate_renewal_reloads_only_valid_running_nginx() -> None:
    text = (ROOT / "deploy/us-east-uat/reload-nginx-after-renewal.sh").read_text()
    assert "systemctl is-active --quiet nginx" in text
    assert "nginx -t" in text
    assert "systemctl reload nginx" in text
    assert text.index("nginx -t") < text.index("systemctl reload nginx")
    assert "docker" not in text


def test_b_https_unknown_host_rejects_handshake() -> None:
    text = (ROOT / "deploy/us-east-uat/nginx-https-deny-unknown.conf").read_text()
    assert "listen 443 ssl default_server;" in text
    assert "ssl_reject_handshake on;" in text
    assert "access_log off;" in text
    assert "proxy_pass" not in text
    assert "ssl_certificate" not in text

"""Resend SMTP probe never prints credentials or sends mail by default."""

import json
import ssl

from scripts import check_resend_smtp as probe


class FakeSMTP:
    calls: list[object] = []
    error: Exception | None = None

    def __init__(self, host: str, port: int, *, timeout: int) -> None:
        self.calls.append(("connect", host, port, timeout))
        if self.error:
            raise self.error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.calls.append("close")

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name: str) -> bool:
        return name == "starttls"

    def starttls(self, *, context: ssl.SSLContext):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        self.calls.append("starttls")

    def login(self, user: str, password: str):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["From"], message["To"]))
        return {}


def test_default_checks_tls_only(monkeypatch, capsys):
    FakeSMTP.calls = []
    FakeSMTP.error = None
    monkeypatch.setattr(probe.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("AUTH_SMTP_PASSWORD", "secret-not-to-print")
    assert probe.main([]) == 0
    assert "starttls" in FakeSMTP.calls
    assert not any(isinstance(call, tuple) and call[0] in {"login", "send"} for call in FakeSMTP.calls)
    result = json.loads(capsys.readouterr().out)
    assert result == {"result": "ok", "stage": "starttls", "email_sent": False}


def test_explicit_send_uses_env_key_without_echo(monkeypatch, capsys):
    FakeSMTP.calls = []
    FakeSMTP.error = None
    monkeypatch.setattr(probe.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("AUTH_SMTP_PASSWORD", "secret-not-to-print")
    assert probe.main(["--send", "--to", "recipient@example.org"]) == 0
    assert ("login", "resend", "secret-not-to-print") in FakeSMTP.calls
    assert ("send", "noreply@mail-momcozy-uat.luteos.cloud", "recipient@example.org") in FakeSMTP.calls
    output = capsys.readouterr().out
    assert "secret-not-to-print" not in output
    assert "recipient@example.org" not in output
    assert json.loads(output) == {"result": "ok", "stage": "smtp_accepted", "email_sent": True}


def test_errors_do_not_expose_smtp_exception_or_credentials(monkeypatch, capsys):
    FakeSMTP.calls = []
    FakeSMTP.error = TimeoutError("secret-not-to-print recipient@example.org")
    monkeypatch.setattr(probe.smtplib, "SMTP", FakeSMTP)
    assert probe.main([]) == 1
    output = capsys.readouterr().out
    assert "secret-not-to-print" not in output
    assert "recipient@example.org" not in output
    assert json.loads(output) == {"result": "failed", "stage": "tcp", "reason": "timeout", "email_sent": False}
    FakeSMTP.error = None


def test_send_requires_recipient_and_key(monkeypatch, capsys):
    FakeSMTP.calls = []
    monkeypatch.setattr(probe.smtplib, "SMTP", FakeSMTP)
    monkeypatch.delenv("AUTH_SMTP_PASSWORD", raising=False)
    assert probe.main(["--send"]) == 2
    assert FakeSMTP.calls == []
    assert probe.main(["--send", "--to", "recipient@example.org"]) == 2
    assert FakeSMTP.calls == []
    assert "recipient@example.org" not in capsys.readouterr().out

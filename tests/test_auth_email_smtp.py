"""SMTP contract used by the Resend transactional-email integration."""

import asyncio
import ssl
from email.message import EmailMessage

from app.core.settings import Settings
from app.modules.auth import email as auth_email


def test_resend_smtp_delivery_uses_starttls_and_api_key(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, *, timeout: int) -> None:
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            calls.append(("close",))

        def starttls(self, *, context: ssl.SSLContext) -> None:
            assert isinstance(context, ssl.SSLContext)
            assert context.verify_mode == ssl.CERT_REQUIRED
            calls.append(("starttls",))

        def login(self, username: str, password: str) -> None:
            calls.append(("login", username, password))

        def send_message(self, message: EmailMessage) -> None:
            calls.append(("send", message["From"], message["To"], message["Subject"], message.get_content().strip()))

    monkeypatch.setattr(auth_email.smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        auth_email_from="no-reply@auth.example.test",
        auth_smtp_host="smtp.resend.com",
        auth_smtp_port=587,
        auth_smtp_username="resend",
        auth_smtp_password="fixture-api-key",
    )

    asyncio.run(auth_email.SmtpAuthEmailSender(settings).send(
        recipient="reviewer@example.test",
        subject="Verify your Momcozy email",
        body="Your Momcozy security code is: 12345678",
    ))

    assert calls == [
        ("connect", "smtp.resend.com", 587, 10),
        ("starttls",),
        ("login", "resend", "fixture-api-key"),
        ("send", "no-reply@auth.example.test", "reviewer@example.test", "Verify your Momcozy email", "Your Momcozy security code is: 12345678"),
        ("close",),
    ]


def test_resend_smtp_values_are_loaded_from_deployment_env(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_EMAIL_FROM", "no-reply@auth.example.test")
    monkeypatch.setenv("AUTH_SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("AUTH_SMTP_PORT", "587")
    monkeypatch.setenv("AUTH_SMTP_USERNAME", "resend")
    monkeypatch.setenv("AUTH_SMTP_PASSWORD", "fixture-api-key")

    settings = Settings.from_env()

    assert settings.auth_email_from == "no-reply@auth.example.test"
    assert settings.auth_smtp_host == "smtp.resend.com"
    assert settings.auth_smtp_port == 587
    assert settings.auth_smtp_username == "resend"
    assert settings.auth_smtp_password == "fixture-api-key"
    assert "fixture-api-key" not in repr(settings)

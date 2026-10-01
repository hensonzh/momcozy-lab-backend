"""Check B/UAT Resend SMTP connectivity; send only with explicit opt-in.

The optional send path reads AUTH_SMTP_PASSWORD from the process environment.
Do not pass a key on the command line or store it in the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

HOST = "smtp.resend.com"
PORT = 587
SENDER = "noreply@mail-momcozy-uat.luteos.cloud"


def _output(*, result: str, stage: str, email_sent: bool = False, reason: str | None = None) -> None:
    payload: dict[str, str | bool] = {"result": result, "stage": stage, "email_sent": email_sent}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Explicitly authenticate and submit one test email")
    parser.add_argument("--to", help="Recipient of the optional test email")
    args = parser.parse_args(argv)
    if args.to and not args.send:
        _output(result="invalid_config", stage="preflight", reason="send_required")
        return 2
    if args.send:
        recipient = (args.to or "").strip()
        if not recipient or "\n" in recipient or "\r" in recipient or parseaddr(recipient) != ("", recipient) or "@" not in recipient:
            _output(result="invalid_config", stage="preflight", reason="recipient_required")
            return 2
        key = os.getenv("AUTH_SMTP_PASSWORD", "")
        if not key:
            _output(result="invalid_config", stage="preflight", reason="key_required")
            return 2

    stage = "tcp"
    try:
        with smtplib.SMTP(HOST, PORT, timeout=10) as client:
            stage = "starttls"
            client.ehlo()
            if not client.has_extn("starttls"):
                _output(result="failed", stage=stage, reason="starttls_unavailable")
                return 1
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            if args.send:
                stage = "auth"
                client.login("resend", key)
                message = EmailMessage()
                message["From"] = SENDER
                message["To"] = recipient
                message["Subject"] = "Momcozy UAT Resend SMTP connectivity test"
                message.set_content("This is a Momcozy UAT SMTP test message. No verification code is included.")
                stage = "smtp_accepted"
                refused = client.send_message(message)
                if refused:
                    _output(result="failed", stage=stage, reason="recipient_refused")
                    return 1
        _output(result="ok", stage="smtp_accepted" if args.send else "starttls", email_sent=args.send)
        return 0
    except (TimeoutError, socket.timeout):
        reason = "timeout"
    except socket.gaierror:
        reason = "dns_failure"
    except ssl.SSLError:
        reason = "tls_failure"
    except smtplib.SMTPAuthenticationError:
        reason = "authentication_failed"
    except (smtplib.SMTPException, OSError):
        reason = "smtp_or_network_failure"
    # SMTP exceptions can contain addresses, responses and credentials. Never log them.
    _output(result="failed", stage=stage, reason=reason)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

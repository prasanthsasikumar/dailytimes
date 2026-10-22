"""Sends the sign-in emails. Resend API, SMTP, or log-only for local installs."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from .settings import Settings

log = logging.getLogger("dailytimes.mail")


class Mailer:
    def __init__(self, settings: Settings):
        self.s = settings
        self.outbox: list[EmailMessage] = []  # last messages, for tests and dev mode

    def send(self, to: str, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s.mail_from, to, subject
        msg.set_content(text)
        self.outbox = (self.outbox + [msg])[-20:]
        if self.s.resend_api_key:
            r = httpx.post("https://api.resend.com/emails", timeout=15,
                           headers={"Authorization": f"Bearer {self.s.resend_api_key}"},
                           json={"from": self.s.mail_from, "to": [to], "subject": subject, "text": text})
            r.raise_for_status()
        elif self.s.smtp_host:
            ctx = ssl.create_default_context()
            port = self.s.smtp_port
            smtp = (smtplib.SMTP_SSL(self.s.smtp_host, port, context=ctx, timeout=20) if port == 465
                    else smtplib.SMTP(self.s.smtp_host, port, timeout=20))
            with smtp:
                if port != 465:
                    smtp.starttls(context=ctx)
                if self.s.smtp_user:
                    smtp.login(self.s.smtp_user, self.s.smtp_password)
                smtp.send_message(msg)
        else:
            # No mail configured (local install): the link goes to the server log only.
            log.warning("mail not configured; message for %s:\n%s", to, text)

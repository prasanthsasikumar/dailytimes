"""Server configuration, all from environment variables (see .env.example)."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field

log = logging.getLogger("dailytimes.settings")


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    public_url: str = "http://localhost:8787"
    secret: str = ""
    dev: bool = False
    admin_token: str = ""
    inbound_domain: str = "in.example.com"
    inbound_secret: str = ""
    cron_secret: str = ""
    operator: str = "this Daily Times server"
    contact: str = ""
    # mail: Resend API, SMTP, or (dev) log only
    mail_from: str = "Daily Times <times@localhost>"
    resend_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    llm_enabled: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def mail_configured(self) -> bool:
        return bool(self.resend_api_key or self.smtp_host)

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        s = cls(
            public_url=os.environ.get("DAILYTIMES_PUBLIC_URL", "http://localhost:8787").rstrip("/"),
            secret=os.environ.get("DAILYTIMES_SECRET", ""),
            dev=_flag("DAILYTIMES_DEV"),
            admin_token=os.environ.get("DAILYTIMES_ADMIN_TOKEN", ""),
            inbound_domain=os.environ.get("DAILYTIMES_INBOUND_DOMAIN", "in.example.com"),
            inbound_secret=os.environ.get("DAILYTIMES_INBOUND_SECRET", ""),
            cron_secret=os.environ.get("CRON_SECRET", ""),
            operator=os.environ.get("DAILYTIMES_OPERATOR", "this Daily Times server"),
            contact=os.environ.get("DAILYTIMES_CONTACT", ""),
            mail_from=os.environ.get("MAIL_FROM", "Daily Times <times@localhost>"),
            resend_api_key=os.environ.get("RESEND_API_KEY", ""),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            llm_enabled=bool(os.environ.get("ANTHROPIC_API_KEY")),
        )
        for k, v in overrides.items():
            setattr(s, k, v)
        if not s.secret:
            if not s.dev and s.public_url.startswith("https://"):
                raise RuntimeError("DAILYTIMES_SECRET must be set in production (e.g. `openssl rand -hex 32`)")
            s.secret = secrets.token_hex(32)
            log.warning("DAILYTIMES_SECRET not set: using a random one, sessions end when the server restarts")
        return s

"""Inbound email: lets assistants that can only send mail deliver a brief.

Address format: `brief+<device token>@<your inbound domain>`. The body is either
a JSON brief (`{"blocks": [...]}`) or the markdown-ish text understood by
`blocks.parse_plaintext`. The subject becomes the brief title.

Transports sit behind `InboundTransport` so the relay doesn't care whether mail
arrives as a provider webhook (raw MIME POSTed to /v1/inbound/email), via IMAP
polling, or something else.
"""

from __future__ import annotations

import email
import email.policy
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..blocks import Brief, parse_plaintext

_ADDR = re.compile(r"[\w.-]+\+([A-Za-z0-9_\-]+)@", re.I)


@dataclass
class InboundMessage:
    token: str
    brief: Brief


class InboundError(ValueError):
    pass


def parse_mime(raw: bytes) -> InboundMessage:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    recipients = " ".join(str(msg.get(h, "")) for h in ("To", "Cc", "Delivered-To", "X-Original-To"))
    m = _ADDR.search(recipients)
    if not m:
        raise InboundError("recipient must look like brief+<device token>@domain")
    part = msg.get_body(preferencelist=("plain",))
    if part is None:
        raise InboundError("message has no text/plain body")
    body = part.get_content().strip()
    subject = (str(msg.get("Subject", "")) or None)
    if subject:
        subject = subject[:60]
    if body.startswith("{"):
        data = json.loads(body)
        data.setdefault("title", subject)
        brief = Brief.model_validate(data)
    else:
        brief = parse_plaintext(_strip_signature(body), title=subject)
    return InboundMessage(token=m.group(1), brief=brief)


def _strip_signature(body: str) -> str:
    return body.split("\n-- \n", 1)[0]


class InboundTransport(ABC):
    """Source of inbound brief emails."""

    @abstractmethod
    def fetch(self) -> list[bytes]:
        """Return raw RFC 822 messages that have arrived since the last call."""


class WebhookMIMETransport(InboundTransport):
    """Messages pushed by a mail provider (Mailgun/Postmark/SES raw MIME) to the relay.

    The FastAPI route hands each body straight to `parse_mime`; `fetch` exists
    so tests and batch tools can treat it like any other transport.
    """

    def __init__(self) -> None:
        self._inbox: list[bytes] = []

    def push(self, raw: bytes) -> None:
        self._inbox.append(raw)

    def fetch(self) -> list[bytes]:
        out, self._inbox = self._inbox, []
        return out


class IMAPTransport(InboundTransport):
    """Stub: poll a mailbox over IMAP. Not implemented in the MVP."""

    def __init__(self, host: str, user: str, password: str, folder: str = "INBOX"):
        self.host, self.user, self.password, self.folder = host, user, password, folder

    def fetch(self) -> list[bytes]:
        raise NotImplementedError("IMAP polling is not implemented yet; use the webhook transport")

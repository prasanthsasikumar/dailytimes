"""Email-to-print: mail the PDF to a printer's address (HP ePrint, Epson Connect, Canon, Brother).

Options
  to            printer email address (required)
  smtp_host     SMTP server (required unless dry-run)
  smtp_port     default 587
  username, password   SMTP login; `password_env` names an env var instead of inlining it
  from_addr     sender (default: username); most printer services only accept allow-listed senders
  starttls      default true (port 465 uses implicit TLS)
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from .base import Adapter, AdapterError, PrintJob, PrintResult, register


@register
class EmailPrintAdapter(Adapter):
    name = "email"
    formats = ("pdf",)

    def formats_for(self, profile):
        return ("pdf",)

    def build_message(self, job: PrintJob) -> EmailMessage:
        self._need("to")
        if job.rendered.pdf is None:
            raise AdapterError("email adapter needs a PDF render")
        msg = EmailMessage()
        msg["To"] = self.options["to"]
        msg["From"] = self.options.get("from_addr") or self.options.get("username") or "dailytimes@localhost"
        msg["Subject"] = f"{job.title} {job.date.isoformat()}"
        msg.set_content("The Daily Times. Print the attached PDF.\n")
        msg.add_attachment(job.rendered.pdf, maintype="application", subtype="pdf", filename=f"{job.basename}.pdf")
        return msg

    def send(self, job: PrintJob) -> PrintResult:
        msg = self.build_message(job)
        if self.dry_run:
            raw = msg.as_bytes()
            return PrintResult(True, self.name, f"would mail {len(raw)} bytes to {msg['To']}", True,
                               data={"message": msg, "bytes": len(raw)})
        self._need("smtp_host")
        host, port = self.options["smtp_host"], int(self.options.get("smtp_port", 587))
        password = self.options.get("password") or os.environ.get(self.options.get("password_env", ""), "")
        ctx = ssl.create_default_context()
        smtp = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
        with smtp:
            if port != 465 and self.options.get("starttls", True):
                smtp.starttls(context=ctx)
            if self.options.get("username"):
                smtp.login(self.options["username"], password)
            smtp.send_message(msg)
        return PrintResult(True, self.name, f"mailed to {msg['To']}")

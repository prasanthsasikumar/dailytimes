"""A minimal IPP/1.1 client: just enough for Print-Job of a PDF to a driverless printer."""

from __future__ import annotations

import struct
from urllib.parse import urlsplit, urlunsplit

import httpx

PRINT_JOB = 0x0002
GET_PRINTER_ATTRIBUTES = 0x000B

TAG_OPERATION = 0x01
TAG_END = 0x03
CHARSET = 0x47
LANGUAGE = 0x48
URI = 0x45
NAME = 0x42
KEYWORD = 0x44
MIME = 0x49

STATUS_TEXT = {
    0x0000: "successful-ok",
    0x0001: "successful-ok-ignored-or-substituted-attributes",
    0x0400: "client-error-bad-request",
    0x0401: "client-error-forbidden",
    0x0406: "client-error-not-found",
    0x040A: "client-error-document-format-not-supported",
    0x0500: "server-error-internal-error",
    0x0502: "server-error-service-unavailable",
    0x0507: "server-error-busy",
}


def _attr(tag: int, name: str, value: str) -> bytes:
    n, v = name.encode(), value.encode()
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(v)) + v


def encode_request(operation: int, printer_uri: str, request_id: int = 1, *, job_name: str | None = None,
                   document_format: str | None = None, user: str = "dailytimes") -> bytes:
    out = struct.pack(">BBHI", 1, 1, operation, request_id)
    out += bytes([TAG_OPERATION])
    out += _attr(CHARSET, "attributes-charset", "utf-8")
    out += _attr(LANGUAGE, "attributes-natural-language", "en")
    out += _attr(URI, "printer-uri", printer_uri)
    out += _attr(NAME, "requesting-user-name", user)
    if job_name:
        out += _attr(NAME, "job-name", job_name)
    if document_format:
        out += _attr(MIME, "document-format", document_format)
    out += bytes([TAG_END])
    return out


def http_url(printer_uri: str) -> str:
    p = urlsplit(printer_uri)
    scheme = {"ipp": "http", "ipps": "https"}.get(p.scheme, p.scheme)
    netloc = p.netloc if p.port else f"{p.hostname}:631"
    return urlunsplit((scheme, netloc, p.path or "/ipp/print", p.query, ""))


def decode_status(body: bytes) -> tuple[int, str]:
    if len(body) < 8:
        raise ValueError("short IPP response")
    _, _, status, _ = struct.unpack(">BBHI", body[:8])
    return status, STATUS_TEXT.get(status, hex(status))


def print_job(printer_uri: str, pdf: bytes, job_name: str, client: httpx.Client | None = None,
              timeout: float = 30) -> tuple[int, str]:
    body = encode_request(PRINT_JOB, printer_uri, job_name=job_name, document_format="application/pdf") + pdf
    own = client is None
    client = client or httpx.Client(timeout=timeout, verify=False)  # printers use self-signed certs
    try:
        r = client.post(http_url(printer_uri), content=body, headers={"Content-Type": "application/ipp"})
        r.raise_for_status()
        return decode_status(r.content)
    finally:
        if own:
            client.close()

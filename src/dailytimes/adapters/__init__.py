"""Output adapters. Importing this package registers the built-in ones."""

from . import catprinter, cups, email_print, escpos_adapter, file  # noqa: F401  (register)
from .base import REGISTRY, Adapter, AdapterError, DiscoveredPrinter, PrintJob, PrintResult, get_adapter, register
from .discovery import discover_all, suggest_profile

__all__ = ["REGISTRY", "Adapter", "AdapterError", "DiscoveredPrinter", "PrintJob", "PrintResult", "get_adapter",
           "register", "discover_all", "suggest_profile", "print_brief"]


def print_brief(brief, profile, adapter: Adapter, brief_id: str | None = None, date=None) -> PrintResult:
    """Render a brief for `profile` in the formats `adapter` wants, and send it."""
    from ..render import render

    rendered = render(brief, profile, adapter.formats_for(profile), date)
    return adapter.send(PrintJob(rendered, brief_id=brief_id))

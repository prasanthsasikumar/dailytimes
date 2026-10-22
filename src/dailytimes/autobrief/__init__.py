"""Auto edition: a brief built from public sources when no AI agent sent one."""

from .compose import build_auto_brief
from .interests import Interests, Location
from .suggest import suggest

__all__ = ["build_auto_brief", "Interests", "Location", "suggest"]

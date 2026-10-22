"""Vercel entry point: the whole Daily Times server as one ASGI function."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dailytimes.relay.app import create_app  # noqa: E402

app = create_app()

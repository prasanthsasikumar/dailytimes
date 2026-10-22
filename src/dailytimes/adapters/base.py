"""Output adapter plugin interface.

An adapter moves rendered output to a printer. It declares which formats it
accepts (`pdf`, `raster`); the caller renders with the printer's profile and
hands over a `PrintJob`. Supporting a new printer type means writing one
adapter (registered here or via the `dailytimes.adapters` entry-point group)
plus a profile YAML. Nothing else changes.

Every adapter supports `dry_run=True`: it does all the encoding work (builds the
IPP request, ESC/POS bytes, MIME message, ...) but doesn't touch hardware or network.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, ClassVar

from ..render import Rendered
from ..render.profiles import Profile


@dataclass
class PrintJob:
    rendered: Rendered
    title: str = "The Daily Times"
    brief_id: str | None = None

    @property
    def profile(self) -> Profile:
        return self.rendered.profile

    @property
    def date(self) -> dt.date:
        return self.rendered.date

    @property
    def basename(self) -> str:
        return f"daily-times-{self.date.isoformat()}-{self.profile.name}"


@dataclass
class PrintResult:
    ok: bool
    adapter: str
    detail: str
    dry_run: bool = False
    artifacts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveredPrinter:
    name: str
    adapter: str
    options: dict[str, Any]
    source: str  # lpstat | mdns | usb | serial | ble
    make_model: str = ""
    suggested_profile: str = ""


class AdapterError(RuntimeError):
    pass


class Adapter(ABC):
    name: ClassVar[str]
    formats: ClassVar[tuple[str, ...]]  # formats this adapter can send, preferred first

    def __init__(self, dry_run: bool = False, **options: Any):
        self.dry_run = dry_run
        self.options = options

    def formats_for(self, profile: Profile) -> tuple[str, ...]:
        """Which formats to render for this profile."""
        if profile.preferred_output in self.formats:
            return (profile.preferred_output,)
        return (self.formats[0],)

    @abstractmethod
    def send(self, job: PrintJob) -> PrintResult: ...

    @classmethod
    def discover(cls, timeout: float = 2.0) -> list[DiscoveredPrinter]:
        return []

    def _need(self, *keys: str) -> None:
        missing = [k for k in keys if not self.options.get(k)]
        if missing:
            raise AdapterError(f"{self.name} adapter needs option(s): {', '.join(missing)}")


REGISTRY: dict[str, type[Adapter]] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    REGISTRY[cls.name] = cls
    return cls


def _load_plugins() -> None:
    for ep in entry_points(group="dailytimes.adapters"):
        try:
            cls = ep.load()
            REGISTRY.setdefault(cls.name, cls)
        except Exception:  # a broken third-party plugin must not break printing
            continue


def get_adapter(name: str, dry_run: bool = False, **options: Any) -> Adapter:
    if name not in REGISTRY:
        _load_plugins()
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise AdapterError(f"unknown adapter {name!r}; available: {', '.join(sorted(REGISTRY))}") from None
    return cls(dry_run=dry_run, **options)

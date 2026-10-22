"""Presence backends: something that says "a person is at the printer now".

Each backend calls `on_presence(source)` and gets back the agent's result dict.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from abc import ABC, abstractmethod
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

log = logging.getLogger("dailytimes.presence")

OnPresence = Callable[[str], dict]


class PresenceBackend(ABC):
    def __init__(self, on_presence: OnPresence, **options: Any):
        self.on_presence = on_presence
        self.options = options

    @abstractmethod
    def start(self) -> None: ...

    def stop(self) -> None:
        pass


class SimulatedPresence(PresenceBackend):
    """For development and demos.

    Options: `stdin: true` (press Enter = someone walked up), `every_seconds: N`
    (periodic presence), or call `trigger()` from code/tests.
    """

    def start(self) -> None:
        self._stop = threading.Event()
        if self.options.get("stdin"):
            threading.Thread(target=self._stdin, daemon=True).start()
            print("simulated presence: press Enter when someone walks up to the printer", file=sys.stderr)
        if every := self.options.get("every_seconds"):
            threading.Thread(target=self._periodic, args=(float(every),), daemon=True).start()

    def trigger(self, source: str = "simulated") -> dict:
        return self.on_presence(source)

    def _stdin(self) -> None:
        for _ in sys.stdin:
            print(json.dumps(self.trigger("simulated:stdin")), file=sys.stderr)

    def _periodic(self, every: float) -> None:
        while not self._stop.wait(every):
            self.trigger("simulated:timer")

    def stop(self) -> None:
        if hasattr(self, "_stop"):
            self._stop.set()


class ManualPresence(PresenceBackend):
    """For people without sensors: an HTTP call (phone shortcut, smart button, `dailytimes presence`)
    or a physical push button on a GPIO pin (`button_pin`).

    Options: host (127.0.0.1), port (8765), button_pin (optional, BCM numbering).
    """

    def start(self) -> None:
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                if self.path.rstrip("/") != "/presence":
                    return self._send(404, {"error": "POST /presence"})
                try:
                    self._send(200, backend.on_presence("manual:http"))
                except Exception as e:  # report, don't kill the server
                    log.exception("presence handler failed")
                    self._send(500, {"error": str(e)})

            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/") in ("", "/status"):
                    return self._send(200, {"ok": True, "backend": "manual"})
                self._send(404, {"error": "not found"})

            def log_message(self, fmt, *args):
                log.debug(fmt, *args)

        host, port = self.options.get("host", "127.0.0.1"), int(self.options.get("port", 8765))
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        log.info("manual presence: POST http://%s:%s/presence", host, self.port)
        if pin := self.options.get("button_pin"):
            self._button = GPIOPresence(lambda s: self.on_presence("manual:button"), pin=pin, pull_up=True,
                                        device_factory=self.options.get("device_factory"))
            self._button.start()

    def stop(self) -> None:
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()


class GPIOPresence(PresenceBackend):
    """A digital sensor on a GPIO pin: PIR (HC-SR501, AM312) or mmWave (LD2410 OUT pin).

    Options: pin (BCM), active_high (true), pull_up (null = floating, sensor drives the line),
    bounce_ms (200), hold_seconds (minimum time the line must stay active, filters glitches).
    Uses gpiozero, so it also runs on a laptop with GPIOZERO_PIN_FACTORY=mock.
    """

    def _make_device(self):
        if factory := self.options.get("device_factory"):
            return factory(**self._device_kwargs())
        try:
            from gpiozero import DigitalInputDevice
        except ImportError as e:
            raise RuntimeError("GPIO presence needs gpiozero: pip install 'dailytimes[gpio]'") from e
        return DigitalInputDevice(**self._device_kwargs())

    def _device_kwargs(self) -> dict:
        pull_up = self.options.get("pull_up")
        kw = {"pin": self.options["pin"], "pull_up": pull_up,
              "bounce_time": float(self.options.get("bounce_ms", 200)) / 1000}
        if pull_up is None:
            kw["active_state"] = bool(self.options.get("active_high", True))
        return kw

    def start(self) -> None:
        if "pin" not in self.options:
            raise ValueError("gpio presence needs a `pin` option")
        self.device = self._make_device()
        hold = float(self.options.get("hold_seconds", 0))

        def activated():
            if hold:
                time.sleep(hold)
                if not self.device.is_active:
                    return
            try:
                self.on_presence("gpio")
            except Exception:
                log.exception("presence handler failed")

        self.device.when_activated = activated

    def stop(self) -> None:
        if hasattr(self, "device"):
            self.device.close()


class BLEPresence(PresenceBackend):
    """Stub: phone proximity via BLE RSSI.

    Phones randomise their BLE address, so this expects a stable advertiser:
    a beacon app (iBeacon/Eddystone) or a tag on your keyring. Options: name or
    address, rssi_threshold (-65), cooldown_seconds (60).
    Needs `bleak`; scanning loop is a minimal sketch, not hardened.
    """

    def start(self) -> None:
        try:
            from bleak import BleakScanner  # noqa: F401
        except ImportError as e:
            raise RuntimeError("BLE presence needs bleak: pip install 'dailytimes[ble]'") from e
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def matches(self, name: str | None, address: str, rssi: int) -> bool:
        want_name, want_addr = self.options.get("name"), self.options.get("address")
        if want_addr and address.lower() != str(want_addr).lower():
            return False
        if want_name and name != want_name:
            return False
        return rssi >= int(self.options.get("rssi_threshold", -65))

    def _run(self) -> None:
        import asyncio

        from bleak import BleakScanner

        last = 0.0
        cooldown = float(self.options.get("cooldown_seconds", 60))

        def seen(device, adv):
            nonlocal last
            if self.matches(device.name or adv.local_name, device.address, adv.rssi) and time.time() - last > cooldown:
                last = time.time()
                self.on_presence("ble")

        async def scan():
            async with BleakScanner(detection_callback=seen):
                while not self._stop.is_set():
                    await asyncio.sleep(1)

        asyncio.run(scan())

    def stop(self) -> None:
        if hasattr(self, "_stop"):
            self._stop.set()


BACKENDS: dict[str, type[PresenceBackend]] = {
    "simulated": SimulatedPresence,
    "manual": ManualPresence,
    "gpio": GPIOPresence,
    "ble": BLEPresence,
}


def make_backend(name: str, on_presence: OnPresence, **options: Any) -> PresenceBackend:
    return BACKENDS[name](on_presence, **options)

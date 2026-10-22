import time

import httpx
import pytest

from dailytimes.agent.presence import BLEPresence, GPIOPresence, ManualPresence, SimulatedPresence, make_backend


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, source):
        self.calls.append(source)
        return {"action": "print", "source": source}


def test_simulated_trigger_and_timer():
    rec = Recorder()
    b = make_backend("simulated", rec, every_seconds=0.05)
    assert isinstance(b, SimulatedPresence)
    assert b.trigger() == {"action": "print", "source": "simulated"}
    b.start()
    time.sleep(0.2)
    b.stop()
    assert rec.calls.count("simulated:timer") >= 2


def test_manual_http_endpoint():
    rec = Recorder()
    b = ManualPresence(rec, host="127.0.0.1", port=0)
    b.start()
    try:
        base = f"http://127.0.0.1:{b.port}"
        r = httpx.post(base + "/presence")
        assert r.status_code == 200 and r.json()["source"] == "manual:http"
        assert httpx.get(base + "/status").json()["ok"] is True
        assert httpx.post(base + "/other").status_code == 404
    finally:
        b.stop()
    assert rec.calls == ["manual:http"]


def test_manual_http_reports_handler_errors():
    def boom(source):
        raise RuntimeError("printer on fire")

    b = ManualPresence(boom, port=0)
    b.start()
    try:
        r = httpx.post(f"http://127.0.0.1:{b.port}/presence")
        assert r.status_code == 500 and "printer on fire" in r.json()["error"]
    finally:
        b.stop()


class FakePin:
    """Stands in for gpiozero.DigitalInputDevice."""

    instances = []

    def __init__(self, pin, pull_up, bounce_time, active_state=None):
        self.pin, self.pull_up, self.bounce_time, self.active_state = pin, pull_up, bounce_time, active_state
        self.is_active = False
        self.when_activated = None
        self.closed = False
        FakePin.instances.append(self)

    def fire(self, stay_active=True):
        self.is_active = True
        cb = self.when_activated
        if not stay_active:
            self.is_active = False
        cb()

    def close(self):
        self.closed = True


def test_gpio_pir_triggers_on_rising_edge():
    rec = Recorder()
    b = GPIOPresence(rec, pin=17, device_factory=FakePin)
    b.start()
    dev = FakePin.instances[-1]
    assert (dev.pin, dev.pull_up, dev.active_state, dev.bounce_time) == (17, None, True, 0.2)
    dev.fire()
    assert rec.calls == ["gpio"]
    b.stop()
    assert dev.closed


def test_gpio_hold_filters_glitches():
    rec = Recorder()
    b = GPIOPresence(rec, pin=4, hold_seconds=0.01, device_factory=FakePin)
    b.start()
    dev = FakePin.instances[-1]
    dev.fire(stay_active=False)
    assert rec.calls == []
    dev.fire(stay_active=True)
    assert rec.calls == ["gpio"]


def test_gpio_requires_pin():
    with pytest.raises(ValueError):
        GPIOPresence(Recorder()).start()


def test_manual_with_button_pin():
    rec = Recorder()
    b = ManualPresence(rec, port=0, button_pin=27, device_factory=FakePin)
    b.start()
    try:
        btn = FakePin.instances[-1]
        assert btn.pin == 27 and btn.pull_up is True
        btn.fire()
        assert rec.calls == ["manual:button"]
    finally:
        b.stop()


def test_ble_matching_rules():
    b = BLEPresence(Recorder(), name="Keys", rssi_threshold=-60)
    assert b.matches("Keys", "AA:BB", -55)
    assert not b.matches("Keys", "AA:BB", -75)
    assert not b.matches("Other", "AA:BB", -40)
    b2 = BLEPresence(Recorder(), address="aa:bb:cc:dd:ee:ff")
    assert b2.matches(None, "AA:BB:CC:DD:EE:FF", -50)

"""`dailytimes setup`: pair this machine with an account and pick a printer, interactively.

Pairing code (from the website) -> device token -> discover printers -> choose
profile -> test print -> write ~/.config/dailytimes/agent.yaml (mode 600).
Every question has a flag, so it also runs unattended (`--yes`).
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Callable

import httpx
import yaml

DEFAULT_CONFIG = Path.home() / ".config" / "dailytimes" / "agent.yaml"


class SetupError(RuntimeError):
    pass


def pair(server: str, code: str, http: httpx.Client | None = None) -> dict:
    client = http or httpx.Client(timeout=20)
    r = client.post(f"{server.rstrip('/')}/v1/pair", json={"code": code.strip()})
    if r.status_code == 404:
        raise SetupError("that pairing code is unknown or expired; get a new one on the Printers page")
    r.raise_for_status()
    return r.json()


def _choose(prompt: str, options: list[str], default: int, ask: Callable[[str], str]) -> int:
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    while True:
        got = ask(f"{prompt} [{default + 1}]: ").strip()
        if not got:
            return default
        if got.isdigit() and 1 <= int(got) <= len(options):
            return int(got) - 1
        print("  pick a number from the list")


def run_setup(server: str, code: str, *, config_path: Path = DEFAULT_CONFIG, yes: bool = False,
              profile: str | None = None, adapter: str | None = None, options: dict | None = None,
              presence: str = "manual", gpio_pin: int | None = None, test_print: bool = True,
              print_after: str = "06:30", ask: Callable[[str], str] = input,
              http: httpx.Client | None = None) -> Path:
    from ..adapters import discover_all, get_adapter, print_brief
    from ..adapters.discovery import default_page_profile
    from ..calibration import calibration_brief
    from ..render import list_profiles, load_profile

    print(f"Pairing with {server} ...")
    paired = pair(server, code, http)
    print(f"Paired as “{paired['name']}” (timezone {paired['timezone']}).")

    # ---- printer
    if adapter is None:
        print("Looking for printers (a few seconds) ...")
        found = discover_all(timeout=3.0)
        choices = [f"{p.name}  [{p.adapter}, suggested profile {p.suggested_profile}]" for p in found]
        choices += ["Save PDFs to a folder (no printer, or print them yourself)",
                    "Email-to-print address (HP ePrint, Epson Connect, ...)"]
        idx = 0 if yes else _choose("Which printer?", choices, 0, ask)
        if idx < len(found):
            adapter, options = found[idx].adapter, dict(found[idx].options)
            profile = profile or found[idx].suggested_profile
        elif idx == len(found):
            adapter = "file"
            folder = str(Path.home() / "DailyTimes")
            options = {"directory": folder if yes else (ask(f"Folder [{folder}]: ").strip() or folder),
                       "formats": ["pdf"]}
        else:
            adapter = "email"
            options = {"to": ask("Printer email address: ").strip(), "smtp_host": ask("SMTP server: ").strip(),
                       "username": ask("SMTP username: ").strip(), "password_env": "DAILYTIMES_SMTP_PASSWORD"}
            print("  Put the SMTP password in the DAILYTIMES_SMTP_PASSWORD environment variable.")
    options = options or {}

    # ---- paper profile
    if profile is None:
        profile = default_page_profile() if adapter in ("file", "email", "cups") else "thermal-80mm"
    if not yes:
        names = [p.name for p in list_profiles()]
        descs = [f"{p.name:18} {p.description}" for p in list_profiles()]
        profile = names[_choose("Paper profile?", descs, names.index(profile) if profile in names else 0, ask)]
    prof = load_profile(profile)

    # ---- test print
    if test_print and (yes or ask("Print a test page now? [Y/n]: ").strip().lower() in ("", "y", "yes")):
        result = print_brief(calibration_brief(prof, adapter), prof, get_adapter(adapter, **options))
        print(("  " if result.ok else "  FAILED: ") + result.detail)

    # ---- presence
    presence_cfg: dict = {"backend": "manual", "options": {"host": "127.0.0.1", "port": 8765}}
    if not yes:
        idx = _choose("How does it know someone walked up?", [
            "No sensor: a phone shortcut, smart button or `dailytimes presence` (HTTP)",
            "Motion sensor (PIR or mmWave) on a Raspberry Pi GPIO pin",
            "Push button on a GPIO pin (plus the HTTP trigger)"], 0, ask)
        if idx in (1, 2):
            pin = int(ask("GPIO pin (BCM number) [17]: ").strip() or 17)
            presence_cfg = ({"backend": "gpio", "options": {"pin": pin}} if idx == 1 else
                            {"backend": "manual", "options": {"host": "127.0.0.1", "port": 8765, "button_pin": pin}})
            print("  Install GPIO support: pip install 'dailytimes[gpio]'")
    elif presence == "gpio":
        presence_cfg = {"backend": "gpio", "options": {"pin": gpio_pin or 17}}

    config = {
        "relay": {"url": paired.get("server") or server, "token": paired["token"]},
        "outputs": [{"profile": profile, "adapter": adapter, "options": options}],
        "presence": presence_cfg,
        "rules": {"print_after": print_after, "cooldown_minutes": 15, "fallback": "retry", "fallback_after": "11:00"},
        "poll_interval_seconds": 300,
        "state_file": str(config_path.parent / "state.json"),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# Written by `dailytimes setup`. Contains this printer's token: keep it private.\n")
        yaml.safe_dump(config, f, sort_keys=False)
    print(f"\nSaved {config_path}")
    print("Start it now with:   dailytimes agent")
    print("Run it at boot with: dailytimes service   (prints the systemd/launchd file and how to install it)")
    return config_path


def service_file(config_path: Path = DEFAULT_CONFIG) -> tuple[str, str, str]:
    """Return (path to install at, file contents, install commands) for this OS."""
    exe = shutil.which("dailytimes") or f"{sys.executable} -m dailytimes.cli"
    if platform.system() == "Darwin":
        dest = Path.home() / "Library/LaunchAgents/org.dailytimes.agent.plist"
        args = "".join(f"<string>{a}</string>" for a in [*exe.split(), "agent", "--config", str(config_path)])
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>org.dailytimes.agent</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/tmp/dailytimes-agent.log</string>
  <key>StandardErrorPath</key><string>/tmp/dailytimes-agent.log</string>
</dict></plist>
"""
        cmds = f"launchctl load -w {dest}"
        return str(dest), body, cmds
    dest = "/etc/systemd/system/dailytimes-agent.service"
    user = os.environ.get("USER", "pi")
    import grp

    groups = [g for g in ("gpio", "lp", "dialout") if _group_exists(grp, g)]  # systemd fails on unknown groups
    body = f"""[Unit]
Description=Daily Times printer agent
Wants=network-online.target
After=network-online.target cups.service

[Service]
User={user}
{"SupplementaryGroups=" + " ".join(groups) if groups else "# no gpio/lp/dialout groups on this machine"}
ExecStart={exe} agent --config {config_path}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    cmds = "sudo systemctl daemon-reload && sudo systemctl enable --now dailytimes-agent"
    return dest, body, cmds


def _group_exists(grp, name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False

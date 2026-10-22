"""`dailytimes` command line."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml


def _opts(pairs: list[str] | None) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--opt expects key=value, got {pair!r}")
        k, v = pair.split("=", 1)
        out[k] = yaml.safe_load(v) if v else ""
    return out


def _load_brief(path: str):
    from .blocks import Brief, parse_plaintext

    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    if text.lstrip().startswith("{"):
        return Brief.model_validate(json.loads(text))
    return parse_plaintext(text)


# ---------------------------------------------------------------- commands

def cmd_serve(a):
    import os

    import uvicorn

    from .relay.app import create_app

    if a.db:
        os.environ["DAILYTIMES_DB"] = a.db
    if a.public_url:
        os.environ["DAILYTIMES_PUBLIC_URL"] = a.public_url
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level=a.log_level)


def cmd_register(a):
    import httpx

    headers = {"Authorization": f"Bearer {a.admin_token}"} if a.admin_token else {}
    r = httpx.post(f"{a.relay.rstrip('/')}/v1/devices", json={"name": a.name, "timezone": a.timezone},
                   headers=headers, timeout=15)
    if r.status_code != 201:
        raise SystemExit(f"registration failed: {r.status_code} {r.text}")
    print(json.dumps(r.json(), indent=2))


def cmd_send(a):
    from .client import send

    print(json.dumps(send(a.endpoint, _load_brief(a.file)), indent=2))


def cmd_setup(a):
    from pathlib import Path

    from .agent.setup import SetupError, run_setup

    try:
        run_setup(a.server, a.code, config_path=Path(a.config).expanduser(), yes=a.yes, profile=a.profile,
                  adapter=a.adapter, options=_opts(a.opt), presence=a.presence, gpio_pin=a.gpio_pin,
                  test_print=not a.no_test_print, print_after=a.print_after)
    except SetupError as e:
        raise SystemExit(str(e))


def cmd_service(a):
    from pathlib import Path

    from .agent.setup import service_file

    dest, body, cmds = service_file(Path(a.config).expanduser())
    print(f"# Save as {dest}, then run:\n#   {cmds}\n")
    print(body)


def cmd_render(a):
    from .render import load_profile, render

    brief = _load_brief(a.file)
    profile = load_profile(a.profile)
    fmts = {"pdf": ("pdf",), "png": ("raster",), "both": ("pdf", "raster")}[a.format] if a.format else None
    date = dt.date.fromisoformat(a.date) if a.date else None
    r = render(brief, profile, fmts, date)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"daily-times-{r.date.isoformat()}-{profile.name}"
    if r.pdf:
        (out / f"{stem}.pdf").write_bytes(r.pdf)
        print(out / f"{stem}.pdf")
    for i, png in enumerate(r.png_pages()):
        p = out / (f"{stem}.png" if len(r.images) == 1 else f"{stem}-p{i + 1}.png")
        p.write_bytes(png)
        print(p)


def cmd_profiles(a):
    from .render import list_profiles

    for p in list_profiles():
        geo = f"{p.printable_width_px}px@{p.dpi}dpi" if p.type == "thermal" else (p.page_size or "")
        print(f"{p.name:18} {p.type:8} {p.layout:10} {geo:16} {p.description}")


def cmd_printers(a):
    from .adapters import discover_all
    from .adapters.discovery import default_page_profile

    found = discover_all(timeout=a.timeout)
    if a.json:
        print(json.dumps([p.__dict__ for p in found], indent=2))
        return
    if not found:
        print("No printers found.")
        print("  - USB/serial receipt printers: check the cable and permissions (dialout/lp group on Linux).")
        print("  - Network printers: make sure this machine is on the same network (mDNS/Bonjour).")
        print(f"  - No printer at all? Use the file or email adapter (suggested page profile: {default_page_profile()}).")
        return
    print(f"{'NAME':28} {'ADAPTER':9} {'PROFILE':14} {'SOURCE':7} OPTIONS")
    for p in found:
        opts = " ".join(f"{k}={v}" for k, v in p.options.items())
        print(f"{p.name[:28]:28} {p.adapter:9} {p.suggested_profile:14} {p.source:7} {opts}")
    print("\nTest one with: dailytimes test-print --adapter <ADAPTER> --profile <PROFILE> --opt <OPTIONS...>")


def cmd_test_print(a):
    from .adapters import get_adapter, print_brief
    from .calibration import calibration_brief
    from .render import load_profile

    profile = load_profile(a.profile)
    adapter = get_adapter(a.adapter, dry_run=a.dry_run, **_opts(a.opt))
    result = print_brief(calibration_brief(profile, a.adapter), profile, adapter)
    print(("DRY RUN: " if result.dry_run else "") + result.detail)
    if not result.ok:
        raise SystemExit(1)


def cmd_print(a):
    from .adapters import get_adapter, print_brief
    from .render import load_profile

    profile = load_profile(a.profile)
    adapter = get_adapter(a.adapter, dry_run=a.dry_run, **_opts(a.opt))
    result = print_brief(_load_brief(a.file), profile, adapter)
    print(("DRY RUN: " if result.dry_run else "") + result.detail)
    if not result.ok:
        raise SystemExit(1)


def cmd_agent(a):
    from .agent.config import load_config
    from .agent.runner import run_agent

    from pathlib import Path

    path = Path(a.config).expanduser()
    if not path.exists():
        raise SystemExit(f"no agent config at {path}; run `dailytimes setup --server <url> --code <code>` first")
    cfg = load_config(path)
    if a.dry_run:
        for out in cfg.outputs:
            out.dry_run = True
    run_agent(cfg, once=a.once)


def cmd_presence(a):
    import httpx

    r = httpx.post(a.url.rstrip("/") + "/presence", timeout=10)
    print(r.text)
    if r.status_code >= 400:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dailytimes", description="The Daily Times: your assistant's brief, on paper.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("serve", "relay"):
        p = sub.add_parser(name, help="run the server: website, relay API, MCP" + (" (alias of serve)" if name == "relay" else ""))
        p.add_argument("--host", default="0.0.0.0")
        p.add_argument("--port", type=int, default=8787)
        p.add_argument("--db", default=None, help="SQLite path (default $DAILYTIMES_DB or ./dailytimes.db)")
        p.add_argument("--public-url", default=None)
        p.add_argument("--log-level", default="info")
        p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("register", help="admin: create an account + printer without the website")
    p.add_argument("--relay", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--timezone", default="UTC")
    p.add_argument("--admin-token", default=None)
    p.set_defaults(fn=cmd_register)

    p = sub.add_parser("send", help="send a brief (JSON or markdown-ish text) to your endpoint")
    p.add_argument("file", help="brief file, or - for stdin")
    p.add_argument("--endpoint", required=True, help="your MCP URL (…/mcp) or webhook URL (…/brief)")
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("setup", help="pair this machine with your account and choose a printer")
    p.add_argument("--server", required=True, help="e.g. https://times.example.com")
    p.add_argument("--code", required=True, help="pairing code from the Printers page")
    p.add_argument("--config", default="~/.config/dailytimes/agent.yaml")
    p.add_argument("--yes", action="store_true", help="don't ask; use flags and suggestions")
    p.add_argument("--profile")
    p.add_argument("--adapter")
    p.add_argument("--opt", action="append", metavar="KEY=VALUE")
    p.add_argument("--presence", choices=["manual", "gpio"], default="manual")
    p.add_argument("--gpio-pin", type=int)
    p.add_argument("--print-after", default="06:30")
    p.add_argument("--no-test-print", action="store_true")
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("service", help="print a systemd unit / launchd plist to run the agent at boot")
    p.add_argument("--config", default="~/.config/dailytimes/agent.yaml")
    p.set_defaults(fn=cmd_service)

    p = sub.add_parser("render", help="render a brief to PDF/PNG without printing")
    p.add_argument("file")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", default="out")
    p.add_argument("--format", choices=["pdf", "png", "both"])
    p.add_argument("--date")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("profiles", help="list printer profiles")
    p.set_defaults(fn=cmd_profiles)

    p = sub.add_parser("printers", help="detect printers and suggest a profile")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_printers)

    for name, fn, helptext in (("test-print", cmd_test_print, "print a calibration slip/page"),
                               ("print", cmd_print, "print a brief file directly")):
        p = sub.add_parser(name, help=helptext)
        if name == "print":
            p.add_argument("file")
        p.add_argument("--profile", required=True)
        p.add_argument("--adapter", required=True, help="cups | escpos | catprinter | email | file | <plugin>")
        p.add_argument("--opt", action="append", metavar="KEY=VALUE", help="adapter option, repeatable")
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(fn=fn)

    p = sub.add_parser("agent", help="run the device agent next to the printer")
    p.add_argument("--config", default="~/.config/dailytimes/agent.yaml")
    p.add_argument("--once", action="store_true", help="poll, handle one presence check, exit")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_agent)

    p = sub.add_parser("presence", help="signal presence to a running agent (manual backend)")
    p.add_argument("--url", default="http://127.0.0.1:8765")
    p.set_defaults(fn=cmd_presence)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

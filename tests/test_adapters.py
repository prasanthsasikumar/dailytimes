import datetime as dt
import email
import struct

import httpx
import pytest

from dailytimes.adapters import (
    REGISTRY,
    Adapter,
    AdapterError,
    PrintJob,
    PrintResult,
    get_adapter,
    print_brief,
    register,
)
from dailytimes.adapters import catprinter, ipp
from dailytimes.adapters.cups import CupsAdapter
from dailytimes.adapters.discovery import default_page_profile, suggest_profile
from dailytimes.calibration import calibration_brief
from dailytimes.render import load_profile, render

DAY = dt.date(2026, 10, 21)


def job(brief, profile_name, formats):
    return PrintJob(render(brief, load_profile(profile_name), formats, DAY), brief_id="br_test")


def test_builtin_adapters_registered():
    assert {"cups", "escpos", "catprinter", "email", "file"} <= set(REGISTRY)
    with pytest.raises(AdapterError, match="unknown adapter"):
        get_adapter("fax")


def test_new_printer_type_needs_only_an_adapter(sample_brief):
    @register
    class Recorder(Adapter):
        name = "recorder-test"
        formats = ("raster",)

        def send(self, job):
            return PrintResult(True, self.name, f"{len(job.rendered.images)} image(s)")

    try:
        a = get_adapter("recorder-test")
        res = print_brief(sample_brief, load_profile("thermal-58mm"), a, date=DAY)
        assert res.ok and res.detail == "1 image(s)"
    finally:
        REGISTRY.pop("recorder-test")


# ------------------------------------------------------------------ cups / ipp

def test_cups_dry_run_lp_command(sample_brief):
    a = get_adapter("cups", dry_run=True, printer="HP_LaserJet", copies=2, lp_options=["fit-to-page"])
    assert a.formats_for(load_profile("a4")) == ("pdf",)
    res = a.send(job(sample_brief, "a4", ("pdf",)))
    assert res.ok and res.dry_run
    assert res.data["command"] == ["lp", "-d", "HP_LaserJet", "-t", "The Daily Times", "-o", "media=A4",
                                   "-n", "2", "-o", "fit-to-page", "-"]


def test_cups_receipt_media_is_custom_length(sample_brief):
    a = get_adapter("cups", dry_run=True)
    j = job(sample_brief, "receipt-80mm-pdf", ("pdf", "raster"))
    assert a.media(j).startswith("Custom.80x") and a.media(j).endswith("mm")


def test_cups_dry_run_ipp(sample_brief):
    a = get_adapter("cups", dry_run=True, uri="ipp://printer.local/ipp/print")
    res = a.send(job(sample_brief, "letter", ("pdf",)))
    assert res.ok and "http://printer.local:631/ipp/print" in res.detail
    req = res.data["ipp_request"]
    assert req[:4] == bytes([1, 1, 0, 2]) and b"application/pdf" in req and req.endswith(b"\x03")


def test_cups_needs_pdf(sample_brief):
    with pytest.raises(AdapterError):
        get_adapter("cups", dry_run=True).send(job(sample_brief, "thermal-58mm", ("raster",)))


def test_ipp_print_job_against_fake_printer():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, content=struct.pack(">BBHI", 1, 1, 0x0000, 1) + b"\x03")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    status, text = ipp.print_job("ipps://10.0.0.5/ipp/print", b"%PDF-1.4 fake", "Daily", client=client)
    assert (status, text) == (0, "successful-ok")
    assert seen["url"] == "https://10.0.0.5:631/ipp/print"
    assert seen["body"].endswith(b"\x03%PDF-1.4 fake")
    assert ipp.decode_status(struct.pack(">BBHI", 1, 1, 0x040A, 1))[1] == "client-error-document-format-not-supported"


def test_lpstat_parsing_and_profile_suggestion():
    out = ("device for Brother_HL_L2350DW: dnssd://Brother%20HL-L2350DW._ipp._tcp.local./?uuid=x\n"
           "device for EPSON_TM_T20III: usb://EPSON/TM-T20III?serial=123\n"
           "device for POS58: usb://Unknown/Printer\n")
    found = CupsAdapter.parse_lpstat(out)
    assert [p.options["printer"] for p in found] == ["Brother_HL_L2350DW", "EPSON_TM_T20III", "POS58"]
    assert found[1].suggested_profile == "thermal-80mm"
    assert found[2].suggested_profile == "thermal-58mm"
    assert found[0].suggested_profile in ("a4", "letter")


def test_default_page_profile_by_locale(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LC_PAPER", "en_US.UTF-8")
    assert default_page_profile() == "letter" == suggest_profile("Canon PIXMA")
    monkeypatch.setenv("LC_PAPER", "en_NZ.UTF-8")
    assert default_page_profile() == "a4"


# ------------------------------------------------------------------ escpos

@pytest.mark.parametrize("profile,cut", [("thermal-80mm", True), ("thermal-58mm", False)])
def test_escpos_dry_run_raster(sample_brief, tmp_path, profile, cut):
    dump = tmp_path / "out.bin"
    a = get_adapter("escpos", dry_run=True, connection="usb", vendor_id="0x0416", product_id="0x5011",
                    dump_path=str(dump))
    assert a.formats_for(load_profile(profile)) == ("raster",)
    res = a.send(job(sample_brief, profile, ("raster",)))
    data = res.data["escpos"]
    assert res.ok and res.dry_run and dump.read_bytes() == data
    assert b"\x1dv0" in data  # GS v 0 raster bit image
    assert (b"\x1dV" in data) == cut  # GS V cut only with a cutter
    assert len(data) > 10_000


def test_escpos_rejects_too_wide_image(sample_brief):
    j = job(sample_brief, "thermal-80mm", ("raster",))
    j.rendered.profile = load_profile("thermal-58mm")
    with pytest.raises(AdapterError, match="576px"):
        get_adapter("escpos", dry_run=True).send(j)


def test_escpos_requires_connection_options():
    a = get_adapter("escpos", connection="network")
    with pytest.raises(AdapterError, match="host"):
        a._printer()


# ------------------------------------------------------------------ cat printer

def test_catprinter_packet_format():
    p = catprinter.packet(0xA1, b"\x50\x00")
    assert p[:6] == bytes([0x51, 0x78, 0xA1, 0x00, 0x02, 0x00]) and p[-1] == 0xFF
    assert catprinter.crc8(b"123456789") == 0xF4  # CRC-8/SMBUS check value
    assert catprinter.encode_line([True] + [False] * 7 + [True])[:2] == bytes([0x01, 0x01])


def test_catprinter_dry_run(sample_brief):
    j = job(sample_brief, "thermal-58mm", ("raster",))
    res = get_adapter("catprinter", dry_run=True).send(j)
    lines = j.rendered.images[0].height
    assert res.ok and res.data["bytes"] > lines * (48 + 8)
    assert res.data["stream"].count(bytes([0x51, 0x78, 0xA2])) == lines
    with pytest.raises(AdapterError, match="not implemented"):
        get_adapter("catprinter", address="AA:BB").send(j)
    with pytest.raises(AdapterError, match="384px"):
        get_adapter("catprinter", dry_run=True).send(job(sample_brief, "thermal-80mm", ("raster",)))


# ------------------------------------------------------------------ email

def test_email_dry_run_builds_pdf_message(sample_brief):
    a = get_adapter("email", dry_run=True, to="abc123@hpeprint.com", username="me@example.com")
    assert a.formats_for(load_profile("thermal-80mm")) == ("pdf",)
    res = a.send(job(sample_brief, "a4", ("pdf",)))
    msg = email.message_from_bytes(res.data["message"].as_bytes())
    assert msg["To"] == "abc123@hpeprint.com" and msg["From"] == "me@example.com"
    att = [p for p in msg.walk() if p.get_content_type() == "application/pdf"]
    assert att and att[0].get_filename() == "daily-times-2026-10-21-a4.pdf"
    assert att[0].get_payload(decode=True).startswith(b"%PDF")
    with pytest.raises(AdapterError, match="to"):
        get_adapter("email", dry_run=True).send(job(sample_brief, "a4", ("pdf",)))


# ------------------------------------------------------------------ file

def test_file_adapter_writes_and_dry_runs(sample_brief, tmp_path):
    a = get_adapter("file", directory=str(tmp_path), formats=["pdf", "png"])
    assert a.formats_for(load_profile("a4")) == ("pdf", "raster")
    from dailytimes.blocks import Brief

    long = Brief.model_validate({"blocks": [{"type": "text", "text": f"Item {i} " + "word " * 25}
                                            for i in range(40)]})
    res = a.send(job(long, "a5", ("pdf", "raster")))
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names[:2] == ["daily-times-2026-10-21-a5-p1.png", "daily-times-2026-10-21-a5-p2.png"]
    assert names[-1] == "daily-times-2026-10-21-a5.pdf"
    assert res.ok and not res.dry_run
    dry = get_adapter("file", dry_run=True, directory=str(tmp_path / "dry"))
    res = print_brief(sample_brief, load_profile("thermal-58mm"), dry, date=DAY)
    assert res.dry_run and res.artifacts[0].endswith("daily-times-2026-10-21-thermal-58mm.png")
    assert not (tmp_path / "dry").exists()
    named = get_adapter("file", dry_run=True, directory=str(tmp_path), name="brief-{profile}")
    assert named.send(job(sample_brief, "a4", ("pdf",))).artifacts == [str(tmp_path / "brief-a4.pdf")]


# ------------------------------------------------------------------ calibration / CLI

def test_test_print_cli_dry_run(capsys, tmp_path):
    from dailytimes.cli import main

    main(["test-print", "--profile", "thermal-80mm", "--adapter", "escpos", "--dry-run",
          "--opt", "connection=network", "--opt", "host=10.0.0.9"])
    assert "DRY RUN: encoded" in capsys.readouterr().out
    main(["test-print", "--profile", "a4", "--adapter", "file", "--opt", f"directory={tmp_path}"])
    assert any(p.suffix == ".pdf" for p in tmp_path.iterdir())


def test_calibration_brief_valid_for_all_profiles():
    from dailytimes.render import list_profiles

    for p in list_profiles():
        b = calibration_brief(p, "file")
        assert b.title == "Calibration" and render(b, p, ("pdf", "raster"), DAY).pdf


def test_printers_cli_handles_none(monkeypatch, capsys):
    import dailytimes.adapters as A
    from dailytimes.cli import main

    monkeypatch.setattr(A, "discover_all", lambda timeout: [])
    main(["printers", "--timeout", "0.1"])
    assert "No printers found" in capsys.readouterr().out

    found = CupsAdapter.parse_lpstat("device for EPSON_TM_T20III: usb://EPSON/TM-T20III\n")
    monkeypatch.setattr(A, "discover_all", lambda timeout: found)
    main(["printers"])
    out = capsys.readouterr().out
    assert "EPSON_TM_T20III" in out and "thermal-80mm" in out

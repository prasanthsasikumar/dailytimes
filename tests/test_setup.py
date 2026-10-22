"""`dailytimes setup` against an in-process server: pairing, config file, test print."""

import stat

import pytest
import yaml
from fastapi.testclient import TestClient

from dailytimes.agent.config import load_config
from dailytimes.agent.setup import SetupError, run_setup, service_file
from dailytimes.relay.app import create_app
from dailytimes.relay.settings import Settings


@pytest.fixture
def server():
    app = create_app(":memory:", Settings.from_env(public_url="http://testserver", secret="x" * 64))
    with TestClient(app) as c:
        user = app.state.store.create_user("p@example.com", "Pacific/Auckland")
        c.code = app.state.store.create_pairing(user, "Hall printer")
        c.store = app.state.store
        yield c


def test_unattended_setup_writes_private_config(server, tmp_path, capsys):
    cfg = tmp_path / "agent.yaml"
    run_setup("http://testserver", server.code, config_path=cfg, yes=True, profile="thermal-58mm", adapter="file",
              options={"directory": str(tmp_path / "out"), "formats": ["png"]}, http=server)
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    data = yaml.safe_load(cfg.read_text())
    assert data["relay"]["url"] == "http://testserver" and data["relay"]["token"].startswith("dtd_")
    assert data["outputs"] == [{"profile": "thermal-58mm", "adapter": "file",
                                "options": {"directory": str(tmp_path / "out"), "formats": ["png"]}}]
    assert load_config(cfg).rules.print_after.strftime("%H:%M") == "06:30"
    assert list((tmp_path / "out").glob("*.png"))  # test print happened
    assert server.store.device_for_token(data["relay"]["token"]).name == "Hall printer"
    assert "Paired as “Hall printer”" in capsys.readouterr().out


def test_interactive_setup_choices(server, tmp_path, monkeypatch):
    import dailytimes.adapters as A

    monkeypatch.setattr(A, "discover_all", lambda timeout: [])
    answers = iter(["1", str(tmp_path / "pdfs"), "", "n", "2", "4"])  # folder, path, profile default, no test, PIR pin 4
    run_setup("http://testserver", server.code, config_path=tmp_path / "a.yaml", ask=lambda q: next(answers),
              http=server)
    data = yaml.safe_load((tmp_path / "a.yaml").read_text())
    assert data["outputs"][0]["adapter"] == "file" and data["outputs"][0]["options"]["directory"] == str(tmp_path / "pdfs")
    assert data["outputs"][0]["profile"] in ("a4", "letter")
    assert data["presence"] == {"backend": "gpio", "options": {"pin": 4}}


def test_bad_code(server, tmp_path):
    with pytest.raises(SetupError, match="expired"):
        run_setup("http://testserver", "ZZZZ-ZZZZ", config_path=tmp_path / "x.yaml", yes=True, adapter="file",
                  http=server)


def test_service_file_mentions_config(tmp_path):
    dest, body, cmds = service_file(tmp_path / "agent.yaml")
    assert str(tmp_path / "agent.yaml") in body and cmds

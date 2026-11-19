from fastapi.testclient import TestClient

from kairos_backend.channels import demo_recipients as dr
from kairos_backend.channels.phone_demo import MessagesAppSmsProvider


def test_add_normalises_indian_mobile_and_masks(tmp_path, monkeypatch):
    env = {"KAIROS_DEMO_RECIPIENTS_FILE": str(tmp_path / "r.json")}
    assert dr.add("98765 43210", env) == "+919876543210"
    assert dr.add("+91-9876543210", env) == "+919876543210"
    assert dr.load(env) == frozenset({"+919876543210"})
    assert all("98765" not in m for m in dr.masked(env))
    assert dr.remove("9876543210", env) and not dr.load(env)


def test_runtime_recipient_is_allowed_but_still_dry_run(tmp_path):
    env = {"KAIROS_DEMO_RECIPIENTS_FILE": str(tmp_path / "r.json"), "KAIROS_DEMO_SMS": "1"}
    dr.add("9876543210", env)
    p = MessagesAppSmsProvider(env=env)        # dry_run defaults to True
    assert p._check({"instruction_id": "i1", "to": "+919876543210"}) == "+919876543210"
    assert p.dry_run is True


def test_api_routes_mask(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DEMO_RECIPIENTS_FILE", str(tmp_path / "r.json"))
    from kairos_backend.api import app
    c = TestClient(app)
    r = c.post("/demo/recipients", json={"number": "9876543210"})
    assert r.status_code == 200 and "43210" not in r.text and "9876543210" not in r.text
    assert c.post("/demo/recipients", json={"number": "12"}).status_code == 422
    assert c.get("/demo/recipients").json()["recipients"]
    assert c.delete("/demo/recipients/9876543210").json()["removed"] is True

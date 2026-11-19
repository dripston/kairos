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


def test_remove_by_masked_form_or_last4_never_needs_the_full_number(tmp_path):
    from kairos_backend.channels.phone_demo import DemoSafetyError
    import pytest
    env = {"KAIROS_DEMO_RECIPIENTS_FILE": str(tmp_path / "r.json")}
    dr.add("9876543210", env)
    dr.add("9123401234", env)
    assert dr.resolve("+********3210", env) == "+919876543210"
    assert dr.remove_matching("3210", env) and dr.load(env) == frozenset({"+919123401234"})
    assert dr.remove_matching("0000", env) is False
    dr.add("9000001234", env)
    with pytest.raises(DemoSafetyError):     # two numbers end in 1234: refuse, remove nothing
        dr.remove_matching("1234", env)
    with pytest.raises(DemoSafetyError):
        dr.remove_matching("12", env)
    assert len(dr.load(env)) == 2


def test_api_delete_by_hint_or_body_keeps_the_number_out_of_the_url(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DEMO_RECIPIENTS_FILE", str(tmp_path / "r.json"))
    from kairos_backend.api import app
    c = TestClient(app)
    c.post("/demo/recipients", json={"number": "9876543210"})
    c.post("/demo/recipients", json={"number": "9123401234"})
    masked = c.get("/demo/recipients").json()["recipients"]
    one = next(m for m in masked if m.endswith("3210"))
    assert c.delete(f"/demo/recipients/{one}").json()["removed"] is True       # masked form in the path
    r = c.request("DELETE", "/demo/recipients", json={"hint": "1234"})          # last 4 in a JSON body
    assert r.json()["removed"] is True and r.json()["recipients"] == []
    c.post("/demo/recipients", json={"number": "9876543210"})
    assert c.request("DELETE", "/demo/recipients", json={"number": "9876543210"}).json()["removed"] is True
    assert c.request("DELETE", "/demo/recipients", json={}).status_code == 422

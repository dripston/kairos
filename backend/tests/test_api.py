"""Smoke test of the HTTP layer on the K03 spine: load data, enroll, decide, dispatch, pay, STOP."""
import importlib
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from spine.config import DATASET_AS_OF_DATE as AS_OF  # 2026-08-07, a Friday
from spine.synthetic_dataset import write_dataset


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("KAIROS_IMPORT_DIR", str(tmp_path / "imports"))
    (tmp_path / "imports").mkdir()
    import kairos_backend.api as api
    api = importlib.reload(api)
    return TestClient(api.app), api


def test_end_to_end(client):
    c, api = client
    assert c.get("/health").json()["policy_version"] == "1.1.0"

    write_dataset(os.path.join(os.environ["KAIROS_IMPORT_DIR"], "syn.csv"), 20, seed=42)
    assert c.post("/spine/import", json={"csv_path": "syn.csv"}).json()["loaded"] == 20

    c.put("/campaigns", json={"id": "C1", "budget_per_case_paise": 5_000, "start_date": str(AS_OF),
                              "end_date": "2026-08-20"})
    c.put("/templates", json={"id": "SMS_EN", "channel": "sms", "lang": "en"})
    # first synthetic row: SL100001, consent on SMS, DPD 1 on the as-of date
    assert c.post("/campaigns/C1/enroll", json={"loan_ids": ["SL100001"]}).json()["enrolled"] == 1

    body = {"borrower_id": "BRW-SL100001", "obligation_id": "SL100001-OD1", "campaign_id": "C1", "channel": "sms",
            "template_id": "SMS_EN", "at": f"{AS_OF}T10:00:00", "est_cost_paise": 20}
    d = c.post("/decide", json=body).json()
    assert d["action"] == "SEND" and d["reserved"], d

    # FSM-F1: internal statuses are not provider words (422); a receipt before dispatch changes nothing
    for word in ("DISPATCHING", "RESERVED", "bogus"):
        assert c.post(f"/provider-status/{d['instruction_id']}", json={"status": word}).status_code == 422
    early = c.post(f"/provider-status/{d['instruction_id']}", json={"status": "DELIVERED"})
    assert early.status_code == 200 and early.json()["status"] == "RESERVED"

    assert c.post(f"/dispatch/{d['instruction_id']}").json()["outcome"] == "SENT"
    blocked = c.post("/decide", json={**body, "at": f"{AS_OF}T11:00:00"}).json()
    assert (blocked["action"], blocked["reason"]) == ("WAIT", "SAME_CHANNEL_GAP")

    pay = {"obligation_id": "SL100001-OD1", "amount_paise": 100, "at": f"{AS_OF}T12:00:00",
           "payment_id": "PAY-API-1", "reference": "UTR-API-1"}
    assert c.post("/events/payment-verified", json=pay).json()["applied"]
    dup_ref = c.post("/events/payment-verified", json={**pay, "payment_id": "PAY-API-2"})
    assert dup_ref.status_code == 409  # same bank reference again → rejected by K03

    c.post("/events/stop", json={"borrower_id": "BRW-SL100001", "at": f"{AS_OF}T13:00:00", "channel": "sms"})
    after = c.post("/decide", json={**body, "at": f"{AS_OF}T15:00:00"}).json()
    assert (after["action"], after["reason"]) == ("CANCEL", "STOP_OPT_OUT")
    assert c.get("/cap-breaches").json()["count"] == 0
    b = c.get("/borrowers/BRW-SL100001").json()
    assert {e["event_type"] for e in b["events"]} >= {"touch_sent", "payment_received", "consent_changed"}
    # PII: the phone is masked and the e-mail left out unless policy_owner asks
    raw = api.ledger._conn().execute("SELECT phone FROM borrowers WHERE borrower_id='BRW-SL100001'").fetchone()[0]
    assert "email" not in b and b["phone"] != raw and b["phone"].endswith(raw[-4:]) and raw not in json.dumps(b)
    full = c.get("/borrowers/BRW-SL100001", headers={"X-Kairos-Role": "policy_owner"}).json()
    assert full["phone"] == raw and "email" in full


def test_unknown_borrower_is_404(client):
    c, _ = client
    assert c.get("/borrowers/NOPE").status_code == 404


def test_unknown_channel_and_wrong_typed_gate_facts_are_422(client):
    """RT-F03: malformed enums / wrong-typed facts are the caller's error (422), never a 500."""
    c, _ = client
    t = "2026-11-20T10:00:00"
    for url, body in [("/events/opt-in", {"borrower_id": "B1", "channel": "telegram", "consent_version": "v", "at": t}),
                      ("/events/stop", {"borrower_id": "B1", "channel": "telegram", "at": t}),
                      ("/events/response", {"borrower_id": "B1", "channel": "telegram", "at": t}),
                      ("/events/call", {"borrower_id": "B1", "channel": "telegram", "at": t, "answered_seconds": 3}),
                      ("/decide", {"borrower_id": "B1", "obligation_id": "B1-A-1", "campaign_id": "C1",
                                   "channel": "telegram", "template_id": None, "at": t, "est_cost_paise": 20})]:
        assert c.post(url, json=body).status_code == 422, url
    gate = {"at": "2026-11-20T10:00:00+05:30", "channel": "sms", "dpd": 12}
    assert c.post("/gate/evaluate", json=gate).json()["decision"] == "SEND"
    for bad in ({"dpd": "12"}, {"sends_today_channel": "x"}, {"consent": "yes"}, {"channel": "telegram"},
                {"at": "garbage"}, {"no_such_fact": 1}):
        assert c.post("/gate/evaluate", json={**gate, **bad}).status_code == 422, bad


def test_unknown_borrower_is_404_on_every_borrower_scoped_write(client):
    """RT-F02: an unknown borrower id is a 404 and writes nothing (no stop-for-day row, no ticket, no audit)."""
    c, api = client
    nb, t = "NO-SUCH-BORROWER", "2026-11-20T10:00:00"
    ins = {"borrower_id": nb, "obligation_id": f"{nb}-A-1", "campaign_id": "C1", "channel": "sms",
           "template_id": None, "at": t, "est_cost_paise": 20}
    for method, url, body in [
            ("POST", "/events/response", {"borrower_id": nb, "at": t, "channel": "sms"}),
            ("POST", "/events/call", {"borrower_id": nb, "at": t, "answered_seconds": 30}),
            ("POST", "/events/stop", {"borrower_id": nb, "at": t}),
            ("POST", "/events/opt-in", {"borrower_id": nb, "channel": "sms", "consent_version": "v1", "at": t}),
            ("POST", "/events/exception", {"borrower_id": nb, "at": t, "event": "DISPUTE", "band": "B1"}),
            ("POST", "/events/exception", {"borrower_id": nb, "at": t, "event": "ABUSIVE", "band": "B1"}),
            ("PUT", f"/borrowers/{nb}/lender", {"lender_id": "EXAMPLE"}),
            ("POST", f"/control/supervisor-clear/{nb}", None),
            ("POST", "/decide", ins),
            ("POST", "/preview", ins)]:
        r = c.request(method, url, json=body)
        assert r.status_code == 404, (url, r.status_code, r.text)
    db = api.ledger._conn()
    for table in ("stop_for_day", "tickets", "borrower_state", "touches", "consents", "events", "audit"):
        assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE borrower_id=?", (nb,)).fetchone()[0] == 0, table


def test_decide_reused_instruction_id_for_another_borrower_is_409(client):
    """RT-F07 at the API: 409 with reason CONFLICT_INSTRUCTION_ID and reserved=false."""
    c, api = client
    csv_path = os.path.join(tempfile.mkdtemp(), "syn.csv")
    write_dataset(csv_path, 3, seed=42)
    api.ledger.import_dataset(csv_path)
    c.put("/campaigns", json={"id": "C1", "budget_per_case_paise": 5_000, "start_date": str(AS_OF),
                              "end_date": "2026-08-20"})
    c.put("/templates", json={"id": "SMS_EN", "channel": "sms", "lang": "en"})
    c.post("/campaigns/C1/enroll", json={"loan_ids": ["SL100001", "SL100002"]})
    body = {"borrower_id": "BRW-SL100001", "obligation_id": "SL100001-OD1", "campaign_id": "C1", "channel": "sms",
            "template_id": "SMS_EN", "at": f"{AS_OF}T10:00:00", "est_cost_paise": 20, "instruction_id": "SHARED-1"}
    assert c.post("/decide", json=body).json()["reserved"]
    r = c.post("/decide", json={**body, "borrower_id": "BRW-SL100002", "obligation_id": "SL100002-OD1"})
    assert r.status_code == 409
    assert (r.json()["detail"]["reason"], r.json()["detail"]["reserved"]) == ("CONFLICT_INSTRUCTION_ID", False)
    assert c.post("/decide", json=body).json()["reason"] == "ALREADY_RESERVED"


def test_spine_import_is_confined_to_the_import_dir_and_echoes_no_values(client, tmp_path):
    """RT-F10: only files under KAIROS_IMPORT_DIR; outside paths are a 403 whether or not they exist;
    rejected rows are line + column + rule, never the raw value."""
    import csv as _csv

    from spine.synthetic_dataset import COLUMNS

    c, _ = client
    imports = tmp_path / "imports"
    outside = tmp_path / "outside.csv"
    write_dataset(str(outside), 2, seed=1)
    (imports / "link.csv").symlink_to(outside)
    for path in (str(outside), "../outside.csv", "link.csv", "/etc/hosts", "/definitely/not/here.csv", ".", ""):
        r = c.post("/spine/import", json={"csv_path": path})
        assert r.status_code == 403 and "outside" not in r.text and "hosts" not in r.text, path
    assert c.post("/spine/import", json={"csv_path": "nope.csv"}).status_code == 404
    (imports / "junk.csv").write_bytes(b"\xff\xfe\x00junk")
    assert c.post("/spine/import", json={"csv_path": "junk.csv"}).status_code == 422

    row = {col: "" for col in COLUMNS} | {"Loan ID": "X1", "Name": "n", "Mobile Number": "TOKEN-SECRET-42",
                                          "Product": "p", "EMI Amount": "1", "POS": "1", "TOS": "1",
                                          "Bounce Date": "2026-08-01", "Remaining EMIs": "1", "Risk Tagging": "r",
                                          "Payment Link": "l"}
    bad = [row, {**row, "Loan ID": "X2", "Mobile Number": "0123456789", "EMI Amount": "SECRET-EMI"},
           {**row, "Loan ID": "X3", "Mobile Number": "0123456789", "Name": ""}]
    (imports / "sub").mkdir()
    with open(imports / "sub" / "bad.csv", "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(bad)
    r = c.post("/spine/import", json={"csv_path": str(imports / "sub" / "bad.csv")})  # absolute, but inside
    assert r.status_code == 200 and "SECRET" not in r.text and r.json()["file"] == os.path.join("sub", "bad.csv")
    assert r.json()["rejected"] == [{"line": 2, "column": "Mobile Number", "rule": "10_digits"},
                                    {"line": 3, "column": "EMI Amount", "rule": "number"},
                                    {"line": 4, "column": "Name", "rule": "required"}]


def test_events_since_is_a_cursor_over_the_audit(client):
    c, api = client
    csv_path = os.path.join(tempfile.mkdtemp(), "syn.csv")
    write_dataset(csv_path, 5, seed=42)
    api.ledger.import_dataset(csv_path)  # POST /spine/import is confined to KAIROS_IMPORT_DIR (RT-F10)
    c.put("/campaigns", json={"id": "C1", "budget_per_case_paise": 5_000, "start_date": str(AS_OF), "end_date": "2026-08-20"})
    c.put("/templates", json={"id": "SMS_EN", "channel": "sms", "lang": "en"})
    c.post("/campaigns/C1/enroll", json={"loan_ids": ["SL100001"]})
    first = c.get("/events/since").json()
    assert first["provenance"] == "SIMULATED" and first["events"]
    body = {"borrower_id": "BRW-SL100001", "obligation_id": "SL100001-OD1", "campaign_id": "C1", "channel": "sms",
            "template_id": "SMS_EN", "at": f"{AS_OF}T10:00:00", "est_cost_paise": 20}
    c.post("/decide", json=body)
    nxt = c.get("/events/since", params={"cursor": first["cursor"]}).json()
    assert [e["event_type"] for e in nxt["events"]] == ["supervisor.decision"]
    e = nxt["events"][0]
    assert e["decision"] == "SEND" and e["policy_version"] == "1.1.0" and e["detail"]["channel"] == "sms"
    assert c.get("/events/since", params={"cursor": nxt["cursor"]}).json()["events"] == []

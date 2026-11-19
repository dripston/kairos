"""Smoke test of the HTTP layer on the K03 spine: load data, enroll, decide, dispatch, pay, STOP."""
import importlib
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from spine.config import DATASET_AS_OF_DATE as AS_OF  # 2026-08-07, a Friday
from spine.synthetic_dataset import write_dataset


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    return TestClient(api.app), api


def test_end_to_end(client):
    c, api = client
    assert c.get("/health").json()["policy_version"] == "1.1.0"

    csv_path = os.path.join(tempfile.mkdtemp(), "syn.csv")
    write_dataset(csv_path, 20, seed=42)
    assert c.post("/spine/import", json={"csv_path": csv_path}).json()["loaded"] == 20

    c.put("/campaigns", json={"id": "C1", "budget_per_case_paise": 5_000, "start_date": str(AS_OF),
                              "end_date": "2026-08-20"})
    c.put("/templates", json={"id": "SMS_EN", "channel": "sms", "lang": "en"})
    # first synthetic row: SL100001, consent on SMS, DPD 1 on the as-of date
    assert c.post("/campaigns/C1/enroll", json={"loan_ids": ["SL100001"]}).json()["enrolled"] == 1

    body = {"borrower_id": "BRW-SL100001", "obligation_id": "SL100001-OD1", "campaign_id": "C1", "channel": "sms",
            "template_id": "SMS_EN", "at": f"{AS_OF}T10:00:00", "est_cost_paise": 20}
    d = c.post("/decide", json=body).json()
    assert d["action"] == "SEND" and d["reserved"], d

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


def test_unknown_borrower_is_404(client):
    c, _ = client
    assert c.get("/borrowers/NOPE").status_code == 404


def test_events_since_is_a_cursor_over_the_audit(client):
    c, api = client
    csv_path = os.path.join(tempfile.mkdtemp(), "syn.csv")
    write_dataset(csv_path, 5, seed=42)
    c.post("/spine/import", json={"csv_path": csv_path})
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

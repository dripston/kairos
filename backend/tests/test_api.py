"""Smoke test of the HTTP layer: set up, decide, dispatch, STOP — end to end."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    return TestClient(api.app)


def test_end_to_end(client):
    assert client.get("/health").json()["policy_version"] == "1.1.0"
    client.put("/campaigns", json={"id": "C1", "budget_per_case_inr": 100, "allocated_cases": 10,
                                   "start_date": "2026-11-20", "end_date": "2026-11-30"})
    client.put("/templates", json={"id": "SMS_EN", "channel": "sms", "lang": "en"})
    client.put("/borrowers", json={"id": "B1", "dpd": 42, "dpd_updated_at": "2026-11-20T07:00:00",
                                   "consent": {"sms": True, "whatsapp": True}})
    client.put("/obligations", json={"id": "B1-A", "borrower_id": "B1", "campaign_id": "C1"})

    body = {"borrower_id": "B1", "obligation_id": "B1-A", "campaign_id": "C1", "channel": "sms",
            "template_id": "SMS_EN", "at": "2026-11-20T10:00:00", "est_cost_inr": 0.2}
    d = client.post("/decide", json=body).json()
    assert d["action"] == "SEND" and d["reserved"]

    sent = client.post(f"/dispatch/{d['instruction_id']}").json()
    assert sent["outcome"] == "SENT"

    blocked = client.post("/decide", json={**body, "at": "2026-11-20T11:00:00"}).json()
    assert (blocked["action"], blocked["reason"]) == ("WAIT", "SAME_CHANNEL_GAP")

    client.post("/events/stop", json={"borrower_id": "B1", "at": "2026-11-20T12:00:00", "channel": "sms"})
    after = client.post("/decide", json={**body, "at": "2026-11-20T15:00:00"}).json()
    assert (after["action"], after["reason"]) == ("CANCEL", "STOP_OPT_OUT")
    assert client.get("/cap-breaches").json()["count"] == 0


def test_unknown_borrower_is_404(client):
    r = client.get("/borrowers/NOPE")
    assert r.status_code == 404

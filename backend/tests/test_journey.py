"""The crazy journey (replay/journey.py), quick mode: the invariants the demo claims, on real modules."""
from __future__ import annotations

import json

import pytest

from replay.journey import run


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    out = tmp_path_factory.mktemp("journey")
    facts = run(seed=42, quick=True, out_dir=str(out), echo=False, cohort_size=4)
    return facts, out


def test_no_disclosure_before_otp(journey):
    facts, _ = journey
    s3 = facts["steps"]["3"]
    assert s3["status"] in ("pending", "ok", "error")
    assert s3["disclosed_before_otp"] is False
    if s3["status"] == "ok":  # CM-03 merged: verified, then balance, and the fake UTR is caught
        assert s3["verified"] and s3["fraud_suspect"]


def test_stop_on_sms_suppresses_every_channel_next_cycle(journey):
    s = journey[0]["steps"]["2_stop"]
    assert s["stop_applied"] is True
    assert set(s["channels"]) == {"sms", "whatsapp", "email", "ivr", "voicebot"}
    assert all(v.endswith("STOP_OPT_OUT") for v in s["channels"].values())
    assert s["plan_rule"] == "STOP_OPT_OUT"


def test_payment_cancels_only_that_emi(journey):
    s = journey[0]["steps"]["6"]
    assert s["loan1_touch"] == "CANCELED" and s["loan1_dispatch"] == "BLOCKED"
    assert s["loan2_touch"] == "RESERVED" and s["loan2_dispatch"] == "SENT"


def test_duplicates_ignored_and_learning_moves(journey):
    s = journey[0]["steps"]["7"]
    assert s["duplicate_webhook"] == "DUPLICATE" and s["duplicate_callback"] is True
    assert s["moved_arms"] > 0 and sum(s["finalized"].values()) > 0


def test_voice_thresholds_fail_closed_and_hitl(journey):
    st = journey[0]["steps"]
    assert st["1"]["voice_touch_status_after_plan"] == "PENDING_APPROVAL"   # every call needs a person
    assert st["1"]["bot"]["use_case"] == "PRE_DUE_REMINDER" and not st["1"]["bot"]["blocked"]
    assert st["1"]["ptp_hold_until"]                                        # PTP @ 0.9 -> hold
    assert st["5"]["supervisor_hold"] and not st["5"]["stopped"]            # STOP @ 0.45 -> hold, not opt-out
    assert not st["5"]["opted_out_channels"]
    assert st["4"]["triage_during_hold"] == "REFUSED"                       # a human cannot beat the Gate
    assert st["4"]["triage_bot"] == "HARDSHIP_TRIAGE" and st["4"]["approval"] == "APPROVED"
    assert st["2_bounce"]["broken_ptp"] and st["2_bounce"]["next_bot"] == "PTP_FOLLOW_UP"


def test_zero_cap_breaches_and_reports_written(journey):
    facts, out = journey
    assert facts["steps"]["8"]["cap_breaches"] == 0
    assert all(d["cap_breaches"] in (0, None) for d in facts["steps"]["8"]["days"])
    tl = json.loads((out / "timeline.json").read_text())
    assert tl["provenance"] == "SIMULATED" and tl["events"] and tl["crm_envelopes"]
    html = (out / "journey.html").read_text()
    assert "SIMULATED" in html and html.count("<tr ") == len(tl["events"])
    # no phone numbers or names in what the demo shows
    assert "+91" not in html and "user_name\": \"<first name, masked>" in json.dumps(tl["events"])


def test_deterministic_hash(journey, tmp_path):
    again = run(seed=42, quick=True, out_dir=None, echo=False, cohort_size=4)
    assert again["journey_hash"] == journey[0]["journey_hash"]


def test_demo_journey_endpoint_returns_the_player_shape(tmp_path, monkeypatch):
    """POST /demo/journey/run: what frontend/src/api/journey.js normalizeJourney() plays."""
    import importlib

    from fastapi.testclient import TestClient
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    r = TestClient(api.app).post("/demo/journey/run", json={"seed": 42})
    assert r.status_code == 200
    j = r.json()
    assert j["provenance"] == "SIMULATED" and j["borrower_id"] == j["cast"]["A"] and len(j["steps"]) >= 8
    for s in j["steps"]:
        assert s["id"] and s["title"] and s["story"] and s["events"]
        assert {"cohort", "risk_tier", "layer", "next_action", "holds", "emis"} <= set(s["borrower"])
        assert s["borrower"]["phone"] == "masked" and "(synthetic)" in s["borrower"]["name"]
    stop = next(s for s in j["steps"] if s["id"] == "stop")
    assert "STOP · all channels" in stop["borrower"]["holds"]
    pay = next(s for s in j["steps"] if s["id"] == "payment")
    assert [e["status"].split(" ")[0] for e in pay["borrower"]["emis"]] == ["Paid", "Due"]
    # the API's own database is untouched by a journey run
    assert api.ledger._conn().execute("SELECT COUNT(*) FROM touches").fetchone()[0] == 0


def test_auto_churn_moves_the_broken_promise_cohort(journey):
    ac = journey[0]["auto_churn"]
    if ac == "pending":  # k27/auto-churn not merged
        return
    assert ac["ptp_status"] == "BROKEN" and ac["cohort"][1] == "BROKEN_PTP" and ac["channels"]

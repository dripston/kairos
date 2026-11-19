"""Journey 1 — /demo/journey/outbound (replay/outbound_journey.py + kairos_backend/journey_outbound.py).

The page's events come from the real engines (ledger rows back every event), human controls go through the real
HITL / Ledger control plane and the Gate (an override at 20:00 is refused by CONTACT_WINDOW, locked), STOP cancels
every queued touch and opts out every channel, pause / kill switch halt dispatch, approve / reject change what
happens, a run is deterministic per seed, and nothing in the output carries PII. All SIMULATED, dry-run."""
from __future__ import annotations

import importlib
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kairos_backend import journey_outbound as JR
from kairos_backend.ledger import QUEUED
from replay.outbound_journey import CHANNELS, OutboundJourney

VIKRAM, AARAV = "BRW-SL100073", "BRW-SL100174"
PHONE = re.compile(r"(?<![\w-])\d{10}(?!\w)|\+91")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(JR.router)
    c = TestClient(app)
    yield c
    JR.manager.close()


@pytest.fixture
def journey():
    made = []

    def make(**kw):
        s = OutboundJourney(**kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def _run_to(s: OutboundJourney, day: int, phase: str = "window"):
    """Step until the 0-based `day` reaches `phase` ('window' = after that day's plan)."""
    while not (s.day == day and s.phase == phase):
        assert s.phase != "finished"
        s.step()


def _touches(s, bid=None):
    q = "SELECT * FROM touches" + (" WHERE borrower_id=?" if bid else "") + " ORDER BY scheduled_at"
    return s.worker.call(lambda: [dict(r) for r in s.r.ledger._conn().execute(q, (bid,) if bid else ())])


def _audit_types(s, bid=None):
    q = "SELECT type FROM audit" + (" WHERE borrower_id=?" if bid else "")
    return s.worker.call(lambda: [r["type"] for r in s.r.ledger._conn().execute(q, (bid,) if bid else ())])


def _spot(s, comp=None, kind=None):
    return [e for e in s.events if e.get("spot") and (comp is None or e["component"] == comp)
            and (kind is None or e["kind"] == kind)]


# ---------------- API ----------------
def test_api_page_presets_start_step_state(client):
    page = client.get("/demo/journey/outbound")
    assert page.status_code == 200 and "SIMULATED" in page.text and "Aladdin" in page.text
    assert not re.search(r"""(src|href)=["']https?://""", page.text)          # offline-safe: no external assets
    p = client.get("/demo/journey/outbound/api/presets").json()
    assert [x["borrower_id"] for x in p["presets"]] == [VIKRAM, AARAV, "BRW-SL100067"]
    assert p["stages"][0] == "churn" and p["stages"][-1] == "payments"
    assert client.post("/demo/journey/outbound/api/step").status_code == 409          # nothing started yet
    st = client.post("/demo/journey/outbound/api/start", json={"spotlight": VIKRAM}).json()
    assert st["spotlight"] == VIKRAM and st["provenance"] == "SIMULATED" and st["snapshot"]["phase"] == "ready"
    assert st["snapshot"]["card"]["first_name"] == "Vikram"
    s1 = client.post("/demo/journey/outbound/api/step").json()
    assert s1["snapshot"]["phase"] == "window" and s1["events"]
    assert {e["stage"] for e in s1["events"] if e.get("spot")} >= {"churn", "learner", "aladdin"}
    s2 = client.post("/demo/journey/outbound/api/step").json()
    assert s2["snapshot"]["phase"] == "ready"
    assert any(e.get("spot") and e["stage"] == "executor" for e in s2["events"])
    assert s2["events"][0]["id"] == s1["events"][-1]["id"] + 1                          # no gaps, no repeats
    again = client.get("/demo/journey/outbound/api/state", params={"cursor": s2["cursor"]}).json()
    assert again["events"] == [] and again["cursor"] == s2["cursor"]
    assert client.post("/demo/journey/outbound/api/start", json={"spotlight": "BRW-NOPE"}).status_code == 404


def test_api_is_mounted_on_the_main_app(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    paths = {getattr(r, "path", "") for r in api.app.routes}
    assert "/demo/journey/outbound" in paths and "/demo/journey/outbound/api/step" in paths


# ---------------- real engines ----------------
def test_events_come_from_the_real_modules(journey):
    s = journey()
    s.step()                                                     # day 1 plan
    contact = _spot(s, "learner", "contact")[0]
    assert contact["detail"]["channel"] == "sms" and contact["detail"]["scores"]
    assert any(x["chosen"] and x["draw"] is not None for x in contact["detail"]["scores"])   # the Ranking's own scores
    allowed = _spot(s, "aladdin", "allowed")[0]
    assert {g["channel"] for g in allowed["detail"]["checks"]} == set(CHANNELS)
    assert "STOP_OPT_OUT" in allowed["detail"]["rule_order"] and allowed["detail"]["budget"]["pool_left_paise"] > 0
    t = [x for x in _touches(s, VIKRAM) if x["status"] in QUEUED]
    assert len(t) == 1 and t[0]["channel"] == "sms"            # the ledger reserved exactly what the event says
    assert "DECISION" in _audit_types(s, VIKRAM)
    s.step()                                                     # day 1 sends
    sent = _touches(s, VIKRAM)[0]
    assert sent["status"] in ("SENT", "DELIVERED") and sent["provider_ref"]
    kinds = [(e["component"], e["kind"]) for e in _spot(s)]
    assert ("executor", "dispatched") in kinds and ("channel", "delivered") in kinds
    assert ("channel", "duplicate_ignored") in kinds
    assert "SEND_TIME_CHECK" in _audit_types(s, VIKRAM)
    s.step(), s.step()                                           # day 2: the SMS's 24 h window closes
    fin = _spot(s, "outcomes", "finalized")[0]
    assert fin["detail"]["status"] == "NO_REPLY" and fin["detail"]["learned"]
    assert fin["detail"]["after"] < fin["detail"]["before"]     # the posterior moved down after no reply


def test_the_spotlight_story_unfolds_without_a_script(journey):
    """Vikram: SMS, no reply → WhatsApp → promise (PTP) → promise kept → bank-verified payment → exit."""
    s = journey()
    while s.phase != "finished" and not _spot(s, "payments", "verified"):
        s.step()
    sends = [e["detail"]["channel"] for e in _spot(s, "executor", "dispatched")]
    assert sends == ["sms", "whatsapp"]
    assert [e["detail"]["label"] for e in _spot(s, "intent", "classified")] == ["PTP"]
    pay = _spot(s, "payments", "verified")[0]
    assert pay["day"] <= 8 and pay["detail"]["amount_paise"] == 740900
    assert any(e["detail"].get("to") == "PAID_EXIT" for e in _spot(s, "churn", "moved"))
    held = [e for e in _spot(s, "aladdin", "checks")
            if {g["reason"] for g in e["detail"]["checks"]} == {"PTP_HOLD", "NO_CONSENT"}]
    assert held       # during the promise every consented channel answers PTP_HOLD (voice bot: no consent)


# ---------------- human controls ----------------
def test_override_goes_through_the_gate_and_is_blocked_in_quiet_hours(journey):
    s = journey()
    s.step()                                                     # day 1, approval window
    out = s.act("override", mode="channel", channel="whatsapp", time="20:00")
    assert out["ok"] is False and out["refusal"]["code"] == "CONTACT_WINDOW"
    ev = [e for e in out["events"] if e["kind"] == "override_blocked"][0]
    assert ev["detail"]["verdict"]["rule"] == "CONTACT_WINDOW" and ev["detail"]["verdict"]["locked_by_guardrails"]
    assert "HUMAN_ACTION_REFUSED" in _audit_types(s, VIKRAM)
    assert [x["channel"] for x in _touches(s, VIKRAM) if x["status"] in QUEUED] == ["sms"]   # nothing changed
    assert out["snapshot"]["audit"][-1]["ok"] is False
    # an allowed override: the learner's SMS is cancelled, a manual WhatsApp is reserved for a second person
    pv = s.act("preview", channel="whatsapp", time="14:30")["result"]
    assert pv["allowed"] and pv["needs_approval"] == ["MANUAL_DISPATCH"] and pv["replaces"]["channel"] == "sms"
    ok = s.act("override", mode="channel", channel="whatsapp", time="14:30")
    assert ok["ok"]
    ts = {x["channel"]: x for x in _touches(s, VIKRAM)}
    assert ts["sms"]["status"] == "CANCELED" and ts["sms"]["reason"] == "CANCELED_BY_HUMAN"
    assert ts["whatsapp"]["status"] == "PENDING_APPROVAL"
    iid = ts["whatsapp"]["instruction_id"]
    mine = s.act("approve", approval_id=iid)                     # maker-checker: the presenter cannot approve it
    assert mine["ok"] is False and mine["refusal"]["code"] == "MAKER_CHECKER"
    s.step()                                                     # window closes: the second person approves; sends
    t = s.worker.call(lambda: s.r.ledger.touch(iid))
    assert t["status"] in ("SENT", "DELIVERED")
    disp = _spot(s, "executor", "dispatched")
    assert [e["detail"]["channel"] for e in disp] == ["whatsapp"] and disp[0]["t"].endswith("14:30")


def test_override_wait_cancels_todays_touch(journey):
    s = journey()
    s.step()
    out = s.act("override", mode="wait")
    assert out["ok"] and any(e["kind"] == "override_wait" for e in out["events"])
    s.step()
    assert not _spot(s, "executor", "dispatched")
    assert all(x["status"] == "CANCELED" for x in _touches(s, VIKRAM))
    # queued outside the window → applied right after the next plan
    s2 = journey()
    _run_to(s2, 2, "ready")                                      # evening of day 2, before day 3's plan
    q = s2.act("override", mode="wait")
    assert q["ok"] and q["result"]["queued"] and q["snapshot"]["override_queued"]["mode"] == "wait"
    s2.step()                                                    # day 3 plan: the learner picks WhatsApp, then WAIT
    assert any(e["kind"] == "override_wait" for e in s2.events if e["day"] == 3)
    s2.step()
    assert not [e for e in _spot(s2, "executor", "dispatched") if e["day"] == 3]


def test_stop_cancels_every_queued_touch_on_every_channel(journey):
    s = journey()
    s.step()
    queued = [x for x in _touches(s, VIKRAM) if x["status"] in QUEUED]
    assert queued
    out = s.act("stop")
    assert out["ok"] and out["result"]["canceled"] == len(queued)
    assert all(x["status"] == "CANCELED" for x in _touches(s, VIKRAM))
    cons = s.worker.call(lambda: [dict(r) for r in s.r.ledger._conn().execute(
        "SELECT channel, opted_out FROM consents WHERE borrower_id=?", (VIKRAM,))])
    assert cons and all(c["opted_out"] for c in cons)
    stop_ev = [e for e in out["events"] if e["kind"] == "stop"][0]
    assert all(not g["allowed"] and g["reason"] == "STOP_OPT_OUT" for g in stop_ev["detail"]["checks"])
    assert out["snapshot"]["card"]["stopped"] and set(out["snapshot"]["card"]["consents"].values()) == {"STOP"}
    s.step()
    assert not _spot(s, "executor", "dispatched")
    s.step()                                                     # next plan: Aladdin answers STOP on every channel
    chk = [e for e in _spot(s, "aladdin") if e["day"] == 2 and e["detail"].get("checks")][0]
    assert {g["reason"] for g in chk["detail"]["checks"]} == {"STOP_OPT_OUT"}
    assert s.act("stop")["refusal"]["code"] == "ALREADY_STOPPED"
    assert s.act("override", mode="wait")["ok"] is False


def test_campaign_pause_halts_dispatch_and_resume_restarts_it(journey):
    s = journey()
    s.step()
    out = s.act("campaign", paused=True)
    assert out["ok"] and out["result"]["cancelled"] > 0 and out["snapshot"]["campaign_paused"]
    s.step()                                                     # day 1 sends: nothing goes out
    assert not [e for e in s.events if e["component"] == "executor" and e["day"] == 1]
    s.step()                                                     # day 2 plan: every plan waits on CAMPAIGN_PAUSED
    blocked = [e for e in s.events if e["day"] == 2 and e["component"] == "aladdin" and e["kind"] == "blocked"]
    assert blocked and all(e.get("detail", {}).get("reason", "CAMPAIGN_PAUSED") == "CAMPAIGN_PAUSED" for e in blocked)
    assert not [x for x in _touches(s) if x["day"] >= "2026-11-24" and x["status"] in QUEUED]
    assert s.act("campaign", paused=False)["result"]["campaign_paused"] is False
    s.step(), s.step()                                           # day 2 sends (nothing reserved), day 3 plan
    s.step()
    assert [e for e in s.events if e["component"] == "executor" and e["day"] == 3]


def test_kill_switch_cancels_everything_and_blocks_at_send(journey):
    s = journey()
    s.step()
    n_queued = len([x for x in _touches(s) if x["status"] in QUEUED])
    out = s.act("outbound", paused=True)
    assert out["ok"] and out["result"]["cancelled"] == n_queued > 0
    assert "OUTBOUND_PAUSED" in _audit_types(s)
    s.step()
    assert not [e for e in s.events if e["component"] == "executor"]
    s.step()                                                     # next plan: Aladdin refuses every send
    chk = [e for e in _spot(s, "aladdin") if e["day"] == 2 and e["detail"].get("checks")][0]
    assert {g["reason"] for g in chk["detail"]["checks"]} == {"OUTBOUND_PAUSED"}
    s.act("outbound", paused=False)
    s.step(), s.step(), s.step()                                 # day 2 sends (none), day 3 plan + sends
    assert [e for e in s.events if e["component"] == "executor" and e["day"] == 3]


def test_hold_and_release(journey):
    s = journey()
    s.step()
    out = s.act("hold", on=True)
    assert out["ok"] and out["snapshot"]["card"]["holds"] == ["MANUAL_PAUSE"]
    assert all(x["status"] == "CANCELED" for x in _touches(s, VIKRAM))
    s.step(), s.step()
    chk = [e for e in _spot(s, "aladdin") if e["day"] == 2 and e["detail"].get("checks")][0]
    assert {g["reason"] for g in chk["detail"]["checks"]} - {"NO_CONSENT"} == {"SUPERVISOR_HOLD"}
    rel = s.act("hold", on=False)
    assert rel["ok"] and rel["snapshot"]["card"]["holds"] == []


def test_approve_and_reject_change_the_outcome(journey):
    """Aarav's day-4 voice-bot call waits for a person: approved → the call happens (and he pays that day);
    rejected → no call, no payment that day. Through HITL.approve / HITL.reject."""
    def at_window():
        s = journey(spotlight=AARAV)
        _run_to(s, 3)
        p = [x for x in s.state()["snapshot"]["pending_approvals"] if x["borrower_id"] == AARAV]
        assert p and p[0]["channel"] == "voicebot" and p[0]["rules"] == ["VOICE_CHANNEL"]
        return s, p[0]["approval_id"]

    a, iid = at_window()
    out = a.act("approve", approval_id=iid)
    assert out["ok"] and out["result"]["outcome"] == "APPROVED"
    assert any(e["kind"] == "rechecked" and e["detail"].get("stage") == "approval" for e in out["events"])
    a.step()
    assert [e for e in _spot(a, "voice", "call_ended") if e["day"] == 4]
    assert [e for e in _spot(a, "payments", "verified") if e["day"] == 4]

    r, iid = at_window()
    out = r.act("reject", approval_id=iid)
    assert out["ok"] and out["result"]["outcome"] == "REJECTED"
    assert r.worker.call(lambda: r.r.ledger.touch(iid))["status"] == "CANCELED"
    r.step()
    assert not [e for e in _spot(r, "voice", "call_ended") if e["day"] == 4]
    assert not [e for e in _spot(r, "payments", "verified") if e["day"] == 4]
    assert r.act("approve", approval_id=iid)["refusal"]["code"] == "NOT_IN_WINDOW"
    assert [row["what"].startswith("Rejected") for row in r.state()["snapshot"]["audit"]] == [True]


def test_deterministic_per_seed(journey):
    def run(seed):
        s = journey(seed=seed, borrowers=60, days=5)
        while s.phase != "finished":
            s.step()
        return [(e["t"], e["component"], e["kind"], e.get("borrower_id"), e["summary"]) for e in s.events], s

    a, sa = run(9)
    b, _ = run(9)
    assert a == b and len(a) > 200
    c, _ = run(10)
    assert c != a
    assert sa.state()["snapshot"]["cap_breaches"] == 0


def test_no_pii_in_any_output(journey, client):
    s = journey()
    _run_to(s, 1)
    s.act("override", mode="channel", channel="whatsapp", time="20:00")
    s.act("stop")
    blob = json.dumps(s.state(0), ensure_ascii=False)
    assert not PHONE.search(blob)
    assert not re.search(r"[\w.]+@[\w-]+\.[a-z]{2,}", blob)
    full = s.worker.call(lambda: s.r.ledger._conn().execute(
        "SELECT full_name, phone FROM borrowers WHERE borrower_id=?", (VIKRAM,)).fetchone())
    surname = full["full_name"].split()[-1]
    assert surname not in blob and full["phone"] not in blob
    # other borrowers' events reach the page as counters only: no ids, no names
    other = [e for e in s.state(0)["events"] if not e.get("spot") and e.get("component") not in ("human",)
             and "summary" not in e]
    assert other and all("borrower_id" not in e and "first_name" not in e for e in other)
    page = client.get("/demo/journey/outbound").text
    assert not PHONE.search(page)

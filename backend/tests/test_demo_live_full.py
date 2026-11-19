"""Live view, full mode (replay/live_full.py): every component emits events on seed 42, the invariants hold
(no disclosure before OTP, STOP stops every channel, only the paid EMI's touches are cancelled, duplicate
webhooks ignored, zero cap breaches), counters are deterministic, no PII, and the API (start / state /
borrower / interactive approvals / stop). Race mode is covered, unchanged, by test_demo_live.py."""
from __future__ import annotations

import importlib
import json
import re
import sqlite3
import time
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from replay.live import LiveManager
from replay.live_full import COMPONENTS, FullCampaign

PHONE = re.compile(r"(?<![\w-])\d{10}(?!\w)|\+91")   # a bare 10-digit number; hex ids like INS-d904… are not phones


def _run(seed=42, n=300, days=14, workdir=None) -> FullCampaign:
    c = FullCampaign(seed=seed, borrowers=n, days=days, day_seconds=0, workdir=workdir).start()
    c.join(300)
    assert c.status == "finished", c.error
    return c


@pytest.fixture(scope="module")
def run42(tmp_path_factory):
    wd = tmp_path_factory.mktemp("full42")
    c = _run(workdir=str(wd))
    return c, c.state(0, 10_000) | {"events": list(c.events)}, wd / "full.db"


def _by_comp(st):
    out = defaultdict(list)
    for e in st["events"]:
        out[e["component"]].append(e)
    return out


def test_every_component_emits_on_seed_42(run42):
    _, st, _ = run42
    comps = _by_comp(st)
    assert set(COMPONENTS) <= set(comps), set(COMPONENTS) - set(comps)
    kinds = {(e["component"], e["kind"]) for e in st["events"]}
    for want in [("spine", "portfolio_loaded"), ("spine", "budget_scope"), ("churn", "assigned"), ("churn", "moved"),
                 ("learner", "contact"), ("learner", "wait"), ("learner", "posterior"), ("learner", "switch"), ("aladdin", "allowed"),
                 ("aladdin", "blocked"), ("aladdin", "rechecked"), ("hitl", "approval_pending"), ("hitl", "approved"),
                 ("hitl", "rejected"), ("executor", "dispatched"), ("channel", "delivered"), ("channel", "read"),
                 ("borrower", "replied"), ("intent", "classified"), ("voice", "bot_selected"), ("voice", "call_ended"),
                 ("voice", "duplicate_ignored"), ("cm03", "message"), ("cm03", "otp_verified"),
                 ("cm03", "fraud_suspect"), ("payments", "verified"), ("outcomes", "finalized"),
                 ("reports", "daily_summary"), ("reports", "day_end")]:
        assert want in kinds, want
    assert st["mode"] == "full" and st["provenance"] == "SIMULATED"


def test_event_schema(run42):
    _, st, _ = run42
    ids = [e["id"] for e in st["events"]]
    assert ids == list(range(1, len(ids) + 1))
    for e in st["events"]:
        assert {"id", "t", "day", "component", "kind", "borrower_id", "first_name", "summary", "detail", "links",
                "provenance"} <= set(e)
        assert e["component"] in COMPONENTS and e["provenance"] == "SIMULATED"
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", e["t"]) and 1 <= e["day"] <= 14
        assert e["summary"] and "None" not in e["summary"]
        for link in e["links"]:
            assert link["from_component"] in COMPONENTS and link["to_component"] in COMPONENTS


def test_no_disclosure_before_otp(run42):
    _, st, _ = run42
    cm = [e for e in st["events"] if e["component"] == "cm03"]
    assert st["components"]["cm03"]["disclosures_before_otp"] == 0
    verified_at = {}
    for e in cm:
        if e["kind"] == "message":
            assert not e["detail"]["discloses_account_data"]
        if e["kind"] == "otp_verified":
            verified_at.setdefault(e["borrower_id"], e["id"])
        if e["kind"] == "balance_shared":
            assert e["borrower_id"] in verified_at and verified_at[e["borrower_id"]] < e["id"]
    assert verified_at and st["components"]["cm03"]["fakes_caught"] >= 1


def test_stop_stops_every_channel(run42):
    _, st, db = run42
    stops = {e["borrower_id"]: e["t"] for e in st["events"] if e["component"] == "borrower" and e["kind"] == "stop"}
    assert stops, "seed 42 should contain at least one STOP"
    for e in st["events"]:
        if e["component"] == "executor" and e["borrower_id"] in stops:
            assert e["t"] <= stops[e["borrower_id"]], (e["borrower_id"], e["t"])
    con = sqlite3.connect(db)
    for bid in stops:
        rows = con.execute("SELECT channel, opted_out FROM consents WHERE borrower_id=?", (bid,)).fetchall()
        assert rows and all(r[1] == 1 for r in rows)          # every channel, not just the one STOP came on
    con.close()


def test_only_the_paid_emi_is_cancelled(run42):
    _, st, db = run42
    paid = [e for e in st["events"] if e["component"] == "payments" and e["kind"] == "verified"]
    assert paid and all(e["detail"]["cancelled_other_emis"] == 0 for e in paid)
    assert st["components"]["payments"]["touches_cancelled"] >= 1
    con = sqlite3.connect(db)
    rows = con.execute("SELECT t.obligation_id, o.amount_paid_paise, o.amount_due_paise FROM touches t JOIN obligations o "
                       "ON o.obligation_id=t.obligation_id WHERE t.reason='OBLIGATION_CLEARED'").fetchall()
    assert rows and all(r[1] >= r[2] for r in rows)
    con.close()


def test_duplicate_webhooks_ignored_and_zero_breaches(run42):
    _, st, db = run42
    c = st["components"]
    assert c["voice"]["duplicates_ignored"] >= 1 and c["channel"]["duplicates_ignored"] >= 1
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM voice_calls").fetchone()[0] == c["voice"]["calls"]
    con.close()
    assert c["aladdin"]["cap_breaches"] == 0 and st["counters"]["kairos"]["cap_breaches"] == 0
    assert all(e["detail"]["cap_breaches"] == 0 for e in st["events"] if e["kind"] == "day_end")
    assert st["final"]["report"]["cap_breaches_k01"] == 0


def test_counters_follow_the_events(run42):
    _, st, _ = run42
    c, ev = st["components"], st["events"]
    n = lambda comp, kind: sum(1 for e in ev if e["component"] == comp and e["kind"] == kind)  # noqa: E731
    assert c["executor"]["dispatched"] == n("executor", "dispatched") == st["final"]["report"]["contacts_sent"]
    assert c["executor"]["spend_paise"] == st["final"]["report"]["spend_paise"]
    assert c["payments"]["verified"] == n("payments", "verified")
    assert c["hitl"]["requested"] == n("hitl", "approval_pending")
    assert c["voice"]["calls"] == n("voice", "call_ended") and c["intent"]["classified"] == n("intent", "classified")
    assert sum(c["churn"]["cohort_sizes"].values()) == st["borrowers"]
    assert c["hitl"]["pending"] == 0


def test_deterministic_counters():
    a, b = _run(n=150, days=6), _run(n=150, days=6)
    sa, sb = a.state(0, 10_000), b.state(0, 10_000)
    assert sa["components"] == sb["components"] and sa["counters"] == sb["counters"]
    strip = lambda evs: [(e["t"], e["component"], e["kind"], e["borrower_id"], e["summary"]) for e in evs]  # noqa: E731
    assert strip(sa["events"]) == strip(sb["events"])
    other = _run(seed=7, n=150, days=6).state(0, 10_000)
    assert other["components"] != sa["components"]


def test_no_pii(run42):
    _, st, _ = run42
    blob = json.dumps(st, ensure_ascii=False)
    assert not PHONE.search(blob)
    assert not re.search(r"[\w.]+@[\w-]+\.[a-z]{2,}", blob)
    assert "Mobile" not in blob and "full_name" not in blob and "phone" not in blob.lower()
    for e in st["events"]:
        if e["first_name"]:
            assert " " not in e["first_name"]
        if e["borrower_id"]:
            assert e["borrower_id"].startswith("BRW-")


def test_borrower_trace(run42):
    c, st, _ = run42
    bid = next(e["borrower_id"] for e in st["events"] if e["component"] == "payments" and e["kind"] == "verified")
    tr = c.borrower(bid)
    comps = {e["component"] for e in tr["events"]}
    assert {"churn", "payments"} <= comps and tr["card"]["paid"] and tr["card"]["cohort"]
    assert [e["id"] for e in tr["events"]] == sorted(e["id"] for e in tr["events"])
    with pytest.raises(KeyError):
        c.borrower("BRW-NOPE")


def test_manager_start_state_stop():
    m = LiveManager()
    run = m.start(mode="full", seed=3, borrowers=40, days=3, speed=0.25)
    assert run.mode == "full"
    out = m.stop(timeout=30)
    assert out["stopped"] and out["clean"] and run.status == "stopped"
    m.start(mode="full", seed=3, borrowers=40, days=2, speed=64).join(60)
    st = m.state()
    assert st["status"] == "finished" and st["mode"] == "full" and st["components"]["executor"]["dispatched"] > 0
    with pytest.raises(ValueError):
        m.start(mode="nope")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    return TestClient(api.app), api


def _wait(c, pred, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/demo/live/state", params={"limit": 1}).json()
        if pred(st):
            return st
        time.sleep(0.05)
    raise AssertionError(f"timed out: {st['status']}")


def test_api_full_mode_and_borrower(client):
    c, api = client
    r = c.post("/demo/live/start", json={"mode": "full", "seed": 5, "borrowers": 60, "days": 3, "speed": 64})
    assert r.status_code == 200 and r.json()["mode"] == "full"
    st = _wait(c, lambda s: s["status"] == "finished")
    assert st["mode"] == "full" and set(COMPONENTS) == set(st["components"])
    ev = c.get("/demo/live/state", params={"limit": 10000}).json()["events"]
    bid = next(e["borrower_id"] for e in ev if e["borrower_id"])
    tr = c.get(f"/demo/live/borrower/{bid}")
    assert tr.status_code == 200 and tr.json()["events"]
    assert c.get("/demo/live/borrower/BRW-NOPE").status_code == 404
    pend = next((e for e in ev if e["kind"] == "approval_pending"), None)
    if pend:   # not interactive: a simulated supervisor decides
        assert c.post(f"/demo/live/approvals/{pend['detail']['approval_id']}/approve").status_code == 409
    assert c.post("/demo/live/approvals/NOPE/approve").status_code in (404, 409)
    assert c.post("/demo/live/start", json={"mode": "bogus"}).status_code == 422


def test_api_interactive_approvals(client):
    c, api = client
    r = c.post("/demo/live/start", json={"mode": "full", "seed": 42, "borrowers": 300, "days": 4, "speed": 64,
                                         "interactive_approvals": True, "approval_wait_s": 20})
    assert r.status_code == 200 and r.json()["interactive_approvals"] is True
    st = _wait(c, lambda s: s.get("awaiting_approvals") and s.get("pending_approvals"))
    pend = st["pending_approvals"]
    assert pend and all(p["approval_id"] and p["rules"] for p in pend)
    ok = c.post(f"/demo/live/approvals/{pend[0]['approval_id']}/approve", json={"reason": "looks fine"})
    assert ok.status_code == 200 and ok.json()["outcome"] in ("APPROVED", "BLOCKED_AT_RECHECK")
    again = c.post(f"/demo/live/approvals/{pend[0]['approval_id']}/approve")
    assert again.status_code == 409
    if len(pend) > 1:
        no = c.post(f"/demo/live/approvals/{pend[1]['approval_id']}/reject")
        assert no.status_code == 200 and no.json()["outcome"] == "REJECTED"
    assert c.post("/demo/live/approvals/NOPE/approve").status_code == 404
    ev = c.get("/demo/live/state", params={"limit": 10000}).json()["events"]
    mine = [e for e in ev if e["component"] == "hitl" and e["detail"].get("by") == "you"]
    assert mine and mine[0]["detail"]["approval_id"] == pend[0]["approval_id"]
    out = c.post("/demo/live/stop").json()
    assert out["stopped"]

"""Regressions for the E2E QA pass on the deployed demo (20 Nov 2026), second batch.

The frontend has no JS test runner in this repo, so the UI fixes are guarded here by the backend contracts they rely
on and by source checks that keep the UI text in line with the K01 policy.
"""
import importlib
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .test_ledger import D, add_loan

REPO = Path(__file__).resolve().parents[2]
LIVE_HTML = REPO / "backend" / "kairos_backend" / "static" / "live.html"
FRONTEND_SRC = REPO / "frontend" / "src"
K01 = REPO / "policy" / "K01-decision-policy" / "policy.json"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api_mod
    api_mod = importlib.reload(api_mod)
    lg = api_mod.ledger
    lg.upsert_template("SMS_EN", "sms", "en")
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    lg.enroll("C1", [add_loan(lg, "B1")])
    lg.start_day(D)
    yield TestClient(api_mod.app), api_mod
    lg.close()


SMS = {"borrower_id": "B1", "obligation_id": "B1-A-1", "campaign_id": "C1", "channel": "sms", "template_id": "SMS_EN",
       "at": f"{D}T15:00:00", "est_cost_paise": 20}


def test_control_room_kill_switch_contract(api):
    """Control Room "Pause all outbound" now calls POST /control/outbound with exactly this body
    (frontend/src/api/kairos.js setOutboundPaused). Pausing cancels the queue and the Gate refuses every send;
    resuming lets sends through again. It used to change only the browser's demo state."""
    c, mod = api
    assert c.post("/decide", json=SMS).json()["reserved"]
    paused = c.post("/control/outbound", json={"paused": True, "actor": "human:ops.desk", "reason": "Operator decision"})
    assert paused.status_code == 200 and paused.json() == {"paused": True, "canceled": 1}
    held = c.post("/preview", json={**SMS, "at": f"{D}T16:00:00"}).json()
    assert (held["decision"], held["reason"]) == ("WAIT", "OUTBOUND_PAUSED")
    row = c.get("/audit", params={"limit": 5}).json()[0]
    assert (row["type"], row["actor"]) == ("OUTBOUND_PAUSED", "human:ops.desk")
    resumed = c.post("/control/outbound", json={"paused": False, "actor": "human:ops.desk", "reason": "Operator decision"})
    assert resumed.json() == {"paused": False, "canceled": 0}
    assert c.post("/preview", json={**SMS, "at": f"{D}T16:00:00"}).json()["decision"] == "SEND"
    assert c.post("/control/outbound", json={"actor": "x"}).status_code == 422  # `paused` is required


def test_resume_after_dated_hold_reports_still_blocked(api):
    """Smart CRM "Resume contact" lifts a Pause contact only. After "Hold for 2 days" the backend keeps the hold and
    says so in still_blocked_by; the UI toast now shows it instead of a plain success."""
    c, _ = api
    act = lambda **b: c.post("/hitl/borrowers/B1/actions", headers={"X-Kairos-Role": "operator"},
                             json={"actor": "ops.desk", "reason": "Operator decision", "now": f"{D}T09:00:00", **b})
    assert act(action="hold", days=2).status_code == 200
    out = act(action="resume").json()
    assert out["released_pauses"] == 0 and out["still_blocked_by"] == ["SUPERVISOR_HOLD"]
    assert act(action="pause").status_code == 200
    out = act(action="resume").json()
    assert out["released_pauses"] == 1 and out["still_blocked_by"] == ["SUPERVISOR_HOLD"]  # the dated hold remains


def test_frontend_quiet_hours_match_k01_contact_window():
    """The UI said "Quiet hours (20:00–08:00)" and suggested an 18:00–20:00 WhatsApp window while K01's contact
    window ends at 19:00 (end exclusive): the journey page blocks a 20:00 send but the Control Room claimed 19:30 was
    allowed. Every quiet-hours / window string in the UI must fit inside K01."""
    cw = json.loads(K01.read_text(encoding="utf-8"))["contact_window"]
    start, end = cw["start"], cw["end_exclusive"]
    src = {p: p.read_text(encoding="utf-8") for p in FRONTEND_SRC.rglob("*.js*")}
    quiet = [(p.name, m) for p, s in src.items() for m in re.findall(r"[Qq]uiet[^'`\"]*?(\d\d:\d\d)–(\d\d:\d\d)", s)]
    quiet += [(p.name, m) for p, s in src.items() for m in re.findall(r"quiet: '(\d\d:\d\d)–(\d\d:\d\d)'", s)]
    assert quiet, "no quiet-hours strings found: update this test"
    for name, (q_from, q_to) in quiet:
        assert (q_from, q_to) == (end, start), f"{name}: quiet hours {q_from}–{q_to}, K01 contact window {start}–{end}"
    for p, s in src.items():
        for a, b in re.findall(r"window: (?:ch === '\w+' \? )?'(\d\d:\d\d)–(\d\d:\d\d)'", s):
            assert start <= a and b <= end, f"{p.name}: suggested window {a}–{b} is outside K01 {start}–{end}"


def test_live_page_axis_and_header_fixes():
    """/demo/live: before the first rupee the y-axis loop ran to `top + 1` paise with 0.5-paise steps, drawing extra
    "₹0" labels above the chart over the Recovered KPI card; and the header wrapped to two rows at 1440 px."""
    html = LIVE_HTML.read_text(encoding="utf-8")
    assert "v <= top + 1;" not in html and "v <= top + step / 2;" in html
    assert "Math.max(niceStep(max / 100, 3) * 100, spark ? 0 : 10000)" in html
    assert "@media (min-width: 1101px) { .topbar, .demo { flex-wrap: nowrap; }" in html

"""K27 auto-churn engine: DSL, cohorts, both rule engines, journeys, layers, lifecycle, idempotency, no rewind,
precedence, cooldown, Sarvam mapping, determinism, API. Everything SIMULATED (synthetic ids, no PII)."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta

import pytest

from kairos_backend.churn import ChurnEngine, CohortSet, Journeys, static_profile
from kairos_backend.churn import fsm, sarvam_export
from kairos_backend.churn.cohorts import UNASSIGNED, load_json
from kairos_backend.churn.dsl import evaluate, validate
from kairos_backend.churn.state import BorrowerState, ChurnEvent
from replay import cn10

D0 = date(2026, 11, 23)  # a Monday


def prof(tag="C3 - Medium Risk", dpd=1, bounce="0,1,0,1,0,1", pos=100_000, state="Karnataka", cut=None, lang="en"):
    return static_profile(dpd=dpd, as_of=D0, pos_paise=pos, risk_tag=tag, bounce_string=bounce, state=state,
                          language=lang, high_ticket_pos_paise=cut)


@pytest.fixture
def eng():
    e = ChurnEngine(":memory:")
    yield e
    e.close()


def at(day=0, h=10, m=0):
    return datetime.combine(D0 + timedelta(days=day), datetime.min.time()).replace(hour=h, minute=m).isoformat()


def ev(eid, kind, day=0, h=10, m=0, bid="B1", **kw):
    return {"event_id": eid, "borrower_id": bid, "kind": kind, "occurred_at": at(day, h, m), **kw}


def tr(res, engine=None):
    return [(t["engine"], t["from"], t["to"], t["rule_id"]) for t in res["transitions"]
            if engine is None or t["engine"] == engine]


# ---------------- DSL ----------------
@pytest.mark.parametrize("cond,expect", [
    ({"field": "dpd", "op": "=", "value": 5}, True), ({"field": "dpd", "op": "≠", "value": 5}, False),
    ({"field": "dpd", "op": ">", "value": 4}, True), ({"field": "dpd", "op": "≥", "value": 5}, True),
    ({"field": "dpd", "op": "<", "value": 5}, False), ({"field": "dpd", "op": "≤", "value": 5}, True),
    ({"field": "state", "op": "contains", "value": "karna"}, True),
    ({"field": "state", "op": "not_contains", "value": "kerala"}, True),
    ({"field": "dpd", "op": "between", "value": [1, 5]}, True), ({"field": "dpd", "op": "between", "value": [6, 9]}, False),
    ({"field": "band", "op": "in", "value": ["B1", "B2"]}, True), ({"field": "band", "op": "not_in", "value": ["B1"]}, False),
    ({"field": "ptp.date", "op": "exists", "value": False}, True),
    ({"field": "sms.status", "op": "<=", "value": "Delivered"}, True),
    ({"field": "whatsapp.status", "op": ">=", "value": "Read"}, True),
    ({"field": "whatsapp.status", "op": ">", "value": "Read"}, False),
])
def test_dsl_operators(cond, expect):
    feats = {"dpd": 5, "state": "Karnataka", "band": "B1", "ptp.date": None, "sms.status": "Delivered",
             "whatsapp.status": "Read"}
    validate(cond)
    assert evaluate(cond, feats)[0] is expect


def test_dsl_and_or_chain_left_to_right_and_grouping():
    f = {"a": 1, "b": 2, "c": 3}
    # PRD left-to-right, no grouping: ((a=9 OR b=2) AND c=3) -> True ; ((a=1 OR b=9) AND c=9) -> False
    assert evaluate({"chain": [{"field": "a", "op": "=", "value": 9}, {"join": "OR", "field": "b", "op": "=", "value": 2},
                               {"join": "AND", "field": "c", "op": "=", "value": 3}]}, f)[0]
    assert not evaluate({"chain": [{"field": "a", "op": "=", "value": 1}, {"join": "OR", "field": "b", "op": "=", "value": 9},
                                   {"join": "AND", "field": "c", "op": "=", "value": 9}]}, f)[0]
    assert evaluate({"any": [{"all": [{"field": "a", "op": "=", "value": 1}, {"field": "b", "op": "=", "value": 9}]},
                             {"not": {"field": "c", "op": "=", "value": 9}}]}, f)[0]


@pytest.mark.parametrize("bad", [{"field": "x", "op": "~", "value": 1}, {"field": "x"},
                                 {"field": "x", "op": "between", "value": 3}, {"field": "sms.status", "op": "=", "value": "Seen"},
                                 {"chain": [{"field": "a", "op": "=", "value": 1}, {"join": "XOR", "field": "b", "op": "=", "value": 1}]}])
def test_dsl_rejects_malformed(bad):
    with pytest.raises(ValueError):
        validate(bad)


# ---------------- cohorts: priority, coverage, CN10 reproduction ----------------
def test_priority_first_match_wins_and_unassigned():
    cs = CohortSet({"cohorts": [
        {"id": "A", "condition": {"field": "dpd", "op": ">=", "value": 10}},
        {"id": "B", "condition": {"field": "dpd", "op": ">=", "value": 5}}]})
    assert cs.assign({"dpd": 12})[0] == "A"
    assert cs.assign({"dpd": 7})[0] == "B"
    cid, expl = cs.assign({"dpd": 1})
    assert cid == UNASSIGNED and expl["skipped"] == ["A", "B"]
    cov = cs.coverage([("x", {"dpd": 12}, None, None), ("y", {"dpd": 6}, None, None), ("z", {"dpd": 1}, None, None)])
    assert cov["by_cohort"] == {"A": 1, "B": 1, UNASSIGNED: 1}
    assert cov["orphans"] == 1 and cov["orphan_ids"] == ["z"] and cov["overlapping_borrowers"] == 1
    snap = CohortSet(cs.snapshot_orphans("ORPH1", "Orphans 23 Nov", ["z"], "2026-11-23T10:00:00"))
    assert snap.assign({"dpd": 1}, borrower_id="z")[0] == "ORPH1"
    assert snap.assign({"dpd": 1}, borrower_id="new")[0] == UNASSIGNED  # frozen: does not recompute


def test_cohort_validation_rejects_unknown_field_and_duplicate():
    with pytest.raises(ValueError):
        CohortSet({"cohorts": [{"id": "A", "condition": {"field": "nope", "op": "=", "value": 1}}]})
    with pytest.raises(ValueError):
        CohortSet({"cohorts": [{"id": "A", "condition": True}, {"id": "A", "condition": True}]})


def test_scope_by_campaign_and_region():
    cs = CohortSet({"cohorts": [{"id": "KA", "condition": True, "scope": {"regions": ["Karnataka"]}},
                                {"id": "CMP", "condition": True, "scope": {"campaigns": ["X"]}}]})
    assert cs.assign({}, region="Karnataka")[0] == "KA"
    assert cs.assign({}, region="Kerala", campaign="X")[0] == "CMP"
    assert cs.assign({}, region="Kerala", campaign="Y")[0] == UNASSIGNED


def test_day0_reproduces_cn10_cohorts(eng):
    import random
    rng = random.Random(3)
    tags = ["C1 - First", "C2 - Low", "C3 - Medium Risk", "C4 - High", "C5 - Ticket", ""]
    rows = []
    for i in range(60):
        bs = ",".join(rng.choice("01") for _ in range(6))
        rows.append((f"B{i}", rng.choice(tags), bs, rng.randint(10_000, 900_000)))
    cut = cn10.high_ticket_threshold([r[3] for r in rows])
    for bid, tag, bs, pos in rows:
        eng.enroll(bid, prof(tag=tag, bounce=bs, pos=pos, cut=cut), "CMP", D0)
        assert eng.state(bid).cohort == cn10.cohort_of(tag, bs, pos, cut), bid


def test_cn10_journeys_match_calendar():
    j = Journeys.load()
    for c, cal in cn10.CALENDAR.items():
        days = {d["day"]: [(s["channels"][0], s["tier"]) for s in d["steps"]] for d in j.journeys[f"CN10_{c}"]["days"]}
        assert days == cal


# ---------------- fold: no rewind, SMS read, STOP not a reply ----------------
def test_older_event_never_rewinds(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    eng.process(ev("p1", "ptp", 0, 12, state="OPEN", date="2026-11-26"))
    r = eng.process(ev("p0", "ptp", 0, 9, state="BROKEN"))      # older, arrives late
    assert r["late"] and "ptp_status" in r["changed"]["_ignored_older"]
    assert eng.state("B1").flags["ptp_status"] == "OPEN"
    eng.process(ev("d2", "delivery", 0, 11, channel="whatsapp", touch_id="T1", status="READ"))
    eng.process(ev("d1", "delivery", 0, 10, channel="whatsapp", touch_id="T1", status="DELIVERED"))
    assert eng.state("B1").features(D0)["whatsapp.status"] == "Read"


def test_sms_read_never_tracked_and_stop_is_not_a_reply(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    r = eng.process(ev("s1", "delivery", channel="sms", touch_id="T1", status="READ"))
    assert r["changed"]["_ignored"] == "SMS_READ_NOT_TRACKED"
    eng.process(ev("s2", "reply", channel="sms", touch_id="T2", text="STOP"))
    st = eng.state("B1")
    assert st.flags["stop"] and st.campaign_replies == 0 and st.features(D0)["sms.replied_7d"] == 0


# ---------------- idempotency + determinism ----------------
def test_idempotent_per_event_id(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    a = eng.process(ev("r1", "reply", channel="whatsapp", touch_id="T1"))
    b = eng.process(ev("r1", "reply", channel="whatsapp", touch_id="T1"))
    assert a["transitions"] and b["duplicate"] and not b["transitions"]
    assert eng.state("B1").campaign_replies == 1
    assert eng.tick(D0 + timedelta(days=1))["day"] == eng.tick(D0 + timedelta(days=1))["day"]
    assert eng.tick(D0 + timedelta(days=1)).get("duplicate")
    assert eng.tick(D0)["refused"] == "OLDER_THAN_LAST_TICK"


def _script(e):
    e.enroll("B1", prof(), "CMP", D0)
    e.enroll("B2", prof(tag="C4 - High"), "CMP", D0)
    e.process(ev("a", "reply", channel="whatsapp", touch_id="T1"))
    e.process(ev("b", "intent", 0, 11, intent="PTP", ptp_date="2026-11-25"))
    e.process(ev("c", "call", 0, 12, bid="B2", channel="voicebot", status="NO_ANSWER", slot="08-11"))
    e.process(ev("d", "call", 1, 12, bid="B2", channel="voicebot", status="NO_ANSWER", slot="08-11"))
    for d in range(1, 6):
        e.tick(D0 + timedelta(days=d))
    return [(t["borrower_id"], t["engine"], t["from_value"], t["to_value"], t["rule_id"], t["evidence"])
            for t in e.db.execute("SELECT * FROM churn_transitions ORDER BY id")]


def test_deterministic_same_inputs_same_transitions():
    assert _script(ChurnEngine(":memory:")) == _script(ChurnEngine(":memory:"))


# ---------------- precedence: STOP / hardship / K13 always win ----------------
def test_stop_wins_over_override_and_pin(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    eng.process(ev("r", "reply", channel="whatsapp", touch_id="T1"))           # pinned WA_RESPONSIVE
    o = eng.request_override("B1", "C4", "ops:maker", "test", at=datetime.fromisoformat(at(0, 11)))
    eng.approve_override(o["override_id"], "ops:checker", at=datetime.fromisoformat(at(0, 11, 5)))
    assert eng.state("B1").cohort == "C4"
    r = eng.process(ev("s", "stop", 0, 12, channel="sms"))
    st = eng.state("B1")
    assert st.cohort == "OPTED_OUT" and st.lifecycle == "STOPPED_OPT_OUT"
    assert ("cohort", "C4", "OPTED_OUT", "PREC.STOP") in tr(r)
    assert eng.allowed_channels("B1", D0)["channels"] == []


def test_hardship_wins_goes_human_and_freezes_risk(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    r = eng.process(ev("h", "intent", intent="HARDSHIP"))
    st = eng.state("B1")
    assert (st.cohort, st.lifecycle, st.layer, st.risk_frozen) == ("HARDSHIP", "HARDSHIP_HUMAN", "L4", True)
    assert ("risk_freeze", False, True, "RISK.HARDSHIP_FREEZE") in tr(r)
    r = eng.process(ev("bp", "ptp", 0, 11, state="BROKEN"))
    assert not tr(r, "risk") and st.risk_tier == "MEDIUM"                       # frozen: no escalation
    a = eng.allowed_channels("B1", D0)
    assert a["channels"] == [] and a["reason"] == "LIFECYCLE_HARDSHIP_HUMAN"
    for d in range(1, 4):
        eng.tick(D0 + timedelta(days=d))
    assert st.lifecycle == "ACTIVE" and not st.risk_frozen and st.layer != "L4"   # 2-day hold over


def test_k13_hold_pauses_and_release(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    eng.process(ev("cb", "hold", hold="CALLBACK_PENDING"))
    st = eng.state("B1")
    assert (st.cohort, st.lifecycle, st.layer) == ("ON_HOLD", "PAUSED_HOLD", "L4")
    eng.process(ev("cb2", "hold", 0, 12, hold="CALLBACK_PENDING", active=False))
    assert st.lifecycle == "ACTIVE" and st.cohort == "C3" and st.layer == "L3"


# ---------------- risk rules + cooldown / hysteresis ----------------
def test_risk_rules(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    st = eng.state("B1")
    r = eng.process(ev("p1", "ptp", state="BROKEN"))
    assert ("risk", "MEDIUM", "HIGH", "RISK.BROKEN_PTP_UP") in tr(r) and st.cohort == "BROKEN_PTP"
    assert "p1" in r["transitions"][0]["evidence"]
    r = eng.process(ev("p2", "ptp", 0, 11, state="BROKEN", ptp_id="second"))
    assert ("risk", "HIGH", "CRITICAL", "RISK.REPEAT_BROKEN_PTP_CRITICAL") in tr(r)
    r = eng.process(ev("pay", "payment", 0, 12, state="VERIFIED", cleared=False))
    assert ("risk", "CRITICAL", "HIGH", "RISK.PAYMENT_VERIFIED_DOWN") in tr(r)
    r = eng.process(ev("rev", "payment", 0, 13, state="REVERSED"))
    assert ("risk", "HIGH", "CRITICAL", "RISK.PAYMENT_REVERSED_UP") in tr(r)


def test_refusal_and_no_contact_and_dpd_floor(eng):
    eng.enroll("B1", prof(tag="C2 - Low"), "CMP", D0)
    st = eng.state("B1")
    assert st.risk_tier == "LOW"
    r = eng.process(ev("v", "call", channel="voicebot", status="COMPLETED", duration_s=40, disposition="REFUSED_TO_PAY",
                       confidence=0.9))
    assert ("risk", "LOW", "MEDIUM", "RISK.REFUSED_TO_PAY_UP") in tr(r)
    for i, ch in enumerate(("sms", "sms", "email", "email")):
        eng.process(ev(f"f{i}", "delivery", 3, 10 + i, channel=ch, touch_id=f"T{i}", status="FAILED"))
    eng.tick(D0 + timedelta(days=3))
    assert st.risk_tier == "HIGH"                               # NO_CONTACT_MULTICHANNEL_UP (cooldown passed)
    e2 = ChurnEngine(":memory:")
    e2.enroll("B9", prof(tag="C2 - Low", dpd=70), "CMP", D0)
    e2.tick(D0 + timedelta(days=1))
    assert e2.state("B9").risk_tier == "HIGH"                   # DPD_B3_FLOOR


def test_cooldown_and_hysteresis_stop_flapping(eng):
    eng.enroll("B1", prof(tag="C2 - Low"), "CMP", D0)
    st = eng.state("B1")
    for i, ch in enumerate(("sms", "sms", "email", "email")):
        eng.process(ev(f"f{i}", "delivery", 0, 9 + i, channel=ch, touch_id=f"T{i}", status="FAILED"))
    eng.tick(D0 + timedelta(days=1))
    assert st.risk_tier == "MEDIUM"
    eng.tick(D0 + timedelta(days=2))                            # 24 h later: cooldown blocks a second step up
    assert st.risk_tier == "MEDIUM"
    for i in range(3):                                          # sustained engagement would move it down...
        eng.process(ev(f"r{i}", "reply", 2, 10 + i, channel="sms", touch_id=f"R{i}"))
    res = eng.process(ev("x", "delivery", 2, 14, channel="sms", touch_id="X", status="DELIVERED"))
    eng.tick(D0 + timedelta(days=4))                            # ...but it reverses the last move < 120 h later
    assert st.risk_tier == "MEDIUM"
    eng.tick(D0 + timedelta(days=6))                            # > 120 h after the up-move: allowed
    assert st.risk_tier == "LOW"
    assert res is not None


# ---------------- cohort reassignment rules ----------------
def test_reassign_rules_pin_and_restart_vs_continue(eng):
    eng.enroll("B1", prof(dpd=3), "CMP", D0)
    st = eng.state("B1")
    assert st.journey_id == "CN10_C3" and eng.allowed_channels("B1", D0)["day"] == 3
    r = eng.process(ev("r", "reply", channel="whatsapp", touch_id="T1"))
    assert ("cohort", "C3", "WA_RESPONSIVE", "RA.WA_REPLY_PIN") in tr(r)
    assert st.pin["cohort"] == "WA_RESPONSIVE" and st.journey_entered == D0.isoformat()
    assert eng.allowed_channels("B1", D0)["day"] == 1            # behaviour journey RESTARTS at day 1
    eng.process(ev("bp", "ptp", 0, 11, state="BROKEN"))           # higher priority than the pin floor
    assert st.cohort == "BROKEN_PTP"
    e2 = ChurnEngine(":memory:")
    e2.enroll("B2", prof(dpd=3), "CMP", D0)
    e2.process(ev("v", "payment", 0, bid="B2", state="REVERSED"))     # risk up, no reply
    s2 = e2.state("B2")
    assert s2.cohort == "C4" and s2.journey_id == "CN10_C4"
    assert e2.allowed_channels("B2", D0)["day"] == 3              # CN10 risk move CONTINUES at the DPD cycle day


def test_needs_time_and_claim_and_paid_exit(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    st = eng.state("B1")
    r = eng.process(ev("n", "intent", intent="NEEDS_TIME"))
    assert ("cohort", "C3", "ENGAGED", "RA.CM03_NEEDS_TIME_PIN") in tr(r)
    eng.process(ev("c", "intent", 0, 11, intent="CLAIMS_PAID"))
    assert (st.cohort, st.lifecycle) == ("CLAIMED_PAID", "PAUSED_CLAIM")
    assert eng.allowed_channels("B1", D0)["reason"] == "LIFECYCLE_PAUSED_CLAIM"
    eng.tick(D0 + timedelta(days=3))                              # 48 h without verification -> follow-up
    assert st.lifecycle == "ACTIVE" and st.cohort == "CLAIMED_PAID"
    eng.process(ev("v", "payment", 3, 12, state="VERIFIED", cleared=True))
    assert (st.cohort, st.lifecycle) == ("PAID_EXITED", "PAID_EXIT")
    r = eng.process(ev("rv", "payment", 3, 13, state="REVERSED"))
    assert st.lifecycle == "ACTIVE" and ("lifecycle", "PAID_EXIT", "ACTIVE", "STOP.NONE") in tr(r)


def test_ptp_pause_until_date_plus_one_then_broken(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    st = eng.state("B1")
    eng.process(ev("p", "intent", intent="PTP", ptp_date="2026-11-25"))
    assert st.lifecycle == "PAUSED_PTP" and st.lifecycle_until == "2026-11-26"
    eng.tick(date(2026, 11, 25))
    assert st.lifecycle == "PAUSED_PTP"
    eng.tick(date(2026, 11, 26))
    assert st.flags["ptp_status"] == "BROKEN" and st.cohort == "BROKEN_PTP" and st.lifecycle == "ACTIVE"


def test_ptp_kept_on_verified_payment(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    eng.process(ev("p", "intent", intent="PTP", ptp_date="2026-11-25"))
    eng.process(ev("v", "payment", 1, 10, state="VERIFIED", cleared=True))
    st = eng.state("B1")
    assert st.flags["ptp_status"] == "KEPT" and st.lifecycle == "PAID_EXIT"


# ---------------- lifecycle FSM + exhausted + stop_policy hook ----------------
def test_fsm_table_guards_terminal_exits():
    assert fsm.allowed("STOPPED_OPT_OUT", "ACTIVE", "opt_in")
    assert not fsm.allowed("STOPPED_OPT_OUT", "ACTIVE", "tick")
    assert not fsm.allowed("EXHAUSTED_CAMPAIGN", "ACTIVE", "tick")
    assert fsm.allowed("EXHAUSTED_CAMPAIGN", "ACTIVE", "new_campaign")
    assert set(fsm.STATES) >= {"PAUSED_PTP", "PAUSED_CLAIM", "EXHAUSTED_CAMPAIGN", "STOPPED_OPT_OUT",
                               "HARDSHIP_HUMAN", "PAID_EXIT"}


def test_exhausted_campaign_and_stop_policy_hook():
    e = ChurnEngine(":memory:", stop_policy=lambda feats, st: {"budget": 3, "source": "k09-rl-test"})
    e.enroll("B1", prof(), "CMP", D0)
    st = e.state("B1")
    for i in range(3):
        e.process(ev(f"a{i}", "attempt", i, 10, channel="sms", touch_id=f"T{i}", cost_paise=25))
    assert st.lifecycle == "EXHAUSTED_CAMPAIGN"
    assert "no_response_budget=3 (stop_policy:k09-rl-test)" in e.trace("B1")["transitions"][-1]["detail"]["why"]
    e.tick(D0 + timedelta(days=4))
    assert st.lifecycle == "EXHAUSTED_CAMPAIGN"                 # sticky for the campaign
    e.start_campaign("CMP-2", ["B1"], D0 + timedelta(days=5))
    assert st.lifecycle == "ACTIVE" and st.campaign_attempts == 0


def test_opt_in_reenters(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    eng.process(ev("s", "stop", channel="whatsapp"))
    eng.process(ev("o", "opt_in", 1, channel="sms", consent_version="v2"))
    st = eng.state("B1")
    assert st.lifecycle == "ACTIVE" and st.cohort == "C3"


# ---------------- journeys + layers ----------------
def test_journey_step_selection_and_layer_gating(eng):
    eng.enroll("B1", prof(tag="C4 - High", dpd=2), "CMP", D0)
    a = eng.allowed_channels("B1", D0)
    assert a["day"] == 2 and a["steps"][0]["channels"] == ["ivr"] and a["channels"][0] == "ivr"
    assert set(a["channels"]) == {"ivr", "whatsapp", "sms"}
    st = eng.state("B1")
    st.layer = "L2"                                             # ceiling at L2: IVR step substituted
    a = eng.allowed_channels("B1", D0)
    assert "ivr" not in a["channels"] and a["substituted"] and a["channels"]
    a = eng.allowed_channels("B1", D0, consented={"sms"})
    assert a["channels"] == ["sms"]
    e2 = ChurnEngine(":memory:")
    e2.enroll("B2", prof(tag="C2 - Low", dpd=2), "CMP", D0)
    assert e2.allowed_channels("B2", D0)["reason"] == "NO_STEP_TODAY"   # C2 calendar: day 1, 4, 7, 10


def test_layer_rules(eng):
    eng.enroll("B1", prof(tag="C4 - High"), "CMP", D0)
    st = eng.state("B1")
    assert st.layer == "L3"
    eng.process(ev("c1", "call", 0, 9, channel="voicebot", status="NO_ANSWER", slot="08-11"))
    r = eng.process(ev("c2", "call", 1, 9, channel="voicebot", status="NO_ANSWER", slot="08-11"))
    assert ("layer", "L3", "L2", "LAYER.L3_NO_ANSWER_X2_TO_L2") in tr(r)
    assert st.layer_avoid_slots == ["08-11"]
    eng.process(ev("w1", "delivery", 2, 10, channel="whatsapp", touch_id="W1", status="FAILED"))
    r = eng.process(ev("w2", "delivery", 3, 10, channel="whatsapp", touch_id="W2", status="FAILED"))
    assert ("layer", "L2", "L1", "LAYER.L2_NOT_ON_WHATSAPP_TO_L1") in tr(r)
    for i in range(3):
        r = eng.process(ev(f"s{i}", "delivery", 5 + i, 10, channel="sms", touch_id=f"S{i}", status="DELIVERED"))
    assert ("layer", "L1", "L2", "LAYER.L1_DELIVERED_NO_REPLY_X3_TO_L2") in tr(r)


def test_layer_read_no_reply_escalates_and_reply_homes_down(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    st = eng.state("B1")
    st.layer = "L1"
    eng.process(ev("e1", "delivery", 0, 9, channel="email", touch_id="E1", status="READ"))
    r = eng.process(ev("e2", "delivery", 1, 9, channel="email", touch_id="E2", status="READ"))
    assert ("layer", "L1", "L2", "LAYER.L1_READ_NO_REPLY_X2_TO_L2") in tr(r)
    eng.process(ev("w1", "delivery", 2, 9, channel="whatsapp", touch_id="W1", status="READ"))
    r = eng.process(ev("w2", "delivery", 3, 9, channel="whatsapp", touch_id="W2", status="READ"))
    assert ("layer", "L2", "L3", "LAYER.L2_READ_NO_REPLY_X2_TO_L3") in tr(r)
    e2 = ChurnEngine(":memory:")
    e2.enroll("B2", prof(), "CMP", D0)
    e2.process(ev("r0", "reply", 0, 9, bid="B2", channel="sms", touch_id="S0"))   # -> ENGAGED (L1..L3)
    s2 = e2.state("B2")
    s2.layer, s2.layer_since = "L3", at(0, 9)
    r = e2.process(ev("r1", "reply", 3, 9, bid="B2", channel="sms", touch_id="S1"))
    assert s2.cohort == "ENGAGED" and ("layer", "L3", "L1", "LAYER.REPLY_HOMES_DOWN") in tr(r)


def test_recommend_layer_accept_reject(eng):
    eng.enroll("B1", prof(tag="C4 - High"), "CMP", D0)
    t = datetime.fromisoformat(at(2))
    r = eng.recommend_layer("B1", {"layer": "L2"}, {"n_obs": 5}, at=t)
    assert r["accepted"] and r["rule_id"] == "REC.ACCEPT_DOWN"
    r = eng.recommend_layer("B1", {"layer": "L3"}, {"n_obs": 50, "expected_lift": 1.5}, at=t)
    assert not r["accepted"] and r["rule_id"] == "REC.COOLDOWN"
    t2 = t + timedelta(days=2)
    assert eng.recommend_layer("B1", {"layer": "L3"}, {"n_obs": 5, "expected_lift": 2}, at=t2)["rule_id"] == \
        "REC.UP_INSUFFICIENT_EVIDENCE"
    assert eng.recommend_layer("B1", {"layer": "L4"}, {}, at=t2)["rule_id"] == "REC.L4_HUMAN_ONLY"
    assert eng.recommend_layer("B1", {"layer": "L3"}, {"n_obs": 50, "expected_lift": 1.5}, at=t2)["accepted"]
    e2 = ChurnEngine(":memory:")
    e2.enroll("B2", prof(tag="C1 - First", bounce="0,0,0,0,0,1"), "CMP", D0)   # C1 journey has no L3
    assert e2.recommend_layer("B2", {"layer": "L3"}, {"n_obs": 99, "expected_lift": 3}, at=t)["rule_id"] == \
        "REC.OUT_OF_JOURNEY"


# ---------------- overrides, manual schedule, scope, analytics, trace ----------------
def test_maker_checker_override_and_manual(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    o = eng.request_override("B1", "DIGITAL_FIRST" if False else "WA_RESPONSIVE", "ops:a", "customer prefers WA")
    assert o["status"] == "PENDING" and eng.state("B1").cohort == "C3"
    with pytest.raises(PermissionError):
        eng.approve_override(o["override_id"], "ops:a")
    with pytest.raises(ValueError):
        eng.request_override("B1", "C4", "ops:a", "")
    r = eng.approve_override(o["override_id"], "ops:b")
    assert r["status"] == "APPROVED" and eng.state("B1").cohort == "WA_RESPONSIVE"
    m = eng.schedule_manual("B1", "sms", datetime.fromisoformat(at(1, 11)), "ops:a", "ad-hoc")
    a = eng.allowed_channels("B1", D0 + timedelta(days=1))
    assert a["channels"] == ["sms"] and a["reason"] == "MANUAL_SCHEDULE_REPLACES_JOURNEY_STEP"
    assert eng.cancel_manual(m["manual_id"], "ops:a", "changed mind")["canceled"]
    engines = [h["engine"] for h in eng.history("B1")]
    assert "cohort" in engines and "manual_schedule" in engines


def test_scoped_rules_only_fire_in_scope():
    doc = load_json("reassign_rules.json")
    doc = copy.deepcopy(doc)
    doc["rules"].insert(0, {"id": "RA.KA_ONLY", "on": ["reply"], "when": True, "assign": "DIGITAL_FIRST" if False else "ENGAGED",
                            "scope": {"regions": ["Karnataka"], "campaigns": ["CMP"]}})
    from kairos_backend.churn import ReassignEngine
    e = ChurnEngine(":memory:", reassign=ReassignEngine(doc))
    e.enroll("K", prof(state="Karnataka"), "CMP", D0)
    e.enroll("M", prof(state="Maharashtra"), "CMP", D0)
    e.process(ev("1", "reply", bid="K", channel="sms", touch_id="1"))
    e.process(ev("2", "reply", bid="M", channel="sms", touch_id="2"))
    assert e.state("K").cohort_rule == "RA.KA_ONLY" and e.state("M").cohort_rule != "RA.KA_ONLY"


def test_coverage_analytics_trace(eng):
    eng.enroll("B1", prof(state="Karnataka"), "CMP", D0)
    eng.enroll("B2", prof(tag="C4 - High", state="Kerala"), "CMP", D0)
    eng.process(ev("r", "reply", channel="whatsapp", touch_id="T1"))
    eng.tick(D0 + timedelta(days=1))
    cov = eng.coverage(D0 + timedelta(days=1))
    assert cov["total"] == 2 and cov["orphans"] == 0
    an = eng.analytics()
    assert an["cohort_sizes_by_day"]["2026-11-24"] and "C3 -> WA_RESPONSIVE" in an["cohort_flows"]
    assert "Karnataka" in an["cohort_flows_by_region"] and an["layer_flows"]
    assert eng.analytics(region="Kerala")["cohort_flows"] == {"- -> C4": 1}
    t = eng.trace("B1")
    assert t["steps"][0]["event_id"] == "r" and t["steps"][0]["decisions"][1]["step"] == "cohort"
    assert t["now"]["cohort"] == "WA_RESPONSIVE" and t["now"]["today"]["journey"] == "WA_FIRST"


def test_put_cohorts_validated_and_audited(eng):
    doc = copy.deepcopy(eng.cohorts.doc)
    bad = copy.deepcopy(doc)
    bad["cohorts"][0]["condition"] = {"field": "nope", "op": "=", "value": 1}
    with pytest.raises(ValueError):
        eng.put_cohorts(bad, "ops", "typo")
    doc["policy_version"] = "0.1.1-draft"
    assert eng.put_cohorts(doc, "ops", "reorder")["version"] == "0.1.1-draft"


# ---------------- Sarvam mapping ----------------
def test_sarvam_webhook_mapping_live_workflow_shape(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    payload = {"v": 2, "event": "call_ended", "status": "completed", "duration": "42",
               "attempt_id": "att-1", "interaction_id": "int-1", "end_datetime": "2026-11-23T06:00:00Z",
               "failure_reason": "", "recording_url": "",
               "final_agent_variables": json.dumps({"touch_id": "K27-B1-2026-11-23", "disposition": "PTP",
                                                    "disposition_confidence": "0.91", "ptp_date": "2026-11-25"})}
    evs = sarvam_export.webhook_to_events(payload, lambda w: w["touch_id"][4:].rsplit("-", 3)[0])
    assert len(evs) == 1 and evs[0].event_id == "sarvam:att-1" and evs[0].status == "COMPLETED"
    assert evs[0].occurred_at == datetime(2026, 11, 23, 11, 30) and evs[0].attrs["confidence"] == 0.91
    eng.process(evs[0])
    st = eng.state("B1")
    assert st.flags["ptp_status"] == "OPEN" and st.lifecycle == "PAUSED_PTP"
    assert eng.process(evs[0])["duplicate"]
    assert sarvam_export.webhook_to_events({**payload, "event": "before_call"}, lambda w: "B1") == []
    na = sarvam_export.webhook_to_events({"attempt_id": "att-2", "status": "no-answer", "end_datetime": at(0, 12)},
                                         lambda w: "B1")
    assert na[0].status == "NO_ANSWER"


def test_sarvam_low_confidence_stop_is_not_applied(eng):
    eng.enroll("B1", prof(), "CMP", D0)
    eng.process(ev("v", "call", channel="voicebot", status="COMPLETED", duration_s=30, disposition="STOP_REQUESTED",
                   confidence=0.3))
    assert not eng.state("B1").flags.get("stop")


def test_sarvam_export_payloads_dry_run_no_pii(eng):
    eng.enroll("B1", prof(tag="C4 - High", dpd=1), "CMP", D0)
    st = eng.state("B1")
    eng.process(ev("p", "ptp", state="BROKEN"))                 # BROKEN_PTP day 1 = voicebot PTP follow-up
    up = sarvam_export.cohort_upload(eng, D0)
    assert up["dry_run"] and up["rows"] == 1 and up["body"]["parent"] == "campaign"
    assert "PHONE_REF:B1" in up["body"]["content"] and up["requires_phone_resolution"]
    assert set(up["body"]["transformation"]) == {"phone_number", "user_identifier", "app_variables"}
    assert "Kairos---PT-07a93e21-1a9b" in up["by_agent"]
    from kairos_backend.policy import load_policy
    cfg = sarvam_export.campaign_config("BROKEN_PTP", D0, "PTP_FOLLOW_UP", "B1", load_policy())
    rc = cfg["body"]["app_config"]["retry_config"]
    assert cfg["body"]["dry_run"] and rc["max_retries"] == 2 and all(i >= 120 for i in rc["retry_interval_minutes"])
    assert len(rc["retry_interval_minutes"]) == rc["max_retries"]
    assert sarvam_export.retry_plan("B1", per_channel_day_cap=3, attempts_today=2)["max_retries"] == 0
    g = sarvam_export.gate_response(eng, "B1", datetime.fromisoformat(at(0, 10)))
    assert g["ac_next"] == "dial" and st.journey_id == "BROKEN_PTP"
    eng.process(ev("s", "stop", 0, 11, channel="sms"))
    assert sarvam_export.gate_response(eng, "B1", datetime.fromisoformat(at(0, 12)))["reason"] == "END:STOPPED_OPT_OUT"


# ---------------- API ----------------
def test_churn_api(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("KAIROS_CHURN_DB", str(tmp_path / "churn.db"))
    import kairos_backend.api as api
    api = importlib.reload(api)
    from kairos_backend.churn import api as capi
    e = ChurnEngine(str(tmp_path / "churn.db"))
    capi.configure(ledger=None, engine=e)
    c = TestClient(api.app)
    e.enroll("B1", prof(), "CMP", D0)
    assert c.get("/churn/cohorts").json()["doc"]["cohorts"][0]["id"] == "OPTED_OUT"
    r = c.post("/churn/events", json=ev("r", "reply", channel="whatsapp", touch_id="T1")).json()
    assert r["state"]["cohort"] == "WA_RESPONSIVE"
    assert c.post("/churn/events", json=ev("r", "reply", channel="whatsapp", touch_id="T1")).json()["duplicate"]
    assert c.post("/churn/events", json={"kind": "nope"}).status_code == 422
    assert c.post("/churn/tick", json={"as_of": "2026-11-24"}).json()["day"] == "2026-11-24"
    assert c.get("/churn/borrowers/B1").json()["cohort"] == "WA_RESPONSIVE"
    assert c.get("/churn/borrowers/B1/trace").json()["steps"][0]["event_id"] == "r"
    assert c.get("/churn/borrowers/B1/allowed", params={"day": "2026-11-24"}).json()["journey"] == "WA_FIRST"
    assert c.get("/churn/borrowers/NOPE").status_code == 404
    assert c.get("/churn/coverage", params={"as_of": "2026-11-24"}).json()["total"] == 1
    assert "cohort_flows" in c.get("/churn/analytics", params={"region": "Karnataka"}).json()
    assert "STOPPED_OPT_OUT" in c.get("/churn/fsm").json()["states"]
    rec = c.post("/churn/borrowers/B1/recommend-layer", json={"layer": "L1", "at": at(3)}).json()
    assert rec["accepted"]
    capi.configure(ledger=None, engine=None)

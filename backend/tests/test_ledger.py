"""K06 acceptance: atomic reservation, caps across campaigns, budget, cancellations, K13 holds."""
import threading
from datetime import date, datetime, timedelta

import pytest

from kairos_backend.exceptions import ExceptionEvent
from kairos_backend.executor import Executor, FakeProvider, Supervisor
from kairos_backend.ledger import Instruction, Ledger

D = date(2026, 11, 20)  # a Friday — a contact day


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


@pytest.fixture
def ledger(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "k06.db")
    lg.upsert_campaign("C1", budget_per_case_inr=100, allocated_cases=10, start_date=D, end_date=D + timedelta(days=10),
                       overdue_at_start_inr=50_000, target_amount_pct=60, target_count_pct=50)
    lg.upsert_campaign("C2", budget_per_case_inr=100, allocated_cases=10, start_date=D, end_date=D + timedelta(days=10))
    for t, ch, lang in [("SMS_EN", "sms", "en"), ("WA_HI", "whatsapp", "hi"), ("EM_EN", "email", "en"), ("IVR_HI", "ivr", "hi")]:
        lg.upsert_template(t, ch, lang)
    add_borrower(lg, "B1", dpd=12)
    yield lg
    lg.close()


def add_borrower(lg, bid, dpd=12, obligations=("A",), campaign="C1", lender=None, when=at(7)):
    lg.upsert_borrower(bid, dpd=dpd, dpd_updated_at=when, lender_id=lender,
                       consent={"sms": True, "whatsapp": True, "email": True, "ivr": True, "voicebot": False})
    for o in obligations:
        lg.upsert_obligation(f"{bid}-{o}", bid, campaign, overdue_inr=5000)


def ins(bid="B1", ch="sms", t=None, ob="A", camp="C1", cost=0.20, tpl=None, iid=None):
    tpl = tpl or {"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN", "ivr": "IVR_HI"}[ch]
    return Instruction(bid, f"{bid}-{ob}", camp, ch, tpl, t or at(10), cost, instruction_id=iid)


# ---------- the atomic core ----------
def test_parallel_workers_never_exceed_channel_cap(ledger):
    """4 workers (think: 4 campaigns) each try every 2-hour slot for the same borrower on SMS.
    B1 cap is 3 SMS/day — exactly 3 must be reserved, never 4."""
    slots = [at(h) for h in (8, 10, 12, 14, 16, 18)]
    results, barrier = [], threading.Barrier(4)

    def worker(w):
        barrier.wait()
        for t in slots:
            r = ledger.reserve(ins(t=t, iid=f"W{w}-{t:%H}"))
            results.append(r.reserved)
        ledger.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(results) == 3
    assert ledger.cap_breaches() == []


def test_parallel_workers_stay_within_budget(policy, tmp_path):
    """Pool ₹2 for a one-day campaign. 40 borrowers race for ₹0.40 WhatsApp sends: at most 5 fit."""
    lg = Ledger(policy, tmp_path / "budget.db")
    lg.upsert_campaign("CB", budget_per_case_inr=1, allocated_cases=2, start_date=D, end_date=D)
    lg.upsert_template("WA_HI", "whatsapp", "hi")
    for i in range(40):
        add_borrower(lg, f"P{i}", campaign="CB")
    reserved, barrier = [], threading.Barrier(8)

    def worker(w):
        barrier.wait()
        for i in range(w, 40, 8):
            reserved.append(lg.reserve(ins(f"P{i}", "whatsapp", camp="CB", cost=0.40)).reserved)
        lg.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    camp = lg.campaign_state("CB")
    assert sum(reserved) == 5
    assert camp["committed_inr"] <= camp["pool_inr"] + 1e-9
    lg.close()


def test_same_instruction_reserved_once(ledger):
    first = ledger.reserve(ins(iid="DUP-1"))
    again = ledger.reserve(ins(iid="DUP-1"))
    assert first.reserved and again.reserved and again.idempotent_replay
    assert len(ledger.borrower_state("B1")["touches"]) == 1


def test_stable_id_collapses_identical_proposals(ledger):
    a = ledger.reserve(ins(t=at(11)))
    b = ledger.reserve(ins(t=at(11)))
    assert a.instruction_id == b.instruction_id and b.idempotent_replay


def test_caps_count_across_campaigns(ledger):
    for h in (8, 10, 12):
        assert ledger.reserve(ins(t=at(h), camp="C1")).reserved
    d = ledger.reserve(ins(t=at(14), camp="C2")).decision
    assert (d.decision, d.reason) == ("WAIT", "CHANNEL_CAP")


def test_same_slot_from_two_campaigns_one_wins(ledger):
    """E03: two campaigns race for the borrower at the same moment — one reserved, one blocked."""
    a = ledger.reserve(ins(t=at(11), camp="C1", iid="C1-x"))
    b = ledger.reserve(ins(t=at(11), camp="C2", iid="C2-x"))
    assert a.reserved and not b.reserved and b.decision.reason == "SAME_CHANNEL_GAP"


# ---------- cancellations within scope ----------
def test_stop_cancels_everything_queued_and_suppresses_all_channels(ledger):
    ledger.reserve(ins(ch="sms", t=at(10)))
    ledger.reserve(ins(ch="whatsapp", t=at(12), cost=0.4))
    before = ledger.campaign_state("C1")["committed_inr"]
    assert before == pytest.approx(0.6)
    assert ledger.record_stop("B1", at(9), channel="whatsapp") == 2
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0)
    d = ledger.reserve(ins(ch="email", t=at(15), cost=0.05)).decision
    assert (d.decision, d.reason) == ("CANCEL", "STOP_OPT_OUT")


def test_opt_back_in_is_per_channel(ledger):
    ledger.record_stop("B1", at(9))
    ledger.opt_in("B1", "sms", "v3", at(9, 30))
    assert ledger.reserve(ins(ch="sms", t=at(10))).reserved
    assert ledger.reserve(ins(ch="whatsapp", t=at(11), cost=0.4)).decision.reason == "NO_CONSENT"


def test_verified_payment_cancels_only_that_obligation(ledger):
    add_borrower(ledger, "B2", obligations=("A", "B"))
    a = ledger.reserve(ins("B2", "sms", at(10), ob="A"))
    b = ledger.reserve(ins("B2", "whatsapp", at(12), ob="B", cost=0.4))
    assert ledger.record_payment_verified("B2", "B2-A", 5000, at(9, 30)) == 1
    assert ledger.touch(a.instruction_id)["status"] == "CANCELED"
    assert ledger.touch(b.instruction_id)["status"] == "RESERVED"  # Loan B stays open
    d = ledger.reserve(ins("B2", "sms", at(14), ob="A")).decision
    assert (d.decision, d.reason) == ("CANCEL", "OBLIGATION_CLEARED")


def test_reply_stops_outbound_for_the_day_only(ledger):
    r = ledger.reserve(ins(t=at(15)))
    assert ledger.record_response("B1", at(11), channel="whatsapp") == 1
    assert ledger.touch(r.instruction_id)["status"] == "CANCELED"
    assert ledger.reserve(ins(ch="email", t=at(16), cost=0.05)).decision.reason == "STOP_FOR_DAY"
    ledger.upsert_borrower("B1", 13, at(8, day=D + timedelta(days=1)), {"sms": True})
    assert ledger.reserve(ins(t=at(10, day=D + timedelta(days=1)))).reserved


def test_call_pickup_over_10s_stops_day(ledger):
    assert ledger.record_call("B1", at(9), answered_seconds=8) == 0
    assert ledger.reserve(ins(t=at(10))).reserved
    ledger.record_call("B1", at(11), answered_seconds=25)
    assert ledger.reserve(ins(ch="whatsapp", t=at(12), cost=0.4)).decision.reason == "STOP_FOR_DAY"


# ---------- send-time re-check & executor ----------
def test_send_time_recheck_blocks_and_releases(ledger):
    r = ledger.reserve(ins(t=at(10)))
    ledger.upsert_template("SMS_EN", "sms", "en", approved=False)  # template withdrawn after approval
    ex = Executor(ledger, {"sms": FakeProvider("sms")})
    out = ex.dispatch(r.instruction_id)
    assert (out.outcome, out.reason) == ("BLOCKED", "NO_APPROVED_TEMPLATE")
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0)


def test_dispatch_happens_once(ledger):
    r = ledger.reserve(ins(t=at(10)))
    prov = FakeProvider("sms", cost_inr=0.18)
    ex = Executor(ledger, {"sms": prov})
    assert ex.dispatch(r.instruction_id).outcome == "SENT"
    assert ex.dispatch(r.instruction_id).outcome == "SKIPPED"
    assert prov.sent == [r.instruction_id]
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0.18)  # reconciled to actual


def test_uncertain_send_is_checked_not_resent(ledger):
    r = ledger.reserve(ins(t=at(10)))
    prov = FakeProvider("sms", script=lambda t: "UNKNOWN_BUT_DELIVERED")
    ex = Executor(ledger, {"sms": prov})
    assert ex.dispatch(r.instruction_id).outcome == "UNCERTAIN"
    assert ex.resolve_uncertain(r.instruction_id) == "DELIVERED"
    assert prov.sent == [r.instruction_id]  # asked the provider — never sent twice


def test_uncertain_lost_send_is_released_for_replanning(ledger):
    r = ledger.reserve(ins(t=at(10)))
    ex = Executor(ledger, {"sms": FakeProvider("sms", script=lambda t: "UNKNOWN_LOST")})
    ex.dispatch(r.instruction_id)
    assert ex.resolve_uncertain(r.instruction_id) == "FAILED"
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0)


def test_late_status_never_downgrades(ledger):
    r = ledger.reserve(ins(t=at(10)))
    Executor(ledger, {"sms": FakeProvider("sms")}).dispatch(r.instruction_id)
    assert ledger.record_provider_status(r.instruction_id, "DELIVERED") == "DELIVERED"
    assert ledger.record_provider_status(r.instruction_id, "SENT") == "DELIVERED"  # older SENT arrives late
    assert ledger.record_provider_status(r.instruction_id, "READ") == "DELIVERED"  # read is not a reply


def test_reconcile_exactly_once(ledger):
    r = ledger.reserve(ins(t=at(10)))
    assert ledger.reconcile(r.instruction_id, 0.25) is True
    assert ledger.reconcile(r.instruction_id, 0.25) is False
    assert ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0.25)


def test_supervisor_instruction_carries_reason_version_and_expiry(ledger):
    si = Supervisor(ledger).decide(ins(t=at(10)))
    assert si.action == "SEND" and si.reserved and si.policy_version == "1.1.0"
    assert si.expires_at.startswith("2026-11-20T19:00")
    blocked = Supervisor(ledger).decide(ins(t=at(19, 30)))
    assert (blocked.action, blocked.reason) == ("WAIT", "CONTACT_WINDOW")


# ---------- budget pacing & campaign controls ----------
def test_daily_pacing_is_pool_over_remaining_contact_days(ledger):
    d = ledger.preview(ins(t=at(10)))
    days = len(ledger.policy.contact_days(D, D + timedelta(days=10)))
    assert d.detail["budget"]["today_allowance"] == pytest.approx(1000 / days, abs=0.01)


def test_daily_allowance_is_a_hard_limit(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "pace.db")
    lg.upsert_campaign("CP", budget_per_case_inr=1, allocated_cases=2, start_date=D, end_date=D + timedelta(days=1))
    lg.upsert_template("WA_HI", "whatsapp", "hi")
    for i in range(6):
        add_borrower(lg, f"Q{i}", campaign="CP")
    ok = [lg.reserve(ins(f"Q{i}", "whatsapp", camp="CP", cost=0.4)).reserved for i in range(6)]
    assert sum(ok) == 2  # ₹2 pool over 2 contact days → ₹1 today → two ₹0.40 sends
    lg.close()


def test_campaign_period_pause_and_global_kill_switch(ledger):
    assert ledger.reserve(ins(t=at(10, day=D - timedelta(days=1)))).decision.reason == "OUTSIDE_CAMPAIGN_PERIOD"
    r = ledger.reserve(ins(t=at(10)))
    assert ledger.set_campaign_status("C1", "PAUSED") == 1
    assert ledger.touch(r.instruction_id)["status"] == "CANCELED"
    assert ledger.reserve(ins(t=at(12))).decision.reason == "CAMPAIGN_PAUSED"
    ledger.set_campaign_status("C1", "RUNNING")
    ledger.reserve(ins(t=at(12)))
    assert ledger.set_outbound_paused(True) == 1
    assert ledger.reserve(ins(t=at(14))).decision.reason == "OUTBOUND_PAUSED"


def test_collection_target_stops_campaign(ledger):
    for i in range(5):
        add_borrower(ledger, f"T{i}")
        ledger.record_payment_verified(f"T{i}", f"T{i}-A", 5000, at(9))
    d = ledger.reserve(ins(t=at(10))).decision
    assert (d.decision, d.reason) == ("WAIT", "CAMPAIGN_TARGET_MET")


def test_stale_dpd_holds(ledger):
    ledger.upsert_borrower("B1", 12, at(7) - timedelta(hours=30), {"sms": True})
    assert ledger.reserve(ins(t=at(10))).decision.reason == "DPD_INVALID"


# ---------- K13 holds through the ledger ----------
def test_callback_pauses_nudges_and_dedupes_ticket(ledger):
    ledger.reserve(ins(t=at(15)))
    e = ExceptionEvent("CALLBACK_REQUEST", "B1")
    first = ledger.apply_exception("B1", e, at(10), obligation_id="B1-A")
    second = ledger.apply_exception("B1", e, at(10, 30), obligation_id="B1-A")
    assert first["canceled"] == 1 and first["ticket_id"] == second["ticket_id"]
    assert ledger.reserve(ins(ch="whatsapp", t=at(16), cost=0.4)).decision.reason == "CALLBACK_PENDING"


def test_hardship_beyond_four_days_hands_off(ledger):
    out = ledger.apply_exception("B1", ExceptionEvent("HARDSHIP", "B1", date_offset_days=10), at(10))
    assert out["queue"] == "COLLECTIONS_SUPERVISOR"
    d = ledger.reserve(ins(t=at(12))).decision
    assert (d.decision, d.reason) == ("HANDOFF", "HARDSHIP_HOLD")


def test_abusive_holds_until_supervisor_clears(ledger):
    ledger.apply_exception("B1", ExceptionEvent("ABUSIVE", "B4"), at(10))
    assert ledger.reserve(ins(t=at(12))).decision.reason == "SUPERVISOR_HOLD"
    ledger.clear_supervisor_hold("B1")
    assert ledger.reserve(ins(t=at(12))).reserved


def test_ptp_hold_then_broken_ptp_on_rollover(ledger):
    ledger.apply_exception("B1", ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=2), at(10), obligation_id="B1-A")
    nxt = D + timedelta(days=1)
    ledger.upsert_borrower("B1", 13, at(7, day=nxt), {"sms": True})  # fresh DPD feed for the new day
    assert ledger.reserve(ins(t=at(12, day=nxt))).decision.reason == "PTP_HOLD"
    broken = ledger.start_day(D + timedelta(days=3))
    assert broken == ["B1"]
    st = ledger.borrower_state("B1")
    assert st["broken_ptp"] == 1 and st["ptp_date"] is None


def test_audit_records_every_decision_with_policy_version(ledger):
    ledger.reserve(ins(t=at(10)))
    ledger.reserve(ins(t=at(19, 30)))
    log = ledger.audit_log()
    assert {e["reason"] for e in log if e["type"] == "DECISION"} == {"ELIGIBLE", "CONTACT_WINDOW"}
    assert all(e["policy_version"] == "1.1.0" for e in log)

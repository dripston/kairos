"""K06 acceptance on top of the K03 spine: atomic reservation, caps across campaigns, budget,
cancellations in scope, payments/reversals through K03, K13 holds, and a K03-dataset run."""
import os
import tempfile
import threading
from datetime import date, datetime, timedelta

import pytest

from kairos_backend.exceptions import ExceptionEvent
from kairos_backend.executor import Executor, FakeProvider, Supervisor
from kairos_backend.ledger import Instruction, Ledger, PaymentError

D = date(2026, 11, 20)  # a Friday — a contact day
CH_TPL = {"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN", "ivr": "IVR_HI"}
ALL_CONSENT = {"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 0}


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


def add_loan(lg, bid, loan="A", dpd=12, emi_paise=500_000, consents=None, as_of=D):
    """A K03 borrower + loan + one EMI that is `dpd` days past due on `as_of` (negative = not yet due)."""
    conn = lg._spine()
    with conn:
        conn.execute("INSERT OR IGNORE INTO borrowers (borrower_id, full_name, phone, language) VALUES (?,?,?, 'en')",
                     (bid, f"Test {bid}", "+910000000000"))
        for ch, ok in (consents if consents is not None else ALL_CONSENT).items():
            conn.execute("INSERT OR IGNORE INTO consents (borrower_id, channel, consented, opted_out, updated_at)"
                         " VALUES (?,?,?,0,?)", (bid, ch, ok, as_of.isoformat()))
        conn.execute("INSERT INTO loans (loan_id, borrower_id, product, principal_paise) VALUES (?,?,'personal_loan',?)",
                     (f"{bid}-{loan}", bid, emi_paise * 12))
        conn.execute("INSERT INTO obligations (obligation_id, loan_id, installment_no, due_date, amount_due_paise)"
                     " VALUES (?,?,1,?,?)", (f"{bid}-{loan}-1", f"{bid}-{loan}", (as_of - timedelta(days=dpd)).isoformat(), emi_paise))
    return f"{bid}-{loan}"


def ins(bid="B1", ch="sms", t=None, loan="A", camp="C1", cost=20, tpl=None, iid=None, purpose=None):
    return Instruction(bid, f"{bid}-{loan}-1", camp, ch, tpl or CH_TPL[ch], t or at(10), cost,
                       purpose=purpose, instruction_id=iid)


@pytest.fixture
def ledger(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "k06.db")
    for t, (ch, lang) in {"SMS_EN": ("sms", "en"), "WA_HI": ("whatsapp", "hi"), "EM_EN": ("email", "en"),
                          "IVR_HI": ("ivr", "hi")}.items():
        lg.upsert_template(t, ch, lang)
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10),
                       target_amount_pct=40)
    lg.upsert_campaign("C2", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    lg.enroll("C1", [add_loan(lg, "B1")])
    lg.enroll("C2", ["B1-A"])
    lg.start_day(D)  # daily LMS refresh → DPD is fresh today
    yield lg
    lg.close()


# ---------- the atomic core ----------
def test_parallel_workers_never_exceed_channel_cap(ledger):
    """4 workers (think: 4 campaigns) try every 2-hour slot for one borrower on SMS. B1 cap = 3/day."""
    slots = [at(h) for h in (8, 10, 12, 14, 16, 18)]
    results, barrier = [], threading.Barrier(4)

    def worker(w):
        barrier.wait()
        for t in slots:
            results.append(ledger.reserve(ins(t=t, iid=f"W{w}-{t:%H}")).reserved)
        ledger.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(results) == 3
    assert ledger.cap_breaches() == []


def test_parallel_workers_stay_within_budget(policy, tmp_path):
    """Pool = 5 paise x 40 cases = ₹2 for a one-day campaign. 40 borrowers race for ₹0.40 sends: 5 fit."""
    lg = Ledger(policy, tmp_path / "budget.db")
    lg.upsert_campaign("CB", budget_per_case_paise=5, start_date=D, end_date=D)
    lg.upsert_template("WA_HI", "whatsapp", "hi")
    lg.enroll("CB", [add_loan(lg, f"P{i}") for i in range(40)])
    lg.start_day(D)
    reserved, barrier = [], threading.Barrier(8)

    def worker(w):
        barrier.wait()
        for i in range(w, 40, 8):
            reserved.append(lg.reserve(ins(f"P{i}", "whatsapp", camp="CB", cost=40)).reserved)
        lg.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    camp = lg.campaign_state("CB")
    assert sum(reserved) == 5 and camp["committed_paise"] == 200 == camp["pool_paise"]
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


# ---------- reading the K03 spine ----------
def test_dpd_and_band_come_from_k03_due_dates(ledger):
    add_loan(ledger, "B5", dpd=45)
    ledger.enroll("C1", ["B5-A"])
    d = ledger.preview(ins("B5"))
    assert d.band == "B2" and d.detail["dpd"] == 45


def test_pre_due_emi_is_reminder_only(ledger):
    add_loan(ledger, "B6", dpd=-3)  # due in 3 days → PRE_DUE
    ledger.enroll("C1", ["B6-A"])
    assert ledger.preview(ins("B6")).band == "PRE_DUE"
    assert ledger.preview(ins("B6", purpose="collection")).reason == "PURPOSE_NOT_ALLOWED"


def test_stale_lms_sync_holds(ledger):
    ledger.mark_spine_synced(at(10) - timedelta(hours=30))
    assert ledger.reserve(ins(t=at(10))).decision.reason == "DPD_INVALID"


def test_dataset_loan_with_missing_dpd_is_held(ledger):
    add_loan(ledger, "B7")
    conn = ledger._spine()
    with conn:
        conn.execute("UPDATE loans SET source='dataset', file_dpd=NULL WHERE loan_id='B7-A'")
    ledger.enroll("C1", ["B7-A"])
    assert ledger.reserve(ins("B7")).decision.reason == "DPD_INVALID"


def test_loan_not_in_campaign_is_not_contacted(ledger):
    add_loan(ledger, "B8")
    assert ledger.reserve(ins("B8")).decision.reason == "NOT_ENROLLED"


def test_no_consent_in_k03_means_wait(ledger):
    add_loan(ledger, "B9", consents={"sms": 1, "whatsapp": 0})
    ledger.enroll("C1", ["B9-A"])
    assert ledger.reserve(ins("B9", "whatsapp", cost=40)).decision.reason == "NO_CONSENT"


# ---------- cancellations within scope ----------
def test_stop_cancels_everything_and_updates_k03_consents(ledger):
    ledger.reserve(ins(ch="sms", t=at(10)))
    ledger.reserve(ins(ch="whatsapp", t=at(12), cost=40))
    assert ledger.campaign_state("C1")["committed_paise"] == 60
    assert ledger.record_stop("B1", at(9), channel="whatsapp") == 2
    assert ledger.campaign_state("C1")["committed_paise"] == 0
    st = ledger.borrower_state("B1")
    assert all(c["opted_out"] == 1 for c in st["consents"])
    assert any(e["event_type"] == "consent_changed" for e in st["events"])
    d = ledger.reserve(ins(ch="email", t=at(15), cost=5)).decision
    assert (d.decision, d.reason) == ("CANCEL", "STOP_OPT_OUT")


def test_opt_back_in_is_per_channel(ledger):
    ledger.record_stop("B1", at(9))
    ledger.opt_in("B1", "sms", "v3", at(9, 30))
    assert ledger.reserve(ins(ch="sms", t=at(10))).reserved
    assert ledger.reserve(ins(ch="whatsapp", t=at(11), cost=40)).decision.reason == "STOP_OPT_OUT"


def test_verified_payment_clears_only_that_emi(ledger):
    a, b = add_loan(ledger, "B2", "A"), add_loan(ledger, "B2", "B", emi_paise=320_000)
    ledger.enroll("C1", [a, b])
    ra = ledger.reserve(ins("B2", "sms", at(10), loan="A"))
    rb = ledger.reserve(ins("B2", "whatsapp", at(12), loan="B", cost=40))
    out = ledger.record_payment_verified("B2-A-1", 500_000, at(9, 30), "PAY-1", "UTR-4021-7731")
    assert out == {"applied": True, "obligation_cleared": True, "canceled": 1}
    assert ledger.touch(ra.instruction_id)["status"] == "CANCELED"
    assert ledger.touch(rb.instruction_id)["status"] == "RESERVED"  # Loan B stays open
    d = ledger.reserve(ins("B2", "sms", at(14), loan="A")).decision
    assert (d.decision, d.reason) == ("CANCEL", "OBLIGATION_CLEARED")
    loans = {l["loan_id"]: l for l in ledger.borrower_state("B2", as_of=D)["loans"]}
    assert loans["B2-A"]["cured"] and not loans["B2-B"]["cured"]


def test_partial_payment_keeps_emi_open(ledger):
    out = ledger.record_payment_verified("B1-A-1", 100_000, at(9), "PAY-P", "UTR-PART")
    assert out["applied"] and not out["obligation_cleared"]
    assert ledger.reserve(ins(t=at(10))).reserved


def test_reused_bank_reference_is_rejected(ledger):
    add_loan(ledger, "B3")
    ledger.record_payment_verified("B1-A-1", 100_000, at(9), "PAY-X1", "UTR-SAME")
    with pytest.raises(PaymentError):
        ledger.record_payment_verified("B3-A-1", 100_000, at(9), "PAY-X2", "UTR-SAME")


def test_same_payment_twice_is_applied_once(ledger):
    assert ledger.record_payment_verified("B1-A-1", 100_000, at(9), "PAY-D", "UTR-D")["applied"]
    assert not ledger.record_payment_verified("B1-A-1", 100_000, at(9), "PAY-D", "UTR-D")["applied"]
    assert ledger.campaign_state("C1")["collected_paise"] == 100_000


def test_reversal_reopens_emi_and_rolls_back_progress(ledger):
    ledger.record_payment_verified("B1-A-1", 500_000, at(9), "PAY-R", "UTR-R")
    assert ledger.reserve(ins(t=at(10))).decision.reason == "OBLIGATION_CLEARED"
    assert ledger.record_payment_reversed("PAY-R", at(11), "bounced") is True
    camp = ledger.campaign_state("C1")
    assert camp["collected_paise"] == 0 and camp["cured_count"] == 0
    assert ledger.reserve(ins(t=at(12))).reserved


def test_reply_stops_day_and_is_written_to_k03(ledger):
    r = ledger.reserve(ins(t=at(15)))
    assert ledger.record_response("B1", at(11), channel="whatsapp", details="Kal pay kar dunga") == 1
    assert ledger.touch(r.instruction_id)["status"] == "CANCELED"
    replies = [e for e in ledger.borrower_state("B1")["events"] if e["event_type"] == "reply_received"]
    assert replies and replies[0]["obligation_id"] == "B1-A-1"
    assert ledger.reserve(ins(ch="email", t=at(16), cost=5)).decision.reason == "STOP_FOR_DAY"
    nxt = D + timedelta(days=1)
    ledger.start_day(nxt)
    assert ledger.reserve(ins(t=at(10, day=nxt))).reserved


def test_call_pickup_over_10s_stops_day(ledger):
    assert ledger.record_call("B1", at(9), answered_seconds=8) == 0
    assert ledger.reserve(ins(t=at(10))).reserved
    ledger.record_call("B1", at(11), answered_seconds=25)
    assert ledger.reserve(ins(ch="whatsapp", t=at(12), cost=40)).decision.reason == "STOP_FOR_DAY"


# ---------- send-time re-check & executor ----------
def test_send_time_recheck_blocks_and_releases(ledger):
    r = ledger.reserve(ins(t=at(10)))
    ledger.upsert_template("SMS_EN", "sms", "en", approved=False)  # template withdrawn after approval
    out = Executor(ledger, {"sms": FakeProvider("sms")}).dispatch(r.instruction_id)
    assert (out.outcome, out.reason) == ("BLOCKED", "NO_APPROVED_TEMPLATE")
    assert ledger.campaign_state("C1")["committed_paise"] == 0


def test_dispatch_once_reconciles_and_writes_touch_sent(ledger):
    r = ledger.reserve(ins(t=at(10)))
    prov = FakeProvider("sms", cost_paise=18)
    ex = Executor(ledger, {"sms": prov})
    assert ex.dispatch(r.instruction_id).outcome == "SENT"
    assert ex.dispatch(r.instruction_id).outcome == "SKIPPED"
    assert prov.sent == [r.instruction_id]
    assert ledger.campaign_state("C1")["committed_paise"] == 18  # reconciled to actual
    sent = [e for e in ledger.borrower_state("B1")["events"] if e["event_type"] == "touch_sent"]
    assert len(sent) == 1 and sent[0]["obligation_id"] == "B1-A-1"


def test_uncertain_send_is_checked_not_resent(ledger):
    r = ledger.reserve(ins(t=at(10)))
    prov = FakeProvider("sms", script=lambda t: "UNKNOWN_BUT_DELIVERED")
    ex = Executor(ledger, {"sms": prov})
    assert ex.dispatch(r.instruction_id).outcome == "UNCERTAIN"
    assert ex.resolve_uncertain(r.instruction_id) == "DELIVERED"
    assert prov.sent == [r.instruction_id]


def test_uncertain_lost_send_is_released_for_replanning(ledger):
    r = ledger.reserve(ins(t=at(10)))
    ex = Executor(ledger, {"sms": FakeProvider("sms", script=lambda t: "UNKNOWN_LOST")})
    ex.dispatch(r.instruction_id)
    assert ex.resolve_uncertain(r.instruction_id) == "FAILED"
    assert ledger.campaign_state("C1")["committed_paise"] == 0


def test_late_status_never_downgrades(ledger):
    r = ledger.reserve(ins(t=at(10)))
    Executor(ledger, {"sms": FakeProvider("sms")}).dispatch(r.instruction_id)
    assert ledger.record_provider_status(r.instruction_id, "DELIVERED") == "DELIVERED"
    assert ledger.record_provider_status(r.instruction_id, "SENT") == "DELIVERED"
    assert ledger.record_provider_status(r.instruction_id, "READ") == "DELIVERED"


def test_reconcile_exactly_once(ledger):
    r = ledger.reserve(ins(t=at(10)))
    assert ledger.reconcile(r.instruction_id, 25) is True
    assert ledger.reconcile(r.instruction_id, 25) is False
    assert ledger.campaign_state("C1")["committed_paise"] == 25


def test_supervisor_instruction_carries_reason_version_and_expiry(ledger):
    si = Supervisor(ledger).decide(ins(t=at(10)))
    assert si.action == "SEND" and si.reserved and si.policy_version == "1.1.0"
    assert si.expires_at.startswith("2026-11-20T19:00")
    blocked = Supervisor(ledger).decide(ins(t=at(19, 30)))
    assert (blocked.action, blocked.reason) == ("WAIT", "CONTACT_WINDOW")


# ---------- budget pacing & campaign controls ----------
def test_pool_is_budget_per_case_times_enrolled_and_paced(ledger):
    camp = ledger.campaign_state("C1")
    assert camp["allocated_cases"] == 1 and camp["pool_paise"] == 10_000
    days = len(ledger.policy.contact_days(D, D + timedelta(days=10)))
    d = ledger.preview(ins(t=at(10)))
    assert d.detail["budget"]["today_allowance_paise"] == 10_000 // days


def test_daily_allowance_is_a_hard_limit(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "pace.db")
    lg.upsert_campaign("CP", budget_per_case_paise=48, start_date=D, end_date=D + timedelta(days=1))
    lg.upsert_template("WA_HI", "whatsapp", "hi")
    lg.enroll("CP", [add_loan(lg, f"Q{i}") for i in range(5)])
    lg.start_day(D)
    ok = [lg.reserve(ins(f"Q{i}", "whatsapp", camp="CP", cost=40)).reserved for i in range(5)]
    assert sum(ok) == 3  # pool 240 paise over 2 contact days → 120 today → three 40-paise sends
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
    ledger.enroll("C1", [add_loan(ledger, "B4")])  # overdue at start: B1 ₹5,000 + B4 ₹5,000
    ledger.record_payment_verified("B1-A-1", 500_000, at(9), "PAY-T", "UTR-T")  # 50% ≥ 40% target
    assert ledger.campaign_state("C1")["target_met"]
    d = ledger.reserve(ins("B4", t=at(10))).decision
    assert (d.decision, d.reason) == ("WAIT", "CAMPAIGN_TARGET_MET")


# ---------- K13 holds through the ledger ----------
def test_callback_pauses_nudges_and_dedupes_ticket(ledger):
    ledger.reserve(ins(t=at(15)))
    e = ExceptionEvent("CALLBACK_REQUEST", "B1")
    first = ledger.apply_exception("B1", e, at(10), obligation_id="B1-A-1")
    second = ledger.apply_exception("B1", e, at(10, 30), obligation_id="B1-A-1")
    assert first["canceled"] == 1 and first["ticket_id"] == second["ticket_id"]
    assert ledger.reserve(ins(ch="whatsapp", t=at(16), cost=40)).decision.reason == "CALLBACK_PENDING"


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
    ledger.apply_exception("B1", ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=2), at(10), obligation_id="B1-A-1")
    nxt = D + timedelta(days=1)
    ledger.start_day(nxt)
    assert ledger.reserve(ins(t=at(12, day=nxt))).decision.reason == "PTP_HOLD"
    assert ledger.start_day(D + timedelta(days=3)) == ["B1"]
    st = ledger.borrower_state("B1")["state"]
    assert st["broken_ptp"] == 1 and st["ptp_date"] is None


def test_ptp_paid_before_date_clears_hold(ledger):
    ledger.apply_exception("B1", ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=2), at(10), obligation_id="B1-A-1")
    ledger.record_payment_verified("B1-A-1", 500_000, at(11), "PAY-PTP", "UTR-PTP")
    assert ledger.borrower_state("B1")["state"]["ptp_date"] is None
    assert ledger.start_day(D + timedelta(days=3)) == []


def test_audit_records_every_decision_with_policy_version(ledger):
    ledger.reserve(ins(t=at(10)))
    ledger.reserve(ins(t=at(19, 30)))
    log = ledger.audit_log()
    assert {e["reason"] for e in log if e["type"] == "DECISION"} == {"ELIGIBLE", "CONTACT_WINDOW"}
    assert all(e["policy_version"] == "1.1.0" for e in log)


# ---------- end to end on K03's own synthetic dataset ----------
def test_k03_synthetic_dataset_runs_through_k06(policy, tmp_path):
    from spine.config import DATASET_AS_OF_DATE as AS_OF  # 2026-08-07, a Friday
    from spine.synthetic_dataset import write_dataset

    csv_path = os.path.join(tempfile.mkdtemp(), "syn.csv")
    write_dataset(csv_path, 200, seed=42)
    lg = Ledger(policy, tmp_path / "e2e.db")
    report = lg.import_dataset(csv_path)
    assert report["loaded"] == 200 and not report["rejected"]
    lg.upsert_template("SMS_EN", "sms", "en")
    lg.upsert_campaign("CX", budget_per_case_paise=2_000, start_date=AS_OF, end_date=AS_OF + timedelta(days=13))
    loans = [r[0] for r in lg._conn().execute("SELECT loan_id FROM loans ORDER BY loan_id")]
    assert lg.enroll("CX", loans) == 200
    reasons = {}
    for i, loan_id in enumerate(loans):
        ob, bid = lg._conn().execute("SELECT o.obligation_id, l.borrower_id FROM obligations o JOIN loans l"
                                     " ON l.loan_id=o.loan_id WHERE o.loan_id=?", (loan_id,)).fetchone()
        t = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 8 + (i % 10), 0)
        d = lg.reserve(Instruction(bid, ob, "CX", "sms", "SMS_EN", t, 20)).decision
        reasons[d.reason] = reasons.get(d.reason, 0) + 1
    assert reasons.get("ELIGIBLE", 0) > 150          # most borrowers are contactable on SMS
    assert set(reasons) <= {"ELIGIBLE", "NO_CONSENT", "STOP_OPT_OUT"}  # the only reasons this data can produce
    assert lg.cap_breaches() == []
    lg.close()

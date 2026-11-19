"""K06 human-in-the-loop: approvals, borrower actions, guardrail locks, manual scheduler, case queue,
roles + masking, audit — and the HTTP routes. Every send here is SIMULATED (FakeProvider)."""
import importlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from kairos_backend.exceptions import ExceptionEvent
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.hitl import HITL, Conflict, Forbidden, Refused, mask
from kairos_backend.ledger import Ledger

from .test_ledger import CH_TPL, D, add_loan, at, ins

NOW = at(9)
SAT = D + timedelta(days=1)


def audit_types(lg, type_):
    return [r for r in lg.audit_log(1000) if r["type"] == type_]


@pytest.fixture
def env(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "hitl.db")
    for t, (ch, lang) in {"SMS_EN": ("sms", "en"), "WA_HI": ("whatsapp", "hi"), "EM_EN": ("email", "en"),
                          "IVR_HI": ("ivr", "hi"), "SMS_STD": ("sms", "en")}.items():
        lg.upsert_template(t, ch, lang)
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    lg.enroll("C1", [add_loan(lg, "B1"), add_loan(lg, "BH", dpd=75)])
    lg.start_day(D)
    h = HITL(lg)
    for tpl, intensity in (("SMS_EN", "LOW"), ("WA_HI", "LOW"), ("EM_EN", "LOW"), ("IVR_HI", "STANDARD"), ("SMS_STD", "STANDARD")):
        h.register_template(tpl, intensity, "Hi {first_name}, {amount} is due on {due_date}. Pay: {pay_link}",
                            "ops", "policy_owner", "catalog load", now=NOW)
    providers = {ch: FakeProvider(ch) for ch in ("sms", "whatsapp", "email", "ivr", "voicebot")}
    yield lg, h, Executor(lg, providers), providers
    lg.close()


def committed(lg):
    return lg.campaign_state("C1")["committed_paise"]


# ---------- approval gating ----------
def test_ivr_send_is_pending_holds_budget_and_is_never_dispatched(env):
    lg, h, ex, prov = env
    r = lg.reserve(ins("B1", "ivr", cost=300))
    assert r.reserved and r.decision.detail["touch_status"] == "PENDING_APPROVAL"
    assert r.decision.detail["approval_rules"] == ["VOICE_CHANNEL"]
    assert lg.touch(r.instruction_id)["status"] == "PENDING_APPROVAL"
    assert committed(lg) == 300  # counts toward budget like RESERVED
    assert ex.dispatch(r.instruction_id).outcome == "SKIPPED"
    assert ex.dispatch_due(at(18)) == [] and prov["ivr"].sent == []
    # it also holds its cap slot: a second IVR within the gap is blocked by the Gate
    assert lg.reserve(ins("B1", "ivr", t=at(11))).decision.reason == "SAME_CHANNEL_GAP"
    assert lg.cap_breaches() == []


def test_approve_rechecks_and_then_dispatch_sends(env):
    lg, h, ex, prov = env
    r = lg.reserve(ins("B1", "ivr", cost=300))
    out = h.approve(r.instruction_id, "ravi", "supervisor", "borrower asked for a call", now=NOW)
    assert out["outcome"] == "APPROVED" and out["gate"]["decision"] == "SEND"
    assert lg.touch(r.instruction_id)["status"] == "RESERVED"
    assert ex.dispatch(r.instruction_id, at(10)).outcome == "SENT" and prov["ivr"].sent == [r.instruction_id]
    row = audit_types(lg, "APPROVAL_GRANTED")[0]
    assert row["actor"] == "human:ravi" and row["reason"] == "borrower asked for a call"
    assert row["detail"]["role"] == "supervisor"


def test_operator_cannot_approve_and_refusal_is_audited(env):
    lg, h, _, _ = env
    r = lg.reserve(ins("B1", "ivr"))
    with pytest.raises(Forbidden):
        h.approve(r.instruction_id, "asha", "operator", "looks fine", now=NOW)
    assert lg.touch(r.instruction_id)["status"] == "PENDING_APPROVAL"
    refused = audit_types(lg, "HUMAN_ACTION_REFUSED")[0]
    assert refused["detail"]["refusal"] == "ROLE_NOT_PERMITTED" and refused["instruction_id"] == r.instruction_id


def test_reject_cancels_and_releases_budget(env):
    lg, h, _, _ = env
    r = lg.reserve(ins("B1", "ivr", cost=300))
    assert committed(lg) == 300
    h.reject(r.instruction_id, "asha", "operator", "too aggressive today", now=NOW)
    t = lg.touch(r.instruction_id)
    assert (t["status"], t["reason"]) == ("CANCELED", "REJECTED_BY_HUMAN") and committed(lg) == 0
    with pytest.raises(Conflict):
        h.approve(r.instruction_id, "ravi", "supervisor", "late", now=NOW)


def test_approval_expires_at_end_of_contact_window(env):
    lg, h, _, _ = env
    a = lg.reserve(ins("B1", "ivr", cost=300))
    b = lg.reserve(ins("BH", "ivr", cost=300))
    assert h.tick(at(18, 59))["expired_approvals"] == 0
    assert h.tick(at(19))["expired_approvals"] == 2
    assert lg.touch(a.instruction_id)["reason"] == "APPROVAL_EXPIRED" and committed(lg) == 0
    assert len(audit_types(lg, "APPROVAL_EXPIRED")) == 2
    assert lg.touch(b.instruction_id)["status"] == "CANCELED"


def test_approving_after_expiry_cancels_instead(env):
    lg, h, _, _ = env
    r = lg.reserve(ins("B1", "ivr"))
    assert h.approve(r.instruction_id, "ravi", "supervisor", "ok", now=at(19, 30))["outcome"] == "EXPIRED"
    assert lg.touch(r.instruction_id)["status"] == "CANCELED"


def test_stop_between_reserve_and_approval_blocks_at_recheck(env):
    """The K03 feed marks the borrower opted out (no K06 event): the approval re-runs the Gate and loses."""
    lg, h, ex, prov = env
    r = lg.reserve(ins("B1", "ivr", cost=300))
    with lg._spine() as conn:
        conn.execute("UPDATE consents SET opted_out=1 WHERE borrower_id='B1'")
    out = h.approve(r.instruction_id, "ravi", "supervisor", "approve", now=NOW)
    assert out["outcome"] == "BLOCKED_AT_RECHECK" and out["gate"]["reason"] == "STOP_OPT_OUT"
    assert lg.touch(r.instruction_id)["status"] == "CANCELED" and committed(lg) == 0
    assert ex.dispatch(r.instruction_id).outcome == "SKIPPED" and prov["ivr"].sent == []


def test_stop_event_cancels_pending_approvals(env):
    lg, h, _, _ = env
    r = lg.reserve(ins("B1", "ivr", cost=300))
    assert lg.record_stop("B1", at(9, 30), "sms") == 1
    assert lg.touch(r.instruction_id)["reason"] == "STOP_OPT_OUT" and committed(lg) == 0
    assert h.list_approvals("supervisor", now=NOW) == []


def test_standard_intensity_needs_approval_only_above_dpd_threshold(env):
    lg, h, _, _ = env
    hi_std = lg.reserve(ins("BH", "sms", tpl="SMS_STD"))
    assert hi_std.decision.detail["approval_rules"] == ["STANDARD_INTENSITY_HIGH_DPD"]
    hi_low = lg.reserve(ins("BH", "whatsapp"))
    assert lg.touch(hi_low.instruction_id)["status"] == "RESERVED"
    lo_std = lg.reserve(ins("B1", "sms", tpl="SMS_STD"))
    assert lg.touch(lo_std.instruction_id)["status"] == "RESERVED"  # DPD 12 is under the threshold


def test_first_send_after_hardship_needs_approval_then_not_again(env):
    lg, h, _, _ = env
    lg.apply_exception("B1", ExceptionEvent("HARDSHIP", "B1", date_offset_days=1), NOW)  # hold until Sat 09:00
    assert lg.reserve(ins("B1", "sms", t=at(10))).decision.reason == "HARDSHIP_HOLD"
    lg.start_day(SAT)
    first = lg.reserve(ins("B1", "sms", t=at(10, day=SAT)))
    assert first.decision.detail["approval_rules"] == ["FIRST_SEND_AFTER_HARDSHIP"]
    assert h.approve(first.instruction_id, "ravi", "supervisor", "tone checked", now=at(9, 30, SAT))["outcome"] == "APPROVED"
    second = lg.reserve(ins("B1", "whatsapp", t=at(13, day=SAT)))
    assert lg.touch(second.instruction_id)["status"] == "RESERVED"


# ---------- borrower actions ----------
ACTIONS = [("pause", {}), ("resume", {}), ("hold", {"days": 1}), ("cancel_next", {}),
           ("handoff", {"queue": "COLLECTIONS_SUPERVISOR"}), ("assign", {"agent": "Priya"}), ("dnc", {})]


def test_every_borrower_action_writes_one_audit_row_with_reason(env):
    lg, h, _, _ = env
    for action, params in ACTIONS:
        before = len(lg.audit_log(1000))
        h.act("B1", action, "asha", "operator", f"reason for {action}", now=NOW, **params)
        rows = lg.audit_log(1000)
        assert len(rows) == before + 1, action
        assert (rows[0]["type"], rows[0]["detail"]["action"], rows[0]["reason"]) == ("HUMAN_ACTION", action, f"reason for {action}")
        assert rows[0]["actor"] == "human:asha" and rows[0]["detail"]["role"] == "operator"


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_reason_is_mandatory(env, reason):
    lg, h, _, _ = env
    before = len(lg.audit_log(1000))
    with pytest.raises(ValueError):
        h.act("B1", "pause", "asha", "operator", reason, now=NOW)
    with pytest.raises(ValueError):
        h.schedule_manual("B1", "sms", "SMS_EN", at(11), "asha", "operator", reason, 20, now=NOW)
    assert len(lg.audit_log(1000)) == before and lg.borrower_state("B1")["state"]["supervisor_hold"] == 0


def test_pause_cancels_queue_blocks_sends_and_resume_restores(env):
    lg, h, _, _ = env
    r = lg.reserve(ins("B1", "sms", cost=20))
    out = h.act("B1", "pause", "asha", "operator", "borrower in hospital per branch", now=NOW)
    assert out["canceled"] == 1 and lg.touch(r.instruction_id)["reason"] == "MANUAL_PAUSE" and committed(lg) == 0
    assert lg.reserve(ins("B1", "sms", t=at(13))).decision.reason == "SUPERVISOR_HOLD"
    assert h.act("B1", "pause", "asha", "operator", "again", now=NOW)["already_paused"]
    res = h.act("B1", "resume", "asha", "operator", "branch confirmed discharge", now=NOW)
    assert res["released_pauses"] == 1 and res["still_blocked_by"] == []
    assert lg.reserve(ins("B1", "sms", t=at(13))).reserved


def test_resume_cannot_silently_lift_a_k13_supervisor_hold(env):
    lg, h, _, _ = env
    lg.apply_exception("B1", ExceptionEvent("ABUSIVE", "B1"), NOW, "B1-A-1")
    with pytest.raises(Forbidden):
        h.act("B1", "resume", "asha", "operator", "seems calm now", now=NOW)
    # an operator's own pause on top of it: resuming the pause leaves the K13 hold in place
    h.act("B1", "pause", "asha", "operator", "pause too", now=NOW)
    assert h.act("B1", "resume", "asha", "operator", "lift my pause", now=NOW)["still_blocked_by"] == ["SUPERVISOR_HOLD"]
    assert lg.reserve(ins("B1", "sms", t=at(13))).decision.reason == "SUPERVISOR_HOLD"
    out = h.act("B1", "resume", "ravi", "supervisor", "reviewed transcript", now=NOW)
    assert out["cleared_external_hold"] and lg.reserve(ins("B1", "sms", t=at(13))).reserved


def test_hold_until_rechecks_payment_before_outreach_resumes(env):
    lg, h, _, _ = env
    h.act("B1", "hold", "asha", "operator", "salary credit expected", until=at(15), now=NOW)
    assert lg.reserve(ins("B1", "sms", t=at(16))).decision.reason == "SUPERVISOR_HOLD"
    assert h.release_due_holds(at(14, 59)) == []
    released = h.release_due_holds(at(15))
    assert released[0]["payment"] == "UNPAID" and released[0]["unpaid_obligations"] == ["B1-A-1"]
    assert audit_types(lg, "HOLD_RELEASED")[0]["detail"]["payment"] == "UNPAID"
    assert lg.reserve(ins("B1", "sms", t=at(16))).reserved
    # paid during the hold: the release records it, and the Gate cancels anything for the cleared EMI
    h.act("BH", "hold", "asha", "operator", "promised by phone", until=at(15), now=NOW)
    lg.record_payment_verified("BH-A-1", 500_000, at(12), "PAY-H1", "UTR-H1")
    assert h.release_due_holds(at(15))[0]["payment"] == "PAID"
    assert lg.reserve(ins("BH", "sms", t=at(16))).decision.reason == "OBLIGATION_CLEARED"


def test_hold_must_be_in_the_future(env):
    _, h, _, _ = env
    with pytest.raises(ValueError):
        h.act("B1", "hold", "asha", "operator", "oops", until=at(8), now=NOW)


def test_cancel_next_cancels_only_the_earliest_queued_touch(env):
    lg, h, _, _ = env
    a = lg.reserve(ins("B1", "sms", t=at(10), cost=20))
    b = lg.reserve(ins("B1", "sms", t=at(13), cost=20))
    out = h.act("B1", "cancel_next", "asha", "operator", "duplicate of branch call", now=NOW)
    assert out["canceled_instruction_id"] == a.instruction_id
    assert lg.touch(a.instruction_id)["status"] == "CANCELED" and lg.touch(b.instruction_id)["status"] == "RESERVED"
    assert committed(lg) == 20


def test_handoff_is_idempotent_and_pauses_outreach(env):
    lg, h, _, _ = env
    lg.reserve(ins("B1", "sms"))
    one = h.act("B1", "handoff", "asha", "operator", "wants a person", summary="Call +91 98765 43210 re: EMI", now=NOW)
    two = h.act("B1", "handoff", "asha", "operator", "clicked twice", now=NOW)
    assert one["ticket_id"] == two["ticket_id"] and two["idempotent"] and one["canceled"] == 1
    cases = h.list_cases("operator", queue="COLLECTIONS_SUPERVISOR", now=NOW)
    assert [c["ticket_id"] for c in cases] == [one["ticket_id"]]
    assert cases[0]["type"] == "HUMAN_HANDOFF" and cases[0]["sla_due_at"].startswith("2026-11-20T13:00")
    assert "98765" not in cases[0]["summary_line_1"]  # PII scrubbed before it is stored
    assert lg.reserve(ins("B1", "sms", t=at(13))).decision.reason == "SUPERVISOR_HOLD"
    with pytest.raises(ValueError):
        h.act("B1", "handoff", "asha", "operator", "x", queue="NOPE", now=NOW)


def test_assign_to_agent(env):
    _, h, _, _ = env
    h.act("B1", "assign", "asha", "operator", "language match", agent="Priya", now=NOW)
    out = h.act("B1", "assign", "ravi", "supervisor", "rebalance", agent="Imran", now=NOW)
    assert out["previous_agent"] == "Priya" and h.borrower_controls("B1", "viewer")["assignment"]["agent"] == "Imran"


def test_dnc_suppresses_all_channels_and_only_borrower_opt_in_reenables_one(env):
    lg, h, _, _ = env
    q = [lg.reserve(ins("B1", ch, t=at(10 + i))) for i, ch in enumerate(("sms", "whatsapp", "email"))]
    assert h.act("B1", "dnc", "asha", "operator", "borrower asked branch to stop", now=NOW)["canceled"] == 3
    assert all(lg.touch(r.instruction_id)["reason"] == "DO_NOT_CONTACT" for r in q) and committed(lg) == 0
    for ch in ("sms", "whatsapp", "email", "ivr"):
        assert lg.reserve(ins("B1", ch, t=at(15))).decision.reason == "STOP_OPT_OUT"
    for action in ("undo_dnc", "override_stop", "override_consent"):
        with pytest.raises(Refused):
            h.act("B1", action, "ravi", "policy_owner", "try to undo", now=NOW)
    assert h.act("B1", "resume", "asha", "operator", "try to undo", now=NOW)["still_blocked_by"] == ["STOP_OPT_OUT"]
    assert lg.reserve(ins("B1", "sms", t=at(15))).decision.reason == "STOP_OPT_OUT"  # resume does not undo DNC
    with pytest.raises(Forbidden):
        h.act("B1", "record_opt_in", "asha", "operator", "she said ok", channel="sms", consent_version="v2", now=NOW)
    with pytest.raises(ValueError):
        h.act("B1", "record_opt_in", "ravi", "supervisor", "no evidence", channel="sms", now=NOW)
    h.act("B1", "record_opt_in", "ravi", "supervisor", "signed opt-in form", channel="sms", consent_version="CV-2026-11", now=NOW)
    assert lg.reserve(ins("B1", "sms", t=at(15))).reserved
    assert lg.reserve(ins("B1", "whatsapp", t=at(15))).decision.reason == "STOP_OPT_OUT"


@pytest.mark.parametrize("action", ["override_stop", "override_consent", "exceed_cap", "bypass_quiet_hours",
                                    "bypass_contact_window", "send_unregistered_template", "force_send", "undo_dnc"])
def test_locked_actions_are_refused_for_every_role_and_audited(env, action):
    lg, h, _, _ = env
    for role in ("viewer", "operator", "supervisor", "policy_owner"):
        with pytest.raises(Refused) as e:
            h.act("B1", action, "someone", role, "please", now=NOW)
        assert e.value.code == "LOCKED_BY_GUARDRAILS"
    rows = audit_types(lg, "HUMAN_ACTION_REFUSED")
    assert len(rows) == 4 and {r["detail"]["role"] for r in rows} == {"viewer", "operator", "supervisor", "policy_owner"}
    assert all(r["detail"]["action"] == action for r in rows)


# ---------- manual scheduler ----------
def test_manual_send_dedupes_against_automated_touch_same_emi_channel_day(env):
    lg, h, _, _ = env
    auto = lg.reserve(ins("B1", "sms", t=at(10)))
    assert auto.reserved
    out = h.schedule_manual("B1", "sms", "SMS_EN", at(15), "asha", "operator", "follow-up", 20, now=NOW)
    r = out["results"][0]
    assert (r["outcome"], r["reason"]) == ("REFUSED", "MANUAL_DUPLICATE") and out["refused"] == 1
    assert audit_types(lg, "HUMAN_ACTION_REFUSED")[0]["detail"]["refusal"] == "MANUAL_DUPLICATE"
    other = h.schedule_manual(["B1"], "whatsapp", "WA_HI", at(15), "asha", "operator", "try WhatsApp", 40, now=NOW)
    assert other["pending_approval"] == 1 and other["results"][0]["approval_rules"] == ["MANUAL_DISPATCH"]
    # and an automated touch can't double up a manual one either (same-channel gap, across sources)
    assert lg.reserve(ins("B1", "whatsapp", t=at(16))).decision.reason == "SAME_CHANNEL_GAP"


def test_manual_send_respects_caps_window_and_template(env):
    lg, h, _, _ = env
    lg.enroll("C1", [add_loan(lg, "B1", loan="B")])
    for hh in (8, 10, 12):
        assert lg.reserve(ins("B1", "sms", t=at(hh))).reserved  # loan A: B1 SMS cap 3/day reached
    cap = h.schedule_manual("B1", "sms", "SMS_EN", at(15), "asha", "operator", "loan B reminder", 20,
                            obligation_id="B1-B-1", now=NOW)["results"][0]
    assert (cap["reason"], cap["locked_by_guardrails"]) == ("CHANNEL_CAP", True)
    late = h.schedule_manual("BH", "sms", "SMS_EN", at(21), "asha", "operator", "after hours", 20, now=NOW)["results"][0]
    assert late["reason"] == "CONTACT_WINDOW" and late["locked_by_guardrails"]
    sunday = h.schedule_manual("BH", "sms", "SMS_EN", at(10, day=D + timedelta(days=2)), "asha", "operator", "sun", 20,
                               now=NOW)["results"][0]
    assert sunday["reason"] in ("QUIET_DAY", "DPD_INVALID") and sunday["locked_by_guardrails"]
    tpl = h.schedule_manual("BH", "sms", "SMS_FREE_TEXT", at(11), "asha", "operator", "custom words", 20, now=NOW)["results"][0]
    assert tpl["reason"] == "NO_APPROVED_TEMPLATE"
    with pytest.raises(Refused):
        h.schedule_manual("BH", "sms", "SMS_EN", at(21), "ravi", "policy_owner", "just this once", 20, force=True, now=NOW)
    assert len(audit_types(lg, "HUMAN_ACTION_REFUSED")) == 5
    assert lg.cap_breaches() == []


def test_manual_send_needs_a_second_person_then_dispatches(env):
    lg, h, ex, prov = env
    iid = h.schedule_manual("BH", "sms", "SMS_EN", at(11), "asha", "supervisor", "promised a reminder", 20,
                            now=NOW)["results"][0]["instruction_id"]
    assert lg.touch(iid)["status"] == "PENDING_APPROVAL" and committed(lg) == 20
    with pytest.raises(Forbidden) as e:
        h.approve(iid, "asha", "supervisor", "self-approve", now=NOW)
    assert e.value.code == "MAKER_CHECKER"
    assert h.approve(iid, "ravi", "supervisor", "ok", now=NOW)["outcome"] == "APPROVED"
    assert [r.outcome for r in ex.dispatch_due(at(11))] == ["SENT"] and prov["sms"].sent == [iid]
    assert audit_types(lg, "MANUAL_SCHEDULED")[0]["instruction_id"] == iid


def test_manual_for_a_list_of_borrowers(env):
    lg, h, _, _ = env
    with pytest.raises(KeyError):  # unknown borrower → 404 at the API, and nothing is scheduled for the others
        h.schedule_manual(["B1", "BH", "NOPE"], "email", "EM_EN", at(11), "asha", "operator", "statement", 5, now=NOW)
    assert lg.borrower_state("B1")["touches"] == []
    out = h.schedule_manual(["B1", "BH"], "email", "EM_EN", at(11, 30), "asha", "operator", "statement", 5, now=NOW)
    assert out["pending_approval"] == 2 and len(h.list_approvals("viewer", now=NOW)) >= 2


def test_cancel_and_reschedule(env):
    lg, h, _, _ = env
    iid = h.schedule_manual("BH", "sms", "SMS_EN", at(11), "asha", "operator", "reminder", 20, now=NOW)["results"][0]["instruction_id"]
    moved = h.reschedule(iid, at(15), "asha", "operator", "borrower asked for afternoon", now=NOW)
    new = moved["new"]["instruction_id"]
    assert lg.touch(iid)["status"] == "CANCELED" and lg.touch(new)["status"] == "PENDING_APPROVAL" and committed(lg) == 20
    assert lg.touch(new)["scheduled_at"].startswith("2026-11-20T15:00")
    # moving outside the window is refused and the original stays where it was
    back = h.reschedule(new, at(21), "asha", "operator", "evening", now=NOW)
    assert back["restored"] and back["new"]["reason"] == "CONTACT_WINDOW"
    assert lg.touch(new)["status"] == "PENDING_APPROVAL" and committed(lg) == 20
    assert len(h.list_approvals("supervisor", now=NOW)) == 1
    h.cancel_scheduled(new, "asha", "operator", "paid at branch", now=NOW)
    assert lg.touch(new)["status"] == "CANCELED" and committed(lg) == 0
    with pytest.raises(Conflict):
        h.cancel_scheduled(new, "asha", "operator", "again", now=NOW)
    # automated touches can be canceled from the queue too
    auto = lg.reserve(ins("B1", "sms"))
    h.cancel_scheduled(auto.instruction_id, "asha", "operator", "queue clean-up", now=NOW)
    assert lg.touch(auto.instruction_id)["reason"] == "CANCELED_BY_HUMAN"


def test_live_preview_renders_masked_template_without_reserving(env):
    lg, h, _, _ = env
    p = h.preview_manual("BH", "sms", "SMS_EN", at(11), "operator", 20)
    assert p["allowed"] and p["would_need_approval"] == ["MANUAL_DISPATCH"]
    assert p["preview"]["rendered"].startswith("[SIMULATED] Hi Test,") and "₹5,000.00" in p["preview"]["rendered"]
    v = h.preview_manual("BH", "sms", "SMS_EN", at(11), "viewer", 20)["preview"]["rendered"]
    assert "Test" not in v and "5,000" not in v and "•••" in v
    assert lg.borrower_state("BH")["touches"] == [] and committed(lg) == 0
    blocked = h.preview_manual("BH", "sms", "SMS_EN", at(21), "operator")
    assert not blocked["allowed"] and blocked["reason"] == "CONTACT_WINDOW" and blocked["locked_by_guardrails"]


# ---------- case queue ----------
def test_case_lifecycle_and_explicit_hold_lift(env):
    lg, h, _, _ = env
    out = lg.apply_exception("B1", ExceptionEvent("CALLBACK_REQUEST", "B1"), NOW, "B1-A-1", ("Wants a call", "re EMI"))
    tid = out["ticket_id"]
    cases = h.list_cases("operator", queue="COLLECTIONS_SUPERVISOR", now=NOW)
    assert cases[0]["ticket_id"] == tid and cases[0]["sla_due_at"] and not cases[0]["sla_breached"]
    assert h.list_cases("operator", now=at(12))[0]["sla_breached"]
    h.claim_case(tid, "asha", "operator", now=NOW)
    with pytest.raises(Conflict):
        h.claim_case(tid, "ravi", "operator", now=NOW)
    h.resolve_case(tid, "asha", "operator", "called back, will pay Monday", "CONTACTED_RESOLVED", now=NOW)
    assert lg.borrower_state("B1")["state"]["callback_pending"] == 1  # resolving alone never lifts the hold
    assert h.list_cases("operator", now=NOW) == []
    with pytest.raises(Forbidden):
        h.reopen_case(tid, "asha", "operator", "need to lift hold", now=NOW)
    h.reopen_case(tid, "ravi", "supervisor", "lift the callback hold", now=NOW)
    with pytest.raises(Forbidden):
        h.resolve_case(tid, "asha", "operator", "done", "CONTACTED_RESOLVED", lift_hold=True, now=NOW)
    with pytest.raises(ValueError):
        h.resolve_case(tid, "ravi", "supervisor", "done", "MADE_UP_CODE", now=NOW)
    res = h.resolve_case(tid, "ravi", "supervisor", "callback done", "CONTACTED_RESOLVED", lift_hold=True, now=NOW)
    assert res["hold_lifted"] == "CALLBACK_PENDING" and lg.borrower_state("B1")["state"]["callback_pending"] == 0
    assert lg.reserve(ins("B1", "sms", t=at(13))).reserved
    assert [r["type"] for r in h.audit("viewer", borrower_id="B1", types=["CASE_CLAIMED", "CASE_RESOLVED", "CASE_REOPENED"])] == \
        ["CASE_RESOLVED", "CASE_REOPENED", "CASE_RESOLVED", "CASE_CLAIMED"]


def test_resolving_handoff_lifts_its_hold_only_when_asked(env):
    lg, h, _, _ = env
    tid = h.act("B1", "handoff", "asha", "operator", "dispute", now=NOW)["ticket_id"]
    h.resolve_case(tid, "ravi", "supervisor", "closed", "NO_ACTION_NEEDED", now=NOW)
    assert lg.borrower_state("B1")["state"]["supervisor_hold"] == 1
    h.reopen_case(tid, "ravi", "supervisor", "lift", now=NOW)
    h.resolve_case(tid, "ravi", "supervisor", "closed", "NO_ACTION_NEEDED", lift_hold=True, now=NOW)
    assert lg.borrower_state("B1")["state"]["supervisor_hold"] == 0


# ---------- roles, masking, audit ----------
def test_mask_by_role():
    rec = {"full_name": "Asha Kumar", "phone": "+919876543210", "email": "asha@example.com",
           "amount_paise": 500_000, "nested": [{"phone": "9876543210"}], "borrower_id": "B1"}
    v = mask(rec, "viewer")
    assert (v["full_name"], v["phone"], v["email"], v["amount_paise"]) == ("A. K.", "••••••••••10", "•••", "•••")
    assert v["nested"][0]["phone"] == "••••••••10" and v["borrower_id"] == "B1"
    o = mask(rec, "operator")
    assert (o["full_name"], o["phone"], o["email"], o["amount_paise"]) == ("Asha Kumar", "••••••••3210", "•••@example.com", 500_000)
    assert mask(rec, "hacker")["full_name"] == "A. K."  # unknown role → strictest view


def test_permissions_viewer_reads_only(env):
    lg, h, _, _ = env
    r = lg.reserve(ins("B1", "ivr"))
    rows = h.list_approvals("viewer", now=NOW)
    assert rows[0]["phone"].startswith("•") and rows[0]["full_name"] == "T. B."
    assert h.list_approvals("supervisor", now=NOW)[0]["full_name"] == "Test B1"
    for call in (lambda: h.act("B1", "pause", "v", "viewer", "x", now=NOW),
                 lambda: h.reject(r.instruction_id, "v", "viewer", "x", now=NOW),
                 lambda: h.schedule_manual("B1", "sms", "SMS_EN", at(11), "v", "viewer", "x", 20, now=NOW),
                 lambda: h.act("B1", "pause", "x", "root", "x", now=NOW)):
        with pytest.raises(Forbidden):
            call()
    assert {r["detail"]["refusal"] for r in audit_types(lg, "HUMAN_ACTION_REFUSED")} == {"ROLE_NOT_PERMITTED", "UNKNOWN_ROLE"}
    with pytest.raises(Forbidden):
        h.list_approvals("root")
    assert set(h.borrower_controls("B1", "viewer")["allowed_actions"]) == set()
    assert "dnc" in h.borrower_controls("B1", "operator")["allowed_actions"]


def test_audit_is_queryable_and_never_stores_raw_pii(env):
    lg, h, _, _ = env
    h.act("B1", "pause", "asha", "operator", "she said call +91 98765 43210 or mail asha.k@example.com", now=NOW)
    h.act("BH", "assign", "ravi", "supervisor", "rebalance", agent="Imran", now=at(10))
    raw = lg._conn().execute("SELECT group_concat(reason || detail, ' ') FROM audit").fetchone()[0]
    assert "98765" not in raw and "asha.k@example.com" not in raw and "Test B1" not in raw
    assert [r["borrower_id"] for r in h.audit("viewer", human_only=True, types=["HUMAN_ACTION"])] == ["BH", "B1"]
    assert [r["detail"]["action"] for r in h.audit("viewer", actor="asha")] == ["pause"]
    assert len(h.audit("viewer", since=at(9, 30), human_only=True)) == 1
    assert h.audit("viewer", borrower_id="B1", types=["HUMAN_ACTION"])[0]["reason"].count("[phone]") == 1


# ---------- HTTP ----------
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_DB", str(tmp_path / "api.db"))
    import kairos_backend.api as api_mod
    api_mod = importlib.reload(api_mod)
    lg = api_mod.ledger
    for t, ch, lang in (("SMS_EN", "sms", "en"), ("IVR_HI", "ivr", "hi"), ("WA_HI", "whatsapp", "hi")):
        lg.upsert_template(t, ch, lang)
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    lg.enroll("C1", [add_loan(lg, "B1"), add_loan(lg, "BH", dpd=75)])
    lg.start_day(D)
    yield TestClient(api_mod.app), api_mod
    lg.close()


def H(role):
    return {"X-Kairos-Role": role}


def test_api_approvals_flow(api):
    c, mod = api
    body = {"borrower_id": "B1", "obligation_id": "B1-A-1", "campaign_id": "C1", "channel": "ivr",
            "template_id": "IVR_HI", "at": f"{D}T10:00:00", "est_cost_paise": 300}
    d = c.post("/decide", json=body).json()
    assert d["reserved"] and d["detail"]["touch_status"] == "PENDING_APPROVAL"
    iid = d["instruction_id"]
    assert c.post(f"/dispatch/{iid}").json()["outcome"] == "SKIPPED"
    rows = c.get("/hitl/approvals", params={"now": f"{D}T09:00:00"}, headers=H("viewer")).json()
    assert [r["instruction_id"] for r in rows] == [iid] and rows[0]["phone"].startswith("•")
    human = {"actor": "ravi", "reason": "call approved", "now": f"{D}T09:00:00"}
    assert c.post(f"/hitl/approvals/{iid}/approve", json=human, headers=H("operator")).status_code == 403
    assert c.post(f"/hitl/approvals/{iid}/approve", json={**human, "reason": ""}, headers=H("supervisor")).status_code == 422
    ok = c.post(f"/hitl/approvals/{iid}/approve", json=human, headers=H("supervisor")).json()
    assert ok["outcome"] == "APPROVED"
    assert c.post(f"/dispatch/{iid}", params={"at": f"{D}T10:00:00"}).json()["outcome"] == "SENT"
    again = c.post(f"/hitl/approvals/{iid}/reject", json=human, headers=H("supervisor"))
    assert again.status_code == 409 and again.json()["detail"]["code"] == "NOT_PENDING_SENT"


def test_api_borrower_actions_manual_cases_audit(api):
    c, mod = api
    now = f"{D}T09:00:00"
    act = lambda bid, **b: c.post(f"/hitl/borrowers/{bid}/actions", json={"actor": "asha", "reason": "r", "now": now, **b},
                                  headers=H("operator"))
    assert act("B1", action="pause").json()["action"] == "pause"
    locked = act("B1", action="override_stop")
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "LOCKED_BY_GUARDRAILS"
    assert act("B1", action="resume").json()["released_pauses"] == 1
    assert act("B1", action="hold", until=f"{D}T15:00:00").json()["until"].startswith(f"{D}T15:00")
    assert c.post("/hitl/tick", params={"now": f"{D}T15:00:00"}).json()["released_holds"][0]["payment"] == "UNPAID"
    assert act("NOPE", action="pause").status_code == 404
    assert act("B1", action="fly").status_code == 422
    tid = act("B1", action="handoff", summary="wants a person").json()["ticket_id"]
    assert c.get("/hitl/borrowers/B1", headers=H("viewer")).json()["supervisor_hold"]

    meta = {"actor": "ojas", "reason": "catalog", "intensity": "LOW", "body": "Hi {first_name}, {amount} due {due_date}"}
    assert c.put("/hitl/templates/SMS_EN", json=meta, headers=H("operator")).status_code == 403
    assert c.put("/hitl/templates/SMS_EN", json=meta, headers=H("policy_owner")).json()["intensity"] == "LOW"
    m = {"actor": "asha", "reason": "reminder", "now": now, "borrower_ids": ["BH"], "channel": "sms",
         "template_id": "SMS_EN", "at": f"{D}T11:00:00", "est_cost_paise": 20}
    prev = c.post("/hitl/manual/preview", json={k: m[k] for k in ("channel", "template_id", "at")} | {"borrower_id": "BH"},
                  headers=H("operator")).json()
    assert prev["allowed"] and prev["would_need_approval"] == ["MANUAL_DISPATCH"]
    assert prev["preview"]["rendered"] == "[SIMULATED] Hi Test, ₹5,000.00 due 2026-09-06"
    res = c.post("/hitl/manual", json=m, headers=H("operator")).json()
    iid = res["results"][0]["instruction_id"]
    assert res["pending_approval"] == 1
    assert c.post("/hitl/manual", json=m, headers=H("viewer")).status_code == 403
    assert c.post("/hitl/manual", json={**m, "force": True}, headers=H("policy_owner")).status_code == 409
    mv = c.post(f"/hitl/manual/{iid}/reschedule", json={"actor": "asha", "reason": "later", "now": now,
                                                        "at": f"{D}T15:00:00"}, headers=H("operator")).json()
    new = mv["new"]["instruction_id"]
    assert c.delete(f"/hitl/manual/{new}", params={"actor": "asha", "reason": "paid", "now": now},
                    headers=H("operator")).json()["status"] == "CANCELED"
    assert c.delete(f"/hitl/manual/{new}", params={"actor": "asha", "reason": "paid"}, headers=H("operator")).status_code == 409

    cases = c.get("/hitl/cases", params={"queue": "COLLECTIONS_SUPERVISOR", "now": now}, headers=H("operator")).json()
    assert [x["ticket_id"] for x in cases] == [tid]
    hb = {"actor": "ravi", "reason": "taking it", "now": now}
    assert c.post(f"/hitl/cases/{tid}/claim", json=hb, headers=H("supervisor")).json()["status"] == "CLAIMED"
    r = c.post(f"/hitl/cases/{tid}/resolve", json={**hb, "resolution_code": "CONTACTED_RESOLVED", "lift_hold": True},
               headers=H("supervisor")).json()
    assert r["hold_lifted"] == "HANDOFF"
    assert c.post(f"/hitl/cases/{tid}/reopen", json=hb, headers=H("operator")).status_code == 403
    assert c.post(f"/hitl/cases/{tid}/reopen", json=hb, headers=H("supervisor")).json()["status"] == "OPEN"

    audit = c.get("/hitl/audit", params={"borrower_id": "B1", "human_only": True}, headers=H("viewer")).json()
    types = {a["type"] for a in audit}
    assert {"HUMAN_ACTION", "HUMAN_ACTION_REFUSED", "CASE_CLAIMED", "CASE_RESOLVED", "CASE_REOPENED"} <= types
    only = c.get("/hitl/audit", params=[("type", "SCHEDULE_CANCELED"), ("type", "SCHEDULE_RESCHEDULED")],
                 headers=H("viewer")).json()
    assert sorted(a["type"] for a in only) == ["SCHEDULE_CANCELED", "SCHEDULE_RESCHEDULED"]
    pol = c.get("/hitl/policy", headers=H("operator")).json()
    assert pol["approval_policy"]["status"].startswith("DRAFT") and "approve" not in pol["permissions"]

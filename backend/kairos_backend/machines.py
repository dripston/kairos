"""The Kairos state-machine catalog: every agentic lifecycle declared with kairos_backend.fsm.

These declarations are read off the code that enforces them (ledger, supervisor, gate, outcomes,
exceptions, and — when their branches are merged — hitl and voice). They do not drive that code;
the conformance tests in tests/fsm/ drive the real implementation with seeded random event
sequences and fail on any observed state change that is not a declared transition. STATES.md is
rendered from this module (scripts/render_states.py) and a test keeps the two in sync.

Machines (name -> builder):
    k06.touch               touches.status in the ledger (reserve -> dispatch -> provider)
    k06.touch+hitl          the same with PENDING_APPROVAL (k06/hitl-control)
    k01.gate                the K01 check order inside Gate.evaluate (+ the ledger's pre-gate)
    k06.supervisor.plan     SupervisorFSM.plan: RECEIVED -> ... -> DECIDED (SEND/WAIT/CANCEL/HANDOFF)
    k06.supervisor.dispatch SupervisorFSM.dispatch
    k09.outcome             touch_outcomes: PENDING -> REPLY / NO_REPLY / UNRESOLVED (+ late / duplicate)
    k13.exception           exceptions.handle(): one borrower event -> (action, hold, queue)
    k13.hold.ptp            borrower PTP hold: captured -> kept / broken
    k13.hold.hardship       borrower hardship hold (WAIT or HANDOFF) until a date
    k13.hold.callback       callback pending
    k13.hold.supervisor     supervisor hold (routed stop / handoff) until a person clears it
    k01.consent             per-channel contactability: STOP on any channel, re-entry per channel
    k09.intent              inbound message -> intent: rules first, at most one LLM call (the only LLM choice)
    k09.learner.arm         where an arm's estimate comes from: prior -> pooled -> segment
    k17.payment_claim       "I already paid" claim: PENDING_VERIFICATION -> VERIFIED / EXPIRED
    k06.hitl.approval       a held send's approval: PENDING -> APPROVED / BLOCKED_AT_RECHECK / ...
    k17.voice.disposition   disposition x confidence -> action (thresholds.json)
    k17.voice.call          one call-end webhook: PROCESSING -> DONE / UNMATCHED / DUPLICATE
    cm03.conversation       CM-03 inbound WhatsApp servicing: identify -> OTP -> serve / hand off / close
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .fsm import Machine, Transition

T = Transition

# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _loops(states: Iterable[str], events: Iterable[str], rule: str, action: Optional[str] = None) -> List[Transition]:
    """Self-loops: the event is accepted in that state and changes nothing (idempotent / ignored)."""
    return [T(s, e, s, rule=rule, action=action) for s in states for e in events]


GATE_SEND = "Gate.evaluate == SEND"

# =============================================================================================
# 1. Touch lifecycle (ledger.touches.status)
# =============================================================================================
TOUCH_PROVIDER_EVENTS = ("PROVIDER_ACCEPTED", "PROVIDER_DELIVERED", "PROVIDER_UNKNOWN", "PROVIDER_REJECTED")
# Ledger.record_provider_status(status): the provider word -> the machine event
PROVIDER_STATUS_EVENT = {"ACCEPTED": "PROVIDER_ACCEPTED", "SENT": "PROVIDER_ACCEPTED",
                         "DELIVERED": "PROVIDER_DELIVERED", "READ": "PROVIDER_DELIVERED",
                         "UNKNOWN": "PROVIDER_UNKNOWN", "UNCERTAIN": "PROVIDER_UNKNOWN",
                         "FAILED": "PROVIDER_REJECTED", "REJECTED": "PROVIDER_REJECTED"}


def touch_machine(hitl: bool = False) -> Machine:
    """One touch (= one reserved instruction). PROPOSED and REFUSED have no ledger row."""
    rows = ["RESERVED", "DISPATCHING", "UNCERTAIN", "SENT", "DELIVERED", "FAILED", "CANCELED"]
    if hitl:
        rows.insert(1, "PENDING_APPROVAL")
    queued = ["RESERVED"] + (["PENDING_APPROVAL"] if hitl else [])
    tr: List[Transition] = []
    if hitl:
        tr += [
            T("PROPOSED", "RESERVE", "RESERVED", "gate_send & !approval_required", "reserve cap slot + cost",
              "K06 Ledger.reserve: Gate in one BEGIN IMMEDIATE tx; reserve_hook adds no rule"),
            T("PROPOSED", "RESERVE", "PENDING_APPROVAL", "gate_send & approval_required", "reserve cap slot + cost",
              "K06 HITL approval_policy.json approval_rules (reserve_hook)"),
        ]
    else:
        tr.append(T("PROPOSED", "RESERVE", "RESERVED", "gate_send", "reserve cap slot + cost",
                    "K06 Ledger.reserve: Gate (K01 evaluation_order) in one BEGIN IMMEDIATE tx"))
    tr.append(T("PROPOSED", "RESERVE", "REFUSED", "!gate_send", "audit DECISION, no row",
                "K01 first failing rule (WAIT/CANCEL/HANDOFF)" + ("; HITL veto" if hitl else "")))
    tr += _loops(rows, ["RESERVE"], "K06 idempotency: same instruction_id is reserved once (ALREADY_RESERVED)")
    tr += [
        T("RESERVED", "BEGIN_DISPATCH", "DISPATCHING", "gate_send", None, "K06 send-time re-check (Ledger.begin_dispatch)"),
        T("RESERVED", "BEGIN_DISPATCH", "CANCELED", "!gate_send", "release cost",
          "K06 guardrails run twice: blocked at send time"),
    ]
    tr += _loops([s for s in rows if s != "RESERVED"], ["BEGIN_DISPATCH"],
                  "K06 dispatch once: NOT_DISPATCHABLE_<status> (Executor SKIPPED)")
    for q in queued:
        tr.append(T(q, "CANCEL_QUEUED", "CANCELED", None, "release cost",
                    "STOP (all), verified payment (that EMI), K13 holds, stop-for-day (that day), "
                    "outbound pause, campaign PAUSED/STOPPED"))
    tr += _loops([s for s in rows if s not in queued], ["CANCEL_QUEUED"],
                 "only queued work is canceled; an in-flight or sent touch is never recalled")
    if hitl:
        tr += [
            T("PENDING_APPROVAL", "APPROVE", "RESERVED", "!expired & gate_send", None,
              "HITL.approve: Gate re-run atomically (APPROVED)"),
            T("PENDING_APPROVAL", "APPROVE", "CANCELED", "!expired & !gate_send", "release cost",
              "HITL.approve: guardrail wins (BLOCKED_AT_RECHECK)"),
            T("PENDING_APPROVAL", "APPROVE", "CANCELED", "expired", "release cost", "HITL approval expiry"),
            T("PENDING_APPROVAL", "REJECT", "CANCELED", None, "release cost", "HITL.reject"),
            T("PENDING_APPROVAL", "EXPIRE", "CANCELED", None, "release cost",
              "HITL.expire_due: END_OF_CONTACT_WINDOW"),
            T("RESERVED", "HUMAN_CANCEL", "CANCELED", None, "release cost", "HITL cancel_scheduled / cancel_next / reschedule"),
            T("PENDING_APPROVAL", "HUMAN_CANCEL", "CANCELED", None, "release cost", "HITL cancel_scheduled / cancel_next / reschedule"),
        ]
    # provider callbacks. Never downgrade (E04); FAILED / CANCELED are final.
    tr += [
        T("DISPATCHING", "PROVIDER_ACCEPTED", "SENT", None, "K03 touch_sent event", "provider ACCEPTED"),
        T("DISPATCHING", "PROVIDER_DELIVERED", "DELIVERED", None, "K03 touch_sent event", "provider DELIVERED / READ (reach only)"),
        T("DISPATCHING", "PROVIDER_UNKNOWN", "UNCERTAIN", None, "keep cap slot + cost", "no provider answer: never resend blindly"),
        T("DISPATCHING", "SHUTDOWN_GRACE_EXPIRED", "UNCERTAIN", None, "keep cap slot + cost; DISPATCH_UNCERTAIN audit",
          "graceful shutdown: still in flight at the deadline (Ledger.mark_inflight_uncertain); never re-sent"),
        T("DISPATCHING", "PROVIDER_REJECTED", "FAILED", None, "release cost", "provider REJECTED / NO_ADAPTER"),
        T("UNCERTAIN", "PROVIDER_ACCEPTED", "SENT", None, "K03 touch_sent event", "resolve_uncertain: provider has it"),
        T("UNCERTAIN", "PROVIDER_DELIVERED", "DELIVERED", None, "K03 touch_sent event", "resolve_uncertain: provider delivered it"),
        T("UNCERTAIN", "PROVIDER_REJECTED", "FAILED", None, "release cost", "resolve_uncertain: NOT_FOUND"),
        T("UNCERTAIN", "PROVIDER_UNKNOWN", "UNCERTAIN", None, None, "still unknown: stays UNCERTAIN"),
        T("SENT", "PROVIDER_DELIVERED", "DELIVERED", None, None, "provider DELIVERED / READ (reach only)"),
    ]
    tr += _loops(["SENT"], ["PROVIDER_ACCEPTED", "PROVIDER_UNKNOWN", "PROVIDER_REJECTED"],
                 "E04 never downgrade: a late / lower status is ignored")
    tr += _loops(["DELIVERED", "FAILED", "CANCELED"], TOUCH_PROVIDER_EVENTS,
                 "final: provider callbacks change nothing")
    states = ["PROPOSED", "REFUSED"] + rows
    terminal = ["REFUSED", "DELIVERED", "FAILED", "CANCELED"]
    name = "k06.touch+hitl" if hitl else "k06.touch"
    return Machine(
        name, "1", states, "PROPOSED", terminal, tr,
        description=("One touch = one reserved instruction for one EMI. Reserved touches count toward caps and "
                     "budget immediately; the Gate runs at approval (reserve) and again at send time; a touch is "
                     "dispatched at most once; an uncertain send is resolved by asking the provider, never resent."
                     + (" With HITL, a send matching an approval rule waits in PENDING_APPROVAL (holding its cap "
                        "slot and budget) until a person approves (Gate re-run), rejects, or it expires." if hitl else "")),
        enforced_by=(["kairos_backend/ledger.py: Ledger.reserve / begin_dispatch / record_provider_status / _cancel_queued",
                      "kairos_backend/executor.py: Executor.dispatch / resolve_uncertain"]
                     + (["kairos_backend/hitl.py (k06/hitl-control): HITL._on_reserve / approve / reject / expire_due / cancel_scheduled"]
                        if hitl else [])
                     + ["kairos_backend/fsm_strict.py: KAIROS_FSM_STRICT=1 SQLite trigger rejects undeclared status changes"]),
        proven_by=(["tests/fsm/test_conformance_touch.py (seeded random event sequences)",
                    "tests/test_ledger.py (dispatch once, uncertain, STOP / payment scope)"]
                   + (["tests/fsm/test_conformance_hitl.py (runs when kairos_backend.hitl is present)"] if hitl else [])),
        state_notes={"PROPOSED": "no row yet", "REFUSED": "gate said no, no row"},
        source="code")


# =============================================================================================
# 2. K01 Gate check order (+ the ledger's pre-gate CONTROL stop)
# =============================================================================================
# decisions each rule can return (gate.py). Every other rule returns WAIT.
RULE_DECISIONS = {"STOP_OPT_OUT": ("CANCEL",), "OBLIGATION_CLEARED": ("CANCEL",), "HARDSHIP_HOLD": ("WAIT", "HANDOFF")}
DECISIONS = ("SEND", "WAIT", "CANCEL", "HANDOFF")


def gate_machine(policy) -> Machine:
    from . import gate
    order = gate.rule_order(policy)
    k01 = list(policy.evaluation_order)
    tr: List[Transition] = [
        T("PRE_GATE", "HIT", "WAIT", None, None,
          "Ledger._pre_gate CONTROL: OUTBOUND_PAUSED / CAMPAIGN_<status> / OUTSIDE_CAMPAIGN_PERIOD / NOT_ENROLLED"),
        T("PRE_GATE", "PASS", order[0], None, None, "operational stops clear"),
    ]
    for i, rid in enumerate(order):
        nxt = order[i + 1] if i + 1 < len(order) else "SEND"
        ref = (f"K01 evaluation_order[{k01.index(rid)}]" if rid in k01
               else "K13 hold placed by gate.EXTRA_RULES_AFTER (pending Product placement)")
        decs = RULE_DECISIONS.get(rid, ("WAIT",))
        if rid == "HARDSHIP_HOLD":
            tr += [T(rid, "HIT", "WAIT", "!hardship_supervisor", None, ref + "; K01 holds.HARDSHIP"),
                   T(rid, "HIT", "HANDOFF", "hardship_supervisor", None,
                     ref + "; K01 holds.HARDSHIP date beyond 4 days -> supervisor")]
        else:
            tr.append(T(rid, "HIT", decs[0], None, None, ref))
        tr.append(T(rid, "PASS", nxt, None, None, "rule passes" + ("; all passed -> ELIGIBLE" if nxt == "SEND" else "")))
    states = ["PRE_GATE"] + order + list(DECISIONS)
    return Machine(
        "k01.gate", policy.version, states, "PRE_GATE", DECISIONS, tr,
        description=("May this touch be sent at this moment? The rules run in policy.json evaluation_order and the "
                     "first hit wins; every decision carries the rule and policy_version. The Gate is pure: all "
                     "state arrives as Facts computed atomically by the ledger."),
        enforced_by=["kairos_backend/gate.py: evaluate() / rule_order()", "kairos_backend/ledger.py: Ledger._pre_gate"],
        proven_by=["tests/fsm/test_conformance_gate.py (seeded random Facts)", "tests/test_gate_k01.py (Ojas's K01 cases)"],
        source="policy+code")


# =============================================================================================
# 3. SupervisorFSM.plan / .dispatch
# =============================================================================================
BORROWER_CANCEL_RULES = ("STOP_OPT_OUT", "OBLIGATION_CLEARED")
BORROWER_WAIT_RULES = ("HARDSHIP_HOLD", "CALLBACK_PENDING", "SUPERVISOR_HOLD", "PTP_HOLD")
RANK_WAIT_RULES = ("DPD_VALID", "LEARNER")


def supervisor_plan_machine() -> Machine:
    def rule_in(*rules: str) -> Callable:
        return lambda ctx: ctx.get("rule") in rules
    guards = {
        "borrower_cancel_rule": rule_in(*BORROWER_CANCEL_RULES),
        "borrower_wait_rule": rule_in(*BORROWER_WAIT_RULES),
        "hardship_supervisor_rule": rule_in("HARDSHIP_HOLD"),
        "rank_wait_rule": rule_in(*RANK_WAIT_RULES),
    }
    S = "SupervisorFSM.plan"
    tr = [
        T("NEW", "PLAN_REQUESTED", "RECEIVED", None, None, S),
        T("RECEIVED", "CHECK_BORROWER", "BORROWER_CHECK", None, None, f"{S}: facts from the ledger (K03 spine)"),
        T("BORROWER_CHECK", "BORROWER_CLEAR", "ELIGIBILITY", None, None, "no borrower-level rule hits"),
        T("BORROWER_CHECK", "CANCEL", "DECIDED", "borrower_cancel_rule", "CRM supervisor.decision",
          "STOP_OPT_OUT | OBLIGATION_CLEARED (policy order)"),
        T("BORROWER_CHECK", "WAIT", "DECIDED", "borrower_wait_rule", "CRM supervisor.decision",
          "HARDSHIP_HOLD | CALLBACK_PENDING | SUPERVISOR_HOLD | PTP_HOLD"),
        T("BORROWER_CHECK", "HANDOFF", "DECIDED", "hardship_supervisor_rule", "CRM supervisor.decision",
          "HARDSHIP_HOLD with supervisor flag"),
        T("ELIGIBILITY", "ELIGIBLE", "RANK", None, None, "Ledger.preview: >= 1 candidate passes the full Gate"),
        T("ELIGIBILITY", "WAIT", "DECIDED", None, "CRM supervisor.decision",
          "no eligible candidate: earliest rule in policy order (or NO_CANDIDATES / IDEMPOTENCY / SLOT_MISMATCH)"),
        T("ELIGIBILITY", "CANCEL", "DECIDED", None, "CRM supervisor.decision",
          "no eligible candidate; earliest rule is a CANCEL (e.g. STOP on the other channels after a per-channel opt-in)"),
        T("RANK", "RANKED", "RESERVE", None, None, "learner ranks ONLY eligible candidates (replies per rupee)"),
        T("RANK", "WAIT", "DECIDED", "rank_wait_rule", "CRM supervisor.decision",
          "NO_BAND (DPD_VALID) | LEARNER_WAIT | LEARNER_CHOICE_NOT_ELIGIBLE (LEARNER)"),
        T("RESERVE", "SEND", "DECIDED", None, "touch RESERVED; CRM supervisor.decision", "Ledger.reserve (atomic)"),
        T("RESERVE", "WAIT", "DECIDED", None, "CRM supervisor.decision", "reserve lost a race (re-check in the write tx)"),
        T("RESERVE", "CANCEL", "DECIDED", None, "CRM supervisor.decision", "reserve lost a race (re-check in the write tx)"),
        T("RESERVE", "HANDOFF", "DECIDED", None, "CRM supervisor.decision", "reserve lost a race (re-check in the write tx)"),
    ]
    return Machine(
        "k06.supervisor.plan", "1",
        ["NEW", "RECEIVED", "BORROWER_CHECK", "ELIGIBILITY", "RANK", "RESERVE", "DECIDED"], "NEW", ["DECIDED"], tr,
        guards=guards, exclusive=[set(guards)],
        description=("Plan one touch for one EMI. Borrower-level holds first (policy order), then the full Gate per "
                     "candidate, then the learner ranks only what is eligible, then an atomic reserve. The LLM is "
                     "not in this path; the learner is a deterministic Beta-Bernoulli posterior mean in PRODUCTION."),
        enforced_by=["kairos_backend/supervisor.py: SupervisorFSM.plan (records each Transition)"],
        proven_by=["tests/fsm/test_conformance_supervisor.py (seeded random borrowers / candidates)",
                   "tests/test_supervisor_fsm.py"],
        source="code")


def supervisor_dispatch_machine() -> Machine:
    tr = [
        T("NEW", "DISPATCH_REQUESTED", "DISPATCHING", None, None, "SupervisorFSM.dispatch"),
        T("DISPATCHING", "SENT", "DISPATCHED", None, "CRM touch.dispatched", "send-time Gate passed; provider ACCEPTED"),
        T("DISPATCHING", "FAILED", "DISPATCHED", None, "CRM touch.dispatched", "send-time Gate passed; provider REJECTED"),
        T("DISPATCHING", "UNCERTAIN", "DISPATCHED", None, "CRM touch.dispatched", "send-time Gate passed; no provider answer"),
        T("DISPATCHING", "BLOCKED", "BLOCKED", None, "CRM supervisor.decision CANCEL + touch.dispatched",
          "send-time Gate failed, or the touch was canceled before dispatch"),
        T("DISPATCHING", "SKIPPED", "SKIPPED", None, "CRM touch.dispatched", "already in flight / sent / failed: no second send"),
    ]
    return Machine(
        "k06.supervisor.dispatch", "1", ["NEW", "DISPATCHING", "DISPATCHED", "BLOCKED", "SKIPPED"], "NEW",
        ["DISPATCHED", "BLOCKED", "SKIPPED"], tr,
        description="Dispatch one reserved touch through the Executor; the Gate runs again at send time.",
        enforced_by=["kairos_backend/supervisor.py: SupervisorFSM.dispatch", "kairos_backend/executor.py: Executor.dispatch"],
        proven_by=["tests/fsm/test_conformance_supervisor.py", "tests/test_supervisor_fsm.py"], source="code")


# =============================================================================================
# 4. Outcome lifecycle (outcomes.touch_outcomes)
# =============================================================================================
OUTCOME_FINAL = ("REPLY", "NO_REPLY", "UNRESOLVED")


def outcome_machine() -> Machine:
    R = "K01 reply_attribution"
    tr = [
        T("NEW", "REGISTER", "PENDING", None, "window_end = executed_at + reply_window_hours", R + ".reply_window_hours"),
        T("PENDING", "MEANINGFUL_REPLY", "PENDING_REPLIED", "in_window", "reply_at, reached=1 (REPLY_ATTRIBUTED)",
          R + ": most recent executed touch whose window contains it"),
        T("PENDING", "MEANINGFUL_REPLY", "PENDING", "!in_window", "late_reply_at (LATE_REPLY), reward unchanged",
          R + ": outside the window never becomes a reward"),
        T("PENDING", "WINDOW_CLOSED", "UNRESOLVED", "never_sent", "kept out of learning",
          "outcomes._decide: ledger touch FAILED / CANCELED"),
        T("PENDING", "WINDOW_CLOSED", "NO_REPLY", "!never_sent & reach_known", "learner.apply_outcome(0) once",
          "outcomes._decide: provider confirmed delivered / not connected"),
        T("PENDING", "WINDOW_CLOSED", "NO_REPLY", "!never_sent & !reach_known & ledger_sent", "learner.apply_outcome(0) once",
          "outcomes._decide: ledger SENT / DELIVERED"),
        T("PENDING", "WINDOW_CLOSED", "UNRESOLVED", "!never_sent & !reach_known & !ledger_sent", "kept out of learning",
          "unresolved provider outcomes stay out of learning (UNCERTAIN, no provider info)"),
        T("PENDING_REPLIED", "WINDOW_CLOSED", "REPLY", None, "learner.apply_outcome(1) once", R + ".one_reward_per_touch"),
        T("PENDING_REPLIED", "MEANINGFUL_REPLY", "PENDING_REPLIED", None, "DUPLICATE", R + ".one_reward_per_touch"),
    ]
    tr += _loops(["PENDING", "PENDING_REPLIED"] + list(OUTCOME_FINAL), ["REGISTER"], "register_execution is idempotent")
    tr += _loops(["PENDING", "PENDING_REPLIED"] + list(OUTCOME_FINAL), ["REACH", "CLICK_OR_READ"],
                 R + ".click_effect: reachability only, never a reply")
    tr += _loops(OUTCOME_FINAL, ["MEANINGFUL_REPLY"], "late reply / duplicate: recorded, reward unchanged",
                 "LATE_REPLY / DUPLICATE")
    tr += _loops(OUTCOME_FINAL, ["WINDOW_CLOSED"], "finalize_due is idempotent")
    return Machine(
        "k09.outcome", "1", ["NEW", "PENDING", "PENDING_REPLIED"] + list(OUTCOME_FINAL), "NEW", OUTCOME_FINAL, tr,
        description=("One FINAL learning outcome per executed touch, decided when its 24 h reply window closes. "
                     "Delivery / read / click are reach, never a reply; a verified payment is tracked separately "
                     "and never a reward; duplicates and late replies never change the reward; UNRESOLVED never "
                     "reaches the learner."),
        enforced_by=["kairos_backend/outcomes.py: OutcomeFinalizer.register_execution / record_reply / record_reach / finalize_due",
                     "kairos_backend/learner.py: apply_outcome idempotent on instruction_id"],
        proven_by=["tests/fsm/test_conformance_outcome.py (seeded random replies / clocks)", "tests/test_outcomes.py"],
        state_notes={"NEW": "no row", "PENDING_REPLIED": "status PENDING with reply_at"},
        source="code")


# =============================================================================================
# 5. K13 exception decision table + the holds it places
# =============================================================================================
def k13_exception_machine(policy) -> Machine:
    x = policy.exceptions
    thr = x["promise_to_pay"]["broken_ptp"]["threshold"]
    auto = x["promise_to_pay"]["auto_accept_max_days"]
    within = policy.raw["holds"]["HARDSHIP"]["borrower_date_within_days"]
    q = x["queues"]["default"]
    maxreb = x["needs_time"]["max_rebuttals_by_band"]
    guards = {
        "broken": lambda c: c["broken_ptp_count"] >= thr,
        "in_window": lambda c: bool(c["in_window"]),
        "rebuttals_left": lambda c: c["rebuttals_used"] < maxreb[c["band"]],
        "paid_now": lambda c: bool(c["paid_now"]),
        "far": lambda c: (c["date_offset_days"] or 0) > auto,
        "answered": lambda c: bool(c["callback_answered"]),
        "retry_left": lambda c: c["callback_attempts"] < 2,
        "has_date": lambda c: c["date_offset_days"] is not None,
        "beyond": lambda c: c["date_offset_days"] is not None and c["date_offset_days"] > within,
    }

    def eff(hold: str, queue: str = "") -> str:
        return f"hold={hold}; queue={queue or '-'}"
    K = "K13 exceptions.json"
    tr = [
        T("RECEIVED", "NEEDS_TIME", "PUSH_PAYMENT_NOW", "broken", eff("NONE"), f"{K} broken_ptp >= {thr}"),
        T("RECEIVED", "NEEDS_TIME", "ACK_ONLY", "!broken & !in_window", eff("STOP_FOR_DAY"),
          f"{K} needs_time.rebuttals_only_inside_contact_window"),
        T("RECEIVED", "NEEDS_TIME", "REBUTTAL", "!broken & in_window & rebuttals_left", eff("NONE"),
          f"{K} needs_time.max_rebuttals_by_band"),
        T("RECEIVED", "NEEDS_TIME", "ACCEPT_NO_DATE", "!broken & in_window & !rebuttals_left", eff("STOP_FOR_DAY"),
          f"{K} needs_time.no_date_after_rebuttals"),
        T("RECEIVED", "PTP_OFFERED", "VERIFY_PAYMENT", "broken & paid_now", eff("NONE"), f"{K} broken_ptp.if_paid_now"),
        T("RECEIVED", "PTP_OFFERED", "RECORD_PTP_NO_HOLD", "broken & !paid_now", eff("STOP_FOR_DAY"),
          f"{K} broken_ptp.if_not_paid_now"),
        T("RECEIVED", "PTP_OFFERED", "CAPTURE_PTP", "!broken & !far", eff("PTP_HOLD"),
          f"{K} promise_to_pay.auto_accept_max_days={auto}"),
        T("RECEIVED", "PTP_OFFERED", "CAPTURE_PTP_SUPERVISOR", "!broken & far", eff("PTP_HOLD", q),
          f"{K} promise_to_pay.beyond_auto_accept"),
        T("RECEIVED", "CALLBACK_REQUEST", "CREATE_CALLBACK_TASK", None, eff("CALLBACK_PENDING", q), f"{K} callback (E08)"),
        T("RECEIVED", "CALLBACK_DUE", "CLOSE_CALLBACK_TASK", "answered", eff("STOP_FOR_DAY"), f"{K} callback.if_answered"),
        T("RECEIVED", "CALLBACK_DUE", "RETRY_CALLBACK_2H", "!answered & retry_left", eff("CALLBACK_PENDING", q),
          f"{K} callback.if_unanswered"),
        T("RECEIVED", "CALLBACK_DUE", "SEND_MISSED_CALLBACK_MSG", "!answered & !retry_left", eff("STOP_FOR_DAY"),
          f"{K} callback.if_second_attempt_missed"),
        T("RECEIVED", "HARDSHIP", "HOLD_2_DAYS", "!has_date", eff("HARDSHIP_HOLD"), "K01 holds.HARDSHIP.if_no_date"),
        T("RECEIVED", "HARDSHIP", "HOLD_UNTIL_DATE", "has_date & !beyond", eff("HARDSHIP_HOLD"),
          f"K01 holds.HARDSHIP within {within} days"),
        T("RECEIVED", "HARDSHIP", "HOLD_UNTIL_DATE_SUPERVISOR", "has_date & beyond", eff("HARDSHIP_HOLD", q),
          f"K01 holds.HARDSHIP beyond {within} days -> supervisor"),
    ]
    for ev in ("DISPUTE", "COMPLAINT", "WAIVER_REQUEST"):
        tr.append(T("RECEIVED", ev, "CREATE_TICKET", None, eff("STOP_FOR_DAY", q), f"{K} routed_exceptions (outreach continues)"))
    for ev in ("ABUSIVE", "LEGAL_THREAT", "FRAUD_CLAIM"):
        tr.append(T("RECEIVED", ev, "END_CONVERSATION_AND_TICKET", None, eff("SUPERVISOR_HOLD", q),
                    f"{K} routed_exceptions (paused until supervisor clears)"))
    outs = list(OrderedDict.fromkeys(t.dst for t in tr))
    return Machine(
        "k13.exception", x.get("policy_version", "?"), ["RECEIVED"] + outs, "RECEIVED", outs, tr, guards=guards,
        description=("One borrower event (classified upstream — by rules, DTMF, a voice disposition, or the LLM "
                     "for ambiguous free text) -> one (action, hold, queue) from exceptions.json. The table is pure; "
                     "the ledger applies the hold. Note: the *_SUPERVISOR states are the same K13 action "
                     "(CAPTURE_PTP / HOLD_UNTIL_DATE) with a supervisor ticket."),
        enforced_by=["kairos_backend/exceptions.py: handle()", "kairos_backend/ledger.py: Ledger.apply_exception"],
        proven_by=["tests/fsm/test_conformance_k13.py (every event x seeded random facts)", "tests/test_exceptions_k13.py"],
        source="policy+code")


def ptp_hold_machine() -> Machine:
    R = "K01 holds.PROMISE_TO_PAY"
    tr = []
    for s in ("NO_PTP", "PTP_KEPT", "PTP_BROKEN", "PTP_HOLD"):
        tr.append(T(s, "PTP_OFFERED", "PTP_HOLD", "!broken_threshold", "ptp_date, ptp_obligation; stop-for-day today; cancel queued",
                    R + " (K13 CAPTURE_PTP" + ("; replaces the open promise)" if s == "PTP_HOLD" else ")")))
        tr.append(T(s, "PTP_OFFERED", s, "broken_threshold", "stop-for-day only",
                    "K13 broken_ptp >= threshold: RECORD_PTP_NO_HOLD"))
    tr += [
        T("PTP_HOLD", "PAYMENT_VERIFIED", "PTP_KEPT", "clears_promised_emi", "cancel that EMI's queued touches", R + ".if_paid"),
        T("PTP_HOLD", "PAYMENT_VERIFIED", "PTP_HOLD", "!clears_promised_emi", None,
          "another EMI, or partial: hold stays (E14)"),
        T("PTP_HOLD", "DAY_STARTED", "PTP_KEPT", "promise_date_passed & promised_emi_paid", None, R + ".on_ptp_date"),
        T("PTP_HOLD", "DAY_STARTED", "PTP_BROKEN", "promise_date_passed & !promised_emi_paid", "broken_ptp += 1",
          R + ".if_unpaid_by_end_of_ptp_date"),
        T("PTP_HOLD", "DAY_STARTED", "PTP_HOLD", "!promise_date_passed", None, "hold runs through the PTP date (inclusive)"),
    ]
    tr += _loops(["NO_PTP", "PTP_KEPT", "PTP_BROKEN"], ["PAYMENT_VERIFIED", "DAY_STARTED"], "no open promise")
    return Machine(
        "k13.hold.ptp", "1", ["NO_PTP", "PTP_HOLD", "PTP_KEPT", "PTP_BROKEN"], "NO_PTP", [], tr,
        description=("A borrower-wide promise-to-pay hold: no outbound activity on any channel through the PTP date. "
                     "Kept = the promised EMI is verified paid (D7/LMS); broken = unpaid when the next day starts "
                     "(BROKEN_PTP counted; at the threshold no new hold is granted). KEPT / BROKEN are the outcome of "
                     "the last promise; a new promise starts a new hold."),
        enforced_by=["kairos_backend/ledger.py: apply_exception (PTP_HOLD), record_payment_verified, start_day",
                     "kairos_backend/gate.py: PTP_HOLD rule"],
        proven_by=["tests/fsm/test_conformance_holds.py", "tests/test_ledger.py"],
        state_notes={"PTP_KEPT": "ptp cleared, paid", "PTP_BROKEN": "ptp cleared, broken_ptp+1"},
        source="code")


def hardship_hold_machine() -> Machine:
    R = "K01 holds.HARDSHIP"
    tr = []
    for s in ("NONE", "HARDSHIP_HOLD", "HARDSHIP_HANDOFF"):
        tr.append(T(s, "HARDSHIP", "HARDSHIP_HANDOFF", "date_beyond", "hardship_until=date, supervisor=1, ticket",
                    R + ".if_borrower_gives_date_beyond_4_days"))
        tr.append(T(s, "HARDSHIP", "HARDSHIP_HOLD", "!date_beyond", "hardship_until=date or +2 days, supervisor=0",
                    R + ".if_no_date / within 4 days"))
    tr += [
        T("HARDSHIP_HOLD", "CLOCK", "NONE", "until_passed", None, "hold ends at hardship_until (Gate compares at send time)"),
        T("HARDSHIP_HOLD", "CLOCK", "HARDSHIP_HOLD", "!until_passed", None, "still held"),
        T("HARDSHIP_HANDOFF", "CLOCK", "NONE", "until_passed", None, "hold ends at hardship_until"),
        T("HARDSHIP_HANDOFF", "CLOCK", "HARDSHIP_HANDOFF", "!until_passed", None, "still held (HANDOFF)"),
        T("HARDSHIP_HANDOFF", "CLEAR_SUPERVISOR", "HARDSHIP_HOLD", None, "hardship_supervisor=0",
          "Ledger.clear_supervisor_hold (a person): the date hold itself stays"),
        T("NONE", "CLOCK", "NONE", None, None, "nothing held"),
    ]
    tr += _loops(["NONE", "HARDSHIP_HOLD"], ["CLEAR_SUPERVISOR"], "no supervisor flag on this hold")
    return Machine(
        "k13.hold.hardship", "1", ["NONE", "HARDSHIP_HOLD", "HARDSHIP_HANDOFF"], "NONE", [], tr,
        description="Hardship pauses every channel (Gate WAIT, or HANDOFF when the borrower's date is beyond 4 days).",
        enforced_by=["kairos_backend/ledger.py: apply_exception (HARDSHIP_HOLD), clear_supervisor_hold",
                     "kairos_backend/gate.py: HARDSHIP_HOLD rule"],
        proven_by=["tests/fsm/test_conformance_holds.py"], source="code")


def callback_hold_machine() -> Machine:
    R = "K13 callback (E08)"
    tr = []
    for s in ("NONE", "CALLBACK_PENDING"):
        tr += [
            T(s, "CALLBACK_REQUEST", "CALLBACK_PENDING", None, "ticket (deduped per day), pause all outbound", R + ".while_pending"),
            T(s, "CALLBACK_DUE", "NONE", "answered", "close task; stop-for-day", R + ".if_answered"),
            T(s, "CALLBACK_DUE", "CALLBACK_PENDING", "!answered & retry_left", "retry in 2 h", R + ".if_unanswered"),
            T(s, "CALLBACK_DUE", "NONE", "!answered & !retry_left", "one 'we tried to reach you' message; stop-for-day",
              R + ".if_second_attempt_missed"),
        ]
    return Machine(
        "k13.hold.callback", "1", ["NONE", "CALLBACK_PENDING"], "NONE", [], tr,
        description="A borrower asked for a person: all outbound nudges pause until the callback is answered or missed twice.",
        enforced_by=["kairos_backend/ledger.py: apply_exception (CALLBACK_PENDING)", "kairos_backend/gate.py: CALLBACK_PENDING rule"],
        proven_by=["tests/fsm/test_conformance_holds.py"], source="code")


def supervisor_hold_machine() -> Machine:
    tr = []
    for s in ("NONE", "SUPERVISOR_HOLD"):
        tr.append(T(s, "ROUTED_STOP", "SUPERVISOR_HOLD", None, "ticket; cancel queued",
                    "K13 routed_exceptions ABUSIVE / LEGAL_THREAT / FRAUD_CLAIM"))
        tr.append(T(s, "HANDOFF", "SUPERVISOR_HOLD", None, "ticket; cancel queued",
                    "voice LedgerHandoff / HITL pause, hold, handoff (k17-k18, k06/hitl-control)"))
        tr.append(T(s, "CLEAR_SUPERVISOR", "NONE", None, "supervisor_hold=0", "Ledger.clear_supervisor_hold (a person)"))
    return Machine(
        "k13.hold.supervisor", "1", ["NONE", "SUPERVISOR_HOLD"], "NONE", [], tr,
        description="Paused on every channel until a person clears it. Kairos never lifts it on its own.",
        enforced_by=["kairos_backend/ledger.py: apply_exception (SUPERVISOR_HOLD), clear_supervisor_hold",
                     "kairos_backend/gate.py: SUPERVISOR_HOLD rule"],
        proven_by=["tests/fsm/test_conformance_holds.py"], source="code")


def consent_machine() -> Machine:
    R = "K01 holds.STOP_OPT_OUT"
    tr = []
    for s in ("CONTACTABLE", "NOT_CONSENTED", "OPTED_OUT"):
        tr.append(T(s, "STOP", "OPTED_OUT", None, "cancel ALL queued sends; K03 consent_changed",
                    R + ": STOP on any channel suppresses all channels"))
        tr.append(T(s, "OPT_IN_THIS_CHANNEL", "CONTACTABLE", None, "consent version recorded", R + ".re_entry (per channel)"))
        tr.append(T(s, "VOICE_CONSENT_WITHDRAWN", "NOT_CONSENTED" if s != "OPTED_OUT" else "OPTED_OUT", None,
                    "consented=0 on voice channels", "K17 WRONG_PERSON / THIRD_PARTY (k17-k18, voice channels only)"))
    tr += [
        T("OPTED_OUT", "OPT_IN_OTHER_CHANNEL", "OPTED_OUT", "has_consent_row", None,
          R + ".re_entry: other channels stay opted out"),
        T("OPTED_OUT", "OPT_IN_OTHER_CHANNEL", "NOT_CONSENTED", "!has_consent_row", None,
          "no consent row for this channel: borrower stop flag lifted, still no consent (WAIT NO_CONSENT)"),
        T("CONTACTABLE", "OPT_IN_OTHER_CHANNEL", "CONTACTABLE", None, None, "other channel only"),
        T("NOT_CONSENTED", "OPT_IN_OTHER_CHANNEL", "NOT_CONSENTED", None, None, "other channel only"),
    ]
    return Machine(
        "k01.consent", "1", ["CONTACTABLE", "NOT_CONSENTED", "OPTED_OUT"], "CONTACTABLE", [], tr,
        description=("Contactability of one borrower on one channel, as the Gate sees it (STOP_OPT_OUT -> CANCEL, "
                     "CONSENT -> WAIT). The initial state is whatever the K03 consents table says; CONTACTABLE is "
                     "drawn as the start."),
        enforced_by=["kairos_backend/ledger.py: record_stop / opt_in / facts_for", "kairos_backend/gate.py: STOP_OPT_OUT, CONSENT"],
        proven_by=["tests/fsm/test_conformance_holds.py"], source="code")


# =============================================================================================
# 5b. Borrower intent classification: the ONLY place an LLM is allowed to choose a value
# =============================================================================================
def intent_machine() -> Machine:
    I = "kairos_backend/intent.py IntentClassifier.classify"
    tr = [
        T("STRUCTURED", "HIT", "RULES", None, "DTMF map / WhatsApp button map", f"{I}: structured input never reaches the LLM"),
        T("STRUCTURED", "PASS", "EMPTY", None, None, "no DTMF digits, no button payload"),
        T("EMPTY", "HIT", "RULES", None, "OTHER (ambiguous)", "empty text"),
        T("EMPTY", "PASS", "STOP_WORD", None, None, "has text"),
        T("STOP_WORD", "HIT", "RULES", None, "STOP", "K01 holds.STOP_OPT_OUT keywords on any channel: never the LLM"),
        T("STOP_WORD", "PASS", "TEXT_RULES", None, None, "not an opt-out"),
        T("TEXT_RULES", "HIT", "RULES", None, "one rule intent / bare date -> PTP / dated conflict -> PRECEDENCE",
          "English + Hinglish keyword rules and date extraction"),
        T("TEXT_RULES", "PASS", "ASK_LLM", None, "redact + truncate (500 chars)", "no rule matched, or rules conflict without a date"),
        T("ASK_LLM", "LLM_VALID", "LLM", None, "intent from the closed enum; summary <= 160 chars",
          "gpt-6-luna, reasoning effort none, no tools, strict JSON schema, store=false"),
        T("ASK_LLM", "LLM_INVALID", "FALLBACK", None, "OTHER, ambiguous, needs review", "schema / refusal / incomplete"),
        T("ASK_LLM", "LLM_ERROR", "FALLBACK", None, "OTHER, ambiguous, needs review", "transport error never reaches the caller"),
        T("ASK_LLM", "NO_LLM", "FALLBACK", None, "OTHER, ambiguous, needs review", "no LLM configured"),
    ]
    return Machine(
        "k09.intent", "1", ["STRUCTURED", "EMPTY", "STOP_WORD", "TEXT_RULES", "ASK_LLM", "RULES", "LLM", "FALLBACK"],
        "STRUCTURED", ["RULES", "LLM", "FALLBACK"], tr,
        description=("Classify one inbound borrower message into a closed intent enum. Deterministic rules first; at "
                     "most ONE LLM call, only for free text the rules cannot decide. The LLM's answer is a *value* "
                     "(an event for k13.exception / STOP / payment verification), never a transition: what the intent "
                     "does is decided by the K13 table, the Gate and the ledger."),
        enforced_by=["kairos_backend/intent.py: IntentClassifier.classify / LunaClient / validate_llm_output"],
        proven_by=["tests/fsm/test_conformance_intent.py (seeded random messages, spy LLM)", "tests/test_intent.py"],
        llm="classify", source="code")


# =============================================================================================
# 6. K09 learner arm (data lifecycle)
# =============================================================================================
def learner_arm_machine() -> Machine:
    tr = [
        T("PRIOR", "OUTCOME_THIS_BAND", "SEGMENT", "segment_full", "alpha/beta += reward", "min_segment_obs reached"),
        T("PRIOR", "OUTCOME_THIS_BAND", "POOLED", "!segment_full", "alpha/beta += reward",
          "sparse segment: pooled channel x slot posterior"),
        T("PRIOR", "OUTCOME_OTHER_BAND", "POOLED", None, None, "pooled evidence for this channel x slot"),
        T("POOLED", "OUTCOME_THIS_BAND", "SEGMENT", "segment_full", "alpha/beta += reward", "min_segment_obs reached"),
        T("POOLED", "OUTCOME_THIS_BAND", "POOLED", "!segment_full", "alpha/beta += reward", "still sparse"),
        T("POOLED", "OUTCOME_OTHER_BAND", "POOLED", None, None, "pooled evidence grows"),
        T("SEGMENT", "OUTCOME_THIS_BAND", "SEGMENT", None, "alpha/beta += reward", "segment posterior"),
        T("SEGMENT", "OUTCOME_OTHER_BAND", "SEGMENT", None, None, "segment posterior unaffected"),
    ]
    tr += _loops(["PRIOR", "POOLED", "SEGMENT"], ["DUPLICATE_OUTCOME", "NOT_LEARNABLE"],
                 "one update per instruction_id; PENDING / UNRESOLVED never reach the learner")
    return Machine(
        "k09.learner.arm", "1", ["PRIOR", "POOLED", "SEGMENT"], "PRIOR", ["SEGMENT"], tr,
        description=("Where one arm's (band x channel x slot) estimate comes from. Only FINAL REPLY / NO_REPLY "
                     "outcomes move it, once per instruction_id. PRODUCTION ranks by the posterior mean (no randomness); "
                     "seeded Thompson draws only in SANDBOX / SIMULATOR. On k09/optimize (not merged here) evidence "
                     "decays with age, so a SEGMENT arm can fall back to POOLED when its weighted count drops below "
                     "min_segment_obs: that DECAY transition is not declared until that branch lands."),
        enforced_by=["kairos_backend/learner.py: BetaBernoulliLearner.apply_outcome / _posteriors / rank"],
        proven_by=["tests/fsm/test_conformance_learner.py", "tests/test_learner.py"], source="code")


# =============================================================================================
# 7. Branch-only machines (declared from origin/k17-k18/voice-engine and origin/k06/hitl-control)
# =============================================================================================
def payment_claim_machine() -> Machine:
    tr = [
        T("NONE", "CLAIM", "PENDING_VERIFICATION", None,
          "stop_for_day rows for every day the 48 h pause touches; cancel those days' queued touches; NEVER mark paid",
          "K17 thresholds.json payment_claim (ALREADY_PAID >= accept_at)"),
        T("PENDING_VERIFICATION", "RESOLVE", "VERIFIED", "emi_paid", None,
          "the ledger (D7/LMS via Ledger.record_payment_verified) shows the EMI fully paid"),
        T("PENDING_VERIFICATION", "RESOLVE", "EXPIRED", "!emi_paid & pause_over", None,
          "no verification within pause_hours: reminders resume on their own"),
        T("PENDING_VERIFICATION", "RESOLVE", "PENDING_VERIFICATION", "!emi_paid & !pause_over", None, "still waiting"),
    ]
    tr += _loops(["VERIFIED", "EXPIRED"], ["RESOLVE"], "final: resolve_payment_claims only reads PENDING_VERIFICATION")
    return Machine(
        "k17.payment_claim", "0.1.0-draft", ["NONE", "PENDING_VERIFICATION", "VERIFIED", "EXPIRED"], "NONE",
        ["VERIFIED", "EXPIRED"], tr,
        description=("A borrower says they already paid. A claim is never a payment: reminders pause for 48 h while "
                     "the ledger waits for D7/LMS; only Ledger.record_payment_verified clears an EMI. There is no "
                     "REJECTED state in the code: a fake claim simply EXPIRES (see findings)."),
        enforced_by=["kairos_backend/voice/ingest.py (k17-k18/voice-engine): VoiceIngester._payment_claim / resolve_payment_claims"],
        proven_by=["tests/fsm/test_conformance_voice.py (runs when kairos_backend.voice is present)"],
        source="branch:k17-k18/voice-engine")


def hitl_approval_machine() -> Machine:
    H = "K06 HITL approval_policy.json"
    tr = [
        T("NEW", "RESERVE", "PENDING", "gate_send & approval_required", "touch PENDING_APPROVAL (holds cap slot + budget)",
          f"{H} approval_rules (VOICE_CHANNEL, STANDARD_INTENSITY_HIGH_DPD, FIRST_SEND_AFTER_HARDSHIP, MANUAL_DISPATCH)"),
        T("PENDING", "APPROVE", "PENDING", "maker_is_checker", "HUMAN_ACTION_REFUSED audited", f"{H} approval.maker_checker"),
        T("PENDING", "APPROVE", "EXPIRED", "!maker_is_checker & expired", "touch CANCELED, budget released",
          f"{H} approval.expiry END_OF_CONTACT_WINDOW"),
        T("PENDING", "APPROVE", "APPROVED", "!maker_is_checker & !expired & gate_send", "touch RESERVED",
          "Gate re-run atomically at approval; Executor re-checks again at send time"),
        T("PENDING", "APPROVE", "BLOCKED_AT_RECHECK", "!maker_is_checker & !expired & !gate_send",
          "touch CANCELED, budget released", "the guardrail wins, the approval cannot"),
        T("PENDING", "REJECT", "REJECTED", None, "touch CANCELED, budget released", "HITL.reject"),
        T("PENDING", "EXPIRE_DUE", "EXPIRED", "expired", "touch CANCELED, budget released", "HITL.expire_due / tick"),
        T("PENDING", "EXPIRE_DUE", "PENDING", "!expired", None, "not yet"),
        T("PENDING", "HUMAN_CANCEL", "CANCELED", None, "touch CANCELED, budget released", "HITL.cancel_scheduled / cancel_next"),
        T("PENDING", "RESCHEDULE", "RESCHEDULED", None, "touch CANCELED; a NEW instruction through the full Gate",
          "HITL.reschedule"),
        T("PENDING", "CANCEL_QUEUED", "WITHDRAWN", None, "touch CANCELED; approval decision left NULL",
          "STOP / holds / payment / pause cancel PENDING_APPROVAL like RESERVED"),
    ]
    tr += _loops(["APPROVED", "BLOCKED_AT_RECHECK", "REJECTED", "EXPIRED", "CANCELED", "WITHDRAWN"],
                 ["APPROVE", "REJECT"], "Conflict NOT_PENDING_<status>: nothing changes")
    return Machine(
        "k06.hitl.approval", "0.1.0",
        ["NEW", "PENDING", "APPROVED", "BLOCKED_AT_RECHECK", "REJECTED", "EXPIRED", "CANCELED", "RESCHEDULED",
         "WITHDRAWN"], "NEW",
        ["APPROVED", "BLOCKED_AT_RECHECK", "REJECTED", "EXPIRED", "CANCELED", "RESCHEDULED", "WITHDRAWN"], tr,
        description=("A send that passed every guardrail but matches an approval rule waits for a person. Approval can "
                     "only make outreach stricter: the Gate runs again at approval and at send time. RESCHEDULED is "
                     "final here; the code can restore it when the new slot is refused (see findings)."),
        enforced_by=["kairos_backend/hitl.py (k06/hitl-control): HITL._on_reserve / approve / reject / expire_due / cancel_scheduled / reschedule"],
        proven_by=["tests/fsm/test_conformance_hitl.py (runs when kairos_backend.hitl is present)"],
        state_notes={"WITHDRAWN": "touch canceled by a system event"},
        source="branch:k06/hitl-control")


VOICE_COVERED = ("STOP_REQUESTED", "HARDSHIP", "ABUSIVE", "DISPUTE", "WRONG_PERSON", "THIRD_PARTY", "PTP",
                 "PARTIAL_PTP", "ALREADY_PAID", "CALLBACK_REQUESTED", "NO_RESPONSE")


def load_voice_thresholds(path: Optional[Path] = None) -> Optional[dict]:
    from .policy import policy_dir
    p = Path(path) if path else policy_dir() / "K17-voice-dispositions" / "thresholds.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def voice_disposition_machine(thresholds: dict) -> Machine:
    """decide() in voice/ingest.py as a machine: event = the bot's disposition (an LLM *classification*
    inside the call), guards = confidence thresholds and date / time checks done by Kairos."""
    rules = thresholds["rules"]
    min_any = float(thresholds["min_confidence_any"])
    V = f"K17 thresholds.json v{thresholds.get('policy_version', '?')}"
    guards = {"accepted": lambda c: c["confidence"] >= float(rules[c["disposition"]]["accept_at"]),
              "below_min_any": lambda c: c["confidence"] < min_any,
              "has_date": lambda c: bool(c.get("ptp_date")),
              "date_in_past": lambda c: bool(c.get("date_in_past")),
              "beyond_max_days": lambda c: bool(c.get("beyond_max_days")),
              "has_time": lambda c: bool(c.get("callback_at")),
              "time_in_past": lambda c: bool(c.get("time_in_past")),
              "in_contact_window": lambda c: bool(c.get("in_contact_window"))}
    tr: List[Transition] = [T("RECEIVED", "UNCLEAR", "REVIEW", None, "human review ticket", f"{V}: unknown disposition")]
    for disp, r in rules.items():
        acc, below = r["accept"], r["below"]
        ref = f"{V} rules.{disp} accept_at={r['accept_at']}"
        if disp == "NO_RESPONSE":
            tr.append(T("RECEIVED", disp, "NONE", None, None, ref))
            continue
        if disp in ("PTP", "PARTIAL_PTP"):
            tr += [
                T("RECEIVED", disp, below, "!accepted", "treated as NEEDS_TIME_NO_DATE", ref + " (LOW_CONFIDENCE)"),
                T("RECEIVED", disp, below, "accepted & !has_date", "treated as NEEDS_TIME_NO_DATE", ref + " (NO_DATE)"),
                T("RECEIVED", disp, below, "accepted & has_date & date_in_past", "treated as NEEDS_TIME_NO_DATE",
                  ref + " (DATE_IN_PAST)"),
                T("RECEIVED", disp, below, "accepted & has_date & !date_in_past & beyond_max_days",
                  "treated as NEEDS_TIME_NO_DATE", f"{V} ptp.max_ptp_days_by_band / by_use_case (BEYOND_MAX_DAYS)"),
                T("RECEIVED", disp, acc, "accepted & has_date & !date_in_past & !beyond_max_days",
                  "K13 PTP_OFFERED -> PTP hold + PTP_FOLLOW_UP", ref),
            ]
            continue
        if disp == "CALLBACK_REQUESTED":
            tr += [
                T("RECEIVED", disp, below, "!accepted", "human review ticket", ref),
                T("RECEIVED", disp, "REVIEW", "accepted & !has_time", "human review ticket", f"{V} (CALLBACK_WITHOUT_TIME)"),
                T("RECEIVED", disp, "REVIEW", "accepted & has_time & time_in_past", "human review ticket", f"{V} callback.must_be_in_future"),
                T("RECEIVED", disp, "REVIEW", "accepted & has_time & !time_in_past & !in_contact_window",
                  "human review ticket", f"{V} callback.must_be_inside_contact_window"),
                T("RECEIVED", disp, acc, "accepted & has_time & !time_in_past & in_contact_window",
                  "Supervisor.decide voicebot touch (full Gate)", ref),
            ]
            continue
        tr.append(T("RECEIVED", disp, acc, "accepted", None, ref))
        if disp in VOICE_COVERED:
            tr.append(T("RECEIVED", disp, below, "!accepted", None, ref + " (below)"))
        else:
            tr.append(T("RECEIVED", disp, "REVIEW", "!accepted & below_min_any", "human review ticket (UNCLEAR)",
                        f"{V} min_confidence_any={min_any}"))
            tr.append(T("RECEIVED", disp, below, "!accepted & !below_min_any", None, ref + " (below)"))
    outs = list(OrderedDict.fromkeys(t.dst for t in tr))
    return Machine(
        "k17.voice.disposition", thresholds.get("policy_version", "?"), ["RECEIVED"] + outs, "RECEIVED", outs, tr,
        guards=guards, events=["UNCLEAR"] + list(rules),
        description=("The Sarvam bot (an LLM) only *classifies* the call into one of 16 dispositions with a "
                     "confidence; Kairos — not the bot — decides what it does. Safety first: a low-confidence STOP / "
                     "hardship / abuse still goes to a person; a low-confidence PTP / paid / callback never creates a "
                     "hold or a payment."),
        enforced_by=["kairos_backend/voice/ingest.py (k17-k18/voice-engine): decide() + VoiceIngester._apply"],
        proven_by=["tests/fsm/test_conformance_voice.py (runs when kairos_backend.voice is present)"],
        source="branch:k17-k18/voice-engine")


def voice_call_machine() -> Machine:
    tr = [
        T("NEW", "CALL_END", "PROCESSING", "borrower_known", "insert voice_calls row", "idempotent by call_id"),
        T("NEW", "CALL_END", "UNMATCHED", "!borrower_known", "insert voice_calls row", "no borrower for touch_id"),
        T("PROCESSING", "PROCESSED", "DONE", None, "ledger effects + CRM voice.call_disposition", "VoiceIngester._process"),
        T("PROCESSING", "ERROR", "NEW", None, "row deleted so the same webhook can be retried", "VoiceIngester.ingest"),
    ]
    tr += _loops(["PROCESSING", "DONE", "UNMATCHED"], ["CALL_END"], "duplicate webhook: DUPLICATE, nothing changes")
    return Machine(
        "k17.voice.call", "1", ["NEW", "PROCESSING", "DONE", "UNMATCHED"], "NEW", ["DONE", "UNMATCHED"], tr,
        description="One Sarvam call-end webhook, processed exactly once.",
        enforced_by=["kairos_backend/voice/ingest.py (k17-k18/voice-engine): VoiceIngester.ingest"],
        proven_by=["tests/fsm/test_conformance_voice.py (runs when kairos_backend.voice is present)"],
        source="branch:k17-k18/voice-engine")


CM03_ACTIVE = ("NEW", "AWAIT_LOAN_ID", "AWAIT_OTP", "VERIFIED", "AWAIT_PTP_DATE", "AWAIT_UTR")
CM03_VERIFIED = ("VERIFIED", "AWAIT_PTP_DATE", "AWAIT_UTR")


def cm03_conversation_machine() -> Machine:
    """CM-03 WhatsApp servicing conversation (origin/cm03/whatsapp-servicing, cm03/engine.py). One inbound
    message is one event; NLU (rules, then the k09 classifier) only *names* the event."""
    E = "cm03/engine.py ConversationEngine"
    tr: List[Transition] = []
    for s in CM03_ACTIVE:
        tr += [T(s, "FRAUD_SIGNAL", "HANDOFF", None, "refuse; FRAUD_SUSPECT ticket; nothing disclosed",
                 f"{E}._step: fraud detectors run before anything else (injection, phishing, spoofing, OTP scam)"),
               T(s, "STOP", "CLOSED", None, "Ledger.record_stop (all channels) when it is the registered number",
                 "K01 holds.STOP_OPT_OUT: no OTP needed to opt out"),
               T(s, "WRONG_PERSON", "CLOSED", None, "WRONG_NUMBER_CLOSED; contact-data review", f"{E}: no disclosure"),
               T(s, "ENGINE_ERROR", "HANDOFF", None, "holding reply; a human picks it up", f"{E}.handle")]
    tr += [
        T("NEW", "FIRST_MESSAGE", "AWAIT_OTP", "registered & !otp_blocked", "send OTP; park the request",
          "CM-03: zero disclosure before verification (OTP)"),
        T("NEW", "FIRST_MESSAGE", "HANDOFF", "registered & otp_blocked", "OTP_LOCKED / OTP_SEND_LIMIT", "otp.py lockout / send limit"),
        T("NEW", "FIRST_MESSAGE", "AWAIT_LOAN_ID", "!registered & !has_loan_id", "ask for the loan id", "unregistered sender"),
        T("NEW", "FIRST_MESSAGE", "HANDOFF", "!registered & has_loan_id", "UNREGISTERED_NUMBER (same answer either way)",
          "no account enumeration before verification"),
        T("AWAIT_LOAN_ID", "LOAN_ID_REPLY", "HANDOFF", "has_loan_id", "UNREGISTERED_NUMBER, or fraud on impersonation",
          "no account enumeration before verification"),
        T("AWAIT_LOAN_ID", "LOAN_ID_REPLY", "AWAIT_LOAN_ID", "!has_loan_id & !asked_twice", "ask again", f"{E}._pre_verify"),
        T("AWAIT_LOAN_ID", "LOAN_ID_REPLY", "HANDOFF", "!has_loan_id & asked_twice", "UNIDENTIFIED", f"{E}._pre_verify"),
        T("AWAIT_OTP", "OTP_CODE", "VERIFIED", "otp_ok", "bind sender + conversation + account; serve the parked request",
          "OTP verified (the parked request is then served as a VERIFIED event in the same message)"),
        T("AWAIT_OTP", "OTP_CODE", "AWAIT_OTP", "otp_wrong", "attempts left", "otp.py"),
        T("AWAIT_OTP", "OTP_CODE", "AWAIT_OTP", "otp_expired", "ask to resend", "otp.py"),
        T("AWAIT_OTP", "OTP_CODE", "HANDOFF", "otp_locked", "OTP_LOCKED", "3 wrong codes"),
        T("AWAIT_OTP", "RESEND_OTP", "AWAIT_OTP", "otp_sent", None, "otp.py send limit"),
        T("AWAIT_OTP", "RESEND_OTP", "HANDOFF", "!otp_sent", "OTP_LOCKED / OTP_SEND_LIMIT", "otp.py send limit"),
        T("AWAIT_OTP", "OTHER_BEFORE_OTP", "AWAIT_OTP", None, "park it; PRE_VERIFY_SAFE copy only",
          f"{E}._reply disclosure guard"),
    ]
    for s in CM03_VERIFIED:
        tr += [
            T(s, "SERVED", "VERIFIED", None, "balance / pay link / statement / NOC / loan details / claim / PTP recorded",
              "audited tool calls; collection intents through K13 (exceptions.handle)"),
            T(s, "ASK_PTP_DATE", "AWAIT_PTP_DATE", None, "needs time, or a date outside the window",
              "K13 needs_time / promise_to_pay.auto_accept_max_days"),
            T(s, "ASK_UTR", "AWAIT_UTR", None, "claim without a bank reference", "a claim is never a payment"),
            T(s, "ROUTE_TO_HUMAN", "HANDOFF", None, "ticket: callback / hardship / dispute / waiver / complaint / "
              "PTP beyond window / payment reversed / unknown twice", "K13 routed_exceptions, callback, hardship"),
            T(s, "CROSS_ACCOUNT", "HANDOFF", None, "FRAUD_SUSPECT", "verified borrower asked about another borrower's loan"),
            T(s, "GOODBYE", "CLOSED", None, None, "thanks"),
            T(s, "CLARIFY", "VERIFIED", None, "clarify / menu / language", f"{E}._serve"),
        ]
    tr += [T("HANDOFF", "ANY_MESSAGE", "HANDOFF", None, "holding reply", "a human owns the conversation"),
           T("HANDOFF", "STOP", "HANDOFF", None, "Ledger.record_stop; stays with the human", f"{E}._step")]
    return Machine(
        "cm03.conversation", "1", list(CM03_ACTIVE) + ["HANDOFF", "CLOSED"], "NEW", ["HANDOFF", "CLOSED"], tr,
        exclusive=[{"otp_ok", "otp_wrong", "otp_expired", "otp_locked"}],
        description=("One inbound WhatsApp servicing conversation. Nothing about the account is looked up for a reply "
                     "before OTP verification; a request made before it is parked and served right after. Rules (and "
                     "the k09 classifier, LLM only for ambiguous free text) name the event; the engine's code picks the "
                     "transition and the tool calls. Idle for 30 minutes, or CLOSED, means the next message opens a new "
                     "conversation."),
        enforced_by=["kairos_backend/cm03/engine.py (cm03/whatsapp-servicing): ConversationEngine._step / _pre_verify / _serve / _reply"],
        proven_by=["tests/fsm/test_conformance_cm03.py (runs when kairos_backend.cm03 is present: the seeded CM-03 eval, "
                   "every message's state change must be declared)"],
        llm="classify", source="branch:cm03/whatsapp-servicing")


# =============================================================================================
# catalog
# =============================================================================================
def catalog(policy=None, voice_thresholds: Optional[dict] = None) -> "OrderedDict[str, Machine]":
    """Every declared machine, in STATES.md order. Policy-driven machines are built from `policy`
    (default: load_policy()); the voice disposition machine needs thresholds.json (merged branch or path)."""
    if policy is None:
        from .policy import load_policy
        policy = load_policy()
    if voice_thresholds is None:
        voice_thresholds = load_voice_thresholds()
    ms = [touch_machine(), touch_machine(hitl=True), gate_machine(policy), supervisor_plan_machine(),
          supervisor_dispatch_machine(), outcome_machine(), k13_exception_machine(policy), ptp_hold_machine(),
          hardship_hold_machine(), callback_hold_machine(), supervisor_hold_machine(), consent_machine(),
          intent_machine(), payment_claim_machine(), hitl_approval_machine()]
    if voice_thresholds is not None:
        ms.append(voice_disposition_machine(voice_thresholds))
    ms += [voice_call_machine(), cm03_conversation_machine(), learner_arm_machine()]
    return OrderedDict((m.name, m) for m in ms)


# Built at import: a bad declaration fails here (policy-free machines only; the others validate when built).
TOUCH = touch_machine()
TOUCH_HITL = touch_machine(hitl=True)
SUPERVISOR_PLAN = supervisor_plan_machine()
SUPERVISOR_DISPATCH = supervisor_dispatch_machine()
OUTCOME = outcome_machine()
PTP_HOLD = ptp_hold_machine()
HARDSHIP_HOLD = hardship_hold_machine()
CALLBACK_HOLD = callback_hold_machine()
SUPERVISOR_HOLD = supervisor_hold_machine()
CONSENT = consent_machine()
LEARNER_ARM = learner_arm_machine()
PAYMENT_CLAIM = payment_claim_machine()
HITL_APPROVAL = hitl_approval_machine()
VOICE_CALL = voice_call_machine()
INTENT = intent_machine()
CM03 = cm03_conversation_machine()

STATIC: Dict[str, Machine] = {m.name: m for m in (TOUCH, TOUCH_HITL, SUPERVISOR_PLAN, SUPERVISOR_DISPATCH, OUTCOME,
                                                   PTP_HOLD, HARDSHIP_HOLD, CALLBACK_HOLD, SUPERVISOR_HOLD, CONSENT,
                                                   LEARNER_ARM, PAYMENT_CLAIM, HITL_APPROVAL, VOICE_CALL, INTENT, CM03)}

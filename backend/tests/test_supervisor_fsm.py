"""K06 SupervisorFSM: borrower check → eligibility → rank → reserve → decided → dispatch."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Optional

import pytest

from kairos_backend.contracts import ArmScore, Candidate, Mode, Ranking, RecordingSink, SYNTHETIC_COSTS
from kairos_backend.exceptions import ExceptionEvent
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.ledger import Ledger, stable_instruction_id
from kairos_backend.supervisor import (BLOCKED, BORROWER_CHECK, DECIDED, DISPATCHED, DISPATCHING, ELIGIBILITY,
                                       PLAN_STATES, RANK, RECEIVED, RESERVE, SKIPPED, SupervisorFSM)

D = date(2026, 11, 20)  # Friday — a contact day
SUNDAY = date(2026, 11, 22)
TEMPLATES = {"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN", "ivr": "IVR_HI", "voicebot": "VB_EN"}
SLOT_OF_HOUR = {8: "08-11", 9: "08-11", 10: "08-11", 11: "11-14", 12: "11-14", 13: "11-14", 14: "14-17",
                15: "14-17", 16: "14-17", 17: "17-19", 18: "17-19", 19: "17-19"}


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


class FakeLearner:
    """Deterministic stand-in for the K09 learner (contracts.Learner protocol).
    p(reply) per channel is fixed; ranking = best replies-per-₹ among qualifying arms, first wins ties.
    With exploration modes and an rng, a seeded jitter is added (Thompson-like, reproducible)."""

    model_version = "fake-learner/v1@0"

    def __init__(self, probs: Optional[dict] = None, force_wait: bool = False, min_p: float = 0.03):
        self.probs = probs or {}
        self.force_wait = force_wait
        self.min_p = min_p
        self.calls: list[tuple] = []

    def rank(self, band, candidates, mode, rng=None, borrower_id=None, persist=True) -> Ranking:
        self.calls.append((band, tuple((c.channel, c.slot) for c in candidates), mode, borrower_id))
        scores = []
        for c in candidates:
            mean = self.probs.get(c.channel, 0.10)
            sampled = round(mean + rng.uniform(-0.05, 0.05), 6) if (mode.allows_exploration and rng) else None
            p_used = sampled if sampled is not None else mean
            rpi = p_used / c.est_cost_inr if c.est_cost_inr > 0 else 0.0
            q = p_used >= self.min_p and c.est_cost_inr > 0
            scores.append(ArmScore(band, c.channel, c.slot, 1.0, 1.0, 0, "prior", mean, sampled, p_used,
                                   c.est_cost_inr, rpi, q, "fake"))
        qual = [s for s in scores if s.qualifies]
        if self.force_wait or not qual:
            return Ranking("WAIT", None, scores, self.model_version, mode, "forced" if self.force_wait else "none qualifies")
        best = qual[0]
        for s in qual[1:]:
            if s.replies_per_inr > best.replies_per_inr:
                best = s
        return Ranking("SEND", best, scores, self.model_version, mode, "best replies per INR")

    def apply_outcome(self, outcome) -> bool:
        return False

    def estimates(self, band=None):
        return []


def cand(ch, t, tpl="default", cost=None):
    return Candidate(ch, SLOT_OF_HOUR[t.hour], t, TEMPLATES[ch] if tpl == "default" else tpl,
                     SYNTHETIC_COSTS[ch] if cost is None else cost)


def seed(lg: Ledger):
    lg.upsert_campaign("C1", budget_per_case_inr=100, allocated_cases=10, start_date=D, end_date=D + timedelta(days=10),
                       overdue_at_start_inr=50_000, target_amount_pct=60, target_count_pct=50)
    for t, ch, lang in [("SMS_EN", "sms", "en"), ("WA_HI", "whatsapp", "hi"), ("EM_EN", "email", "en"),
                        ("IVR_HI", "ivr", "hi"), ("VB_EN", "voicebot", "en")]:
        lg.upsert_template(t, ch, lang)
    add_borrower(lg, "B1")


def add_borrower(lg, bid, dpd=12, obligations=("A",), campaign="C1", when=at(7)):
    lg.upsert_borrower(bid, dpd=dpd, dpd_updated_at=when,
                       consent={"sms": True, "whatsapp": True, "email": True, "ivr": True, "voicebot": False})
    for o in obligations:
        lg.upsert_obligation(f"{bid}-{o}", bid, campaign, overdue_inr=5000)


class Rig:
    def __init__(self, policy, path, learner=None, mode=Mode.PRODUCTION):
        self.ledger = Ledger(policy, path)
        seed(self.ledger)
        self.providers = {ch: FakeProvider(ch) for ch in ("sms", "whatsapp", "email", "ivr")}
        self.executor = Executor(self.ledger, self.providers)
        self.learner = learner or FakeLearner({"sms": 0.10, "whatsapp": 0.30, "email": 0.004})
        self.sink = RecordingSink()
        self.fsm = SupervisorFSM(self.ledger, self.executor, self.learner, self.sink, mode=mode)

    def plan(self, cands, when=at(9), bid="B1", ob="B1-A", camp="C1", band="B1", rng=None):
        return self.fsm.plan(bid, ob, camp, band, cands, when, rng=rng)

    def touches(self, bid="B1"):
        return self.ledger.borrower_state(bid)["touches"]


@pytest.fixture
def rig(policy, tmp_path):
    r = Rig(policy, tmp_path / "fsm.db")
    yield r
    r.ledger.close()


STD = [cand("sms", at(10)), cand("whatsapp", at(10)), cand("email", at(10))]


# ---------- happy path ----------
def test_send_walks_every_state_and_carries_full_payload(rig):
    r = rig.plan(STD)
    assert r.states == list(PLAN_STATES)
    assert (r.decision, r.reason, r.rule) == ("SEND", "ELIGIBLE", "ALL_PASSED")
    assert (r.channel, r.slot, r.template_id) == ("whatsapp", "08-11", "WA_HI")
    assert r.instruction_id == stable_instruction_id("B1", "B1-A", "whatsapp", rig.ledger.policy.local(at(10)))
    assert r.reserved and not r.idempotent_replay
    assert r.expires_at.startswith("2026-11-20T19:00")
    assert rig.ledger.touch(r.instruction_id)["status"] == "RESERVED"
    env = r.envelope
    assert env["event_type"] == "supervisor.decision" and env["touch_id"] == r.instruction_id
    p = env["payload"]
    assert p["decision"] == "SEND" and p["policy_version"] == "1.1.0"
    assert p["model_version"] == "fake-learner/v1@0"
    assert p["chosen"] == {"channel": "whatsapp", "slot": "08-11", "template_id": "WA_HI"}
    assert [s["channel"] for s in p["scores"]] == ["whatsapp", "sms", "email"]  # email below the floor → last
    assert p["rejected"] == [] and p["instruction_id"] == r.instruction_id
    assert rig.sink.envelopes == [env]


def test_missing_consent_filters_that_candidate_only(rig):
    learner = rig.learner
    learner.probs["voicebot"] = 0.99  # would win if it were allowed
    r = rig.plan([cand("voicebot", at(10)), cand("sms", at(10))])
    assert r.decision == "SEND" and r.channel == "sms"
    assert [(x["channel"], x["rule"], x["reason"]) for x in r.rejected] == [("voicebot", "CONSENT", "NO_CONSENT")]
    assert learner.calls[-1][1] == (("sms", "08-11"),)  # the learner never saw voicebot
    assert r.envelope["payload"]["rejected"][0]["reason"] == "NO_CONSENT"


# ---------- WAIT paths ----------
@pytest.mark.parametrize("when,reason,rule", [
    (at(19, 30), "CONTACT_WINDOW", "CONTACT_WINDOW"),
    (at(7, 30), "CONTACT_WINDOW", "CONTACT_WINDOW"),
    (at(10, day=SUNDAY), "QUIET_DAY", "QUIET_DAY"),
])
def test_quiet_hours_and_sunday_wait(rig, when, reason, rule):
    if when.date() != D:  # fresh DPD feed for that day, so only the calendar can block
        add_borrower(rig.ledger, "B1", when=at(7, day=when.date()))
    cands = [Candidate(ch, "17-19", when, TEMPLATES[ch], SYNTHETIC_COSTS[ch]) for ch in ("sms", "whatsapp")]
    r = rig.plan(cands, when=at(9, day=when.date()))
    assert (r.decision, r.reason, r.rule) == ("WAIT", reason, rule)
    assert r.states == [RECEIVED, BORROWER_CHECK, ELIGIBILITY, DECIDED]
    assert rig.learner.calls == [] and rig.touches() == []
    assert r.envelope["payload"]["instruction_id"] is None and r.envelope["payload"]["expires_at"] is None


def test_channel_cap_reached_waits(rig):
    for h in (8, 10, 12):
        assert rig.plan([cand("sms", at(h))], when=at(8)).decision == "SEND"
    r = rig.plan([cand("sms", at(14))], when=at(8))
    assert (r.decision, r.reason, r.rule) == ("WAIT", "CHANNEL_CAP", "CHANNEL_CAP")
    assert r.rejected[0]["detail"] == {"cap": 3, "sent": 3}
    assert len(rig.touches()) == 3 and rig.ledger.cap_breaches() == []


def test_no_approved_template_waits(rig):
    rig.ledger.upsert_template("SMS_EN", "sms", "en", approved=False)
    r = rig.plan([cand("sms", at(10)), cand("email", at(10), tpl=None)])
    assert (r.decision, r.reason, r.rule) == ("WAIT", "NO_APPROVED_TEMPLATE", "TEMPLATE_APPROVED")
    assert {x["reason"] for x in r.rejected} == {"NO_APPROVED_TEMPLATE"}


def test_budget_exhausted_waits(rig):
    lg = rig.ledger
    lg.upsert_campaign("CB", budget_per_case_inr=0.1, allocated_cases=1, start_date=D, end_date=D)
    add_borrower(lg, "B9", campaign="CB")
    r = rig.plan([cand("whatsapp", at(10))], bid="B9", ob="B9-A", camp="CB")
    assert (r.decision, r.reason, r.rule) == ("WAIT", "BUDGET_EXHAUSTED", "BUDGET_AVAILABLE")
    assert rig.touches("B9") == []


def test_learner_wait_waits_and_reserves_nothing(rig):
    rig.learner.force_wait = True
    r = rig.plan(STD)
    assert (r.decision, r.reason, r.rule) == ("WAIT", "LEARNER_WAIT", "LEARNER")
    assert r.states == [RECEIVED, BORROWER_CHECK, ELIGIBILITY, RANK, DECIDED]
    assert r.envelope["payload"]["model_version"] == "fake-learner/v1@0"
    assert len(r.envelope["payload"]["scores"]) == 3
    assert rig.touches() == []


def test_no_arm_above_floor_waits(rig):
    r = rig.plan([cand("email", at(10))])  # p=0.004 < 0.03 floor
    assert (r.decision, r.reason) == ("WAIT", "LEARNER_WAIT")


# ---------- borrower-level blocks ----------
def test_stop_cancels_and_queued_touches_are_canceled(rig):
    first = rig.plan(STD)
    assert rig.ledger.touch(first.instruction_id)["status"] == "RESERVED"
    rig.ledger.record_stop("B1", at(9, 30), channel="sms")
    assert rig.ledger.touch(first.instruction_id)["status"] == "CANCELED"
    calls = len(rig.learner.calls)
    r = rig.plan(STD, when=at(9, 45))
    assert (r.decision, r.reason, r.rule) == ("CANCEL", "STOP_OPT_OUT", "STOP_OPT_OUT")
    assert r.states == [RECEIVED, BORROWER_CHECK, DECIDED]
    assert len(rig.learner.calls) == calls  # never ranked
    assert all(t["status"] == "CANCELED" for t in rig.touches())
    assert rig.ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0)


def test_hardship_with_supervisor_flag_hands_off(rig):
    rig.ledger.apply_exception("B1", ExceptionEvent("HARDSHIP", "B1", date_offset_days=10), at(9))
    r = rig.plan(STD, when=at(9, 30))
    assert (r.decision, r.reason, r.rule) == ("HANDOFF", "HARDSHIP_HOLD", "HARDSHIP_HOLD")
    assert r.states == [RECEIVED, BORROWER_CHECK, DECIDED]
    assert r.envelope["payload"]["decision"] == "HANDOFF"


def test_hardship_without_date_waits(rig):
    rig.ledger.apply_exception("B1", ExceptionEvent("HARDSHIP", "B1"), at(9))
    r = rig.plan(STD, when=at(9, 30))
    assert (r.decision, r.reason) == ("WAIT", "HARDSHIP_HOLD")


def test_ptp_hold_waits(rig):
    rig.ledger.apply_exception("B1", ExceptionEvent("PTP_OFFERED", "B1", date_offset_days=2), at(9), obligation_id="B1-A")
    nxt = D + timedelta(days=1)
    rig.ledger.upsert_borrower("B1", 13, at(7, day=nxt), {"sms": True, "whatsapp": True, "email": True})
    r = rig.plan([cand("sms", at(10, day=nxt))], when=at(9, day=nxt))
    assert (r.decision, r.reason, r.rule) == ("WAIT", "PTP_HOLD", "PTP_HOLD")
    assert r.states == [RECEIVED, BORROWER_CHECK, DECIDED]


def test_cleared_obligation_cancels(rig):
    rig.ledger.record_payment_verified("B1", "B1-A", 5000, at(8))
    r = rig.plan(STD)
    assert (r.decision, r.reason, r.rule) == ("CANCEL", "OBLIGATION_CLEARED", "OBLIGATION_CLEARED")


# ---------- idempotency & determinism ----------
def test_plan_twice_reserves_once_and_emits_identical_envelopes(rig):
    a = rig.plan(STD)
    b = rig.plan(STD)
    assert a.instruction_id == b.instruction_id and b.reserved and b.idempotent_replay
    assert len(rig.touches()) == 1
    assert rig.ledger.campaign_state("C1")["committed_inr"] == pytest.approx(SYNTHETIC_COSTS["whatsapp"])
    assert a.envelope == b.envelope
    assert rig.sink.envelopes[0] == rig.sink.envelopes[1]


def test_spent_instruction_id_is_not_offered_again(rig):
    """A canceled stable id can never be reserved again; the plan moves to the next best arm."""
    a = rig.plan([cand("whatsapp", at(10)), cand("sms", at(10))])
    rig.ledger.set_campaign_status("C1", "PAUSED")
    rig.ledger.set_campaign_status("C1", "RUNNING")
    assert rig.ledger.touch(a.instruction_id)["status"] == "CANCELED"
    b = rig.plan([cand("whatsapp", at(10)), cand("sms", at(10))])
    assert b.decision == "SEND" and b.channel == "sms"
    assert b.rejected[0]["rule"] == "IDEMPOTENCY" and b.rejected[0]["reason"] == "INSTRUCTION_CANCELED"


def test_transitions_and_envelopes_are_deterministic(policy, tmp_path):
    runs = []
    for name in ("a", "b"):
        rg = Rig(policy, tmp_path / f"{name}.db", mode=Mode.SIMULATOR)
        rng = random.Random(42)
        out = [rg.plan(STD, rng=rng), rg.plan([cand("sms", at(19, 30))], rng=rng),
               rg.plan([cand("sms", at(12)), cand("email", at(12))], rng=rng)]
        runs.append(([[t.as_dict() for t in r.transitions] for r in out], rg.sink.envelopes))
        rg.ledger.close()
    assert runs[0] == runs[1]
    # sampled_p is populated (SIMULATOR) and still reproducible
    assert runs[0][1][0]["payload"]["scores"][0]["sampled_p"] is not None


# ---------- dispatch ----------
def test_dispatch_sends_once_then_skips(rig):
    r = rig.plan(STD)
    first = rig.fsm.dispatch(r.instruction_id, at(10))
    assert (first.outcome, first.decision, first.state) == ("SENT", "SEND", DISPATCHED)
    again = rig.fsm.dispatch(r.instruction_id, at(10, 1))
    assert (again.outcome, again.decision, again.state) == ("SKIPPED", None, SKIPPED)
    assert rig.providers["whatsapp"].sent == [r.instruction_id]
    assert [t.to for t in first.transitions] == [DISPATCHING, DISPATCHED]
    assert [e["event_type"] for e in rig.sink.envelopes] == ["supervisor.decision", "touch.dispatched", "touch.dispatched"]
    assert rig.sink.envelopes[1]["dedupe_key"] != rig.sink.envelopes[2]["dedupe_key"]


def test_payment_verified_between_reserve_and_dispatch_blocks(rig):
    r = rig.plan(STD)
    rig.ledger.record_payment_verified("B1", "B1-A", 5000, at(9, 30))
    out = rig.fsm.dispatch(r.instruction_id, at(10))
    assert (out.outcome, out.decision, out.state) == ("BLOCKED", "CANCEL", BLOCKED)
    assert (out.rule, out.reason, out.gate_decision) == ("OBLIGATION_CLEARED", "OBLIGATION_CLEARED", "CANCEL")
    assert rig.ledger.touch(r.instruction_id)["status"] == "CANCELED"
    assert rig.providers["whatsapp"].sent == []
    cancel = [e for e in rig.sink.envelopes if e["payload"]["decision"] == "CANCEL"]
    assert {e["event_type"] for e in cancel} == {"supervisor.decision", "touch.dispatched"}
    assert all(e["payload"]["rule"] == "OBLIGATION_CLEARED" for e in cancel)


def test_stop_between_reserve_and_dispatch_blocks(rig):
    r = rig.plan(STD)
    rig.ledger.record_stop("B1", at(9, 30))
    out = rig.fsm.dispatch(r.instruction_id, at(10))
    assert (out.outcome, out.decision, out.rule, out.reason) == ("BLOCKED", "CANCEL", "STOP_OPT_OUT", "STOP_OPT_OUT")
    assert rig.providers["whatsapp"].sent == []


def test_send_time_recheck_blocks_with_gate_rule(rig):
    """Conditions changed without an event-driven cancel → begin_dispatch's re-check blocks it."""
    r = rig.plan(STD)
    rig.ledger.upsert_template("WA_HI", "whatsapp", "hi", approved=False)
    out = rig.fsm.dispatch(r.instruction_id, at(10))
    assert (out.outcome, out.decision) == ("BLOCKED", "CANCEL")
    assert (out.rule, out.reason, out.gate_decision) == ("TEMPLATE_APPROVED", "NO_APPROVED_TEMPLATE", "WAIT")
    assert rig.ledger.touch(r.instruction_id)["status"] == "CANCELED"
    assert rig.ledger.campaign_state("C1")["committed_inr"] == pytest.approx(0)
    assert rig.providers["whatsapp"].sent == []


def test_all_envelopes_carry_policy_version(rig):
    a = rig.plan(STD)
    rig.plan([cand("sms", at(19, 30))])
    rig.fsm.dispatch(a.instruction_id, at(10))
    rig.fsm.dispatch(a.instruction_id, at(10, 5))
    rig.ledger.record_stop("B1", at(11))
    rig.plan(STD, when=at(11, 30))
    envs = rig.sink.envelopes
    assert len(envs) == 5
    assert {e["payload"]["decision"] for e in envs} >= {"SEND", "WAIT", "CANCEL"}
    for e in envs:
        assert e["payload"]["policy_version"] == "1.1.0"
        assert e["provenance"] == "SIMULATED" and e["borrower_id"] == "B1" and e["dedupe_key"]
        for k in ("decision", "reason", "rule", "expires_at", "instruction_id", "model_version", "chosen",
                  "scores", "rejected"):
            assert k in e["payload"]

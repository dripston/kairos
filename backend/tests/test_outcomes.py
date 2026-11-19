"""K09 outcome finalizer: reply attribution (24 h window, one reward per touch), reach vs reply vs
payment, UNRESOLVED for unknown provider outcomes, exactly-once learning."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from kairos_backend.learner import BetaBernoulliLearner
from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.outcomes import (DUPLICATE, LATE_REPLY, REACH_ONLY, REPLY_ATTRIBUTED, UNATTRIBUTED,
                                     OutcomeFinalizer)
from tests.spine_helpers import add_loan

D = date(2026, 11, 20)  # a Friday — a contact day
TPL = {"sms": "SMS_EN", "whatsapp": "WA_HI", "email": "EM_EN", "ivr": "IVR_HI"}


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


class SpyLearner:
    """Wraps the real learner and counts apply_outcome calls; can be told to fail once."""

    def __init__(self, inner):
        self.inner, self.calls, self.fail_next = inner, [], False

    @property
    def model_version(self):
        return self.inner.model_version

    def rank(self, *a, **k):
        return self.inner.rank(*a, **k)

    def estimates(self, band=None):
        return self.inner.estimates(band)

    def apply_outcome(self, outcome):
        self.calls.append((outcome.instruction_id, outcome.status))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("learner crashed")
        return self.inner.apply_outcome(outcome)


@pytest.fixture
def ledger(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "k09o.db")
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    lg.upsert_campaign("C2", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    for ch, tpl in TPL.items():
        lg.upsert_template(tpl, ch, "en" if ch in ("sms", "email") else "hi")
    lg.start_day(D)  # daily LMS refresh: the spine's DPD is fresh today
    yield lg
    lg.close()


@pytest.fixture
def learner(ledger):
    return SpyLearner(BetaBernoulliLearner(ledger))


@pytest.fixture
def fin(ledger, learner):
    return OutcomeFinalizer(ledger, learner)


def add_borrower(lg, bid, campaign="C1", dpd=12):
    """K03 borrower + loan + one EMI `dpd` days overdue, enrolled in `campaign`."""
    return add_loan(lg, bid, D, dpd=dpd, campaign=campaign)


def send(lg, fin, bid, ch="whatsapp", t=None, provider="ACCEPTED", campaign="C1", band="B1", slot="11-14"):
    """Reserve -> dispatch -> provider status in the ledger, then register the execution."""
    t = t or at(11, 30)
    if lg._conn().execute("SELECT 1 FROM borrowers WHERE borrower_id=?", (bid,)).fetchone() is None:
        add_borrower(lg, bid, campaign)
    r = lg.reserve(Instruction(bid, f"{bid}-A-1", campaign, ch, TPL[ch], t, 40))
    assert r.reserved, r.decision
    assert lg.begin_dispatch(r.instruction_id, at=t).allowed
    lg.record_provider_status(r.instruction_id, provider)
    assert fin.register_execution(r.instruction_id, bid, band, ch, slot, t)
    return r.instruction_id


def n_updates(ledger):
    return ledger._conn().execute("SELECT COUNT(*) FROM learner_updates").fetchone()[0]


# ---------- windows / pending ----------
def test_window_comes_from_policy_and_pending_is_not_learned(ledger, fin, learner):
    assert fin.reply_window == timedelta(hours=ledger.policy.raw["reply_attribution"]["reply_window_hours"]) \
        == timedelta(hours=24)
    iid = send(ledger, fin, "P1")
    o = fin.outcome(iid)
    assert o.status == "PENDING" and o.window_end == o.executed_at + timedelta(hours=24)
    assert fin.register_execution(iid, "P1", "B1", "whatsapp", "11-14", at(11, 30)) is False  # idempotent
    assert fin.finalize_due(at(11, 29, D + timedelta(days=1))) == []  # window still open
    assert fin.outcome(iid).status == "PENDING"
    assert learner.calls == [] and n_updates(ledger) == 0
    assert fin.metrics()["pending"] == 1


def test_reply_in_window_becomes_reply_and_is_learned_once(ledger, fin, learner):
    iid = send(ledger, fin, "P1")
    assert fin.record_reply("P1", at(15), meaningful=True, channel="whatsapp") == REPLY_ATTRIBUTED
    done = fin.finalize_due(at(12, 0, D + timedelta(days=1)))
    assert [(o.instruction_id, o.status, o.reward) for o in done] == [(iid, "REPLY", 1)]
    assert done[0].reached is True and done[0].reply_at is not None
    assert learner.calls == [(iid, "REPLY")]
    # finalize again: nothing new, learner not called again
    assert fin.finalize_due(at(12, 0, D + timedelta(days=2))) == []
    assert learner.calls == [(iid, "REPLY")] and n_updates(ledger) == 1


def test_sent_without_reply_is_no_reply(ledger, fin, learner):
    iid = send(ledger, fin, "P1", provider="ACCEPTED")  # ledger status SENT, no reach callback
    assert ledger.touch(iid)["status"] == "SENT"
    done = fin.finalize_due(at(12, 0, D + timedelta(days=1)))
    assert [(o.status, o.reward) for o in done] == [("NO_REPLY", 0)]
    assert learner.calls == [(iid, "NO_REPLY")]


def test_reply_outside_window_is_not_attributed(ledger, fin):
    iid = send(ledger, fin, "P1")
    assert fin.record_reply("P1", at(11, 0), meaningful=True) == UNATTRIBUTED  # before the touch
    assert fin.record_reply("P1", at(12, 0, D + timedelta(days=1)), meaningful=True) == LATE_REPLY  # after window
    o = fin.finalize_due(at(13, 0, D + timedelta(days=1)))[0]
    assert o.instruction_id == iid and o.status == "NO_REPLY"
    assert fin.metrics()["late_replies"] == 1 and fin.metrics()["replies"] == 0


def test_reply_goes_to_most_recent_touch_only(ledger, fin):
    first = send(ledger, fin, "P1", ch="whatsapp", t=at(9))
    second = send(ledger, fin, "P1", ch="sms", t=at(14), slot="14-17")
    assert fin.record_reply("P1", at(16), meaningful=True) == REPLY_ATTRIBUTED
    status = {o.instruction_id: o.status for o in fin.finalize_due(at(15, 0, D + timedelta(days=1)))}
    assert status == {first: "NO_REPLY", second: "REPLY"}


# ---------- duplicates / late ----------
def test_duplicate_reply_callback_counted_once(ledger, fin, learner):
    iid = send(ledger, fin, "P1")
    assert fin.record_reply("P1", at(15), meaningful=True) == REPLY_ATTRIBUTED
    assert fin.record_reply("P1", at(15), meaningful=True) == DUPLICATE
    assert fin.record_reply("P1", at(16), meaningful=True) == DUPLICATE  # second reply, same touch: one reward
    fin.finalize_due(at(12, 0, D + timedelta(days=1)))
    assert learner.calls == [(iid, "REPLY")]
    arm = ledger._conn().execute("SELECT n_obs FROM learner_arms").fetchone()
    assert arm["n_obs"] == 1
    m = fin.metrics()
    assert (m["replies"], m["late_replies"]) == (1, 0)


def test_late_reply_after_finalization_does_not_change_reward(ledger, fin, learner):
    iid = send(ledger, fin, "P1", provider="DELIVERED")
    [o] = fin.finalize_due(at(12, 0, D + timedelta(days=1)))
    assert o.status == "NO_REPLY"
    est_before, version_before = learner.estimates("B1"), learner.model_version
    # reply time is inside the window, but the callback arrives after the touch was finalized
    assert fin.record_reply("P1", at(20), meaningful=True) == LATE_REPLY
    assert fin.record_reply("P1", at(20), meaningful=True) == DUPLICATE
    row = fin.row(iid)
    assert row["status"] == "NO_REPLY" and row["reply_at"] is None and row["late_reply_at"] is not None
    assert fin.finalize_due(at(12, 0, D + timedelta(days=3))) == []
    assert learner.calls == [(iid, "NO_REPLY")]
    assert learner.estimates("B1") == est_before and learner.model_version == version_before
    assert fin.metrics()["late_replies"] == 1


# ---------- reach vs reply vs payment ----------
def test_reach_reply_and_payment_are_separate(ledger, fin, learner):
    a = send(ledger, fin, "P1")                     # reached + replied
    b = send(ledger, fin, "P2")                     # reached via click only
    c = send(ledger, fin, "P3", provider="UNKNOWN")  # paid, but provider outcome unknown
    d = send(ledger, fin, "P4", provider="DELIVERED")  # delivered (ledger) only

    assert fin.record_reach(a, True, at(11, 31)) is True
    assert fin.record_reach(a, True, at(11, 32)) is False   # duplicate delivery callback
    assert fin.record_reach(a, False, at(11, 33)) is False  # reach never goes back
    assert fin.record_reply("P1", at(13), meaningful=True) == REPLY_ATTRIBUTED
    assert fin.record_reply("P2", at(13), meaningful=False) == REACH_ONLY  # link click / read
    assert fin.record_payment_verified("P3", at(18)) == c
    assert fin.record_payment_verified("P3", at(19)) is None  # already noted
    assert fin.record_payment_verified("P9", at(18)) is None  # no touch

    status = {o.instruction_id: o.status for o in fin.finalize_due(at(12, 0, D + timedelta(days=1)))}
    assert status == {a: "REPLY", b: "NO_REPLY", c: "UNRESOLVED", d: "NO_REPLY"}
    m = fin.metrics()
    assert m["executed"] == 4 and m["executed_borrowers"] == 4
    assert (m["reached_touches"], m["reached_borrowers"]) == (3, 3)  # a (callback), b (click), d (DELIVERED)
    assert (m["replies"], m["no_replies"], m["unresolved"], m["pending"]) == (1, 2, 1, 0)
    assert (m["verified_payments"], m["paying_borrowers"]) == (1, 1)
    assert m["late_replies"] == 0
    # the payment never became a reward; the click never became a reply
    assert sorted(learner.calls) == sorted([(a, "REPLY"), (b, "NO_REPLY"), (d, "NO_REPLY")])


def test_metrics_by_campaign(ledger, fin):
    send(ledger, fin, "P1", campaign="C1")
    send(ledger, fin, "P2", campaign="C2")
    fin.record_reply("P2", at(13), meaningful=True)
    assert fin.metrics("C1")["executed"] == 1 and fin.metrics("C1")["replies"] == 0
    fin.finalize_due(at(12, 0, D + timedelta(days=1)))
    assert fin.metrics("C2")["replies"] == 1 and fin.metrics("C1")["no_replies"] == 1
    assert fin.metrics()["executed"] == 2


# ---------- unknown provider outcomes ----------
def test_uncertain_touch_is_unresolved_and_not_learned(ledger, fin, learner):
    iid = send(ledger, fin, "P1", provider="UNKNOWN")
    assert ledger.touch(iid)["status"] == "UNCERTAIN"
    [o] = fin.finalize_due(at(12, 0, D + timedelta(days=1)))
    assert (o.status, o.reward) == ("UNRESOLVED", None)
    assert learner.calls == [] and n_updates(ledger) == 0
    # stays out of learning on later runs too
    assert fin.finalize_due(at(12, 0, D + timedelta(days=5))) == []
    assert fin.outcome(iid).status == "UNRESOLVED" and n_updates(ledger) == 0


def test_uncertain_touch_with_confirmed_reach_is_no_reply(ledger, fin):
    iid = send(ledger, fin, "P1", provider="UNKNOWN")
    fin.record_reach(iid, True, at(12))
    assert fin.finalize_due(at(12, 0, D + timedelta(days=1)))[0].status == "NO_REPLY"


def test_reply_proves_receipt_even_when_provider_uncertain(ledger, fin):
    send(ledger, fin, "P1", provider="UNKNOWN")
    fin.record_reply("P1", at(13), meaningful=True)
    assert fin.finalize_due(at(12, 0, D + timedelta(days=1)))[0].status == "REPLY"


def test_failed_touch_is_unresolved(ledger, fin, learner):
    iid = send(ledger, fin, "P1", provider="REJECTED")
    assert ledger.touch(iid)["status"] == "FAILED"
    assert fin.finalize_due(at(12, 0, D + timedelta(days=1)))[0].status == "UNRESOLVED"
    assert learner.calls == []


def test_touch_without_ledger_row_needs_reach_info(ledger, fin):
    """Simulator touches may not be in the ledger: no reach info -> UNRESOLVED; reach known -> NO_REPLY."""
    fin.register_execution("SIM-1", "S1", "B2", "sms", "08-11", at(9))
    fin.register_execution("SIM-2", "S2", "B2", "ivr", "08-11", at(9))
    fin.record_reach("SIM-2", False, at(9, 1))  # call placed, not connected: observable no-reply
    status = {o.instruction_id: o.status for o in fin.finalize_due(at(10, 0, D + timedelta(days=1)))}
    assert status == {"SIM-1": "UNRESOLVED", "SIM-2": "NO_REPLY"}
    assert fin.record_reach("NOPE", True, at(9)) is False


# ---------- exactly-once learning ----------
def test_learning_heals_after_a_crash_between_finalize_and_learn(ledger, fin, learner):
    iid = send(ledger, fin, "P1")
    fin.record_reply("P1", at(13), meaningful=True)
    learner.fail_next = True
    with pytest.raises(RuntimeError):
        fin.finalize_due(at(12, 0, D + timedelta(days=1)))
    assert fin.outcome(iid).status == "REPLY" and n_updates(ledger) == 0
    assert fin.finalize_due(at(12, 0, D + timedelta(days=2))) == []  # nothing newly finalized ...
    assert n_updates(ledger) == 1                                     # ... but the reward was applied
    fin.finalize_due(at(12, 0, D + timedelta(days=3)))
    assert learner.calls == [(iid, "REPLY"), (iid, "REPLY")] and n_updates(ledger) == 1


def test_end_to_end_learner_moves_with_outcomes(ledger):
    lr = BetaBernoulliLearner(ledger, min_segment_obs=1)
    fin = OutcomeFinalizer(ledger, lr, reply_window_hours=2)
    v0 = lr.model_version
    before = {(e["channel"], e["slot"]): e["mean"] for e in lr.estimates("B1")}
    iid = send(ledger, fin, "P1", ch="whatsapp", slot="11-14")
    assert fin.outcome(iid).window_end == fin.outcome(iid).executed_at + timedelta(hours=2)
    fin.record_reply("P1", at(12), meaningful=True)
    fin.finalize_due(at(14))
    after = {(e["channel"], e["slot"]): e["mean"] for e in lr.estimates("B1")}
    assert after[("whatsapp", "11-14")] > before[("whatsapp", "11-14")]
    assert lr.model_version != v0


# ---------- free-text replies through the intent stack ----------
def test_text_reply_is_classified_then_attributed(ledger, fin, monkeypatch):
    monkeypatch.delenv("KAIROS_INTENT_STACK", raising=False)
    send(ledger, fin, "P1")
    code, res = fin.record_text_reply("P1", at(15), "bhai kal pakka de dunga", channel="whatsapp")
    assert code == REPLY_ATTRIBUTED and res.label == "PTP" and res.entities["date"] == "2026-11-21"
    code, res = fin.record_text_reply("P1", at(16), "link bhejo")
    assert code == DUPLICATE and res.label == "LINK_REQUEST"           # one reward per touch


def test_text_reply_stop_and_wrong_number_are_not_meaningful(ledger, fin):
    send(ledger, fin, "P1")
    send(ledger, fin, "P2")
    code, res = fin.record_text_reply("P1", at(15), "STOP")
    assert (code, res.label) == (REACH_ONLY, "STOP")
    code, res = fin.record_text_reply("P2", at(15), "galat number hai", use_stack=False)
    assert (code, res.label, res.tier_used) == (REACH_ONLY, "WRONG_NUMBER", "legacy_rules")
    assert fin.row(fin.ledger._conn().execute(
        "SELECT instruction_id FROM touch_outcomes WHERE borrower_id='P1'").fetchone()[0])["reply_at"] is None

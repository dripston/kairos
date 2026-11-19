"""K09 STOP-risk model: STOP events charged to the touch whose reply window holds them, channel x
contact-number segments with hierarchical shrinkage, and the ContextualLearner's opt-out penalty / cap."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from kairos_backend.borrower_context import BorrowerContextModel, ContextualLearner
from kairos_backend.contracts import SYNTHETIC_COSTS, Candidate, Mode
from kairos_backend.learner import BetaBernoulliLearner
from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.outcomes import OutcomeFinalizer
from kairos_backend.stop_risk import SEGMENTS, StopRiskModel, segment_of
from tests.spine_helpers import add_loan

D = date(2026, 11, 20)  # Friday


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


@pytest.fixture
def ledger(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "stop.db")
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=20))
    lg.upsert_template("WA_HI", "whatsapp", "hi")
    lg.upsert_template("SMS_HI", "sms", "hi")
    lg.start_day(D)
    yield lg
    lg.close()


def touch(ledger, fin, borrower, ch, when):
    ob = add_loan(ledger, borrower, D, campaign="C1")
    ledger.start_day(when.date())
    r = ledger.reserve(Instruction(borrower, ob, "C1", ch, "WA_HI" if ch == "whatsapp" else "SMS_HI", when,
                                   SYNTHETIC_COSTS[ch]))
    assert r.reserved, r.as_dict()
    assert ledger.begin_dispatch(r.instruction_id, when).allowed
    ledger.record_provider_status(r.instruction_id, "ACCEPTED")
    fin.register_execution(r.instruction_id, borrower, "B1", ch, "08-11", when)
    return r.instruction_id


def setup(ledger):
    lr = BetaBernoulliLearner(ledger, priors={("whatsapp", "08-11"): (0.12, 10), ("sms", "08-11"): (0.10, 10)})
    ctx = BorrowerContextModel(ledger)
    return lr, ctx, OutcomeFinalizer(ledger, ContextualLearner(lr, ctx))


def test_segments():
    assert [segment_of(n) for n in (0, 1, 2, 3, 9)] == ["n0", "n1", "n2", "n3+", "n3+"]
    assert SEGMENTS == ("n0", "n1", "n2", "n3+")
    with pytest.raises(ValueError):
        StopRiskModel(None, prior_mean=0)


def test_stop_is_charged_to_the_touch_whose_window_holds_it(ledger):
    _, _, fin = setup(ledger)
    sm = StopRiskModel(ledger, prior_mean=0.01, prior_strength=100, strength=10)
    touch(ledger, fin, "P1", "whatsapp", at(10))                      # P1: first touch, STOP 2 h later
    ledger.record_stop("P1", at(12), "whatsapp")
    touch(ledger, fin, "P2", "whatsapp", at(10))                      # P2: two touches, no STOP
    touch(ledger, fin, "P2", "sms", at(10, 0, D + timedelta(days=3)))
    touch(ledger, fin, "P3", "sms", at(10))                           # P3: STOP outside the window
    ledger.record_stop("P3", at(12, 0, D + timedelta(days=2)), "sms")
    # before any window closes there is no evidence at all
    assert sm.counts(at(9, 0, D + timedelta(days=1))) == {}
    c = sm.counts(at(9, 0, D + timedelta(days=5)))
    assert c[("whatsapp", "n0")] == [1, 2] and c[("sms", "n0")] == [0, 1] and c[("sms", "n1")] == [0, 1]
    assert sm.n_prior("P2", at(9, 0, D + timedelta(days=5))) == 2
    # hierarchy: pooled (1 + 1) / (4 + 100); channel and segment centred on the level above
    when = at(9, 0, D + timedelta(days=5))
    pooled = 2 / 104
    wa = (1 + 10 * pooled) / (2 + 10)
    assert sm.p_stop("whatsapp", "n0", when) == pytest.approx((1 + 10 * wa) / (2 + 10))
    assert sm.p_stop("whatsapp", "n2", when) == pytest.approx(wa)      # unseen segment = channel level
    assert sm.p_stop("sms", "n0", when) < sm.p_stop("whatsapp", "n0", when)
    assert {e["segment"] for e in sm.estimates(when)} == set(SEGMENTS)


def test_penalty_moves_choice_and_cap_turns_send_into_wait(ledger):
    lr, ctx, fin = setup(ledger)
    sm = StopRiskModel(ledger, prior_mean=0.003, prior_strength=1000, strength=10)
    for i in range(3):                                                # WhatsApp touches that drew STOPs
        touch(ledger, fin, f"W{i}", "whatsapp", at(10))
        ledger.record_stop(f"W{i}", at(12), "whatsapp")
    when = at(9, 0, D + timedelta(days=3))
    cands = [Candidate("whatsapp", "08-11", when, None, 40), Candidate("sms", "08-11", when, None, 40)]
    plain = ContextualLearner(lr, ctx).rank("B1", cands, Mode.PRODUCTION, borrower_id="NEW", persist=False, at=when)
    assert plain.chosen.channel == "whatsapp" and plain.chosen.stop_risk is None   # better reply prior at equal cost
    pen = ContextualLearner(lr, ctx, stop_model=sm, stop_penalty=1)
    r = pen.rank("B1", cands, Mode.PRODUCTION, borrower_id="NEW", persist=False, at=when)
    assert r.chosen.channel == "sms" and "P(STOP)" in r.chosen.reason
    wa = next(s for s in r.scores if s.channel == "whatsapp")
    assert wa.replies_per_inr == pytest.approx((wa.p_used - 1 * wa.stop_risk) / 0.40)
    capped = ContextualLearner(lr, ctx, stop_model=sm, stop_max=0.001)
    assert capped.rank("B1", cands, Mode.PRODUCTION, borrower_id="NEW", persist=False, at=when).action == "WAIT"
    with pytest.raises(ValueError):
        ContextualLearner(lr, ctx, stop_penalty=5)                    # needs a model

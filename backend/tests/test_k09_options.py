"""K09 optional decision components: discounted arm evidence (decay), hierarchical slots, the
replies-per-rupee value floor, the gap recency feature, per-borrower channel affinity, and the replay's
KairosOptions plumbing. Every option is off by default and the default model is unchanged."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from kairos_backend.borrower_context import GAP_BUCKETS, BorrowerContextModel, ContextualLearner
from kairos_backend.contracts import SYNTHETIC_COSTS, Candidate, Mode, Outcome
from kairos_backend.learner import BetaBernoulliLearner
from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.outcomes import OutcomeFinalizer
from tests.spine_helpers import add_loan

D = date(2026, 11, 20)  # Friday


def at(hh, mm=0, day=D):
    return datetime(day.year, day.month, day.day, hh, mm)


@pytest.fixture
def ledger(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "opts.db")
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=20))
    lg.upsert_template("WA_HI", "whatsapp", "hi")
    lg.upsert_template("SMS_HI", "sms", "hi")
    lg.start_day(D)
    yield lg
    lg.close()


def oc(iid, status, executed, ch="whatsapp", slot="11-14", band="B1", borrower="X"):
    return Outcome(iid, borrower, band, ch, slot, executed, executed + timedelta(hours=24), status)


def cand(ch, t, slot="11-14"):
    return Candidate(ch, slot, t, None, SYNTHETIC_COSTS[ch])


PRIORS = {("whatsapp", "11-14"): (0.10, 10), ("sms", "11-14"): (0.10, 10)}


# ---------- learner: defaults unchanged ----------
def test_default_options_keep_the_model_version(policy, tmp_path):
    a = BetaBernoulliLearner(Ledger(policy, tmp_path / "a.db"))
    b = BetaBernoulliLearner(Ledger(policy, tmp_path / "b.db"), decay=None, hier_strength=None,
                             min_replies_per_inr=0.0)
    c = BetaBernoulliLearner(Ledger(policy, tmp_path / "c.db"), decay=0.9)
    assert a.model_version == b.model_version != c.model_version


@pytest.mark.parametrize("kw", [{"decay": 0.0}, {"decay": 1.5}, {"hier_strength": 0}])
def test_invalid_options_rejected(ledger, kw):
    with pytest.raises(ValueError):
        BetaBernoulliLearner(ledger, **kw)


# ---------- decay ----------
def test_decay_discounts_old_evidence_by_execution_day(ledger):
    lr = BetaBernoulliLearner(ledger, priors=PRIORS, decay=0.5, min_segment_obs=1000)
    old = at(10, day=D - timedelta(days=2))
    for i in range(10):
        lr.apply_outcome(oc(f"O{i}", "REPLY", old))            # 10 replies two days ago -> weight 0.25
    for i in range(10):
        lr.apply_outcome(oc(f"N{i}", "NO_REPLY", at(10)))       # 10 misses today -> weight 1
    s = lr.rank("B1", [cand("whatsapp", at(11))], Mode.PRODUCTION, persist=False, at=at(11)).scores[0]
    # prior 1 / 9, + 2.5 replies, + 10 misses (pooled: segment has < min_segment_obs)
    assert (s.alpha, s.beta) == pytest.approx((1 + 2.5, 9 + 10))
    plain = BetaBernoulliLearner(ledger, priors=PRIORS, min_segment_obs=1000)
    p = plain.rank("B1", [cand("whatsapp", at(11))], Mode.PRODUCTION, persist=False, at=at(11)).scores[0]
    assert (p.alpha, p.beta) == pytest.approx((11, 19))
    assert s.posterior_mean < p.posterior_mean


def test_decay_one_equals_undiscounted_counts(ledger):
    lr = BetaBernoulliLearner(ledger, priors=PRIORS, decay=1.0, min_segment_obs=1000)
    for i in range(4):
        lr.apply_outcome(oc(f"R{i}", "REPLY" if i % 2 else "NO_REPLY", at(10, day=D - timedelta(days=i))))
    s = lr.rank("B1", [cand("whatsapp", at(11))], Mode.PRODUCTION, persist=False, at=at(11)).scores[0]
    assert (s.alpha, s.beta, s.source) == (pytest.approx(3), pytest.approx(11), "pooled")


# ---------- hierarchical slots ----------
def test_hierarchical_segment_is_centred_on_pooled_mean(ledger):
    lr = BetaBernoulliLearner(ledger, priors=PRIORS, hier_strength=20)
    for i in range(30):
        lr.apply_outcome(oc(f"B2-{i}", "REPLY" if i < 15 else "NO_REPLY", at(10), band="B2"))  # 50% in B2
    for i in range(2):
        lr.apply_outcome(oc(f"B1-{i}", "NO_REPLY", at(10), band="B1"))
    s = lr.rank("B1", [cand("whatsapp", at(11))], Mode.PRODUCTION, persist=False, at=at(11)).scores[0]
    mu = (1 + 15) / (10 + 32)                       # pooled channel x slot posterior mean
    assert s.source == "hier" and s.n_obs == 2
    assert (s.alpha, s.beta) == pytest.approx((20 * mu, 20 * (1 - mu) + 2))


# ---------- value floor ----------
def test_min_replies_per_inr_turns_send_into_wait(ledger):
    cands = [cand("whatsapp", at(11))]                          # p 0.10 / INR 0.40 = 0.25 replies per INR
    lo = BetaBernoulliLearner(ledger, priors=PRIORS, min_replies_per_inr=0.2)
    hi = BetaBernoulliLearner(ledger, priors=PRIORS, min_replies_per_inr=0.3)
    assert lo.rank("B1", cands, Mode.PRODUCTION, persist=False).action == "SEND"
    r = hi.rank("B1", cands, Mode.PRODUCTION, persist=False)
    assert r.action == "WAIT" and "min_replies_per_inr" in r.scores[0].reason


def test_value_floor_applies_to_borrower_adjusted_score(ledger):
    lr = BetaBernoulliLearner(ledger, priors=PRIORS, min_replies_per_inr=0.2)
    ctx = BorrowerContextModel(ledger)
    cl = ContextualLearner(lr, ctx)
    cands = [cand("whatsapp", at(11))]
    assert cl.rank("B1", cands, Mode.PRODUCTION, borrower_id="P1", persist=False, at=at(11)).action == "SEND"
    with ledger.tx() as c:   # new|r0 borrowers reply at half the pooled rate -> 0.25 * ~0.6 < 0.2
        c.execute("INSERT INTO learner_context (bucket, alpha, beta, n_obs) VALUES ('new|r0', ?, ?, 400)",
                  (ctx.prior[0] + 6, ctx.prior[1] + 394))
        c.execute("INSERT INTO learner_context (bucket, alpha, beta, n_obs) VALUES ('replied|r0', ?, ?, 100)",
                  (ctx.prior[0] + 20, ctx.prior[1] + 80))
    r = cl.rank("B1", cands, Mode.PRODUCTION, borrower_id="P1", persist=False, at=at(11))
    assert r.action == "WAIT" and r.scores[0].replies_per_inr < 0.2


# ---------- context: gap recency + channel affinity ----------
def _touch(ledger, fin, borrower, ch, tpl, when, obligation):
    ledger.start_day(when.date())
    r = ledger.reserve(Instruction(borrower, obligation, "C1", ch, tpl, when, SYNTHETIC_COSTS[ch]))
    assert r.reserved, r.as_dict()
    assert ledger.begin_dispatch(r.instruction_id, when).allowed
    ledger.record_provider_status(r.instruction_id, "ACCEPTED")
    fin.register_execution(r.instruction_id, borrower, "B1", ch, "08-11", when)
    return r.instruction_id


def test_gap_buckets_follow_days_since_last_executed_touch(ledger):
    ctx = BorrowerContextModel(ledger, recency="gap")
    assert set(ctx.multipliers()) == set(GAP_BUCKETS)
    assert ctx.bucket_for("NOBODY", at(9)) == "new|g-"
    fin = OutcomeFinalizer(ledger, ContextualLearner(BetaBernoulliLearner(ledger), ctx))
    ob = add_loan(ledger, "P1", D, campaign="C1")
    _touch(ledger, fin, "P1", "whatsapp", "WA_HI", at(10), ob)
    fin.finalize_due(at(11, 0, D + timedelta(days=1)))
    assert ctx.bucket_for("P1", at(9, 0, D + timedelta(days=2))) == "miss1|g2"
    assert ctx.bucket_for("P1", at(9, 0, D + timedelta(days=3))) == "miss1|g3"
    assert ctx.bucket_for("P1", at(9, 0, D + timedelta(days=5))) == "miss1|g4-6"
    assert ctx.bucket_for("P1", at(9, 0, D + timedelta(days=9))) == "miss1|g7+"
    with pytest.raises(ValueError):
        BorrowerContextModel(ledger, recency="weekly")


def test_channel_affinity_prefers_the_channel_the_borrower_replied_on(ledger):
    lr = BetaBernoulliLearner(ledger, priors=PRIORS)
    ctx = BorrowerContextModel(ledger)
    cl = ContextualLearner(lr, ctx, affinity_strength=5)
    plain = ContextualLearner(lr, ctx)
    fin = OutcomeFinalizer(ledger, cl)
    ob = add_loan(ledger, "P1", D, campaign="C1")
    assert cl.channel_affinity("P1", at(9)) == {}                    # no history -> no factor
    wa = _touch(ledger, fin, "P1", "whatsapp", "WA_HI", at(10), ob)
    fin.record_reply("P1", at(11), meaningful=True, channel="whatsapp")
    _touch(ledger, fin, "P1", "sms", "SMS_HI", at(10, 0, D + timedelta(days=3)), ob)
    fin.finalize_due(at(12, 0, D + timedelta(days=4)))
    when = at(9, 0, D + timedelta(days=5))
    aff = cl.channel_affinity("P1", when)
    assert aff["whatsapp"] > 1.0 > aff["sms"]
    assert wa
    # equal arm priors per rupee favour sms (cheaper); affinity flips the choice to whatsapp
    cands = [cand("whatsapp", when), cand("sms", when)]
    assert plain.rank("B1", cands, Mode.PRODUCTION, borrower_id="P1", persist=False, at=when).chosen.channel == "sms"
    r = cl.rank("B1", cands, Mode.PRODUCTION, borrower_id="P1", persist=False, at=when)
    assert r.chosen.channel == "whatsapp" and "affinity" in r.chosen.reason
    with pytest.raises(ValueError):
        ContextualLearner(lr, ctx, affinity_strength=0)


# ---------- replay plumbing ----------
def test_kairos_options_validate_and_report_changes():
    from replay.k09 import KairosOptions
    assert KairosOptions.from_dict(None) == KairosOptions() and KairosOptions().changed() == {}
    assert KairosOptions.from_dict({"decay": 0.9}).changed() == {"decay": 0.9}
    with pytest.raises(ValueError):
        KairosOptions.from_dict({"peek_at_simulator": True})

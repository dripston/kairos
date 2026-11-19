"""K09 borrower-context layer: engagement x recency buckets from the ledger's own history,
multipliers shrunk toward the pooled rate, idempotent updates, ContextualLearner rescoring."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from kairos_backend.borrower_context import BUCKETS, BorrowerContextModel, ContextualLearner
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
    lg = Ledger(policy, tmp_path / "ctx.db")
    lg.upsert_campaign("C1", budget_per_case_paise=10_000, start_date=D, end_date=D + timedelta(days=10))
    lg.upsert_template("WA_HI", "whatsapp", "hi")
    lg.upsert_template("SMS_EN", "sms", "en")
    lg.start_day(D)
    yield lg
    lg.close()


def outcome(iid, status, borrower="P1", t=at(11)):
    return Outcome(iid, borrower, "B1", "whatsapp", "11-14", t, t + timedelta(hours=24), status)


def test_unseen_buckets_are_exactly_one_and_new_borrower_is_new_r0(ledger):
    ctx = BorrowerContextModel(ledger)
    assert set(ctx.multipliers()) == set(BUCKETS)
    assert all(m == pytest.approx(1.0) for m in ctx.multipliers().values())
    assert ctx.bucket_for("NOBODY", at(9)) == "new|r0"


def test_multiplier_shrinks_toward_pool_and_updates_are_idempotent(ledger):
    ctx = BorrowerContextModel(ledger)
    for i in range(40):
        assert ctx.apply_outcome(outcome(f"N{i}", "NO_REPLY", borrower=f"Q{i}"))
    assert ctx.apply_outcome(outcome("N0", "NO_REPLY", borrower="Q0")) is False   # once per touch
    assert ctx.apply_outcome(outcome("U1", "UNRESOLVED")) is False                # never learned
    m = ctx.multipliers()
    # all 40 misses sit in new|r0 (the bucket at execution time). Bucket mean is centred on the pooled
    # rate with strength 30: (pooled*30 + 0) / (30 + 40) -> multiplier 30/70. Unseen buckets stay 1.0.
    assert m["new|r0"] == pytest.approx(30 / 70)
    assert m["replied|r0"] == pytest.approx(1.0) and m["miss2+|r2+"] == pytest.approx(1.0)
    row = {e["bucket"]: e for e in ctx.estimates()}["new|r0"]
    assert (row["no_replies"], row["n_obs"]) == (40, 40)


def test_history_moves_borrower_to_miss_and_replied_buckets(ledger):
    lr = BetaBernoulliLearner(ledger)
    ctx = BorrowerContextModel(ledger)
    fin = OutcomeFinalizer(ledger, ContextualLearner(lr, ctx))
    add_loan(ledger, "P1", D, campaign="C1")
    r = ledger.reserve(Instruction("P1", "P1-A-1", "C1", "whatsapp", "WA_HI", at(10), SYNTHETIC_COSTS["whatsapp"]))
    assert ledger.begin_dispatch(r.instruction_id, at(10)).allowed
    ledger.record_provider_status(r.instruction_id, "ACCEPTED")
    fin.register_execution(r.instruction_id, "P1", "B1", "whatsapp", "08-11", at(10))
    assert ctx.bucket_for("P1", at(12)) == "new|r1"           # executed, not finalized yet
    fin.finalize_due(at(11, 0, D + timedelta(days=1)))
    assert ctx.bucket_for("P1", at(12, 0, D + timedelta(days=1))) == "miss1|r1"
    assert ctx.bucket_for("P1", at(12, 0, D + timedelta(days=8))) == "miss1|r0"   # older than 7 days


def test_contextual_learner_rescales_and_can_turn_send_into_wait(ledger):
    lr = BetaBernoulliLearner(ledger, priors={("whatsapp", "11-14"): (0.05, 20)})
    ctx = BorrowerContextModel(ledger)
    cl = ContextualLearner(lr, ctx)
    cands = [Candidate("whatsapp", "11-14", at(11), "WA_HI", SYNTHETIC_COSTS["whatsapp"])]
    r0 = cl.rank("B1", cands, Mode.PRODUCTION, borrower_id="P1", persist=False, at=at(11))
    assert r0.action == "SEND" and "new|r0" in r0.reason
    # many misses in one bucket, replies elsewhere: that bucket's multiplier drops below 1
    with ledger.tx() as c:
        c.execute("INSERT INTO learner_context (bucket, alpha, beta, n_obs) VALUES ('new|r0', ?, ?, 600)",
                  (ctx.prior[0], ctx.prior[1] + 600))
        c.execute("INSERT INTO learner_context (bucket, alpha, beta, n_obs) VALUES ('replied|r0', ?, ?, 100)",
                  (ctx.prior[0] + 60, ctx.prior[1] + 40))
    assert ctx.multiplier("new|r0") < 0.5
    r1 = cl.rank("B1", cands, Mode.PRODUCTION, borrower_id="P1", persist=False, at=at(11))
    assert r1.action == "WAIT" and r1.scores[0].p_used < lr.min_reply_prob
    assert r1.scores[0].replies_per_inr == pytest.approx(r1.scores[0].p_used / 0.40)
    assert "k09-ctx-v1" in cl.model_version

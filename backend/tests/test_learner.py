"""K09 learner: seeded priors, pooled fallback, replies-per-rupee ranking, WAIT, exploration gating,
persistence and idempotent updates."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

import pytest

from kairos_backend.contracts import MODEL_FAMILY, SYNTHETIC_COSTS, Candidate, Mode, Outcome
from kairos_backend.learner import SIMULATOR_PRIORS, BetaBernoulliLearner
from kairos_backend.ledger import Ledger

T0 = datetime(2026, 11, 20, 11, 30)


@pytest.fixture
def ledger(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "k09.db")
    yield lg
    lg.close()


def cand(ch, slot="11-14", cost=None, at=T0, tpl=None):
    return Candidate(ch, slot, at, tpl, SYNTHETIC_COSTS[ch] if cost is None else cost)


def outcome(iid, status, band="B1", ch="sms", slot="11-14", borrower="B1"):
    return Outcome(iid, borrower, band, ch, slot, T0, T0 + timedelta(hours=24), status)


def all_candidates(slot="11-14"):
    return [cand(ch, slot) for ch in ("sms", "whatsapp", "email", "ivr", "voicebot")]


# ---------- priors ----------
def test_seeded_priors_are_deterministic(policy, tmp_path):
    a = BetaBernoulliLearner(Ledger(policy, tmp_path / "a.db"))
    b = BetaBernoulliLearner(Ledger(policy, tmp_path / "b.db"))
    assert a.estimates() == b.estimates()
    assert a.model_version == b.model_version
    assert a.model_version.startswith(MODEL_FAMILY + "/") and a.model_version.endswith("@0")
    ra = a.rank("B1", all_candidates("17-19"), Mode.PRODUCTION, persist=False)
    rb = b.rank("B1", all_candidates("17-19"), Mode.PRODUCTION, persist=False)
    assert ra.as_dict() == rb.as_dict()
    # prior alpha = mean * strength, beta = (1 - mean) * strength; untouched arms sit at the prior
    est = {(e["band"], e["channel"], e["slot"]): e for e in a.estimates()}
    wa = est[("B1", "whatsapp", "17-19")]
    mean, strength = SIMULATOR_PRIORS[("whatsapp", "17-19")]
    assert wa["alpha"] == pytest.approx(mean * strength) and wa["beta"] == pytest.approx((1 - mean) * strength)
    assert wa["source"] == "prior" and wa["n_obs"] == 0 and wa["prior_provenance"] == "SYNTHETIC"
    assert est[("B1", "whatsapp", "17-19")]["mean"] > est[("B1", "whatsapp", "11-14")]["mean"]
    assert len(est) == 5 * 5 * 4  # every band x channel x slot is reported


def test_different_priors_give_a_different_model_version(ledger):
    a = BetaBernoulliLearner(ledger)
    b = BetaBernoulliLearner(ledger, priors={("sms", "11-14"): (0.5, 10)})
    assert a.model_version != b.model_version
    with pytest.raises(ValueError):
        BetaBernoulliLearner(ledger, priors={("sms", "11-14"): (1.5, 10)})


# ---------- pooled fallback ----------
def test_pooled_fallback_for_sparse_segment_then_segment(ledger):
    lr = BetaBernoulliLearner(ledger)  # default min_segment_obs = 30
    a0, b0 = lr.prior("sms", "11-14")
    for i in range(10):
        assert lr.apply_outcome(outcome(f"I{i}", "REPLY" if i < 4 else "NO_REPLY", band="B2"))
    # B1 has no data of its own: pooled from B2's 10 outcomes
    s = lr.rank("B1", [cand("sms")], Mode.PRODUCTION, persist=False).scores[0]
    assert (s.source, s.n_obs) == ("pooled", 10)
    assert (s.alpha, s.beta) == (pytest.approx(a0 + 4), pytest.approx(b0 + 6))
    # B2 itself is still sparse (10 < 30) -> pooled too
    assert lr.rank("B2", [cand("sms")], Mode.PRODUCTION, persist=False).scores[0].source == "pooled"
    # an arm with no observations anywhere stays at its prior
    p = lr.rank("B1", [cand("email")], Mode.PRODUCTION, persist=False).scores[0]
    assert (p.source, p.n_obs) == ("prior", 0)

    for i in range(10, 30):
        lr.apply_outcome(outcome(f"I{i}", "NO_REPLY", band="B2"))
    seg = lr.rank("B2", [cand("sms")], Mode.PRODUCTION, persist=False).scores[0]
    assert (seg.source, seg.n_obs) == ("segment", 30)
    assert (seg.alpha, seg.beta) == (pytest.approx(a0 + 4), pytest.approx(b0 + 26))
    # B1 still borrows the pooled channel x slot posterior
    assert lr.rank("B1", [cand("sms")], Mode.PRODUCTION, persist=False).scores[0].source == "pooled"


def test_pooled_combines_all_bands(ledger):
    lr = BetaBernoulliLearner(ledger, min_segment_obs=5)
    a0, b0 = lr.prior("whatsapp", "08-11")
    lr.apply_outcome(outcome("X1", "REPLY", band="B1", ch="whatsapp", slot="08-11"))
    lr.apply_outcome(outcome("X2", "NO_REPLY", band="B3", ch="whatsapp", slot="08-11"))
    s = lr.rank("B4", [cand("whatsapp", "08-11")], Mode.PRODUCTION, persist=False).scores[0]
    assert (s.source, s.n_obs, s.alpha, s.beta) == ("pooled", 2, pytest.approx(a0 + 1), pytest.approx(b0 + 1))


# ---------- ranking ----------
def test_brief_example_whatsapp_beats_sms_per_rupee(ledger):
    """SMS 8% / INR 0.20 = 0.40 replies per INR vs WhatsApp 25% / INR 0.50 = 0.50 -> WhatsApp."""
    lr = BetaBernoulliLearner(ledger, priors={("sms", "11-14"): (0.08, 20), ("whatsapp", "11-14"): (0.25, 20)})
    r = lr.rank("B1", [cand("sms", cost=0.20), cand("whatsapp", cost=0.50)], Mode.PRODUCTION, persist=False)
    by = {s.channel: s for s in r.scores}
    assert by["sms"].replies_per_inr == pytest.approx(0.40)
    assert by["whatsapp"].replies_per_inr == pytest.approx(0.50)
    assert r.action == "SEND" and r.chosen.channel == "whatsapp"
    assert by["sms"].p_used > lr.min_reply_prob  # sms also qualified; it simply lost on replies per rupee


def test_wait_when_all_below_threshold(ledger):
    lr = BetaBernoulliLearner(ledger, priors={("email", "11-14"): (0.01, 20), ("sms", "11-14"): (0.02, 20)})
    r = lr.rank("B1", [cand("email"), cand("sms")], Mode.PRODUCTION, persist=False)
    assert r.action == "WAIT" and r.chosen is None
    assert "min_reply_prob" in r.reason
    assert all(not s.qualifies and "below min_reply_prob" in s.reason for s in r.scores)


def test_wait_when_cost_is_zero(ledger):
    lr = BetaBernoulliLearner(ledger)
    r = lr.rank("B1", [cand("whatsapp", cost=0.0), cand("sms", cost=-1.0)], Mode.PRODUCTION, persist=False)
    assert r.action == "WAIT" and r.chosen is None
    assert all(s.replies_per_inr == 0.0 and not s.qualifies for s in r.scores)
    assert "cost" in r.scores[0].reason


def test_wait_when_no_candidates(ledger):
    lr = BetaBernoulliLearner(ledger)
    r = lr.rank("B1", [], Mode.PRODUCTION, borrower_id="B1", at=T0)
    assert r.action == "WAIT" and r.chosen is None and r.scores == []
    assert "no eligible candidates" in r.reason
    assert r.decision_id is not None  # WAIT decisions are persisted too


def test_tie_break_is_deterministic_and_order_independent(ledger):
    lr = BetaBernoulliLearner(ledger, priors={("sms", "11-14"): (0.10, 20), ("whatsapp", "11-14"): (0.20, 20),
                                              ("sms", "08-11"): (0.10, 20)})
    cands = [cand("whatsapp", cost=0.40), cand("sms", "11-14", cost=0.20), cand("sms", "08-11", cost=0.20)]
    r1 = lr.rank("B1", cands, Mode.PRODUCTION, persist=False)
    r2 = lr.rank("B1", list(reversed(cands)), Mode.PRODUCTION, persist=False)
    # all three are 0.5 replies per INR: sms beats whatsapp (channel order), 08-11 beats 11-14 (slot order)
    assert (r1.chosen.channel, r1.chosen.slot) == ("sms", "08-11")
    assert r1.as_dict() == r2.as_dict()


def test_production_never_samples_even_with_rng(ledger):
    lr = BetaBernoulliLearner(ledger)
    rng = random.Random(7)
    state = rng.getstate()
    r = lr.rank("B1", all_candidates(), Mode.PRODUCTION, rng=rng, persist=False)
    assert rng.getstate() == state  # the rng was never consulted
    assert all(s.sampled_p is None and s.p_used == s.posterior_mean for s in r.scores)


def test_exploration_needs_an_rng(ledger):
    lr = BetaBernoulliLearner(ledger)
    r = lr.rank("B1", all_candidates(), Mode.SIMULATOR, rng=None, persist=False)
    assert all(s.sampled_p is None for s in r.scores)


@pytest.mark.parametrize("mode", [Mode.SANDBOX, Mode.SIMULATOR])
def test_thompson_same_seed_same_ranking(ledger, mode):
    lr = BetaBernoulliLearner(ledger)
    r1 = lr.rank("B1", all_candidates(), mode, rng=random.Random(42), persist=False)
    r2 = lr.rank("B1", all_candidates(), mode, rng=random.Random(42), persist=False)
    assert r1.as_dict() == r2.as_dict()
    assert all(s.sampled_p is not None and s.p_used == s.sampled_p for s in r1.scores)
    # candidate order does not change the draws
    r3 = lr.rank("B1", list(reversed(all_candidates())), mode, rng=random.Random(42), persist=False)
    assert r3.as_dict() == r1.as_dict()


def test_thompson_different_seeds_may_differ(ledger):
    # two weak, equally good arms (0.5 replies per INR each): exploration should pick both over seeds
    lr = BetaBernoulliLearner(ledger, priors={("sms", "11-14"): (0.10, 4), ("whatsapp", "11-14"): (0.20, 4)})
    cands = [cand("sms", cost=0.20), cand("whatsapp", cost=0.40)]
    runs = [lr.rank("B1", cands, Mode.SIMULATOR, rng=random.Random(seed), persist=False) for seed in range(30)]
    assert {r.chosen.channel for r in runs if r.action == "SEND"} == {"sms", "whatsapp"}
    draws = {lr.rank("B1", cands, Mode.SIMULATOR, rng=random.Random(seed), persist=False).scores[0].sampled_p
             for seed in (1, 2)}
    assert len(draws) == 2


# ---------- persistence / model version ----------
def test_decision_scores_persisted_and_model_version_changes(ledger):
    lr = BetaBernoulliLearner(ledger)
    v0 = lr.model_version
    r = lr.rank("B1", all_candidates(), Mode.PRODUCTION, borrower_id="B1")
    assert r.model_version == v0 and r.decision_id
    row = dict(ledger._conn().execute("SELECT * FROM decision_scores WHERE decision_id=?", (r.decision_id,)).fetchone())
    assert row["model_version"] == v0 and row["mode"] == "PRODUCTION" and row["borrower_id"] == "B1"
    assert row["action"] == "SEND" and (row["chosen_channel"], row["chosen_slot"]) == (r.chosen.channel, r.chosen.slot)
    assert row["band"] == "B1" and row["reason"] == r.reason
    assert len(json.loads(row["scores_json"])) == 5

    # the same decision again -> same decision_id, still one row (insert-or-ignore)
    again = lr.rank("B1", all_candidates(), Mode.PRODUCTION, borrower_id="B1")
    assert again.decision_id == r.decision_id
    assert ledger._conn().execute("SELECT COUNT(*) FROM decision_scores").fetchone()[0] == 1

    assert lr.apply_outcome(outcome("I1", "REPLY"))
    v1 = lr.model_version
    assert v1 != v0 and v1.endswith("@1") and v1.split("@")[0] == v0.split("@")[0]
    r2 = lr.rank("B1", all_candidates(), Mode.PRODUCTION, borrower_id="B1")
    assert r2.model_version == v1 and r2.decision_id != r.decision_id
    assert ledger._conn().execute("SELECT COUNT(*) FROM decision_scores").fetchone()[0] == 2


def test_persist_false_writes_nothing(ledger):
    lr = BetaBernoulliLearner(ledger)
    r = lr.rank("B1", all_candidates(), Mode.PRODUCTION, borrower_id="B1", persist=False)
    assert r.decision_id is None
    assert ledger._conn().execute("SELECT COUNT(*) FROM decision_scores").fetchone()[0] == 0


# ---------- updates ----------
def test_apply_outcome_is_idempotent(ledger):
    lr = BetaBernoulliLearner(ledger)
    a0, b0 = lr.prior("sms", "11-14")
    assert lr.apply_outcome(outcome("I1", "REPLY")) is True
    assert lr.apply_outcome(outcome("I1", "REPLY")) is False
    assert lr.apply_outcome(outcome("I1", "NO_REPLY")) is False  # same instruction, different claim: still once
    assert lr.apply_outcome(outcome("I2", "NO_REPLY")) is True
    arm = dict(ledger._conn().execute("SELECT * FROM learner_arms WHERE band='B1' AND channel='sms' AND slot='11-14'").fetchone())
    assert (arm["alpha"], arm["beta"], arm["n_obs"]) == (pytest.approx(a0 + 1), pytest.approx(b0 + 1), 2)
    assert lr.n_updates == 2


@pytest.mark.parametrize("status", ["PENDING", "UNRESOLVED"])
def test_pending_and_unresolved_are_not_counted(ledger, status):
    lr = BetaBernoulliLearner(ledger)
    before, v0 = lr.estimates("B1"), lr.model_version
    assert lr.apply_outcome(outcome("I1", status)) is False
    assert lr.estimates("B1") == before and lr.model_version == v0
    assert ledger._conn().execute("SELECT COUNT(*) FROM learner_updates").fetchone()[0] == 0
    # the touch can still be counted once it is finalized
    assert lr.apply_outcome(outcome("I1", "NO_REPLY")) is True


def test_estimates_before_and_after(ledger):
    lr = BetaBernoulliLearner(ledger, min_segment_obs=1)
    before = {(e["channel"], e["slot"]): e for e in lr.estimates("B1")}
    lr.apply_outcome(outcome("I1", "REPLY", ch="whatsapp", slot="17-19"))
    after = {(e["channel"], e["slot"]): e for e in lr.estimates("B1")}
    assert after[("whatsapp", "17-19")]["mean"] > before[("whatsapp", "17-19")]["mean"]
    assert after[("whatsapp", "17-19")]["source"] == "segment" and after[("whatsapp", "17-19")]["n_obs"] == 1
    assert after[("sms", "11-14")] == before[("sms", "11-14")]

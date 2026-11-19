"""Logged choice probabilities (learner.thompson_propensities) and off-policy estimators (kairos_backend/ope.py)."""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from kairos_backend.contracts import Candidate, Mode
from kairos_backend.learner import WAIT_ACTION, BetaBernoulliLearner, arm_key
from kairos_backend.ledger import Ledger
from kairos_backend.ope import WAIT, estimate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ope_k09  # noqa: E402

T0 = datetime(2026, 11, 20, 8, 0)
COST = {"sms": 25, "whatsapp": 40, "email": 5, "ivr": 150}


def cands():
    return [Candidate(ch, slot, T0, None, COST[ch]) for ch in ("sms", "whatsapp", "ivr") for slot in ("08-11", "17-19")]


@pytest.fixture
def learner(policy, tmp_path):
    lg = Ledger(policy, tmp_path / "ope.db")
    yield BetaBernoulliLearner(lg, propensity_draws=600)
    lg.close()


def test_thompson_propensities_are_valid_probabilities(learner):
    r = learner.rank("B1", cands(), Mode.SIMULATOR, rng=random.Random(1), borrower_id="X", persist=False, at=T0)
    assert r.propensity_kind == "thompson_mc"
    assert 0.0 < r.propensity <= 1.0
    assert set(r.arm_probs) == {arm_key(c) for c in cands()} | {WAIT_ACTION}
    assert all(0.0 < p <= 1.0 for p in r.arm_probs.values())
    assert sum(r.arm_probs.values()) == pytest.approx(1.0, abs=1e-9)
    assert r.propensity == r.arm_probs[arm_key(r.chosen)]


def test_greedy_logs_propensity_one_and_production_never_samples(learner):
    for mode, rng in ((Mode.SIMULATOR, None), (Mode.PRODUCTION, random.Random(1))):
        r = learner.rank("B1", cands(), mode, rng=rng, borrower_id="X", persist=False, at=T0)
        assert (r.propensity, r.propensity_kind, r.arm_probs) == (1.0, "greedy", None)
        assert all(s.sampled_p is None for s in r.scores)


def test_logging_is_off_by_default_and_never_changes_the_choice(policy, tmp_path):
    plain = BetaBernoulliLearner(Ledger(policy, tmp_path / "a.db"))
    logged = BetaBernoulliLearner(Ledger(policy, tmp_path / "b.db"), propensity_draws=300)
    assert plain.model_version == logged.model_version          # logging is not part of the model hash
    for seed in range(25):
        a = plain.rank("B2", cands(), Mode.SIMULATOR, rng=random.Random(seed), persist=False, at=T0)
        b = logged.rank("B2", cands(), Mode.SIMULATOR, rng=random.Random(seed), persist=False, at=T0)
        assert a.propensity_kind == "thompson_unlogged" and a.propensity is None
        assert (a.chosen, [s.sampled_p for s in a.scores]) == (b.chosen, [s.sampled_p for s in b.scores])


def test_propensities_are_deterministic_and_match_thompson_frequencies(policy, tmp_path):
    l1 = BetaBernoulliLearner(Ledger(policy, tmp_path / "a.db"), propensity_draws=4000)
    l2 = BetaBernoulliLearner(Ledger(policy, tmp_path / "b.db"), propensity_draws=4000)
    r1 = l1.rank("B1", cands(), Mode.SIMULATOR, rng=random.Random(3), persist=False, at=T0)
    r2 = l2.rank("B1", cands(), Mode.SIMULATOR, rng=random.Random(99), persist=False, at=T0)
    assert r1.arm_probs == r2.arm_probs                          # seeded from the posterior table, not the decision rng
    freq = Counter()
    n = 3000
    for seed in range(n):
        r = l1.rank("B1", cands(), Mode.SIMULATOR, rng=random.Random(10_000 + seed), persist=False, at=T0)
        freq[arm_key(r.chosen) if r.chosen else WAIT_ACTION] += 1
    for k, p in r1.arm_probs.items():
        assert freq[k] / n == pytest.approx(p, abs=0.03), k


def test_propensity_is_persisted_with_the_decision(learner):
    r = learner.rank("B1", cands(), Mode.SIMULATOR, rng=random.Random(5), borrower_id="BR-1", persist=True, at=T0)
    row = learner.ledger._conn().execute(
        "SELECT propensity, propensity_kind, arm_probs_json FROM decision_scores WHERE decision_id=?",
        (r.decision_id,)).fetchone()
    assert row["propensity"] == pytest.approx(r.propensity) and row["propensity_kind"] == "thompson_mc"
    assert json.loads(row["arm_probs_json"]) == pytest.approx(r.arm_probs)


# ---------- estimators ----------
MEANS = {"a": 0.2, "b": 0.5, "c": 0.1}
LOGGER = {"a": 0.5, "b": 0.3, "c": 0.2}


def _bandit_log(rng, n):
    rows = []
    keys = list(LOGGER)
    for _ in range(n):
        a = rng.choices(keys, weights=[LOGGER[k] for k in keys])[0]
        rows.append({"actions": keys, "chosen": a, "propensity": LOGGER[a], "reward": int(rng.random() < MEANS[a]),
                     "q_hat": {k: 0.3 for k in keys}, "cost": {"a": 40, "b": 150, "c": 25}})
    return rows


def test_ips_is_unbiased_on_a_tiny_bandit_with_known_values():
    target = lambda row: {"b": 1.0}   # noqa: E731   true value 0.5
    rng = random.Random(7)
    vals = [estimate(_bandit_log(rng, 400), target, n_boot=0)["ips"]["value"] for _ in range(300)]
    mean = sum(vals) / len(vals)
    se = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 / len(vals) ** 0.5
    assert abs(mean - 0.5) < 3 * se + 1e-3
    one = estimate(_bandit_log(random.Random(1), 2000), target, n_boot=300, seed=1)
    for k in ("ips", "snips", "dr"):
        lo, hi = one[k]["ci"]
        assert lo <= 0.5 <= hi, k
    assert one["dm"]["value"] == pytest.approx(0.3)            # the deliberately wrong model alone is biased


def test_wait_has_known_zero_reward_and_snips_normalises_for_it():
    target = lambda row: {"b": 0.5, WAIT: 0.5}   # noqa: E731   true value 0.25
    out = estimate(_bandit_log(random.Random(3), 4000), target, n_boot=200, seed=3)
    for k in ("ips", "snips", "dr"):
        assert out[k]["value"] == pytest.approx(0.25, abs=0.04), k
    assert out["target_cost_paise_per_decision"] == pytest.approx(75.0)


def test_estimate_rejects_invalid_propensities():
    rows = _bandit_log(random.Random(0), 5)
    rows[0]["propensity"] = 0.0
    with pytest.raises(ValueError):
        estimate(rows, lambda row: {"a": 1.0}, n_boot=0)


# ---------- the harness ----------
def test_ope_log_is_deterministic_and_consistent(tmp_path):
    runs = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        runs.append(ope_k09.run_log(80, 3, 5, 200, str(tmp_path / name), 0.05))
    (rows_a, mismatch, inconsistent), (rows_b, _, _) = runs
    assert rows_a and mismatch == 0 and inconsistent == 0
    assert rows_a == rows_b
    for r in rows_a:
        assert 0.0 < r["propensity"] <= 1.0
        assert sum(r["arm_probs"].values()) == pytest.approx(1.0, abs=1e-9)

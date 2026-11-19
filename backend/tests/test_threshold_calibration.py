"""Learn-then-Test threshold calibration (scripts/calibrate_thresholds.py, kairos_backend/calibration.py)."""
from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path

import pytest

from kairos_backend import intent_stack as ist
from kairos_backend.calibration import binom_cdf, grid, ltt_fixed_sequence, pvalue_binomial, pvalue_hoeffding
from kairos_backend.intent_stack import HAND_SET, MODEL_ACCEPT, MODEL_NEVER_ALONE, IntentStack, Thresholds, load_thresholds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import calibrate_thresholds as ct  # noqa: E402

TODAY = date(2026, 11, 19)


# ---------- LTT core ----------
def test_binomial_cdf_matches_direct_sum():
    from math import comb
    for n, k, p in ((10, 3, 0.2), (50, 0, 0.05), (200, 7, 0.05), (5, 5, 0.5)):
        direct = sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
        assert binom_cdf(k, n, p) == pytest.approx(direct, rel=1e-9, abs=1e-12)


def test_pvalues_are_monotone_and_hoeffding_is_looser():
    assert pvalue_binomial(0, 100, 0.05) < pvalue_binomial(3, 100, 0.05) < pvalue_binomial(6, 100, 0.05)
    assert pvalue_binomial(0, 0, 0.05) == 1.0 and pvalue_hoeffding(9, 100, 0.05) == 1.0
    assert pvalue_hoeffding(1, 200, 0.05) >= pvalue_binomial(1, 200, 0.05)


def _synthetic_trial(rng, n):
    """confidence c ~ U(0,1); wrong with probability e(c) = 0.3 * (1 - c)^2 (known)."""
    return [(c, rng.random() < 0.3 * (1 - c) ** 2) for c in (rng.random() for _ in range(n))]


def _true_risk(lam):
    # E[e(c) | c >= lam] for c ~ U(0,1): 0.3 * (1 - lam)^2 / 3
    return 0.1 * (1 - lam) ** 2


def test_ltt_guarantee_holds_on_synthetic_data_with_known_error():
    alpha, delta, trials = 0.05, 0.10, 300
    rng = random.Random(123)
    lambdas = grid(0.80, 0.01, 0.01)   # fixed sequence must start where there is data to test
    failures = chosen_n = 0
    for _ in range(trials):
        data = _synthetic_trial(rng, 400)

        def stats(lam, data=data):
            auto = [wrong for c, wrong in data if c >= lam]
            return len(auto), sum(auto)
        lam, _ = ltt_fixed_sequence(lambdas, stats, alpha, delta)
        if lam is not None:
            chosen_n += 1
            failures += _true_risk(lam) > alpha
    assert chosen_n > trials * 0.8                 # it does certify something most of the time
    assert failures / trials <= delta + 0.03       # and the risk bound fails at most ~delta of the time
    # the oracle threshold (true risk == alpha) is ~0.293; LTT stays on the safe side of it
    assert _true_risk(0.293) == pytest.approx(alpha, abs=1e-3)


def test_ltt_returns_none_when_nothing_is_certified():
    lam, trace = ltt_fixed_sequence([0.9, 0.8], lambda lam: (10, 5), 0.05, 0.1)
    assert lam is None and len(trace) == 1 and trace[0]["certified"] is False


# ---------- the script ----------
def test_script_is_deterministic_per_seed(tmp_path):
    outs = []
    for name in ("a.json", "b.json"):
        p = tmp_path / name
        assert ct.main(["--quick", "--seed", "11", "--out", str(p)]) == 0
        d = json.loads(p.read_text())
        d.pop("runtime_seconds")
        outs.append(d)
    assert outs[0] == outs[1]
    assert outs[0]["chosen"]["model_accept"] <= MODEL_NEVER_ALONE
    p = tmp_path / "c.json"
    ct.main(["--quick", "--seed", "12", "--out", str(p)])
    assert json.loads(p.read_text())["data"] == outs[0]["data"]   # same sizes, different split


def test_fast_path_matches_a_real_stack_at_several_thresholds():
    rows = ct.load_rows(15, "both")
    traced = ct.Traced(rows)
    for lam in (0.95, 0.70, 0.55, 0.50):
        stack = IntentStack(thresholds=Thresholds(model_accept=lam, source="test"))
        real = [stack.classify(r["text"], TODAY) for r in rows]
        fast = [res for _, res in traced.at(lam)]
        assert [(r.label, r.tier_used, r.needs_human) for r in real] == \
               [(r.label, r.tier_used, r.needs_human) for r in fast]


def test_safety_intents_do_not_depend_on_the_threshold():
    rows = [r for r in ct.load_rows(30, "both") if r["label"] in ("STOP", "HARDSHIP")]
    assert len(rows) > 40
    for lam in (MODEL_NEVER_ALONE, 0.99, MODEL_ACCEPT, 0.50, 0.30):
        stack = IntentStack(thresholds=Thresholds(model_accept=lam, source="test"))
        res = [stack.classify(r["text"], TODAY) for r in rows]
        assert all(x.label == r["label"] for x, r in zip(res, rows)), lam
    inv = ct.Traced(rows).safety_invariant(grid(0.99, 0.30, 0.01) + [MODEL_NEVER_ALONE])
    assert inv["min_recall_over_grid"] == {"STOP": 1.0, "HARDSHIP": 1.0}
    assert inv["auto_handled_as_non_safety_at_any_threshold"] == 0


# ---------- the flag ----------
def test_default_path_is_hand_set_and_unchanged(monkeypatch):
    monkeypatch.delenv(ist.THRESHOLDS_ENV, raising=False)
    assert load_thresholds() is HAND_SET
    assert load_thresholds({}) is HAND_SET
    assert IntentStack().thresholds is HAND_SET
    assert IntentStack.from_env({}).thresholds is HAND_SET
    assert (HAND_SET.rule_accept, HAND_SET.model_accept, HAND_SET.local_llm_below) == (0.85, 0.70, 0.70)
    assert ist.RULE_ACCEPT == 0.85 and ist.MODEL_ACCEPT == 0.70     # the hand-set constants themselves
    res = IntentStack().classify("kal pakka 5000 bhej dunga", TODAY)
    assert "thresholds" not in res.evidence


def test_flag_reads_the_calibration_file(tmp_path):
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"chosen": {"model_accept": 0.55}}))
    th = load_thresholds({ist.THRESHOLDS_ENV: "calibrated"}, path=p)
    assert th.model_accept == 0.55 and th.rule_accept == 0.85 and th.source.startswith("calibrated:")
    res = IntentStack(thresholds=th).classify("kal pakka 5000 bhej dunga", TODAY)
    assert res.evidence["thresholds"] == th.source


@pytest.mark.parametrize("content", [None, "not json", json.dumps({"chosen": {"model_accept": 7}})])
def test_flag_fails_closed_without_a_valid_file(tmp_path, content):
    p = tmp_path / "cal.json"
    if content is not None:
        p.write_text(content)
    th = load_thresholds({ist.THRESHOLDS_ENV: "calibrated"}, path=p)
    assert th.model_accept == MODEL_NEVER_ALONE and "fail-closed" in th.source
    assert load_thresholds({ist.THRESHOLDS_ENV: "bogus"}).model_accept == MODEL_NEVER_ALONE

"""K09 RL v2 (kairos_backend/rl): empirical-Bayes hierarchy, value terms, dual pacing, lookahead /
stopping, warm start, and the replay policy (deterministic, cap-safe, baseline untouched)."""
from __future__ import annotations

import json
import tempfile

import pytest

from kairos_backend.rl.hier import KAPPA_MAX, Level, mom_kappa, multipliers
from kairos_backend.rl.lookahead import contacts_left, continuation_value, wait_is_better
from kairos_backend.rl.pacing import DualPacer
from kairos_backend.rl.policy import RL2Options
from kairos_backend.rl.value import Rec, ValueModel, trans_key
from kairos_backend.rl.warmstart import export_posteriors
from replay.k09 import run_comparison


def rec(i, reply=0, bid="B0", ch="whatsapp", slot="17-19", day=100, cohort="C1", region="MH", r=0, prev=None,
        paid=0, stop=0, p_arm=0.1, band="B1"):
    return Rec(f"I{i}", bid, day, band, ch, slot, cohort, region, "CMP", r, prev, reply, paid, stop, p_arm, p_arm)


# ---------- hierarchy ----------
def test_kappa_collapses_when_groups_do_not_differ():
    groups = {g: (10.0, 10.0) for g in range(8)}
    assert mom_kappa(groups) == KAPPA_MAX
    k, m = multipliers(groups)
    assert all(abs(v - 1.0) < 1e-6 for v in m.values())


def test_kappa_small_when_groups_really_differ():
    groups = {0: (40.0, 10.0), 1: (2.0, 10.0), 2: (10.0, 10.0), 3: (25.0, 10.0), 4: (1.0, 10.0)}
    k = mom_kappa(groups)
    assert k < 5
    _, m = multipliers(groups)
    assert m[0] > 2 and m[4] < 0.5


def test_group_without_evidence_is_its_parent_and_formula():
    lv = Level("x", lambda r: r.bid, kappa=4.0)
    lv.fit([("a", 3.0, 1.0)])
    assert lv.get("unseen") == 1.0
    assert lv.get("a") == pytest.approx((3 + 4) / (1 + 4))


# ---------- value terms ----------
def test_reply_prediction_is_the_product_of_levels_and_borrower_counts_shrink():
    mdl = ValueModel(arm_decay=1.0, pop_decay=1.0)
    # borrower B1 replies a lot, B2 never: the borrower level separates them, region / cohort stay ~1
    recs = [rec(i, reply=int(i % 2 == 0), bid="B1") for i in range(10)]
    recs += [rec(100 + i, reply=0, bid=f"N{i % 30}") for i in range(90)]
    for r in recs:
        mdl.add(r)
    mdl.refresh(101)
    probe = rec(0, bid="B1")
    manual = mdl.p_arm("B1", "whatsapp", "17-19")
    for lv in mdl.reply_levels:
        manual *= lv.get(lv.keyfn(probe))
    assert mdl.p_reply(probe) == pytest.approx(min(manual, 0.95))
    assert mdl.borrower.get("B1") > 1.5 > 1.0 > mdl.borrower.get("N0")
    o, e, k = mdl.borrower_evidence("B1")
    assert o == 5 and e > 0 and k > 0


def test_pay_and_stop_terms_learn_from_outcomes():
    mdl = ValueModel(arm_decay=1.0, pop_decay=1.0, pay_prior=(0.25, 4.0), stop_prior=(0.05, 4.0))
    for i in range(40):
        mdl.add(rec(i, reply=1, paid=int(i < 30), bid=f"B{i}"))
    for i in range(40):
        mdl.add(rec(100 + i, reply=0, stop=int(i < 10), bid=f"S{i}", r=2))
    mdl.refresh(101)
    assert mdl.q_global > 0.6                      # 30 of 40 replies paid
    p = 0.1
    assert mdl.p_stop(rec(0, r=2), p) > mdl.p_stop(rec(0, r=0), p)   # STOPs came after 2 recent touches
    v0 = mdl.value(0.1, 0.5, 0.0, 1.0, alpha=0.0, mu=0.0)
    v1 = mdl.value(0.1, 0.5, 0.01, 2.0, alpha=1.0, mu=5.0)
    assert v0 == pytest.approx(0.1) and v1 == pytest.approx(0.1 * (1 + 1.0) - 0.05)


def test_evidence_decay_follows_drift():
    mdl = ValueModel(arm_decay=0.5, pop_decay=1.0)
    for i in range(60):
        mdl.add(rec(i, reply=1, day=90, bid=f"B{i}"))      # old: always replied
    for i in range(60):
        mdl.add(rec(100 + i, reply=0, day=100, bid=f"C{i}"))  # recent: never
    mdl.refresh(101)
    assert mdl.p_arm("B1", "whatsapp", "17-19") < 0.2


def test_transition_key():
    assert trans_key(None, "sms") == ("first", "sms")
    assert trans_key(("whatsapp", "D", "g2"), "ivr") == ("L2", "D", "ivr")


# ---------- pacing ----------
def test_dual_price_moves_with_spend_and_guard_holds():
    p = DualPacer(mode="dual", eta=0.5, phi=0.5, warmup_touches=0)
    lam0 = p.price(0, [0.004, 0.003, 0.002, 0.001], 0.002, 10, 10, allowance=100, costs_today=[40, 40, 40, 40])
    assert lam0 == pytest.approx(0.002)            # allowance 100 paise runs out at the 3rd best touch (0.002)
    p.update(spent_prev=50, target_prev=100)       # under target -> cheaper money
    assert p.lam_dual < 0.002
    lo = p.lam_dual
    p.update(spent_prev=150, target_prev=100)      # over target -> dearer money
    assert p.lam_dual > lo
    assert p.price(1, [], 0.01, 10, 1) == pytest.approx(0.005)   # last day: spend-down, guard only


def test_floor_mode_is_the_previous_relative_floor():
    p = DualPacer(mode="floor", phi=0.5, warmup_touches=200)
    assert p.price(0, [0.01], 0.002, 199, 10) == 0.0
    assert p.price(1, [0.01], 0.002, 200, 10) == pytest.approx(0.001)


# ---------- lookahead / stopping ----------
def test_wait_when_fatigue_ages_out_and_time_remains():
    assert wait_is_better(v_today=0.064, v_tomorrow=0.08, cost=40, lam=0.0005, days_left=10)
    assert not wait_is_better(v_today=0.064, v_tomorrow=0.08, cost=40, lam=0.0005, days_left=1)
    assert not wait_is_better(v_today=0.08, v_tomorrow=0.08, cost=40, lam=0.0005, days_left=10)


def test_stopping_pauses_silent_borrowers_but_not_responsive_ones():
    k = 2.0
    dead = continuation_value(v_unit=0.05, p_unit=0.05, o=0, e=1.5, kappa=k, cost=40, lam=0.002, k_left=3)
    live = continuation_value(v_unit=0.05, p_unit=0.05, o=2, e=0.4, kappa=k, cost=40, lam=0.002, k_left=3)
    assert dead <= 0 < live
    assert contacts_left(14) == 6 and contacts_left(1) == 1 and contacts_left(0) == 0


# ---------- warm start ----------
def test_warm_start_round_trip_population_only():
    mdl = ValueModel(arm_decay=1.0, pop_decay=1.0)
    for i in range(200):
        mdl.add(rec(i, reply=int(i % 4 == 0), bid=f"B{i % 50}", cohort=f"C{i % 3}", r=i % 3))
    mdl.refresh(101)
    warm = json.loads(json.dumps(export_posteriors(mdl, ess_cap=100, level_ess_cap=20)))
    assert "borrower" not in warm["level_pseudo"] and "borrower x channel" not in warm["level_pseudo"]
    assert all(s <= 100 for _, s in warm["arm_prior"].values())
    assert all(e <= 20 + 1e-6 for rows in warm["level_pseudo"].values() for _, (_, e) in rows)
    cold, hot = ValueModel(), ValueModel(warm=warm)
    cold.refresh(1)
    hot.refresh(1)
    assert hot.p_arm("B1", "whatsapp", "17-19") != cold.p_arm("B1", "whatsapp", "17-19")
    assert hot.p_arm("B1", "whatsapp", "17-19") == pytest.approx(0.25, abs=0.03)


# ---------- the replay policy ----------
def test_options_validated():
    with pytest.raises(ValueError):
        RL2Options.from_dict({"nope": 1})
    with pytest.raises(ValueError):
        run_comparison(10, 1, 1, workdir=tempfile.mkdtemp(), policy="nope")


def test_rl2_replay_is_deterministic_cap_safe_and_leaves_the_baseline_alone():
    kw = dict(explore=False, policy="rl2",
              rl2_opts={"pacing": "dual", "alpha": 1.0, "mu": 2.0, "lookahead": True, "stopping": True})
    a = run_comparison(120, 5, 11, workdir=tempfile.mkdtemp(), drift_day=2, **kw)
    b = run_comparison(120, 5, 11, workdir=tempfile.mkdtemp(), drift_day=2, **kw)
    plain = run_comparison(120, 5, 11, workdir=tempfile.mkdtemp(), drift_day=2, explore=False)
    for key in ("envelope_hash", "meaningful_replies", "spend_paise", "verified_payments", "daily", "decisions"):
        assert a["kairos"][key] == b["kairos"][key]
        assert a["baseline"][key] == plain["baseline"][key]           # CN10 untouched by the policy option
    assert a["kairos"]["cap_breaches_k01"] == 0 and not a["kairos"]["k15_safety_violations"]
    assert a["kairos"]["spend_paise"] <= a["matched"]["budget_pool_paise"]
    assert a["kairos"]["model_version"].startswith("k09-rl2-v1/")
    lv = a["rl2"]["levels"]
    assert lv["reply"][0]["level"] == "band x channel x slot" and lv["layers"]["mermaid"].startswith("flowchart")
    assert a["rl2"]["levels"] == b["rl2"]["levels"]

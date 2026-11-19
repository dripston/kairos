"""k01.gate conformance: Gate.evaluate() walks exactly the declared check order.

Seeded random Facts. For each, the machine is walked rule by rule (each rule's own predicate gives
the HIT / PASS event); the terminal state and the rule it stopped at must equal evaluate()'s decision
and rule. Every declared transition must be exercised across the seeds.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pytest

from kairos_backend import gate
from kairos_backend.gate import Facts, evaluate
from kairos_backend.machines import gate_machine
from tests.fsm.world import Coverage

SEEDS = range(12)
N = 600


def random_facts(rng: random.Random, policy) -> Facts:
    day = date(2026, 11, 16) + timedelta(days=rng.randrange(14))  # includes Sundays
    at = datetime(day.year, day.month, day.day, rng.randrange(6, 22), rng.choice((0, 30, 59)))
    p = lambda x: rng.random() < x  # noqa: E731
    return Facts(
        at=at, channel=rng.choice(list(policy.raw["channels"])),
        dpd=None if p(0.03) else rng.choice([-9, -7, -6, -3, 0, 1, 5, 12, 31, 45, 61, 90, 91, 120, 400]),
        dpd_age_hours=rng.choice([0.0, 3.0, 23.9, 24.0, 24.1, 40.0]) if p(0.1) else 1.0,
        consent=not p(0.05), opted_out=p(0.04),
        hardship_until=(at + timedelta(hours=rng.choice([-5, 5]))) if p(0.06) else None,
        hardship_supervisor=p(0.5), callback_pending=p(0.04), supervisor_hold=p(0.04),
        ptp_date=(at.date() + timedelta(days=rng.choice([-1, 0, 2]))) if p(0.06) else None,
        obligation_cleared=p(0.04), stop_for_day=p(0.05),
        sends_today_channel=rng.choice([0, 0, 0, 1, 2, 3, 5]), sends_today_all=rng.choice([0, 0, 1, 3, 4, 6]),
        sends_week_channel=rng.choice([0, 0, 2, 7, 12]), sends_week_all=rng.choice([0, 0, 3, 9, 20, 30]),
        mins_since_last_same_channel=None if p(0.7) else rng.choice([10.0, 60.0, 119.0, 121.0, 400.0]),
        lender_id=rng.choice([None, None, "EXAMPLE"]), template_approved=not p(0.05),
        budget_remaining_inr=rng.choice([1e9, 1e9, 0.5, 0.0]), est_cost_inr=rng.choice([0.2, 1.0, 3.5]),
        campaign_target_met=p(0.04), purpose=rng.choice([None, None, "reminder", "collection"]),
        verified_overdue=not p(0.1),
    )


def walk(m, policy, f: Facts):
    ctx = gate._Ctx(policy, f)
    state, path = m.step("PRE_GATE", "PASS")
    trail = [path]
    while state not in m.terminal:
        rid = state
        hit = gate.RULES[rid](ctx)
        state, t = m.step(rid, "HIT" if hit else "PASS", {"hardship_supervisor": bool(f.hardship_supervisor)})
        trail.append(t)
        if hit:
            assert hit[0] == state, f"{rid} returned {hit[0]} but the machine declares {state}"
            return state, rid, trail
    return state, "ALL_PASSED", trail


@pytest.mark.parametrize("seed", SEEDS)
def test_evaluate_follows_the_declared_check_order(policy, seed):
    m = gate_machine(policy)
    rng = random.Random(seed)
    for _ in range(N):
        f = random_facts(rng, policy)
        state, rule, _ = walk(m, policy, f)
        d = evaluate(policy, f)
        assert (d.decision, d.rule) == (state, rule), f


def test_every_declared_gate_transition_is_exercised(policy):
    m = gate_machine(policy)
    cov = Coverage()
    for seed in SEEDS:
        rng = random.Random(seed)
        for _ in range(N):
            for t in walk(m, policy, random_facts(rng, policy))[2]:
                cov.add(t)
    # the pre-gate CONTROL stop lives in the ledger (tested in test_conformance_touch.py)
    assert cov.missing(m, exempt=[("PRE_GATE", "HIT")]) == []


def test_machine_order_is_the_policy_order(policy):
    m = gate_machine(policy)
    chain, s = [], "PRE_GATE"
    while s not in m.terminal:
        s = m.step(s, "PASS")[0]
        chain.append(s)
    assert chain[:-1] == gate.rule_order(policy) and chain[-1] == "SEND"
    k01 = [r for r in chain if r in policy.evaluation_order]
    assert k01 == list(policy.evaluation_order)  # K01's own order is preserved; K13 extras are slotted in

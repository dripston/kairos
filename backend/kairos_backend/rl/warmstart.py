"""K09 RL v2 cross-cohort warm start ("second campaign onwards").

At cohort close, export the POPULATION-level posteriors (never borrower-level: the next cohort has new
borrowers): the global channel x slot reply means and every population level's (observed, expected)
counts, each scaled down to a capped effective sample size so the next cohort's own evidence overrides
them within days. The next cohort's ValueModel starts from them (rl.value.ValueModel.load_warm).

Deployment-legitimate: it is what a lender's previous campaign left in the ledger. The cold-start
number stays the primary CM-02 claim; the warm start is reported separately as "second campaign onwards".
"""
from __future__ import annotations

from typing import Optional

POP_LEVELS = ("band x channel x slot", "fatigue (touches in previous 6 days)", "channel transition",
              "cohort x channel transition", "cohort", "region (cohort x state)", "pay: cohort", "pay: region",
              "pay: DPD band", "stop: fatigue", "stop: cohort", "stop: region")


def _jsonable(k):
    return list(k) if isinstance(k, tuple) else k


def export_posteriors(model, ess_cap: float = 200.0, level_ess_cap: float = 30.0) -> dict:
    """arm_prior: 'channel|slot' -> (mean, strength <= ess_cap); level_pseudo: level -> [(key, (O, E))]
    with each group's expected count capped at level_ess_cap (O scaled with it, so O/E is kept)."""
    n_arm: dict = {}
    for r in model.records:
        n_arm[(r.ch, r.slot)] = n_arm.get((r.ch, r.slot), 0) + 1
    arm = {}
    for (ch, slot), p in sorted(model.p_global.items()):
        a0, b0 = model.prior.get((ch, slot), (0.1, 1.9))
        arm[f"{ch}|{slot}"] = (round(p, 6), round(min(ess_cap, a0 + b0 + n_arm.get((ch, slot), 0)), 2))
    levels = {}
    for lv in [model.band] + model.reply_levels + model.pay_levels + model.stop_levels:
        if lv.name not in POP_LEVELS:
            continue
        rows = []
        for k, (o, e) in sorted(lv.counts.items(), key=lambda kv: repr(kv[0])):
            if e <= 0:
                continue
            f = min(1.0, level_ess_cap / e)
            rows.append([_jsonable(k), [round(o * f, 4), round(e * f, 4)]])
        levels[lv.name] = rows
    return {"arm_prior": arm, "level_pseudo": levels,
            "pay_prior": [round(model.q_global * min(ess_cap, 50.0), 4), min(ess_cap, 50.0)],
            "stop_prior": [round(model.sigma * min(ess_cap, 50.0), 5), min(ess_cap, 50.0)],
            "ess_cap": ess_cap, "level_ess_cap": level_ess_cap,
            "status": "PROPOSAL: population posteriors from a closed cohort (SIMULATED evidence)"}


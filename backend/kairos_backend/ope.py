"""Off-policy evaluation from logged decisions (stdlib only). Used by scripts/ope_k09.py.

Estimators (per-decision value V(pi) = E_x[ sum_a pi(a|x) q(x, a) ] over the logged contexts):
    IPS    Horvitz-Thompson / inverse propensity scoring:  mean_i  w_i r_i,  w_i = pi(a_i|x_i) / b(a_i|x_i)
    SNIPS  self-normalised IPS:  sum_i w_i r_i / sum_i (w_i + pi(WAIT|x_i))
    DM     direct method:  mean_i  sum_a pi(a|x_i) q_hat(x_i, a)
    DR     doubly robust (Dudik, Langford & Li 2011, arXiv:1103.4601):  DM_i + w_i (r_i - q_hat(x_i, a_i))

The no-contact action WAIT has a KNOWN reward (0 replies, 0 cost), so it needs no importance weight:
w_i is 0 when the log or the target picks WAIT, and SNIPS normalises by w_i + pi(WAIT|x_i), whose
expectation is 1 per row when the logger covers every contact action the target uses. With an adaptive
logger (a learner that updates between decisions) IPS / DR stay unbiased because each b(a|x) is the
probability at decision time given the history (martingale argument; Li et al. WSDM 2011).

Rows are dicts: {"actions": [action ids], "chosen": id, "propensity": b(chosen), "reward": 0/1,
"q_hat": {id: model estimate}, "cost": {id: paise}}. A target is a function row -> {id: prob}.
"""
from __future__ import annotations

import random
from typing import Any, Callable, Dict, List, Sequence

WAIT = "WAIT"
Target = Callable[[Dict[str, Any]], Dict[str, float]]
ESTIMATORS = ("ips", "snips", "dm", "dr")


def contributions(rows: Sequence[Dict[str, Any]], target: Target) -> Dict[str, List[float]]:
    """Per-row pieces every estimator is built from (so the bootstrap only re-sums them)."""
    w, wr, piw, dm, dr, cost = [], [], [], [], [], []
    for row in rows:
        pi = target(row)
        a, b, r = row["chosen"], float(row["propensity"]), float(row["reward"])
        if not (0.0 < b <= 1.0):
            raise ValueError(f"propensity must be in (0, 1], got {b}")
        qh = row["q_hat"]
        wi = pi.get(a, 0.0) / b if a != WAIT else 0.0
        d = sum(p * qh.get(x, 0.0) for x, p in pi.items() if x != WAIT)
        w.append(wi)
        wr.append(wi * r)
        piw.append(pi.get(WAIT, 0.0))
        dm.append(d)
        dr.append(d + (wi * (r - qh.get(a, 0.0)) if a != WAIT else 0.0))
        cost.append(sum(p * row["cost"].get(x, 0) for x, p in pi.items() if x != WAIT))
    return {"w": w, "wr": wr, "piw": piw, "dm": dm, "dr": dr, "cost": cost}


def _point(c: Dict[str, List[float]], idx: Sequence[int]) -> Dict[str, float]:
    n = len(idx)
    swr = sum(c["wr"][i] for i in idx)
    den = sum(c["w"][i] + c["piw"][i] for i in idx)
    return {"ips": swr / n, "snips": swr / den if den > 0 else 0.0,
            "dm": sum(c["dm"][i] for i in idx) / n, "dr": sum(c["dr"][i] for i in idx) / n,
            "cost_paise": sum(c["cost"][i] for i in idx) / n}


def estimate(rows: Sequence[Dict[str, Any]], target: Target, n_boot: int = 500, seed: int = 0,
             level: float = 0.95) -> Dict[str, Any]:
    """Point estimates, percentile bootstrap CIs (resampling decisions), effective sample size and
    replies per Rs 1,000 (value / exact expected cost of the target on the same contexts)."""
    c = contributions(rows, target)
    n = len(rows)
    idx = list(range(n))
    pt = _point(c, idx)
    rng = random.Random(seed)
    boots: Dict[str, List[float]] = {k: [] for k in ESTIMATORS + tuple("per_1000_inr_" + k for k in ESTIMATORS)}
    for _ in range(n_boot):
        s = [rng.randrange(n) for _ in range(n)]
        p = _point(c, s)
        for k in ESTIMATORS:
            boots[k].append(p[k])
            boots["per_1000_inr_" + k].append(_per_1000(p[k], p["cost_paise"]))
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    sw, sw2 = sum(c["w"]), sum(x * x for x in c["w"])
    out: Dict[str, Any] = {"n": n, "ess": round(sw * sw / sw2, 1) if sw2 > 0 else 0.0,
                           "target_cost_paise_per_decision": round(pt["cost_paise"], 4)}
    for k in ESTIMATORS:
        out[k] = {"value": round(pt[k], 5), "ci": [round(_q(boots[k], lo_q), 5), round(_q(boots[k], hi_q), 5)],
                  "replies_per_1000_inr": round(_per_1000(pt[k], pt["cost_paise"]), 3),
                  "replies_per_1000_inr_ci": [round(_q(boots["per_1000_inr_" + k], lo_q), 3),
                                              round(_q(boots["per_1000_inr_" + k], hi_q), 3)]}
    return out


def _per_1000(value: float, cost_paise: float) -> float:
    return value / (cost_paise / 100.0) * 1000.0 if cost_paise > 0 else 0.0


def _q(xs: List[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]

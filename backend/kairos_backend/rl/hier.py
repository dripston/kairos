"""Empirical-Bayes hierarchy used by every K09 RL v2 quantity (P(reply), P(pay | reply), P(STOP), fatigue,
slot and channel-transition effects).

One idea, used at every level: a level is a set of groups (e.g. cohort C3, or cohort C3 x Maharashtra, or
one borrower). Given what the levels above already predict, each finalized touch j carries an expected
count E_j (the parent's prediction) and an observed count O_j (1 if it happened, else 0). A group g's
effect is a multiplier m_g on its parent with a Gamma(kappa, kappa) prior (mean 1, variance 1/kappa):

    m_g = (O_g + kappa) / (E_g + kappa)          O_g, E_g = decay-weighted sums over the group's touches

so a group with no evidence is exactly its parent (m = 1), and it moves away only as its own evidence
grows. kappa is estimated from the data (method of moments on the between-group spread of O/E, the
Poisson analogue of DerSimonian-Laird): if groups differ no more than noise explains, kappa is large and
the level collapses onto its parent; if they really differ, kappa is small and groups keep their own rate.

Everything here is pure arithmetic on numbers the caller got from the ledger; deterministic (no rng,
groups visited in sorted order).
"""
from __future__ import annotations

import math
from typing import Hashable, Iterable, Optional

KAPPA_MIN = 0.5
KAPPA_MAX = 1e6


def mom_kappa(groups: dict, kappa_min: float = KAPPA_MIN, kappa_max: float = KAPPA_MAX,
              min_groups: int = 3) -> float:
    """Method-of-moments kappa for {key: (O, E)}: between-group variance of the ratio O/E beyond Poisson
    noise is tau^2 = 1/kappa. Returns kappa_max when there is no excess spread (full shrinkage)."""
    gs = [(o, e) for o, e in (groups[k] for k in sorted(groups, key=repr)) if e > 0]
    if len(gs) < min_groups:
        return kappa_max
    so = sum(o for o, _ in gs)
    se = sum(e for _, e in gs)
    if se <= 0 or so <= 0:
        return kappa_max
    rbar = so / se
    # weighted spread of the ratios, minus what Poisson noise alone gives (each ratio has var rbar / E)
    q = sum(e * (o / e - rbar) ** 2 for o, e in gs)
    denom = se - sum(e * e for _, e in gs) / se
    if denom <= 0:
        return kappa_max
    tau2 = (q - (len(gs) - 1) * rbar) / denom
    tau2 = tau2 / (rbar * rbar)            # relative (multiplier) scale
    if tau2 <= 0:
        return kappa_max
    return min(kappa_max, max(kappa_min, 1.0 / tau2))


def multipliers(groups: dict, kappa: Optional[float] = None) -> tuple[float, dict]:
    """(kappa, {key: m}) with m = (O + kappa) / (E + kappa). kappa=None -> method of moments."""
    k = mom_kappa(groups) if kappa is None else float(kappa)
    return k, {key: (o + k) / (e + k) for key, (o, e) in groups.items()}


def gamma_upper(o: float, e: float, kappa: float, z: float = 1.0) -> float:
    """Posterior mean + z standard deviations of a group multiplier (Gamma(o + kappa, e + kappa))."""
    a, b = o + kappa, e + kappa
    return a / b + z * math.sqrt(a) / b


class Level:
    """One level of the hierarchy: keyfn(record) -> group key (None = record not in this level)."""

    def __init__(self, name: str, keyfn, decay: Optional[float] = None, kappa: Optional[float] = None,
                 describe=None):
        self.name, self.keyfn, self.decay, self.fixed_kappa = name, keyfn, decay, kappa
        self.describe = describe or (lambda key: str(key))
        self.kappa = KAPPA_MAX
        self.m: dict = {}
        self.counts: dict = {}

    def fit(self, rows: Iterable[tuple[Hashable, float, float]]) -> None:
        """rows: (key, observed weight, expected weight)."""
        acc: dict = {}
        for key, o, e in rows:
            if key is None:
                continue
            a = acc.get(key)
            if a is None:
                acc[key] = [o, e]
            else:
                a[0] += o
                a[1] += e
        self.counts = {k: (v[0], v[1]) for k, v in acc.items()}
        self.kappa, self.m = multipliers(self.counts, self.fixed_kappa)

    def get(self, key) -> float:
        return self.m.get(key, 1.0) if key is not None else 1.0

    def table(self, min_e: float = 0.0, top: Optional[int] = None) -> list[dict]:
        rows = [{"group": self.describe(k), "observed": round(o, 2), "expected": round(e, 2),
                 "multiplier": round(self.m[k], 3)} for k, (o, e) in self.counts.items() if e >= min_e]
        rows.sort(key=lambda r: (-abs(math.log(max(r["multiplier"], 1e-9))), r["group"]))
        return rows[:top] if top else rows

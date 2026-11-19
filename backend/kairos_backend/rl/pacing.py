"""K09 RL v2 budget pacing: bandits-with-knapsacks style dual price on money.

The campaign has one money constraint (the pool) and K01 hands it out per day (allowance = pool left /
contact days left, unspent money carries forward). A touch is worth funding when

    value - lambda x cost > 0          (value in reply units, cost in paise)

and today's funded touches are taken in order of value / cost until K01's allowance stops them. lambda is
the shadow price of a paisa:

  * dual update, once per day, from yesterday's spend vs its pacing target (target = rho x allowance):
        lambda <- lambda x exp(eta x (spent / target - 1))
    spending under target lowers the price (fund more), spending at the allowance raises it a little
    (keep the bar where the money is worth most);
  * ratio guard (the previous "relative value floor", kept as the fallback): lambda >= phi x the campaign's
    realized replies per paisa so far, so a touch is never funded far below the campaign's own average;
  * spend-down: on the last `spend_down_days` contact days the dual part is dropped (only the guard stays),
    so money that would be returned unspent is used if anything clears the guard.

mode="floor" reproduces the previous rule (guard only, no dual), mode="dual" is the full rule.
"""
from __future__ import annotations

import math
from typing import Optional


class DualPacer:
    def __init__(self, mode: str = "dual", eta: float = 0.5, rho: float = 1.0, phi: float = 0.5,
                 warmup_touches: int = 200, spend_down_days: int = 1, init_quantile: float = 0.5,
                 lam_max_rel: float = 3.0):
        if mode not in ("dual", "floor", "none"):
            raise ValueError("mode must be dual, floor or none")
        self.mode, self.eta, self.rho, self.phi = mode, float(eta), float(rho), float(phi)
        self.warmup, self.spend_down_days = int(warmup_touches), int(spend_down_days)
        self.init_quantile, self.lam_max_rel = float(init_quantile), float(lam_max_rel)
        self.lam_dual: Optional[float] = None
        self.history: list[dict] = []

    def guard(self, realized_per_paisa: Optional[float], n_final: int) -> float:
        if self.mode == "none" or realized_per_paisa is None or n_final < self.warmup:
            return 0.0
        return self.phi * realized_per_paisa

    def update(self, spent_prev: Optional[int], target_prev: Optional[int]) -> None:
        """Dual step from yesterday's spend vs target (both paise)."""
        if self.mode != "dual" or self.lam_dual is None or not target_prev:
            return
        x = spent_prev / (self.rho * target_prev) - 1.0
        self.lam_dual *= math.exp(self.eta * max(-1.0, min(1.0, x)))

    def price(self, day: int, ratios_today: list[float], realized_per_paisa: Optional[float], n_final: int,
              days_left: int, allowance: Optional[int] = None, costs_today: Optional[list] = None) -> float:
        """lambda for today (reply units per paisa). ratios_today: candidate value/cost of today's best arms
        (used once, to initialise the dual price at the ratio where today's allowance would run out)."""
        g = self.guard(realized_per_paisa, n_final)
        lam = g
        if self.mode == "dual":
            if self.lam_dual is None and ratios_today:
                self.lam_dual = self._clearing(ratios_today, costs_today, allowance)
            if self.lam_dual is not None and realized_per_paisa:
                self.lam_dual = min(self.lam_dual, self.lam_max_rel * realized_per_paisa)
            if days_left > self.spend_down_days and self.lam_dual is not None:
                lam = max(lam, self.lam_dual)
        self.history.append({"day": day + 1, "lambda": lam, "guard": g, "dual": self.lam_dual,
                             "days_left": days_left})
        return lam

    def _clearing(self, ratios, costs, allowance) -> float:
        """Price at which today's allowance would just be used (fractional knapsack cutoff)."""
        pairs = sorted(zip(ratios, costs or [1] * len(ratios)), key=lambda t: -t[0])
        if not allowance or not costs:
            return pairs[int(self.init_quantile * (len(pairs) - 1))][0]
        spent = 0
        for r, c in pairs:
            spent += c
            if spent >= self.rho * allowance:
                return r
        return 0.0

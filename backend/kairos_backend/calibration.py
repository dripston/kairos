"""Learn-then-Test threshold selection (stdlib only).

Angelopoulos, Bates, Candès, Jordan & Lei, "Learn then Test: Calibrating Predictive Algorithms to
Achieve Risk Control", arXiv:2110.01052. Used by scripts/calibrate_thresholds.py to choose the
small model's "may answer alone" threshold for the intent stack.

For each candidate threshold λ we test the null H_λ: risk(λ) > α, where risk(λ) is the error rate
among the messages the stack would handle without a human at λ. The p-value is the exact binomial
tail P(Bin(n_λ, α) <= k_λ) (k_λ errors among n_λ auto-handled messages), or Hoeffding's
exp(-2 n_λ (α - k_λ/n_λ)^2). Fixed-sequence testing walks λ from the most conservative value
downwards and rejects while p <= δ; the first failure stops the walk. Every rejected λ then has
risk <= α simultaneously with probability >= 1 - δ (family-wise error control), for calibration data
exchangeable with the data the threshold is used on. We return the smallest rejected λ (the most
automation that is still certified). Nothing rejected -> None (the caller must fail closed).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(Bin(n, p) <= k), summed in log space (exact enough for n up to ~1e5)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq
             for i in range(k + 1)]
    m = max(terms)
    return min(1.0, math.exp(m) * sum(math.exp(t - m) for t in terms))


def pvalue_binomial(k: int, n: int, alpha: float) -> float:
    """Valid p-value for H0: P(error) > alpha given k errors in n Bernoulli trials."""
    if n == 0:
        return 1.0
    return binom_cdf(k, n, alpha)


def pvalue_hoeffding(k: int, n: int, alpha: float) -> float:
    if n == 0:
        return 1.0
    gap = alpha - k / n
    return 1.0 if gap <= 0 else math.exp(-2.0 * n * gap * gap)


PVALUES: Dict[str, Callable[[int, int, float], float]] = {"binomial": pvalue_binomial, "hoeffding": pvalue_hoeffding}


def ltt_fixed_sequence(lambdas_desc: Sequence[float], stats: Callable[[float], Tuple[int, int]], alpha: float,
                       delta: float, pvalue: str = "binomial") -> Tuple[Optional[float], List[dict]]:
    """lambdas_desc: thresholds from most conservative (high) to least. stats(λ) -> (n_auto, k_errors).
    Returns (smallest certified λ or None, per-λ trace up to and including the first non-rejection)."""
    pv = PVALUES[pvalue]
    chosen: Optional[float] = None
    trace: List[dict] = []
    for lam in lambdas_desc:
        n, k = stats(lam)
        p = pv(k, n, alpha)
        rejected = p <= delta
        trace.append({"lambda": round(lam, 4), "n_auto": n, "errors": k,
                      "risk_hat": round(k / n, 4) if n else None, "p_value": round(p, 6), "certified": rejected})
        if not rejected:
            break
        chosen = lam
    return chosen, trace


def grid(hi: float = 0.99, lo: float = 0.30, step: float = 0.01) -> List[float]:
    """Descending grid hi, hi - step, ..., lo (rounded to avoid float drift)."""
    n = int(round((hi - lo) / step))
    return [round(hi - i * step, 6) for i in range(n + 1)]

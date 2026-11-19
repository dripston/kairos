"""Payment propensity: a DEMO RULE, not a trained model.

    rule_propensity(dpd, band, ...) -> {"p", "horizon_days", "intensity", "reasons", "model"}

p is a hand-set guess of "a verified payment arrives within horizon_days". It is deterministic,
always in [P_MIN, P_MAX], and monotonic non-increasing in DPD (every other input fixed). Use it to
pick an outreach INTENSITY hint for the Supervisor; it never overrides K01 / K13 (the Gate and
holds still decide whether anything is sent). Label anything built on it "rule-based-demo".

Intensity:
    WAIT      borrower's last intent asks us to pause: NEEDS_TIME, HARDSHIP, STOP, CALLBACK
              (K13: rebuttal/hold/callback flows own the next touch; STOP is K01 opt-out)
    LOW       p >= LOW_INTENSITY_AT: likely to pay without much nudging, keep touches light
    STANDARD  everything else (band caps from K01 still apply)

Learned propensity is DEFERRED. ShadowPropensityLogger records (features, p_rule) at decision time
and the VERIFIED label later (1 = payment verified by D7 inside the horizon, 0 = horizon passed
without one). Once enough labels have matured, an online logistic regression
(sklearn SGDClassifier(loss="log_loss").partial_fit) can be trained on them in SHADOW mode only —
scored and compared with this rule, never acting — before anyone proposes using it. sklearn is
not a dependency of this package on purpose.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Protocol, Tuple

MODEL = "rule-based-demo"
P_MIN, P_MAX = 0.02, 0.95
LOW_INTENSITY_AT = 0.60
BASE_HORIZON_DAYS = 7
WAIT_INTENTS = frozenset({"NEEDS_TIME", "HARDSHIP", "STOP", "CALLBACK"})
BANDS = ("PRE_DUE", "B1", "B2", "B3", "B4")

# 7-day payment probability by DPD (piecewise linear between knots, flat outside). Hand-set.
_DPD_KNOTS: Tuple[Tuple[int, float], ...] = (
    (-6, 0.85), (0, 0.78), (30, 0.45), (60, 0.25), (90, 0.12), (180, 0.05), (365, 0.03),
)

# Log-odds nudges. Hand-set for the demo; documented in the returned reasons.
_INTENT_SHIFT: Dict[str, float] = {
    "CLAIMS_PAID": 0.8, "PTP": 0.6, "CALLBACK": 0.0, "OTHER": 0.0, "STOP": 0.0,
    "NEEDS_TIME": -0.3, "DISPUTE": -0.7, "HARDSHIP": -0.8, "WRONG_PERSON": -1.0,
}
_BROKEN_PTP_SHIFT = -0.6      # per broken promise, counted up to 3
_REPLIED_SHIFT = 0.4
_NACH_ACTIVE_SHIFT = 0.5      # active auto-debit (NACH) mandate
_NACH_ABSENT_SHIFT = -0.2     # no mandate / mandate bounced


def _base_p(dpd: int) -> float:
    if dpd <= _DPD_KNOTS[0][0]:
        return _DPD_KNOTS[0][1]
    for (x0, y0), (x1, y1) in zip(_DPD_KNOTS, _DPD_KNOTS[1:]):
        if dpd <= x1:
            return y0 + (y1 - y0) * (dpd - x0) / (x1 - x0)
    return _DPD_KNOTS[-1][1]


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def rule_propensity(dpd: int, band: str, broken_ptp: int = 0, last_intent: Optional[str] = None,
                    replied_recently: bool = False, nach_mandate: Optional[bool] = None,
                    horizon_days: int = BASE_HORIZON_DAYS) -> Dict[str, Any]:
    if band not in BANDS:
        raise ValueError(f"unknown band {band!r}")
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")
    base = _base_p(int(dpd))
    reasons: List[str] = [f"demo rule (not trained): base {base:.2f} at DPD {dpd} ({band}), {BASE_HORIZON_DAYS}-day"]
    z = _logit(base)
    nb = max(0, min(int(broken_ptp), 3))
    if nb:
        z += _BROKEN_PTP_SHIFT * nb
        reasons.append(f"{nb} broken PTP(s) lower p")
    if last_intent:
        shift = _INTENT_SHIFT.get(last_intent, 0.0)
        if shift:
            z += shift
            reasons.append(f"last intent {last_intent} {'raises' if shift > 0 else 'lowers'} p")
    if replied_recently:
        z += _REPLIED_SHIFT
        reasons.append("replied recently raises p")
    if nach_mandate is True:
        z += _NACH_ACTIVE_SHIFT
        reasons.append("active NACH mandate raises p")
    elif nach_mandate is False:
        z += _NACH_ABSENT_SHIFT
        reasons.append("no / bounced NACH mandate lowers p")
    p7 = _sigmoid(z)
    p = 1.0 - (1.0 - p7) ** (horizon_days / BASE_HORIZON_DAYS) if horizon_days != BASE_HORIZON_DAYS else p7
    if horizon_days != BASE_HORIZON_DAYS:
        reasons.append(f"scaled to {horizon_days}-day horizon (constant hazard)")
    p = round(min(P_MAX, max(P_MIN, p)), 4)

    if last_intent in WAIT_INTENTS:
        intensity = "WAIT"
        reasons.append(f"last intent {last_intent} -> WAIT (pause owned by K13/K01 flow)")
    elif p >= LOW_INTENSITY_AT:
        intensity = "LOW"
        reasons.append(f"p >= {LOW_INTENSITY_AT} -> LOW intensity")
    else:
        intensity = "STANDARD"
    return {"p": p, "horizon_days": horizon_days, "intensity": intensity, "reasons": reasons, "model": MODEL}


def propensity_features(dpd: int, band: str, broken_ptp: int = 0, last_intent: Optional[str] = None,
                        replied_recently: bool = False, nach_mandate: Optional[bool] = None,
                        horizon_days: int = BASE_HORIZON_DAYS) -> Dict[str, Any]:
    """The inputs rule_propensity sees, as a flat dict for shadow logging."""
    return {"dpd": dpd, "band": band, "broken_ptp": broken_ptp, "last_intent": last_intent,
            "replied_recently": replied_recently, "nach_mandate": nach_mandate, "horizon_days": horizon_days}


# ---------------------------------------------------------------------------------------------
# Shadow logging for a future learned model (deferred; no sklearn here)
# ---------------------------------------------------------------------------------------------

class ShadowPropensityLogger(Protocol):
    def log_score(self, decision_id: str, borrower_id: str, at: datetime, features: Dict[str, Any],
                  p_rule: float) -> None:
        """Called when rule_propensity is used for a decision. Idempotent on decision_id."""
        ...

    def record_verified_payment(self, borrower_id: str, verified_at: datetime) -> int:
        """A D7-VERIFIED payment (never a borrower claim). Labels open rows inside their horizon 1."""
        ...

    def mature(self, now: datetime) -> int:
        """Rows whose horizon ended without a verified payment become label 0."""
        ...

    def matured_examples(self) -> List[Tuple[Dict[str, Any], int]]:
        """(features, label) pairs with a final label: training data for shadow partial_fit."""
        ...


class NoopShadowLogger:
    def log_score(self, decision_id, borrower_id, at, features, p_rule) -> None:
        return None

    def record_verified_payment(self, borrower_id, verified_at) -> int:
        return 0

    def mature(self, now) -> int:
        return 0

    def matured_examples(self) -> List[Tuple[Dict[str, Any], int]]:
        return []


@dataclass
class _ShadowRow:
    decision_id: str
    borrower_id: str
    at: datetime
    features: Dict[str, Any]
    p_rule: float
    label: Optional[int] = None

    @property
    def horizon_end(self) -> datetime:
        return self.at + timedelta(days=int(self.features.get("horizon_days") or BASE_HORIZON_DAYS))


@dataclass
class InMemoryShadowLogger:
    rows: Dict[str, _ShadowRow] = field(default_factory=dict)

    def log_score(self, decision_id: str, borrower_id: str, at: datetime, features: Dict[str, Any],
                  p_rule: float) -> None:
        if decision_id not in self.rows:
            self.rows[decision_id] = _ShadowRow(decision_id, borrower_id, at, dict(features), p_rule)

    def record_verified_payment(self, borrower_id: str, verified_at: datetime) -> int:
        n = 0
        for r in self.rows.values():
            if r.borrower_id == borrower_id and r.label is None and r.at <= verified_at < r.horizon_end:
                r.label = 1
                n += 1
        return n

    def mature(self, now: datetime) -> int:
        n = 0
        for r in self.rows.values():
            if r.label is None and now >= r.horizon_end:
                r.label = 0
                n += 1
        return n

    def matured_examples(self) -> List[Tuple[Dict[str, Any], int]]:
        return [(dict(r.features), r.label) for r in self.rows.values() if r.label is not None]

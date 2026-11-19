"""K21 propensity-to-pay scorecard and nudge intensity (Ojas, policy/K21-propensity-intensity).

    score(inputs) -> ScoreResult(score 0-100, intensity, override, top_reasons, evidence_strength, ...)

Every number (points, cut-offs, clamp, intensity levels, overrides, low-evidence cap) is read from
`scorecard.json`; nothing here is hard-coded from the policy. The weights are SYNTHETIC (K21 section 6):
label anything built on this "scorecard-heuristic" and SIMULATED.

What it answers: likelihood of a VERIFIED payment within `horizon_days`, and how hard to work on
this borrower today. Holds always win: an override forces WAIT, but the score is still computed
and returned for analytics. K09 still chooses channel, time and whether a touch is worth its
cost; K01 caps stay the ceiling (`final_allowance`).

This sits next to `propensity.py` (the older rule-based demo, a probability with LOW/STANDARD/WAIT).
Which one the Supervisor uses is an open Product decision (POLICY_COVERAGE.md, OPEN_DECISION K21-1).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .policy import policy_dir

SCORECARD_DIRNAME = "K21-propensity-intensity"
INTENSITIES = ("WAIT", "LOW", "STANDARD", "HIGH")
VOICE_CHANNELS = frozenset({"ivr", "voicebot"})


def load_scorecard(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else policy_dir() / SCORECARD_DIRNAME / "scorecard.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class ScoreInputs:
    """One borrower x obligation on one day. None = no data (neutral, never a penalty)."""
    band: str                                   # PRE_DUE / B1..B4 (K01 band)
    ontime_emis_6m: Optional[int] = None
    broken_ptp: Optional[int] = None            # broken promises this allocation period
    paid_last_30d: bool = False
    partial_payment_cycle: bool = False
    responded_7d: bool = False                  # any borrower response on any channel
    response_channels: int = 0
    whatsapp_read_7d: bool = False
    link_clicked_7d: bool = False
    call_answered_7d: bool = False              # answered > 10 s
    sms_delivered_7d: bool = False              # worth 0 by decision; not evidence either
    unreachable_14d: bool = False
    intent: Optional[str] = None                # WILLING / SILENT / NEEDS_TIME / DISPUTE / REFUSED / HARDSHIP
    nach_bounce_reason: Optional[str] = None    # TECHNICAL / INSUFFICIENT_FUNDS
    aa_net_cash_flow: Optional[str] = None      # POSITIVE / NEGATIVE (only with AA consent)
    aa_dti: Optional[float] = None
    aa_risk_flags: Optional[int] = None
    override: Optional[str] = None              # one of scorecard.overrides_to_wait


@dataclass
class ScoreResult:
    score: int
    intensity: str
    override: Optional[str]
    top_reasons: List[str]
    evidence_strength: float
    horizon_days: int
    model_version: str
    policy_version: str
    computed_at: Optional[str] = None
    contributions: List[Tuple[str, int]] = field(default_factory=list)
    band_intensity: str = ""                    # intensity from the score alone, before caps/overrides


class Scorecard:
    def __init__(self, data: Optional[dict] = None):
        self.data = data or load_scorecard()
        self.points = self.data["points"]
        self.version = self.data["policy_version"]
        self.model_version = self.data["model_version"]
        self.overrides = frozenset(self.data["overrides_to_wait"])

    # ------------------------------------------------------------------ score
    def _contributions(self, x: ScoreInputs) -> List[Tuple[str, int]]:
        pt = self.points
        out: List[Tuple[str, int]] = []

        def add(name: str, v) -> None:
            if v:
                out.append((name, int(v)))

        add(f"dpd_band:{x.band}", pt["dpd_band"][x.band])
        if x.ontime_emis_6m is not None:
            lo, hi = pt["ontime_emis_last_6m"]["range"]
            add("ontime_emis_6m", max(lo, min(hi, (x.ontime_emis_6m - 3) * 4)))
        bp = pt["broken_ptp_in_period"]
        add("broken_ptp", max(bp["max_total"], (x.broken_ptp or 0) * bp["per_item"]))
        if x.paid_last_30d:
            add("paid_last_30d", pt["paid_in_last_30_days"])
        if x.partial_payment_cycle:
            add("partial_payment", pt["partial_payment_this_cycle"])
        if x.responded_7d:
            add("responded_7d", pt["borrower_responded_last_7d"])
        if x.response_channels >= 2:
            add("responded_2plus_channels", pt["responded_on_2plus_channels"])
        if x.whatsapp_read_7d:
            add("whatsapp_read", pt["whatsapp_read_last_7d"])
        if x.link_clicked_7d:
            add("link_clicked", pt["link_clicked_last_7d"])
        if x.call_answered_7d:
            add("call_answered", pt["call_answered_over_10s_last_7d"])
        if x.sms_delivered_7d:
            add("sms_delivered", pt["sms_delivered_last_7d"])   # 0 by decision: delivery is not engagement
        if x.unreachable_14d:
            add("unreachable_14d", pt["unreachable_all_channels_14d"])
        if x.intent:
            add(f"intent:{x.intent}", pt["intent"][x.intent])
        if x.nach_bounce_reason:
            add(f"bounce:{x.nach_bounce_reason}", pt["nach_bounce_reason"][x.nach_bounce_reason])
        if x.aa_net_cash_flow:
            add(f"aa_cash_flow:{x.aa_net_cash_flow}", pt["aa_net_cash_flow_last_month"][x.aa_net_cash_flow])
        if x.aa_dti is not None:
            if x.aa_dti > 0.65:
                add("aa_dti_high", pt["aa_dti"]["above_0_65"])
            elif x.aa_dti < 0.40:
                add("aa_dti_low", pt["aa_dti"]["below_0_40"])
        if (x.aa_risk_flags or 0) >= 3:
            add("aa_risk_flags", pt["aa_risk_flags_3plus"])
        return out

    @staticmethod
    def evidence_strength(x: ScoreInputs) -> float:
        """Share of the 4 signal groups with at least one non-missing input. SMS delivery is not evidence."""
        groups = (
            x.ontime_emis_6m is not None or x.broken_ptp is not None,
            any((x.responded_7d, x.whatsapp_read_7d, x.link_clicked_7d, x.call_answered_7d, x.unreachable_14d)),
            bool(x.intent),
            bool(x.nach_bounce_reason or x.aa_net_cash_flow or x.aa_dti is not None),
        )
        return sum(groups) / len(groups)

    def band_intensity(self, s: int) -> str:
        return next(b["intensity"] for b in self.data["intensity_by_score"] if b["min"] <= s <= b["max"])

    def score(self, x: ScoreInputs, now: Optional[datetime] = None) -> ScoreResult:
        if x.override and x.override not in self.overrides:
            raise ValueError(f"unknown override {x.override!r}; allowed: {sorted(self.overrides)}")
        contrib = self._contributions(x)
        lo, hi = self.data["clamp"]
        s = max(lo, min(hi, self.data["base_score"] + sum(v for _, v in contrib)))
        by_score = self.band_intensity(s)
        ev = self.evidence_strength(x)
        intensity = by_score
        if intensity == "HIGH" and ev <= 0.5:          # low-evidence cap (decided 19 Nov)
            intensity = "STANDARD"
        if x.override:                                  # holds always win; score still logged
            intensity = "WAIT"
        top = sorted(contrib, key=lambda kv: -abs(kv[1]))[:3]
        return ScoreResult(score=s, intensity=intensity, override=x.override or None,
                           top_reasons=[f"{k} {v:+d}" for k, v in top], evidence_strength=ev,
                           horizon_days=self.data["horizon_days"], model_version=self.model_version,
                           policy_version=self.version,
                           computed_at=now.isoformat(timespec="seconds") if now else None,
                           contributions=contrib, band_intensity=by_score)

    # ------------------------------------------------------------------ allowance
    @staticmethod
    def intensity_allowance(intensity: str, band_cap: int) -> int:
        """Per-channel daily allowance from intensity alone (LOW = 1 touch in total)."""
        return {"WAIT": 0, "LOW": 1, "STANDARD": math.ceil(band_cap / 2), "HIGH": band_cap}[intensity]

    def final_allowance(self, intensity: str, k01_cap: int, lender_cap: Optional[int] = None,
                        official_cap: Optional[int] = None) -> int:
        """Strictest of the intensity allowance, the K01 policy cap, the lender cap and the official cap."""
        caps = [self.intensity_allowance(intensity, k01_cap), k01_cap]
        caps += [c for c in (lender_cap, official_cap) if c is not None]
        return max(0, min(caps))

    @staticmethod
    def channels(intensity: str, consented: Iterable[str], cost: Dict[str, float],
                 reach_evidence: Iterable[str] = ()) -> List[str]:
        """Channels this intensity may use today, cheapest first.
        LOW: the single cheapest consented non-voice channel with evidence of reach, else the cheapest
        consented non-voice one. STANDARD: all consented except voicebot. HIGH: all consented."""
        cons = sorted(set(consented), key=lambda ch: (cost.get(ch, float("inf")), ch))
        if intensity == "WAIT":
            return []
        if intensity == "LOW":
            pool = [c for c in cons if c not in VOICE_CHANNELS]
            reached = [c for c in pool if c in set(reach_evidence)]
            pick = reached or pool
            return pick[:1]
        if intensity == "STANDARD":
            return [c for c in cons if c != "voicebot"]
        return cons


_DEFAULT: Optional[Scorecard] = None


def default_scorecard() -> Scorecard:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Scorecard()
    return _DEFAULT


def score(x: ScoreInputs, now: Optional[datetime] = None) -> ScoreResult:
    return default_scorecard().score(x, now)

"""Shared synthetic world for the FSM conformance tests: a real K06 ledger on a fresh K03 spine.

Synthetic data only. Every borrower `Bn` has two loans (`Bn-A`, `Bn-B`), one EMI each
(`Bn-A-1`, `Bn-B-1`), enrolled in campaign C1. Times are local (Asia/Kolkata); D is a Friday.
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, Optional

from kairos_backend.contracts import SYNTHETIC_COSTS, ArmScore, Candidate, Ranking, replies_per_rupee
from kairos_backend.ledger import Ledger
from tests.spine_helpers import add_loan

D = date(2026, 11, 20)  # Friday, a contact day
TEMPLATES = {"sms": ("SMS_EN", "en"), "whatsapp": ("WA_HI", "hi"), "email": ("EM_EN", "en"),
             "ivr": ("IVR_HI", "hi"), "voicebot": ("VB_EN", "en")}
CHANNELS = tuple(TEMPLATES)
COST = {ch: int(SYNTHETIC_COSTS.get(ch, 100)) or 100 for ch in CHANNELS}


def at(hh: int, mm: int = 0, day: date = D) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm)


def new_ledger(policy, path, budget_per_case_paise: int = 10_000, end_days: int = 10) -> Ledger:
    lg = Ledger(policy, path)
    for ch, (tpl, lang) in TEMPLATES.items():
        lg.upsert_template(tpl, ch, lang)
    lg.upsert_campaign("C1", budget_per_case_paise=budget_per_case_paise, start_date=D,
                       end_date=D + timedelta(days=end_days))
    lg.start_day(D)  # daily LMS refresh: DPD fresh today
    return lg


def add_borrower(lg: Ledger, bid: str, dpd: int = 12, consents: Optional[dict] = None, campaign: str = "C1") -> None:
    for loan in ("A", "B"):
        add_loan(lg, bid, D, loan=loan, dpd=dpd, consents=consents, campaign=campaign)


def random_consents(rng: random.Random) -> Dict[str, int]:
    return {ch: int(rng.random() < 0.85) for ch in CHANNELS}


class Coverage:
    """Which declared transitions the real implementation was observed to take."""

    def __init__(self):
        self.fired: Counter = Counter()

    def add(self, t) -> None:
        self.fired[(t.src, t.event, t.dst, t.guard)] += 1

    def missing(self, machine, exempt: Iterable = ()) -> list:
        ex = set(exempt)
        return [(t.src, t.event, t.dst, t.guard) for t in machine.transitions
                if (t.src, t.event, t.dst, t.guard) not in self.fired and (t.src, t.event) not in ex
                and (t.src, t.event, t.dst, t.guard) not in ex]


class TableLearner:
    """Deterministic K09 stand-in: fixed p(reply) per channel; best replies-per-rupee wins.
    `rogue=True` names a channel that was NOT among the candidates (exercises LEARNER_CHOICE_NOT_ELIGIBLE);
    `force_wait=True` always says WAIT."""
    model_version = "table-learner/v1@0"

    def __init__(self, probs: Optional[dict] = None, force_wait: bool = False, rogue: bool = False):
        self.probs = probs or {"sms": 0.10, "whatsapp": 0.30, "email": 0.02, "ivr": 0.08, "voicebot": 0.2}
        self.force_wait, self.rogue = force_wait, rogue

    def rank(self, band, candidates, mode, rng=None, borrower_id=None, persist=True) -> Ranking:
        scores = []
        for c in candidates:
            p = self.probs.get(c.channel, 0.1)
            scores.append(ArmScore(band, c.channel, c.slot, 1.0, 1.0, 0, "prior", p, None, p, c.est_cost_paise,
                                   replies_per_rupee(p, c.est_cost_paise), p >= 0.03 and c.est_cost_paise > 0, "table"))
        qual = [s for s in scores if s.qualifies]
        if self.force_wait or not qual:
            return Ranking("WAIT", None, scores, self.model_version, mode, "wait")
        best = max(qual, key=lambda s: s.replies_per_inr)
        if self.rogue:
            others = [ch for ch in CHANNELS if ch not in {c.channel for c in candidates}]
            if others:
                best = ArmScore(band, others[0], best.slot, 1, 1, 0, "prior", 0.9, None, 0.9, 100, 1.0, True, "rogue")
        return Ranking("SEND", best, scores, self.model_version, mode, "best")

    def apply_outcome(self, outcome) -> bool:
        return False

    def estimates(self, band=None):
        return []


def candidate(ch: str, t: datetime, slot: Optional[str] = None, template: Optional[str] = "default") -> Candidate:
    from kairos_backend.contracts import slot_of
    return Candidate(ch, slot or slot_of(t) or "08-11", t, TEMPLATES[ch][0] if template == "default" else template,
                     COST[ch])


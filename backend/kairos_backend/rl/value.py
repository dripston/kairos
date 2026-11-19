"""K09 RL v2 collections value: P(reply), P(pay | reply) and P(STOP), each one hierarchical model.

Levels (each shrunk to the one above with an empirical-Bayes kappa, see rl.hier):

    P(reply)   global channel x slot (Beta, SIMULATOR prior) -> DPD band            [arm; decay per day]
               x fatigue (contacts in the previous 6 days)                            [population effects]
               x channel transition (previous touch's channel layer and outcome -> this channel)
               x campaign -> cohort (CN C1-C5) -> region (cohort x state)             [segment]
               -> borrower -> borrower x channel -> borrower x slot                   [individual]
    P(pay | reply)  global -> campaign -> cohort -> region -> DPD band
    P(STOP | touch) = P(reply-like response) x stop share: global -> fatigue -> cohort -> region

    value = P(reply) x (1 + alpha x P(pay | reply) x EMI / mean EMI) - mu x P(STOP)        (reply units)

Evidence comes only from finalized touch outcomes (apply_outcome, real time), the payment noted on the
touch (touch_outcomes.payment_verified_at, inside the touch's reply window) and STOP events in the spine
`events` table inside the touch's reply window. The borrower level is the borrower's own decay-weighted
counts shrunk to their cohort x region estimate, so it is deterministic and explainable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..learner import SIMULATOR_PRIORS
from .hier import Level

CH_LAYER = {"sms": 1, "email": 1, "whatsapp": 2, "ivr": 3, "voicebot": 3}
LAYER_NAMES = {1: "L1 cheap digital (SMS, email)", 2: "L2 rich digital (WhatsApp)", 3: "L3 voice (IVR, voicebot)",
               4: "L4 human (agent hand-off)"}


@dataclass
class Rec:
    """One finalized touch, as the model sees it (all fields from the ledger / spine)."""
    iid: str
    bid: str
    day: int              # executed date ordinal (local)
    band: str
    ch: str
    slot: str
    cohort: str
    region: str
    campaign: str
    rec: int              # executed touches in the previous 6 days (0, 1, 2+)
    prev: Optional[tuple]  # (previous channel, its outcome R/D/U, gap bucket) or None
    reply: int
    paid: int
    stop: int
    p_arm: float          # arm-level estimate when the outcome arrived (for expected counts)
    p_pred: float = 0.0   # full reply prediction when the outcome arrived


def rec_bucket(n: int) -> int:
    return 0 if n <= 0 else (1 if n == 1 else 2)


def gap_bucket(g: int) -> str:
    return "g2" if g <= 2 else ("g3-4" if g <= 4 else "g5+")


def trans_key(prev: Optional[tuple], ch: str):
    """Transition group: (previous layer, previous outcome) -> this channel. None for a first touch."""
    if prev is None:
        return ("first", ch)
    pch, out, _ = prev
    return (f"L{CH_LAYER.get(pch, 0)}", out, ch)


class ValueModel:
    def __init__(self, arm_decay: float = 0.7, pop_decay: float = 0.85, ind_decay: Optional[float] = None,
                 prior_strength: float = 20.0, pay_prior: tuple = (0.25, 20.0), stop_prior: tuple = (0.06, 30.0),
                 warm: Optional[dict] = None, segments: bool = True, individual: bool = True,
                 sequencing: bool = True):
        self.arm_decay, self.pop_decay, self.ind_decay = arm_decay, pop_decay, ind_decay
        self.prior = {k: (m * s, (1 - m) * s) for k, (m, s) in SIMULATOR_PRIORS.items()}
        self.pay_prior = (pay_prior[0] * pay_prior[1], pay_prior[1])
        self.stop_prior = (stop_prior[0] * stop_prior[1], stop_prior[1])
        self.records: list[Rec] = []
        self.ref_day: Optional[int] = None
        self.dirty = True
        self.warm_prior: dict = {}
        if warm:
            self.load_warm(warm)
        L = Level
        self.band = L("band x channel x slot", lambda r: (r.band, r.ch, r.slot), arm_decay,
                      describe=lambda k: f"{k[0]} {k[1]} {k[2]}")
        self.fatigue = L("fatigue (touches in previous 6 days)", lambda r: r.rec, pop_decay,
                         describe=lambda k: f"{k} prior touches" if k < 2 else "2+ prior touches")
        self.trans = L("channel transition", lambda r: trans_key(r.prev, r.ch), pop_decay,
                       describe=lambda k: (f"first touch -> {k[1]}" if k[0] == "first"
                                           else f"{k[0]} {dict(R='replied', D='delivered, no reply', U='not delivered')[k[1]]} -> {k[2]}"))
        self.trans_cohort = L("cohort x channel transition", lambda r: (r.cohort,) + trans_key(r.prev, r.ch), pop_decay,
                              describe=lambda k: f"{k[0]} " + " ".join(str(x) for x in k[1:]))
        self.campaign = L("campaign", lambda r: r.campaign, pop_decay)
        self.cohort = L("cohort", lambda r: r.cohort, pop_decay)
        self.region = L("region (cohort x state)", lambda r: (r.cohort, r.region), pop_decay,
                        describe=lambda k: f"{k[0]} {k[1]}")
        self.borrower = L("borrower", lambda r: r.bid, ind_decay)
        self.borrower_ch = L("borrower x channel", lambda r: (r.bid, r.ch), ind_decay,
                             describe=lambda k: f"{k[0]} {k[1]}")
        self.borrower_slot = L("borrower x slot", lambda r: (r.bid, r.slot), ind_decay,
                               describe=lambda k: f"{k[0]} {k[1]}")
        self.reply_levels = ([self.fatigue] + ([self.trans, self.trans_cohort] if sequencing else [])
                             + ([self.campaign, self.cohort, self.region] if segments else [])
                             + ([self.borrower, self.borrower_ch, self.borrower_slot] if individual else []))
        self.pay_campaign = L("pay: campaign", lambda r: r.campaign, pop_decay)
        self.pay_cohort = L("pay: cohort", lambda r: r.cohort, pop_decay)
        self.pay_region = L("pay: region", lambda r: (r.cohort, r.region), pop_decay,
                            describe=lambda k: f"{k[0]} {k[1]}")
        self.pay_band = L("pay: DPD band", lambda r: r.band, pop_decay)
        self.pay_levels = [self.pay_campaign, self.pay_cohort, self.pay_region, self.pay_band]
        self.stop_fatigue = L("stop: fatigue", lambda r: r.rec, pop_decay)
        self.stop_cohort = L("stop: cohort", lambda r: r.cohort, pop_decay)
        self.stop_region = L("stop: region", lambda r: (r.cohort, r.region), pop_decay,
                             describe=lambda k: f"{k[0]} {k[1]}")
        self.stop_levels = [self.stop_fatigue, self.stop_cohort, self.stop_region]
        self.p_global: dict = {}
        self.q_global = self.pay_prior[0] / self.pay_prior[1]
        self.sigma = self.stop_prior[0] / self.stop_prior[1]

    # ---------- warm start ----------
    def load_warm(self, warm: dict) -> None:
        """Population-level posteriors exported at a previous cohort's close (rl.warmstart): arm channel x
        slot means with a capped effective sample size, and band / fatigue / transition / cohort effects as
        pseudo-counts. Never borrower-level (new cohort, new borrowers)."""
        for k, (m, s) in warm.get("arm_prior", {}).items():
            ch, slot = k.split("|")
            self.prior[(ch, slot)] = (m * s, (1 - m) * s)
        self.warm_prior = {name: {tuple(x) if isinstance(x, list) else x: v for x, v in rows}
                           for name, rows in warm.get("level_pseudo", {}).items()}
        if "pay_prior" in warm:
            self.pay_prior = tuple(warm["pay_prior"])
        if "stop_prior" in warm:
            self.stop_prior = tuple(warm["stop_prior"])

    def _pseudo(self, lv: Level) -> list:
        return [(k, o, e) for k, (o, e) in self.warm_prior.get(lv.name, {}).items()]

    # ---------- evidence ----------
    def add(self, r: Rec) -> None:
        self.records.append(r)
        self.dirty = True

    @staticmethod
    def _w(decay: Optional[float], age: int) -> float:
        return 1.0 if decay is None or age <= 0 else decay ** age

    def refresh(self, ref_day: int) -> None:
        """Refit every level from the decay-weighted finalized touches (called lazily after new outcomes)."""
        if not self.dirty and ref_day == self.ref_day:
            return
        self.ref_day, self.dirty = ref_day, False
        recs = self.records
        ages = [ref_day - r.day for r in recs]
        # global arm: Beta(prior + decayed counts) per channel x slot
        acc: dict = {}
        for r, a in zip(recs, ages):
            w = self._w(self.arm_decay, a)
            x = acc.setdefault((r.ch, r.slot), [0.0, 0.0])
            x[0] += w * r.reply
            x[1] += w
        self.p_global = {}
        for key in set(self.prior) | set(acc):
            a0, b0 = self.prior.get(key, (0.1, 1.9))
            s, n = acc.get(key, (0.0, 0.0))
            self.p_global[key] = (a0 + s) / (a0 + b0 + n)
        self.band.fit(self._pseudo(self.band) + [
            (self.band.keyfn(r), self._w(self.arm_decay, a) * r.reply,
             self._w(self.arm_decay, a) * self.p_global.get((r.ch, r.slot), 0.05)) for r, a in zip(recs, ages)])
        # reply levels, fitted in order on what the levels above already predict (p_arm when it arrived)
        cum = [r.p_arm for r in recs]
        for lv in self.reply_levels:
            ws = [self._w(lv.decay, a) for a in ages]
            keys = [lv.keyfn(r) for r in recs]
            lv.fit(self._pseudo(lv) + [(k, w * r.reply, w * c) for k, w, r, c in zip(keys, ws, recs, cum)])
            cum = [c * lv.get(k) for c, k in zip(cum, keys)]
        # P(pay | reply)
        replied = [(r, a) for r, a in zip(recs, ages) if r.reply]
        a0, n0 = self.pay_prior
        sw = sum(self._w(self.pop_decay, a) * r.paid for r, a in replied)
        nw = sum(self._w(self.pop_decay, a) for _, a in replied)
        self.q_global = (a0 + sw) / (n0 + nw)
        cum = [self.q_global] * len(replied)
        for lv in self.pay_levels:
            keys = [lv.keyfn(r) for r, _ in replied]
            ws = [self._w(lv.decay, a) for _, a in replied]
            lv.fit(self._pseudo(lv) + [(k, w * r.paid, w * c) for k, w, (r, _), c in zip(keys, ws, replied, cum)])
            cum = [c * lv.get(k) for c, k in zip(cum, keys)]
        # P(STOP | touch) = p_reply x sigma x effects
        s0, n0 = self.stop_prior
        st = sum(self._w(self.pop_decay, a) * r.stop for r, a in zip(recs, ages))
        ex = sum(self._w(self.pop_decay, a) * r.p_pred for r, a in zip(recs, ages))
        self.sigma = (s0 + st) / (n0 + ex)
        cum = [r.p_pred * self.sigma for r in recs]
        for lv in self.stop_levels:
            keys = [lv.keyfn(r) for r in recs]
            ws = [self._w(lv.decay, a) for a in ages]
            lv.fit(self._pseudo(lv) + [(k, w * r.stop, w * c) for k, w, r, c in zip(keys, ws, recs, cum)])
            cum = [c * lv.get(k) for c, k in zip(cum, keys)]

    # ---------- prediction ----------
    def p_arm(self, band: str, ch: str, slot: str) -> float:
        a0, b0 = self.prior.get((ch, slot), (0.1, 1.9))
        return self.p_global.get((ch, slot), a0 / (a0 + b0)) * self.band.get((band, ch, slot))

    def factors(self, r: Rec) -> list[tuple[str, float]]:
        """(level name, multiplier) for a would-be touch described by `r` (reply/paid/stop unused)."""
        return [(lv.name, lv.get(lv.keyfn(r))) for lv in self.reply_levels]

    def p_reply(self, r: Rec) -> float:
        p = self.p_arm(r.band, r.ch, r.slot)
        for lv in self.reply_levels:
            p *= lv.get(lv.keyfn(r))
        return min(p, 0.95)

    def p_pay(self, r: Rec) -> float:
        q = self.q_global
        for lv in self.pay_levels:
            q *= lv.get(lv.keyfn(r))
        return min(q, 1.0)

    def p_stop(self, r: Rec, p_reply: float) -> float:
        s = p_reply * self.sigma
        for lv in self.stop_levels:
            s *= lv.get(lv.keyfn(r))
        return min(s, 1.0)

    def value(self, p_reply: float, p_pay: float, p_stop: float, emi_rel: float, alpha: float, mu: float) -> float:
        return p_reply * (1.0 + alpha * p_pay * emi_rel) - mu * p_stop

    def borrower_evidence(self, bid: str) -> tuple[float, float, float]:
        """(observed replies, expected replies, kappa) at the borrower level."""
        o, e = self.borrower.counts.get(bid, (0.0, 0.0))
        return o, e, self.borrower.kappa

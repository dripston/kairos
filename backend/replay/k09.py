"""K09 end-to-end replay: K15 DEV simulator + K06 Supervisor/Ledger on the K03 spine + K09 learner.

Every arm runs through the SAME stack on the SAME borrowers, so seed, borrowers, budget and caps
are matched by construction:

    K03 synthetic dataset (spine.synthetic_dataset rows, anchored at the replay start date)
      → spine import (borrowers, consents, loans, EMIs) + K15 portfolio from the same CSV
    candidates (consented channel × slot, approved template, cost in paise)
      → SupervisorFSM.plan     borrower holds, K01 gate per candidate (DPD/band live from the EMI
                               due date, consent from spine consents), rank, atomic reserve
      → SupervisorFSM.dispatch K01 gate re-checked at send time, sent once (touch_sent → spine)
      → DevSimulator.run_day   delivery / meaningful reply / STOP / payment (K15, seeded)
      → Ledger                 record_response, record_stop (spine consents), record_payment_verified
                               (K03 apply_payment on that EMI, bank reference used once)
      → OutcomeFinalizer       one outcome per executed touch when its 24 h window closes
      → learner.apply_outcome  (K09 arm only; idempotent)

Arms differ only in the ranker:
    cn10    CreditNirvana's 10-day cohort cadence (replay/cn10.py) — CN's own strategy doc, stand-in
            until the official fixed-cadence file (K02)
    k15     the K15 placeholder schedule (WhatsApp every 5 days, SMS every 3, morning)
    kairos  K09 Beta-Bernoulli (+ borrower context), seeded Thompson sampling in SIMULATOR mode

Every number is SIMULATED: DEV simulator, synthetic dataset and priors, placeholder costs/caps.
Money is integer paise; INR fields in the report are display conversions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from kairos_backend.borrower_context import BorrowerContextModel, ContextualLearner
from kairos_backend.contracts import ArmScore, Candidate, Mode, Ranking, RecordingSink, replies_per_rupee
from kairos_backend.executor import Executor, FakeProvider
from kairos_backend.learner import BetaBernoulliLearner
from kairos_backend.ledger import Ledger
from kairos_backend.outcomes import OutcomeFinalizer
from kairos_backend.policy import load_policy
from kairos_backend.supervisor import SupervisorFSM
from replay import cn10
from replay import settings as S
from replay.policies import Decision
from replay.portfolio import ContactLog, portfolio_from_csv
from replay.safety import check_day
from replay.simulator import DevSimulator
from spine.importer import parse_row, read_rows
from spine.policy import dpd_band
from spine.synthetic_dataset import COLUMNS, make_row

_HERE = os.path.dirname(os.path.abspath(__file__))
CAMPAIGN_ID = "CMP-K09-REPLAY"
LENDER_ID = "REPLAY-K15"
LENDER_FILE = os.path.join(_HERE, "k15_lender_caps.json")
START = date(2026, 11, 23)  # a Monday; Sundays are K01 quiet days for every arm

# K15 slot name → (contract slot, local send time). The contract's "11-14" slot is unused here.
SLOT_MAP = {"morning": ("08-11", dtime(9, 30)), "afternoon": ("14-17", dtime(14, 30)),
            "evening": ("17-19", dtime(17, 30))}
K15_SLOT = {v[0]: k for k, v in SLOT_MAP.items()}
COST_PAISE = dict(S.COST_PAISE)        # one cost table for ranking AND charging (= contracts.SYNTHETIC_COSTS)
TEMPLATE_LANGS = ("en", "hi")          # K01 templates.languages
BANDS = ("B1", "B2", "B3", "B4")
BASELINES = ("cn10", "k15")
IVR_NOTE = ("CN10 IVR is a one-way voice blast in CN's doc; the K15 DEV simulator models 'ivr' as able to "
            "produce replies, and this replay follows the simulator, so IVR replies count as meaningful.")


def template_for(channel: str, language: str) -> Optional[str]:
    """K22 stand-in: approved template per channel × language; other languages → None (K01: WAIT)."""
    return f"{S.TEMPLATES[channel][0]}_{language.upper()}" if language in TEMPLATE_LANGS else None


# ---------- dataset: the same borrowers for K03, K15 and K09 ----------
def write_replay_dataset(path: str, rows: int, seed: int, as_of: date) -> str:
    """K03's synthetic generator (same seed → same borrowers as scripts/make_dataset.py), with Bounce
    Date anchored at `as_of` so the spine's live DPD on the replay's first day equals the file DPD."""
    rng = random.Random(seed)
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for i in range(rows):
            w.writerow(make_row(i, rng, as_of=as_of))
    return path


@dataclass
class LoanInfo:
    """What the replay needs from the dataset row beyond K15's Borrower (all from the spine row)."""
    loan_id: str
    obligation_id: str
    file_dpd: Optional[int]
    emi_paise: int
    pos_paise: int
    risk_tag: str
    bounce_string: str
    represent: bool
    cohort: str = ""


def load_loans(csv_path: str) -> list[LoanInfo]:
    out = []
    for _, row in read_rows(csv_path):
        r = parse_row(row)
        out.append(LoanInfo(r["loan_id"], f"{r['loan_id']}-OD1", r["file_dpd"], r["emi_paise"], r["pos_paise"],
                            r["risk_tag"], r["bounce_string"], bool(r["represent"])))
    pos_cut = cn10.high_ticket_threshold([x.pos_paise for x in out])
    for x in out:
        x.cohort = cn10.cohort_of(x.risk_tag, x.bounce_string, x.pos_paise, pos_cut)
    return out


class DriftSimulator(DevSimulator):
    """E17 drift scenario: from `drift_day`, one channel's response drops by `factor`.
    Every arm sees the same drift (same seed, same day)."""

    def __init__(self, portfolio, seed, drift_day: Optional[int], channel: str = "whatsapp", factor: float = 0.35):
        super().__init__(portfolio, seed)
        self.drift_day, self.drift_channel, self.factor, self._applied = drift_day, channel, factor, False
        if drift_day is not None:
            self.label = f"{self.label} + E17 drift: {channel} response x{factor} from day {drift_day + 1}"

    def run_day(self, day, decisions, portfolio, log):
        if self.drift_day is not None and day >= self.drift_day and not self._applied:
            for aff in self.affinity:
                aff[self.drift_channel] *= self.factor
            self._applied = True
        return super().run_day(day, decisions, portfolio, log)


class CadenceRanker:
    """contracts.Learner for a fixed cadence: SEND the scheduled channel's morning candidate (and its
    scheduled template, if one is named), else WAIT. Subject to the same K06 gate, caps and budget as
    the adaptive arm. Never learns."""

    def __init__(self, model_version: str):
        self.model_version = model_version
        self.want: dict[str, tuple[str, Optional[str]]] = {}   # borrower_id -> (channel, template or None)
        self.min_reply_prob = 0.0

    def rank(self, band, candidates, mode, rng=None, borrower_id=None, persist=True, at=None) -> Ranking:
        ch, tpl = self.want.get(borrower_id, (None, None))
        for c in candidates:
            if c.channel == ch and c.slot == SLOT_MAP["morning"][0] and (tpl is None or c.template_id == tpl):
                s = ArmScore(band, c.channel, c.slot, 0.0, 0.0, 0, "schedule", 0.0, None, 0.0, c.est_cost_paise,
                             0.0, True, "fixed cadence")
                return Ranking("SEND", s, [s], self.model_version, mode, "fixed cadence schedule")
        return Ranking("WAIT", None, [], self.model_version, mode, "not due today or channel not eligible")

    def apply_outcome(self, outcome) -> bool:
        return False

    def estimates(self, band=None):
        return []


class _NullLearner:
    model_version = "none"

    def apply_outcome(self, outcome) -> bool:
        return False


ARMS = {
    "cn10": cn10.LABEL + " on the K06 stack",
    "k15": "Fixed cadence (K15 placeholder schedule: WhatsApp every 5 days, SMS every 3) on the K06 stack",
    "kairos": "K09 Beta-Bernoulli (+ borrower context) with seeded Thompson sampling on the K06 stack",
}


class StackRun:
    """One arm of the campaign on a fresh ledger DB (spine + K06 + K09 tables in one SQLite file)."""

    def __init__(self, arm: str, csv_path: str, seed: int, days: int, db_path: str, budget_per_case_paise: int,
                 drift_day: Optional[int] = None, context: bool = True, explore: bool = True,
                 start: date = START, portfolio=None, loans=None):
        if arm not in ARMS:
            raise ValueError(f"unknown arm {arm}")
        self.arm, self.seed, self.days, self.start = arm, seed, days, start
        self.explore = explore   # seeded Thompson sampling on arm choice (SIMULATOR); False = posterior mean
        self.context = context
        self.portfolio = copy.deepcopy(portfolio if portfolio is not None else portfolio_from_csv(csv_path))
        self.loans = loans if loans is not None else load_loans(csv_path)
        self.policy = load_policy(lender_files=[LENDER_FILE])
        self.ledger = Ledger(self.policy, db_path)
        self.sink = RecordingSink()
        self.providers = {ch: FakeProvider(ch, cost_paise=COST_PAISE[ch]) for ch in S.CHANNELS}
        self.executor = Executor(self.ledger, self.providers)
        if arm == "kairos":
            base = BetaBernoulliLearner(self.ledger)
            self.ranker = ContextualLearner(base, BorrowerContextModel(self.ledger)) if context else base
        else:
            self.ranker = CadenceRanker("cn10/creditnirvana-10day" if arm == "cn10" else "fixed-cadence/k15-placeholder")
        self.fsm = SupervisorFSM(self.ledger, self.executor, self.ranker, self.sink, mode=Mode.SIMULATOR)
        self.finalizer = OutcomeFinalizer(self.ledger, self.ranker if arm == "kairos" else _NullLearner())
        self.simulator = DriftSimulator(self.portfolio, seed, drift_day)
        self.log = ContactLog()
        self.import_report = self._setup(csv_path, budget_per_case_paise)
        self.decisions = Counter()          # SEND / WAIT / CANCEL / HANDOFF from plan()
        self.wait_rules = Counter()
        self.dispatch = Counter()           # SENT / BLOCKED / SKIPPED / ...
        self.k15_violations = Counter()
        self.scheduled = Counter()          # cadence arms: scheduled steps by channel
        self.nach_notices = 0
        self.daily: list[dict] = []
        self.plan_seconds = 0.0
        self.attempted, self.reached, self.replied, self.paid = set(), set(), set(), set()
        self.spend_paise = 0
        self.replies = self.payments = self.opt_outs = 0
        self.mix = []                      # per day: Counter of (band, channel) sent
        self.preference = []               # per day (K09): band -> top channel/slot by posterior replies/₹

    # ---------- setup ----------
    def _setup(self, csv_path: str, budget_per_case_paise: int) -> dict:
        lg = self.ledger
        rep = lg.import_dataset(csv_path, as_of=self.start)        # K03 importer: borrowers, consents, loans, EMIs
        # allocation period = the replay's contact days, so K01 daily pacing (pool left ÷ contact days
        # left) can hand out the whole matched pool; reply windows still close after it ends.
        lg.upsert_campaign(CAMPAIGN_ID, budget_per_case_paise=int(budget_per_case_paise), start_date=self.start,
                           end_date=self.start + timedelta(days=self.days - 1), name="K09 replay (SIMULATED)")
        lg.enroll(CAMPAIGN_ID, [x.loan_id for x in self.loans])
        for ch in S.CHANNELS:
            for lang in TEMPLATE_LANGS:
                lg.upsert_template(template_for(ch, lang), ch, lang)
        for tid, ch, lang in cn10.all_templates():
            lg.upsert_template(tid, ch, lang)
        with lg.tx() as c:  # K15 placeholder caps as a lender override, in one transaction
            for b in self.portfolio:
                lg._set_state(c, b.borrower_id, lender_id=LENDER_ID)
        return {k: (len(v) if isinstance(v, list) else v) for k, v in rep.items()}

    # ---------- one day ----------
    def candidates(self, b, d: date, channels=None, template=None) -> list[Candidate]:
        out = []
        for ch in (channels if channels is not None else sorted(b.consents)):
            tpl = template or template_for(ch, b.language)
            for k15_slot, (slot, t) in SLOT_MAP.items():
                out.append(Candidate(ch, slot, datetime.combine(d, t), tpl, COST_PAISE[ch]))
        return out

    def _rng(self, day: int, index: int) -> Optional[random.Random]:
        return random.Random(f"{self.seed}:{day}:{index}") if self.explore else None

    def _dpd(self, i: int, day: int) -> Optional[int]:
        """Mirror of the spine's live DPD (the EMI due date never moves; only the clock does)."""
        f = self.loans[i].file_dpd
        return None if f is None else f + day

    def _plan_queue(self, day: int, d: date, plan_at: datetime) -> list[tuple]:
        """(borrower, candidates, wanted channel/template or None) to plan today, in order."""
        def too_soon(b):
            # K15 placeholder rule the K01 gate can't express (K01 gaps are per channel): no contact
            # within MIN_DAYS_BETWEEN_CONTACTS days on ANY channel. Applied identically to every arm.
            last = self.log.last_day(b.index)
            if last is not None and day - last < S.MIN_DAYS_BETWEEN_CONTACTS:
                self.wait_rules["K15_MIN_DAYS_BETWEEN_CONTACTS"] += 1
                return True
            return False

        queue = []
        if self.arm == "kairos":
            order = []
            for b in self.portfolio:
                if b.paid or b.opted_out or not b.consents or too_soon(b):
                    continue
                r = self.ranker.rank(dpd_band(self._dpd(b.index, day)), self.candidates(b, d), Mode.SIMULATOR,
                                     rng=self._rng(day, b.index), borrower_id=b.borrower_id, persist=False,
                                     at=plan_at)
                if r.action == "SEND":
                    # allocate the budget by expected value (posterior mean, borrower-adjusted), not by
                    # the Thompson draw: exploration picks the arm, it shouldn't randomise who is funded
                    c = r.chosen
                    order.append((-replies_per_rupee(c.posterior_mean, c.est_cost_paise),
                                  -(self._dpd(b.index, day) or 0), b.index))
            order.sort()
            return [(self.portfolio[i], self.candidates(self.portfolio[i], d), None) for _, _, i in order]
        if self.arm == "k15":
            for b in self.portfolio:
                if "whatsapp" in b.consents and (day + b.index) % 5 == 0:
                    want = ("whatsapp", None)
                elif "sms" in b.consents and (day + b.index) % 3 == 0:
                    want = ("sms", None)
                else:
                    continue
                if not too_soon(b):
                    queue.append((b, self.candidates(b, d), want))
            return queue
        # cn10: every scheduled step of the borrower's cohort calendar, one channel per plan
        for b in self.portfolio:
            if b.paid:
                continue  # the only early exit is a verified payment
            loan = self.loans[b.index]
            steps = cn10.steps_for(loan.cohort, self._dpd(b.index, day), b.language, loan.represent)
            if not steps or too_soon(b):
                continue
            for ch, tier, tpl in steps:
                self.scheduled[ch] += 1
                queue.append((b, self.candidates(b, d, channels=[ch], template=tpl), (ch, tpl)))
        return queue

    def run_day(self, day: int):
        d = self.start + timedelta(days=day)
        self.ledger.start_day(d)  # daily LMS refresh: spine DPD fresh (K01 stale rule)
        plan_at = datetime.combine(d, dtime(8, 0))
        t0 = time.perf_counter()
        sends = []
        for b, cands, want in self._plan_queue(day, d, plan_at):
            if want is not None:
                self.ranker.want[b.borrower_id] = want
            res = self.fsm.plan(b.borrower_id, self.loans[b.index].obligation_id, CAMPAIGN_ID, None, cands,
                                plan_at, rng=self._rng(day, b.index))
            self.decisions[res.decision] += 1
            if res.decision != "SEND":
                self.wait_rules[res.rule] += 1
                continue
            sends.append((res.send_at, b, res))
        self.plan_seconds += time.perf_counter() - t0

        # dispatch in send-time order (gate re-checked at send time)
        sends.sort(key=lambda s: (s[0], s[1].index, s[2].channel))
        k15_decisions, sent_meta = [], []
        for send_at, b, res in sends:
            at = datetime.fromisoformat(send_at).replace(tzinfo=None)
            out = self.fsm.dispatch(res.instruction_id, at)
            self.dispatch[out.outcome] += 1
            if not out.sent:
                continue
            if "_NACH_" in (res.template_id or ""):
                self.nach_notices += 1
            k15_decisions.append(Decision(b.index, res.channel, K15_SLOT[res.slot], S.TEMPLATES[res.channel][0]))
            sent_meta.append((res, b, at))
            self.finalizer.register_execution(res.instruction_id, b.borrower_id, res.band, res.channel, res.slot, at)

        for rule, _, _ in check_day(day, k15_decisions, self.portfolio, self.log):
            self.k15_violations[rule] += 1

        outcomes = self.simulator.run_day(day, k15_decisions, self.portfolio, self.log)
        for dec in k15_decisions:
            self.log.record(dec.borrower_index, day)

        mix = Counter()
        day_spend = day_replies = day_payments = 0
        for o, (res, b, at) in zip(outcomes, sent_meta):
            self.attempted.add(b.index)
            day_spend += COST_PAISE[o.channel]
            mix[(res.band, o.channel)] += 1
            self.finalizer.record_reach(res.instruction_id, o.delivered, at + timedelta(minutes=1))
            if o.delivered:
                self.reached.add(b.index)
            reply_at = at + timedelta(minutes=90)
            if o.opted_out:
                self.ledger.record_stop(b.borrower_id, reply_at, o.channel)   # spine consents: opted out
                b.opted_out = True
                self.opt_outs += 1
            if o.meaningful_reply:
                self.finalizer.record_reply(b.borrower_id, reply_at, meaningful=True, channel=o.channel)
                self.ledger.record_response(b.borrower_id, reply_at, o.channel)
                self.replied.add(b.index)
                day_replies += 1
            if o.payment_verified:
                paid_at = reply_at + timedelta(hours=2)
                loan = self.loans[b.index]
                self.ledger.record_payment_verified(loan.obligation_id, loan.emi_paise, paid_at,
                                                    payment_id=f"PAY-{res.instruction_id}",
                                                    reference=f"SIM-UTR-{res.instruction_id}")
                self.finalizer.record_payment_verified(b.borrower_id, paid_at)
                b.paid = True
                self.paid.add(b.index)
                day_payments += 1

        # close reply windows that have ended (learning happens here, inside the campaign)
        self.finalizer.finalize_due(datetime.combine(d, dtime(23, 59)))
        if self.arm == "kairos":
            self.preference.append({band: (score_table(self.ranker, band, eligible_only=True) or [None])[0]
                                    for band in BANDS})
        self.spend_paise += day_spend
        self.replies += day_replies
        self.payments += day_payments
        self.mix.append(mix)
        self.daily.append({"day": day + 1, "date": d.isoformat(), "sent": len(k15_decisions),
                           "spend_paise": day_spend, "meaningful_replies": day_replies,
                           "verified_payments": day_payments,
                           "channels": dict(sorted(Counter(ch for (_, ch) in mix.elements()).items()))})

    def run(self) -> dict:
        for day in range(self.days):
            self.run_day(day)
        self.finalizer.finalize_due(datetime.combine(self.start + timedelta(days=self.days + 2), dtime(0, 0)))
        return self.report()

    # ---------- report ----------
    def report(self) -> dict:
        planned = sum(self.decisions.values())
        fm = self.finalizer.metrics(CAMPAIGN_ID)
        envelope_hash = hashlib.sha256(json.dumps(
            [(e["event_type"], e["dedupe_key"], e["payload"].get("decision"), e["payload"].get("instruction_id"))
             for e in self.sink.envelopes]).encode()).hexdigest()[:16]
        out = {
            "arm": self.arm, "label": ARMS[self.arm], "simulator": self.simulator.label,
            "seed": self.seed, "days": self.days, "borrowers": len(self.portfolio),
            "funnel_unique_borrowers": {
                "eligible": sum(1 for b in self.portfolio if b.consents),
                "attempted": len(self.attempted), "reached": len(self.reached),
                "meaningful_reply": len(self.replied), "verified_payment": len(self.paid)},
            "contacts_sent": self.dispatch.get("SENT", 0),
            "spend_paise": self.spend_paise,
            "spend_inr": round(self.spend_paise / 100, 2),
            "meaningful_replies": self.replies,
            "replies_per_1000_inr": round(self.replies / self.spend_paise * 100_000, 2) if self.spend_paise else 0.0,
            "verified_payments": self.payments,
            "opt_outs": self.opt_outs,
            "decisions": dict(sorted(self.decisions.items())),
            "wait_rules": dict(self.wait_rules.most_common(8)),
            "dispatch": dict(sorted(self.dispatch.items())),
            "cap_breaches_k01": len(self.ledger.cap_breaches()),
            "k15_safety_violations": dict(self.k15_violations),
            "outcomes": {k: fm[k] for k in ("executed", "reached_touches", "replies", "no_replies", "pending",
                                            "unresolved", "verified_payments", "late_replies")},
            "planning_seconds": round(self.plan_seconds, 2),
            "decisions_per_second": round(planned / self.plan_seconds) if self.plan_seconds else None,
            "model_version": self.ranker.model_version,
            "crm_envelopes": len(self.sink.envelopes), "envelope_hash": envelope_hash,
            "daily": self.daily,
        }
        if self.arm == "cn10":
            out["cn10"] = {"cohorts": dict(sorted(Counter(x.cohort for x in self.loans).items())),
                           "scheduled_steps": dict(sorted(self.scheduled.items())),
                           "nach_notices_sent": self.nach_notices,
                           "repeat_after_day_10": cn10.REPEAT_AFTER_DAY_10, "ivr_note": IVR_NOTE}
        return out


# ---------- learner views ----------
def preference_switches(run: StackRun, band: str) -> list[dict]:
    """In-campaign switches of the learner's preferred CHANNEL for a band (posterior replies/₹ among
    arms above the reply floor), recorded at the end of each day after reply windows close."""
    out, prev = [], None
    for day, pref in enumerate(run.preference):
        top = pref[band]
        if top is None:
            continue
        if prev is not None and top["channel"] != prev["channel"]:
            out.append({"day": day + 1, "date": (run.start + timedelta(days=day)).isoformat(), "band": band,
                        "from": f"{prev['channel']} {prev['slot']} ({prev['replies_per_inr']}/₹, p={prev['mean']})",
                        "to": f"{top['channel']} {top['slot']} ({top['replies_per_inr']}/₹, p={top['mean']})"})
        prev = top
    return out


def channel_switches(run: StackRun, band: str, min_sends: int = 30) -> list[dict]:
    """Days where the most-sent channel for a band changes (K09 arm). Band-days with fewer
    than `min_sends` sends are ignored so a handful of touches can't count as a switch."""
    out, prev = [], None
    for day, mix in enumerate(run.mix):
        by_ch = {ch: n for (b, ch), n in mix.items() if b == band}
        if sum(by_ch.values()) < min_sends:
            continue
        top = max(sorted(by_ch), key=lambda ch: by_ch[ch])
        if prev is not None and top != prev:
            out.append({"day": day + 1, "date": (run.start + timedelta(days=day)).isoformat(), "band": band,
                        "from": prev, "to": top, "sent": by_ch})
        prev = top
    return out


def score_table(learner, band: str, eligible_only: bool = False) -> list[dict]:
    """Posterior replies/₹ per channel × slot for a band (what PRODUCTION ranking would use).
    eligible_only drops arms below the learner's minimum reply probability (they can only WAIT)."""
    rows = []
    for e in learner.estimates(band):
        if e["slot"] not in K15_SLOT:
            continue
        if eligible_only and e["mean"] < learner.min_reply_prob:
            continue
        rows.append({"channel": e["channel"], "slot": K15_SLOT[e["slot"]], "mean": round(e["mean"], 4),
                     "replies_per_inr": round(replies_per_rupee(e["mean"], COST_PAISE[e["channel"]]), 3),
                     "n_obs": e["n_obs"], "source": e["source"]})
    return sorted(rows, key=lambda r: (-r["replies_per_inr"], r["channel"], r["slot"]))


def cohort_close(learner, min_obs: int = 30) -> dict:
    """What could carry forward to the next cohort (proposal only; Ojas approves a versioned policy)."""
    carry, sparse = [], []
    for e in learner.estimates():
        if e["slot"] not in K15_SLOT:
            continue
        row = {"band": e["band"], "channel": e["channel"], "slot": K15_SLOT[e["slot"]],
               "mean": round(e["mean"], 4), "n_obs": e["n_obs"]}
        (carry if e.get("segment_n_obs", e["n_obs"]) >= min_obs else sparse).append(row)
    return {"model_version": learner.model_version,
            "carry_forward_candidates": sorted(carry, key=lambda r: (r["band"], -r["mean"])),
            "do_not_carry_sparse": len(sparse),
            "rule": f"segment estimates with >= {min_obs} finalized outcomes; everything else falls back to "
                    "the pooled prior next cohort",
            "status": "PROPOSAL (SIMULATED evidence; needs K01 owner approval and a new policy/model version)"}


# ---------- the comparison ----------
def run_comparison(n_borrowers: int, days: int, seed: int, drift_day: Optional[int] = 6,
                   budget_per_case_paise: Optional[int] = None, workdir: Optional[str] = None,
                   baselines: tuple = ("cn10",), context: bool = True, explore: bool = True,
                   dataset: Optional[str] = None, start: Optional[date] = None) -> dict:
    """budget_per_case_paise=None → MATCHED SPEND: the shared pool is set to what the FIRST baseline
    spends when unconstrained (a calibration run), so every arm runs on the same money and K09 has
    to allocate it better. Pass a number to fix the pool instead.

    dataset=None generates K03's synthetic dataset (same seed → same borrowers) anchored at `start`;
    a dataset path is used as given (start defaults to the K03 dataset as-of date, so DPD = file DPD)."""
    for b in baselines:
        if b not in BASELINES:
            raise ValueError(f"unknown baseline {b} (choose from {BASELINES})")
    workdir = workdir or tempfile.mkdtemp(prefix="k09-replay-")
    if dataset is None:
        start = start or START
        csv_path = write_replay_dataset(os.path.join(workdir, f"synthetic_{n_borrowers}_seed{seed}.csv"),
                                        n_borrowers, seed, start)
        dataset_basis = (f"K03 synthetic generator, seed {seed}, {n_borrowers} rows, Bounce Date anchored at "
                         f"{start.isoformat()}")
    else:
        from spine.config import DATASET_AS_OF_DATE
        start = start or DATASET_AS_OF_DATE
        csv_path = dataset
        dataset_basis = f"dataset file {dataset} (as-of {start.isoformat()})"
    portfolio, loans = portfolio_from_csv(csv_path), load_loans(csv_path)
    n_borrowers = len(portfolio)

    def stack(arm, name, per_case):
        return StackRun(arm, csv_path, seed, days, os.path.join(workdir, f"{name}.db"), per_case, drift_day,
                        context=context, explore=explore, start=start, portfolio=portfolio, loans=loans)

    budget_basis = "fixed"
    calibration = None
    if budget_per_case_paise is None:
        generous = max(COST_PAISE.values()) * days   # ≥ 1 contact/day at the dearest channel: never binds
        cal = stack(baselines[0], f"calibration-{baselines[0]}", generous)
        cal.run()
        calibration = {"arm": baselines[0], "spend_paise": cal.spend_paise, "contacts_sent": cal.dispatch.get("SENT", 0)}
        budget_per_case_paise = max(1, math.ceil(cal.spend_paise / n_borrowers))
        budget_basis = (f"matched to {baselines[0]} fixed-cadence spend (calibration run "
                        f"₹{cal.spend_paise / 100:,.2f}, unconstrained)")
    runs, before = {}, None
    for arm in (*baselines, "kairos"):
        run = stack(arm, arm, budget_per_case_paise)
        if arm == "kairos":
            before = {band: score_table(run.ranker, band) for band in ("B1", "B2")}
        runs[arm] = run
        run.run()
    k = runs["kairos"]
    out = {
        "provenance": "SIMULATED (DEV simulator, synthetic dataset and priors, placeholder costs/caps/templates)",
        "baseline_arm": baselines[0],
        "baseline_note": ("cn10 = CreditNirvana's own 10-day post-due strategy doc (HDFC project), used as a "
                          "stand-in until the official fixed-cadence policy file (K02) arrives; k15 = the K15 "
                          "placeholder schedule"),
        "matched": {"seed": seed, "borrowers": n_borrowers, "days": days, "start": start.isoformat(),
                    "dataset": dataset_basis, "budget_per_case_paise": budget_per_case_paise,
                    "budget_basis": budget_basis, "budget_pool_paise": budget_per_case_paise * n_borrowers,
                    "calibration": calibration,
                    "caps": f"K01 v{k.policy.version} + lender override {LENDER_ID} (1/day, 3 per 7 days)"
                            f" + K15 {S.MIN_DAYS_BETWEEN_CONTACTS}-day cross-channel gap (replay pre-filter)",
                    "costs_paise": COST_PAISE, "drift_day": None if drift_day is None else drift_day + 1,
                    "kairos_context": context, "kairos_explore": explore},
        "import": k.import_report,
        "baseline": runs[baselines[0]].report(),
        "baselines": {b: runs[b].report() for b in baselines},
        "kairos": k.report(),
        "scores_before": before,
        "scores_after": {band: score_table(k.ranker, band)[:4] for band in ("B1", "B2")},
        "preference_switches": {band: preference_switches(k, band) for band in BANDS},
        "send_mix_switches": {band: channel_switches(k, band) for band in BANDS},
        "preference_by_day": [{b: (f"{v['channel']} {v['slot']}" if v else None) for b, v in day.items()}
                              for day in k.preference],
        "cohort_close": cohort_close(k.ranker),
        "ivr_note": IVR_NOTE,
        "dbs": {arm: os.path.join(workdir, f"{arm}.db") for arm in runs},
    }
    for run in runs.values():
        run.ledger.close()
    return out

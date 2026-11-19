"""K09 end-to-end replay: K15 DEV simulator + backend K06 Supervisor/Ledger + K09 learner/outcomes.

Both arms (fixed cadence and adaptive K09) run through the SAME stack, so seed, budget and caps
are matched by construction:

    candidates (consented channel × slot, approved template, cost)
      → SupervisorFSM.plan     borrower holds, K01 gate per candidate, rank, atomic reserve
      → SupervisorFSM.dispatch K01 gate re-checked at send time, sent once
      → DevSimulator.run_day   delivery / meaningful reply / STOP / payment (K15, seeded)
      → Ledger                 record_response (stop-for-day), record_stop, record_payment_verified
      → OutcomeFinalizer       one outcome per executed touch when its 24 h window closes
      → learner.apply_outcome  (adaptive arm only; idempotent)

The only difference between the arms is the ranker: FixedCadenceRanker (the K15 placeholder
schedule) or BetaBernoulliLearner (seeded Thompson sampling, SIMULATOR mode). Every number is
SIMULATED: DEV simulator, synthetic priors, placeholder costs/caps.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from kairos_backend.contracts import ArmScore, Candidate, Mode, Ranking, RecordingSink  # noqa: E402
from kairos_backend.executor import Executor, FakeProvider  # noqa: E402
from kairos_backend.learner import BetaBernoulliLearner  # noqa: E402
from kairos_backend.ledger import Ledger  # noqa: E402
from kairos_backend.outcomes import OutcomeFinalizer  # noqa: E402
from kairos_backend.policy import load_policy  # noqa: E402
from kairos_backend.supervisor import SupervisorFSM  # noqa: E402

from replay import settings as S  # noqa: E402
from replay.policies import Decision  # noqa: E402
from replay.portfolio import ContactLog, generate_portfolio  # noqa: E402
from replay.safety import check_day  # noqa: E402
from replay.simulator import DevSimulator  # noqa: E402
from spine.policy import dpd_band  # noqa: E402

CAMPAIGN_ID = "CMP-K09-REPLAY"
LENDER_ID = "REPLAY-K15"
LENDER_FILE = os.path.join(_HERE, "k15_lender_caps.json")
START = date(2026, 11, 23)  # a Monday; Sundays are K01 quiet days for both arms

# K15 slot name → (contract slot, local send time). The contract's "11-14" slot is unused here.
SLOT_MAP = {"morning": ("08-11", dtime(9, 30)), "afternoon": ("14-17", dtime(14, 30)),
            "evening": ("17-19", dtime(17, 30))}
K15_SLOT = {v[0]: k for k, v in SLOT_MAP.items()}
COST_INR = {ch: p / 100 for ch, p in S.COST_PAISE.items()}   # one cost table for ranking AND charging
TEMPLATE_LANGS = ("en", "hi")                                  # K01 templates.languages


def template_for(channel: str, language: str) -> Optional[str]:
    """K22 stand-in: approved template per channel × language; other languages → None (K01: WAIT)."""
    return f"{S.TEMPLATES[channel][0]}_{language.upper()}" if language in TEMPLATE_LANGS else None


class DriftSimulator(DevSimulator):
    """E17 drift scenario: from `drift_day`, one channel's response drops by `factor`.
    Both arms see the same drift (same seed, same day)."""

    def __init__(self, portfolio, seed, drift_day: Optional[int], channel: str = "whatsapp", factor: float = 0.35):
        super().__init__(portfolio, seed)
        self.drift_day, self.drift_channel, self.factor, self._applied = drift_day, channel, factor, False
        if drift_day is not None:
            self.label = (f"{self.label} + E17 drift: {channel} response x{factor} from day {drift_day + 1}")

    def run_day(self, day, decisions, portfolio, log):
        if self.drift_day is not None and day >= self.drift_day and not self._applied:
            for aff in self.affinity:
                aff[self.drift_channel] *= self.factor
            self._applied = True
        return super().run_day(day, decisions, portfolio, log)


class FixedCadenceRanker:
    """contracts.Learner for the baseline: the K15 schedule (WhatsApp every 5 days, SMS every 3,
    morning), now subject to the same K06 gate, caps and budget as the adaptive arm. Never learns."""

    model_version = "fixed-cadence/k15-placeholder"

    def __init__(self):
        self.due: dict[str, str] = {}

    def rank(self, band, candidates, mode, rng=None, borrower_id=None, persist=True, at=None) -> Ranking:
        want = self.due.get(borrower_id)
        for c in candidates:
            if c.channel == want and c.slot == SLOT_MAP["morning"][0]:
                s = ArmScore(band, c.channel, c.slot, 0.0, 0.0, 0, "schedule", 0.0, None, 0.0, c.est_cost_inr,
                             0.0, True, "fixed cadence")
                return Ranking("SEND", s, [s], self.model_version, mode, "fixed cadence schedule")
        return Ranking("WAIT", None, [], self.model_version, mode, "not due today or channel not eligible")

    def apply_outcome(self, outcome) -> bool:
        return False

    def estimates(self, band=None):
        return []


@dataclass
class ArmConfig:
    name: str            # "baseline" | "kairos"
    label: str


ARMS = {"baseline": ArmConfig("baseline", "Fixed cadence (K15 placeholder schedule) on the K06 stack"),
        "kairos": ArmConfig("kairos", "K09 Beta-Bernoulli + seeded Thompson sampling on the K06 stack")}


class StackRun:
    """One arm of the campaign on a fresh ledger DB."""

    def __init__(self, arm: str, portfolio, seed: int, days: int, db_path: str,
                 budget_per_case_inr: float, drift_day: Optional[int] = None):
        self.arm, self.seed, self.days = arm, seed, days
        self.portfolio = copy.deepcopy(portfolio)
        self.policy = load_policy(lender_files=[LENDER_FILE])
        self.ledger = Ledger(self.policy, db_path)
        self.sink = RecordingSink()
        self.providers = {ch: FakeProvider(ch, cost_inr=COST_INR[ch]) for ch in S.CHANNELS}
        self.executor = Executor(self.ledger, self.providers)
        if arm == "kairos":
            self.ranker = BetaBernoulliLearner(self.ledger)
        else:
            self.ranker = FixedCadenceRanker()
        self.fsm = SupervisorFSM(self.ledger, self.executor, self.ranker, self.sink, mode=Mode.SIMULATOR)
        self.finalizer = OutcomeFinalizer(self.ledger, self.ranker if arm == "kairos" else _NullLearner())
        self.simulator = DriftSimulator(self.portfolio, seed, drift_day)
        self.log = ContactLog()
        self._setup(budget_per_case_inr)
        self.decisions = Counter()          # SEND / WAIT / CANCEL / HANDOFF from plan()
        self.wait_rules = Counter()
        self.dispatch = Counter()           # SENT / BLOCKED / SKIPPED / ...
        self.k15_violations = Counter()
        self.daily: list[dict] = []
        self.plan_seconds = 0.0
        self.attempted, self.reached, self.replied, self.paid = set(), set(), set(), set()
        self.spend = 0.0
        self.replies = self.payments = self.opt_outs = 0
        self.mix = []                      # per day: Counter of (band, channel) sent
        self.preference = []               # per day (adaptive): band -> top channel/slot by posterior replies/₹

    # ---------- setup ----------
    def _setup(self, budget_per_case_inr: float):
        lg = self.ledger
        end = START + timedelta(days=self.days + 2)
        lg.upsert_campaign(CAMPAIGN_ID, budget_per_case_inr=budget_per_case_inr, allocated_cases=len(self.portfolio),
                           start_date=START, end_date=end, name="K09 replay (SIMULATED)")
        for ch in S.CHANNELS:
            for lang in TEMPLATE_LANGS:
                lg.upsert_template(template_for(ch, lang), ch, lang)
        when = datetime.combine(START, dtime(7, 0))
        with lg.tx() as c:  # bulk load in one transaction (same columns upsert_borrower writes)
            for b in self.portfolio:
                consent = {ch: (ch in b.consents) for ch in S.CHANNELS}
                c.execute("INSERT INTO borrowers (id, dpd, dpd_updated_at, lender_id, consent, consent_version,"
                          " verified_overdue, opted_out) VALUES (?,?,?,?,?,?,1,?)",
                          (b.borrower_id, b.dpd, lg._iso(when), LENDER_ID, json.dumps(consent),
                           json.dumps({ch: "synthetic-v1" for ch in b.consents}), int(b.opted_out)))
                c.execute("INSERT INTO obligations (id, borrower_id, campaign_id, overdue_inr) VALUES (?,?,?,?)",
                          (f"{b.borrower_id}-EMI", b.borrower_id, CAMPAIGN_ID, 5000.0))

    def _refresh_dpd(self, day: int):
        """New DPD feed each morning: +1 day, timestamp refreshed (K01: stale DPD holds outreach)."""
        if day == 0:
            return
        with self.ledger.tx() as c:
            c.execute("UPDATE borrowers SET dpd = dpd + 1, dpd_updated_at = ?",
                      (self.ledger._iso(datetime.combine(START + timedelta(days=day), dtime(7, 0))),))
        for b in self.portfolio:
            b.dpd += 1

    # ---------- one day ----------
    def candidates(self, b, d: date) -> list[Candidate]:
        out = []
        for ch in sorted(b.consents):
            tpl = template_for(ch, b.language)
            for k15_slot, (slot, t) in SLOT_MAP.items():
                out.append(Candidate(ch, slot, datetime.combine(d, t), tpl, COST_INR[ch]))
        return out

    def _rng(self, day: int, index: int) -> random.Random:
        return random.Random(f"{self.seed}:{day}:{index}")

    def run_day(self, day: int):
        d = START + timedelta(days=day)
        self._refresh_dpd(day)
        self.ledger.start_day(d)
        plan_at = datetime.combine(d, dtime(8, 0))
        t0 = time.perf_counter()

        # K15 placeholder rule the K01 gate can't express (K01 gaps are per channel): no contact
        # within MIN_DAYS_BETWEEN_CONTACTS days on ANY channel. Applied identically to both arms.
        def too_soon(b):
            last = self.log.last_day(b.index)
            if last is not None and day - last < S.MIN_DAYS_BETWEEN_CONTACTS:
                self.wait_rules["K15_MIN_DAYS_BETWEEN_CONTACTS"] += 1
                return True
            return False

        # who to plan, in which order
        if self.arm == "kairos":
            order = []
            for b in self.portfolio:
                if b.paid or b.opted_out or not b.consents or too_soon(b):
                    continue
                band = dpd_band(b.dpd)
                r = self.ranker.rank(band, self.candidates(b, d), Mode.SIMULATOR, rng=self._rng(day, b.index),
                                     borrower_id=b.borrower_id, persist=False, at=plan_at)
                if r.action == "SEND":
                    order.append((-r.chosen.replies_per_inr, -b.dpd, b.index))
            order.sort()
            planned = [self.portfolio[i] for _, _, i in order]
        else:
            self.ranker.due = {}
            planned = []
            for b in self.portfolio:
                if "whatsapp" in b.consents and (day + b.index) % 5 == 0:
                    self.ranker.due[b.borrower_id] = "whatsapp"
                elif "sms" in b.consents and (day + b.index) % 3 == 0:
                    self.ranker.due[b.borrower_id] = "sms"
                else:
                    continue
                if too_soon(b):
                    continue
                planned.append(b)

        sends = []
        for b in planned:
            band = dpd_band(b.dpd)
            res = self.fsm.plan(b.borrower_id, f"{b.borrower_id}-EMI", CAMPAIGN_ID, band, self.candidates(b, d),
                                plan_at, rng=self._rng(day, b.index))
            self.decisions[res.decision] += 1
            if res.decision != "SEND":
                self.wait_rules[res.rule] += 1
                continue
            sends.append((res.send_at, b, res))
        self.plan_seconds += time.perf_counter() - t0

        # dispatch in send-time order (gate re-checked at send time)
        sends.sort(key=lambda s: (s[0], s[1].index))
        k15_decisions, sent_meta = [], []
        for send_at, b, res in sends:
            at = datetime.fromisoformat(send_at).replace(tzinfo=None)
            out = self.fsm.dispatch(res.instruction_id, at)
            self.dispatch[out.outcome] += 1
            if not out.sent:
                continue
            k15_decisions.append(Decision(b.index, res.channel, K15_SLOT[res.slot], S.TEMPLATES[res.channel][0]))
            sent_meta.append((res, b, at))
            self.finalizer.register_execution(res.instruction_id, b.borrower_id, dpd_band(b.dpd), res.channel,
                                              res.slot, at)

        for rule, _, _ in check_day(day, k15_decisions, self.portfolio, self.log):
            self.k15_violations[rule] += 1

        outcomes = self.simulator.run_day(day, k15_decisions, self.portfolio, self.log)
        for dec in k15_decisions:
            self.log.record(dec.borrower_index, day)

        mix = Counter()
        day_spend = day_replies = day_payments = 0
        for o, (res, b, at) in zip(outcomes, sent_meta):
            self.attempted.add(b.index)
            day_spend += COST_INR[o.channel]
            mix[(dpd_band(b.dpd), o.channel)] += 1
            self.finalizer.record_reach(res.instruction_id, o.delivered, at + timedelta(minutes=1))
            if o.delivered:
                self.reached.add(b.index)
            reply_at = at + timedelta(minutes=90)
            if o.opted_out:
                self.ledger.record_stop(b.borrower_id, reply_at, o.channel)
                b.opted_out = True
                self.opt_outs += 1
            if o.meaningful_reply:
                self.finalizer.record_reply(b.borrower_id, reply_at, meaningful=True, channel=o.channel)
                self.ledger.record_response(b.borrower_id, reply_at, o.channel)
                self.replied.add(b.index)
                day_replies += 1
            if o.payment_verified:
                paid_at = reply_at + timedelta(hours=2)
                self.ledger.record_payment_verified(b.borrower_id, f"{b.borrower_id}-EMI", 5000.0, paid_at)
                self.finalizer.record_payment_verified(b.borrower_id, paid_at)
                b.paid = True
                self.paid.add(b.index)
                day_payments += 1

        # close reply windows that have ended (learning happens here, inside the campaign)
        self.finalizer.finalize_due(datetime.combine(d, dtime(23, 59)))
        if self.arm == "kairos":
            self.preference.append({band: score_table(self.ranker, band, eligible_only=True)[0]
                                    for band in ("B1", "B2", "B3", "B4")})
        self.spend += day_spend
        self.replies += day_replies
        self.payments += day_payments
        self.mix.append(mix)
        self.daily.append({"day": day + 1, "date": d.isoformat(), "sent": len(k15_decisions),
                           "spend_inr": round(day_spend, 2), "meaningful_replies": day_replies,
                           "verified_payments": day_payments,
                           "channels": dict(Counter(ch for (_, ch) in mix.elements()))})

    def run(self) -> dict:
        for day in range(self.days):
            self.run_day(day)
        self.finalizer.finalize_due(datetime.combine(START + timedelta(days=self.days + 2), dtime(0, 0)))
        return self.report()

    # ---------- report ----------
    def report(self) -> dict:
        planned = sum(self.decisions.values())
        fm = self.finalizer.metrics(CAMPAIGN_ID)
        envelope_hash = hashlib.sha256(json.dumps(
            [(e["event_type"], e["dedupe_key"], e["payload"].get("decision"), e["payload"].get("instruction_id"))
             for e in self.sink.envelopes]).encode()).hexdigest()[:16]
        return {
            "arm": self.arm, "label": ARMS[self.arm].label, "simulator": self.simulator.label,
            "seed": self.seed, "days": self.days, "borrowers": len(self.portfolio),
            "funnel_unique_borrowers": {
                "eligible": sum(1 for b in self.portfolio if b.consents),
                "attempted": len(self.attempted), "reached": len(self.reached),
                "meaningful_reply": len(self.replied), "verified_payment": len(self.paid)},
            "contacts_sent": self.dispatch.get("SENT", 0),
            "spend_inr": round(self.spend, 2),
            "meaningful_replies": self.replies,
            "replies_per_1000_inr": round(self.replies / self.spend * 1000, 2) if self.spend else 0.0,
            "verified_payments": self.payments,
            "opt_outs": self.opt_outs,
            "decisions": dict(self.decisions), "wait_rules": dict(self.wait_rules.most_common(8)),
            "dispatch": dict(self.dispatch),
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


class _NullLearner:
    model_version = "none"

    def apply_outcome(self, outcome) -> bool:
        return False


def preference_switches(run: StackRun, band: str) -> list[dict]:
    """In-campaign switches of the learner's preferred CHANNEL for a band (posterior replies/₹ among
    arms above the reply floor), recorded at the end of each day after reply windows close."""
    out, prev = [], None
    for day, pref in enumerate(run.preference):
        top = pref[band]
        if prev is not None and top["channel"] != prev["channel"]:
            out.append({"day": day + 1, "date": (START + timedelta(days=day)).isoformat(), "band": band,
                        "from": f"{prev['channel']} {prev['slot']} ({prev['replies_per_inr']}/₹, p={prev['mean']})",
                        "to": f"{top['channel']} {top['slot']} ({top['replies_per_inr']}/₹, p={top['mean']})"})
        prev = top
    return out


def channel_switches(run: StackRun, band: str, min_sends: int = 30) -> list[dict]:
    """Days where the most-sent channel for a band changes (adaptive arm). Band-days with fewer
    than `min_sends` sends are ignored so a handful of touches can't count as a switch."""
    out, prev = [], None
    for day, mix in enumerate(run.mix):
        by_ch = {ch: n for (b, ch), n in mix.items() if b == band}
        if sum(by_ch.values()) < min_sends:
            continue
        top = max(sorted(by_ch), key=lambda ch: by_ch[ch])
        if prev is not None and top != prev:
            out.append({"day": day + 1, "date": (START + timedelta(days=day)).isoformat(), "band": band,
                        "from": prev, "to": top, "sent": by_ch})
        prev = top
    return out


def score_table(learner: BetaBernoulliLearner, band: str, eligible_only: bool = False) -> list[dict]:
    """Posterior replies/₹ per channel × slot for a band (what PRODUCTION ranking would use).
    eligible_only drops arms below the learner's minimum reply probability (they can only WAIT)."""
    rows = []
    for e in learner.estimates(band):
        if e["slot"] not in K15_SLOT:
            continue
        if eligible_only and e["mean"] < learner.min_reply_prob:
            continue
        cost = COST_INR[e["channel"]]
        rows.append({"channel": e["channel"], "slot": K15_SLOT[e["slot"]], "mean": round(e["mean"], 4),
                     "replies_per_inr": round(e["mean"] / cost, 3), "n_obs": e["n_obs"], "source": e["source"]})
    return sorted(rows, key=lambda r: -r["replies_per_inr"])


def cohort_close(learner: BetaBernoulliLearner, min_obs: int = 30) -> dict:
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


def run_comparison(n_borrowers: int, days: int, seed: int, drift_day: Optional[int] = 6,
                   budget_per_case_inr: Optional[float] = None, workdir: Optional[str] = None) -> dict:
    """budget_per_case_inr=None → MATCHED SPEND: the shared pool is set to what the fixed cadence
    spends when unconstrained (a calibration run), so both arms run on the same money and the
    adaptive arm has to allocate it better. Pass a number to fix the pool instead."""
    portfolio = generate_portfolio(n_borrowers, seed)
    workdir = workdir or tempfile.mkdtemp(prefix="k09-replay-")
    budget_basis = "fixed"
    if budget_per_case_inr is None:
        generous = round(S.DAILY_BUDGET_PAISE / 100 / S.N_BORROWERS * days, 4)  # K15 placeholder, scaled
        cal = StackRun("baseline", portfolio, seed, days, os.path.join(workdir, "calibration.db"), generous, drift_day)
        cal.run()
        budget_per_case_inr = round(max(cal.spend, 1.0) / n_borrowers, 6)
        budget_basis = f"matched to fixed-cadence spend (calibration run ₹{cal.spend:,.2f})"
    runs = {}
    for arm in ("baseline", "kairos"):
        run = StackRun(arm, portfolio, seed, days, os.path.join(workdir, f"{arm}.db"), budget_per_case_inr, drift_day)
        if arm == "kairos":
            before = {band: score_table(run.ranker, band) for band in ("B1", "B2")}
        runs[arm] = run
        run.run()
    k = runs["kairos"]
    return {
        "provenance": "SIMULATED (DEV simulator, synthetic priors, placeholder costs/caps/templates)",
        "matched": {"seed": seed, "borrowers": n_borrowers, "days": days, "start": START.isoformat(),
                    "budget_per_case_inr": budget_per_case_inr, "budget_basis": budget_basis,
                    "budget_pool_inr": round(budget_per_case_inr * n_borrowers, 2),
                    "caps": f"K01 v{k.policy.version} + lender override {LENDER_ID} (1/day, 3 per 7 days)"
                            f" + K15 {S.MIN_DAYS_BETWEEN_CONTACTS}-day cross-channel gap (replay pre-filter)",
                    "costs_inr": COST_INR, "drift_day": None if drift_day is None else drift_day + 1},
        "baseline": runs["baseline"].report(),
        "kairos": k.report(),
        "scores_before": before,
        "scores_after": {band: score_table(k.ranker, band)[:4] for band in ("B1", "B2")},
        "preference_switches": {band: preference_switches(k, band) for band in ("B1", "B2", "B3", "B4")},
        "send_mix_switches": {band: channel_switches(k, band) for band in ("B1", "B2", "B3", "B4")},
        "preference_by_day": [{b: f"{v['channel']} {v['slot']}" for b, v in day.items()} for day in k.preference],
        "cohort_close": cohort_close(k.ranker),
        "dbs": {arm: os.path.join(workdir, f"{arm}.db") for arm in runs},
    }

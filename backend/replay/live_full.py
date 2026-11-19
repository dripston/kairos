"""Live view, full mode (`POST /demo/live/start {"mode": "full"}`): one SIMULATED campaign through EVERY component.

Race mode (replay/live.py, the default) compares K09 with CN10 and must never change. Full mode runs ONE arm
(K09 + K27 auto-churn, `kairos_churn`) on the real modules, all on one fresh SQLite file, and turns what they
return into display events (schema: backend/README.md "Live view: full mode events"):

    spine     K03 import (K24 intake path), campaign enrolment, budget scope, day roll-over (BROKEN_PTP)
    churn     K27 ChurnEngine: day-0 cohort / risk tier / layer per borrower, moves on live events and daily ticks
    learner   K09 ContextualLearner via SupervisorFSM.plan: contact (channel, slot, template, why) or wait, per
              borrower per day; day-end posterior moves; the channel switch after the day-7 WhatsApp drift
    aladdin   K06 Gate: allow / block with the rule at plan time, re-check at approval and at dispatch
    hitl      kairos_backend.hitl: approvals (VOICE_CHANNEL, FIRST_SEND_AFTER_HARDSHIP, …) decided by a simulated
              supervisor, or by a person through POST /demo/live/approvals/{id}/approve|reject (interactive);
              cases opened by K13 / voice handoffs and worked by the simulated supervisor
    executor  Executor + MockChannelProvider (sms, whatsapp, ivr, voicebot; dry-run, nothing leaves the process)
              and FakeProvider (email)
    channel   delivery / read receipts through channels.normalize.apply_callback (duplicates ignored)
    borrower  DEV simulator outcomes; a reply becomes synthetic TEXT (English / Hinglish / Hindi, seeded)
    intent    IntentStack (rules → small model → abstain; no LLM tier is configured here) → K13 action
    voice     voice.registry.select_bot (Ritu / Advait) for the K27 voice-first step, a Sarvam outbound
              call-end payload in the real schema, voice.ingest.VoiceIngester (thresholds → holds / STOP / review)
    cm03      cm03.engine.ConversationEngine: OTP first, balance only after OTP, fake UTR → FRAUD_SUSPECT,
              D7-settled claim → verified payment, STOP on WhatsApp → all channels
    payments  Ledger.record_payment_verified (only that EMI's queued touches are cancelled), claims verified or
              rejected (F4: no D7 credit by T+2), promises kept / broken
    outcomes  OutcomeFinalizer: one outcome per executed touch when its 24 h window closes → learner update
    reports   K26 daily_segment_report per day, cap breaches from the ledger

Full-mode demo rules (SIMULATED, documented, not in the race):
  * budget: a fixed demo pool (BUDGET_PER_CASE_PAISE per borrower), not the race's matched budget.
  * voice-first lane: when the borrower's K27 journey step today names a voice channel and the borrower has
    voice-bot consent, the call is placed by the Sarvam bot (at most VOICE_PER_DAY a day). It still goes through
    the Gate, the HITL approval queue and the send-time re-check.
  * DEV-simulator outcome → reply text / call disposition / promise-keeping are seeded mappings (see _category).
  * CM-03: ~4% of borrowers write in on a seeded day, plus one borrower a day who pays through WhatsApp.

Deterministic by seed (ticket / conversation ids from uuid never reach the events). No PII: borrower ids and
synthetic first names only; phones are never put in an event. Nothing is sent anywhere.
"""
from __future__ import annotations

import hashlib
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from kairos_backend import reports as k26
from kairos_backend.channels.mock import MockChannelProvider
from kairos_backend.channels.normalize import apply_callback
from kairos_backend.contracts import Mode, RecordingSink
from kairos_backend.exceptions import ExceptionEvent
from kairos_backend.executor import Executor, FakeProvider, Supervisor
from kairos_backend.hitl import HITL, HitlError
from kairos_backend.intent import IntentClassifier
from kairos_backend.intent_stack import IntentStack
from kairos_backend.supervisor import SupervisorFSM
from kairos_backend.voice import registry as voice_registry
from kairos_backend.voice.ingest import VoiceIngester
from replay import k09
from replay import live as L
from replay import settings as S
from replay.portfolio import portfolio_from_csv

PROVENANCE = "SIMULATED"
COMPONENTS = ("spine", "churn", "learner", "aladdin", "executor", "channel", "borrower", "intent", "voice", "cm03",
              "hitl", "payments", "outcomes", "reports")
ARM = "kairos_churn"
CAMPAIGN = k09.CAMPAIGN_ID
DRIFT_DAY = L.DRIFT_DAY
BUDGET_PER_CASE_PAISE = 500        # ₹5 per borrower for the campaign (demo pool, SIMULATED)
VOICE_PER_DAY = 6                  # voice-first lane: Sarvam bot calls per day at most
CM03_SHARE = 0.04                  # share of borrowers who write in on WhatsApp on a seeded day
OTP = "135790"                     # fixture OTP for CM-03 (never shown; events say <OTP>)
SUPERVISOR = ("sup.simulated", "supervisor")
HUMAN = ("demo.supervisor", "supervisor")
APPROVAL_CUT = 0.345               # display position where the approval window ends (before the 09:30 sends)
CH_NAME, CH_ARTICLE, SLOT_WORD = L.CH_NAME, L.CH_ARTICLE, L.SLOT_WORD
LOWER_CH = {"ivr": "voice call", "voicebot": "voice-bot call", "email": "email"}
MONEY = re.compile(r"(₹|rs\.?|inr)\s*[\d,]+", re.I)


def _u(*parts) -> float:
    """Seeded uniform in [0, 1) from any parts (stable across processes)."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:15], 16) / 16 ** 15


def _pick(seq, *parts):
    return seq[int(_u(*parts) * len(seq)) % len(seq)]


def _short_ref(ref: Optional[str]) -> Optional[str]:
    """Provider reference for display: the adapter prefix and the last 4 characters (never a long digit run)."""
    if not ref:
        return None
    head = ref.rsplit("-", 1)[0] if "-" in ref else "ref"
    return f"{head}-…{ref[-4:]}"


def _chn(ch: str) -> str:
    """Channel name for the middle of a sentence ("Asha's voice-bot call", "Asha's WhatsApp")."""
    return LOWER_CH.get(ch, CH_NAME.get(ch, ch))


def _pos(at: datetime) -> float:
    """Display position in the day: before 08:00 → 0–0.04, planning has its own 0.04–0.30 slots,
    08:00–09:30 (approvals) → 0.30–0.35, 09:30–22:00 → 0.35–0.99."""
    m = at.hour * 60 + at.minute
    if m < 480:
        return 0.04 * m / 480
    if m < 570:
        return 0.30 + 0.05 * (m - 480) / 90
    return min(0.99, 0.35 + 0.64 * (m - 570) / 750)


def _utc_z(local: datetime) -> str:
    return (local - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _naive(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=None)


# ---------- synthetic reply text (seeded per borrower and day) ----------
TEXTS = {
    "STOP": ("STOP", "Please stop sending me these messages", "mujhe message mat bhejo", "कृपया मैसेज भेजना बंद करें"),
    "PTP": ("salary late hai, {dd} tareekh tak kar dunga", "will pay on {dd}th after salary",
            "मैं {dd} तारीख तक भुगतान कर दूंगा", "{dd} tareekh ko pakka payment kar dunga"),
    "ALREADY_PAID": ("paid already, UTR {utr}", "payment kar diya hai, check karo", "maine EMI bhar di hai",
                     "I have already paid this month"),
    "LINK": ("send me the payment link, paying now", "link bhejo abhi pay karta hoon"),
    "NEEDS_TIME": ("thoda time chahiye, salary late hai", "need some more time to arrange the money",
                   "abhi paise nahi hai, kuch din do", "abhi mushkil hai, baad mein dekhenge"),
    "HARDSHIP": ("job chali gayi, abhi pay nahi kar sakta", "I lost my job last month, cannot pay now",
                 "hospital mein hoon, pay nahi kar paunga", "नौकरी चली गई है, अभी नहीं दे सकता"),
    "CALLBACK": ("call me tomorrow evening", "kal shaam ko call karna"),
    "BALANCE": ("kitna baaki hai?", "what is my outstanding amount?"),
    "DISPUTE": ("this amount is wrong, I already closed this loan", "galat amount hai, maine loan close kar diya tha"),
    "GREETING": ("ok thanks", "thik hai", "hmm dekhta hoon"),
}
OTHER_MIX = (("PTP", 0.16), ("NEEDS_TIME", 0.20), ("LINK", 0.12), ("CALLBACK", 0.12), ("HARDSHIP", 0.14),
             ("BALANCE", 0.12), ("DISPUTE", 0.06), ("GREETING", 0.08))
VOICE_DISPOSITION = {"PTP": "PTP", "ALREADY_PAID": "ALREADY_PAID", "LINK": "PAY_NOW_LINK",
                     "NEEDS_TIME": "NEEDS_TIME_NO_DATE", "HARDSHIP": "HARDSHIP", "CALLBACK": "CALLBACK_REQUESTED",
                     "BALANCE": "NEEDS_TIME_NO_DATE", "DISPUTE": "DISPUTE", "GREETING": "NO_RESPONSE"}
VOICE_SUMMARY = {"PTP": "Borrower promised to pay on {date}.", "ALREADY_PAID": "Borrower says the EMI is already paid.",
                 "PAY_NOW_LINK": "Borrower asked for the payment link to pay now.",
                 "NEEDS_TIME_NO_DATE": "Borrower needs some more time, no date given.",
                 "HARDSHIP": "Borrower said: I lost my job last month, cannot pay now.",
                 "CALLBACK_REQUESTED": "Borrower said: call me tomorrow evening.",
                 "DISPUTE": "Borrower disputes the amount due.", "STOP_REQUESTED": "Borrower asked not to be called again.",
                 "NO_RESPONSE": "Borrower listened, no clear answer.",
                 "REFUSED_TO_PAY": "Borrower said he will not pay.",
                 "LANGUAGE_BARRIER": "Borrower asked to talk in Hindi.",
                 "WRONG_PERSON": "The person who answered says it is the wrong number.",
                 "THIRD_PARTY": "A family member answered and will pass on the message."}


class FullStackRun(k09.StackRun):
    """The K09 + K27 arm with every other component wired in. run_day = plan_phase + send_phase."""

    def __init__(self, csv_path, seed, days, db_path, per_case, *, portfolio, loans, names, interactive=False):
        super().__init__(ARM, csv_path, seed, days, db_path, per_case, DRIFT_DAY, portfolio=portfolio, loans=loans)
        self.names = names
        self.interactive = interactive
        self._idx = {b.borrower_id: b.index for b in self.portfolio}
        # executor on the mock channel adapters (dry-run: nothing leaves the process), same costs as the replay
        self.providers = {ch: MockChannelProvider(ch, seed=seed, profile="perfect", cost_paise=k09.COST_PAISE[ch])
                          for ch in ("sms", "whatsapp", "ivr", "voicebot")}
        self.providers["email"] = FakeProvider("mock-email", cost_paise=k09.COST_PAISE["email"])
        self.executor = Executor(self.ledger, self.providers)
        self.fsm = SupervisorFSM(self.ledger, self.executor, self.ranker, self.sink, mode=Mode.SIMULATOR)
        self.voice_ranker = k09.CadenceRanker("k27/voice-first-step")
        self.voice_fsm = SupervisorFSM(self.ledger, self.executor, self.voice_ranker, self.sink, mode=Mode.SIMULATOR)
        self.hitl = HITL(self.ledger)
        at0 = datetime.combine(self.start, dtime(7, 0))
        for ch in S.CHANNELS:   # replay templates are reminders: LOW intensity (the high-DPD rule is for STANDARD)
            for lang in k09.TEMPLATE_LANGS:
                self.hitl.register_template(k09.template_for(ch, lang), "LOW", None, "policy.owner", "policy_owner",
                                            "replay reminder template", now=at0)
        self.voice = VoiceIngester(self.ledger, finalizer=self.finalizer, sink=RecordingSink(),
                                   supervisor=Supervisor(self.ledger), hitl=self.hitl, provenance=PROVENANCE)
        self.intent = IntentStack(local_llm=None, cloud_llm=None)
        from kairos_backend.cm03.engine import ConversationEngine
        self._cm_now = at0
        self.cm03 = ConversationEngine(self.ledger, clock=lambda: self.policy.local(self._cm_now),
                                       classifier=IntentClassifier(), otp_code_source=lambda: OTP)
        self.day_events: list = []
        self._day = 0
        self._plan_i = 0
        self._learn_prev = None
        self._top_prev = None
        self.pending: dict = {}             # instruction id -> approval info (open approvals today)
        self.sends: list = []               # (send_at, borrower, PlanResult) planned today
        self.promises: dict = {}            # borrower_id -> {date, keep, obligation_id}
        self.claims: list = []              # text payment claims waiting for D7 {bid, made, deadline}
        self.callbacks: dict = {}           # borrower_id -> day the agent calls back
        self.bots: dict = {}                # instruction id -> select_bot result
        self._voice_today: list = []
        self._jwait: dict = {}
        self._dup_done = False
        self._not_approved: set = set()
        self.extra_paid = 0
        self._setup_full()

    # ---------- events ----------
    def ev(self, comp: str, kind: str, at: datetime, bid: Optional[str] = None, summary: str = "",
           links=(), pos: Optional[float] = None, **detail) -> dict:
        assert comp in COMPONENTS, comp
        e = {"t": at.strftime("%Y-%m-%dT%H:%M"), "day": self._day + 1, "component": comp, "kind": kind,
             "borrower_id": bid, "first_name": self.names.get(bid) if bid else None, "summary": summary,
             "detail": detail, "links": [{"from_component": a, "to_component": b} for a, b in links],
             "_pos": _pos(at) if pos is None else pos, "_o": len(self.day_events)}
        self.day_events.append(e)
        return e

    def name(self, bid) -> str:
        return self.names.get(bid, "This borrower")

    def _plan_pos(self) -> float:
        self._plan_i += 1
        return 0.04 + 0.255 * min(1.0, self._plan_i / max(1, len(self.portfolio) + VOICE_PER_DAY))

    def _date(self) -> date:
        return self.start + timedelta(days=self._day)

    def _at(self, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(self._date(), dtime(hh, mm))

    def _band(self, res_band: Optional[str]) -> str:
        return res_band if res_band in ("PRE_DUE", "B1", "B2", "B3", "B4") else "B1"

    # ---------- setup: spine, churn day 0, CM-03 sample ----------
    def _setup_full(self):
        at = datetime.combine(self.start, dtime(7, 0))
        rep = self.import_report
        consents = Counter(ch for b in self.portfolio for ch in b.consents)
        bands = Counter(b.dpd_band for b in self.portfolio)
        n = len(self.portfolio)
        self.ev("spine", "portfolio_loaded", at, None,
                f"Portfolio loaded: {n} synthetic borrowers, {len(self.loans)} loans enrolled in the campaign "
                f"({', '.join(f'{v} {k}' for k, v in sorted(bands.items()))}).",
                links=[("spine", "churn")], pos=0.001, borrowers=n, loans_enrolled=len(self.loans),
                obligations=len(self.loans), consents_by_channel=dict(sorted(consents.items())),
                bands=dict(sorted(bands.items())), import_report=rep)
        self.ev("spine", "budget_scope", at, None,
                f"Budget scope: {L.money(BUDGET_PER_CASE_PAISE)} per borrower, {L.money(BUDGET_PER_CASE_PAISE * n)} "
                f"for {self.days} days; Aladdin paces it per day.",
                links=[("spine", "aladdin")], pos=0.002, per_case_paise=BUDGET_PER_CASE_PAISE,
                pool_paise=BUDGET_PER_CASE_PAISE * n, days=self.days,
                basis="full-mode demo pool (SIMULATED), not the race's matched budget")
        for i, b in enumerate(self.portfolio):
            st = self.churn.state(b.borrower_id)
            self.ev("churn", "assigned", at + timedelta(minutes=1), b.borrower_id,
                    f"{self.name(b.borrower_id)} starts in cohort {st.cohort}, {st.risk_tier.lower()} risk, layer "
                    f"{st.layer}.", links=[("spine", "churn")], pos=0.003 + 0.035 * i / max(1, n),
                    cohort=st.cohort, risk_tier=st.risk_tier, layer=st.layer, journey=st.journey_id,
                    lifecycle=st.lifecycle)
        # CM-03 sample: a synthetic WhatsApp number (+910…, never a real mobile; never put in an event)
        self.cm03_by_day: dict = defaultdict(list)
        self.phone: dict = {}
        weekdays = [d for d in range(1, max(2, self.days - 1)) if (self.start + timedelta(days=d)).weekday() != 6]
        with self.ledger._spine() as conn:
            for b in self.portfolio:
                bid = b.borrower_id
                self.phone[bid] = f"+910{100000000 + (self.seed % 800) * 10000 + b.index:09d}"
                conn.execute("UPDATE borrowers SET phone=? WHERE borrower_id=?", (self.phone[bid], bid))
        sample = sorted((b.borrower_id for b in self.portfolio if _u(self.seed, "cm03", b.borrower_id) < CM03_SHARE),
                        key=lambda x: _u(self.seed, "cm03order", x))
        kinds = ("balance", "fake_utr", "stop", "balance", "fake_utr")
        for i, bid in enumerate(sample if weekdays else []):
            d = _pick(weekdays, self.seed, "cm03day", bid)
            self.cm03_by_day[d].append((bid, kinds[i % len(kinds)]))

    # ---------- K27 candidates (+ the voice-first lane) ----------
    def _churn_candidates(self, b, d: date) -> list:
        a = self.churn.allowed_channels(b.borrower_id, d, consented=set(b.consents))
        if not a["channels"]:
            self.churn_waits[a["reason"]] += 1
            self._jwait[b.borrower_id] = a["reason"]
            return []
        voice_step = any(c in ("ivr", "voicebot") for s in a["steps"] for c in s["channels"])
        last = self.log.last_day(b.index)
        rested = last is None or (self._day - last) >= S.MIN_DAYS_BETWEEN_CONTACTS
        has_bot = k09.dpd_band(self._dpd(b.index, self._day)) in ("PRE_DUE", "B1", "B2", "B3")
        if voice_step and has_bot and "voicebot" in b.consents and rested and len(self._voice_today) < VOICE_PER_DAY:
            self._voice_today.append(b)
            return []
        avoid = set(a.get("avoid_slots") or [])
        out = [c for c in self.candidates(b, d, channels=a["channels"]) if c.slot not in avoid]
        return out or self.candidates(b, d, channels=a["channels"])

    # ---------- churn feed ----------
    def _churn(self, event: dict, trigger: str) -> None:
        res = self.churn.process({"provenance": PROVENANCE, **event})
        self._churn_moves(event["borrower_id"], res.get("transitions") or [], event["occurred_at"], trigger)

    def _churn_moves(self, bid: str, transitions: list, at: datetime, trigger: str) -> None:
        for t in transitions:
            eng = t.get("engine")
            if eng not in ("cohort", "layer", "risk", "journey", "lifecycle"):
                continue
            st = self.churn.state(bid)
            what = {"cohort": "cohort", "layer": "layer", "risk": "risk tier", "journey": "journey",
                    "lifecycle": "status"}[eng]
            self.ev("churn", "moved", at, bid,
                    f"Auto-churn: {self.name(bid)} moves {what} {t.get('from')} → {t.get('to')} ({trigger}).",
                    links=[("churn", "learner")], engine=eng, **{"from": t.get("from"), "to": t.get("to")},
                    rule=t.get("rule_id"), cohort=st.cohort, layer=st.layer, risk_tier=st.risk_tier, trigger=trigger)

    # ---------- the day ----------
    def run_day(self, day: int):
        self.plan_phase(day)
        if not self.interactive:
            self.decide_pending_simulated()
        self.send_phase(day)

    def plan_phase(self, day: int):
        self._day, self._plan_i = day, 0
        self._voice_today, self._jwait, self.sends, self.pending, self._not_approved = [], {}, [], {}, set()
        d = self._date()
        quiet = d.weekday() == 6
        self.ev("spine", "day_start", self._at(6, 0), None,
                f"Day {day + 1} ({d:%A %d %b}): the LMS refresh marks the spine fresh"
                + (" — Sunday is a quiet day, nobody is contacted." if quiet else "."),
                links=[("spine", "aladdin")], pos=0.0005, date=d.isoformat(), weekday=d.strftime("%A"), quiet_day=quiet)
        if day == DRIFT_DAY:
            self.ev("borrower", "world_change", self._at(6, 5), None,
                    "The simulated world changes today: WhatsApp replies start dropping (x0.35). Kairos is not told.",
                    pos=0.001, channel="whatsapp", factor=0.35)
        broken = self.ledger.start_day(d)
        for bid in broken:
            p = self.promises.get(bid, {})
            self.ev("payments", "promise_broken", self._at(6, 10), bid,
                    f"{self.name(bid)}'s promise date passed with no payment: broken promise, the hold ends.",
                    links=[("payments", "churn")], promise_date=p.get("date"))
        self.hitl.tick(now=self._at(6, 15))
        self._supervisor_cases()
        # K27 daily tick (PTP due → KEPT/BROKEN, expiries); report each borrower's move
        before = {bid: (s.cohort, s.layer, s.risk_tier) for bid in self.churn.borrower_ids()
                  for s in (self.churn.state(bid),)}
        self.churn.tick(d)
        for bid, (c0, l0, r0) in before.items():
            s = self.churn.state(bid)
            for eng, a, b in (("cohort", c0, s.cohort), ("layer", l0, s.layer), ("risk", r0, s.risk_tier)):
                if a != b:
                    self._churn_moves(bid, [{"engine": eng, "from": a, "to": b, "rule_id": s.cohort_rule or "K27_TICK"}],
                                      self._at(6, 20), "daily tick")
        self._resolve_claims()
        # K09 plan (08:00): the learner's queue, then the Gate per borrower
        plan_at = self._at(8, 0)
        rest = set()
        for b in self.portfolio:
            last = self.log.last_day(b.index)
            if not (b.paid or b.opted_out or not b.consents) and last is not None and day - last < S.MIN_DAYS_BETWEEN_CONTACTS:
                rest.add(b.index)
        queue_ = self._plan_queue(day, d, plan_at)
        queued = {b.index for b, _, _ in queue_}
        voice_ids = {b.index for b in self._voice_today}
        for b in self.portfolio:
            if b.paid or b.opted_out or not b.consents or b.index in queued or b.index in voice_ids:
                continue
            nm = self.name(b.borrower_id)
            if b.index in rest:
                gap = day - self.log.last_day(b.index)
                self.ev("learner", "wait", plan_at, b.borrower_id,
                        f"{nm} is not contacted today (messaged {'yesterday' if gap == 1 else f'{gap} days ago'}; "
                        "resting helps replies).", pos=self._plan_pos(), rule="REST", reason="REST")
            elif b.borrower_id in self._jwait:
                self.ev("learner", "wait", plan_at, b.borrower_id,
                        f"{nm} is not contacted today: the auto-churn journey has no step today.",
                        links=[("churn", "learner")], pos=self._plan_pos(), rule="JOURNEY",
                        reason=self._jwait[b.borrower_id])
            else:
                self.ev("learner", "wait", plan_at, b.borrower_id,
                        f"{nm} is not contacted today (a message is unlikely to pay off now; the money goes to "
                        "people more likely to reply).", pos=self._plan_pos(), rule="LEARNER", reason="LOW_VALUE")
        for b, cands, want in queue_:
            res = self.fsm.plan(b.borrower_id, self.loans[b.index].obligation_id, CAMPAIGN, None, cands, plan_at,
                                rng=self._rng(day, b.index))
            self.decisions[res.decision] += 1
            self._on_plan(b, res, "k09")
        # voice-first lane: the K27 step says voice; the Sarvam bot places the call (Gate + HITL + re-check)
        for b in self._voice_today:
            self.voice_ranker.want[b.borrower_id] = ("voicebot", None)
            res = self.voice_fsm.plan(b.borrower_id, self.loans[b.index].obligation_id, CAMPAIGN, None,
                                      self.candidates(b, d, channels=["voicebot"]), plan_at)
            self.decisions[res.decision] += 1
            self._on_plan(b, res, "voice_first")

    def _on_plan(self, b, res, lane: str):
        bid, nm, at = b.borrower_id, self.name(b.borrower_id), self._at(8, 0)
        if res.decision == "SEND":
            send_at = _naive(res.send_at)
            ch, slot = res.channel, SLOT_WORD.get(res.slot, res.slot)
            c = res.ranking.chosen if res.ranking is not None else None
            if lane == "voice_first":
                why = "the auto-churn journey step today is a voice call, so the voice bot calls"
                chance = None
            else:
                chance = round(c.posterior_mean, 4) if c is not None else None
                pct = max(1, round((chance or 0) * 100))
                why = (f"people like {nm} ({L.BAND_WORD.get(res.band, res.band)}) reply most to {CH_NAME[ch]} in the "
                       f"{slot}: about {pct} in 100")
            pos = self._plan_pos()
            self.ev("learner", "contact", at, bid, f"{nm} gets {CH_ARTICLE[ch]} at {L.clock(send_at.time())} ({why}).",
                    links=[("churn", "learner"), ("learner", "aladdin")], pos=pos, channel=ch, slot=res.slot,
                    send_time=send_at.strftime("%H:%M"), template_id=res.template_id, band=res.band,
                    reply_chance=chance, replies_per_inr=round(c.replies_per_inr, 4) if c is not None else None,
                    reason=why, lane=lane)
            touch = self.ledger.touch(res.instruction_id)
            self.ev("aladdin", "allowed", at, bid, f"Aladdin checked {nm}'s {_chn(ch)}: every rule passed "
                    "(consent, caps, quiet hours, budget, holds).", links=[("aladdin", "executor")],
                    pos=pos + 1e-6, stage="plan", channel=ch, rule=res.rule, policy_version=res.policy_version)
            self.sends.append((res.send_at, b, res))
            if ch == "voicebot":
                self._select_bot(b, res, pos + 2e-6)
            if touch and touch["status"] == "PENDING_APPROVAL":
                rules = self._approval_rules(res.instruction_id)
                self.pending[res.instruction_id] = {"approval_id": res.instruction_id, "borrower_id": bid,
                                                    "first_name": self.names.get(bid), "channel": ch, "rules": rules,
                                                    "send_time": send_at.strftime("%H:%M"), "day": self._day + 1}
                self.ev("hitl", "approval_pending", at, bid,
                        f"{nm}'s {_chn(ch)} waits for a person to approve it ({', '.join(rules)}).",
                        links=[("aladdin", "hitl")], pos=pos + 3e-6, approval_id=res.instruction_id, rules=rules,
                        channel=ch, send_time=send_at.strftime("%H:%M"), interactive=self.interactive)
            return
        if res.rule in L.NOT_GUARDRAIL:
            self.ev("learner", "wait", at, bid, f"{nm} is not contacted today (no message is worth its cost now).",
                    pos=self._plan_pos(), rule="LEARNER", reason=res.reason)
            return
        cand_ch = res.rejected[0].get("channel") if res.rejected else None
        label, why = L.block_label(res.rule, res.reason)
        self.ev("aladdin", "blocked", at, bid,
                f"Aladdin blocked this: {why.format(name=nm, channel=CH_NAME.get(cand_ch or '', 'this channel'))}.",
                links=[("learner", "aladdin")], pos=self._plan_pos(), stage="plan", rule=res.rule, reason=res.reason,
                label=label, channel=cand_ch, decision=res.decision)

    def _approval_rules(self, iid: str) -> list:
        import json
        r = self.ledger._conn().execute("SELECT rules FROM hitl_approvals WHERE instruction_id=?", (iid,)).fetchone()
        return json.loads(r["rules"]) if r else []

    def _select_bot(self, b, res, pos):
        bid = b.borrower_id
        at = self._at(8, 1)
        borrower, obligation, ctx = voice_registry.inputs_from_ledger(self.ledger, bid, at, res.obligation_id)
        ctx.update(touch_id=res.instruction_id)
        sel = voice_registry.select_bot(borrower, obligation, ctx, self.voice.registry, self.voice.thresholds)
        self.bots[res.instruction_id] = sel
        if sel.get("use_case") is None:
            return
        self.ev("voice", "bot_selected", at, bid,
                f"Voice bot for {self.name(bid)}: {sel['voice']} ({sel['use_case'].replace('_', ' ').lower()}, "
                f"{sel['language']}); the call variables carry no amounts and no contact numbers.",
                links=[("learner", "voice")], pos=pos, bot=sel["voice"], use_case=sel["use_case"],
                language=sel["language"], agent_id=sel["agent_id"],
                requires_human_approval=sel["requires_human_approval"],
                blocked_variables=sorted(sel.get("blocked_variables") or {}))

    # ---------- approvals ----------
    def decide_pending_simulated(self):
        """A simulated supervisor works the queue between 08:20 and 09:25 (deterministic, ~1 in 8 rejected)."""
        for k, iid in enumerate(sorted(self.pending)):
            at = self._at(8, 20) + timedelta(minutes=min(65, 3 * k))
            if _u(self.seed, "reject", iid) < 0.12:
                self.decide(iid, False, at, "not today: borrower was spoken to recently", SUPERVISOR,
                            "simulated supervisor")
            else:
                self.decide(iid, True, at, "script and timing reviewed", SUPERVISOR, "simulated supervisor")

    def decide(self, iid: str, approve: bool, at: datetime, reason: str, who=HUMAN, by: str = "you") -> dict:
        info = self.pending.get(iid)
        if info is None:
            raise KeyError(iid)
        bid, ch = info["borrower_id"], info["channel"]
        nm = self.name(bid)
        actor, role = who
        if approve:
            r = self.hitl.approve(iid, actor, role, reason, now=at)
        else:
            r = self.hitl.reject(iid, actor, role, reason, now=at)
        self.pending.pop(iid, None)
        out = r.get("outcome")
        if out != "APPROVED":
            self._not_approved.add(iid)
        g = r.get("gate") or {}
        if out == "APPROVED":
            self.ev("aladdin", "rechecked", at, bid, f"Aladdin re-ran every rule for {nm}'s {_chn(ch)} at approval: "
                    "still allowed.", links=[("hitl", "aladdin")], stage="approval", channel=ch, rule=g.get("rule"))
            self.ev("hitl", "approved", at, bid, f"{by.capitalize()} approved {nm}'s {_chn(ch)}.",
                    links=[("hitl", "executor")], approval_id=iid, by=by, gate=g.get("reason"))
        elif out == "BLOCKED_AT_RECHECK":
            label, why = L.block_label(g.get("rule"), g.get("reason"))
            self.ev("aladdin", "blocked", at, bid, f"Approved, but Aladdin's re-check stopped it: "
                    f"{why.format(name=nm, channel=CH_NAME[ch])}.", links=[("hitl", "aladdin")], stage="approval",
                    rule=g.get("rule"), reason=g.get("reason"), label=label, channel=ch)
            self.ev("hitl", "blocked_at_recheck", at, bid, f"{by.capitalize()} approved {nm}'s {_chn(ch)}, but the "
                    "guardrail wins.", approval_id=iid, by=by, gate=g.get("reason"))
        elif out == "REJECTED":
            self.ev("hitl", "rejected", at, bid, f"{by.capitalize()} rejected {nm}'s {_chn(ch)} ({reason}); the "
                    "budget goes back to the pool.", links=[("hitl", "aladdin")], approval_id=iid, by=by, reason=reason)
        elif out == "EXPIRED":
            self.ev("hitl", "expired", at, bid, f"Nobody decided {nm}'s {_chn(ch)} in time: it expired.",
                    approval_id=iid, by="system")
        return {"approval_id": iid, "outcome": out, "status": r.get("status"), "gate": g.get("reason")}

    def _supervisor_cases(self):
        """The simulated supervisor works cases older than a day: resolved, the person's hold lifted
        (a K13 hardship hold still runs until its date)."""
        now = self._at(7, 30)
        for cse in self.hitl.list_cases("supervisor", now=now):
            created = _naive(cse["created_at"])
            if (now - created).total_seconds() < 20 * 3600:
                continue
            code = {"HARDSHIP": "HARDSHIP_CONFIRMED", "DISPUTE": "REFERRED_TO_LENDER"}.get(cse["type"], "CONTACTED_RESOLVED")
            try:
                r = self.hitl.resolve_case(cse["ticket_id"], SUPERVISOR[0], SUPERVISOR[1], "worked by the supervisor",
                                           code, lift_hold=True, now=now)
            except (HitlError, ValueError, KeyError):
                continue
            bid = cse["borrower_id"]
            self.ev("hitl", "case_resolved", now, bid,
                    f"A supervisor worked {self.name(bid)}'s {cse['type'].replace('_', ' ').lower()} case "
                    f"({code.replace('_', ' ').lower()}).", links=[("hitl", "aladdin")], queue=cse["queue"],
                    case_type=cse["type"], resolution=code, hold_lifted=r.get("hold_lifted"))

    def _resolve_claims(self):
        now = self._at(6, 30)
        for r in self.voice.resolve_payment_claims(now):
            bid = r["borrower_id"]
            if r["status"] == "VERIFIED":
                self.ev("payments", "claim_verified", now, bid, f"{self.name(bid)}'s payment claim from the call is "
                        "confirmed by the bank.", source="voice")
            else:
                self.ev("payments", "claim_rejected", now, bid, f"{self.name(bid)} said on the call they had paid, but no "
                        "bank credit arrived in time: claim rejected, reminders resume.",
                        links=[("payments", "aladdin")], source="voice", reason="NO_D7_CREDIT_BY_DEADLINE")
        keep = []
        for c in self.claims:
            b = self.portfolio[self._idx[c["bid"]]]
            if b.paid:
                self.ev("payments", "claim_verified", now, c["bid"], f"{self.name(c['bid'])}'s 'already paid' is now "
                        "confirmed by the bank.", source="text")
            elif self._date() >= c["deadline"]:
                self.ev("payments", "claim_rejected", now, c["bid"], f"{self.name(c['bid'])} said they had paid, but "
                        "there is no bank credit after T+2: claim rejected (F4), reminders resume.",
                        links=[("payments", "aladdin")], source="text", reason="NO_D7_CREDIT_BY_T+2")
            else:
                keep.append(c)
        self.claims = keep

    # ---------- send phase ----------
    def send_phase(self, day: int):
        d = self._date()
        sends = sorted(self.sends, key=lambda s: (s[0], s[1].index, s[2].channel))
        k15_decisions, sent_meta = [], []

        def dispatch(batch):
            for send_at, b, res in batch:
                at = _naive(send_at)
                out = self.fsm.dispatch(res.instruction_id, at)
                self.dispatch[out.outcome] += 1
                bid, nm, ch = b.borrower_id, self.name(b.borrower_id), res.channel
                if out.sent:
                    cost = k09.COST_PAISE[ch]
                    touch = self.ledger.touch(res.instruction_id)
                    self.ev("aladdin", "rechecked", at, bid, f"Aladdin re-checked {nm}'s {_chn(ch)} right before "
                            "sending: still allowed.", links=[("aladdin", "executor")], stage="send", channel=ch)
                    self.ev("executor", "dispatched", at, bid, f"{CH_NAME[ch]} sent to {nm} ({L.money(cost)}, mock "
                            "adapter, dry-run).", links=[("executor", "channel")], channel=ch, cost_paise=cost,
                            provider_ref=_short_ref(touch.get("provider_ref") if touch else None),
                            adapter=self.providers[ch].name)
                    k15_decisions.append(k09.Decision(b.index, ch, k09.K15_SLOT[res.slot], S.TEMPLATES[ch][0]))
                    sent_meta.append((res, b, at))
                    self.finalizer.register_execution(res.instruction_id, bid, res.band, ch, res.slot, at)
                elif out.outcome == "BLOCKED" or (self.ledger.touch(res.instruction_id) or {}).get("status") == "CANCELED":
                    if res.instruction_id in self._not_approved:
                        continue
                    label, why = L.block_label(out.rule, out.reason)
                    self.ev("aladdin", "blocked", at, bid, f"Aladdin re-checked before sending and stopped it: "
                            f"{why.format(name=nm, channel=CH_NAME[ch])}.", links=[("executor", "aladdin")],
                            stage="send", rule=out.rule, reason=out.reason, label=label, channel=ch)

        noon = d.isoformat() + "T12"
        dispatch([s for s in sends if s[0] < noon])
        self._cm03_day(day, [s for s in sends if s[0] >= noon])
        dispatch([s for s in sends if s[0] >= noon])

        for rule, _, _ in k09.check_day(day, k15_decisions, self.portfolio, self.log):
            self.k15_violations[rule] += 1
        outcomes = self.simulator.run_day(day, k15_decisions, self.portfolio, self.log)
        for dec in k15_decisions:
            self.log.record(dec.borrower_index, day)
        mix = Counter()
        tot = {"spend": 0, "replies": 0, "payments": 0}
        for o, (res, b, at) in zip(outcomes, sent_meta):
            self.attempted.add(b.index)
            tot["spend"] += k09.COST_PAISE[o.channel]
            mix[(res.band, o.channel)] += 1
            self._churn({"event_id": f"{res.instruction_id}:attempt", "borrower_id": b.borrower_id, "kind": "attempt",
                         "channel": o.channel, "occurred_at": at, "touch_id": res.instruction_id,
                         "cost_paise": k09.COST_PAISE[o.channel]}, "attempt")
            if o.channel == "voicebot":
                self._voice_outcome(o, res, b, at, tot)
            else:
                self._text_outcome(o, res, b, at, tot)
            if o.payment_verified and not b.paid:
                self._pay(b, at + timedelta(minutes=210), f"PAY-{res.instruction_id}", f"SIM-UTR-{res.instruction_id}",
                          "D7/LMS", f"after {CH_ARTICLE[o.channel]}")
                tot["payments"] += 1
        self._promises_due()
        self._callbacks_due()
        expired = self._expire_left(self._at(20, 0))
        outs = self.finalizer.finalize_due(datetime.combine(d, dtime(23, 59)))
        st = Counter(o.status for o in outs)
        if outs:
            learned = sum(v for k, v in st.items() if k in ("REPLY", "NO_REPLY"))
            self.ev("outcomes", "finalized", self._at(21, 30), None,
                    f"{len(outs)} reply windows closed today: {st.get('REPLY', 0)} replies, {st.get('NO_REPLY', 0)} "
                    f"no reply; {learned} outcomes go to the learner.", links=[("outcomes", "learner")],
                    count=len(outs), by_status=dict(sorted(st.items())), learner_updates=learned)
        self._learning_events(self._at(21, 40))
        self._switch_event(self._at(21, 45))
        self.spend_paise += tot["spend"]
        self.replies += tot["replies"]
        self.payments += tot["payments"]
        self.mix.append(mix)
        self.daily.append({"day": day + 1, "date": d.isoformat(), "sent": len(k15_decisions),
                           "spend_paise": tot["spend"], "meaningful_replies": tot["replies"],
                           "verified_payments": tot["payments"],
                           "channels": dict(sorted(Counter(ch for (_, ch) in mix.elements()).items()))})
        rep = k26.daily_segment_report(self.ledger, CAMPAIGN, d)
        t = rep.get("totals", {})
        segs = sorted(rep.get("segments", []), key=lambda s: -(s.get("attempts") or 0))[:3]
        self.ev("reports", "daily_summary", self._at(22, 0), None,
                f"K26 day report: {t.get('attempts', 0)} attempts, {t.get('reach', 0)} reached, "
                f"{t.get('meaningful_replies', 0)} replies, {L.money(t.get('spend_committed_paise') or 0)} committed, "
                f"cap breaches {t.get('cap_breaches', 0)}.",
                links=[("outcomes", "reports")], attempts=t.get("attempts"), reach=t.get("reach"),
                meaningful_replies=t.get("meaningful_replies"), spend_paise=t.get("spend_committed_paise"),
                cap_breaches=t.get("cap_breaches"), policy_version=rep.get("policy_version"),
                top_segments=[{k: s.get(k) for k in ("band", "channel", "attempts", "meaningful_replies")} for s in segs])
        breaches = len(self.ledger.cap_breaches())
        self.ev("reports", "day_end", self._at(22, 0), None,
                f"Day {day + 1} done: {len(k15_decisions)} sent, {L.money(tot['spend'])} spent, cap breaches {breaches}.",
                pos=1.0, cap_breaches=breaches, sent=len(k15_decisions), spend_paise=tot["spend"],
                expired_approvals=expired)

    def _expire_left(self, at: datetime) -> int:
        n = 0
        for iid, info in list(self.pending.items()):
            t = self.ledger.touch(iid) or {}
            if t.get("status") == "PENDING_APPROVAL":
                n += 1
                self.pending.pop(iid, None)
                self.ev("hitl", "expired", at, info["borrower_id"],
                        f"Nobody approved {self.name(info['borrower_id'])}'s {_chn(info['channel'])} before the "
                        "contact window closed: it expired and the budget went back.", approval_id=iid, by="system")
        self.hitl.expire_due(at)
        self.pending = {}
        return n

    # ---------- outcome → text / intent / K13 ----------
    def _category(self, o, b, day) -> str:
        if o.opted_out:
            return "STOP"
        if o.payment_verified:
            return "ALREADY_PAID" if _u(self.seed, "vtxt", b.index, day) < 0.5 else "LINK"
        if o.payment_claimed:
            return "PTP" if _u(self.seed, "ctxt", b.index, day) < 0.6 else "ALREADY_PAID"
        x, acc = _u(self.seed, "otxt", b.index, day), 0.0
        for cat, p in OTHER_MIX:
            acc += p
            if x < acc:
                return cat
        return "GREETING"

    def _text_for(self, cat, b, day, at) -> tuple:
        tpl = _pick(TEXTS[cat], self.seed, "tpl", cat, b.index, day)
        promise = at.date() + timedelta(days=2 + int(_u(self.seed, "pd", b.index, day) * 4))
        utr = f"{int(_u(self.seed, 'utr', b.index, day) * 9e11) + 10 ** 11}"
        return tpl.format(dd=promise.day, utr=utr), promise

    def _text_outcome(self, o, res, b, at, tot):
        iid, ch, bid, nm = res.instruction_id, o.channel, b.borrower_id, self.name(b.borrower_id)
        self.finalizer.record_reach(iid, o.delivered, at + timedelta(minutes=1))
        t1 = at + timedelta(minutes=1)
        if ch == "ivr":
            self.ev("channel", "call_answered" if o.delivered else "call_not_answered", t1, bid,
                    f"Voice call to {nm} {'was answered' if o.delivered else 'was not answered'}.",
                    links=[("channel", "borrower")], channel=ch, status="ANSWERED" if o.delivered else "NO_ANSWER")
        else:
            status = "DELIVERED" if o.delivered else "FAILED"
            env = apply_callback(self.ledger, {"channel": ch, "instruction_id": iid, "status": status, "occurred_at": t1,
                                               "provenance": PROVENANCE, "provider_ref": f"sim-{iid[-8:]}"})
            self.ev("channel", "delivered" if o.delivered else "failed", t1, bid,
                    f"{CH_NAME[ch]} {'reached' if o.delivered else 'did not reach'} {nm}.",
                    links=[("channel", "borrower")], channel=ch, status=env["status"])
            self._churn({"event_id": f"{iid}:dlr", "borrower_id": bid, "kind": "delivery", "channel": ch,
                         "occurred_at": t1, "status": status, "touch_id": iid}, "delivery")
            if o.delivered and _u(self.seed, "dup", iid) < 0.06:
                dup = apply_callback(self.ledger, {"channel": ch, "instruction_id": iid, "status": status,
                                                   "occurred_at": t1, "provenance": PROVENANCE,
                                                   "provider_ref": f"sim-{iid[-8:]}"})
                if dup.get("duplicate"):
                    self.ev("channel", "duplicate_ignored", t1 + timedelta(minutes=2), bid,
                            f"The {CH_NAME[ch]} delivery receipt for {nm} arrived twice: the copy is ignored.",
                            channel=ch, status=status)
            if o.delivered and ch in ("whatsapp", "email") and (o.meaningful_reply or o.opted_out
                                                                  or _u(self.seed, "read", iid) < 0.55):
                apply_callback(self.ledger, {"channel": ch, "instruction_id": iid, "status": "READ",
                                             "occurred_at": at + timedelta(minutes=20), "provenance": PROVENANCE,
                                             "provider_ref": f"sim-{iid[-8:]}"})
                self.ev("channel", "read", at + timedelta(minutes=20), bid,
                        f"{nm} {'read' if ch == 'whatsapp' else 'opened'} the {CH_NAME[ch]} (reach, not a reply).",
                        channel=ch, status="READ")
        reply_at = at + timedelta(minutes=90)
        if not (o.opted_out or o.meaningful_reply):
            return
        cat = self._category(o, b, self._day)
        if ch == "ivr":
            text = "pressed 9 (STOP)" if o.opted_out else "pressed 1 (send me the payment link)"
            self.ev("borrower", "stop" if o.opted_out else "replied", reply_at, bid,
                    f"{nm} {text} on the voice call.", links=[("borrower", "aladdin" if o.opted_out else "outcomes")],
                    channel=ch, text=text)
            if o.opted_out:
                self._stop(b, reply_at, ch)
            else:
                self._replied(b, reply_at, ch, tot)
            return
        text, promise = self._text_for(cat, b, self._day, reply_at)
        self.ev("borrower", "stop" if cat == "STOP" else "replied", reply_at, bid,
                f"{nm} replied on {CH_NAME[ch]}: “{text}”", links=[("borrower", "intent")], channel=ch,
                text=text)
        r = self.intent.classify(text, today=reply_at.date(), channel=ch)
        if o.meaningful_reply:
            self._replied(b, reply_at, ch, tot)
        action = self._act_on_intent(b, res, r, reply_at, ch, o)
        self.ev("intent", "classified", reply_at + timedelta(minutes=1), bid,
                f"Intent: {r.label.replace('_', ' ').lower()} ({r.tier_used.replace('_', ' ')}, "
                f"{r.confidence:.2f}) → {action.replace('_', ' ').lower()}.",
                links=[("intent", "aladdin"), ("intent", "churn")], label=r.label, tier=r.tier_used,
                confidence=round(r.confidence, 3), needs_human=r.needs_human, safety_flags=list(r.safety_flags),
                action=action, source=ch)
        ev = {"event_id": f"{iid}:reply", "borrower_id": bid, "kind": "reply", "channel": ch, "occurred_at": reply_at,
              "touch_id": iid, "intent": r.label, "text": text}
        if r.label == "PTP" and r.entities.get("date"):
            ev["ptp_date"] = r.entities["date"]
        self._churn(ev, f"reply: {r.label.replace('_', ' ').lower()}")

    def _replied(self, b, at, ch, tot):
        self.finalizer.record_reply(b.borrower_id, at, meaningful=True, channel=ch)
        self.ledger.record_response(b.borrower_id, at, ch)
        self.replied.add(b.index)
        tot["replies"] += 1

    def _stop(self, b, at, ch):
        if b.opted_out:
            return
        self.ledger.record_stop(b.borrower_id, at, ch)
        b.opted_out = True
        self.opt_outs += 1

    def _act_on_intent(self, b, res, r, at, ch, o) -> str:
        bid, band, ob = b.borrower_id, self._band(res.band), res.obligation_id
        label = r.label
        if label == "STOP":
            self._stop(b, at, ch)
            return "STOP_ALL_CHANNELS"
        if label == "PTP":
            dt = r.entities.get("date")
            pd = date.fromisoformat(dt) if dt else at.date() + timedelta(days=3)
            out = self.ledger.apply_exception(bid, ExceptionEvent("PTP_OFFERED", band,
                                                                  date_offset_days=(pd - at.date()).days),
                                              at, ob, ("PTP by text", "SIMULATED"))
            if out["hold"] == "PTP_HOLD":
                self.promises[bid] = {"date": pd.isoformat(), "obligation_id": ob,
                                      "keep": _u(self.seed, "keep", bid, pd) < 0.55}
            return out["hold"] if out["hold"] != "NONE" else out["action"]
        if label in ("ALREADY_PAID",):
            if not o.payment_verified:
                self.claims.append({"bid": bid, "made": at.date(), "deadline": at.date() + timedelta(days=3)})
                self._churn({"event_id": f"{res.instruction_id}:claim", "borrower_id": bid, "kind": "payment",
                             "occurred_at": at + timedelta(minutes=2), "state": "CLAIMED"}, "payment claim")
                self.ev("payments", "claim_pending", at + timedelta(minutes=2), bid,
                        f"{self.name(bid)} says they paid: Kairos waits for the bank (D7) before believing it.",
                        links=[("intent", "payments")], source="text", deadline=(at.date() + timedelta(days=3)).isoformat())
            return "PAYMENT_CLAIM"
        if label == "HARDSHIP":
            out = self.ledger.apply_exception(bid, ExceptionEvent("HARDSHIP", band, date_offset_days=5), at, ob,
                                              ("Hardship reported by text", "SIMULATED"))
            if out.get("ticket_id"):
                self.ev("hitl", "case_opened", at + timedelta(minutes=2), bid,
                        f"{self.name(bid)}'s hardship goes to a person ({out['queue'].replace('_', ' ').lower()}).",
                        links=[("intent", "hitl")], queue=out["queue"], case_type="HARDSHIP", reason="HARDSHIP_TEXT")
            return out["hold"]
        if label == "DISPUTE":
            out = self.ledger.apply_exception(bid, ExceptionEvent("DISPUTE", band), at, ob, ("Dispute", "SIMULATED"))
            if out.get("ticket_id"):
                self.ev("hitl", "case_opened", at + timedelta(minutes=2), bid,
                        f"{self.name(bid)}'s dispute goes to a person.", links=[("intent", "hitl")],
                        queue=out["queue"], case_type="DISPUTE", reason="DISPUTE_TEXT")
            return "DISPUTE_TICKET"
        if label in ("CALLBACK", "HUMAN"):
            out = self.ledger.apply_exception(bid, ExceptionEvent("CALLBACK_REQUEST", band), at, ob,
                                              ("Callback requested", "SIMULATED"))
            self.callbacks[bid] = self._day + 1
            return out["hold"]
        if label == "NEEDS_TIME":
            out = self.ledger.apply_exception(bid, ExceptionEvent("NEEDS_TIME", band), at, ob, ("Needs time", "SIMULATED"))
            return out["action"]
        if label in ("LINK_REQUEST", "PAY_NOW"):
            return "PAYMENT_LINK_SENT"
        if r.needs_human or label == "UNKNOWN":
            return "HUMAN_READS_IT"
        return "RECORD_INTENT"

    def _callbacks_due(self):
        for bid, d in list(self.callbacks.items()):
            if d != self._day:
                continue
            at = self._at(11, 0)
            st = self.ledger._state(self.ledger._conn(), bid)
            if not st.get("callback_pending"):
                self.callbacks.pop(bid)
                continue
            out = self.ledger.apply_exception(bid, ExceptionEvent("CALLBACK_DUE", "B1", callback_answered=True), at,
                                              None, ("Callback done", "SIMULATED"))
            self.callbacks.pop(bid)
            self.ev("hitl", "case_resolved", at, bid, f"An agent called {self.name(bid)} back as asked; the callback "
                    "hold ends.", links=[("hitl", "aladdin")], case_type="CALLBACK", resolution=out["action"])

    # ---------- payments ----------
    def _pay(self, b, at, payment_id, reference, source, how):
        bid = b.borrower_id
        loan = self.loans[b.index]
        c = self.ledger._conn()
        before = c.execute("SELECT obligation_id, COUNT(*) n FROM touches WHERE reason='OBLIGATION_CLEARED' "
                           "GROUP BY obligation_id").fetchall()
        before = {r["obligation_id"]: r["n"] for r in before}
        res = self.ledger.record_payment_verified(loan.obligation_id, loan.emi_paise, at, payment_id=payment_id,
                                                  reference=reference)
        after = {r["obligation_id"]: r["n"] for r in c.execute(
            "SELECT obligation_id, COUNT(*) n FROM touches WHERE reason='OBLIGATION_CLEARED' GROUP BY obligation_id")}
        other = sum(n - before.get(o, 0) for o, n in after.items() if o != loan.obligation_id)
        self._on_paid(b, at, loan, res.get("canceled", 0), other, source, how)
        return res

    def _on_paid(self, b, at, loan, cancelled, other, source, how):
        bid = b.borrower_id
        self.finalizer.record_payment_verified(bid, at)
        b.paid = True
        self.paid.add(b.index)
        self.promises.pop(bid, None)
        extra = f" {cancelled} queued message{'s' if cancelled != 1 else ''} for that EMI cancelled." if cancelled else ""
        self.ev("payments", "verified", at, bid,
                f"{self.name(bid)} paid {L.money(loan.emi_paise)}, confirmed by the bank ({how}).{extra}",
                links=[("payments", "outcomes"), ("payments", "churn")], amount_paise=loan.emi_paise,
                obligation_id=loan.obligation_id, source=source, cancelled_touches=cancelled, cancelled_other_emis=other)
        self._churn({"event_id": f"pay:{loan.obligation_id}", "borrower_id": bid, "kind": "payment",
                     "occurred_at": at, "state": "VERIFIED", "cleared": True}, "verified payment")

    def _promises_due(self):
        d = self._date()
        for bid, p in sorted(self.promises.items()):
            if p["date"] != d.isoformat():
                continue
            b = self.portfolio[self._idx[bid]]
            if b.paid or not p["keep"]:
                continue
            at = self._at(11, 30)
            self._pay(b, at, f"PAY-PTP-{bid}-{d}", f"SIM-UTR-PTP-{bid}-{d}", "promise kept", "the promised payment")
            self.extra_paid += 1
            self.ev("payments", "promise_kept", at, bid, f"{self.name(bid)} kept the promise to pay on "
                    f"{d:%d %b}.", links=[("payments", "churn")], promise_date=p["date"])

    # ---------- voice: Sarvam call-end webhook → VoiceIngester ----------
    def _voice_outcome(self, o, res, b, at, tot):
        iid, bid, nm = res.instruction_id, b.borrower_id, self.name(b.borrower_id)
        sel = self.bots.get(iid) or {}
        ended = at + timedelta(minutes=4)
        cat = self._category(o, b, self._day) if (o.meaningful_reply or o.opted_out) else None
        disp, conf, extra = "NO_RESPONSE", 0.95, {}
        if o.delivered and cat is None:
            x = _u(self.seed, "novoice", iid)
            disp, conf = (("REFUSED_TO_PAY", 0.72) if x < 0.22 else ("LANGUAGE_BARRIER", 0.8) if x < 0.30 else
                          ("WRONG_PERSON", 0.86) if x < 0.36 else ("THIRD_PARTY", 0.55) if x < 0.44 else
                          ("STOP_REQUESTED", 0.45) if x < 0.52 else ("NO_RESPONSE", 0.95))
        elif o.delivered:
            if cat == "STOP":
                disp = "STOP_REQUESTED"
                conf = 0.45 if _u(self.seed, "stopconf", iid) < 0.34 else 0.93
            elif cat is not None:
                disp = VOICE_DISPOSITION[cat]
                conf = {"HARDSHIP": 0.55, "PTP": 0.88}.get(disp, 0.8)
                if _u(self.seed, "lowconf", iid) < 0.15:
                    conf = 0.42          # unsure call: review, never a guess
                if disp == "PTP":
                    pd = ended.date() + timedelta(days=0 if o.payment_verified else 3)
                    extra = {"ptp_date": pd.isoformat(), "ptp_amount_type": "FULL"}
                elif disp == "CALLBACK_REQUESTED":
                    extra = {"callback_at": (datetime.combine(ended.date() + timedelta(days=1), dtime(11, 0))
                                             .isoformat() + "+05:30")}
                elif disp == "HARDSHIP":
                    extra = {"hardship_type": "JOB_LOSS", "needs_human": True}
        summary = VOICE_SUMMARY.get(disp, "").format(date=extra.get("ptp_date", ""))
        if disp == "STOP_REQUESTED" and conf < 0.5:
            summary = "Unclear; the borrower may have asked not to be called."
        out_vars = {"disposition": disp, "disposition_confidence": conf, "language_used": "en",
                    "call_summary": summary, **extra}
        initial = dict(sel.get("variables") or {}, touch_id=iid)
        conn = "connected" if o.delivered else "no_answer"
        payload = {"app_id": sel.get("agent_id") or "unknown", "app_version": 1, "attempt_id": f"att-{iid[-16:]}",
                   "campaign_id": CAMPAIGN, "cohort_id": f"k27-{self.churn.state(bid).cohort}",
                   "completion_status": "completed" if o.delivered else "failed", "user_identifier": iid,
                   "user_phone_number": "masked", "connectivity_status": conn, "next_action_status": None,
                   "retry_attempt": 0, "duration": 95 if o.delivered else None,
                   "interaction_id": f"ix-{iid[-16:]}" if o.delivered else None, "recording_url": None,
                   "executed_at": at.isoformat() + "+05:30", "start_datetime": _utc_z(at), "end_datetime": _utc_z(ended),
                   "initial_agent_variables": initial,
                   "final_agent_variables": {**initial, **out_vars} if o.delivered else None,
                   "output_agent_variables": out_vars if o.delivered else None, "metadata": {"touch_id": iid}}
        r = self.voice.ingest(payload)
        p = (r.get("envelope") or {}).get("payload", {})
        bot = sel.get("voice") or "bot"
        self.ev("channel", "call_answered" if o.delivered else "call_not_answered", at + timedelta(minutes=1), bid,
                f"{bot}'s call to {nm} {'was answered' if o.delivered else 'was not answered'}.",
                links=[("channel", "borrower")], channel="voicebot", status=conn.upper())
        action, eff = p.get("action") or r.get("status"), p.get("effective_disposition") or disp
        if o.delivered and disp != "NO_RESPONSE":
            self.ev("borrower", "stop" if action == "RECORD_STOP_ALL_CHANNELS" else "replied", ended - timedelta(minutes=1),
                    bid, f"{nm} on the call: {summary[0].lower() + summary[1:].rstrip('.')}.",
                    links=[("borrower", "voice")], channel="voicebot", text=summary)
        meaningful = bool(p.get("meaningful_reply"))
        review = action in ("REVIEW", "SUPERVISOR_HOLD_AND_REVIEW") or (p.get("accepted") is False and disp != "NO_RESPONSE")
        self.ev("voice", "call_ended", ended, bid,
                f"Sarvam call-end: {disp.replace('_', ' ').lower()} @ {conf:.2f} → "
                f"{(action or 'none').replace('_', ' ').lower()}"
                + (" (unsure: a person reviews it, nothing automatic)" if review else "") + ".",
                links=[("voice", "aladdin"), ("voice", "outcomes")], disposition=disp, confidence=conf, effective=eff,
                action=action, rule=p.get("rule"), connected=o.delivered, bot=bot, use_case=sel.get("use_case"),
                meaningful=meaningful, low_confidence_review=review, call_id=payload["attempt_id"])
        if o.delivered and disp != "NO_RESPONSE":
            ir = self.intent.classify(summary, today=ended.date(), channel="voicebot")
            self.ev("intent", "classified", ended + timedelta(minutes=1), bid,
                    f"Intent of the call summary: {ir.label.replace('_', ' ').lower()} ({ir.tier_used.replace('_', ' ')}, "
                    f"{ir.confidence:.2f}); the bot's disposition decides the action.",
                    links=[("intent", "churn")], label=ir.label, tier=ir.tier_used, confidence=round(ir.confidence, 3),
                    needs_human=ir.needs_human, safety_flags=list(ir.safety_flags), action=action, source="voicebot")
        if action == "RECORD_STOP_ALL_CHANNELS":
            b.opted_out = True
            self.opt_outs += 1
        if meaningful:
            self.replied.add(b.index)
            tot["replies"] += 1
        if action == "PTP_HOLD" and extra.get("ptp_date"):
            self.promises[bid] = {"date": extra["ptp_date"], "obligation_id": res.obligation_id,
                                  "keep": _u(self.seed, "keep", bid, extra["ptp_date"]) < 0.55}
        if action == "PAYMENT_CLAIM_PENDING":
            self.ev("payments", "claim_pending", ended + timedelta(minutes=1), bid,
                    f"{nm} said on the call they had paid: reminders pause 48 h while the bank (D7) is checked.",
                    links=[("voice", "payments")], source="voice")
        if action in ("HARDSHIP_HOLD_AND_HANDOFF", "K13_HOLD_AND_HANDOFF", "SUPERVISOR_HOLD_AND_REVIEW"):
            self.ev("hitl", "case_opened", ended + timedelta(minutes=2), bid,
                    f"{nm}'s call goes to a person ({eff.replace('_', ' ').lower()}); every channel waits.",
                    links=[("voice", "hitl")], queue="voice", case_type=eff, reason=action)
        self._churn({"event_id": payload["attempt_id"], "borrower_id": bid, "kind": "call", "channel": "voicebot",
                     "occurred_at": ended, "status": "ANSWERED" if o.delivered else "NO_ANSWER", "disposition": disp,
                     "confidence": conf, "touch_id": iid, **({"ptp_date": extra["ptp_date"]} if "ptp_date" in extra else {})},
                    f"call: {disp.replace('_', ' ').lower()}")
        if not self._dup_done or _u(self.seed, "vdup", iid) < 0.25:
            self._dup_done = True
            again = self.voice.ingest(dict(payload))
            if again.get("status") == "DUPLICATE":
                self.ev("voice", "duplicate_ignored", ended + timedelta(minutes=3), bid,
                        f"Sarvam sent the same call-end webhook for {nm} again: ignored, nothing changed.",
                        call_id=payload["attempt_id"])

    # ---------- CM-03 ----------
    def _cm03_day(self, day: int, afternoon: list):
        todo = list(self.cm03_by_day.get(day, []))
        if self._date().weekday() != 6:
            queued = [b for s, b, r in afternoon if not b.paid
                      and (self.ledger.touch(r.instruction_id) or {}).get("status") == "RESERVED"]
            if queued:
                todo.append((_pick(sorted(queued, key=lambda x: x.index), self.seed, "payer", day).borrower_id, "pay"))
        for k, (bid, kind) in enumerate(todo):
            try:
                self._cm03_conversation(bid, kind, self._at(12, 0) + timedelta(minutes=12 * k))
            except Exception as exc:   # never break the run on one conversation
                self.ev("cm03", "handoff", self._at(12, 0), bid, f"WhatsApp agent error, a person takes over "
                        f"({type(exc).__name__}).", reason="ENGINE_ERROR")

    def _cm03_conversation(self, bid: str, kind: str, t0: datetime):
        b = self.portfolio[self._idx[bid]]
        nm = self.name(bid)
        loan = self.loans[b.index]
        conn = self.ledger._spine()
        ref = None
        if kind == "pay":
            ref = f"{int(_u(self.seed, 'cmref', bid, self._day) * 9e11) + 10 ** 11}"
            from kairos_backend.cm03.world import add_feed
            add_feed(conn, ref, loan.emi_paise, t0.date(), "SETTLED", bid, loan.obligation_id)
        fake = f"{int(_u(self.seed, 'fake', bid) * 9e11) + 10 ** 11}"
        paid_on = t0.date() - timedelta(days=6)
        turns = {"balance": ["Hi, what is my outstanding balance?", OTP, "balance please"],
                 "fake_utr": ["Hi, I want to check my loan", OTP, f"I already paid on {paid_on.day} {paid_on:%B}, UTR {fake}"],
                 "pay": ["Hi, I paid my EMI", OTP, f"I have paid my EMI today, UTR {ref}"],
                 "stop": ["STOP"]}[kind]
        self.ev("borrower", "whatsapp_inbound", t0, bid, f"{nm} writes to the WhatsApp servicing agent.",
                links=[("borrower", "cm03")], channel="whatsapp", text=turns[0], scenario=kind)
        verified_before = fraud_seen = False
        for i, text in enumerate(turns):
            self._cm_now = t0 + timedelta(minutes=i)
            before_cancel = self._cancel_counts()
            r = self.cm03.handle(self.phone[bid], text, message_id=f"wa-{bid}-{self._day}-{i}")
            joined = " ".join(r.get("replies") or [])
            verified = bool(r.get("verified"))
            leak = (not verified) and (bool(MONEY.search(joined)) or loan.loan_id in joined)
            shown = "<OTP>" if text == OTP else text
            tools = r.get("tool_calls") or []
            self.ev("cm03", "message", self._cm_now, bid,
                    f"WhatsApp: “{shown}” → agent state {str(r.get('state')).replace('_', ' ').lower()}"
                    + (f", tools {', '.join(tools)}" if tools else "") + ".",
                    links=[("cm03", "intent")], turn=i + 1, state=r.get("state"), verified=verified, text=shown,
                    tools=tools, reply_keys=r.get("reply_keys"), discloses_account_data=leak)
            if verified and not verified_before:
                self.ev("cm03", "otp_verified", self._cm_now, bid, f"{nm} entered the right OTP: identity verified, "
                        "account details may now be shared.", links=[("cm03", "spine")])
            verified_before = verified_before or verified
            keys = r.get("reply_keys") or []
            if any(k.startswith("BALANCE") for k in keys) and verified:
                self.ev("cm03", "balance_shared", self._cm_now, bid, f"The agent shared {nm}'s balance (after OTP).",
                        reason="BALANCE")
            if r.get("fraud_suspect") and not fraud_seen:
                fraud_seen = True
                self.ev("cm03", "fraud_suspect", self._cm_now, bid,
                        f"{nm}'s UTR is not in the bank feed although the date is past T+2: FRAUD SUSPECT, a person "
                        "takes over, nothing is marked paid.", links=[("cm03", "hitl")], signal="NOT_IN_FEED",
                        reason=r.get("handoff_reason"))
            if "record_payment_claim" in tools:
                self.ev("cm03", "claim_recorded", self._cm_now, bid, f"{nm}'s payment claim is recorded.",
                        links=[("cm03", "payments")], reason=kind)
            if "apply_verified_payment" in tools:
                cancelled, other = self._cancel_delta(before_cancel, loan.obligation_id)
                self.ev("payments", "claim_verified", self._cm_now, bid, f"{nm}'s UTR matches a settled credit in the "
                        "bank feed (D7).", links=[("cm03", "payments")], source="CM-03 claim")
                self._on_paid(b, self._cm_now, loan, cancelled, other, "CM-03 claim", "UTR matched in D7 via WhatsApp")
            if "record_stop" in tools:
                b.opted_out = True
                self.opt_outs += 1
                self.ev("borrower", "stop", self._cm_now, bid, f"{nm} said STOP on WhatsApp: every channel stops.",
                        links=[("cm03", "aladdin")], channel="whatsapp", text="STOP")
        conv_resolved = r.get("state") not in ("HANDOFF",) and not r.get("handoff_reason")
        if conv_resolved:
            self.ev("cm03", "resolved", self._cm_now, bid, f"{nm}'s WhatsApp conversation was resolved without a human.",
                    state=r.get("state"))
        else:
            self.ev("cm03", "handoff", self._cm_now, bid, f"{nm}'s WhatsApp conversation goes to a person "
                    f"({str(r.get('handoff_reason')).replace('_', ' ').lower()}).", links=[("cm03", "hitl")],
                    reason=r.get("handoff_reason"))
        self._churn({"event_id": f"cm03:{bid}:{self._day}", "borrower_id": bid, "kind": "intent", "channel": "whatsapp",
                     "occurred_at": self._cm_now, "intent": {"stop": "STOP", "pay": "CLAIMS_PAID",
                                                              "fake_utr": "CLAIMS_PAID"}.get(kind, "OTHER")}, "WhatsApp")

    def _cancel_counts(self) -> dict:
        return {r["obligation_id"]: r["n"] for r in self.ledger._conn().execute(
            "SELECT obligation_id, COUNT(*) n FROM touches WHERE reason='OBLIGATION_CLEARED' GROUP BY obligation_id")}

    def _cancel_delta(self, before: dict, obligation_id: str) -> tuple:
        after = self._cancel_counts()
        mine = after.get(obligation_id, 0) - before.get(obligation_id, 0)
        other = sum(n - before.get(o, 0) for o, n in after.items() if o != obligation_id)
        return mine, other

    # ---------- learning views ----------
    def learning_snapshot(self) -> dict:
        return L.LiveStackRun.learning_snapshot(self)

    def _learning_events(self, at: datetime):
        snap = self.learning_snapshot()
        cells = snap.pop("cells")
        if self._learn_prev is not None:
            moves = []
            for key, (m, n) in cells.items():
                pm, pn = self._learn_prev.get(key, (m, n))
                if n > pn and abs(m - pm) >= 0.004:
                    moves.append((abs(m - pm), key, pm, m))
            moves.sort(reverse=True)
            seen, picked = set(), []
            for mv in moves:
                k = (mv[1][1], mv[1][2], round(mv[2], 4), round(mv[3], 4))
                if k not in seen:
                    seen.add(k)
                    picked.append(mv)
            for _, (band, ch, slot), pm, m in picked[:3]:
                up = m > pm
                self.ev("learner", "posterior", at, None,
                        f"Learning: {CH_NAME[ch]} in the {SLOT_WORD.get(slot, slot)} works {'better' if up else 'worse'} "
                        f"for people {L.BAND_WORD.get(band, band)} ({pm * 100:.1f} → {m * 100:.1f} in 100 reply).",
                        links=[("outcomes", "learner")], channel=ch, band=band, slot=slot, before=round(pm, 4),
                        after=round(m, 4), direction="up" if up else "down")
        self._learn_prev = cells
        self.snapshot = snap

    def _switch_event(self, at: datetime):
        """Two views of "the best channel changed": the learner's preferred channel per band (posterior replies
        per rupee among arms above the reply floor, as k09.preference_switches) and the day's send mix."""
        prefs = {band: (k09.score_table(self.ranker, band, eligible_only=True) or [None])[0] for band in k09.BANDS}
        self.preference.append(prefs)
        prev_prefs = getattr(self, "_pref_prev", None) or {}
        for band, top in prefs.items():
            old = prev_prefs.get(band)
            if top and old and top["channel"] != old["channel"]:
                drift = old["channel"] == "whatsapp" and self._day >= DRIFT_DAY
                self.ev("learner", "switch", at, None,
                        f"For people {L.BAND_WORD.get(band, band)}, Kairos now prefers {CH_NAME[top['channel']]} over "
                        f"{CH_NAME[old['channel']]} ({old['replies_per_inr']} → {top['replies_per_inr']} replies per ₹)"
                        + (" after WhatsApp replies dropped." if drift else "."), links=[("outcomes", "learner")],
                        from_channel=old["channel"], to_channel=top["channel"], band=band, basis="posterior",
                        after_drift=drift)
        self._pref_prev = prefs
        mix = Counter(ch for (_, ch) in self.mix[-1].elements()) if self.mix else Counter()
        if sum(mix.values()) < 30:
            return
        top = max(sorted(mix), key=lambda ch: mix[ch])
        prev, self._top_prev = self._top_prev, top
        if prev is None or prev == top:
            return
        drift = prev == "whatsapp" and self._day >= DRIFT_DAY
        lead = (f"WhatsApp stopped working as well from day {DRIFT_DAY + 1}, so Kairos moved to {CH_NAME[top]}"
                if drift else f"Kairos moved its main channel from {CH_NAME[prev]} to {CH_NAME[top]}")
        self.ev("learner", "switch", at, None, f"{lead}: today {mix[top]} {CH_NAME[top]} vs {mix[prev]} {CH_NAME[prev]}.",
                links=[("outcomes", "learner")], from_channel=prev, to_channel=top, counts=dict(mix), basis="send_mix",
                after_drift=drift)


# ---------- counters (built only from released events) ----------
def _new_components() -> dict:
    C = Counter
    return {
        "spine": {"borrowers": 0, "loans_enrolled": 0, "pool_paise": 0, "per_case_paise": 0, "days_started": 0},
        "churn": {"cohort_sizes": {}, "layer_sizes": {}, "risk_sizes": {}, "moves": 0, "moves_by_engine": C(),
                  "_b": {}},
        "learner": {"decisions": 0, "contact": 0, "wait": 0, "by_channel": C(), "wait_by_rule": C(), "voice_first": 0,
                    "posterior_moves": 0, "switches": 0},
        "aladdin": {"checked": 0, "allowed": 0, "blocked": 0, "blocked_by_rule": C(), "blocked_by_stage": C(),
                    "rechecked": 0, "cap_breaches": 0},
        "executor": {"dispatched": 0, "by_channel": C(), "spend_paise": 0},
        "channel": {"delivered": 0, "failed": 0, "read": 0, "answered": 0, "not_answered": 0, "duplicates_ignored": 0,
                    "by_channel": {}},
        "borrower": {"replies": 0, "stops": 0, "inbound": 0, "replies_by_channel": C()},
        "intent": {"classified": 0, "by_label": C(), "by_tier": C(), "needs_human": 0, "actions": C()},
        "voice": {"bots_selected": 0, "calls": 0, "connected": 0, "by_disposition": C(), "by_bot": C(),
                  "by_use_case": C(), "by_action": C(), "low_confidence_to_review": 0, "duplicates_ignored": 0},
        "cm03": {"conversations": 0, "messages": 0, "otp_verified": 0, "disclosures_before_otp": 0, "balance_shared": 0,
                 "fakes_caught": 0, "claims_recorded": 0, "claims_verified": 0, "resolved_without_human": 0,
                 "handoffs": 0},
        "hitl": {"requested": 0, "pending": 0, "approved": 0, "rejected": 0, "blocked_at_recheck": 0, "expired": 0,
                 "cases_opened": 0, "cases_resolved": 0, "by_rule": C()},
        "payments": {"verified": 0, "rupees": 0, "recovered_paise": 0, "by_source": C(), "claims": 0,
                     "claims_verified": 0, "claims_rejected": 0, "touches_cancelled": 0, "cancelled_other_emis": 0,
                     "promises_kept": 0, "promises_broken": 0},
        "outcomes": {"finalized": 0, "by_status": C(), "learner_updates": 0},
        "reports": {"daily_reports": 0, "last": None, "totals": Counter()},
    }


def apply_event(c: dict, e: dict) -> None:
    comp, k, d = e["component"], e["kind"], e.get("detail") or {}
    x = c[comp]
    if comp == "spine":
        if k == "portfolio_loaded":
            x["borrowers"], x["loans_enrolled"] = d["borrowers"], d["loans_enrolled"]
        elif k == "budget_scope":
            x["pool_paise"], x["per_case_paise"] = d["pool_paise"], d["per_case_paise"]
        elif k == "day_start":
            x["days_started"] += 1
    elif comp == "churn":
        b = x["_b"].setdefault(e["borrower_id"], {})
        if k == "assigned":
            b.update(cohort=d["cohort"], layer=d["layer"], risk=d["risk_tier"])
        elif k == "moved":
            x["moves"] += 1
            x["moves_by_engine"][d["engine"]] += 1
            b.update(cohort=d.get("cohort"), layer=d.get("layer"), risk=d.get("risk_tier"))
    elif comp == "learner":
        if k == "contact":
            x["decisions"] += 1
            x["contact"] += 1
            x["by_channel"][d["channel"]] += 1
            x["voice_first"] += d.get("lane") == "voice_first"
        elif k == "wait":
            x["decisions"] += 1
            x["wait"] += 1
            x["wait_by_rule"][d.get("rule") or "LEARNER"] += 1
        elif k == "posterior":
            x["posterior_moves"] += 1
        elif k == "switch":
            x["switches"] += 1
    elif comp == "aladdin":
        if k == "allowed":
            x["checked"] += 1
            x["allowed"] += 1
        elif k == "blocked":
            x["checked"] += 1
            x["blocked"] += 1
            x["blocked_by_rule"][d.get("label") or d.get("rule") or "rule"] += 1
            x["blocked_by_stage"][d.get("stage") or "plan"] += 1
        elif k == "rechecked":
            x["checked"] += 1
            x["rechecked"] += 1
    elif comp == "executor" and k == "dispatched":
        x["dispatched"] += 1
        x["by_channel"][d["channel"]] += 1
        x["spend_paise"] += d["cost_paise"]
    elif comp == "channel":
        ch = d.get("channel")
        per = x["by_channel"].setdefault(ch, {"delivered": 0, "read": 0, "failed": 0, "answered": 0, "not_answered": 0})
        key = {"delivered": "delivered", "failed": "failed", "read": "read", "call_answered": "answered",
               "call_not_answered": "not_answered", "duplicate_ignored": "duplicates_ignored"}[k]
        x[key] += 1
        if key in per:
            per[key] += 1
    elif comp == "borrower":
        if k == "replied":
            x["replies"] += 1
            x["replies_by_channel"][d.get("channel")] += 1
        elif k == "stop":
            x["stops"] += 1
        elif k == "whatsapp_inbound":
            x["inbound"] += 1
    elif comp == "intent" and k == "classified":
        x["classified"] += 1
        x["by_label"][d["label"]] += 1
        x["by_tier"][d["tier"]] += 1
        x["needs_human"] += bool(d.get("needs_human"))
        x["actions"][d.get("action") or "NONE"] += 1
    elif comp == "voice":
        if k == "bot_selected":
            x["bots_selected"] += 1
            x["by_use_case"][d["use_case"]] += 1
        elif k == "call_ended":
            x["calls"] += 1
            x["connected"] += bool(d.get("connected"))
            x["by_disposition"][d["disposition"]] += 1
            x["by_bot"][d.get("bot") or "bot"] += 1
            x["by_action"][d.get("action") or "NONE"] += 1
            x["low_confidence_to_review"] += bool(d.get("low_confidence_review"))
        elif k == "duplicate_ignored":
            x["duplicates_ignored"] += 1
    elif comp == "cm03":
        if k == "message":
            x["messages"] += 1
            x["conversations"] += d.get("turn") == 1
            x["disclosures_before_otp"] += bool(d.get("discloses_account_data"))
        elif k == "otp_verified":
            x["otp_verified"] += 1
        elif k == "balance_shared":
            x["balance_shared"] += 1
        elif k == "fraud_suspect":
            x["fakes_caught"] += 1
        elif k == "claim_recorded":
            x["claims_recorded"] += 1
        elif k == "resolved":
            x["resolved_without_human"] += 1
        elif k == "handoff":
            x["handoffs"] += 1
    elif comp == "hitl":
        if k == "approval_pending":
            x["requested"] += 1
            for r in d.get("rules") or []:
                x["by_rule"][r] += 1
        elif k in ("approved", "rejected", "blocked_at_recheck", "expired"):
            x[k] += 1
        elif k == "case_opened":
            x["cases_opened"] += 1
        elif k == "case_resolved":
            x["cases_resolved"] += 1
        x["pending"] = x["requested"] - x["approved"] - x["rejected"] - x["blocked_at_recheck"] - x["expired"]
    elif comp == "payments":
        if k == "verified":
            x["verified"] += 1
            x["recovered_paise"] += d["amount_paise"]
            x["rupees"] = round(x["recovered_paise"] / 100, 2)
            x["by_source"][d.get("source") or "D7/LMS"] += 1
            x["touches_cancelled"] += d.get("cancelled_touches") or 0
            x["cancelled_other_emis"] += d.get("cancelled_other_emis") or 0
        elif k == "claim_pending":
            x["claims"] += 1
        elif k == "claim_verified":
            x["claims_verified"] += 1
            if d.get("source") == "CM-03 claim":
                c["cm03"]["claims_verified"] += 1
        elif k == "claim_rejected":
            x["claims_rejected"] += 1
        elif k == "promise_kept":
            x["promises_kept"] += 1
        elif k == "promise_broken":
            x["promises_broken"] += 1
    elif comp == "outcomes" and k == "finalized":
        x["finalized"] += d["count"]
        for s, n in d["by_status"].items():
            x["by_status"][s] += n
        x["learner_updates"] += d.get("learner_updates") or 0
    elif comp == "reports":
        if k == "daily_summary":
            x["daily_reports"] += 1
            day = {kk: d.get(kk) for kk in ("attempts", "reach", "meaningful_replies", "spend_paise", "cap_breaches")}
            if day["attempts"] or x["last"] is None:   # a quiet Sunday does not replace the last working day
                x["last"] = {**day, "day": e["day"]}
            for kk, v in day.items():
                if kk != "cap_breaches":
                    x["totals"][kk] += v or 0
        elif k == "day_end":
            c["aladdin"]["cap_breaches"] = max(c["aladdin"]["cap_breaches"], d.get("cap_breaches") or 0)


def public_components(c: dict) -> dict:
    out = {}
    for comp, x in c.items():
        y = {}
        for k, v in x.items():
            if k.startswith("_"):
                continue
            if isinstance(v, Counter):
                v = dict(sorted(v.items(), key=lambda kv: str(kv[0])))
            elif isinstance(v, dict):
                v = {kk: (dict(vv) if isinstance(vv, dict) else vv) for kk, vv in v.items()}
            y[k] = v
        out[comp] = y
    bs = c["churn"]["_b"].values()
    for key, name in (("cohort", "cohort_sizes"), ("layer", "layer_sizes"), ("risk", "risk_sizes")):
        out["churn"][name] = dict(sorted(Counter(b.get(key) for b in bs if b.get(key)).items()))
    return out


def scoreboard(c: dict) -> dict:
    return {"sent": c["executor"]["dispatched"], "spent_paise": c["executor"]["spend_paise"],
            "spent_inr": round(c["executor"]["spend_paise"] / 100, 2), "replies": c["borrower"]["replies"],
            "verified_payments": c["payments"]["verified"], "recovered_paise": c["payments"]["recovered_paise"],
            "recovered_inr": round(c["payments"]["recovered_paise"] / 100, 2), "stop_opt_outs": c["borrower"]["stops"],
            "cap_breaches": c["aladdin"]["cap_breaches"]}


# ---------- the controller ----------
class NotPending(RuntimeError):
    pass


class FullCampaign(L.LiveCampaign):
    """One full-mode run in a background thread (same control surface as the race's LiveCampaign)."""
    mode = "full"

    def __init__(self, seed: int = 42, borrowers: int = 300, days: int = 14, speed: float = 1.0,
                 day_seconds: float = L.DAY_SECONDS, workdir: Optional[str] = None,
                 interactive_approvals: bool = False, approval_wait_s: float = 45.0):
        super().__init__(seed=seed, borrowers=borrowers, days=days, speed=speed, day_seconds=day_seconds,
                         workdir=workdir)
        self.interactive = bool(interactive_approvals)
        self.approval_wait_s = float(approval_wait_s)
        self.components = _new_components()
        self.by_borrower: dict = defaultdict(list)
        self.awaiting = False
        self.pending_view: list = []
        self._cmds: "queue.Queue" = queue.Queue()
        self._run: Optional[FullStackRun] = None
        self._decided_n = 0
        self.summary: Optional[dict] = None

    # ---- worker ----
    def _main(self):
        r = None
        try:
            csv_path = k09.write_replay_dataset(os.path.join(self.workdir, f"synthetic_{self.n}_seed{self.seed}.csv"),
                                                self.n, self.seed, k09.START)
            names = L._first_names(csv_path)
            portfolio, loans = portfolio_from_csv(csv_path), k09.load_loans(csv_path)
            r = self._run = FullStackRun(csv_path, self.seed, self.days, os.path.join(self.workdir, "full.db"),
                                         BUDGET_PER_CASE_PAISE, portfolio=portfolio, loans=loans, names=names,
                                         interactive=self.interactive)
            self.budget = {"per_case_paise": BUDGET_PER_CASE_PAISE, "pool_paise": BUDGET_PER_CASE_PAISE * len(portfolio),
                           "basis": "full-mode demo pool (SIMULATED)"}
            setup = r.day_events
            with self._lock:
                self.status = "running"
            for day in range(self.days):
                if self._stop.is_set():
                    raise L._Stopped()
                with self._lock:
                    self.day, self.progress = day + 1, 0.0
                r.day_events = setup if day == 0 else []
                r.plan_phase(day)
                if self.interactive and r.pending:
                    batch = self._sorted(r.day_events)
                    first = [e for e in batch if e["_pos"] <= APPROVAL_CUT]
                    rest = [e for e in batch if e["_pos"] > APPROVAL_CUT]
                    self._release(first, day, 0.0, APPROVAL_CUT)
                    self._await_approvals(r)
                    r.day_events = []
                    r.send_phase(day)
                    self._release(self._sorted(rest + r.day_events), day, APPROVAL_CUT, 1.0)
                else:
                    if not self.interactive:
                        r.decide_pending_simulated()
                    r.send_phase(day)
                    self._release(self._sorted(r.day_events), day, 0.0, 1.0)
            r.finalizer.finalize_due(datetime.combine(r.start + timedelta(days=self.days + 2), dtime(0, 0)))
            with self._lock:
                rep = r.report()
                self.summary = {"report": L._report_view(rep), "k27": rep.get("k27"),
                                "cm03": r.cm03.metrics(), "extra_payments": r.extra_paid,
                                "sarvam_model_tiers": "skipped: no Sarvam model tier is configured in this branch"}
                self.learning = r.snapshot if hasattr(r, "snapshot") else None
                self.status = "finished"
        except L._Stopped:
            with self._lock:
                self.status = "stopped"
        except Exception as exc:  # surfaced in /state; the API process keeps running
            with self._lock:
                self.status, self.error = "error", f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self.awaiting = False
            self._drain_cmds("run ended")
            if r is not None:
                try:
                    r.churn.close()
                except Exception:
                    pass
                try:
                    r.ledger.close_all()
                except Exception:
                    pass
            if self._own_workdir:
                shutil.rmtree(self.workdir, ignore_errors=True)

    @staticmethod
    def _sorted(batch: list) -> list:
        return sorted(batch, key=lambda e: (e["_pos"], e["_o"]))

    def _release(self, batch: list, day: int, p_from: float = 0.0, p_to: float = 1.0):
        i, n = 0, len(batch)
        prog, last = p_from, time.monotonic()
        while i < n or prog < p_to:
            if self._stop.is_set():
                raise L._Stopped()
            now = time.monotonic()
            dt, last = now - last, now
            if not self.paused:
                prog = p_to if self.day_seconds <= 0 else min(p_to, prog + dt * self.speed / self.day_seconds)
            with self._lock:
                while i < n and (batch[i]["_pos"] <= prog + 1e-9 or prog >= p_to):
                    self._publish(batch[i])
                    i += 1
                self.progress = prog
            if self.day_seconds > 0 and (i < n or prog < p_to):
                time.sleep(0.05)

    def _await_approvals(self, r: FullStackRun):
        with self._lock:
            self.awaiting = True
            self.pending_view = sorted(r.pending.values(), key=lambda p: p["approval_id"])
        deadline = time.monotonic() + self.approval_wait_s
        k = 0
        try:
            while r.pending and time.monotonic() < deadline:
                if self._stop.is_set():
                    raise L._Stopped()
                try:
                    iid, approve, reason, box = self._cmds.get(timeout=0.1)
                except queue.Empty:
                    continue
                if iid not in r.pending:
                    box["error"] = "not pending"
                    box["done"].set()
                    continue
                r.day_events = []
                at = r._at(8, 30) + timedelta(minutes=min(55, 2 * k))
                k += 1
                try:
                    box["result"] = r.decide(iid, approve, at, reason or ("approved in the live demo" if approve
                                                                          else "rejected in the live demo"))
                except (HitlError, KeyError, ValueError) as exc:
                    box["error"] = str(exc)
                with self._lock:
                    for e in self._sorted(r.day_events):
                        self._publish(e)
                    self.pending_view = sorted(r.pending.values(), key=lambda p: p["approval_id"])
                box["done"].set()
        finally:
            with self._lock:
                self.awaiting = False
                self.pending_view = []
            self._drain_cmds("the approval window for today has closed")

    def _drain_cmds(self, why: str):
        while True:
            try:
                _, _, _, box = self._cmds.get_nowait()
            except queue.Empty:
                return
            box["error"] = why
            box["done"].set()

    def submit_approval(self, approval_id: str, approve: bool, reason: Optional[str] = None,
                        timeout: float = 10.0) -> dict:
        """Called from the API thread: the worker thread runs the real HITL call (one SQLite owner)."""
        if not self.interactive:
            raise NotPending("this run has no interactive approvals (start it with interactive_approvals=true)")
        known = any(p["approval_id"] == approval_id for p in self.pending_view)
        if not self.awaiting or not known:
            seen = any(e.get("detail", {}).get("approval_id") == approval_id for e in self.events
                       if e["component"] == "hitl")
            if not seen:
                raise KeyError(approval_id)
            raise NotPending(f"{approval_id} is not pending")
        box = {"done": threading.Event()}
        self._cmds.put((approval_id, bool(approve), reason, box))
        if not box["done"].wait(timeout):
            raise NotPending("the run did not take the decision in time")
        if "error" in box:
            raise NotPending(box["error"])
        return box["result"]

    def _publish(self, e: dict):
        e = {k: v for k, v in e.items() if not k.startswith("_")}
        e["id"] = e["seq"] = len(self.events) + 1
        e["provenance"] = PROVENANCE
        e["mode"] = "full"
        if len(self.events) < L.MAX_EVENTS:
            self.events.append(e)
        if e.get("borrower_id"):
            self.by_borrower[e["borrower_id"]].append(len(self.events) - 1)
        apply_event(self.components, e)
        self.clock = e["t"][11:16]
        if e["component"] == "reports" and e["kind"] == "day_end":
            sb = scoreboard(self.components)
            self.series.setdefault("kairos", []).append({"day": e["day"], "spent_paise": sb["spent_paise"],
                                                         "recovered_paise": sb["recovered_paise"],
                                                         "replies": sb["replies"]})

    # ---- read ----
    def state(self, cursor: int = 0, limit: int = 3000) -> dict:
        with self._lock:
            cursor = max(0, int(cursor))
            evs = self.events[cursor:cursor + max(1, min(int(limit), 10_000))]
            return {
                "provenance": PROVENANCE, "mode": "full",
                "label": "SIMULATED: synthetic borrowers (K03 generator), DEV simulator, mock channel adapters "
                         "(dry-run), fixture webhooks, placeholder costs and caps",
                "status": self.status, "error": self.error, "paused": self.paused, "speed": self.speed,
                "seed": self.seed, "borrowers": self.n, "days": self.days, "day": self.day,
                "day_progress": round(self.progress, 3), "clock": self.clock,
                "date": (k09.START + timedelta(days=max(0, self.day - 1))).isoformat() if self.day else None,
                "drift_day": DRIFT_DAY + 1, "budget": self.budget, "components_list": list(COMPONENTS),
                "interactive_approvals": self.interactive, "awaiting_approvals": self.awaiting,
                "pending_approvals": list(self.pending_view),
                "components": public_components(self.components),
                "counters": {"kairos": scoreboard(self.components)},
                "series": {"kairos": list(self.series.get("kairos", []))},
                "learning": self.learning if self.status == "finished" else None,
                "cursor": cursor + len(evs), "total_events": len(self.events), "events": evs,
                "final": self.summary,
            }

    def borrower(self, borrower_id: str) -> dict:
        with self._lock:
            idx = list(self.by_borrower.get(borrower_id, []))
            if not idx:
                raise KeyError(borrower_id)
            evs = [self.events[i] for i in idx]
        card = {"borrower_id": borrower_id, "first_name": evs[0].get("first_name"), "cohort": None, "layer": None,
                "risk_tier": None, "holds": [], "paid": False, "stopped": False, "provenance": PROVENANCE}
        holds = []
        for e in evs:
            d = e["detail"]
            if e["component"] == "churn":
                card.update(cohort=d.get("cohort") or card["cohort"], layer=d.get("layer") or card["layer"],
                            risk_tier=d.get("risk_tier") or card["risk_tier"])
            act = d.get("action") if e["component"] in ("intent", "voice") else None
            if act in ("PTP_HOLD", "HARDSHIP_HOLD", "CALLBACK_PENDING", "HARDSHIP_HOLD_AND_HANDOFF",
                       "SUPERVISOR_HOLD_AND_REVIEW", "K13_HOLD_AND_HANDOFF", "PAYMENT_CLAIM_PENDING"):
                holds.append(act)
            if e["component"] == "payments" and e["kind"] == "verified":
                card["paid"] = True
            if e["component"] == "borrower" and e["kind"] == "stop":
                card["stopped"] = True
        card["holds"] = sorted(set(holds))
        comps = Counter(e["component"] for e in evs)
        return {"borrower_id": borrower_id, "card": card, "components": dict(comps), "events": evs,
                "provenance": PROVENANCE}

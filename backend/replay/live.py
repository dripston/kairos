"""Live collections view (`/demo/live`): the K09 replay played out in (scaled) real time.

SIMULATED. Two arms run side by side on the SAME synthetic borrowers, seed, caps and budget, through
the SAME stack as `replay/k09.py` (StackRun: K03 spine → K06 SupervisorFSM plan/dispatch → executor →
DEV simulator with the day-7 WhatsApp drift → ledger → OutcomeFinalizer → K09 learner):

    kairos  K09 learner, current replay default (KairosOptions())
    cn10    CreditNirvana 10-day fixed cadence (the baseline the CM-02 bar is measured against)

Nothing here decides anything. `LiveStackRun` only *watches* a StackRun: it wraps `fsm.plan`,
`fsm.dispatch` and `simulator.run_day` on the instance and turns what they return into plain events, so
the numbers are exactly the replay's numbers (tests assert that against `StackRun.report()`).

Pacing: each simulated day is computed in one go, then its events are released over
`DAY_SECONDS / speed` wall seconds (planning in the first third of the day, sends and outcomes by clock
time after that). Counters are built only from released events, so the screen and `/state` agree.

Payloads carry borrower ids (`BRW-<loan id>`) and the synthetic first name from the K03 generator, never
phone numbers, e-mails, surnames or loan amounts other than the verified EMI amount.
"""
from __future__ import annotations

import math
import os
import shutil
import tempfile
import threading
import time
import traceback
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Optional

from replay import k09
from replay import settings as S
from replay.portfolio import portfolio_from_csv

PROVENANCE = "SIMULATED"
DAY_SECONDS = 12.0          # wall seconds per simulated day at speed 1 (14 days ≈ 2.8 min + compute)
ARMS = ("kairos", "cn10")
ARM_LABEL = {"kairos": "Kairos", "cn10": "Fixed schedule"}
DRIFT_DAY = 6               # 0-based, same as run_comparison's default: WhatsApp response x0.35 from day 7
MAX_EVENTS = 200_000        # safety bound on the in-memory feed

CH_NAME = {"whatsapp": "WhatsApp", "sms": "SMS", "email": "Email", "ivr": "Voice call", "voicebot": "Voice bot"}
CH_ARTICLE = {"whatsapp": "a WhatsApp", "sms": "an SMS", "email": "an email", "ivr": "a voice call",
              "voicebot": "a voice-bot call"}
SLOT_WORD = {"08-11": "morning", "11-14": "midday", "14-17": "afternoon", "17-19": "evening"}
BAND_WORD = {"PRE_DUE": "not yet due", "B1": "1–30 days late", "B2": "31–60 days late",
             "B3": "61–90 days late", "B4": "over 90 days late"}

# Gate rule / reason → (short label for the Safety-check gate, plain sentence fragment)
BLOCK_TEXT = {
    "QUIET_DAY": ("quiet day", "it is a quiet day (Sunday): nobody is contacted"),
    "CONTACT_WINDOW": ("quiet hours", "it is outside the allowed contact hours"),
    "STOP_OPT_OUT": ("said STOP", "{name} said STOP earlier, so no more messages on any channel"),
    "NO_CONSENT": ("no consent", "{name} has not agreed to be contacted on {channel}"),
    "CONSENT": ("no consent", "{name} has not agreed to be contacted on {channel}"),
    "WEEKLY_CAP": ("weekly limit", "{name} already got the most messages allowed this week"),
    "WEEKLY_CAPS": ("weekly limit", "{name} already got the most messages allowed this week"),
    "CHANNEL_CAP": ("daily limit", "{name} already got today's message on {channel}"),
    "ALL_CHANNEL_DAY_CAP": ("daily limit", "{name} already got today's message"),
    "SAME_CHANNEL_GAP": ("too soon", "{name} heard from us on {channel} too recently"),
    "STOP_FOR_DAY": ("resting today", "{name} already replied today"),
    "BUDGET_EXHAUSTED": ("budget used", "today's budget is used up"),
    "BUDGET_AVAILABLE": ("budget used", "today's budget is used up"),
    "PRE_DUE_TOTAL_CAP": ("pre-due limit", "the reminder limit before the due date is reached"),
    "PTP_HOLD": ("promised to pay", "{name} promised to pay, so we wait"),
    "HARDSHIP_HOLD": ("hardship hold", "{name} reported hardship; a person handles this"),
    "SUPERVISOR_HOLD": ("held by a person", "a supervisor put {name} on hold"),
    "CALLBACK_PENDING": ("callback booked", "{name} asked for a callback"),
    "OBLIGATION_CLEARED": ("already paid", "{name} has already paid"),
    "NO_APPROVED_TEMPLATE": ("no approved message", "there is no approved message in {name}'s language"),
    "TEMPLATE_APPROVED": ("no approved message", "there is no approved message in {name}'s language"),
    "CHANNEL_NOT_ALLOWED": ("channel not allowed", "{channel} is not allowed at this stage"),
    "DPD_INVALID": ("data check", "the loan's overdue days are not confirmed"),
    "DPD_VALID": ("data check", "the loan's overdue days are not confirmed"),
    "CAMPAIGN_TARGET_MET": ("campaign done", "the campaign already reached its goal"),
    "CAMPAIGN_STOP_CONDITION": ("campaign stopped", "the campaign was stopped"),
}
NOT_GUARDRAIL = {"LEARNER", "IDEMPOTENCY", "CANDIDATE", "ELIGIBILITY"}


def money(paise: int) -> str:
    """Indian grouping, whole rupees for big numbers: 1234567 paise → '₹12,345.67' / '₹4,200'."""
    r = paise / 100
    whole = int(r)
    s = str(abs(whole))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    frac = "" if abs(r) >= 100 or r == whole else f".{int(round(abs(r) * 100)) % 100:02d}"
    return f"{'-' if r < 0 else ''}₹{s}{frac}"


def clock(t: dtime) -> str:
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def block_label(rule: str, reason: str) -> tuple[str, str]:
    return BLOCK_TEXT.get(reason) or BLOCK_TEXT.get(rule) or ("safety rule", "a safety rule says not now")


class LiveStackRun(k09.StackRun):
    """A StackRun whose plan / dispatch / simulator results are also written out as display events."""

    def __init__(self, *a, names: dict, **kw):
        super().__init__(*a, **kw)
        self.names = names                  # borrower_id -> synthetic first name
        self._idx = {b.borrower_id: b.index for b in self.portfolio}
        self.day_events: list[dict] = []
        self._day = 0
        self._by_iid: dict = {}             # instruction_id -> PlanResult (today)
        self._sent: list = []               # PlanResults that went to a provider today, in dispatch order
        self._plan_i = 0
        self._learn_prev: Optional[dict] = None
        self._top_prev: Optional[str] = None
        plan, dispatch, sim_day = self.fsm.plan, self.fsm.dispatch, self.simulator.run_day

        def plan_w(borrower_id, obligation_id, campaign_id, band, cands, at, rng=None):
            res = plan(borrower_id, obligation_id, campaign_id, band, cands, at, rng=rng)
            self._on_plan(res)
            return res

        def dispatch_w(iid, at):
            out = dispatch(iid, at)
            self._on_dispatch(iid, at, out)
            return out

        def sim_w(day, decisions, portfolio, log):
            outs = sim_day(day, decisions, portfolio, log)
            self._on_outcomes(outs)
            return outs

        self.fsm.plan, self.fsm.dispatch, self.simulator.run_day = plan_w, dispatch_w, sim_w

    # ---------- event helpers ----------
    def _ev(self, kind: str, at: datetime, bid: Optional[str] = None, *, pos: Optional[float] = None, **kw) -> dict:
        e = {"arm": self.arm, "day": self._day + 1, "time": at.strftime("%H:%M"), "kind": kind,
             "borrower_id": bid, "name": self.names.get(bid) if bid else None, **kw}
        e["_pos"] = pos if pos is not None else _pos_for(at)
        self.day_events.append(e)
        return e

    def _plan_pos(self) -> float:
        self._plan_i += 1
        return 0.03 + 0.30 * min(1.0, self._plan_i / max(1, len(self.portfolio)))

    def _date(self) -> date:
        return self.start + timedelta(days=self._day)

    # ---------- the day ----------
    def run_day(self, day: int):
        self._day, self._plan_i, self._by_iid, self._sent = day, 0, {}, []
        d = self._date()
        if day == DRIFT_DAY and self.learning:
            self._ev("world", datetime.combine(d, dtime(7, 0)), pos=0.0,
                     text="The simulated world changes today: WhatsApp replies start dropping (x0.35). "
                          "Neither strategy is told; Kairos has to notice on its own.")
        super().run_day(day)
        end = datetime.combine(d, dtime(22, 0))
        if self.learning:
            self._learning_events(end)
            self._switch_event(end)
        self._ev("day_end", end, pos=1.0, cap_breaches=len(self.ledger.cap_breaches()),
                 sent=self.daily[-1]["sent"], spend_paise=self.daily[-1]["spend_paise"])

    def _plan_queue(self, day, d, plan_at):
        if not self.learning:
            return super()._plan_queue(day, d, plan_at)
        rest = set()
        for b in self.portfolio:
            if b.paid or b.opted_out or not b.consents:
                continue
            last = self.log.last_day(b.index)
            if last is not None and day - last < S.MIN_DAYS_BETWEEN_CONTACTS:
                rest.add(b.index)
        queue = super()._plan_queue(day, d, plan_at)
        queued = {b.index for b, _, _ in queue}
        for b in self.portfolio:
            if b.paid or b.opted_out or not b.consents or b.index in queued:
                continue
            name = self.names.get(b.borrower_id, "This borrower")
            if b.index in rest:
                gap = day - self.log.last_day(b.index)
                when = "yesterday" if gap == 1 else f"{gap} days ago"
                self._ev("wait", plan_at, b.borrower_id, pos=self._plan_pos(), rule="REST",
                         text=f"Day {day + 1}: {name} is not contacted today (messaged {when}; "
                              "resting helps replies).")
            else:
                self._ev("wait", plan_at, b.borrower_id, pos=self._plan_pos(), rule="LEARNER",
                         text=f"Day {day + 1}: {name} is not contacted today (a message is unlikely to "
                              "pay off right now, so the money goes to people more likely to reply).")
        return queue

    def _on_plan(self, res):
        at = datetime.combine(self._date(), dtime(8, 0))
        name = self.names.get(res.borrower_id, "This borrower")
        if res.decision == "SEND":
            self._by_iid[res.instruction_id] = res
            send_at = datetime.fromisoformat(res.send_at).replace(tzinfo=None)
            ch, slot = res.channel, SLOT_WORD.get(res.slot, res.slot)
            if self.learning and res.ranking is not None and res.ranking.chosen is not None:
                c = res.ranking.chosen
                pct = max(1, round(c.posterior_mean * 100))
                why = (f"people like {name} ({BAND_WORD.get(res.band, res.band)}) reply most to "
                       f"{CH_NAME[ch]} in the {slot}: about {pct} in 100")
            else:
                why = f"the fixed schedule says {CH_NAME[ch]} today"
            self._ev("decision", at, res.borrower_id, pos=self._plan_pos(), decision="SEND", channel=ch,
                     slot=res.slot, send_time=send_at.strftime("%H:%M"), band=res.band,
                     text=f"Day {self._day + 1}, {clock(send_at.time())}: {name} gets "
                          f"{CH_ARTICLE[ch]} ({why}).")
            return
        cand_ch = None
        if res.rejected:
            cand_ch = res.rejected[0].get("channel")
        if res.decision == "HANDOFF":
            self._ev("handoff", at, res.borrower_id, pos=self._plan_pos(), rule=res.rule, reason=res.reason,
                     text=f"{name} goes to a human agent ({res.reason.replace('_', ' ').lower()}).")
            return
        if res.rule in NOT_GUARDRAIL:
            self._ev("wait", at, res.borrower_id, pos=self._plan_pos(), rule=res.rule,
                     text=f"Day {self._day + 1}: {name} is not contacted today (no message is worth "
                          "its cost right now).")
            return
        label, why = block_label(res.rule, res.reason)
        chn = CH_NAME.get(cand_ch or "", "this channel")
        sentence = why.format(name=name, channel=chn)
        if res.reason == "STOP_OPT_OUT" or res.rule == "STOP_OPT_OUT":
            text = f"Blocked: {name} said STOP, so no more messages on any channel."
        else:
            text = f"Blocked by the safety check: {sentence}."
        self._ev("blocked", at, res.borrower_id, pos=self._plan_pos(), decision=res.decision, rule=res.rule,
                 reason=res.reason, label=label, channel=cand_ch, stage="plan", text=text)

    def _on_dispatch(self, iid, at, out):
        res = self._by_iid.get(iid)
        if res is None:
            return
        name = self.names.get(res.borrower_id, "This borrower")
        if out.sent:
            self._sent.append(res)
            cost = k09.COST_PAISE[res.channel]
            self._ev("sent", at, res.borrower_id, channel=res.channel, cost_paise=cost,
                     text=f"{CH_NAME[res.channel]} sent to {name} ({money(cost)}).")
        else:
            label, why = block_label(out.rule, out.reason)
            self._ev("blocked", at, res.borrower_id, decision=out.decision or "CANCEL", rule=out.rule,
                     reason=out.reason, label=label, channel=res.channel, stage="send",
                     text=f"Stopped at send time: {why.format(name=name, channel=CH_NAME[res.channel])}.")

    def _on_outcomes(self, outcomes):
        for o, res in zip(outcomes, self._sent):
            at = datetime.fromisoformat(res.send_at).replace(tzinfo=None)
            bid, ch = res.borrower_id, o.channel
            name = self.names.get(bid, "This borrower")
            if o.delivered:
                self._ev("delivered", at + timedelta(minutes=1), bid, channel=ch,
                         text=f"{CH_NAME[ch]} reached {name}.")
            else:
                self._ev("not_delivered", at + timedelta(minutes=1), bid, channel=ch,
                         text=f"{CH_NAME[ch]} did not reach {name}.")
            reply_at = at + timedelta(minutes=90)
            if o.opted_out:
                self._ev("stop", reply_at, bid, channel=ch,
                         text=f"{name} said STOP on {CH_NAME[ch]}: all channels stopped for {name}.")
            if o.meaningful_reply:
                self._ev("reply", reply_at, bid, channel=ch, text=f"{name} replied on {CH_NAME[ch]}.")
                if o.payment_claimed:
                    self._ev("promise", reply_at + timedelta(minutes=5), bid, channel=ch,
                             text=f"{name} promised to pay. Waiting for the bank to confirm.")
                    if not o.payment_verified:
                        self._ev("handoff", reply_at + timedelta(hours=2), bid, channel=ch, rule="UNMATCHED_CLAIM",
                                 reason="PAYMENT_CLAIM_NOT_IN_BANK_FEED", needs_human=True,
                                 text=f"{name} says they paid, but the bank has no matching payment yet: "
                                      "a person reviews it.")
            if o.payment_verified:
                emi = self.loans[self._idx[bid]].emi_paise
                self._ev("paid", reply_at + timedelta(hours=2), bid, channel=ch, amount_paise=emi,
                         text=f"{name} paid {money(emi)}, confirmed by the bank (after {CH_ARTICLE[ch]}).")

    # ---------- learning views (K09 arm) ----------
    def learning_snapshot(self) -> dict:
        """Per channel: 'how likely to reply' = the learner's posterior mean, averaged over band × slot,
        weighted by (1 + finalized outcomes). Plus the raw per band × channel × slot means."""
        est = self.ranker.estimates()
        by_ch: dict = {}
        cells = {}
        for e in est:
            if e["slot"] not in k09.K15_SLOT or e["band"] not in k09.BANDS:
                continue
            w = 1 + e["n_obs"]
            a = by_ch.setdefault(e["channel"], [0.0, 0.0, 0])
            a[0] += e["mean"] * w
            a[1] += w
            a[2] += e["segment_n_obs"]
            cells[(e["band"], e["channel"], e["slot"])] = (e["mean"], e["segment_n_obs"])
        channels = [{"channel": ch, "label": CH_NAME[ch], "reply_chance": round(v[0] / v[1], 4),
                     "outcomes": v[2], "cost_paise": k09.COST_PAISE[ch],
                     "replies_per_100_inr": round(v[0] / v[1] / (k09.COST_PAISE[ch] / 100) * 100, 1)}
                    for ch, v in by_ch.items()]
        channels.sort(key=lambda r: S.CHANNELS.index(r["channel"]))
        return {"channels": channels, "cells": cells}

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
            for mv in moves:   # sparse bands share the pooled estimate: report one band per identical move
                k = (mv[1][1], mv[1][2], round(mv[2], 4), round(mv[3], 4))
                if k not in seen:
                    seen.add(k)
                    picked.append(mv)
            for _, (band, ch, slot), pm, m in picked[:2]:
                up = m > pm
                self._ev("learner", at - timedelta(minutes=30), None, channel=ch, band=band, direction="up" if up else "down",
                         text=f"Learning: {CH_NAME[ch]} in the {SLOT_WORD.get(slot, slot)} is working "
                              f"{'better' if up else 'worse'} for people {BAND_WORD.get(band, band)} "
                              f"({pm * 100:.1f} → {m * 100:.1f} in 100 reply) → "
                              f"{'more' if up else 'less'} weight.")
        self._learn_prev = cells
        self._ev("learning", at - timedelta(minutes=29), None, snapshot=snap)

    def _switch_event(self, at: datetime):
        mix = Counter(ch for (_, ch) in self.mix[-1].elements())
        if sum(mix.values()) < 30:
            return
        top = max(sorted(mix), key=lambda ch: mix[ch])
        prev, self._top_prev = self._top_prev, top
        if prev is None or prev == top:
            return
        drift = prev == "whatsapp" and self._day >= DRIFT_DAY
        lead = (f"WhatsApp stopped working as well from day {DRIFT_DAY + 1}, so Kairos moved to "
                f"{CH_NAME[top]}" if drift else f"Kairos moved its main channel from {CH_NAME[prev]} to {CH_NAME[top]}")
        self._ev("switch", at - timedelta(minutes=20), None, channel=top, from_channel=prev,
                 text=f"{lead}: today {mix[top]} {CH_NAME[top]} vs {mix[prev]} {CH_NAME[prev]} messages.")


def _pos_for(at: datetime) -> float:
    """Display position within the day: planning 0–0.33, then 09:00–22:00 → 0.35–0.99."""
    minutes = at.hour * 60 + at.minute
    return max(0.35, min(0.99, 0.35 + 0.64 * (minutes - 9 * 60) / (13 * 60)))


# ---------- counters (built only from released events) ----------
def _new_counters(eligible: int) -> dict:
    return {"spent_paise": 0, "recovered_paise": 0, "sent": 0, "delivered": 0, "replies": 0, "promises": 0,
            "verified_payments": 0, "stop_opt_outs": 0, "blocked": 0, "waits": 0, "handoffs": 0,
            "cap_breaches": 0, "decisions": Counter(), "blocked_by": Counter(), "sent_by_channel": Counter(),
            "replies_by_channel": Counter(), "paid_by_channel": Counter(),
            "_funnel": {k: set() for k in ("decided", "contacted", "reached", "replied", "promised", "paid",
                                            "stopped", "blocked")},
            "eligible": eligible}


def _apply(c: dict, e: dict) -> None:
    k, bid = e["kind"], e.get("borrower_id")
    f = c["_funnel"]
    if k == "decision":
        c["decisions"]["SEND"] += 1
        f["decided"].add(bid)
    elif k == "wait":
        c["decisions"]["WAIT"] += 1
        c["waits"] += 1
        f["decided"].add(bid)
    elif k == "blocked":
        c["blocked"] += 1
        c["blocked_by"][e.get("label") or "safety rule"] += 1
        if e.get("stage") == "plan":
            c["decisions"][e.get("decision") or "WAIT"] += 1
        f["blocked"].add(bid)
    elif k == "handoff":
        c["handoffs"] += 1
        if e.get("rule") != "UNMATCHED_CLAIM":
            c["decisions"]["HANDOFF"] += 1
    elif k == "sent":
        c["sent"] += 1
        c["spent_paise"] += e["cost_paise"]
        c["sent_by_channel"][e["channel"]] += 1
        f["contacted"].add(bid)
    elif k == "delivered":
        c["delivered"] += 1
        f["reached"].add(bid)
    elif k == "reply":
        c["replies"] += 1
        c["replies_by_channel"][e["channel"]] += 1
        f["replied"].add(bid)
    elif k == "promise":
        c["promises"] += 1
        f["promised"].add(bid)
    elif k == "paid":
        c["verified_payments"] += 1
        c["recovered_paise"] += e["amount_paise"]
        c["paid_by_channel"][e["channel"]] += e["amount_paise"]
        f["paid"].add(bid)
    elif k == "stop":
        c["stop_opt_outs"] += 1
        f["stopped"].add(bid)
    elif k == "day_end":
        c["cap_breaches"] = max(c["cap_breaches"], e.get("cap_breaches", 0))


def _public(c: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in c.items() if not k.startswith("_")}
    out["stages"] = {"due": c["eligible"], **{k: len(v) for k, v in c["_funnel"].items()}}
    out["spent_inr"] = round(c["spent_paise"] / 100, 2)
    out["recovered_inr"] = round(c["recovered_paise"] / 100, 2)
    out["recovered_per_rupee_spent"] = round(c["recovered_paise"] / c["spent_paise"], 1) if c["spent_paise"] else 0.0
    out["replies_per_1000_inr"] = round(c["replies"] / c["spent_paise"] * 100_000, 2) if c["spent_paise"] else 0.0
    return out


# ---------- the controller ----------
class LiveCampaign:
    """One live run: both arms in lockstep, day by day, in a background thread."""

    def __init__(self, seed: int = 42, borrowers: int = 300, days: int = 14, speed: float = 1.0,
                 day_seconds: float = DAY_SECONDS, workdir: Optional[str] = None):
        self.seed, self.n, self.days = int(seed), int(borrowers), int(days)
        self.speed, self.day_seconds = float(speed), float(day_seconds)
        self.paused = False
        self.status = "idle"
        self.error: Optional[str] = None
        self.events: list[dict] = []
        self.counters: dict = {}
        self.series: dict = {a: [] for a in ARMS}
        self.learning: Optional[dict] = None
        self.reports: dict = {}
        self.budget: dict = {}
        self.day = 0
        self.progress = 0.0
        self.clock = "07:00"
        self._own_workdir = workdir is None
        self.workdir = workdir or tempfile.mkdtemp(prefix="kairos-live-")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.started_at = time.time()

    # ---- control ----
    def start(self) -> "LiveCampaign":
        self._thread = threading.Thread(target=self._main, name="kairos-demo-live", daemon=True)
        self.status = "preparing"
        self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> bool:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- worker ----
    def _main(self):
        runs = {}
        try:
            csv_path = k09.write_replay_dataset(os.path.join(self.workdir, f"synthetic_{self.n}_seed{self.seed}.csv"),
                                                self.n, self.seed, k09.START)
            names = _first_names(csv_path)
            portfolio, loans = portfolio_from_csv(csv_path), k09.load_loans(csv_path)
            # matched spend, exactly as run_comparison: CN10 unconstrained calibration sets the shared pool
            generous = max(k09.COST_PAISE.values()) * self.days
            cal = k09.StackRun("cn10", csv_path, self.seed, self.days, os.path.join(self.workdir, "calibration.db"),
                               generous, DRIFT_DAY, portfolio=portfolio, loans=loans)
            for d in range(self.days):
                if self._stop.is_set():
                    raise _Stopped()
                cal.run_day(d)
            per_case = max(1, math.ceil(cal.spend_paise / len(portfolio)))
            cal.ledger.close()
            self.budget = {"per_case_paise": per_case, "pool_paise": per_case * len(portfolio),
                           "basis": "matched to the fixed schedule's own unconstrained spend (calibration run)"}
            for arm in ARMS:
                runs[arm] = LiveStackRun(arm, csv_path, self.seed, self.days, os.path.join(self.workdir, f"{arm}.db"),
                                         per_case, DRIFT_DAY, portfolio=portfolio, loans=loans, names=names)
            eligible = sum(1 for b in portfolio if b.consents and not b.opted_out)
            with self._lock:
                self.counters = {a: _new_counters(eligible) for a in ARMS}
                self.status = "running"
            for day in range(self.days):
                if self._stop.is_set():
                    raise _Stopped()
                with self._lock:
                    self.day, self.progress = day + 1, 0.0
                batch = []
                for arm in ARMS:
                    r = runs[arm]
                    r.day_events = []
                    r.run_day(day)
                    batch.extend(r.day_events)
                batch.sort(key=lambda e: (e["_pos"], e["time"], ARMS.index(e["arm"])))
                self._release(batch, day)
            for r in runs.values():
                r.finalizer.finalize_due(datetime.combine(r.start + timedelta(days=self.days + 2), dtime(0, 0)))
            with self._lock:
                self.reports = {a: _report_view(r.report()) for a, r in runs.items()}
                self.learning = runs["kairos"].learning_snapshot()
                self.learning.pop("cells", None)
                self.status = "finished"
        except _Stopped:
            with self._lock:
                self.status = "stopped"
        except Exception as exc:  # surfaced in /state; the API process keeps running
            with self._lock:
                self.status, self.error = "error", f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            for r in runs.values():
                try:
                    r.ledger.close_all()
                except Exception:
                    pass
            if self._own_workdir:
                shutil.rmtree(self.workdir, ignore_errors=True)

    def _release(self, batch: list, day: int):
        """Release `batch` as display progress goes 0 → 1 over day_seconds / speed (pause holds it)."""
        i, n = 0, len(batch)
        prog, last = 0.0, time.monotonic()
        while i < n or prog < 1.0:
            if self._stop.is_set():
                raise _Stopped()
            now = time.monotonic()
            dt, last = now - last, now
            if not self.paused:
                prog = 1.0 if self.day_seconds <= 0 else min(1.0, prog + dt * self.speed / self.day_seconds)
            with self._lock:
                while i < n and batch[i]["_pos"] <= prog + 1e-9:
                    self._publish(batch[i])
                    i += 1
                self.progress = prog
            if self.day_seconds > 0 and (i < n or prog < 1.0):
                time.sleep(0.05)

    def _publish(self, e: dict):
        e = {k: v for k, v in e.items() if not k.startswith("_")}
        e["seq"] = len(self.events) + 1
        e["provenance"] = PROVENANCE
        if len(self.events) < MAX_EVENTS:
            self.events.append(e)
        c = self.counters[e["arm"]]
        _apply(c, e)
        self.clock = e["time"]
        if e["kind"] == "learning" and e["arm"] == "kairos":
            self.learning = e["snapshot"]
        if e["kind"] == "day_end":
            self.series[e["arm"]].append({"day": e["day"], "spent_paise": c["spent_paise"],
                                          "recovered_paise": c["recovered_paise"], "replies": c["replies"]})

    # ---- read ----
    def state(self, cursor: int = 0, limit: int = 3000) -> dict:
        with self._lock:
            cursor = max(0, int(cursor))
            evs = self.events[cursor:cursor + max(1, min(int(limit), 10_000))]
            counters = {a: _public(c) for a, c in self.counters.items()}
            live_series = {a: self.series[a] + ([{"day": self.day, "spent_paise": self.counters[a]["spent_paise"],
                                                  "recovered_paise": self.counters[a]["recovered_paise"],
                                                  "replies": self.counters[a]["replies"], "partial": True}]
                                                if self.counters and self.status == "running"
                                                and (not self.series[a] or self.series[a][-1]["day"] < self.day)
                                                else [])
                           for a in ARMS}
            return {
                "provenance": PROVENANCE,
                "label": "SIMULATED: synthetic borrowers (K03 generator), DEV simulator, placeholder costs and caps",
                "status": self.status, "error": self.error, "paused": self.paused, "speed": self.speed,
                "seed": self.seed, "borrowers": self.n, "days": self.days, "day": self.day,
                "day_progress": round(self.progress, 3), "clock": self.clock,
                "date": (k09.START + timedelta(days=max(0, self.day - 1))).isoformat() if self.day else None,
                "drift_day": DRIFT_DAY + 1, "budget": self.budget,
                "arms": {a: {"label": ARM_LABEL[a], "model": "K09 learner (replay default)" if a == "kairos"
                             else "CreditNirvana 10-day fixed cadence"} for a in ARMS},
                "counters": counters, "series": live_series, "learning": self.learning,
                "cursor": cursor + len(evs), "total_events": len(self.events), "events": evs,
                "final_reports": self.reports or None,
            }


class _Stopped(Exception):
    pass


def _report_view(r: dict) -> dict:
    keys = ("contacts_sent", "spend_paise", "meaningful_replies", "verified_payments", "recovered_paise",
            "opt_outs", "cap_breaches_k01", "decisions", "model_version")
    return {k: r[k] for k in keys}


def _first_names(csv_path: str) -> dict:
    """borrower_id -> synthetic first name (K03 generator). Never phone, surname or e-mail."""
    from spine.importer import parse_row, read_rows
    out = {}
    for _, row in read_rows(csv_path):
        r = parse_row(row)
        out[r["borrower_id"]] = (row.get("Name") or "Borrower").split()[0]
    return out


# ---------- one run at a time (the API's singleton) ----------
class Busy(RuntimeError):
    pass


class LiveManager:
    def __init__(self, factory: Callable[..., LiveCampaign] = LiveCampaign):
        self.factory = factory
        self.current: Optional[LiveCampaign] = None
        self._lock = threading.Lock()

    def start(self, **kw) -> LiveCampaign:
        with self._lock:
            if self.current is not None and self.current.running:
                raise Busy("a live run is already in progress; stop it first")
            self.current = self.factory(**kw).start()
            return self.current

    def stop(self, timeout: float = 10.0) -> dict:
        with self._lock:
            cur = self.current
        if cur is None:
            return {"stopped": False, "status": "idle"}
        clean = cur.stop(timeout)
        return {"stopped": True, "clean": clean, "status": cur.status}

    def control(self, speed: Optional[float] = None, paused: Optional[bool] = None) -> dict:
        cur = self.current
        if cur is None:
            return {"status": "idle"}
        if speed is not None:
            cur.speed = float(speed)
        if paused is not None:
            cur.paused = bool(paused)
        return {"status": cur.status, "speed": cur.speed, "paused": cur.paused}

    def state(self, cursor: int = 0, limit: int = 3000) -> dict:
        cur = self.current
        if cur is None:
            return {"provenance": PROVENANCE, "status": "idle", "cursor": 0, "events": [], "counters": {},
                    "label": "SIMULATED"}
        return cur.state(cursor, limit)


class LiveLoop:
    """Adapter so the API Lifecycle stops a live run on SIGTERM/SIGINT like its tick loops (never starts one)."""
    name = "demo-live"

    def __init__(self, manager: LiveManager):
        self.manager = manager

    def start(self) -> None:
        pass

    def stop(self, timeout: float) -> bool:
        return bool(self.manager.stop(timeout).get("clean", True))

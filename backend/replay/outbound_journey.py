"""Journey 1 — "Outbound collections, live" (`/demo/journey/outbound`): ONE spotlight borrower inside one SIMULATED
campaign, every engine real, and a person who can take control.

The campaign is the full-mode stack of replay/live_full.py (K03 spine, K27 churn, K09 learner, Aladdin = the K06
Gate + ledger reserve, HITL approvals, executor on mock channel adapters, webhooks, intent stack, voice ingest,
outcome finalizer, payments, K26 reports). This module does not script anything: it drives FullStackRun phase by
phase and lets a presenter act between phases through the REAL control plane:

    plan(day)     06:00–08:00  LMS refresh, K27 tick, K09 plan, Gate + reserve, approvals requested
    window        08:00–09:30  the approval window: approvals, overrides, holds, STOP, pause, kill switch
    send(day)     09:30–22:00  send-time re-check, dispatch (dry-run), webhooks, replies, intent, payments,
                               reply windows close → learner update, K26 report

Human controls (all audited, all through kairos_backend.hitl / Ledger):
    approve / reject           HITL.approve (the Gate runs again) / HITL.reject (budget released)
    override: WAIT             HITL.act(cancel_next)
    override: channel + time   Gate preview (Ledger.preview), then HITL.act(cancel_next) + HITL.schedule_manual
                               (same Gate + atomic reserve; MANUAL_DISPATCH → maker-checker: a second person approves)
    hold / release             HITL.act(pause) / HITL.act(resume)
    STOP on the borrower's behalf  HITL.act(dnc): every channel opted out, every queued touch cancelled
    pause / resume campaign    Ledger.set_campaign_status(PAUSED | RUNNING)
    kill switch                Ledger.set_outbound_paused(True | False)

Outside the window (after a day's sends ran) an action applies at 22:05 that evening, i.e. from the next plan;
an override queued then is applied right after the next plan (the Gate decides it at that moment).

Extra spotlight-only views, all computed by the real modules: the learner's channel × time scores (Ranking /
estimates), Aladdin's verdict per channel (Ledger.preview), the rule order, the outcome finalized for each of the
spotlight's touches with the learner's posterior before / after, and one provider receipt sent twice (the
second copy is ignored by channels.normalize.apply_callback — labelled as a demo injection).

Everything is SIMULATED and dry-run; deterministic given the seed when nobody acts. No PII: synthetic first names,
masked initials, no phone numbers anywhere in the output.
"""
from __future__ import annotations

import os
import queue
import shutil
import tempfile
import threading
import time as _time
import uuid
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Optional

from kairos_backend import gate as kgate
from kairos_backend.channels.normalize import apply_callback
from kairos_backend.contracts import slot_of
from kairos_backend.hitl import HitlError
from kairos_backend.ledger import QUEUED, Instruction
from replay import k09
from replay import live as L
from replay import live_full as F
from replay.portfolio import portfolio_from_csv

PROVENANCE = "SIMULATED"
LABEL = ("SIMULATED: synthetic borrowers (K03 generator), DEV simulator, mock channel adapters (dry-run), "
         "fixture webhooks, placeholder costs and caps")
DEFAULT_SEED, DEFAULT_BORROWERS, DEFAULT_DAYS = 9, 200, 14
PRESENTER = ("demo.presenter", "supervisor")          # the person on stage (X-Kairos-Role-equivalent: supervisor)
CHECKER = ("sup.simulated", "supervisor")             # the simulated second person (maker-checker, approvals)
STAGES = ("churn", "learner", "aladdin", "executor", "webhooks", "intent", "outcomes", "payments")
CHANNELS = ("sms", "whatsapp", "email", "ivr", "voicebot")
OVERRIDE_TIMES = ("07:30", "09:30", "14:30", "17:30", "20:00")
CAMPAIGN = F.CAMPAIGN

# Seed 9, 200 borrowers: three borrowers whose SIMULATED world makes a clear story (found by scanning the
# full-mode run; nothing about them is scripted — change the seed and the engines decide differently).
PRESETS = (
    {"borrower_id": "BRW-SL100073", "first_name": "Vikram", "key": "promise", "title": "Reminder → switch → promise → paid",
     "blurb": "An SMS gets no reply; the learner switches to WhatsApp; a promise to pay; the bank confirms it."},
    {"borrower_id": "BRW-SL100174", "first_name": "Aarav", "key": "voice", "title": "A voice call waits for you",
     "blurb": "The journey says call: the voice-bot call is held for a person's approval before it can go."},
    {"borrower_id": "BRW-SL100067", "first_name": "Diya", "key": "hardship", "title": "Hardship goes to a person",
     "blurb": "A reply reports hardship: Kairos stops, a person takes the case, the first send after is approved."},
)

BLOCK_EXTRA = {
    "OUTBOUND_PAUSED": ("all outbound paused", "a person pressed the kill switch, all outbound is paused"),
    "CAMPAIGN_PAUSED": ("campaign paused", "a person paused the campaign"),
    "CANCELED_BY_HUMAN": ("cancelled by a person", "a person overrode it"),
    "DO_NOT_CONTACT": ("STOP recorded", "{name} asked to stop, every channel is off"),
    "MANUAL_PAUSE": ("held by a person", "a person put {name} on hold"),
    "APPROVAL_EXPIRED": ("approval expired", "nobody approved it in time"),
    "REJECTED_BY_HUMAN": ("rejected by a person", "a person rejected it"),
}


def block_text(rule: Optional[str], reason: Optional[str]) -> tuple:
    return BLOCK_EXTRA.get(reason or "") or L.block_label(rule, reason)


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _clock(at: datetime) -> str:
    return L.clock(at.time())


class Refusal(Exception):
    """A human action Kairos refuses (guardrail, maker-checker, nothing to act on). Carries the audit row."""

    def __init__(self, code: str, message: str, http: int = 409, audit: Optional[dict] = None):
        super().__init__(message)
        self.code, self.message, self.http, self.audit = code, message, http, audit


class SessionClosed(RuntimeError):
    """The session was closed or replaced (another client's /start or /close) while a request was using it."""

    code = "SESSION_REPLACED"


# ---------- one owner thread for every SQLite call of a session ----------
class _Worker:
    """Runs every call of one session on one thread. After shutdown() no call is accepted (SessionClosed at once):
    before this, a request that had picked up the session just before another client's /start or /close queued its
    call behind the worker's stop marker and waited the full timeout (120 s, then a 500; >4 min on the deployment)."""

    def __init__(self):
        self.q: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self.closed = False
        self.t = threading.Thread(target=self._loop, name="journey-outbound", daemon=True)
        self.t.start()

    def call(self, fn: Callable, timeout: float = 120.0):
        box = {"done": threading.Event()}
        with self._lock:  # every accepted call is queued before shutdown's final call and stop marker
            if self.closed:
                raise SessionClosed("this journey session was closed or replaced by another client")
            self.q.put((fn, box))
        if not box["done"].wait(timeout):
            raise TimeoutError("journey worker did not answer in time")
        if "exc" in box:
            raise box["exc"]
        return box["result"]

    def shutdown(self, final: Optional[Callable] = None, timeout: float = 20.0) -> None:
        """Stop accepting calls, let the ones already queued finish, run `final` last, then stop the thread."""
        with self._lock:
            if self.closed:
                return
            self.closed = True
            box = {"done": threading.Event()}
            if final is not None:
                self.q.put((final, box))
            self.q.put(None)
        if final is not None:
            box["done"].wait(timeout)
        self.t.join(5)

    def _loop(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            fn, box = item
            try:
                box["result"] = fn()
            except BaseException as exc:  # handed back to the caller's thread
                box["exc"] = exc
            finally:
                box["done"].set()

    def close(self):
        self.shutdown()


class _Shim:
    """The PlanResult fields send_phase reads, for a touch a person scheduled (HITL manual scheduler)."""

    def __init__(self, iid, ch, slot, band, obligation_id, send_at):
        self.instruction_id, self.channel, self.slot, self.band = iid, ch, slot, band
        self.obligation_id, self.send_at = obligation_id, send_at


# ---------- the run: FullStackRun + spotlight views ----------
class JourneyRun(F.FullStackRun):
    def __init__(self, *a, spotlight: str, **kw):
        self.spot = spotlight
        self._outs_today: list = []
        self._est_before: dict = {}
        self._dup_spot_done = False
        super().__init__(*a, interactive=True, **kw)
        orig = self.finalizer.finalize_due

        def finalize_due(now):   # capture what the finalizer closed + the learner's estimates around it
            self._est_before = self._estimates()
            outs = orig(now)
            self._outs_today = list(outs)
            return outs
        self.finalizer.finalize_due = finalize_due

    # ----- helpers -----
    def sb(self):
        return self.portfolio[self._idx[self.spot]]

    def jev(self, kind: str, when: datetime, summary: str, stg: str, comp: str = "human", bid: Optional[str] = None,
            pos: Optional[float] = None, **detail) -> dict:
        """A journey event (human actions, spotlight-only views). Same schema as FullStackRun.ev."""
        e = {"t": when.strftime("%Y-%m-%dT%H:%M"), "day": self._day + 1, "component": comp, "kind": kind,
             "borrower_id": bid, "first_name": self.names.get(bid) if bid else None, "summary": summary,
             "detail": detail, "links": [], "stage": stg,
             "_pos": F._pos(when) if pos is None else pos, "_o": len(self.day_events)}
        self.day_events.append(e)
        return e

    def band_today(self) -> str:
        return k09.dpd_band(self._dpd(self.sb().index, self._day)) or "B1"

    def _estimates(self) -> dict:
        try:
            band = self.band_today()
            return {(e["channel"], e["slot"]): (e["mean"], e["n_obs"]) for e in self.ranker.estimates(band)}
        except Exception:
            return {}

    def queued(self, bid: Optional[str] = None, day: Optional[date] = None) -> list:
        q = ",".join("?" * len(QUEUED))
        sql = f"SELECT * FROM touches WHERE borrower_id=? AND status IN ({q})"
        args = [bid or self.spot, *QUEUED]
        if day is not None:
            sql += " AND day=?"
            args.append(day.isoformat())
        return [dict(r) for r in self.ledger._conn().execute(sql + " ORDER BY scheduled_at", args)]

    def scores(self, ranking=None) -> list:
        """The learner's channel × time view for the spotlight: Thompson draw + posterior when it ranked,
        else the posterior estimates for the band (what it would rank on)."""
        rows = []
        if ranking is not None and ranking.scores:
            chosen = ranking.chosen
            for s in ranking.scores:
                rows.append({"channel": s.channel, "slot": s.slot, "mean": round(s.posterior_mean, 4),
                             "draw": round(s.sampled_p, 4) if s.sampled_p is not None else None,
                             "replies_per_inr": round(s.replies_per_inr, 3), "qualifies": s.qualifies,
                             "n_obs": s.n_obs, "chosen": bool(chosen and s.channel == chosen.channel
                                                               and s.slot == chosen.slot)})
            return rows
        b = self.sb()
        for e in self.ranker.estimates(self.band_today()):
            if e["slot"] not in k09.K15_SLOT or e["channel"] not in b.consents:
                continue
            rows.append({"channel": e["channel"], "slot": e["slot"], "mean": round(e["mean"], 4), "draw": None,
                         "replies_per_inr": round(e["mean"] / (k09.COST_PAISE[e["channel"]] / 100), 3),
                         "qualifies": e["mean"] >= getattr(self.ranker, "min_reply_prob", 0), "n_obs": e["n_obs"],
                         "chosen": False})
        return rows

    def gate_grid(self, at: datetime, exclude: Optional[str] = None) -> list:
        """Aladdin's verdict for the spotlight on every channel at `at` (Ledger.preview: no reservation)."""
        b, loan = self.sb(), self.loans[self.sb().index]
        out = []
        for ch in CHANNELS:
            ins = Instruction(self.spot, loan.obligation_id, CAMPAIGN, ch, k09.template_for(ch, b.language), at,
                              k09.COST_PAISE[ch])
            d = self.ledger.preview(ins, exclude=exclude)
            label = "allowed" if d.allowed else block_text(d.rule, d.reason)[0]
            out.append({"channel": ch, "allowed": d.allowed, "decision": d.decision, "rule": d.rule,
                        "reason": d.reason, "label": label})
        return out

    # ----- plan: enrich the spotlight's learner + Aladdin events -----
    def _on_plan(self, b, res, lane: str):
        n0 = len(self.day_events)
        super()._on_plan(b, res, lane)
        if b.borrower_id != self.spot:
            return
        for e in self.day_events[n0:]:
            if e["component"] == "learner":
                e["detail"]["scores"] = self.scores(res.ranking if lane != "voice_first" else None)
                e["detail"]["decision"] = res.decision
            if e["component"] == "aladdin":
                e["detail"]["rule_order"] = kgate.rule_order(self.policy)
                e["detail"]["budget"] = None
                if res.instruction_id:
                    t = self.ledger.touch(res.instruction_id) or {}
                    e["detail"]["touch_status"] = t.get("status")
                    e["detail"]["cost_paise"] = k09.COST_PAISE.get(res.channel)
            if e["component"] == "aladdin" and e["kind"] == "blocked":
                e["summary"] = self._reblock(e)

    def _reblock(self, e) -> str:
        d = e["detail"]
        if d.get("reason") not in BLOCK_EXTRA:
            return e["summary"]
        why = block_text(d.get("rule"), d.get("reason"))[1].format(
            name=self.name(e["borrower_id"]), channel=L.CH_NAME.get(d.get("channel") or "", "this channel"))
        if d.get("stage") == "send":
            return f"Aladdin re-checked before sending and stopped it: {why}."
        return f"Aladdin blocked this: {why}."

    def spotlight_after_plan(self):
        """After the K09 plan: the learner's view and Aladdin's per-channel verdict for the spotlight."""
        b = self.sb()
        bid = self.spot
        if b.paid:
            return
        plan_at = self._at(8, 0)
        mine = [e for e in self.day_events if e["borrower_id"] == bid and e["component"] in ("learner", "aladdin")]
        q = self.queued(day=self._date())
        at = datetime.fromisoformat(q[0]["scheduled_at"]).replace(tzinfo=None) if q else self._at(9, 30)
        grid = self.gate_grid(at, exclude=q[0]["instruction_id"] if q else None)
        budget = None
        if q:
            ins = self.ledger.instruction_of(q[0])
            d = self.ledger.preview(ins, exclude=q[0]["instruction_id"])
            budget = d.detail.get("budget")
        for e in mine:
            if e["component"] == "learner" and e["kind"] == "wait" and "scores" not in e["detail"]:
                e["detail"]["scores"] = self.scores()
                e["detail"]["decision"] = "WAIT"
            if e["component"] == "aladdin":
                e["detail"]["checks"] = grid
                e["detail"]["at"] = at.strftime("%H:%M")
                if budget:
                    e["detail"]["budget"] = budget
        if not any(e["component"] == "aladdin" for e in mine):
            ok = [g for g in grid if g["allowed"]]
            if b.opted_out or all(g["reason"] == "STOP_OPT_OUT" for g in grid):
                s = f"Aladdin's rules for {self.name(bid)} today: STOP is on, every channel is blocked."
            elif ok:
                s = (f"Aladdin's rules for {self.name(bid)} today: {len(ok)} of {len(grid)} channels allowed; the "
                     "learner chose to wait.")
            else:
                first = grid[0]
                s = (f"Aladdin's rules for {self.name(bid)} today: no channel allowed "
                     f"({block_text(first['rule'], first['reason'])[0]}).")
            self.jev("checks", plan_at, s, "aladdin", comp="aladdin", bid=bid, pos=0.2999, checks=grid,
                     at=at.strftime("%H:%M"), rule_order=kgate.rule_order(self.policy), stage="plan")
        if not any(e["component"] == "learner" for e in mine) and not b.opted_out:
            why = ("on hold" if not any(g["allowed"] for g in grid) else "not in today's plan")
            self.jev("view", plan_at, f"The learner did not plan {self.name(bid)} today ({why}).", "learner",
                     comp="learner", bid=bid, pos=0.2998, scores=self.scores(), decision="WAIT")

    # ----- send: spotlight outcomes + one duplicate receipt -----
    def spotlight_after_send(self):
        bid = self.spot
        after = self._estimates()
        for o in self._outs_today:
            if o.borrower_id != bid:
                continue
            key = (o.channel, o.slot)
            b0, b1 = self._est_before.get(key), after.get(key)
            learn = o.status in ("REPLY", "NO_REPLY")
            what = {"REPLY": "a reply", "NO_REPLY": "no reply", "UNRESOLVED": "unresolved (kept out of learning)"}
            slot = L.SLOT_WORD.get(o.slot, o.slot)
            s = f"{self.name(bid)}'s {L.CH_NAME.get(o.channel, o.channel)} ({slot}): 24 h window closed with " \
                f"{what.get(o.status, o.status.lower())}"
            if learn and b0 and b1:
                k = max(1, (b1[1] or 0) - (b0[1] or 0))
                s += (f" → learner update: {L.CH_NAME.get(o.channel)} in the {slot} for people "
                      f"{L.BAND_WORD.get(o.band, o.band)} is now {b1[0] * 100:.1f} in 100 (was {b0[0] * 100:.1f}; "
                      f"{k} outcome{'s' if k != 1 else ''} learned tonight, this one included).")
            else:
                s += " → not used for learning."
            self.jev("finalized", self._at(21, 31), s, "outcomes", comp="outcomes", bid=bid,
                     instruction_id=o.instruction_id, status=o.status, channel=o.channel, slot=o.slot, band=o.band,
                     learned=learn, before=round(b0[0], 4) if b0 else None, after=round(b1[0], 4) if b1 else None,
                     n_before=b0[1] if b0 else None, n_after=b1[1] if b1 else None)
        if self._dup_spot_done:
            return
        for e in list(self.day_events):
            if e["borrower_id"] == bid and e["component"] == "channel" and e["kind"] == "duplicate_ignored":
                self._dup_spot_done = True
                return
        for e in list(self.day_events):
            if not (e["borrower_id"] == bid and e["component"] == "channel" and e["kind"] == "delivered"):
                continue
            ch = e["detail"]["channel"]
            t = [x for x in self.ledger._conn().execute(
                "SELECT instruction_id FROM touches WHERE borrower_id=? AND channel=? AND day=? AND status IN "
                "('SENT','DELIVERED')", (bid, ch, self._date().isoformat()))]
            if not t:
                continue
            iid = t[0]["instruction_id"]
            at = datetime.strptime(e["t"], "%Y-%m-%dT%H:%M")
            dup = apply_callback(self.ledger, {"channel": ch, "instruction_id": iid, "status": "DELIVERED",
                                               "occurred_at": at, "provenance": PROVENANCE,
                                               "provider_ref": f"sim-{iid[-8:]}"})
            if dup.get("duplicate"):
                self._dup_spot_done = True
                self.jev("duplicate_ignored", at + timedelta(minutes=2),
                         f"The {L.CH_NAME[ch]} provider sent {self.name(bid)}'s delivery receipt again: the copy is "
                         "ignored, nothing changes.", "webhooks", comp="channel", bid=bid, channel=ch,
                         status="DELIVERED", ignored_reason=dup.get("ignored_reason"), injected=True,
                         note="demo: the provider resends one receipt; the real webhook path dedupes it")
            return


# ---------- the session: phases + human controls ----------
class OutboundJourney:
    def __init__(self, seed: int = DEFAULT_SEED, borrowers: int = DEFAULT_BORROWERS, days: int = DEFAULT_DAYS,
                 spotlight: Optional[str] = None, workdir: Optional[str] = None):
        self.seed, self.n, self.days = int(seed), int(borrowers), int(days)
        self._own = workdir is None
        self.workdir = workdir or tempfile.mkdtemp(prefix="kairos-journey-")
        self.events: list = []
        self.audit: list = []
        self.phase = "ready"            # ready (before a day's plan) | window | finished
        self.day = 0                    # 0-based current day
        self._wk = 0
        self._override: Optional[dict] = None
        self._closed = False
        self.campaign_paused = False
        self.outbound_paused = False
        self.worker = _Worker()
        try:
            self.worker.call(lambda: self._build(spotlight))
        except BaseException:
            self.close()
            raise

    # ----- build -----
    def _build(self, spotlight: Optional[str]):
        csv_path = k09.write_replay_dataset(os.path.join(self.workdir, f"synthetic_{self.n}_seed{self.seed}.csv"),
                                            self.n, self.seed, k09.START)
        names = L._first_names(csv_path)
        portfolio, loans = portfolio_from_csv(csv_path), k09.load_loans(csv_path)
        ids = {b.borrower_id for b in portfolio}
        self.presets = [dict(p, first_name=names.get(p["borrower_id"])) for p in PRESETS
                        if self.seed == DEFAULT_SEED and self.n == DEFAULT_BORROWERS and p["borrower_id"] in ids]
        if spotlight is None:
            spotlight = self.presets[0]["borrower_id"] if self.presets else next(
                b.borrower_id for b in portfolio if len(b.consents) >= 2 and not b.opted_out)
        if spotlight not in ids:
            raise KeyError(spotlight)
        self.spot = spotlight
        self.r = JourneyRun(csv_path, self.seed, self.days, os.path.join(self.workdir, "journey.db"),
                            F.BUDGET_PER_CASE_PAISE, portfolio=portfolio, loans=loans, names=names,
                            spotlight=spotlight)
        self._setup_events = list(self.r.day_events)
        self.r.day_events = []
        self.first_name = names.get(spotlight)

    # ----- publishing -----
    def _publish_pending(self) -> list:
        r = self.r
        batch = sorted(r.day_events, key=lambda e: (e["_pos"], e["_o"]))
        r.day_events = []
        out = []
        for e in batch:
            e = {k: v for k, v in e.items() if not k.startswith("_")}
            e["id"] = len(self.events) + 1
            e["provenance"] = PROVENANCE
            e["spot"] = e.get("borrower_id") == self.spot
            if e["spot"] and not e.get("stage"):
                e["stage"] = stage_of(e)
            if e["component"] == "aladdin" and e["kind"] == "blocked" and e["detail"].get("reason") in BLOCK_EXTRA:
                e["summary"] = r._reblock(e)
            self.events.append(e)
            out.append(e)
        return out

    def _now(self) -> datetime:
        """The clock a human action is stamped with: inside the window 08:05 + 2 min per action, else 22:05."""
        r = self.r
        if self.phase == "window":
            self._wk += 1
            return r._at(8, 5) + timedelta(minutes=min(80, 2 * self._wk))
        if self.phase == "finished":
            return r._at(22, 5)
        if self.day == 0:
            return datetime.combine(r.start, dtime(7, 30))
        return datetime.combine(r.start + timedelta(days=self.day - 1), dtime(22, 5))

    def _human_day(self) -> int:
        """0-based day a human action belongs to (the evening before a plan belongs to the previous day)."""
        return self.day if self.phase in ("window", "finished") or self.day == 0 else self.day - 1

    # ----- phases -----
    def step(self) -> dict:
        return self.worker.call(self._step)

    def _step(self) -> dict:
        r = self.r
        if self.phase == "finished":
            return {"events": [], "phase": self.phase}
        if self.phase == "ready":
            r.day_events = list(self._setup_events) if self.day == 0 else []
            r.plan_phase(self.day)
            r.spotlight_after_plan()
            self.phase, self._wk = "window", 0
            out = self._publish_pending()
            if self._override is not None:
                ov, self._override = self._override, None
                try:
                    out += self._apply_override(ov, queued_from_evening=True)
                except Refusal:
                    out += self._publish_pending()
            return {"events": out, "phase": self.phase}
        # window → send
        self._close_window()
        r.send_phase(self.day)
        r.spotlight_after_send()
        out = self._publish_pending()
        self.day += 1
        if self.day >= self.days:
            r.finalizer.finalize_due(datetime.combine(r.start + timedelta(days=self.days + 2), dtime(0, 0)))
            r._day = self.day - 1
            r.spotlight_after_send()
            self.phase = "finished"
            out += self._publish_pending()
        else:
            self.phase = "ready"
        return {"events": out, "phase": self.phase}

    def _close_window(self):
        """09:25: whatever no person decided, the simulated duty supervisor decides (same deterministic rule as
        full mode); a person's manual send is approved by the simulated second person (maker-checker)."""
        r = self.r
        for k, iid in enumerate(sorted(r.pending)):
            info = r.pending[iid]
            t = r.ledger.touch(iid) or {}
            if t.get("status") != "PENDING_APPROVAL":
                r.pending.pop(iid, None)
                r._not_approved.add(iid)
                r.jev("withdrawn", r._at(8, 20), f"{r.name(info['borrower_id'])}'s {F._chn(info['channel'])} is no "
                      f"longer waiting for approval ({block_text(None, t.get('reason'))[0]}).", "aladdin", comp="hitl",
                      bid=info["borrower_id"], approval_id=iid, reason=t.get("reason"))
                continue
            at = r._at(8, 20) + timedelta(minutes=min(65, 3 * k))
            if info.get("manual"):
                r.decide(iid, True, at, "maker-checker: a second person reviewed the manual send", CHECKER,
                         "the duty supervisor (simulated second person)")
            elif F._u(r.seed, "reject", iid) < 0.12:
                r.decide(iid, False, at, "not today: borrower was spoken to recently", CHECKER, "simulated supervisor")
            else:
                r.decide(iid, True, at, "script and timing reviewed", CHECKER, "simulated supervisor")

    # ----- read -----
    def state(self, cursor: int = 0) -> dict:
        return self.worker.call(lambda: self._state(cursor))

    def _state(self, cursor: int = 0) -> dict:
        cursor = max(0, int(cursor))
        return {"provenance": PROVENANCE, "label": LABEL, "seed": self.seed, "borrowers": self.n, "days": self.days,
                "spotlight": self.spot, "presets": self.presets, "snapshot": self._snapshot(),
                "cursor": len(self.events), "events": [compact(e) for e in self.events[cursor:]]}

    def _snapshot(self) -> dict:
        r = self.r
        d = r.start + timedelta(days=min(self.day, self.days - 1))
        live = {t["instruction_id"] for t in r.ledger._conn().execute(
            "SELECT instruction_id FROM touches WHERE status='PENDING_APPROVAL'")}
        pend = sorted((p for p in r.pending.values() if p["approval_id"] in live),   # withdrawn ones drop out at once
                      key=lambda p: (p["borrower_id"] != self.spot, p["send_time"], p["approval_id"]))
        return {"phase": self.phase, "day": min(self.day, self.days - 1) + 1, "date": d.isoformat(),
                "weekday": d.strftime("%A"), "campaign_paused": self.campaign_paused,
                "outbound_paused": self.outbound_paused, "override_queued": self._override,
                "pending_approvals": [dict(p) for p in pend] if self.phase == "window" else [],
                "card": self._card(), "audit": list(self.audit), "cap_breaches": len(r.ledger.cap_breaches()),
                "applies": ("before today's 09:30 sends" if self.phase == "window" else
                            "from the next plan (today's sends already ran)" if self.phase == "ready" and self.day > 0
                            else "from the first plan" if self.phase == "ready" else "the campaign has ended")}

    def _card(self) -> dict:
        r = self.r
        b = r.sb()
        loan = r.loans[b.index]
        c = r.ledger._conn()
        st = r.ledger._state(c, self.spot)
        row = c.execute("SELECT full_name FROM borrowers WHERE borrower_id=?", (self.spot,)).fetchone()
        masked = r.hitl.mask({"full_name": row["full_name"] if row else ""}, "viewer")["full_name"]
        cons = {x["channel"]: ("STOP" if x["opted_out"] else "yes" if x["consented"] else "no")
                for x in c.execute("SELECT channel, consented, opted_out FROM consents WHERE borrower_id=?", (self.spot,))}
        ob = c.execute("SELECT amount_due_paise, amount_paid_paise, due_date FROM obligations WHERE obligation_id=?",
                       (loan.obligation_id,)).fetchone()
        ch = r.churn.state(self.spot)
        day = min(self.day, self.days - 1)
        dpd = r._dpd(b.index, day)
        band = k09.dpd_band(dpd) or "B1"
        best = None
        try:
            rows = [x for x in k09.score_table(r.ranker, band, eligible_only=True) if x["channel"] in b.consents] or \
                   [x for x in k09.score_table(r.ranker, band) if x["channel"] in b.consents]
            if rows:
                x = rows[0]
                slot = next((s for s, k in k09.K15_SLOT.items() if k == x["slot"]), x["slot"])
                best = {"channel": x["channel"], "slot": slot, "slot_word": L.SLOT_WORD.get(slot, slot),
                        "time": k09.SLOT_MAP[x["slot"]][1].strftime("%H:%M") if x["slot"] in k09.SLOT_MAP else None,
                        "reply_chance": x["mean"], "replies_per_inr": x["replies_per_inr"]}
        except Exception:
            best = None
        holds = [h["kind"] for h in r.hitl._active_holds(c, self.spot)]
        flags = []
        if st["stopped"]:
            flags.append("STOP")
        if st["ptp_date"]:
            flags.append(f"PROMISE {date.fromisoformat(st['ptp_date']):%d %b}")
        if st["hardship_until"]:
            flags.append("HARDSHIP HOLD")
        if st["callback_pending"]:
            flags.append("CALLBACK")
        if holds:
            flags.append("HELD BY A PERSON")
        elif st["supervisor_hold"]:
            flags.append("SUPERVISOR HOLD")
        spent = sum(e["detail"].get("cost_paise") or 0 for e in self.events
                    if e.get("spot") and e["component"] == "executor" and e["kind"] == "dispatched")
        return {"borrower_id": self.spot, "first_name": r.names.get(self.spot), "masked_name": masked,
                "language": b.language, "loan_ref": "…" + loan.loan_id[-4:], "emi_paise": loan.emi_paise,
                "outstanding_paise": (ob["amount_due_paise"] - ob["amount_paid_paise"]) if ob else None,
                "pos_paise": loan.pos_paise, "due_date": ob["due_date"] if ob else None, "dpd": dpd, "band": band,
                "band_word": L.BAND_WORD.get(band, band), "cohort": ch.cohort, "risk_tier": ch.risk_tier,
                "layer": ch.layer, "journey": ch.journey_id, "lifecycle": ch.lifecycle, "consents": cons,
                "best": best, "flags": flags, "holds": holds, "paid": bool(b.paid), "stopped": bool(st["stopped"]),
                "spent_paise": spent,
                "queued": [{"channel": t["channel"], "time": t["scheduled_at"][11:16], "day": t["day"],
                            "status": t["status"]} for t in r.queued()]}

    # ----- human controls (all through the real HITL / Ledger) -----
    def act(self, action: str, **params) -> dict:
        return self.worker.call(lambda: self._act(action, **params))

    def _act(self, action: str, **params) -> dict:
        if self.phase == "finished":
            raise Refusal("CAMPAIGN_ENDED", "the campaign has ended; start a new run", 409)
        fn = {"campaign": self._campaign, "outbound": self._outbound, "approve": self._approve,
              "reject": self._reject, "override": self._override_action, "hold": self._hold, "stop": self._stop,
              "preview": self._preview}.get(action)
        if fn is None:
            raise ValueError(f"unknown action {action}")
        n0 = len(self.events)
        try:
            result = fn(**params)
        except HitlError as exc:   # a role / guardrail refusal from the HITL control plane itself
            ref = Refusal(exc.code, str(exc), getattr(exc, "http", 403))
            return self._refused(ref, n0)
        except Refusal as ref:
            return self._refused(ref, n0)
        self._publish_pending()
        return {"ok": True, "result": result, "events": [compact(e) for e in self.events[n0:]],
                "snapshot": self._snapshot(), "cursor": len(self.events)}

    def _refused(self, ref: "Refusal", n0: int) -> dict:
        self._publish_pending()
        return {"ok": False, "http": ref.http, "refusal": {"code": ref.code, "message": ref.message, "audit": ref.audit},
                "events": [compact(e) for e in self.events[n0:]], "snapshot": self._snapshot(),
                "cursor": len(self.events)}

    def _audit(self, what: str, api: str, check: str, result: str, ok: bool, at: datetime, **extra) -> dict:
        row = {"seq": len(self.audit) + 1, "day": self._human_day() + 1, "time": at.strftime("%H:%M"),
               "who": f"{PRESENTER[0]} ({PRESENTER[1]})", "what": what, "api": api, "check": check,
               "result": result, "ok": ok, **extra}
        self.audit.append(row)
        return row

    def _ledger_audit_since(self, last_id: int) -> list:
        rows = self.r.ledger._conn().execute("SELECT id, type, actor, rule, reason FROM audit WHERE id>? ORDER BY id",
                                             (last_id,)).fetchall()
        return [{"id": x["id"], "type": x["type"], "actor": x["actor"], "rule": x["rule"], "reason": x["reason"]}
                for x in rows][:12]

    def _last_audit_id(self) -> int:
        return self.r.ledger._conn().execute("SELECT COALESCE(MAX(id), 0) FROM audit").fetchone()[0]

    def _drop_from_today(self, iids: set):
        """Touches a person cancelled leave today's dispatch list and the approval queue."""
        r = self.r
        for iid in iids:
            if iid in r.pending:
                r.pending.pop(iid, None)
            r._not_approved.add(iid)

    # campaign pause / kill switch
    def _campaign(self, paused: bool) -> dict:
        r, at = self.r, self._now()
        a0 = self._last_audit_id()
        if paused:
            n = r.ledger.set_campaign_status(CAMPAIGN, "PAUSED", actor=f"human:{PRESENTER[0]}")
        else:
            n = r.ledger.set_campaign_status(CAMPAIGN, "RUNNING", actor=f"human:{PRESENTER[0]}")
        self.campaign_paused = bool(paused)
        what = "Paused the campaign" if paused else "Resumed the campaign"
        eff = (f"{n} queued touch{'es' if n != 1 else ''} cancelled, budget released; every new plan waits "
               "(CAMPAIGN_PAUSED)") if paused else "the next plan runs normally"
        r.jev("campaign_paused" if paused else "campaign_resumed", at, f"{what}: {eff}.", "aladdin",
              cancelled=n, api="Ledger.set_campaign_status", ledger_audit=self._ledger_audit_since(a0))
        self._audit(what, "Ledger.set_campaign_status(" + ("PAUSED" if paused else "RUNNING") + ")",
                    "CONTROL rule CAMPAIGN_PAUSED in the Gate's pre-check" if paused else "—", eff, True, at,
                    cancelled=n)
        return {"campaign_paused": self.campaign_paused, "cancelled": n}

    def _outbound(self, paused: bool) -> dict:
        r, at = self.r, self._now()
        a0 = self._last_audit_id()
        n = r.ledger.set_outbound_paused(bool(paused), actor=f"human:{PRESENTER[0]}",
                                         reason="kill switch pressed in the live demo" if paused else "resumed")
        self.outbound_paused = bool(paused)
        what = "KILL SWITCH: paused all outbound" if paused else "Resumed all outbound"
        eff = (f"{n} queued touch{'es' if n != 1 else ''} on every channel cancelled; Aladdin refuses every send "
               "(OUTBOUND_PAUSED)") if paused else "Aladdin allows sends again from the next plan"
        r.jev("outbound_paused" if paused else "outbound_resumed", at, f"{what}: {eff}.", "aladdin", cancelled=n,
              api="Ledger.set_outbound_paused", ledger_audit=self._ledger_audit_since(a0))
        self._audit(what, f"Ledger.set_outbound_paused({bool(paused)})",
                    "CONTROL rule OUTBOUND_PAUSED, checked at plan and at send" if paused else "—", eff, True, at,
                    cancelled=n)
        return {"outbound_paused": self.outbound_paused, "cancelled": n}

    # approvals
    def _decide(self, approval_id: str, approve: bool, reason: Optional[str] = None) -> dict:
        r = self.r
        if self.phase != "window":
            raise Refusal("NOT_IN_WINDOW", "approvals are decided in the 08:00–09:30 approval window", 409)
        info = r.pending.get(approval_id)
        if info is None:
            raise Refusal("NOT_PENDING", f"{approval_id} is not waiting for approval", 404)
        at = self._now()
        verb = "Approved" if approve else "Rejected"
        target = f"{info.get('first_name') or info['borrower_id']}'s {F._chn(info['channel'])} at {info['send_time']}"
        try:
            out = r.decide(approval_id, approve, at, reason or (f"{verb.lower()} in the live demo"), PRESENTER,
                           "the presenter")
        except HitlError as exc:
            row = self._audit(f"{verb} {target}", f"HITL.{'approve' if approve else 'reject'}", exc.code,
                              f"refused: {exc}", False, at)
            r.jev("refused", at, f"Refused: {exc} ({exc.code}).", "aladdin", bid=info["borrower_id"],
                  code=exc.code, api=f"HITL.{'approve' if approve else 'reject'}")
            raise Refusal(exc.code, str(exc), getattr(exc, "http", 403), row)
        g = out.get("gate") or ""
        check = ("Gate re-run at approval: " + ("allowed" if out["outcome"] == "APPROVED" else f"blocked ({g})")
                 if approve else "budget released")
        self._audit(f"{verb} {target}", f"HITL.{'approve' if approve else 'reject'}", check,
                    out["outcome"].replace("_", " ").lower(), out["outcome"] in ("APPROVED", "REJECTED"), at,
                    borrower_id=info["borrower_id"])
        return out

    def _approve(self, approval_id: str, reason: Optional[str] = None) -> dict:
        return self._decide(approval_id, True, reason)

    def _reject(self, approval_id: str, reason: Optional[str] = None) -> dict:
        return self._decide(approval_id, False, reason)

    # spotlight override
    def _target_at(self, hhmm: str) -> datetime:
        if hhmm not in OVERRIDE_TIMES:
            raise ValueError(f"time must be one of {OVERRIDE_TIMES}")
        d = self.r.start + timedelta(days=self.day)
        return datetime.combine(d, _hhmm(hhmm))

    def _preview(self, channel: str, time: str) -> dict:
        """Aladdin's verdict for a channel + time for the spotlight, right now (no reservation)."""
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}")
        if self.phase != "window":
            return {"checked": False, "note": "Aladdin checks it at the next plan (08:00), before anything is "
                                              "reserved; the override is queued until then."}
        return self._verdict(channel, self._target_at(time))

    def _verdict(self, channel: str, at: datetime) -> dict:
        r = self.r
        b, loan = r.sb(), r.loans[r.sb().index]
        q = r.queued(day=at.date())
        ins = Instruction(self.spot, loan.obligation_id, CAMPAIGN, channel, k09.template_for(channel, b.language), at,
                          k09.COST_PAISE[channel], origin="manual")
        d = r.ledger.preview(ins, exclude=q[0]["instruction_id"] if q else None)
        with r.ledger.tx() as c:
            held = bool(r.hitl._active_holds(c, self.spot))
            rules = r.hitl.approval_rules_for(c, ins, d.detail.get("dpd")) if d.allowed else []
        locked = r.hitl.ap["locked_by_guardrails"]["gate_reasons_refused_for_humans"]
        allowed = d.allowed and not held
        reason = d.reason if not d.allowed else ("SUPERVISOR_HOLD" if held else "ELIGIBLE")
        label, why = block_text(d.rule if not held else "SUPERVISOR_HOLD", reason)
        return {"checked": True, "allowed": allowed, "decision": d.decision if not held else "WAIT", "rule": d.rule,
                "reason": reason, "label": "allowed" if allowed else label,
                "why": "" if allowed else why.format(name=r.name(self.spot), channel=L.CH_NAME.get(channel, channel)),
                "locked_by_guardrails": (not d.allowed) and d.reason in locked, "needs_approval": rules,
                "replaces": ({"channel": q[0]["channel"], "time": q[0]["scheduled_at"][11:16]} if q else None),
                "channel": channel, "time": at.strftime("%H:%M"), "date": at.date().isoformat()}

    def _override_action(self, mode: str, channel: Optional[str] = None, time: Optional[str] = None) -> dict:
        if mode not in ("wait", "channel"):
            raise ValueError("mode must be 'wait' or 'channel'")
        if mode == "channel":
            if channel not in CHANNELS:
                raise ValueError(f"channel must be one of {CHANNELS}")
            self._target_at(time or "")
        ov = {"mode": mode, "channel": channel, "time": time}
        if self.phase != "window":
            self._override = ov
            at = self._now()
            what = ("Override: WAIT" if mode == "wait" else
                    f"Override: {L.CH_NAME[channel]} at {time}")
            self.r.jev("override_queued", at, f"{what} for {self.first_name} is queued: Aladdin checks it right "
                       "after the next plan (08:00).", "learner", bid=self.spot, **ov)
            self._audit(what + " (queued)", "queued until the next plan", "checked by Aladdin at 08:00",
                        "queued", True, at, borrower_id=self.spot)
            return {"queued": True, **ov}
        self._apply_override(ov)
        return {"queued": False, **ov}

    def _apply_override(self, ov: dict, queued_from_evening: bool = False) -> list:
        r = self.r
        at = self._now() if not queued_from_evening else r._at(8, 3)
        nm = r.name(self.spot)
        a0 = self._last_audit_id()
        if r.sb().paid or r.sb().opted_out:
            msg = f"{nm} has {'paid' if r.sb().paid else 'said STOP'}: there is nothing to override"
            row = self._audit("Override", "—", "journey ended" if r.sb().paid else "STOP_OPT_OUT", msg, False, at)
            r.jev("refused", at, f"Refused: {msg}.", "learner", bid=self.spot)
            raise Refusal("NOTHING_TO_OVERRIDE", msg, 409, row)
        if ov["mode"] == "wait":
            q = r.queued(day=r._date())
            if not q:
                row = self._audit("Override: WAIT", "HITL.act(cancel_next)", "nothing queued today",
                                  "no change: the learner already chose to wait today", True, at, borrower_id=self.spot)
                r.jev("override_wait", at, f"Override: WAIT for {nm}: nothing was queued today, the learner already "
                      "waits.", "learner", bid=self.spot, cancelled=None)
                return self._publish_pending()
            res = r.hitl.act(self.spot, "cancel_next", PRESENTER[0], PRESENTER[1],
                             "presenter override: wait today", now=at)
            self._drop_from_today({res["canceled_instruction_id"]})
            t = q[0]
            r.jev("override_wait", at, f"Override: WAIT. {nm}'s {L.CH_NAME[t['channel']]} at {t['scheduled_at'][11:16]} "
                  "is cancelled by a person; its budget goes back to the pool.", "learner", bid=self.spot,
                  cancelled={"channel": t["channel"], "time": t["scheduled_at"][11:16]},
                  api="HITL.act(cancel_next)", ledger_audit=self._ledger_audit_since(a0))
            self._audit(f"Override {nm}: WAIT today", "HITL.act(cancel_next)", "operator may cancel a queued touch",
                        f"{L.CH_NAME[t['channel']]} {t['scheduled_at'][11:16]} cancelled", True, at,
                        borrower_id=self.spot)
            return self._publish_pending()
        ch, hhmm = ov["channel"], ov["time"]
        send_at = self._target_at(hhmm)
        v = self._verdict(ch, send_at)
        what = f"Override {nm}: {L.CH_NAME[ch]} at {hhmm}"
        if not v["allowed"]:
            # the real scheduler refuses it too, and writes HUMAN_ACTION_REFUSED (locked_by_guardrails)
            res = r.hitl.schedule_manual(self.spot, ch, k09.template_for(ch, r.sb().language), send_at, PRESENTER[0],
                                         PRESENTER[1], "presenter override", k09.COST_PAISE[ch], campaign_id=CAMPAIGN,
                                         now=at)
            leftover = [x["instruction_id"] for x in res["results"] if x.get("outcome") in QUEUED]
            for iid in leftover:   # cannot happen when the verdict said no; never leave a stray touch
                r.hitl.cancel_scheduled(iid, PRESENTER[0], PRESENTER[1], "verdict said no", now=at)
            lock = " Locked by guardrails: no person can override this." if v["locked_by_guardrails"] else ""
            r.jev("override_blocked", at, f"{what}: Aladdin blocks it ({v['rule']}: {v['why']}).{lock}", "aladdin",
                  bid=self.spot, verdict=v, api="Ledger.preview + HITL.schedule_manual",
                  ledger_audit=self._ledger_audit_since(a0))
            row = self._audit(what, "HITL.schedule_manual", f"✗ {v['rule']} ({v['reason']})",
                              "refused" + (" — locked by guardrails" if v["locked_by_guardrails"] else ""), False, at,
                              borrower_id=self.spot, rule=v["rule"], reason=v["reason"])
            raise Refusal(v["reason"], f"Aladdin blocks it: {v['why']} ({v['rule']})", 409, row)
        q = r.queued(day=send_at.date())
        replaced = None
        if q:
            res = r.hitl.act(self.spot, "cancel_next", PRESENTER[0], PRESENTER[1],
                             f"presenter override: switch to {ch} at {hhmm}", now=at)
            self._drop_from_today({res["canceled_instruction_id"]})
            replaced = {"channel": q[0]["channel"], "time": q[0]["scheduled_at"][11:16]}
        res = r.hitl.schedule_manual(self.spot, ch, k09.template_for(ch, r.sb().language), send_at, PRESENTER[0],
                                     PRESENTER[1], f"presenter override: {ch} at {hhmm}", k09.COST_PAISE[ch],
                                     campaign_id=CAMPAIGN, now=at)
        one = res["results"][0]
        if one["outcome"] not in QUEUED:
            r.jev("override_blocked", at, f"{what}: refused ({one.get('reason')}: {one.get('message')}).", "aladdin",
                  bid=self.spot, result={k: one.get(k) for k in ("outcome", "reason", "message", "locked_by_guardrails")})
            row = self._audit(what, "HITL.schedule_manual", str(one.get("reason")), "refused", False, at)
            raise Refusal(str(one.get("reason") or "REFUSED"), str(one.get("message") or "refused"), 409, row)
        iid = one["instruction_id"]
        band = r.band_today()
        slot = slot_of(send_at) or "08-11"
        r.sends.append((send_at.isoformat(), r.sb(), _Shim(iid, ch, slot, band, one["obligation_id"],
                                                         send_at.isoformat())))
        r._not_approved.discard(iid)
        if ch == "voicebot":   # the voice bot for the call (registry: use case → agent, voice, language)
            r._select_bot(r.sb(), r.sends[-1][2], F._pos(at))
        rules = one.get("approval_rules") or []
        if one["outcome"] == "PENDING_APPROVAL":
            r.pending[iid] = {"approval_id": iid, "borrower_id": self.spot, "first_name": self.first_name,
                              "channel": ch, "rules": rules, "send_time": hhmm, "day": self.day + 1, "manual": True,
                              "maker": PRESENTER[0]}
        rep = f" (replaces the learner's {L.CH_NAME[replaced['channel']]} at {replaced['time']})" if replaced else ""
        r.jev("override_scheduled", at, f"{what}{rep}: Aladdin ✓ every rule passed; reserved"
              + (", waiting for a second person (maker-checker)" if one["outcome"] == "PENDING_APPROVAL" else "")
              + ".", "aladdin", bid=self.spot, verdict=v, approval_rules=rules, replaced=replaced,
              api="Ledger.preview + HITL.act(cancel_next) + HITL.schedule_manual",
              ledger_audit=self._ledger_audit_since(a0))
        self._audit(what, "HITL.schedule_manual", "✓ Gate: every rule passed" + (f"; needs {', '.join(rules)}"
                                                                                  if rules else ""),
                    "reserved" + (" — waits for a second person (maker-checker)" if rules else ""), True, at,
                    borrower_id=self.spot, replaced=replaced)
        return self._publish_pending()

    # hold / STOP
    def _hold(self, on: bool) -> dict:
        r, at = self.r, self._now()
        nm = r.name(self.spot)
        a0 = self._last_audit_id()
        before = {t["instruction_id"]: t for t in r.queued()}
        if on:
            res = r.hitl.act(self.spot, "pause", PRESENTER[0], PRESENTER[1], "presenter put the borrower on hold",
                             now=at)
            self._drop_from_today(set(before))
            eff = (f"{res.get('canceled', 0)} queued touch cancelled; Aladdin answers SUPERVISOR_HOLD on every "
                   "channel until a person releases it") if not res.get("already_paused") else "already on hold"
            r.jev("hold_on", at, f"{nm} is on hold by a person: {eff}.", "aladdin", bid=self.spot,
                  api="HITL.act(pause)", cancelled=res.get("canceled", 0), ledger_audit=self._ledger_audit_since(a0))
            self._audit(f"Hold {nm}", "HITL.act(pause)", "places a MANUAL_PAUSE hold (Gate: SUPERVISOR_HOLD)", eff,
                        True, at, borrower_id=self.spot)
        else:
            res = r.hitl.act(self.spot, "resume", PRESENTER[0], PRESENTER[1], "presenter released the hold", now=at)
            still = res.get("still_blocked_by") or []
            eff = "released; outreach resumes from the next plan" + (
                f" (still blocked by {', '.join(still)})" if still else "")
            r.jev("hold_off", at, f"{nm}'s hold is released: {eff}.", "aladdin", bid=self.spot,
                  api="HITL.act(resume)", still_blocked_by=still, ledger_audit=self._ledger_audit_since(a0))
            self._audit(f"Release {nm}", "HITL.act(resume)", "releases the person's hold", eff, True, at,
                        borrower_id=self.spot)
        return res

    def _stop(self) -> dict:
        r, at = self.r, self._now()
        nm = r.name(self.spot)
        a0 = self._last_audit_id()
        queued = r.queued()
        if r.sb().opted_out:
            row = self._audit(f"STOP for {nm}", "HITL.act(dnc)", "already STOP", "no change", True, at)
            raise Refusal("ALREADY_STOPPED", f"{nm} is already opted out on every channel", 409, row)
        res = r.hitl.act(self.spot, "dnc", PRESENTER[0], PRESENTER[1],
                         "borrower asked to stop (recorded by a person on their behalf)", now=at)
        self._drop_from_today({t["instruction_id"] for t in queued})
        b = r.sb()
        b.opted_out = True
        r.opt_outs += 1
        r._churn({"event_id": f"human-stop:{self.spot}:{self.day}", "borrower_id": self.spot, "kind": "intent",
                  "channel": "whatsapp", "occurred_at": at, "intent": "STOP"}, "STOP recorded by a person")
        cons = {x["channel"]: bool(x["opted_out"]) for x in r.ledger._conn().execute(
            "SELECT channel, opted_out FROM consents WHERE borrower_id=?", (self.spot,))}
        cancelled = [{"channel": t["channel"], "time": t["scheduled_at"][11:16], "day": t["day"], "status": t["status"]}
                     for t in queued]
        grid = r.gate_grid(at.replace(hour=10, minute=0) if at.hour >= 19 or at.hour < 8 else at)
        r.jev("stop", at, f"STOP recorded for {nm} by a person: every channel is off "
              f"({', '.join(L.CH_NAME[c] for c in sorted(cons))}); {res.get('canceled', 0)} queued "
              f"touch{'es' if res.get('canceled', 0) != 1 else ''} cancelled.", "intent", bid=self.spot,
              api="HITL.act(dnc)", cancelled=cancelled, consents_opted_out=cons, checks=grid,
              ledger_audit=self._ledger_audit_since(a0))
        self._audit(f"STOP on behalf of {nm}", "HITL.act(dnc)", "STOP_OPT_OUT on every channel (locked: no one "
                    "can undo it except a per-channel opt-in)", f"{res.get('canceled', 0)} queued cancelled", True, at,
                    borrower_id=self.spot, cancelled=cancelled)
        return {"canceled": res.get("canceled", 0), "cancelled": cancelled, "consents_opted_out": cons}

    # ----- close -----
    def close(self):
        if self._closed:
            return
        self._closed = True

        def _shut():
            r = getattr(self, "r", None)
            if r is not None:
                for fn in (r.churn.close, r.ledger.close_all):
                    try:
                        fn()
                    except Exception:
                        pass
        self.worker.shutdown(_shut, timeout=20)
        if self._own:
            shutil.rmtree(self.workdir, ignore_errors=True)


STAGE_MAP = {"churn": "churn", "learner": "learner", "aladdin": "aladdin", "executor": "executor",
             "channel": "webhooks", "borrower": "intent", "intent": "intent", "cm03": "intent",
             "outcomes": "outcomes", "payments": "payments", "spine": "churn", "reports": "outcomes"}


def stage_of(e: dict) -> str:
    c, k = e["component"], e["kind"]
    if c == "hitl":
        return "intent" if k in ("case_opened", "case_resolved") else "aladdin"
    if c == "voice":
        return "executor" if k == "bot_selected" else "webhooks"
    return STAGE_MAP.get(c, "aladdin")


def compact(e: dict) -> dict:
    """Spotlight, campaign-level and human events in full; other borrowers' events as counters only
    (no names, no ids): the page needs them for the campaign strip, not their story."""
    if e.get("spot") or e.get("borrower_id") is None or e["component"] == "human":
        return e
    d = e.get("detail") or {}
    out = {"id": e["id"], "t": e["t"], "day": e["day"], "component": e["component"], "kind": e["kind"],
           "spot": False}
    if d.get("channel"):
        out["channel"] = d["channel"]
    if e["component"] == "payments" and e["kind"] == "verified":
        out["amount_paise"] = d.get("amount_paise")
    if e["component"] == "aladdin" and e["kind"] == "blocked":
        out["reason"] = d.get("reason")
    if e["component"] == "executor":
        out["cost_paise"] = d.get("cost_paise")
    return out


# ---------- one session at a time (the router's singleton) ----------
class PresenterLocked(RuntimeError):
    """/start or /close from a client without the presenter's token while a presenter session holds the lock."""

    code = "PRESENTER_LOCK"


class Manager:
    """One session at a time. Each session gets a `session_id`; a client that passes it back (X-Journey-Session)
    gets SessionClosed (HTTP 409) instead of acting on a session another client started meanwhile.

    Opt-in presenter lock: /start with a presenter token holds the session for PRESENTER_HOLD_S (renewed by each
    /start with that token): /start or /close without the token is refused (PresenterLocked). /close by the presenter
    releases it. Without a token nothing changes (anyone may start or close)."""

    PRESENTER_HOLD_S = 30 * 60

    def __init__(self, clock: Callable[[], float] = _time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self.session: Optional[OutboundJourney] = None
        self._presenter: Optional[tuple] = None  # (token, held_until)

    def presenter_locked(self, token: Optional[str] = None) -> bool:
        p = self._presenter
        return p is not None and self._clock() < p[1] and token != p[0]

    def _check_presenter(self, token: Optional[str]) -> None:
        if self.presenter_locked(token):
            mins = max(1, int((self._presenter[1] - self._clock()) // 60))
            raise PresenterLocked(f"A presenter session is running; it can be restarted only from the presenter's page "
                                  f"(lock ends in about {mins} min)")

    def start(self, presenter: Optional[str] = None, **kw) -> OutboundJourney:
        with self._lock:
            self._check_presenter(presenter)
        new = OutboundJourney(**kw)  # slow (builds the world): outside the lock
        new.session_id = uuid.uuid4().hex[:12]
        with self._lock:
            try:
                self._check_presenter(presenter)  # a presenter may have locked it while we were building
            except PresenterLocked:
                new.close()
                raise
            old, self.session = self.session, new
            if presenter:
                self._presenter = (presenter, self._clock() + self.PRESENTER_HOLD_S)
        if old is not None:
            old.close()  # an in-flight call on it finishes; later calls get SessionClosed at once (never hang)
        return new

    def get(self, session_id: Optional[str] = None) -> OutboundJourney:
        s = self.session
        if s is None:
            raise LookupError("no journey running: POST /demo/journey/outbound/api/start first")
        if session_id and session_id != getattr(s, "session_id", None):
            raise SessionClosed("another presenter started a new session; this one was replaced")
        return s

    def close(self, presenter: Optional[str] = None):
        with self._lock:
            self._check_presenter(presenter)
            old, self.session = self.session, None
            if presenter and self._presenter and self._presenter[0] == presenter:
                self._presenter = None
        if old is not None:
            old.close()


def presets_for(seed: int = DEFAULT_SEED, borrowers: int = DEFAULT_BORROWERS) -> list:
    return [dict(p) for p in PRESETS] if (seed, borrowers) == (DEFAULT_SEED, DEFAULT_BORROWERS) else []


__all__ = ["OutboundJourney", "JourneyRun", "Manager", "Refusal", "SessionClosed", "PresenterLocked", "PRESETS", "STAGES",
           "CHANNELS", "OVERRIDE_TIMES", "compact", "stage_of", "presets_for"]

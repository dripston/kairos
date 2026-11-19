"""The Kairos "crazy journey": one deterministic, SIMULATED story through every component.

    python3 scripts/run_journey.py              # full: + red-team summary + small K09 vs CN10 replay
    python3 scripts/run_journey.py --quick      # the story only (what tests/test_journey.py runs)

Everything runs on the real modules against one fresh SQLite file (K03 spine + K06 + K09 + voice +
HITL tables). Borrowers come from the K03 synthetic generator (spine.synthetic_dataset.make_row,
seeded); due dates and consents are adjusted so the story happens (see CAST below). Provider sends
are FakeProvider (SIMULATED); provider callbacks and the Sarvam call-end webhook are fixtures fed
through the production normalizers (channels.normalize.apply_callback, voice.ingest.VoiceIngester).

The story (one line per step in the output, one row per event in journey.html):
  1  Day 0 pre-due: K09 + SupervisorFSM plan a cohort (channel, slot, K22 template). Borrower A gets
     the Pre-Due voice bot (voice.registry.select_bot; variables shown with the first name masked),
     approved in the HITL queue; the Sarvam call-end fixture says PTP @ 0.9 -> PTP hold.
  2  Bounce: A's EMI bounces, the promise breaks, K09 sends a WhatsApp reminder. Borrower B replies
     STOP on SMS: every channel is suppressed from the next cycle.
  3  CM-03: A messages the WhatsApp servicing agent (OTP, balance, fake UTR -> FRAUD_SUSPECT).
     Lights up when kairos_backend.cm03 is merged; until then a "pending" line.
  4  Hardship: borrower C reports a job loss on a call (HARDSHIP @ 0.55) -> hold + HITL handoff;
     a supervisor lifts it and approves a Hardship Triage call (approve re-runs the Gate).
  5  Low-confidence STOP: borrower D's call says STOP_REQUESTED @ 0.45 -> fail closed: supervisor
     hold + review, no opt-out recorded.
  6  The LMS feed verifies A's payment on loan 1: only that EMI's queued touch is cancelled; the
     reminder for A's second loan still goes.
  7  Duplicate webhook and duplicate delivery receipt are ignored; reply windows close, outcomes are
     finalized and the K09 arm posteriors move (before/after).
  8  Day end: K26 per-segment report, learner state, cap breaches (must be 0), the red-team summary
     and the K09 vs CN10 headline from a small replay. All SIMULATED.

Optional branches light up here when they are merged: k27/auto-churn (cohort moves on live events),
cm03/whatsapp-servicing (step 3), k09/intent-stack (free-text replies; today kairos_backend.intent).
"""
from __future__ import annotations

import hashlib
import html
import importlib
import importlib.util
import json
import os
import random
import re
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Optional

from kairos_backend.borrower_context import BorrowerContextModel, ContextualLearner
from kairos_backend.channels.normalize import apply_callback
from kairos_backend.contracts import CHANNELS, SYNTHETIC_COSTS, Candidate, Mode, RecordingSink, slot_of
from kairos_backend.executor import Executor, FakeProvider, Supervisor
from kairos_backend.hitl import HITL
from kairos_backend.intent import InboundMessage, IntentClassifier
from kairos_backend.learner import BetaBernoulliLearner
from kairos_backend.ledger import Instruction, Ledger
from kairos_backend.outcomes import OutcomeFinalizer
from kairos_backend.policy import load_policy
from kairos_backend import reports as k26
from kairos_backend.supervisor import SupervisorFSM
from kairos_backend.templates import TemplateCatalog
from kairos_backend.voice import registry as voice_registry
from kairos_backend.voice.ingest import VoiceIngester
from spine.importer import insert_parsed_loan, parse_row
from spine.synthetic_dataset import make_row

PROVENANCE = "SIMULATED"
D0 = date(2026, 11, 23)                      # Monday; Sunday is a K01 quiet day
CAMPAIGN = "CMP-JOURNEY"
SLOT_TIMES = {"08-11": dtime(9, 30), "11-14": dtime(12, 30), "14-17": dtime(15, 0), "17-19": dtime(17, 30)}
TEXT_CHANNELS = ("sms", "whatsapp", "email")
OTP_CODE = "135790"                          # fixture OTP for the CM-03 conversation (SIMULATED)
FAKE_UTR = "999000111222"                    # a reference that is not in the bank feed
SUPERVISOR, OPERATOR = "sup.journey", "ops.journey"
VOICE_TEMPLATES = {                          # gate template ids for voice bots the K22 catalog does not carry
    "PRE_DUE_REMINDER": "VB_KAIROS_PREDUE_EN", "HARDSHIP_TRIAGE": "VB_KAIROS_HARDSHIP_TRIAGE_EN",
    "PTP_FOLLOW_UP": "VB_KAIROS_PTP_FU_EN"}
APP_ID = {k: v["agent_id"] for k, v in voice_registry.load_registry()["use_cases"].items()}

# Event kinds (one colour each in journey.html)
KINDS = {"plan": "K06/K09 decision", "dispatch": "dispatch", "voice": "K17 voice", "channel": "K07/K08 callback",
         "hitl": "K06 HITL", "guardrail": "guardrail", "payment": "LMS / D7", "learner": "K09 learner",
         "report": "K26 report", "cm03": "CM-03", "churn": "K27 auto-churn", "intent": "intent",
         "pending": "pending", "story": "story"}


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, dtime(hh, mm))


def _day(d: date) -> str:
    return f"D{(d - D0).days}"


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class Journey:
    def __init__(self, seed: int = 42, quick: bool = False, out_dir: Optional[Path] = None,
                 db_path: Optional[str] = None, echo: bool = True, cohort_size: int = 6,
                 replay_borrowers: int = 150, replay_days: int = 4):
        self.seed, self.quick, self.echo = seed, quick, echo
        self.cohort_size = cohort_size
        self.replay_borrowers, self.replay_days = replay_borrowers, replay_days
        self.out_dir = Path(out_dir) if out_dir else None
        self.tmp = tempfile.mkdtemp(prefix="kairos-journey-")
        self.policy = load_policy()
        self.ledger = Ledger(self.policy, db_path or os.path.join(self.tmp, "journey.db"))
        self.sink = RecordingSink()
        self.voice_sink = RecordingSink()
        self.providers = {ch: FakeProvider(ch, cost_paise=SYNTHETIC_COSTS[ch]) for ch in CHANNELS}
        self.executor = Executor(self.ledger, self.providers)
        self.hitl = HITL(self.ledger)                       # installs the approval hook on reserve()
        base = BetaBernoulliLearner(self.ledger, decay=0.7)  # K09 replay default (k09/optimize)
        self.learner = ContextualLearner(base, BorrowerContextModel(self.ledger), value_floor_rel=0.5)
        self.fsm = SupervisorFSM(self.ledger, self.executor, self.learner, self.sink, mode=Mode.PRODUCTION,
                                 provenance=PROVENANCE)
        self.finalizer = OutcomeFinalizer(self.ledger, self.learner)
        self.voice = VoiceIngester(self.ledger, finalizer=self.finalizer, sink=self.voice_sink,
                                   supervisor=Supervisor(self.ledger), hitl=self.hitl, provenance=PROVENANCE)
        self.catalog = TemplateCatalog.load(policy=self.policy)
        self.intent = IntentClassifier()                    # rules only: no LLM key in a replay
        self.events: list[dict] = []
        self.facts: dict[str, Any] = {"steps": {}}
        self.webhooks: dict[str, dict] = {}
        self.cast: dict[str, str] = {}
        self.obligations: dict[str, str] = {}
        self.band_of: dict[str, str] = {}

    # ---------- output ----------
    def ev(self, step, at: datetime, kind: str, title: str, *, borrower: Optional[str] = None,
           rule: Optional[str] = None, policy_version: Optional[str] = None, provenance: str = PROVENANCE,
           detail: Optional[dict] = None, envelope: Optional[dict] = None) -> dict:
        role = next((k for k, v in self.cast.items() if v == borrower), None) if borrower else None
        e = {"seq": len(self.events) + 1, "step": str(step), "day": _day(at.date()),
             "at": at.isoformat(timespec="minutes"), "kind": kind, "title": title, "borrower": borrower,
             "role": role, "rule": rule, "policy_version": policy_version, "provenance": provenance,
             "detail": detail or {}, "envelope": envelope}
        self.events.append(e)
        if self.echo:
            who = f" [{role}]" if role else ""
            tag = f"  ({rule})" if rule else ""
            print(f"  {e['day']} {at:%H:%M}  {KINDS[kind]:<17}{who} {title}{tag}")
        return e

    def say(self, text: str) -> None:
        if self.echo:
            print(text)

    # ---------- world ----------
    def _setup(self) -> None:
        lg = self.ledger
        rng = random.Random(self.seed)
        rows = [parse_row(make_row(i, rng, as_of=D0)) for i in range(40)]
        b1 = [r for r in rows if r["file_dpd"] is not None and 5 <= r["file_dpd"] <= 25]
        pool = [r for r in rows if r not in b1[:2]]
        full = {"sms": 1, "whatsapp": 1, "email": 1, "ivr": 1, "voicebot": 1}
        conn = lg._spine()

        def put(r, role, due: date, consents=None, borrower_id=None, phone_ok=True):
            r = dict(r)
            r["bounce_date"] = due
            r["file_dpd"] = (D0 - due).days
            r["opted_out"] = 0
            if consents is not None:
                r["consents"] = dict(consents)
            with conn:
                insert_parsed_loan(conn, r, D0, borrower_id=borrower_id)
            bid = borrower_id or r["borrower_id"]
            if role:
                self.cast[role] = bid
            return bid, r["loan_id"], f"{r['loan_id']}-OD1", r["emi_paise"]

        # A: two loans. Loan 1 due on D2 (pre-due on D0, bounces), loan 2 due on D9 (stays open).
        a, a1_loan, a1, a1_emi = put(pool[0], "A", D0 + timedelta(days=2), full)
        _, a2_loan, a2, _ = put(pool[1], None, D0 + timedelta(days=9), full, borrower_id=a)
        # B: pre-due cohort member who will STOP on SMS
        b, b_loan, bo, _ = put(pool[2], "B", D0 + timedelta(days=3), {**full, "voicebot": 0, "ivr": 0})
        # C, D: already in B1 (generator DPD), voice consent on
        c, c_loan, co, _ = put(b1[0], "C", D0 - timedelta(days=b1[0]["file_dpd"]), full)
        d, d_loan, do, _ = put(b1[1], "D", D0 - timedelta(days=b1[1]["file_dpd"]), full)
        loans = [a1_loan, a2_loan, b_loan, c_loan, d_loan]
        self.cohort = [(a, a1), (b, bo)]
        for i, r in enumerate(pool[3:3 + max(0, self.cohort_size - 2)]):
            eb, eloan, eo, _ = put(r, None, D0 + timedelta(days=2 + i % 3))
            loans.append(eloan)
            self.cohort.append((eb, eo))
        self.obligations = {"A1": a1, "A2": a2, "B": bo, "C": co, "D": do}
        self.a1_emi_paise = a1_emi
        lg.upsert_campaign(CAMPAIGN, budget_per_case_paise=5_000, start_date=D0, end_date=D0 + timedelta(days=13),
                           name="Journey (SIMULATED)")
        lg.enroll(CAMPAIGN, loans)
        synced = self.catalog.sync_to_ledger(lg)             # K22: the Gate checks the same catalog
        for tid in VOICE_TEMPLATES.values():                 # voice gate templates (registry, not catalog)
            lg.upsert_template(tid, "voicebot", "en")
        self.facts["templates_synced"] = synced.get("upserted")
        self.estimates_before = self._estimates()

    def _estimates(self) -> dict:
        return {(e["band"], e["channel"], e["slot"]): (round(e["mean"], 4), e["n_obs"])
                for e in self.learner.estimates()}

    def _band(self, obligation_id: str, at: datetime) -> Optional[str]:
        c = self.ledger._conn()
        ob = self.ledger._obligation(c, obligation_id)
        dpd = self.ledger._loan_dpd(c, ob, at.date())
        return self.policy.band_for(dpd) if dpd is not None else None

    def candidates(self, obligation_id: str, d: date, channels=TEXT_CHANNELS, template: Optional[str] = None,
                   slots=None) -> list[Candidate]:
        band = self._band(obligation_id, _at(d, 8))
        out = []
        for ch in channels:
            tid = template
            if tid is None:
                choice = self.catalog.select(ch, "en", "REMINDER", "STANDARD", band)
                if choice.decision != "SEND":
                    continue
                tid = choice.template_id
            for slot, t in SLOT_TIMES.items():
                if slots and slot not in slots:
                    continue
                out.append(Candidate(ch, slot, datetime.combine(d, t), tid, SYNTHETIC_COSTS[ch]))
        return out

    def plan(self, step, bid: str, obligation_id: str, at: datetime, cands, note: str = "") -> Any:
        res = self.fsm.plan(bid, obligation_id, CAMPAIGN, None, cands, at)
        if res.decision == "SEND":
            touch = self.ledger.touch(res.instruction_id)
            title = (f"{note}K09 picked {res.channel} {res.slot} template {res.template_id}"
                     f" -> {touch['status']}")
        else:
            title = f"{note}{res.decision} {res.reason}"
        self.ev(step, at, "plan", title, borrower=bid, rule=res.rule, policy_version=res.policy_version,
                detail={"band": res.band, "decision": res.decision, "instruction_id": res.instruction_id,
                        "model_version": res.envelope["payload"].get("model_version"),
                        "scores": [{k: s[k] for k in ("channel", "slot", "replies_per_inr")} for s in
                                   res.envelope["payload"]["scores"]]},
                envelope=res.envelope)
        return res

    def dispatch(self, step, iid: str, at: datetime, deliver: bool = True) -> Any:
        out = self.fsm.dispatch(iid, at)
        t = self.ledger.touch(iid)
        self.ev(step, at, "dispatch", f"{t['channel']} {out.outcome} ({out.reason})", borrower=t["borrower_id"],
                rule=out.rule, policy_version=out.policy_version, envelope=out.envelopes[-1],
                detail={"instruction_id": iid, "touch_status": t["status"]})
        if out.sent:
            band = self._band(t["obligation_id"], at)
            self.finalizer.register_execution(iid, t["borrower_id"], band, t["channel"], slot_of(self.policy.local(at)), at)
            if deliver and t["channel"] in TEXT_CHANNELS:
                self.callback(step, {"channel": t["channel"], "instruction_id": iid, "status": "DELIVERED",
                                     "occurred_at": at + timedelta(minutes=1), "provenance": PROVENANCE,
                                     "provider_ref": f"sim-{iid[-8:]}"})
                self.finalizer.record_reach(iid, True, at + timedelta(minutes=1))
        return out

    def callback(self, step, event: dict) -> dict:
        env = apply_callback(self.ledger, event)
        at = datetime.fromisoformat(env["occurred_at"]) if env["occurred_at"] else event["occurred_at"]
        what = env["event_type"] + (" STOP" if env["stop"] else "") + (" (duplicate, ignored)" if env["duplicate"] else "")
        self.ev(step, at.replace(tzinfo=None), "channel", f"{event['channel']} {env['status']}: {what}",
                borrower=env["borrower_id"], rule=env["ignored_reason"],
                detail={"counts_as_reply": env["counts_as_reply"], "canceled": env["canceled"],
                        "dedupe_key": env["dedupe_key"], "touch_status": env["touch_status"]}, envelope=env)
        return env

    def approve(self, step, iid: str, at: datetime, why: str) -> dict:
        pending = [r["instruction_id"] for r in self.hitl.list_approvals("supervisor", now=at)]
        r = self.hitl.approve(iid, SUPERVISOR, "supervisor", why, now=at)
        g = r.get("gate", {})
        t = self.ledger.touch(iid)
        self.ev(step, at, "hitl", f"supervisor {r['outcome']} {t['channel']} touch; Gate re-run said "
                                  f"{g.get('decision')} {g.get('reason')}", borrower=t["borrower_id"],
                rule=g.get("rule"), policy_version=g.get("policy_version"),
                detail={"queue_before": len(pending), "instruction_id": iid, "status": r["status"]})
        return r

    def select_bot(self, step, bid: str, obligation_id: str, at: datetime, touch_id: str = "",
                   human_approved: bool = False) -> dict:
        borrower, obligation, ctx = voice_registry.inputs_from_ledger(self.ledger, bid, at, obligation_id)
        ctx.update(touch_id=touch_id, human_approved=human_approved)
        sel = voice_registry.select_bot(borrower, obligation, ctx, self.voice.registry, self.voice.thresholds)
        shown = {k: ("<first name, masked>" if k == "user_name" and v else v) for k, v in sel["variables"].items()}
        self.ev(step, at, "voice", f"select_bot -> {sel['use_case']} ({sel['voice']}, {sel['language']})",
                borrower=bid, rule=sel["reasons"].get("use_case"), policy_version=sel["thresholds_version"],
                detail={"agent_id": sel["agent_id"], "variables": shown, "blocked_variables": sel["blocked_variables"],
                        "requires_human_approval": sel["requires_human_approval"],
                        "registry_version": sel["registry_version"]})
        sel["variables_shown"] = shown
        return sel

    def webhook(self, step, call_id: str, app: str, touch_id: str, disposition: str, conf: float, ended: datetime,
                **variables) -> dict:
        payload = {"call_id": call_id, "app_id": APP_ID[app], "status": "completed", "duration_seconds": 95,
                   "ended_at": ended.isoformat(), "variables": {"touch_id": touch_id, "disposition": disposition,
                                                                "disposition_confidence": conf, **variables}}
        self.webhooks[call_id] = payload
        r = self.voice.ingest(payload)
        if r["status"] == "DUPLICATE":
            self.ev(step, ended, "voice", f"Sarvam call-end {call_id} again -> DUPLICATE, ignored (first: "
                                          f"{r['first']['effective_disposition']} {r['first']['action']})",
                    rule="IDEMPOTENCY", policy_version=self.voice.version, detail={"first": r["first"]})
            return r
        env = r.get("envelope") or {}
        p = env.get("payload", {})
        self.ev(step, ended, "voice", f"Sarvam call-end: {disposition} @ {conf} -> {p.get('effective_disposition')} "
                                      f"action {p.get('action')}", borrower=env.get("borrower_id"),
                rule=p.get("rule"), policy_version=p.get("policy_version") or self.voice.version,
                provenance=env.get("provenance", PROVENANCE),
                detail={k: p.get(k) for k in ("accepted", "effects", "meaningful_reply", "summary") if k in p},
                envelope=env)
        return r

    def gate_all_channels(self, step, bid: str, obligation_id: str, at: datetime, label: str) -> dict:
        out = {}
        for ch in CHANNELS:
            tid = next((t for t in ("SMS_DUE_EN_01", "WA_PAYLINK_EN_01", "EM_DUE_EN_01", "IVR_DUE_EN_01",
                                    "VB_REMIND_EN_SOFT_01") if t.split("_")[0] == {"sms": "SMS", "whatsapp": "WA",
                                                                                   "email": "EM", "ivr": "IVR",
                                                                                   "voicebot": "VB"}[ch]), None)
            d = self.ledger.preview(Instruction(bid, obligation_id, CAMPAIGN, ch, tid, at, SYNTHETIC_COSTS[ch]))
            out[ch] = f"{d.decision} {d.reason}"
        rules = sorted(set(out.values()))
        self.ev(step, at, "guardrail", f"{label}: " + ", ".join(f"{ch} {v}" for ch, v in out.items()),
                borrower=bid, rule="/".join(sorted({v.split()[1] for v in out.values()})),
                policy_version=self.policy.version, detail={"per_channel": out, "distinct": rules})
        return out

    # ---------- the story ----------
    def run(self) -> dict:
        self._setup()
        A, B, C, D = (self.cast[k] for k in "ABCD")
        self.say(f"Kairos crazy journey · seed {self.seed} · {PROVENANCE} · K01 policy {self.policy.version} · "
                 f"K22 catalog {self.catalog.version if hasattr(self.catalog, 'version') else ''}".rstrip(" ·"))
        self.say(f"Cast (K03 synthetic generator, ids only): A={A} (2 loans)  B={B}  C={C}  D={D}  "
                 f"+ {len(self.cohort) - 2} cohort borrowers")
        self.step1_pre_due()
        self.step2_stop(B)
        self.step4_hardship(C)
        self.step5_low_conf_stop(D)
        self.step2_bounce(A)
        self.step3_cm03(A)
        self.step6_payment(A)
        self.step7_duplicates_and_learning()
        self.step8_day_end()
        return self.finish()

    def step1_pre_due(self):
        lg, A = self.ledger, self.cast["A"]
        self.say("\n1 · Day 0, pre-due: K09 + SupervisorFSM plan the cohort; A gets the Pre-Due voice bot")
        lg.start_day(D0)
        plan_at = _at(D0, 8)
        sends = []
        for bid, ob in self.cohort:
            res = self.plan(1, bid, ob, plan_at, self.candidates(ob, D0))
            if res.decision == "SEND":
                sends.append(res)
        self.facts["steps"]["1"] = {"cohort": len(self.cohort), "sends": len(sends)}
        b_sms = next((r for r in sends if r.borrower_id == self.cast["B"] and r.channel == "sms"), None)
        if b_sms is None:  # K09 chose another channel for B: plan B's SMS slot explicitly (still the Gate + K09)
            b_sms = self.plan(1, self.cast["B"], self.obligations["B"], plan_at,
                              self.candidates(self.obligations["B"], D0, channels=("sms",), slots=("14-17", "17-19")),
                              note="B on SMS: ")
            if b_sms.decision == "SEND":
                sends.append(b_sms)
        self.b_sms = b_sms
        # A's pre-due courtesy call: voicebot touch through the same FSM; the HITL hook holds it for approval
        a1 = self.obligations["A1"]
        vres = self.plan(1, A, a1, plan_at + timedelta(minutes=5),
                         self.candidates(a1, D0, channels=("voicebot",), template=VOICE_TEMPLATES["PRE_DUE_REMINDER"],
                                         slots=("08-11",)), note="voice: ")
        self.facts["steps"]["1"]["voice_touch_status_after_plan"] = lg.touch(vres.instruction_id)["status"]
        self.approve(1, vres.instruction_id, _at(D0, 9), "pre-due courtesy call, script v0.2 reviewed")
        sel = self.select_bot(1, A, a1, _at(D0, 9, 25), touch_id=vres.instruction_id)
        self.facts["steps"]["1"]["bot"] = {"use_case": sel["use_case"], "variables": sel["variables_shown"],
                                          "blocked": sel["blocked_variables"]}
        self.dispatch(1, vres.instruction_id, _at(D0, 9, 30))
        ptp_date = (D0 + timedelta(days=3)).isoformat()
        r = self.webhook(1, "sv-call-A-predue", "PRE_DUE_REMINDER", vres.instruction_id, "PTP", 0.9,
                         _at(D0, 9, 34), ptp_date=ptp_date, ptp_amount_type="FULL", language_used="en",
                         call_summary="Borrower agreed to pay in full by the promised date.")
        st = lg._state(lg._conn(), A)
        self.facts["steps"]["1"]["ptp_hold_until"] = st["ptp_date"]
        self.ev(1, _at(D0, 9, 35), "guardrail", f"PTP hold on A through {st['ptp_date']} (K13; every channel waits)",
                borrower=A, rule="PTP_HOLD", policy_version=self.policy.version)
        for res in sorted(sends, key=lambda r: (r.send_at, r.borrower_id)):
            self.dispatch(1, res.instruction_id, datetime.fromisoformat(res.send_at).replace(tzinfo=None))
        self.facts["steps"]["1"]["voice_status"] = r.get("status")

    def step2_stop(self, B):
        self.say("\n2b · Borrower B replies STOP on SMS: all channels suppressed from the next cycle")
        res = self.b_sms
        if res is None or res.decision != "SEND":
            self.ev("2", _at(D0, 18), "pending", "B had no SMS touch to reply to", borrower=B)
            return
        sent_at = datetime.fromisoformat(res.send_at).replace(tzinfo=None)
        env = self.callback("2", {"channel": "sms", "instruction_id": res.instruction_id, "status": "REPLY",
                                  "text": "STOP", "event_id": "sms-in-B-1", "provenance": PROVENANCE,
                                  "occurred_at": sent_at + timedelta(minutes=40)})
        d1 = D0 + timedelta(days=1)
        self.ledger.start_day(d1)
        per = self.gate_all_channels("2", B, self.obligations["B"], _at(d1, 10), "next cycle, every channel")
        plan = self.plan("2", B, self.obligations["B"], _at(d1, 8), self.candidates(self.obligations["B"], d1))
        self.facts["steps"]["2_stop"] = {"stop_applied": env["stop"], "channels": per, "plan": plan.decision,
                                         "plan_rule": plan.rule}

    def _voice_call(self, step, who: str, d: date, hh: int, app: str, template: str, approve_at: datetime):
        bid, ob = self.cast[who], self.obligations[who]
        res = self.plan(step, bid, ob, _at(d, 8, 10), self.candidates(ob, d, channels=("voicebot",), template=template,
                                                                    slots=(slot_of(_at(d, hh)),)), note="voice: ")
        if res.decision != "SEND":
            return None, None
        self.approve(step, res.instruction_id, approve_at, "routine B1 reminder call")
        sel = self.select_bot(step, bid, ob, _at(d, hh, 25), touch_id=res.instruction_id)
        self.dispatch(step, res.instruction_id, _at(d, hh, 30))
        return res, sel

    def step4_hardship(self, C):
        d1, d2 = D0 + timedelta(days=1), D0 + timedelta(days=2)
        self.say("\n4 · Hardship: C reports a job loss on a call (HARDSHIP @ 0.55) -> hold + HITL handoff; "
                 "a supervisor approves a Hardship Triage call")
        res, _ = self._voice_call(4, "C", d1, 10, "EMI_REMINDER_EARLY", "VB_REMIND_EN_SOFT_01", _at(d1, 9))
        if res is None:
            self.ev(4, _at(d1, 9), "pending", "no voice touch for C", borrower=C)
            return
        text = "I lost my job last month and cannot pay right now"
        it = self.intent.classify(InboundMessage(C, "voicebot", _at(d1, 10, 33), text=text))
        self.ev(4, _at(d1, 10, 33), "intent", f"free text -> {it.intent} ({it.source}, {it.confidence:.2f}): {it.summary}",
                borrower=C, rule=it.k13_event, detail={"ambiguous": it.ambiguous})
        self.webhook(4, "sv-call-C-hardship", "EMI_REMINDER_EARLY", res.instruction_id, "HARDSHIP", 0.55,
                     _at(d1, 10, 34), hardship_type="JOB_LOSS", needs_human=True, language_used="en",
                     call_summary="Borrower reports a job loss and asks for time.")
        st = self.ledger._state(self.ledger._conn(), C)
        cases = self.hitl.list_cases("supervisor", borrower_id=C, now=_at(d1, 11))
        self.ev(4, _at(d1, 11), "hitl", f"case queue: {', '.join(c['type'] + '@' + c['queue'] for c in cases)}; "
                                        f"hardship hold until {st['hardship_until']}", borrower=C,
                rule="HARDSHIP_HOLD", policy_version=self.policy.version)
        held = self.gate_all_channels(4, C, self.obligations["C"], _at(d1, 15), "while on hardship hold")
        # D2: the supervisor confirms hardship, lifts the hold, the operator schedules a triage call,
        # the supervisor approves it (maker-checker) and the Gate runs again at approval
        self.ledger.start_day(d2)
        hard = next((c for c in cases if c["type"] == "HARDSHIP"), cases[0] if cases else None)
        lifted = None
        if hard:
            lifted = self.hitl.resolve_case(hard["ticket_id"], SUPERVISOR, "supervisor", "hardship confirmed on call-back",
                                            "HARDSHIP_CONFIRMED", lift_hold=True, now=_at(d2, 9, 30))
            self.ev(4, _at(d2, 9, 30), "hitl", f"supervisor resolved {hard['type']} case, hold lifted: {lifted['hold_lifted']}",
                    borrower=C, rule="HARDSHIP_CONFIRMED")
        sched = self.hitl.schedule_manual(C, "voicebot", VOICE_TEMPLATES["HARDSHIP_TRIAGE"], _at(d2, 11, 30), OPERATOR,
                                          "operator", "hardship triage call (no collection ask)",
                                          SYNTHETIC_COSTS["voicebot"], campaign_id=CAMPAIGN,
                                          obligation_id=self.obligations["C"], now=_at(d2, 9, 40))
        one = sched["results"][0] if "results" in sched else sched
        iid = one.get("instruction_id")
        self.ev(4, _at(d2, 9, 40), "hitl", f"operator scheduled Hardship Triage call -> {one.get('status')} "
                                           f"rules {one.get('approval_rules') or one.get('rules')}", borrower=C,
                rule=(one.get("decision") or {}).get("rule") if isinstance(one.get("decision"), dict) else None,
                detail={k: v for k, v in one.items() if k in ("status", "outcome", "approval_rules", "reason")})
        sel = self.select_bot(4, C, self.obligations["C"], _at(d2, 9, 45), touch_id=iid or "", human_approved=True)
        ap = self.approve(4, iid, _at(d2, 10), "triage only: listen, offer options, no amounts") if iid else {}
        self.facts["steps"]["4"] = {"intent": it.intent, "hardship_hold": bool(st["hardship_until"]),
                                    "cases": [c["type"] for c in cases], "held_channels": held,
                                    "hold_lifted": (lifted or {}).get("hold_lifted"), "triage_bot": sel["use_case"],
                                    "approval": ap.get("outcome"), "gate_at_approval": ap.get("gate", {}).get("reason")}
        if iid and ap.get("outcome") == "APPROVED":
            self.dispatch(4, iid, _at(d2, 11, 30))

    def step5_low_conf_stop(self, D):
        d1 = D0 + timedelta(days=1)
        self.say("\n5 · Low-confidence STOP: D's call says STOP_REQUESTED @ 0.45 -> fail closed (hold + review)")
        res, _ = self._voice_call(5, "D", d1, 12, "EMI_REMINDER_EARLY", "VB_REMIND_EN_SOFT_01", _at(d1, 9, 5))
        if res is None:
            self.ev(5, _at(d1, 9), "pending", "no voice touch for D", borrower=D)
            return
        self.webhook(5, "sv-call-D-stop", "EMI_REMINDER_EARLY", res.instruction_id, "STOP_REQUESTED", 0.45,
                     _at(d1, 12, 34), language_used="en", call_summary="Unclear; may have asked not to be called.")
        lg = self.ledger
        st = lg._state(lg._conn(), D)
        consents = {r["channel"]: r["opted_out"] for r in lg.borrower_state(D)["consents"]}
        tickets = [t["type"] for t in lg.borrower_state(D)["tickets"]]
        per = self.gate_all_channels(5, D, self.obligations["D"], _at(d1, 16), "after the unsure STOP")
        self.facts["steps"]["5"] = {"supervisor_hold": bool(st["supervisor_hold"]), "stopped": bool(st["stopped"]),
                                    "opted_out_channels": sorted(ch for ch, v in consents.items() if v),
                                    "tickets": tickets, "channels": per}

    def step2_bounce(self, A):
        self.say("\n2 · Bounce: A's EMI bounces, the promise breaks, K09 sends a WhatsApp reminder")
        a1 = self.obligations["A1"]
        d2, d3, d4 = (D0 + timedelta(days=k) for k in (2, 3, 4))
        self.ev("2", _at(d2, 20), "payment", f"NACH presentation for A's EMI returned unpaid (SIMULATED LMS feed); "
                                             f"due date {d2.isoformat()}", borrower=A, rule="EMI_BOUNCED")
        self.ledger.start_day(d3)
        p3 = self.plan("2", A, a1, _at(d3, 8), self.candidates(a1, d3))
        broken = self.ledger.start_day(d4)
        self.ev("2", _at(d4, 0, 5), "guardrail", f"promise date passed unpaid -> BROKEN_PTP for {len(broken)} borrower(s); "
                                                 f"PTP hold ends", borrower=A if A in broken else None, rule="BROKEN_PTP",
                policy_version=self.policy.version)
        self.churn_step(A, d4)
        sel = self.select_bot("2", A, a1, _at(d4, 8, 2))
        res = self.plan("2", A, a1, _at(d4, 8, 5), self.candidates(a1, d4, channels=("whatsapp",)),
                        note="WhatsApp reminder: ")
        out = self.dispatch("2", res.instruction_id, datetime.fromisoformat(res.send_at).replace(tzinfo=None)) \
            if res.decision == "SEND" else None
        self.facts["steps"]["2_bounce"] = {"d3_plan": f"{p3.decision} {p3.rule}", "broken_ptp": A in broken,
                                           "next_bot": sel["use_case"], "reminder": res.channel,
                                           "reminder_outcome": out.outcome if out else None,
                                           "dpd": self.ledger._loan_dpd(self.ledger._conn(),
                                                                        self.ledger._obligation(self.ledger._conn(), a1), d4)}
        self.a_wa = res

    def churn_step(self, A, d: date):
        """k27/auto-churn: cohort moves on live events (broken PTP -> 'broken PTP' cohort -> PTP follow-up bot)."""
        names = [n for n in ("kairos_backend.auto_churn", "kairos_backend.churn", "kairos_backend.k27")
                 if module_available(n)]
        if not names:
            self.ev("2", _at(d, 0, 6), "pending", "k27/auto-churn not merged: the broken-PTP cohort move is shown "
                                                  "by select_bot below (PTP_FOLLOW_UP), not by the churn engine",
                    borrower=A, rule="PENDING_K27")
            self.facts["auto_churn"] = "pending"
            return
        mod = importlib.import_module(names[0])
        fn = next((getattr(mod, f) for f in ("on_event", "apply_event", "reassign", "assign") if hasattr(mod, f)), None)
        if fn is None:
            self.ev("2", _at(d, 0, 6), "pending", f"{names[0]} is merged but exposes no on_event/assign: wire it here",
                    borrower=A, rule="PENDING_K27")
            self.facts["auto_churn"] = "needs-wiring"
            return
        try:
            r = fn(self.ledger, A, "BROKEN_PTP", _at(d, 0, 6))
            self.ev("2", _at(d, 0, 6), "churn", f"auto-churn: {r}", borrower=A, rule="BROKEN_PTP")
            self.facts["auto_churn"] = "ok"
        except Exception as e:  # an API mismatch must not hide the rest of the story
            self.ev("2", _at(d, 0, 6), "pending", f"auto-churn call failed ({type(e).__name__}): adapt churn_step",
                    borrower=A, rule="PENDING_K27")
            self.facts["auto_churn"] = "error"

    def step3_cm03(self, A):
        d4 = D0 + timedelta(days=4)
        self.say("\n3 · CM-03: A messages the WhatsApp servicing agent")
        if not module_available("kairos_backend.cm03.engine"):
            self.ev(3, _at(d4, 13), "pending", "cm03/whatsapp-servicing not merged yet: OTP, balance and the fake-UTR "
                                               "check light up here once it is", borrower=A, rule="PENDING_CM03")
            self.facts["steps"]["3"] = {"status": "pending", "disclosed_before_otp": False}
            return
        try:
            self._cm03(A, d4)
        except Exception as e:
            self.ev(3, _at(d4, 13), "pending", f"CM-03 engine present but the journey call failed "
                                               f"({type(e).__name__}: {str(e)[:80]}): adapt step3_cm03", borrower=A,
                    rule="CM03_API")
            self.facts["steps"]["3"] = {"status": "error", "disclosed_before_otp": False}

    def _cm03(self, A, d4):
        from kairos_backend.cm03.engine import ConversationEngine
        clock = [_at(d4, 13)]
        eng = ConversationEngine(self.ledger, clock=lambda: self.policy.local(clock[0]), otp_code_source=lambda: OTP_CODE)
        phone = self.ledger._conn().execute("SELECT phone FROM borrowers WHERE borrower_id=?", (A,)).fetchone()["phone"]
        a_loans = [r["loan_id"] for r in self.ledger._conn().execute("SELECT loan_id FROM loans WHERE borrower_id=?", (A,))]
        money = re.compile(r"(₹|rs\.?|inr)\s*[\d,]+", re.I)

        def turn(text, minutes):
            clock[0] = _at(d4, 13, minutes)
            r = eng.handle(phone, text, message_id=f"wa-in-A-{minutes}")
            joined = " ".join(r.get("replies", []))
            leak = bool(money.search(joined)) or any(l in joined for l in a_loans)
            self.ev(3, clock[0], "cm03", f"'{text if text != OTP_CODE else '<otp>'}' -> state {r.get('state')}, "
                                         f"verified {r.get('verified')}, tools {r.get('tool_calls')}",
                    borrower=A, rule=r.get("handoff_reason"),
                    detail={"reply_keys": r.get("reply_keys"), "discloses_account_data": leak,
                            "fraud_suspect": r.get("fraud_suspect")})
            return r, leak

        r1, leak1 = turn("Hi, what is my outstanding balance?", 0)
        r2, _ = turn(OTP_CODE, 1)
        r3, leak3 = turn("balance please", 2)
        r4, _ = turn(f"I already paid yesterday, UTR {FAKE_UTR}", 3)
        self.facts["steps"]["3"] = {"status": "ok", "disclosed_before_otp": leak1 and not r1.get("verified"),
                                    "verified": bool(r2.get("verified")), "balance_given": leak3,
                                    "fraud_suspect": bool(r4.get("fraud_suspect")), "handoff": r4.get("handoff_reason")}

    def step6_payment(self, A):
        lg = self.ledger
        d5 = D0 + timedelta(days=5)
        self.say("\n6 · Payment verified by the LMS feed: only that EMI's queued touch is cancelled")
        lg.start_day(d5)
        a1, a2 = self.obligations["A1"], self.obligations["A2"]
        r1 = self.plan(6, A, a1, _at(d5, 8), self.candidates(a1, d5, channels=("whatsapp",), slots=("17-19",)),
                       note="loan 1: ")
        r2 = self.plan(6, A, a2, _at(d5, 8, 1), self.candidates(a2, d5, channels=("sms",), slots=("14-17",)),
                       note="loan 2: ")
        n = lg.record_payment_verified(a1, self.a1_emi_paise, _at(d5, 10), payment_id="PAY-JOURNEY-A1",
                                       reference="SIM-UTR-A1-0001", mode="UPI")
        self.finalizer.record_payment_verified(A, _at(d5, 10))
        t1 = lg.touch(r1.instruction_id) if r1.instruction_id else None
        t2 = lg.touch(r2.instruction_id) if r2.instruction_id else None
        self.ev(6, _at(d5, 10), "payment", f"D7/LMS verified A's loan-1 EMI (UPI, SIMULATED): loan-1 touch "
                                           f"{t1['status'] if t1 else '-'}, loan-2 touch {t2['status'] if t2 else '-'}",
                borrower=A, rule="OBLIGATION_CLEARED", policy_version=self.policy.version,
                detail={"result": n if isinstance(n, (dict, int)) else str(n)})
        o2 = self.dispatch(6, r2.instruction_id, _at(d5, 15)) if t2 else None
        o1 = self.dispatch(6, r1.instruction_id, _at(d5, 17, 30)) if t1 else None
        self.facts["steps"]["6"] = {"loan1_touch": t1["status"] if t1 else None,
                                    "loan1_reason": t1["reason"] if t1 else None,
                                    "loan2_touch": t2["status"] if t2 else None,
                                    "loan2_dispatch": o2.outcome if o2 else None,
                                    "loan1_dispatch": o1.outcome if o1 else None}

    def step7_duplicates_and_learning(self):
        lg = self.ledger
        d5 = D0 + timedelta(days=5)
        self.say("\n7 · Duplicates are ignored; reply windows close and the K09 posteriors move")
        first = self.webhooks["sv-call-A-predue"]
        dup = self.voice.ingest(dict(first))
        self.ev(7, _at(d5, 18), "voice", f"Sarvam call-end sv-call-A-predue delivered again -> {dup['status']}",
                borrower=self.cast["A"], rule="IDEMPOTENCY", policy_version=self.voice.version)
        dup_cb = None
        if getattr(self, "a_wa", None) is not None and self.a_wa.decision == "SEND":
            sent_at = datetime.fromisoformat(self.a_wa.send_at).replace(tzinfo=None)
            dup_cb = self.callback(7, {"channel": "whatsapp", "instruction_id": self.a_wa.instruction_id,
                                       "status": "DELIVERED", "occurred_at": sent_at + timedelta(minutes=1),
                                       "provenance": PROVENANCE, "provider_ref": f"sim-{self.a_wa.instruction_id[-8:]}"})
        outs = self.finalizer.finalize_due(_at(d5 + timedelta(days=1), 23, 59))
        after = self._estimates()
        moved = []
        for k, (mean, n) in sorted(after.items()):
            m0, n0 = self.estimates_before[k]
            if n != n0 or abs(mean - m0) > 1e-9:
                moved.append({"band": k[0], "channel": k[1], "slot": k[2], "before": m0, "after": mean,
                              "n_before": n0, "n_after": n})
        statuses = {}
        for o in outs:
            statuses[o.status] = statuses.get(o.status, 0) + 1
        self.ev(7, _at(d5, 23, 59), "learner", f"{len(outs)} outcomes finalized {statuses}; {len(moved)} arm "
                                                f"posteriors moved", rule="REPLY_WINDOW_24H",
                detail={"moved": moved[:12]})
        for m in moved[:6]:
            self.say(f"      {m['band']:<7} {m['channel']:<9} {m['slot']}  mean {m['before']:.4f} -> {m['after']:.4f}"
                     f"  (n {m['n_before']} -> {m['n_after']})")
        self.facts["steps"]["7"] = {"duplicate_webhook": dup["status"],
                                    "duplicate_callback": bool(dup_cb and dup_cb["duplicate"]),
                                    "finalized": statuses, "moved_arms": len(moved), "moved": moved}

    def step8_day_end(self):
        lg = self.ledger
        d5 = D0 + timedelta(days=5)
        self.say("\n8 · Day end (SIMULATED): K26 per-segment report, learner state, cap breaches, red team, K09 vs CN10")
        days = []
        for k in range(6):
            d = D0 + timedelta(days=k)
            rep = k26.daily_segment_report(lg, CAMPAIGN, d)
            t = rep["totals"]
            days.append({"day": _day(d), "date": d.isoformat(), **{x: t.get(x) for x in
                         ("attempts", "reach", "meaningful_replies", "spend_committed_paise", "cap_breaches")}})
            self.ev(8, _at(d, 23, 58), "report", f"K26 {d.isoformat()}: attempts {t.get('attempts')}, reach "
                                                 f"{t.get('reach')}, replies {t.get('meaningful_replies')}, "
                                                 f"cap breaches {t.get('cap_breaches')}",
                    rule="daily_segment", policy_version=rep["policy_version"], provenance=rep["provenance"],
                    detail={"segments": [{k2: s.get(k2) for k2 in ("band", "channel", "attempts", "meaningful_replies")}
                                         for s in rep["segments"]][:8]})
        breaches = lg.cap_breaches()
        mv = self.learner.model_version
        self.ev(8, _at(d5, 23, 59), "learner", f"learner {mv}, {self.learner.n_updates} updates; K01 cap breaches "
                                               f"{len(breaches)}", rule="CAP_AUDIT", policy_version=self.policy.version)
        self.facts["steps"]["8"] = {"days": days, "cap_breaches": len(breaches), "learner": mv,
                                    "learner_updates": self.learner.n_updates}
        if self.quick:
            self.ev(8, _at(d5, 23, 59), "pending", "red-team suite and K09 vs CN10 replay skipped (--quick)", rule="QUICK")
            return
        from redteam import load_attacks, redteam_policy, run_all
        outcomes = run_all(load_attacks([]).select([], [], []), redteam_policy(), 42)
        tot = {}
        for o in outcomes:
            tot[o.status] = tot.get(o.status, 0) + 1
        line = (f"red team: {len(outcomes)} attacks, {tot.get('PASS', 0)} PASS, {tot.get('FAIL', 0)} FAIL "
                f"(known open findings), {tot.get('ERROR', 0)} ERROR")
        self.ev(8, _at(d5, 23, 59), "guardrail", line, rule="REDTEAM")
        from replay.k09 import run_comparison
        rep = run_comparison(self.replay_borrowers, self.replay_days, self.seed, drift_day=None,
                             workdir=os.path.join(self.tmp, "replay"))
        b, k = rep["baseline"], rep["kairos"]
        lift = (k["replies_per_1000_inr"] / b["replies_per_1000_inr"] - 1) * 100 if b["replies_per_1000_inr"] else 0.0
        head = (f"K09 vs CN10 ({self.replay_borrowers} borrowers x {self.replay_days} days, seed {self.seed}): "
                f"replies/Rs.1000 {b['replies_per_1000_inr']} vs {k['replies_per_1000_inr']} ({lift:+.1f}%), "
                f"cap breaches {b['cap_breaches_k01']} / {k['cap_breaches_k01']}")
        self.ev(8, _at(d5, 23, 59), "report", head, rule="K09_REPLAY", provenance=rep["provenance"].split(" ")[0])
        self.facts["redteam"] = tot
        self.facts["replay"] = {"cn10": b["replies_per_1000_inr"], "k09": k["replies_per_1000_inr"],
                                "lift_pct": round(lift, 1),
                                "breaches": [b["cap_breaches_k01"], k["cap_breaches_k01"]]}

    # ---------- results ----------
    def finish(self) -> dict:
        hashed = [{k: e[k] for k in ("step", "day", "at", "kind", "title", "borrower", "rule", "policy_version",
                                     "provenance")} for e in self.events if e["rule"] not in ("REDTEAM", "K09_REPLAY")]
        self.facts["journey_hash"] = _h(hashed)[:16]
        self.facts["events"] = len(self.events)
        self.facts["cast"] = dict(self.cast)
        self.facts["provenance"] = PROVENANCE
        self.facts["policy_version"] = self.policy.version
        self.facts["crm_envelopes"] = len(self.sink.envelopes) + len(self.voice_sink.envelopes)
        if self.out_dir:
            self.write(self.out_dir)
        self.ledger.close()
        self.say(f"\njourney hash {self.facts['journey_hash']} · {len(self.events)} events · "
                 f"{self.facts['crm_envelopes']} CRM envelopes · cap breaches {self.facts['steps']['8']['cap_breaches']}"
                 f" · {PROVENANCE}")
        return self.facts

    def write(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        timeline = {"provenance": PROVENANCE, "seed": self.seed, "policy_version": self.policy.version,
                    "journey_hash": self.facts["journey_hash"], "cast": self.cast,
                    "events": self.events, "crm_envelopes": self.sink.envelopes + self.voice_sink.envelopes}
        (out / "timeline.json").write_text(json.dumps(timeline, indent=1, default=str), encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(self.facts, indent=1, default=str), encoding="utf-8")
        (out / "journey.html").write_text(render_html(self.events, self.facts), encoding="utf-8")
        self.say(f"wrote {out / 'timeline.json'}, {out / 'summary.json'}, {out / 'journey.html'}")


COLOURS = {"plan": "#2f6fdf", "dispatch": "#5b6b7f", "voice": "#8a4fd6", "channel": "#14899a", "hitl": "#c26a00",
           "guardrail": "#c4314b", "payment": "#1f8a4c", "learner": "#7a6a00", "report": "#3d5a80", "cm03": "#b0457a",
           "churn": "#6b4f2a", "intent": "#5c7f2e", "pending": "#8a8a8a", "story": "#444"}


def render_html(events: list, facts: dict) -> str:
    esc = html.escape
    rows = []
    for e in events:
        c = COLOURS.get(e["kind"], "#444")
        badges = [f'<span class="b prov">{esc(e["provenance"] or "")}</span>']
        if e["policy_version"]:
            badges.append(f'<span class="b">policy {esc(str(e["policy_version"]))}</span>')
        if e["rule"]:
            badges.append(f'<span class="b rule">{esc(str(e["rule"]))}</span>')
        who = f'{esc(e["role"])} · {esc(e["borrower"])}' if e["role"] else esc(e["borrower"] or "")
        det = esc(json.dumps(e["detail"], default=str)) if e["detail"] else ""
        rows.append(f'<tr style="--k:{c}"><td class="t">{esc(e["day"])}<br>{esc(e["at"][11:])}</td>'
                    f'<td class="s">{esc(e["step"])}</td><td><span class="kind">{esc(KINDS.get(e["kind"], e["kind"]))}'
                    f'</span></td><td class="w">{who}</td><td><div class="title">{esc(e["title"])}</div>'
                    f'<div class="badges">{"".join(badges)}</div>'
                    + (f'<details><summary>detail</summary><code>{det}</code></details>' if det else "")
                    + "</td></tr>")
    legend = "".join(f'<span class="kind" style="--k:{COLOURS[k]}">{esc(v)}</span>' for k, v in KINDS.items())
    s8 = facts.get("steps", {}).get("8", {})
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Kairos journey</title>
<style>
body{{font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:16px;background:#fafafa;color:#1d1d1f}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#555;margin-bottom:12px}}
.sim{{display:inline-block;background:#fff3cd;border:1px solid #e0c060;border-radius:4px;padding:1px 6px;font-weight:600}}
table{{border-collapse:collapse;width:100%;background:#fff}} td{{border-bottom:1px solid #eee;padding:6px 8px;vertical-align:top}}
tr{{border-left:4px solid var(--k)}} td.t{{white-space:nowrap;color:#555;font-variant-numeric:tabular-nums}}
td.s{{font-weight:600;text-align:center}} td.w{{white-space:nowrap;color:#333}}
.kind{{display:inline-block;color:#fff;background:var(--k);border-radius:3px;padding:1px 6px;font-size:12px;margin:0 4px 4px 0;white-space:nowrap}}
.b{{display:inline-block;border:1px solid #ccc;border-radius:10px;padding:0 6px;font-size:11px;margin:3px 4px 0 0;color:#333}}
.b.prov{{background:#fff3cd;border-color:#e0c060}} .b.rule{{background:#eef3ff;border-color:#9bb3e8}}
code{{font-size:11px;white-space:pre-wrap;word-break:break-word;color:#444}} summary{{font-size:11px;color:#777;cursor:pointer}}
.legend{{margin:8px 0 12px}} @media (max-width:700px){{td.w{{display:none}}}}
</style></head><body>
<h1>Kairos crazy journey</h1>
<div class="sub"><span class="sim">SIMULATED</span> synthetic borrowers (K03 generator, ids only), fixture callbacks, DEV simulator.
Seed {esc(str(facts.get('seed', '')))} · K01 policy {esc(str(facts.get('policy_version')))} · journey hash <code>{esc(str(facts.get('journey_hash')))}</code>
· cap breaches {esc(str(s8.get('cap_breaches')))} · {len(events)} events</div>
<div class="legend">{legend}</div>
<table><tbody>{''.join(rows)}</tbody></table></body></html>"""


def run(seed: int = 42, quick: bool = False, out_dir: Optional[str] = None, echo: bool = True, **kw) -> dict:
    j = Journey(seed=seed, quick=quick, out_dir=Path(out_dir) if out_dir else None, echo=echo, **kw)
    facts = j.run()
    facts["seed"] = seed
    return facts

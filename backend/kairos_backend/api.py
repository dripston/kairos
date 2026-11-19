"""HTTP API for K06 on the K03 spine. Run:  uvicorn kairos_backend.api:app --reload  (from backend/)

Environment:
  KAIROS_POLICY_DIR  policy folder (default: <repo>/policy)
  KAIROS_DB          SQLite file shared by the K03 spine and K06 (default: backend/kairos.db)
  KAIROS_IMPORT_DIR  the only directory POST /spine/import reads from (default: backend/data/imports)

Money is integer paise (100 paise = ₹1), as in the K03 spine. Every *_paise input is a whole number in
[0, MAX_PAISE] (10^12); anything else is a 422 (RT-F01). The Ledger enforces the same bound.

Human-in-the-loop routes live under /hitl (see kairos_backend/hitl.py). The caller's role comes from the
X-Kairos-Role header (viewer | operator | supervisor | policy_owner; default viewer). THAT IS NOT
AUTHENTICATION — it is a demo switch. Put real auth (SSO + server-side roles) in front before any real use.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

from .exceptions import ExceptionEvent
from .executor import Executor, FakeProvider, Supervisor
from .gate import Facts, evaluate
from .contracts import RecordingSink
from .hitl import HITL, HitlError
from .learner import BetaBernoulliLearner
from .ledger import CONFLICT_INSTRUCTION_ID, MAX_PAISE, Instruction, Ledger, PaymentError
from .outcomes import OutcomeFinalizer
from .policy import load_policy
from . import reports
from .voice import registry as voice_registry
from .voice.ingest import VoiceIngester

BACKEND_DIR = Path(__file__).resolve().parents[1]

policy = load_policy()
ledger = Ledger(policy, os.environ.get("KAIROS_DB", BACKEND_DIR / "kairos.db"))
supervisor = Supervisor(ledger)
hitl = HITL(ledger)  # installs the approval hook: IVR/voicebot, STANDARD at high DPD, manual sends -> PENDING_APPROVAL
# Real adapters (K07 WhatsApp, K08 SMS, K17 IVR) replace these; until then every send is SIMULATED.
executor = Executor(ledger, {ch: FakeProvider(ch) for ch in policy.raw["channels"]})
# K17/K18 voice engine. Envelopes are SIMULATED unless KAIROS_VOICE_PROVENANCE says otherwise (never overclaim).
# Handoffs go through the K06 HITL module (`hitl` above); without it the ingester falls back to a ledger supervisor hold.
voice_sink = RecordingSink()
voice_ingester = VoiceIngester(ledger, finalizer=OutcomeFinalizer(ledger, BetaBernoulliLearner(ledger)),
                               sink=voice_sink, supervisor=supervisor, hitl=globals().get("hitl"),
                               provenance=os.environ.get("KAIROS_VOICE_PROVENANCE", "SIMULATED"))

app = FastAPI(title="Kairos K06 · Supervisor guardrails + executor", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except HitlError as e:
        raise HTTPException(e.http, e.as_dict())
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except PaymentError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except sqlite3.IntegrityError:  # backstop: a constraint the models did not catch is the caller's input (RT-F03)
        raise HTTPException(422, "rejected by a data constraint")


# ---------- models ----------
def _known_channel(ch: str) -> str:
    if ch not in policy.raw["channels"]:
        raise ValueError(f"channel must be one of {policy.raw['channels']}")
    return ch


Channel = Annotated[str, AfterValidator(_known_channel)]  # K01 policy channels only; else 422 (RT-F03)

class CampaignIn(BaseModel):
    id: str
    name: str = ""
    budget_per_case_paise: int = Field(gt=0, le=MAX_PAISE)
    start_date: date
    end_date: date
    target_amount_pct: Optional[float] = None
    target_count_pct: Optional[float] = None


class EnrollIn(BaseModel):
    loan_ids: list[str]
    as_of: Optional[date] = None


class DatasetIn(BaseModel):
    csv_path: str
    as_of: Optional[date] = None


class TemplateIn(BaseModel):
    id: str
    channel: str
    lang: str
    approved: bool = True


class InstructionIn(BaseModel):
    borrower_id: str
    obligation_id: str
    campaign_id: str
    channel: Channel
    template_id: Optional[str]
    at: datetime
    est_cost_paise: int = Field(ge=0, le=MAX_PAISE)
    purpose: Optional[str] = None
    instruction_id: Optional[str] = None
    version: int = 1

    def to(self) -> Instruction:
        return Instruction(**self.model_dump())


class ProviderStatusIn(BaseModel):
    status: str
    provider_ref: Optional[str] = None
    reason: Optional[str] = None


class BorrowerEventIn(BaseModel):
    borrower_id: str
    at: datetime
    channel: Optional[Channel] = None
    details: str = ""


class CallIn(BaseModel):
    borrower_id: str
    at: datetime
    answered_seconds: float
    channel: Channel = "ivr"


class PaymentIn(BaseModel):
    obligation_id: str
    amount_paise: int = Field(gt=0, le=MAX_PAISE)
    at: datetime
    payment_id: str
    reference: str
    mode: str = "upi"


class ReversalIn(BaseModel):
    payment_id: str
    at: datetime
    reason: str


class OptInIn(BaseModel):
    borrower_id: str
    channel: Channel
    consent_version: str
    at: datetime


class ExceptionIn(BaseModel):
    borrower_id: str
    obligation_id: Optional[str] = None
    at: datetime
    event: str
    band: str
    in_window: bool = True
    rebuttals_used: int = 0
    date_offset_days: Optional[int] = None
    broken_ptp_count: int = 0
    paid_now: bool = False
    callback_attempts: int = 0
    callback_answered: bool = False
    summary_line_1: str = ""
    summary_line_2: str = ""
    evidence_ids: list[str] = []


class PauseIn(BaseModel):
    paused: bool
    reason: str = ""
    actor: str = "human:operator"


class CampaignStatusIn(BaseModel):
    status: str  # RUNNING | PAUSED | STOPPED
    actor: str = "human:operator"


class LenderIn(BaseModel):
    lender_id: Optional[str]


class VoiceSelectIn(BaseModel):
    borrower_id: str
    at: Optional[datetime] = None
    obligation_id: Optional[str] = None
    touch_id: Optional[str] = None
    lender_name: Optional[str] = None
    human_approved: bool = False          # HARDSHIP_TRIAGE needs a person's approval
    callback_reason: Optional[str] = None


class GateFactsIn(BaseModel):
    """gate.Facts over HTTP: numbers and flags must be JSON numbers / booleans ("12" is a 422, RT-F03)."""
    model_config = ConfigDict(extra="forbid")
    at: datetime
    channel: Channel
    dpd: Optional[StrictInt]
    dpd_age_hours: StrictFloat = 0.0
    consent: StrictBool = True
    opted_out: StrictBool = False
    hardship_until: Optional[datetime] = None
    hardship_supervisor: StrictBool = False
    callback_pending: StrictBool = False
    supervisor_hold: StrictBool = False
    ptp_date: Optional[date] = None
    obligation_cleared: StrictBool = False
    stop_for_day: StrictBool = False
    sends_today_channel: StrictInt = Field(0, ge=0)
    sends_today_all: StrictInt = Field(0, ge=0)
    sends_week_channel: StrictInt = Field(0, ge=0)
    sends_week_all: StrictInt = Field(0, ge=0)
    mins_since_last_same_channel: Optional[StrictFloat] = None
    official_cap: Optional[StrictInt] = None
    lender_id: Optional[str] = None
    template_approved: StrictBool = True
    budget_remaining_inr: StrictFloat = float("inf")
    est_cost_inr: StrictFloat = 0.0
    campaign_target_met: StrictBool = False
    purpose: Optional[str] = None
    verified_overdue: StrictBool = True


# ---------- read ----------
@app.get("/health")
def health():
    return {"ok": True, "policy_version": policy.version, "env": "SIMULATED"}


@app.get("/policy")
def get_policy():
    return policy.summary()


@app.get("/borrowers/{borrower_id}")
def get_borrower(borrower_id: str, as_of: Optional[date] = None):
    return _call(ledger.borrower_state, borrower_id, as_of)


@app.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str):
    return _call(ledger.campaign_state, campaign_id)


@app.get("/audit")
def audit(limit: int = 200, borrower_id: Optional[str] = None):
    return ledger.audit_log(limit, borrower_id)


@app.get("/cap-breaches")
def cap_breaches():
    rows = ledger.cap_breaches()
    return {"count": len(rows), "rows": rows}


# ---------- setup ----------
def import_dir() -> Path:
    return Path(os.environ.get("KAIROS_IMPORT_DIR", BACKEND_DIR / "data" / "imports")).resolve()


# K03's rejection reasons quote the offending value; the API returns only line + column + rule (RT-F10).
_REJECTION_RULES = [
    (r"missing (.+)$", None, "required"),
    (r"mobile number must be 10 digits", "Mobile Number", "10_digits"),
    (r"bad Bounce Date", "Bounce Date", "iso_date"),
    (r"DPD, Remaining EMIs and Age must be whole numbers", "DPD / Remaining EMIs / Age", "whole_number"),
    (r"Remaining EMIs cannot be negative", "Remaining EMIs", "non_negative"),
    (r"bounce string must be", "Bounce String (last 6 months)", "six_of_0_1_NA"),
    (r"Loan ID .* appears twice", "Loan ID", "unique"),
    (r"(.+?) must be T or F", None, "T_or_F"),
    (r"(.+?) is not a number", None, "number"),
    (r"(.+?) must be more than zero", None, "positive"),
]


def _rejection(r: dict) -> dict:
    for pattern, column, rule in _REJECTION_RULES:
        m = re.match(pattern, r.get("reason", ""))
        if m:  # captured groups are K03 column names, never file values
            return {"line": r.get("line"), "column": column or m.group(1), "rule": rule}
    return {"line": r.get("line"), "column": None, "rule": "invalid"}


@app.post("/spine/import")
def spine_import(body: DatasetIn):
    """Load a PL_Dummy-format CSV into the K03 spine (K03 validation report returned).

    csv_path is resolved against KAIROS_IMPORT_DIR and must stay inside it (no absolute path elsewhere, no
    ../ or symlink out). Anything else is a 403 that does not say whether the file exists. Rejected rows
    are reported as line + column + rule, never the raw value (RT-F10)."""
    base = import_dir()
    path = (base / body.csv_path).resolve()  # an absolute csv_path replaces base, then must still be under it
    if base not in path.parents:
        raise HTTPException(403, "csv_path must name a file inside the import directory")
    if not path.is_file():
        raise HTTPException(404, "no such file in the import directory")
    try:
        report = ledger.import_dataset(path, body.as_of)
    except (OSError, UnicodeError, csv.Error, KeyError, ValueError, RuntimeError, StopIteration):
        raise HTTPException(422, "not a readable dataset CSV")
    return {**report, "file": str(path.relative_to(base)), "rejected": [_rejection(r) for r in report["rejected"]]}


@app.post("/spine/synced")
def spine_synced(at: datetime):
    """422 (and an audit row) for a stamp later than now + 5 min: it would make stale DPD look fresh (RT-F05)."""
    _call(ledger.mark_spine_synced, at, policy.now())
    return {"ok": True}


@app.put("/campaigns")
def put_campaign(c: CampaignIn):
    _call(ledger.upsert_campaign, **c.model_dump())
    return ledger.campaign_state(c.id)


@app.post("/campaigns/{campaign_id}/enroll")
def enroll(campaign_id: str, body: EnrollIn):
    return {"enrolled": _call(ledger.enroll, campaign_id, body.loan_ids, body.as_of)}


@app.post("/campaigns/{campaign_id}/status")
def campaign_status(campaign_id: str, body: CampaignStatusIn):
    if body.status not in ("RUNNING", "PAUSED", "STOPPED"):
        raise HTTPException(422, "status must be RUNNING, PAUSED or STOPPED")
    return {"canceled": _call(ledger.set_campaign_status, campaign_id, body.status, body.actor)}


@app.put("/borrowers/{borrower_id}/lender")
def set_lender(borrower_id: str, body: LenderIn):
    _call(ledger.set_lender, borrower_id, body.lender_id)
    return {"ok": True}


@app.put("/templates")
def put_template(t: TemplateIn):
    ledger.upsert_template(**t.model_dump())
    return {"ok": True}


# ---------- decide / reserve / dispatch ----------
@app.post("/gate/evaluate")
def gate_evaluate(f: GateFactsIn):
    """Stateless check with caller-supplied facts (same fields as gate.Facts). `at` is judged in IST: an
    offset-aware timestamp is converted, a naive one is taken to be IST (RT-F04)."""
    return evaluate(policy, Facts(**f.model_dump())).as_dict()


@app.post("/preview")
def preview(i: InstructionIn):
    return _call(ledger.preview, i.to()).as_dict()


@app.post("/decide")
def decide(i: InstructionIn):
    """Supervisor: approve-and-reserve. Returns SEND (reserved) / WAIT / CANCEL / HANDOFF with reason.
    409 CONFLICT_INSTRUCTION_ID (reserved=false) when instruction_id is already used for another touch."""
    out = _call(supervisor.decide, i.to())
    if out.reason == CONFLICT_INSTRUCTION_ID:
        raise HTTPException(409, out.__dict__)
    return out.__dict__


@app.post("/dispatch/{instruction_id}")
def dispatch(instruction_id: str, at: Optional[datetime] = None):
    return _call(executor.dispatch, instruction_id, at).__dict__


@app.post("/dispatch-due")
def dispatch_due(now: datetime):
    return [r.__dict__ for r in executor.dispatch_due(now)]


@app.post("/resolve-uncertain/{instruction_id}")
def resolve_uncertain(instruction_id: str):
    return {"status": executor.resolve_uncertain(instruction_id)}


@app.post("/provider-status/{instruction_id}")
def provider_status(instruction_id: str, body: ProviderStatusIn):
    return {"status": _call(ledger.record_provider_status, instruction_id, body.status, body.provider_ref, body.reason)}


@app.post("/reconcile/{instruction_id}")
def reconcile(instruction_id: str, actual_cost_paise: int = Query(ge=0, le=MAX_PAISE)):
    return {"reconciled": _call(ledger.reconcile, instruction_id, actual_cost_paise)}


# ---------- borrower events ----------
@app.post("/events/response")
def ev_response(e: BorrowerEventIn):
    return {"canceled": _call(ledger.record_response, e.borrower_id, e.at, e.channel, "reply", e.details)}


@app.post("/events/call")
def ev_call(e: CallIn):
    return {"canceled": _call(ledger.record_call, e.borrower_id, e.at, e.answered_seconds, e.channel)}


@app.post("/events/stop")
def ev_stop(e: BorrowerEventIn):
    return {"canceled": _call(ledger.record_stop, e.borrower_id, e.at, e.channel)}


@app.post("/events/opt-in")
def ev_opt_in(e: OptInIn):
    _call(ledger.opt_in, e.borrower_id, e.channel, e.consent_version, e.at)
    return {"ok": True}


@app.post("/events/payment-verified")
def ev_payment(e: PaymentIn):
    """409 when K03 rejects the claim (e.g. bank reference already used — likely an edited screenshot)."""
    return _call(ledger.record_payment_verified, e.obligation_id, e.amount_paise, e.at, e.payment_id, e.reference, e.mode)


@app.post("/events/payment-reversed")
def ev_reversed(e: ReversalIn):
    return {"reversed": _call(ledger.record_payment_reversed, e.payment_id, e.at, e.reason)}


@app.post("/events/exception")
def ev_exception(e: ExceptionIn):
    ev = ExceptionEvent(e.event, e.band, e.in_window, e.rebuttals_used, e.date_offset_days, e.broken_ptp_count,
                        e.paid_now, e.callback_attempts, e.callback_answered)
    return _call(ledger.apply_exception, e.borrower_id, ev, e.at, e.obligation_id,
                 (e.summary_line_1, e.summary_line_2), e.evidence_ids)


@app.post("/days/{day}/start")
def start_day(day: date):
    return {"broken_ptp": ledger.start_day(day)}


# ---------- human controls ----------
@app.post("/control/outbound")
def control_outbound(body: PauseIn):
    return {"paused": body.paused, "canceled": ledger.set_outbound_paused(body.paused, body.actor, body.reason)}


@app.post("/control/supervisor-clear/{borrower_id}")
def supervisor_clear(borrower_id: str):
    _call(ledger.clear_supervisor_hold, borrower_id)
    return {"ok": True}


# ---------- K17/K18 voice engine ----------
@app.post("/webhooks/sarvam/call-end")
def sarvam_call_end(payload: dict, x_kairos_webhook_secret: Optional[str] = Header(default=None)):
    """Sarvam post-call payload (call / interaction id, app_id, touch_id variable, post-call variables).
    Idempotent by call id. If SARVAM_WEBHOOK_SECRET is set, the X-Kairos-Webhook-Secret header must match."""
    secret = os.environ.get("SARVAM_WEBHOOK_SECRET")
    if secret and x_kairos_webhook_secret != secret:
        raise HTTPException(401, "bad webhook secret")
    return _call(voice_ingester.ingest, payload)


@app.get("/voice/registry")
def voice_registry_get():
    t = voice_ingester.thresholds
    return {"registry": voice_ingester.registry,
            "thresholds": {"policy_version": t["policy_version"], "status": t["status"],
                           "min_confidence_any": t["min_confidence_any"], "rules": t["rules"], "ptp": t["ptp"]}}


@app.post("/voice/select")
def voice_select(body: VoiceSelectIn):
    """Which bot / voice / language Kairos would use for this borrower now (selection only — the
    K01 Gate still decides whether a call may happen)."""
    borrower, obligation, ctx = _call(voice_registry.inputs_from_ledger, ledger, body.borrower_id, body.at,
                                      body.obligation_id)
    ctx.update({k: v for k, v in (("touch_id", body.touch_id), ("lender_name", body.lender_name),
                                  ("callback_reason", body.callback_reason)) if v})
    ctx["human_approved"] = body.human_approved
    return voice_registry.select_bot(borrower, obligation, ctx, voice_ingester.registry, voice_ingester.thresholds)


@app.get("/voice/calls/{call_id}")
def voice_call(call_id: str):
    row = voice_ingester.call(call_id)
    if row is None:
        raise HTTPException(404, f"not found: {call_id}")
    return row


# ---------- human in the loop (K06 HITL) ----------
# Role header: demo only, NOT authentication.
Role = Header("viewer", alias="X-Kairos-Role")


class HumanIn(BaseModel):
    actor: str
    reason: str = ""
    now: Optional[datetime] = None  # demo / simulation clock; default = real time


class BorrowerActionIn(HumanIn):
    action: str  # pause | resume | hold | cancel_next | handoff | assign | dnc | record_opt_in (locked ones are refused)
    until: Optional[datetime] = None
    days: Optional[float] = None
    queue: Optional[str] = None
    summary: Optional[str] = None
    agent: Optional[str] = None
    channel: Optional[str] = None
    consent_version: Optional[str] = None


class ManualIn(HumanIn):
    borrower_ids: list[str]
    channel: str
    template_id: str
    at: datetime
    est_cost_paise: int = Field(ge=0, le=MAX_PAISE)
    campaign_id: Optional[str] = None
    obligation_id: Optional[str] = None
    purpose: Optional[str] = None
    force: bool = False  # always refused: kept so an attempt is visible in the audit


class ManualPreviewIn(BaseModel):
    borrower_id: str
    channel: str
    template_id: str
    at: datetime
    est_cost_paise: int = Field(0, ge=0, le=MAX_PAISE)
    campaign_id: Optional[str] = None
    obligation_id: Optional[str] = None
    purpose: Optional[str] = None


class RescheduleIn(HumanIn):
    at: datetime


class ResolveIn(HumanIn):
    resolution_code: str
    lift_hold: bool = False


class TemplateMetaIn(HumanIn):
    intensity: str  # LOW | STANDARD
    body: Optional[str] = None


@app.get("/hitl/policy")
def hitl_policy(role: str = Role):
    """Approval policy, permission table and what this role may do (for the Control Room)."""
    return {"approval_policy": hitl.ap, "role": role,
            "permissions": sorted(p for p in hitl.ap["permissions"] if hitl.permitted(p, role))}


@app.post("/hitl/tick")
def hitl_tick(now: Optional[datetime] = None):
    """Run the HITL clocks: expire approvals past their window, release manual holds (payment re-checked)."""
    return hitl.tick(now)


@app.get("/hitl/approvals")
def hitl_approvals(status: str = "PENDING", borrower_id: Optional[str] = None, now: Optional[datetime] = None,
                   role: str = Role):
    return _call(hitl.list_approvals, role, status, borrower_id, now)


@app.post("/hitl/approvals/{instruction_id}/approve")
def hitl_approve(instruction_id: str, body: HumanIn, role: str = Role):
    return _call(hitl.approve, instruction_id, body.actor, role, body.reason, body.now)


@app.post("/hitl/approvals/{instruction_id}/reject")
def hitl_reject(instruction_id: str, body: HumanIn, role: str = Role):
    return _call(hitl.reject, instruction_id, body.actor, role, body.reason, body.now)


@app.get("/hitl/borrowers/{borrower_id}")
def hitl_borrower(borrower_id: str, role: str = Role):
    return _call(hitl.borrower_controls, borrower_id, role)


@app.post("/hitl/borrowers/{borrower_id}/actions")
def hitl_borrower_action(borrower_id: str, body: BorrowerActionIn, role: str = Role):
    params = {k: v for k, v in body.model_dump().items()
              if k in ("until", "days", "queue", "summary", "agent", "channel", "consent_version") and v is not None}
    return _call(hitl.act, borrower_id, body.action, body.actor, role, body.reason, body.now, **params)


@app.post("/hitl/manual/preview")
def hitl_manual_preview(body: ManualPreviewIn, role: str = Role):
    """Live template preview + Gate verdict + approval need, without reserving anything."""
    return _call(hitl.preview_manual, body.borrower_id, body.channel, body.template_id, body.at, role,
                 body.est_cost_paise, body.campaign_id, body.obligation_id, body.purpose)


@app.post("/hitl/manual")
def hitl_manual(body: ManualIn, role: str = Role):
    return _call(hitl.schedule_manual, body.borrower_ids, body.channel, body.template_id, body.at, body.actor, role,
                 body.reason, body.est_cost_paise, body.campaign_id, body.obligation_id, body.purpose, body.force, body.now)


@app.delete("/hitl/manual/{instruction_id}")
def hitl_manual_cancel(instruction_id: str, actor: str, reason: str = "", now: Optional[datetime] = None,
                       role: str = Role):
    return _call(hitl.cancel_scheduled, instruction_id, actor, role, reason, now)


@app.post("/hitl/manual/{instruction_id}/reschedule")
def hitl_manual_reschedule(instruction_id: str, body: RescheduleIn, role: str = Role):
    return _call(hitl.reschedule, instruction_id, body.at, body.actor, role, body.reason, body.now)


@app.get("/hitl/cases")
def hitl_cases(queue: Optional[str] = None, status: str = "OPEN", borrower_id: Optional[str] = None,
               now: Optional[datetime] = None, role: str = Role):
    return _call(hitl.list_cases, role, queue, status, borrower_id, now)


@app.post("/hitl/cases/{ticket_id}/claim")
def hitl_case_claim(ticket_id: str, body: HumanIn, role: str = Role):
    return _call(hitl.claim_case, ticket_id, body.actor, role, body.reason or "claimed", body.now)


@app.post("/hitl/cases/{ticket_id}/resolve")
def hitl_case_resolve(ticket_id: str, body: ResolveIn, role: str = Role):
    return _call(hitl.resolve_case, ticket_id, body.actor, role, body.reason, body.resolution_code, body.lift_hold, body.now)


@app.post("/hitl/cases/{ticket_id}/reopen")
def hitl_case_reopen(ticket_id: str, body: HumanIn, role: str = Role):
    return _call(hitl.reopen_case, ticket_id, body.actor, role, body.reason, body.now)


@app.put("/hitl/templates/{template_id}")
def hitl_template(template_id: str, body: TemplateMetaIn, role: str = Role):
    """Template intensity (drives the STANDARD-at-high-DPD approval rule) and body (for the live preview)."""
    return _call(hitl.register_template, template_id, body.intensity, body.body, body.actor, role, body.reason, body.now)


@app.get("/hitl/audit")
def hitl_audit(borrower_id: Optional[str] = None, actor: Optional[str] = None, type: Optional[list[str]] = Query(None),
               instruction_id: Optional[str] = None, since: Optional[datetime] = None, until: Optional[datetime] = None,
               human_only: bool = False, limit: int = Query(200, ge=1, le=5000), role: str = Role):
    """Durable audit store, newest first. Filters: borrower_id, actor, type (repeatable), instruction_id,
    since/until, human_only. Masked for the caller's role."""
    return _call(hitl.audit, role, borrower_id, actor, type, instruction_id, since, until, human_only, limit)


# ---------- K26 reports (read-only, SIMULATED) ----------
REPORT_ROUTES = {"aici": "aici", "data-dump": "data_dump", "trails": "trails", "ftd": "ftd", "hourly": "hourly"}


def _report_response(name: str, rows: list, columns: list, fmt: str, meta: dict):
    if fmt == "csv":
        return Response(reports.rows_to_csv(rows, columns), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"',
                                 "X-Kairos-Provenance": reports.PROVENANCE})
    return {"report": name, "provenance": reports.PROVENANCE, **meta, "columns": columns, "count": len(rows),
            "rows": rows}


@app.get("/reports/daily-segment")
def report_daily_segment(campaign_id: str, day: date, format: str = "json"):
    """CM-02: spend, response and learning per DPD band x channel (and risk cohort) for one day."""
    if format not in ("json", "csv"):
        raise HTTPException(422, "format must be json or csv")
    rep = _call(reports.daily_segment_report, ledger, campaign_id, day)
    if format == "json":
        return rep
    rows = reports.daily_segment_sheets(rep)["daily_segment"]
    return _report_response("daily-segment", rows, reports.DAILY_SEGMENT_COLUMNS, "csv", {})


@app.get("/reports/{name}")
def report_sheet(name: str, start: Optional[str] = None, end: Optional[str] = None, format: str = "json",
                 campaign_id: Optional[str] = None, hash_ids: bool = False):
    """Lender sheets: aici, data-dump, trails, ftd, hourly. start/end: a date (whole IST day, end
    inclusive) or a datetime ([start, end)). hash_ids=true replaces borrower ids with a keyed hash."""
    key = REPORT_ROUTES.get(name)
    if key is None:
        raise HTTPException(404, f"unknown report {name}; one of {', '.join(REPORT_ROUTES)} or daily-segment")
    if format not in ("json", "csv"):
        raise HTTPException(422, "format must be json or csv")
    rows = _call(reports.SHEETS[key], ledger, start, end, campaign_id=campaign_id, hash_ids=hash_ids)
    return _report_response(name, rows, reports.SHEET_COLUMNS[key], format,
                            {"sheet": reports.SHEET_TITLES[key], "start": start, "end": end,
                             "campaign_id": campaign_id, "hashed_ids": hash_ids})


# ---------- demo recipients (phone-demo adapters; numbers stay on this machine, always masked) ----------
class DemoRecipientIn(BaseModel):
    number: str


@app.get("/demo/recipients")
def demo_recipients():
    from .channels import demo_recipients as dr
    return {"recipients": dr.masked(), "note": "Masked. Being listed never sends by itself; the channel "
            "flag, an approved template and dry_run=False are still required."}


@app.post("/demo/recipients")
def demo_recipient_add(body: DemoRecipientIn):
    from .channels import demo_recipients as dr
    from .channels.phone_demo import DemoSafetyError, mask_number
    try:
        return {"added": mask_number(dr.add(body.number)), "recipients": dr.masked()}
    except DemoSafetyError as e:
        raise HTTPException(422, str(e))


@app.delete("/demo/recipients/{number}")
def demo_recipient_remove(number: str):
    from .channels import demo_recipients as dr
    from .channels.phone_demo import DemoSafetyError
    try:
        return {"removed": dr.remove(number), "recipients": dr.masked()}
    except DemoSafetyError as e:
        raise HTTPException(422, str(e))

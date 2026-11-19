"""HTTP API for K06 on the K03 spine. Run:  uvicorn kairos_backend.api:app --reload  (from backend/)

Environment:
  KAIROS_POLICY_DIR  policy folder (default: <repo>/policy)
  KAIROS_DB          SQLite file shared by the K03 spine and K06 (default: backend/kairos.db)

Money is integer paise (100 paise = ₹1), as in the K03 spine.

Human-in-the-loop routes live under /hitl (see kairos_backend/hitl.py). The caller's role comes from the
X-Kairos-Role header (viewer | operator | supervisor | policy_owner; default viewer). THAT IS NOT
AUTHENTICATION — it is a demo switch. Put real auth (SSO + server-side roles) in front before any real use.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .exceptions import ExceptionEvent
from .executor import Executor, FakeProvider, Supervisor
from .gate import Facts, evaluate
from .contracts import RecordingSink
from .hitl import HITL, HitlError
from .learner import BetaBernoulliLearner
from .ledger import Instruction, Ledger, PaymentError
from .outcomes import OutcomeFinalizer
from .policy import load_policy
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


# ---------- models ----------
class CampaignIn(BaseModel):
    id: str
    name: str = ""
    budget_per_case_paise: int = Field(gt=0)
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
    channel: str
    template_id: Optional[str]
    at: datetime
    est_cost_paise: int = Field(ge=0)
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
    channel: Optional[str] = None
    details: str = ""


class CallIn(BaseModel):
    borrower_id: str
    at: datetime
    answered_seconds: float
    channel: str = "ivr"


class PaymentIn(BaseModel):
    obligation_id: str
    amount_paise: int = Field(gt=0)
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
    channel: str
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
@app.post("/spine/import")
def spine_import(body: DatasetIn):
    """Load a PL_Dummy-format CSV into the K03 spine (K03 validation report returned)."""
    if not Path(body.csv_path).exists():
        raise HTTPException(404, f"file not found: {body.csv_path}")
    return ledger.import_dataset(body.csv_path, body.as_of)


@app.post("/spine/synced")
def spine_synced(at: datetime):
    ledger.mark_spine_synced(at)
    return {"ok": True}


@app.put("/campaigns")
def put_campaign(c: CampaignIn):
    ledger.upsert_campaign(**c.model_dump())
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
    ledger.set_lender(borrower_id, body.lender_id)
    return {"ok": True}


@app.put("/templates")
def put_template(t: TemplateIn):
    ledger.upsert_template(**t.model_dump())
    return {"ok": True}


# ---------- decide / reserve / dispatch ----------
@app.post("/gate/evaluate")
def gate_evaluate(f: dict):
    """Stateless check with caller-supplied facts (same fields as gate.Facts)."""
    try:
        facts = Facts(**{**f, "at": datetime.fromisoformat(f["at"])})
    except (TypeError, KeyError, ValueError) as e:
        raise HTTPException(422, str(e))
    return evaluate(policy, facts).as_dict()


@app.post("/preview")
def preview(i: InstructionIn):
    return _call(ledger.preview, i.to()).as_dict()


@app.post("/decide")
def decide(i: InstructionIn):
    """Supervisor: approve-and-reserve. Returns SEND (reserved) / WAIT / CANCEL / HANDOFF with reason."""
    return _call(supervisor.decide, i.to()).__dict__


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
def reconcile(instruction_id: str, actual_cost_paise: int):
    return {"reconciled": _call(ledger.reconcile, instruction_id, actual_cost_paise)}


# ---------- borrower events ----------
@app.post("/events/response")
def ev_response(e: BorrowerEventIn):
    return {"canceled": _call(ledger.record_response, e.borrower_id, e.at, e.channel, "reply", e.details)}


@app.post("/events/call")
def ev_call(e: CallIn):
    return {"canceled": ledger.record_call(e.borrower_id, e.at, e.answered_seconds, e.channel)}


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
    ledger.clear_supervisor_hold(borrower_id)
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
    est_cost_paise: int = Field(ge=0)
    campaign_id: Optional[str] = None
    obligation_id: Optional[str] = None
    purpose: Optional[str] = None
    force: bool = False  # always refused: kept so an attempt is visible in the audit


class ManualPreviewIn(BaseModel):
    borrower_id: str
    channel: str
    template_id: str
    at: datetime
    est_cost_paise: int = Field(0, ge=0)
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

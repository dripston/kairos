"""HTTP API for K06. Run:  uvicorn kairos_backend.api:app --reload  (from backend/)

Environment:
  KAIROS_POLICY_DIR  policy folder (default: <repo>/policy)
  KAIROS_DB          SQLite file   (default: backend/kairos.db)
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .exceptions import ExceptionEvent
from .executor import Executor, FakeProvider, Supervisor
from .gate import Facts, evaluate
from .ledger import Instruction, Ledger
from .policy import load_policy

BACKEND_DIR = Path(__file__).resolve().parents[1]

policy = load_policy()
ledger = Ledger(policy, os.environ.get("KAIROS_DB", BACKEND_DIR / "kairos.db"))
supervisor = Supervisor(ledger)
# Real adapters (K07 WhatsApp, K08 SMS, K17 IVR) replace these; until then every send is SIMULATED.
executor = Executor(ledger, {ch: FakeProvider(ch) for ch in policy.raw["channels"]})

app = FastAPI(title="Kairos K06 · Supervisor guardrails + executor", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _404(fn, *a, **k):
    try:
        return fn(*a, **k)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")


# ---------- models ----------
class CampaignIn(BaseModel):
    id: str
    name: str = ""
    budget_per_case_inr: float = Field(gt=0)
    allocated_cases: int = Field(gt=0)
    start_date: date
    end_date: date
    overdue_at_start_inr: float = 0
    target_amount_pct: Optional[float] = None
    target_count_pct: Optional[float] = None


class BorrowerIn(BaseModel):
    id: str
    dpd: Optional[int]
    dpd_updated_at: datetime
    consent: dict[str, bool]
    consent_version: dict[str, str] = {}
    lender_id: Optional[str] = None
    verified_overdue: bool = True


class ObligationIn(BaseModel):
    id: str
    borrower_id: str
    campaign_id: str
    overdue_inr: float = 0


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
    est_cost_inr: float = Field(ge=0)
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


class CallIn(BorrowerEventIn):
    answered_seconds: float


class PaymentIn(BaseModel):
    borrower_id: str
    obligation_id: str
    amount_inr: float
    at: datetime


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


# ---------- read ----------
@app.get("/health")
def health():
    return {"ok": True, "policy_version": policy.version, "env": "SIMULATED"}


@app.get("/policy")
def get_policy():
    return policy.summary()


@app.get("/borrowers/{borrower_id}")
def get_borrower(borrower_id: str):
    return _404(ledger.borrower_state, borrower_id)


@app.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str):
    return _404(ledger.campaign_state, campaign_id)


@app.get("/audit")
def audit(limit: int = 200, borrower_id: Optional[str] = None):
    return ledger.audit_log(limit, borrower_id)


@app.get("/cap-breaches")
def cap_breaches():
    rows = ledger.cap_breaches()
    return {"count": len(rows), "rows": rows}


# ---------- setup ----------
@app.put("/campaigns")
def put_campaign(c: CampaignIn):
    ledger.upsert_campaign(**c.model_dump())
    return ledger.campaign_state(c.id)


@app.post("/campaigns/{campaign_id}/status")
def campaign_status(campaign_id: str, body: CampaignStatusIn):
    if body.status not in ("RUNNING", "PAUSED", "STOPPED"):
        raise HTTPException(422, "status must be RUNNING, PAUSED or STOPPED")
    return {"canceled": _404(ledger.set_campaign_status, campaign_id, body.status, body.actor)}


@app.put("/borrowers")
def put_borrower(b: BorrowerIn):
    ledger.upsert_borrower(**b.model_dump())
    return ledger.borrower_state(b.id)


@app.put("/obligations")
def put_obligation(o: ObligationIn):
    ledger.upsert_obligation(**o.model_dump())
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
    return _404(ledger.preview, i.to()).as_dict()


@app.post("/decide")
def decide(i: InstructionIn):
    """Supervisor: approve-and-reserve. Returns SEND (reserved) / WAIT / CANCEL / HANDOFF with reason."""
    return _404(supervisor.decide, i.to()).__dict__


@app.post("/dispatch/{instruction_id}")
def dispatch(instruction_id: str, at: Optional[datetime] = None):
    return _404(executor.dispatch, instruction_id, at).__dict__


@app.post("/dispatch-due")
def dispatch_due(now: datetime):
    return [r.__dict__ for r in executor.dispatch_due(now)]


@app.post("/resolve-uncertain/{instruction_id}")
def resolve_uncertain(instruction_id: str):
    return {"status": executor.resolve_uncertain(instruction_id)}


@app.post("/provider-status/{instruction_id}")
def provider_status(instruction_id: str, body: ProviderStatusIn):
    return {"status": _404(ledger.record_provider_status, instruction_id, body.status, body.provider_ref, body.reason)}


@app.post("/reconcile/{instruction_id}")
def reconcile(instruction_id: str, actual_cost_inr: float):
    return {"reconciled": _404(ledger.reconcile, instruction_id, actual_cost_inr)}


# ---------- borrower events ----------
@app.post("/events/response")
def ev_response(e: BorrowerEventIn):
    return {"canceled": ledger.record_response(e.borrower_id, e.at, e.channel)}


@app.post("/events/call")
def ev_call(e: CallIn):
    return {"canceled": ledger.record_call(e.borrower_id, e.at, e.answered_seconds, e.channel or "ivr")}


@app.post("/events/stop")
def ev_stop(e: BorrowerEventIn):
    return {"canceled": ledger.record_stop(e.borrower_id, e.at, e.channel)}


@app.post("/events/opt-in")
def ev_opt_in(e: OptInIn):
    ledger.opt_in(e.borrower_id, e.channel, e.consent_version, e.at)
    return {"ok": True}


@app.post("/events/payment-verified")
def ev_payment(e: PaymentIn):
    return {"canceled": _404(ledger.record_payment_verified, e.borrower_id, e.obligation_id, e.amount_inr, e.at)}


@app.post("/events/exception")
def ev_exception(e: ExceptionIn):
    ev = ExceptionEvent(e.event, e.band, e.in_window, e.rebuttals_used, e.date_offset_days, e.broken_ptp_count,
                        e.paid_now, e.callback_attempts, e.callback_answered)
    try:
        return ledger.apply_exception(e.borrower_id, ev, e.at, e.obligation_id,
                                      (e.summary_line_1, e.summary_line_2), e.evidence_ids)
    except ValueError as err:
        raise HTTPException(422, str(err))


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

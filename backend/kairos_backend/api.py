"""HTTP API for K06 on the K03 spine. Run:  uvicorn kairos_backend.api:app --reload  (from backend/)

Environment:
  KAIROS_POLICY_DIR  policy folder (default: <repo>/policy)
  KAIROS_DB          SQLite file shared by the K03 spine and K06 (default: backend/kairos.db)

Money is integer paise (100 paise = ₹1), as in the K03 spine. Every *_paise input is a whole number in
[0, MAX_PAISE] (10^12); anything else is a 422 (RT-F01). The Ledger enforces the same bound.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .exceptions import ExceptionEvent
from .executor import Executor, FakeProvider, Supervisor
from .gate import Facts, evaluate
from .ledger import MAX_PAISE, Instruction, Ledger, PaymentError
from .policy import load_policy

BACKEND_DIR = Path(__file__).resolve().parents[1]

policy = load_policy()
ledger = Ledger(policy, os.environ.get("KAIROS_DB", BACKEND_DIR / "kairos.db"))
supervisor = Supervisor(ledger)
# Real adapters (K07 WhatsApp, K08 SMS, K17 IVR) replace these; until then every send is SIMULATED.
executor = Executor(ledger, {ch: FakeProvider(ch) for ch in policy.raw["channels"]})

app = FastAPI(title="Kairos K06 · Supervisor guardrails + executor", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
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
    channel: str
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
    channel: Optional[str] = None
    details: str = ""


class CallIn(BaseModel):
    borrower_id: str
    at: datetime
    answered_seconds: float
    channel: str = "ivr"


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
    ledger.set_lender(borrower_id, body.lender_id)
    return {"ok": True}


@app.put("/templates")
def put_template(t: TemplateIn):
    ledger.upsert_template(**t.model_dump())
    return {"ok": True}


# ---------- decide / reserve / dispatch ----------
@app.post("/gate/evaluate")
def gate_evaluate(f: dict):
    """Stateless check with caller-supplied facts (same fields as gate.Facts). `at` is judged in IST: an
    offset-aware timestamp is converted, a naive one is taken to be IST (RT-F04)."""
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
def reconcile(instruction_id: str, actual_cost_paise: int = Query(ge=0, le=MAX_PAISE)):
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

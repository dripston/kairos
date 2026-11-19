"""K27 HTTP routes (mounted by kairos_backend.api). Every response is SIMULATED unless fed LIVE events.

    GET  /churn/cohorts                    cohort definitions (+ versions)
    PUT  /churn/cohorts                    replace them (validated; actor + reason required; audited)
    GET  /churn/coverage?as_of=            per-cohort counts, overlaps, live orphans (UNASSIGNED)
    POST /churn/enroll                     enrol borrowers from the K03 spine for a campaign
    GET  /churn/borrowers/{id}             cohort, risk, journey + day, layer, lifecycle, today's candidates, history
    GET  /churn/borrowers/{id}/trace       step-by-step deterministic explanation
    GET  /churn/borrowers/{id}/allowed     allowed_channels(borrower, day) for K09 / RL v2
    POST /churn/borrowers/{id}/recommend-layer   RL layer recommendation -> accepted / rejected by rule
    POST /churn/events                     normalized event in -> transitions out (idempotent by event_id)
    POST /churn/tick                       daily tick
    GET  /churn/analytics                  sizes over time, flows (cohort / layer / lifecycle, per region), ...
    POST /churn/overrides, /churn/overrides/{id}/decision, /churn/manual   human hooks (maker-checker, audited)
    GET  /churn/fsm                        lifecycle states + transition table
    POST /churn/sarvam/webhook             Sarvam call-attempt payload -> events (borrower via touch_id)
    POST /churn/sarvam/gate                CN AICC auto-churn gate reply for a borrower
    GET  /churn/sarvam/export?day=         DRY-RUN upload_cohort + configure_campaign bodies
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import fsm, sarvam_export
from .engine import ChurnEngine

router = APIRouter(prefix="/churn", tags=["K27 auto-churn"])
_state: dict = {"engine": None, "ledger": None}


def configure(ledger=None, engine: Optional[ChurnEngine] = None) -> None:
    _state["ledger"] = ledger
    _state["engine"] = engine


def engine() -> ChurnEngine:
    if _state["engine"] is None:
        default = Path(__file__).resolve().parents[2] / "kairos_churn.db"
        _state["engine"] = ChurnEngine(os.environ.get("KAIROS_CHURN_DB", str(default)), ledger=_state["ledger"])
    return _state["engine"]


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


class CohortsIn(BaseModel):
    doc: dict
    reason: str


class EnrollIn(BaseModel):
    campaign_id: str
    as_of: date
    loan_ids: Optional[list[str]] = None


class TickIn(BaseModel):
    as_of: date


class RecommendIn(BaseModel):
    layer: str
    source: str = "k09-rl-v2"
    recommendation_id: Optional[str] = None
    evidence: dict = {}
    at: Optional[datetime] = None


class OverrideIn(BaseModel):
    borrower_id: str
    cohort: str
    reason: str
    until: Optional[str] = None
    entry: Optional[str] = None


class DecisionIn(BaseModel):
    approve: bool = True


class ManualIn(BaseModel):
    borrower_id: str
    channel: str
    at: datetime
    reason: str
    template_id: Optional[str] = None


class GateIn(BaseModel):
    borrower_id: str
    at: datetime
    step: str = ""


@router.get("/cohorts")
def get_cohorts():
    e = engine()
    return {"versions": e.versions(), "doc": e.cohorts.doc}


@router.put("/cohorts")
def put_cohorts(body: CohortsIn, x_kairos_actor: str = Header("human:ops")):
    return _call(engine().put_cohorts, body.doc, x_kairos_actor, body.reason)


@router.get("/coverage")
def coverage(as_of: Optional[date] = None):
    return engine().coverage(as_of or date.today())


@router.post("/enroll")
def enroll(body: EnrollIn):
    if _state["ledger"] is None:
        raise HTTPException(409, "no ledger configured")
    return {"enrolled": engine().enroll_from_spine(_state["ledger"], body.campaign_id, body.as_of, body.loan_ids)}


@router.get("/borrowers/{borrower_id}")
def borrower(borrower_id: str, as_of: Optional[date] = None):
    return _call(engine().borrower, borrower_id, as_of)


@router.get("/borrowers/{borrower_id}/trace")
def trace(borrower_id: str, as_of: Optional[date] = None):
    return _call(engine().trace, borrower_id, as_of)


@router.get("/borrowers/{borrower_id}/allowed")
def allowed(borrower_id: str, day: date):
    return _call(engine().allowed_channels, borrower_id, day)


@router.post("/borrowers/{borrower_id}/recommend-layer")
def recommend(borrower_id: str, body: RecommendIn):
    rec = {"layer": body.layer, "source": body.source, "recommendation_id": body.recommendation_id}
    return _call(engine().recommend_layer, borrower_id, rec, body.evidence, body.at)


@router.post("/events")
def events(body: dict):
    out = _call(engine().process, body)
    return out


@router.post("/tick")
def tick(body: TickIn):
    return _call(engine().tick, body.as_of)


@router.get("/analytics")
def analytics(campaign: Optional[str] = None, region: Optional[str] = None, cohort: Optional[str] = None,
              layer: Optional[str] = None):
    return engine().analytics(campaign, region, cohort, layer)


@router.post("/overrides")
def override(body: OverrideIn, x_kairos_actor: str = Header("human:ops")):
    return _call(engine().request_override, body.borrower_id, body.cohort, x_kairos_actor, body.reason, body.until,
                 body.entry)


@router.post("/overrides/{override_id}/decision")
def decide(override_id: str, body: DecisionIn, x_kairos_actor: str = Header("human:supervisor")):
    return _call(engine().approve_override, override_id, x_kairos_actor, body.approve)


@router.post("/manual")
def manual(body: ManualIn, x_kairos_actor: str = Header("human:ops")):
    return _call(engine().schedule_manual, body.borrower_id, body.channel, body.at, x_kairos_actor, body.reason,
                 body.template_id)


@router.get("/fsm")
def fsm_table():
    return {"states": list(fsm.STATES), "terminal": list(fsm.TERMINAL), "transitions": fsm.table()}


@router.post("/sarvam/webhook")
def sarvam_webhook(payload: dict):
    e = engine()

    def borrower_of(w: dict) -> Optional[str]:
        tid = w.get("touch_id") or ""
        if tid.startswith("K27-"):
            return tid[4:].rsplit("-", 3)[0]
        led = _state["ledger"]
        if led is not None and tid:
            t = led.touch(tid)
            return t["borrower_id"] if t else None
        return None
    evs = sarvam_export.webhook_to_events(payload, borrower_of)
    return {"events": [ev.as_dict() for ev in evs], "results": [_call(e.process, ev) for ev in evs]}


@router.post("/sarvam/gate")
def sarvam_gate(body: GateIn):
    return _call(sarvam_export.gate_response, engine(), body.borrower_id, body.at, body.step)


@router.get("/sarvam/export")
def sarvam_export_route(day: date):
    e = engine()
    return {"upload_cohort": sarvam_export.cohort_upload(e, day),
            "configure_campaign_example": sarvam_export.campaign_config("CN10_C4", day, "MID_BUCKET_RESOLUTION", "B2",
                                                                        e.ledger.policy if e.ledger else None)}

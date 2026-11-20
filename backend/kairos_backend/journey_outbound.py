"""Journey 1 — Outbound collections, live: `/demo/journey/outbound` (page) and `/demo/journey/outbound/api/*`.

One SIMULATED campaign on the real engines (replay/outbound_journey.py) with one spotlight borrower, driven phase
by phase by the page, and a person who takes control through the real HITL / Ledger control plane. One session
at a time, on its own temp SQLite file (never the API's DB). Nothing is sent anywhere (mock adapters, dry-run).

    GET  /demo/journey/outbound                   the presenter page (plain HTML/CSS/JS, no external assets)
    GET  /demo/journey/outbound/api/presets       the three preset borrowers (seed 9, 200 borrowers)
    POST /demo/journey/outbound/api/start         {seed, borrowers, days, spotlight} → a fresh session
    POST /demo/journey/outbound/api/step          run the next phase (plan → approval window → sends)
    GET  /demo/journey/outbound/api/state         events after ?cursor= + snapshot (card, approvals, audit)
    POST /demo/journey/outbound/api/campaign      {paused}   Ledger.set_campaign_status(PAUSED | RUNNING)
    POST /demo/journey/outbound/api/outbound      {paused}   Ledger.set_outbound_paused (kill switch)
    POST /demo/journey/outbound/api/approvals/{id}/approve|reject   HITL.approve / HITL.reject
    POST /demo/journey/outbound/api/spotlight/preview   {channel, time}  Aladdin's verdict (Ledger.preview)
    POST /demo/journey/outbound/api/spotlight/override  {mode: wait | channel, channel, time}
    POST /demo/journey/outbound/api/spotlight/hold      {on}     HITL.act(pause | resume)
    POST /demo/journey/outbound/api/spotlight/stop               HITL.act(dnc): STOP on every channel
    POST /demo/journey/outbound/api/close         end the session (temp files removed)

A refused human action (guardrail, maker-checker, outside the window) answers 409 with {"ok": false, "refusal"}
and the audit row; it is never an error page. Roles: the presenter acts as `supervisor` (demo, not auth).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from replay import outbound_journey as J

router = APIRouter(prefix="/demo/journey/outbound", tags=["Journey 1: outbound collections (SIMULATED)"])
PAGE = Path(__file__).with_name("static") / "journey_outbound.html"
manager = J.Manager()


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = Field(J.DEFAULT_SEED, ge=0, le=2**31 - 1)
    borrowers: int = Field(J.DEFAULT_BORROWERS, ge=20, le=600)
    days: int = Field(J.DEFAULT_DAYS, ge=2, le=21)
    spotlight: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_-]{1,40}$")


class PausedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool


class OnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = Field(None, max_length=200)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["sms", "whatsapp", "email", "ivr", "voicebot"]
    time: Literal["07:30", "09:30", "14:30", "17:30", "20:00"]


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["wait", "channel"]
    channel: Optional[Literal["sms", "whatsapp", "email", "ivr", "voicebot"]] = None
    time: Optional[Literal["07:30", "09:30", "14:30", "17:30", "20:00"]] = None


TOKEN = r"^[A-Za-z0-9_-]{4,64}$"
# The page sends back the session id it started (X-Journey-Session): a request for a session another client replaced
# or closed is a clear 409, never a hang and never an action on someone else's session.
SessionHeader = Header(None, alias="X-Journey-Session", pattern=r"^[0-9a-f]{12}$")
REPLACED = ("Another presenter started a new session (or closed this one). Restart?")


def _replaced(e: Exception) -> HTTPException:
    return HTTPException(409, {"code": J.SessionClosed.code, "message": REPLACED, "why": str(e)})


def _session(session_id: Optional[str] = None) -> J.OutboundJourney:
    try:
        return manager.get(session_id)
    except LookupError as e:
        raise HTTPException(409, str(e))
    except J.SessionClosed as e:
        raise _replaced(e)


def _call(fn):
    try:
        return fn()
    except J.SessionClosed as e:
        raise _replaced(e)


def _act(action: str, session_id: Optional[str] = None, **params):
    s = _session(session_id)
    try:
        out = _call(lambda: s.act(action, **params))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not out.get("ok"):
        return JSONResponse(out, status_code=out.get("http") or 409)
    return out


@router.get("", response_class=HTMLResponse)
def page():
    """The presenter page: plain HTML/CSS/JS, no build step, no external assets (offline-safe)."""
    return PAGE.read_text(encoding="utf-8")


@router.get("/api/presets")
def presets():
    return {"provenance": J.PROVENANCE, "seed": J.DEFAULT_SEED, "borrowers": J.DEFAULT_BORROWERS,
            "presets": J.presets_for(), "stages": list(J.STAGES), "override_times": list(J.OVERRIDE_TIMES)}


def _presenter(query: Optional[str], header: Optional[str]) -> Optional[str]:
    return query or header


Presenter = Query(None, pattern=TOKEN, description="opt-in presenter lock token (or X-Journey-Presenter)")
PresenterHeader = Header(None, alias="X-Journey-Presenter", pattern=TOKEN)


def _locked(e: Exception) -> HTTPException:
    return HTTPException(409, {"code": J.PresenterLocked.code, "message": str(e)})


@router.post("/api/start")
def start(body: Optional[StartIn] = None, presenter: Optional[str] = Presenter,
          x_presenter: Optional[str] = PresenterHeader):
    """A fresh SIMULATED campaign (own temp DB) with one spotlight borrower; replaces any running session, unless a
    presenter holds the opt-in lock (`?presenter=<token>` on their /start): then only that token may start or close."""
    b = body or StartIn()
    try:
        s = manager.start(presenter=_presenter(presenter, x_presenter), seed=b.seed, borrowers=b.borrowers,
                          days=b.days, spotlight=b.spotlight)
    except KeyError:
        raise HTTPException(404, "no such borrower in this campaign")
    except J.PresenterLocked as e:
        raise _locked(e)
    return {**_call(lambda: s.state(0)), "session_id": s.session_id}


@router.post("/api/step")
def step(session_id: Optional[str] = SessionHeader):
    """Run the next phase on the real engines: plan (→ approval window) or the window's close + the day's sends.
    409 SESSION_REPLACED (never a hang) when another client replaced or closed the session meanwhile."""
    s = _session(session_id)

    def run():
        out = s.step()
        st = s.state(len(s.events))
        first = len(s.events) - len(out["events"])
        return {**st, "events": [J.compact(e) for e in s.events[first:]], "cursor": len(s.events),
                "session_id": s.session_id}
    return _call(run)


@router.get("/api/state")
def state(cursor: int = Query(0, ge=0), session_id: Optional[str] = SessionHeader):
    s = _session(session_id)
    return {**_call(lambda: s.state(cursor)), "session_id": s.session_id}


@router.post("/api/campaign")
def campaign(body: PausedIn, session_id: Optional[str] = SessionHeader):
    return _act("campaign", session_id, paused=body.paused)


@router.post("/api/outbound")
def outbound(body: PausedIn, session_id: Optional[str] = SessionHeader):
    return _act("outbound", session_id, paused=body.paused)


@router.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, body: Optional[DecideIn] = None, session_id: Optional[str] = SessionHeader):
    return _act("approve", session_id, approval_id=approval_id, reason=body.reason if body else None)


@router.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: str, body: Optional[DecideIn] = None, session_id: Optional[str] = SessionHeader):
    return _act("reject", session_id, approval_id=approval_id, reason=body.reason if body else None)


@router.post("/api/spotlight/preview")
def preview(body: PreviewIn, session_id: Optional[str] = SessionHeader):
    return _act("preview", session_id, channel=body.channel, time=body.time)


@router.post("/api/spotlight/override")
def override(body: OverrideIn, session_id: Optional[str] = SessionHeader):
    if body.mode == "channel" and (body.channel is None or body.time is None):
        raise HTTPException(422, "a channel override needs channel and time")
    return _act("override", session_id, mode=body.mode, channel=body.channel, time=body.time)


@router.post("/api/spotlight/hold")
def hold(body: OnIn, session_id: Optional[str] = SessionHeader):
    return _act("hold", session_id, on=body.on)


@router.post("/api/spotlight/stop")
def stop(session_id: Optional[str] = SessionHeader):
    return _act("stop", session_id)


@router.post("/api/close")
def close(presenter: Optional[str] = Presenter, x_presenter: Optional[str] = PresenterHeader):
    try:
        manager.close(presenter=_presenter(presenter, x_presenter))
    except J.PresenterLocked as e:
        raise _locked(e)
    return {"closed": True}

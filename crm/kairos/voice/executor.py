"""Voice executor: plan → gate → place call → ingest outcome, all through `ingest_event`.

Placing a real call needs the Sarvam voice-agent route (not yet authorized), so
`place_call` runs the flow against a scripted borrower and builds the
call-completion webhook Sarvam would send. That webhook goes through the same
Sarvam adapter and ingestion path a live callback would use. Every event is
labeled SIMULATED.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from kairos.adapters.fixtures import FixtureSarvamCallAdapter
from kairos.ingest import IngestResult, ingest_event, utc_iso
from kairos.seed import BORROWER_ID, CAMPAIGN_ID, LOAN_A, LOAN_B
from kairos.voice.flows import IVR_FLOW_ID, VOICEBOT_FLOW_ID, CallResult, run_ivr, run_voicebot

SARVAM = FixtureSarvamCallAdapter()
SOURCE = "kairos.voice.executor"


@dataclass
class Scenario:
    key: str
    title: str
    flow: str                          # IVR | VOICEBOT
    purpose: str                       # NUDGE | CALLBACK
    loan_id: Optional[str]
    at: str                            # call time, ISO with offset
    answered: bool = True
    inputs: List[Optional[str]] = field(default_factory=list)   # DTMF digits or utterances
    withdraw_voice_consent: bool = False


SCENARIOS: List[Scenario] = [
    Scenario("ivr_needs_time", "IVR: borrower presses 2 (needs time)", "IVR", "NUDGE", LOAN_B,
             "2026-11-21T12:00:00+05:30", inputs=["2"]),
    Scenario("ivr_no_answer", "IVR: no answer", "IVR", "NUDGE", LOAN_B,
             "2026-11-21T12:05:00+05:30", answered=False),
    Scenario("ivr_invalid_digits", "IVR: three invalid digits → bounded exit, no intent", "IVR", "NUDGE", LOAN_B,
             "2026-11-21T12:10:00+05:30", inputs=["7", "0", "5"]),
    Scenario("ivr_during_hold", "IVR nudge during a supervisor WAIT → blocked", "IVR", "NUDGE", LOAN_B,
             "2026-11-20T10:30:00+05:30", inputs=["2"]),
    Scenario("voicebot_promise_date", "Voicebot callback: promises to pay on the 25th", "VOICEBOT", "CALLBACK", LOAN_B,
             "2026-11-21T12:30:00+05:30", inputs=["Haan ji, boliye", "Salary 25 ko aayegi, tab tak pay kar dunga"]),
    Scenario("voicebot_hardship", "Voicebot callback: hardship → handoff", "VOICEBOT", "CALLBACK", LOAN_B,
             "2026-11-21T12:40:00+05:30", inputs=["Ji boliye", "Mera accident ho gaya tha, abhi hospital mein hoon"]),
    Scenario("voicebot_declined", "Voicebot callback: borrower declines the call", "VOICEBOT", "CALLBACK", LOAN_B,
             "2026-11-21T12:50:00+05:30", inputs=["Abhi nahi, busy hoon"]),
    Scenario("voicebot_no_consent", "Voicebot callback without voice consent → blocked", "VOICEBOT", "CALLBACK", LOAN_B,
             "2026-11-21T13:00:00+05:30", inputs=["Haan"], withdraw_voice_consent=True),
]
SCENARIO_BY_KEY = {s.key: s for s in SCENARIOS}


@dataclass
class CallRun:
    scenario: str
    touch_id: str
    blocked_reason: Optional[str]
    result: Optional[CallResult]
    events: List[IngestResult]

    def to_dict(self) -> Dict[str, Any]:
        return {"scenario": self.scenario, "touch_id": self.touch_id, "blocked_reason": self.blocked_reason,
                "disposition": self.result.disposition if self.result else None,
                "exit_reason": self.result.exit_reason if self.result else None,
                "intent": self.result.intent if self.result else None,
                "transcript": self.result.transcript if self.result else [],
                "events": [e.to_dict() for r in self.events for e in r.flatten()]}


def _envelope(event_type: str, at: str, dedupe_key: str, payload: Dict[str, Any], **ids: Any) -> Dict[str, Any]:
    return {"event_type": event_type, "source": SOURCE, "provenance": "SIMULATED", "occurred_at": at,
            "dedupe_key": dedupe_key, "borrower_id": BORROWER_ID, "payload": payload, **ids}


def _gate(conn: sqlite3.Connection, purpose: str, at_utc: str) -> Optional[str]:
    """Executor recheck before dialing: voice consent, then supervisor holds (nudges only)."""
    b = conn.execute("SELECT consent_voice, consent_version FROM borrowers WHERE borrower_id = ?",
                     (BORROWER_ID,)).fetchone()
    if not b["consent_voice"]:
        return f"No voice consent on file ({b['consent_version'] or 'none'})."
    if purpose == "NUDGE":
        hold = conn.execute(
            """SELECT event_id, expires_at FROM supervisor_decisions WHERE borrower_id = ? AND decision = 'WAIT'
               AND expires_at > ? AND occurred_at <= ? ORDER BY occurred_at DESC LIMIT 1""",
            (BORROWER_ID, at_utc, at_utc)).fetchone()
        if hold:
            return f"Supervisor WAIT {hold['event_id']} holds ordinary outreach until {hold['expires_at']}."
        handoff = conn.execute(
            """SELECT event_id FROM supervisor_decisions WHERE borrower_id = ? AND decision = 'HANDOFF'
               AND occurred_at <= ? ORDER BY occurred_at DESC LIMIT 1""", (BORROWER_ID, at_utc)).fetchone()
        if handoff:
            return f"Supervisor HANDOFF {handoff['event_id']} pauses ordinary outreach."
    return None


def place_call(conn: sqlite3.Connection, scenario_key: str) -> CallRun:
    s = SCENARIO_BY_KEY[scenario_key]
    n = conn.execute("SELECT COUNT(*) FROM touches WHERE channel IN ('IVR', 'VOICE')").fetchone()[0] + 1
    touch_id = f"T-1042-V{n:03d}"
    channel = "IVR" if s.flow == "IVR" else "VOICE"
    flow_id = IVR_FLOW_ID if s.flow == "IVR" else VOICEBOT_FLOW_ID
    events: List[IngestResult] = []

    if s.withdraw_voice_consent:
        events.append(ingest_event(conn, _envelope(
            "consent.changed", s.at, f"consent:{touch_id}:voice-off",
            {"channel": "VOICE", "granted": False, "consent_version": "consent-v2 (voice withdrawn)"})))

    events.append(ingest_event(conn, _envelope(
        "touch.planned", s.at, f"planned:{touch_id}",
        {"touch_id": touch_id, "channel": channel, "purpose": s.purpose, "template_id": flow_id, "scheduled_for": s.at},
        campaign_id=CAMPAIGN_ID, loan_id=s.loan_id)))

    at_utc = utc_iso(s.at)
    reason = _gate(conn, s.purpose, at_utc)
    if reason:
        events.append(ingest_event(conn, _envelope("touch.blocked", s.at, f"blocked:{touch_id}",
                                                   {"reason": reason}, touch_id=touch_id)))
        return CallRun(s.key, touch_id, reason, None, events)

    call_id = "sv-call-" + hashlib.sha1(touch_id.encode()).hexdigest()[:10]
    events.append(ingest_event(conn, _envelope(
        "touch.sent", s.at, f"sent:{touch_id}",
        {"channel": channel, "template_id": flow_id, "provider_message_id": call_id}, touch_id=touch_id)))

    now = datetime.fromisoformat(s.at)
    result = (run_ivr(s.answered, s.inputs, s.loan_id) if s.flow == "IVR"
              else run_voicebot(s.answered, [i or "" for i in s.inputs], s.loan_id, now))
    events.append(ingest_event(conn, SARVAM.normalize(sarvam_webhook(call_id, touch_id, s.flow, result, now))))
    return CallRun(s.key, touch_id, None, result, events)


def sarvam_webhook(call_id: str, touch_id: str, flow: str, result: CallResult, started: datetime) -> Dict[str, Any]:
    """The call-completion payload in the contract exported to kairos/voice/agents/*.json."""
    ended = started.replace(microsecond=0) + timedelta(seconds=result.duration_s or 30)
    return {
        "call_id": call_id, "flow": flow, "status": result.disposition.lower().replace("_", "-"),
        "custom_ref": touch_id, "ended_at": ended.isoformat(), "duration_s": result.duration_s,
        "attempts": result.attempts or None, "exit_reason": result.exit_reason, "dtmf": result.dtmf,
        "transcript": result.transcript, "intent": result.intent,
    }


def run_branch(conn: sqlite3.Connection, scenario_key: str) -> CallRun:
    """Alternate branch from the same saved state: reset → replay the 5-step journey → place the call."""
    from kairos.replay import run_all

    run_all(conn)
    return place_call(conn, scenario_key)


def scenario_list() -> List[Tuple[str, str]]:
    return [(s.key, s.title) for s in SCENARIOS]


__all__ = ["SCENARIOS", "place_call", "run_branch", "scenario_list", "sarvam_webhook", "LOAN_A", "LOAN_B"]

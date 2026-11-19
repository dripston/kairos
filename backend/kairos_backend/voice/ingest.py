"""K17/K18 disposition ingester: Sarvam call-end webhook -> thresholds -> ledger effects -> CRM envelope.

    VoiceIngester(ledger, ...).ingest(payload) -> dict

Payload: sarvam://schemas/webhook-payload (outbound campaign attempt | inbound deployment call), with the
live-workflow and first inferred shapes as a fallback (see normalize_payload). Sarvam sends one webhook per
call ATTEMPT, connected or not; idempotent by attempt_id (outbound) / interaction_id (inbound) — a duplicate
does nothing. A non-terminal attempt (next_action_status set: Sarvam will retry) is recorded as an attempt
only. A not-connected terminal attempt (no answer / busy / failed) is reach-only (NO_RESPONSE, no reward).
Workflow `before_call` gate events (legacy path only) are acknowledged and ignored.

Order of work for one call:
  1. Provider delivery (reach): call connected -> touch DELIVERED + OutcomeFinalizer.record_reach;
     answered > K01 pickup threshold -> Ledger.record_call (stop-for-day). Never a reply by itself.
  2. Borrower intent: parse_disposition (strict) -> decide() against policy/K17-voice-dispositions/
     thresholds.json (pure, versioned) -> one action through the existing ledger APIs
     (record_stop, apply_exception for K13 holds, Supervisor.decide for a callback, ...).
  3. Learner reward: meaningful disposition with confidence >= 0.6 -> OutcomeFinalizer.record_reply.
  4. Verified payment is never touched here: ALREADY_PAID only pauses reminders for 48 h while the
     ledger waits for D7/LMS verification (Ledger.record_payment_verified is the only way to pay).
  5. One CRM envelope (contracts.crm_envelope, event_type voice.call_disposition) carrying the
     thresholds policy_version and the rule that fired.

Human handoff: `handoff` is pluggable. HitlHandoff calls the K06 HITL control plane
(kairos_backend.hitl, branch k06/hitl-control) when an instance is passed; otherwise LedgerHandoff
records the same thing with the ledger's own primitives: a SUPERVISOR_HOLD (all channels pause,
queued work canceled) and a HUMAN_HANDOFF ticket in the K13 queue. Clearing it is
Ledger.clear_supervisor_hold (a person).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from spine.ledger import record_event

from ..contracts import SYNTHETIC_COSTS, CrmSink, RecordingSink, crm_envelope
from ..exceptions import ExceptionEvent
from ..ledger import QUEUED, Instruction, Ledger
from .dispositions import (_EMAIL, _PHONE, DISPOSITIONS, UNCLEAR, Disposition, parse_disposition, report_level,
                           scrub_text)
from .registry import agent_use_case, load_registry, load_thresholds, max_ptp_days, normalize_language
from .store import ensure_schema

EVENT_TYPE = "voice.call_disposition"
SOURCE = "kairos.k17"
ACTOR = "agent:voice"
VOICE_CHANNEL = "voicebot"

# dispositions with their own threshold branch in the brief; every other one below
# min_confidence_any becomes UNCLEAR (human review, no automated action)
COVERED = ("STOP_REQUESTED", "HARDSHIP", "ABUSIVE", "DISPUTE", "WRONG_PERSON", "THIRD_PARTY", "PTP",
           "PARTIAL_PTP", "ALREADY_PAID", "CALLBACK_REQUESTED", "NO_RESPONSE")
NOT_BORROWER_REPLY = ("NO_RESPONSE", UNCLEAR, "WRONG_PERSON", "THIRD_PARTY")
RETRY_ON_THE_DAY_RULES = ("DPD_VALID",)
HANDS_TO_HUMAN = ("SUPERVISOR_HOLD_AND_REVIEW", "HARDSHIP_HOLD_AND_HANDOFF", "K13_HOLD_AND_HANDOFF",
                  "STOP_VOICE_MARK_UNVERIFIED", "K13_CALLBACK_REQUEST", "REVIEW")


# ---------- the pure decision ----------
@dataclass
class Verdict:
    effective: str               # disposition after thresholds (PTP may become NEEDS_TIME_NO_DATE; UNCLEAR)
    rule: str                    # threshold rule that fired, e.g. K17.STOP_REQUESTED.BELOW
    action: str                  # thresholds.json action name
    accepted: bool
    reason: str = ""
    detail: dict = field(default_factory=dict)


def decide(d: Disposition, band: Optional[str], use_case: Optional[str], at: datetime, thresholds: dict,
           policy) -> Verdict:
    """Pure: disposition + thresholds -> what Kairos may do. `at` is the call end (local time)."""
    at = policy.local(at)
    disp, conf = d.disposition, d.confidence
    rules, min_any = thresholds["rules"], float(thresholds["min_confidence_any"])
    if disp == UNCLEAR or disp not in rules:
        return Verdict(UNCLEAR, "K17.UNCLEAR.UNKNOWN_DISPOSITION", "REVIEW", False, "UNKNOWN_DISPOSITION")
    rule = rules[disp]
    accepted = conf >= float(rule["accept_at"])
    if disp == "NO_RESPONSE":
        return Verdict(disp, "K17.NO_RESPONSE", "NONE", True, "NO_RESPONSE")

    if disp in ("PTP", "PARTIAL_PTP"):
        max_days = max_ptp_days(thresholds, band, use_case)
        detail = {"max_ptp_days": max_days, "band": band}

        def down(why: str) -> Verdict:
            return Verdict("NEEDS_TIME_NO_DATE", f"K17.{disp}.DOWNGRADE.{why}", rule["below"], False, why, detail)

        if not accepted:
            return down("LOW_CONFIDENCE")
        if not d.ptp_date:
            return down("NO_DATE")
        offset = (date.fromisoformat(d.ptp_date) - at.date()).days
        detail["date_offset_days"] = offset
        if offset < 0:
            return down("DATE_IN_PAST")
        if offset > max_days:
            return down("BEYOND_MAX_DAYS")
        return Verdict(disp, f"K17.{disp}.ACCEPT", rule["accept"], True, "PTP_ACCEPTED", detail)

    if disp == "CALLBACK_REQUESTED" and accepted:
        if not d.callback_at:
            return Verdict(disp, "K17.CALLBACK_REQUESTED.NO_TIME", "REVIEW", False, "CALLBACK_WITHOUT_TIME")
        cb = policy.local(datetime.fromisoformat(d.callback_at))
        detail = {"callback_at": cb.isoformat(timespec="minutes")}
        if cb <= at:
            return Verdict(disp, "K17.CALLBACK_REQUESTED.PAST", "REVIEW", False, "CALLBACK_TIME_IN_PAST", detail)
        if not policy.in_contact_window(cb.strftime("%H:%M")):
            return Verdict(disp, "K17.CALLBACK_REQUESTED.OUTSIDE_WINDOW", "REVIEW", False,
                           "CALLBACK_OUTSIDE_CONTACT_WINDOW", detail)
        return Verdict(disp, "K17.CALLBACK_REQUESTED.ACCEPT", rule["accept"], True, "CALLBACK_IN_WINDOW", detail)

    if accepted:
        return Verdict(disp, f"K17.{disp}.ACCEPT", rule["accept"], True, f"CONFIDENCE>={rule['accept_at']}")
    if disp not in COVERED and conf < min_any:
        return Verdict(UNCLEAR, f"K17.UNCLEAR.LOW_CONFIDENCE.{disp}", "REVIEW", False, f"CONFIDENCE<{min_any}")
    return Verdict(disp, f"K17.{disp}.BELOW", rule["below"], False, f"CONFIDENCE<{rule['accept_at']}")


def is_meaningful(effective: str, confidence: float, thresholds: dict) -> bool:
    m = thresholds["meaningful_reply"]
    return effective in m["dispositions"] and confidence >= float(m["min_confidence"])


# ---------- handoff hooks ----------
class LedgerHandoff:
    """No HITL module: SUPERVISOR_HOLD + HUMAN_HANDOFF ticket through the ledger (as apply_exception
    does for a K13 routed stop). Repeat handoffs the same day attach to the same ticket."""
    via = "ledger"

    def __init__(self, ledger: Ledger, queue: str):
        self.ledger, self.queue = ledger, queue

    def handoff(self, borrower_id: str, obligation_id: Optional[str], at: datetime, kind: str,
                summary: tuple, ticket_id: Optional[str] = None) -> dict:
        lg = self.ledger
        with lg.tx() as c:
            lg._set_state(c, borrower_id, supervisor_hold=1)
            n = lg._cancel_queued(c, "borrower_id=?", (borrower_id,), "SUPERVISOR_HOLD")
            tid = ticket_id or lg._ticket(c, self.queue, borrower_id, obligation_id, "HUMAN_HANDOFF", summary, at)
            lg._audit(c, ACTOR, "VOICE_HANDOFF", borrower_id, at=at, reason=kind, ticket=tid, canceled=n)
        return {"via": self.via, "hold": "SUPERVISOR_HOLD", "ticket_id": tid, "canceled": n}


class HitlHandoff:
    """K06 HITL present (kairos_backend.hitl.HITL): its own handoff action (HANDOFF hold + case)."""
    via = "hitl"

    def __init__(self, hitl, role: str = "operator", actor: str = "system:voice-k17"):
        self.hitl, self.role, self.actor = hitl, role, actor

    def handoff(self, borrower_id: str, obligation_id: Optional[str], at: datetime, kind: str,
                summary: tuple, ticket_id: Optional[str] = None) -> dict:
        r = self.hitl.act(borrower_id, "handoff", self.actor, self.role, f"voice bot: {kind}", now=at,
                          summary=summary[0])
        return {"via": self.via, **r, "k13_ticket_id": ticket_id}


def default_handoff(ledger: Ledger, queue: str, hitl=None):
    return HitlHandoff(hitl) if hitl is not None else LedgerHandoff(ledger, queue)


# ---------- payload normalisation ----------
# sarvam://schemas/webhook-payload (read 2026-11-19): anyOf OutboundWebhookPayload | InboundWebhookPayload.
#   Outbound (campaign attempt, sent whether or not it connected; `campaign_id` present). REQUIRED: app_id,
#     app_version (int), attempt_id, campaign_id, cohort_id, completion_status (failed|partial|completed),
#     user_identifier, user_phone_number. Optional: connectivity_status (busy|failed|connected|no_answer|null),
#     next_action_status (retry|reschedule|business_retry|short_duration_retry|provider_error_retry|
#     internal_error_retry|null; null = this attempt is terminal), retry_attempt, failure_reason, duration
#     (number|null), interaction_id, executed_at (carries its own offset), start_datetime / end_datetime (UTC),
#     initial_ / final_ / output_agent_variables (objects), interaction_transcript, experiment, metadata,
#     agent_phone_number, recording_url.
#   Inbound (call to a deployment; `deployment_id` present). REQUIRED: app_id, app_version, deployment_id,
#     interaction_id, user_phone_number, agent_phone_number.
# Never stored: user_phone_number, agent_phone_number, recording_url, the raw interaction_transcript (turn
# count only). Fallback (documented, not in the schema): the shape seen in CN's live workflow report body
# (status | connectivity_status, string duration, final_agent_variables as a JSON string, workflow `event`,
# `before_call` gate ignored) and the first inferred shapes (call_id / interaction / call.*, *_variables,
# ended_at, duration_seconds, voicemail-style status aliases).
class PayloadError(ValueError):
    """The webhook body is not a Sarvam call payload (API -> 422)."""


GATE_EVENTS = ("before_call",)
OUTBOUND_REQUIRED = {"app_id": str, "app_version": int, "attempt_id": str, "campaign_id": str, "cohort_id": str,
                     "completion_status": str, "user_identifier": str, "user_phone_number": str}
INBOUND_REQUIRED = {"app_id": str, "app_version": int, "deployment_id": str, "interaction_id": str,
                    "user_phone_number": str, "agent_phone_number": str}
COMPLETION_STATUSES = ("failed", "partial", "completed")
CONNECTIVITY = {"connected": "CONNECTED", "busy": "BUSY", "no_answer": "NO_ANSWER", "failed": "FAILED"}
NEXT_ACTIONS = ("retry", "reschedule", "business_retry", "short_duration_retry", "provider_error_retry",
                "internal_error_retry")
# legacy / live-workflow status aliases (the schema has no `status` and no voicemail value)
STATUS_CANON = {  # raw (lowercase, '-'/' ' -> '_') -> canonical
    "completed": "COMPLETED", "answered": "ANSWERED", "connected": "CONNECTED", "in_progress": "CONNECTED",
    "success": "COMPLETED", "ended": "COMPLETED", "call_ended": "COMPLETED",
    "no_answer": "NO_ANSWER", "unanswered": "NO_ANSWER", "not_answered": "NO_ANSWER",
    "busy": "BUSY", "failed": "FAILED", "failure": "FAILED", "error": "FAILED",
    "not_connected": "NOT_CONNECTED", "rejected": "DECLINED", "declined": "DECLINED",
    "canceled": "CANCELED", "cancelled": "CANCELED",
    "voicemail": "VOICEMAIL", "voice_mail": "VOICEMAIL", "machine_detected": "VOICEMAIL", "answering_machine": "VOICEMAIL"}
CONNECTED_STATUSES = ("COMPLETED", "ANSWERED", "CONNECTED", "INBOUND")
# not connected to a person: reach-only outcome NO_RESPONSE
NOT_CONNECTED_STATUSES = ("NO_ANSWER", "BUSY", "FAILED", "NOT_CONNECTED", "DECLINED", "CANCELED", "VOICEMAIL")
_ID_KEYS = ("attempt_id", "call_id", "interaction_id", "id", "call_result_id")
# applied in order, later wins: call-start inputs, legacy post-call keys, the agent's final state, then the
# subset the agent is flagged to report (output_agent_variables: where the disposition variables live)
_VAR_KEYS = ("initial_agent_variables", "agent_variables", "app_variables", "variables", "call_variables",
             "start_variables", "post_call_variables", "post_interaction_variables", "extracted_variables",
             "final_agent_variables", "output_agent_variables")
_SCHEMA_VAR_KEYS = ("initial_agent_variables", "final_agent_variables", "output_agent_variables")


def _vars(v) -> dict:
    if isinstance(v, dict):
        return {k: (x.get("value") if isinstance(x, dict) and "value" in x else x) for k, x in v.items()}
    if isinstance(v, list):
        return {x["name"]: x.get("value") for x in v if isinstance(x, dict) and "name" in x}
    return {}


def _decode_vars(key: str, v: Any) -> Any:
    """Variables are objects in the schema; the live workflow body sends a JSON string. Decode it, 422 if garbage."""
    if not isinstance(v, str):
        return v
    if not v.strip():
        return {}
    try:
        out = json.loads(v)
    except ValueError:
        raise PayloadError(f"{key} is a string but not JSON")
    if not isinstance(out, (dict, list)):
        raise PayloadError(f"{key} must decode to an object")
    return out


def _canon_status(v: Any) -> str:
    raw = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
    return STATUS_CANON.get(raw, raw.upper())


def _duration(v: Any) -> Optional[float]:
    """Seconds from a number (schema), or defensively "45", "45.2", "00:00:45"; None when absent / unreadable."""
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        if ":" in s:
            secs = 0.0
            for part in s.split(":"):
                secs = secs * 60 + float(part)
            return secs
        f = float(s)
    except ValueError:
        return None
    return f if f >= 0 and f == f else None


def _when(v: Any, naive_is_utc: bool = False) -> Optional[datetime]:
    """ISO-8601 -> datetime. Keeps an explicit offset (executed_at); `naive_is_utc` for start/end_datetime,
    which the schema defines as UTC. Legacy ended_at without an offset stays local (old behaviour)."""
    if not v or not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if naive_is_utc and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _scrub_reason(v: Any) -> Optional[str]:
    """Provider failure reason (free text, e.g. "USER_BUSY", "SIP 486"): kept for ops, phones / emails masked."""
    if v in (None, ""):
        return None
    t = _EMAIL.sub("[email]", str(v))
    return _PHONE.sub("[number]", t).strip()[:200] or None


def payload_shape(payload: dict) -> str:
    if "campaign_id" in payload:
        return "outbound"
    if "deployment_id" in payload:
        return "inbound"
    return "legacy"


def _is(v: Any, typ) -> bool:
    if typ is int:
        return isinstance(v, int) and not isinstance(v, bool)
    return isinstance(v, typ)


def validate_payload(payload: Any) -> str:
    """-> the shape ("outbound" | "inbound" | "legacy"). Outbound / inbound: every REQUIRED field of the schema,
    with its type, and the typed optionals that are present. Legacy fallback: an id, typed fields typed.
    PayloadError (422) otherwise. Error messages name fields only, never values (phone numbers)."""
    if not isinstance(payload, dict):
        raise PayloadError("call payload must be a JSON object")
    shape = payload_shape(payload)
    if shape in ("outbound", "inbound"):
        req = OUTBOUND_REQUIRED if shape == "outbound" else INBOUND_REQUIRED
        missing = [k for k in req if payload.get(k) in (None, "")]
        if missing:
            raise PayloadError(f"{shape} payload missing required: {', '.join(missing)}")
        wrong = [k for k, t in req.items() if not _is(payload[k], t)]
        if wrong:
            raise PayloadError(f"{shape} payload wrong type: {', '.join(wrong)}")
        if shape == "outbound" and payload["completion_status"] not in COMPLETION_STATUSES:
            raise PayloadError("completion_status must be failed | partial | completed")
        for k in ("connectivity_status", "next_action_status", "failure_reason", "interaction_id", "agent_phone_number"):
            if payload.get(k) is not None and not isinstance(payload[k], str):
                raise PayloadError(f"{k} must be a string or null")
        if payload.get("retry_attempt") is not None and not _is(payload["retry_attempt"], int):
            raise PayloadError("retry_attempt must be an integer")
        for k in ("experiment", "metadata"):
            if payload.get(k) is not None and not isinstance(payload[k], dict):
                raise PayloadError(f"{k} must be an object or null")
        if payload.get("interaction_transcript") is not None and not isinstance(payload["interaction_transcript"], list):
            raise PayloadError("interaction_transcript must be a list or null")
        for k in ("executed_at", "start_datetime", "end_datetime"):
            if payload.get(k) not in (None, "") and _when(payload[k]) is None:
                raise PayloadError(f"{k} must be an ISO-8601 datetime")
    else:
        for k in ("event", "status", "connectivity_status", "call_status", "failure_reason", "end_datetime"):
            v = payload.get(k)
            if v is not None and not isinstance(v, str):
                raise PayloadError(f"{k} must be a string")
        if payload.get("metadata") is not None and not isinstance(payload["metadata"], dict):
            raise PayloadError("metadata must be an object")
        if payload.get("event") in GATE_EVENTS:
            return shape
        for k in _ID_KEYS:
            v = payload.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (str, int))):
                raise PayloadError(f"{k} must be a string")
        if payload.get("end_datetime") and _when(payload["end_datetime"]) is None:
            raise PayloadError("end_datetime must be an ISO-8601 datetime")
    dur = payload.get("duration")
    if dur not in (None, "") and (isinstance(dur, (bool, dict, list)) or _duration(dur) is None):
        raise PayloadError("duration must be a number of seconds")
    for k in _VAR_KEYS:
        v = payload.get(k)
        if v is not None and not isinstance(v, (str, dict, list)):
            raise PayloadError(f"{k} must be an object")
    return shape


def normalize_payload(payload: dict) -> dict:
    """Sarvam call payload -> one normalised dict (outbound / inbound per the schema, else the legacy fallback).
    Phone numbers, the recording URL and the transcript text are dropped here and never leave this function."""
    shape = validate_payload(payload)
    p = dict(payload)
    call = p.get("call") or p.get("interaction") or {}
    if not isinstance(call, dict) or shape != "legacy":
        call = {}
    meta = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
    decoded = {k: _vars(_decode_vars(k, p.get(k))) for k in _VAR_KEYS}
    variables: dict = {}
    for src in (decoded, {k: _vars(_decode_vars(k, call.get(k))) for k in _VAR_KEYS}):
        for key in _VAR_KEYS:
            variables.update(src[key])
    exp = p.get("experiment") if isinstance(p.get("experiment"), dict) else {}
    transcript = p.get("interaction_transcript")
    out = {"shape": shape, "event": (p.get("event") or None) if shape == "legacy" else None,
           "interaction_id": p.get("interaction_id") or None, "variables": variables, "metadata": meta,
           "failure_reason": _scrub_reason(p.get("failure_reason")),
           "app_version": p.get("app_version"), "experiment_id": exp.get("experiment_id"),
           "variant_id": exp.get("variant_id"), "campaign_ref": p.get("campaign_id"), "cohort_id": p.get("cohort_id"),
           "deployment_id": p.get("deployment_id"),
           "transcript_turns": len(transcript) if isinstance(transcript, list) else None}
    # join keys, tried in order until one resolves to a Kairos touch (the campaign export should set
    # user_identifier to the touch id)
    cands = (decoded["initial_agent_variables"].get("touch_id"), p.get("user_identifier"), meta.get("touch_id"),
             variables.get("touch_id"), p.get("touch_id"))
    seen: list = []
    for c in cands:
        if c not in (None, "") and str(c) not in seen:
            seen.append(str(c))
    out["touch_candidates"] = seen
    out["touch_id"] = seen[0] if seen else None

    if shape == "outbound":
        conn_raw = p.get("connectivity_status")
        status = CONNECTIVITY.get(str(conn_raw or "").strip().lower(), str(conn_raw).upper() if conn_raw else None)
        connected = True if status == "CONNECTED" else False if status in NOT_CONNECTED_STATUSES else None
        nxt = p.get("next_action_status") or None
        retry = p.get("retry_attempt") or 0
        # attempt_id is the idempotency key; a retry_attempt > 0 is suffixed so a retry can never be swallowed
        # as a duplicate of the first try even if Sarvam reuses the attempt_id across retries
        key = str(p["attempt_id"]) + (f"#r{retry}" if retry else "")
        out.update(call_id=key, attempt_id=str(p["attempt_id"]), status=status, raw_status=conn_raw or None,
                   completion_status=p.get("completion_status"), connected=connected,
                   next_action_status=nxt, retry_attempt=retry, terminal=nxt is None,
                   app_id=p.get("app_id"))
    elif shape == "inbound":
        out.update(call_id=str(p["interaction_id"]), attempt_id=None, status="INBOUND", raw_status=None,
                   completion_status=None, connected=True, next_action_status=None, retry_attempt=0, terminal=True,
                   app_id=p.get("app_id"))
    else:
        call_id = next((str(v) for v in (p.get("attempt_id"), p.get("call_id"), p.get("interaction_id"),
                                         call.get("id"), call.get("interaction_id"), call.get("call_id"), p.get("id"),
                                         p.get("call_result_id")) if v not in (None, "")), None)
        # connectivity_status says whether a person was reached; status may only say the attempt finished
        raw_conn = p.get("connectivity_status")
        raw_status = p.get("status") or p.get("call_status") or call.get("status")
        status = _canon_status(raw_conn) if raw_conn else _canon_status(raw_status)
        connected = p.get("connected", call.get("connected"))
        if not isinstance(connected, bool):
            connected = True if status in CONNECTED_STATUSES else False if status in NOT_CONNECTED_STATUSES else None
        out.update(call_id=call_id, attempt_id=call_id, status=status or None,
                   raw_status=str(raw_conn or raw_status or "") or None, completion_status=None, connected=connected,
                   next_action_status=None, retry_attempt=0, terminal=True,
                   app_id=p.get("app_id") or p.get("agent_id") or call.get("app_id") or meta.get("app_id"))
    out["duration_seconds"] = next((d for d in (_duration(v) for v in (
        p.get("duration"), p.get("duration_seconds"), call.get("duration_seconds"), call.get("duration")))
        if d is not None), None)
    schema = shape != "legacy"
    executed = _when(p.get("executed_at"))
    out["executed_at"] = executed.isoformat() if executed else None
    out["started_at"] = _when(p.get("start_datetime"), naive_is_utc=schema)
    out["ended_at"] = next((t for t in (
        _when(p.get("end_datetime"), naive_is_utc=schema), _when(p.get("ended_at")), _when(p.get("end_time")),
        _when(call.get("ended_at")), _when(call.get("end_time")), out["started_at"], executed) if t), None)
    return out


# ---------- the ingester ----------
class VoiceIngester:
    def __init__(self, ledger: Ledger, thresholds: Optional[dict] = None, registry: Optional[dict] = None,
                 finalizer=None, sink: Optional[CrmSink] = None, supervisor=None, handoff=None, hitl=None,
                 provenance: str = "SIMULATED"):
        self.ledger = ledger
        self.policy = ledger.policy
        self.thresholds = thresholds or load_thresholds()
        self.registry = registry or load_registry()
        self.finalizer = finalizer
        self.sink = sink if sink is not None else RecordingSink()
        if supervisor is None:
            from ..executor import Supervisor
            supervisor = Supervisor(ledger)
        self.supervisor = supervisor
        self.handoff = handoff or default_handoff(ledger, self.thresholds["handoff_queue"], hitl)
        self.provenance = provenance
        ensure_schema(ledger)

    @property
    def version(self) -> str:
        return self.thresholds["policy_version"]

    # ----- helpers -----
    def _loc(self, dt: datetime) -> datetime:
        return self.policy.local(dt)

    def _band_of(self, obligation_id: Optional[str], at: datetime) -> Optional[str]:
        if not obligation_id:
            return None
        c = self.ledger._conn()
        ob = self.ledger._obligation(c, obligation_id)
        if ob is None:
            return None
        dpd = self.ledger._loan_dpd(c, ob, at.date())
        return self.policy.band_for(dpd) if dpd is not None else None

    def _review(self, bid: str, ob: Optional[str], at: datetime, kind: str, summary: str) -> dict:
        lg = self.ledger
        with lg.tx() as c:
            tid = lg._ticket(c, self.thresholds["review_queue"], bid, ob, f"VOICE_REVIEW_{kind}",
                             (f"Voice call needs a human read: {kind}", summary), at)
            lg._audit(c, ACTOR, "VOICE_REVIEW", bid, at=at, reason=kind, ticket=tid)
        return {"review_ticket_id": tid}

    def _summary(self, d: Disposition, v: Verdict) -> tuple:
        return (f"Voice: {v.effective} ({d.confidence:.2f}) rule {v.rule}", d.call_summary or "no summary")

    # ----- public -----
    def ingest(self, payload: dict, borrower_id: Optional[str] = None, now: Optional[datetime] = None) -> dict:
        p = normalize_payload(payload)
        if p["event"] in GATE_EVENTS:  # workflow gate before a dial: not an outcome, nothing to record
            return {"status": "IGNORED", "event": p["event"], "reason": "GATE_EVENT_NOT_AN_OUTCOME",
                    "policy_version": self.version}
        call_id = p["call_id"]
        if not call_id:
            raise PayloadError("call payload has no attempt_id / interaction_id (or legacy call id)")
        at = self._loc(p["ended_at"] or now or self.policy.now())
        touch = next((t for t in (self.ledger.touch(c) for c in p["touch_candidates"]) if t), None)
        bid = borrower_id or (touch["borrower_id"] if touch else None)
        use_case = agent_use_case(p["app_id"], self.registry)
        # Sarvam will retry this attempt (next_action_status set): record the attempt only. No reach, no
        # learner NO_RESPONSE, no follow-up closed; the terminal attempt finalizes. Exception: the borrower
        # was reached and the bot captured an intent (e.g. STOP on a short_duration_retry) — that is acted on
        # now, it must not wait for a redial.
        attempt_only = False
        if not p["terminal"]:
            d0 = parse_disposition(p["variables"])
            attempt_only = not (p["connected"] is True and d0.disposition not in NOT_BORROWER_REPLY)
        lg = self.ledger
        with lg.tx() as c:
            prior = c.execute("SELECT * FROM voice_calls WHERE call_id=?", (call_id,)).fetchone()
            if prior is not None:
                return {"status": "DUPLICATE", "call_id": call_id, "attempt_id": p["attempt_id"], "first": {
                    k: prior[k] for k in ("status", "effective_disposition", "rule", "action", "received_at")}}
            ob = touch["obligation_id"] if touch else None
            if bid and not ob:
                r = lg._event_obligation(c, bid, VOICE_CHANNEL)
                ob = r["obligation_id"] if r else None
            row_status = "UNMATCHED" if not bid else "ATTEMPT" if attempt_only else "PROCESSING"
            stored_touch = touch["instruction_id"] if touch else _scrub_reason(p["touch_id"])
            c.execute("INSERT INTO voice_calls (call_id, app_id, use_case, borrower_id, obligation_id, touch_id, status,"
                      " received_at, connected, duration_seconds, policy_version, call_status, raw_status,"
                      " failure_reason, interaction_id, ended_at, metadata, shape, completion_status,"
                      " next_action_status, retry_attempt, variant_id, attempt_detail)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (call_id, p["app_id"], use_case, bid, ob, stored_touch, row_status, lg._iso(at),
                       None if p["connected"] is None else int(p["connected"]), p["duration_seconds"], self.version,
                       p["status"], p["raw_status"], p["failure_reason"], p["interaction_id"],
                       lg._iso(p["ended_at"]) if p["ended_at"] else None,
                       json.dumps(p["metadata"], default=str) if p["metadata"] else None,
                       p["shape"], p["completion_status"], p["next_action_status"], p["retry_attempt"],
                       p["variant_id"], json.dumps(self._attempt_detail(p), default=str)))
        base = {"call_id": call_id, "attempt_id": p["attempt_id"], "shape": p["shape"], "call_status": p["status"],
                "completion_status": p["completion_status"], "connected": p["connected"],
                "terminal": p["terminal"], "next_action_status": p["next_action_status"],
                "failure_reason": p["failure_reason"], "policy_version": self.version}
        if not bid:
            return {"status": "UNMATCHED", **base, "reason": "no borrower for touch_id / user_identifier"}
        if attempt_only:
            return {"status": "ATTEMPT_RECORDED", **base, "borrower_id": bid,
                    "reason": f"NON_TERMINAL:{p['next_action_status']}"}
        try:
            result = self._process(call_id, p, at, bid, ob, touch, use_case)
        except BaseException:
            with lg.tx() as c:  # let a retry of the same webhook run again
                c.execute("DELETE FROM voice_calls WHERE call_id=? AND status='PROCESSING'", (call_id,))
            raise
        self.sink.emit(result["envelope"])
        return result

    @staticmethod
    def _attempt_detail(p: dict) -> dict:
        """Attempt facts worth keeping. Never phone numbers, recording URL or transcript text."""
        return {k: p[k] for k in ("attempt_id", "app_version", "experiment_id", "variant_id", "campaign_ref",
                                  "cohort_id", "deployment_id", "executed_at", "started_at", "transcript_turns",
                                  "retry_attempt", "next_action_status", "completion_status")
                if p.get(k) not in (None, "")}

    def _process(self, call_id: str, p: dict, at: datetime, bid: str, ob: Optional[str], touch: Optional[dict],
                 use_case: Optional[str]) -> dict:
        lg = self.ledger
        effects: dict[str, Any] = {}
        # 1. provider delivery (reach) — never a reply
        if touch is not None and p["connected"] is True and touch["status"] not in ("CANCELED", "FAILED"):
            # Only a touch the Executor dispatched moves. For a touch still queued (RESERVED /
            # PENDING_APPROVAL) the ledger records an orphan receipt and leaves it unchanged (FSM-F1).
            effects["touch_status"] = lg.record_provider_status(touch["instruction_id"], "DELIVERED")
            if touch["status"] in QUEUED:
                effects["touch_not_dispatched"] = True
        if self.finalizer is not None and touch is not None and p["connected"] is not None:
            self.finalizer.record_reach(touch["instruction_id"], bool(p["connected"]), at)
        if p["duration_seconds"] is not None and p["connected"] is not False:
            effects["call_pickup_canceled"] = lg.record_call(bid, at, p["duration_seconds"], channel=VOICE_CHANNEL)

        # 2. borrower intent. An attempt that never reached a person (no answer / busy / failed / voicemail)
        # is reach-only: NO_RESPONSE whatever the variables say (the bot never spoke to the borrower).
        d = parse_disposition(p["variables"])
        not_connected = p["connected"] is False
        if not_connected:
            d = Disposition(disposition="NO_RESPONSE", confidence=0.0, raw_disposition=d.raw_disposition)
        band = self._band_of(ob, at)
        v = decide(d, band, use_case, at, self.thresholds, self.policy)
        if not_connected:
            v.reason = f"NOT_CONNECTED:{p['status'] or 'UNKNOWN'}"
        if v.effective not in NOT_BORROWER_REPLY:
            effects["stop_for_day_canceled"] = lg.record_response(bid, at, VOICE_CHANNEL, kind=f"voice:{v.effective}",
                                                                  details=f"voice disposition {v.effective}")
        effects.update(self._apply(v, d, at, bid, ob, band, touch, call_id) or {})
        if d.needs_human and v.action not in HANDS_TO_HUMAN:
            effects.update(self._review(bid, ob, at, "NEEDS_HUMAN", d.call_summary or "bot flagged needs_human"))
        if use_case in ("PTP_FOLLOW_UP", "CALLBACK") and not not_connected:  # this call was the follow-up due
            with lg.tx() as c:
                c.execute("UPDATE voice_followups SET status='DONE', closed_by=? WHERE borrower_id=? AND status='PENDING'"
                          " AND use_case=? AND due_on<=? AND call_id<>?",
                          (call_id, bid, use_case, at.date().isoformat(), call_id))

        # 3. learner reward (reply), separate from reach and payment
        meaningful = v.effective != UNCLEAR and is_meaningful(v.effective, d.confidence, self.thresholds)
        reward = None
        if meaningful and self.finalizer is not None:
            reward = self.finalizer.record_reply(bid, at, True, channel=VOICE_CHANNEL)

        # 4. persist + envelope
        threshold = self.policy.call_pickup_threshold_s
        ordinal, label = report_level(True, p["connected"], p["duration_seconds"], v.effective, threshold)
        detail = {"verdict": v.reason, "verdict_detail": v.detail, "effects": effects, "errors": d.errors,
                  "meaningful_reply": meaningful, "reward": reward, "call_status": p["status"],
                  "failure_reason": p["failure_reason"], "metadata": p["metadata"]}
        with lg.tx() as c:
            c.execute("UPDATE voice_calls SET status='DONE', disposition=?, effective_disposition=?, confidence=?,"
                      " rule=?, action=?, report_ordinal=?, report_label=?, detail=? WHERE call_id=?",
                      (d.disposition, v.effective, d.confidence, v.rule, v.action, ordinal, label,
                       json.dumps(detail, default=str), call_id))
            lg._audit(c, ACTOR, "VOICE_DISPOSITION", bid, at=at, reason=v.rule, call_id=call_id,
                      disposition=d.disposition, effective=v.effective, confidence=d.confidence, action=v.action,
                      voice_policy_version=self.version)
        loan_id = None
        if ob:
            row = lg._conn().execute("SELECT loan_id FROM obligations WHERE obligation_id=?", (ob,)).fetchone()
            loan_id = row["loan_id"] if row else None
        payload = {
            "call_id": call_id, "attempt_id": p["attempt_id"], "interaction_id": p["interaction_id"],
            "app_id": p["app_id"], "use_case": use_case, "obligation_id": ob,
            "call_status": p["status"], "completion_status": p["completion_status"], "terminal": p["terminal"],
            "next_action_status": p["next_action_status"], "retry_attempt": p["retry_attempt"],
            "app_version": p["app_version"], "experiment_id": p["experiment_id"], "variant_id": p["variant_id"],
            "failure_reason": p["failure_reason"], "metadata": p["metadata"],
            "disposition": d.disposition, "raw_disposition": d.raw_disposition, "effective_disposition": v.effective,
            "confidence": d.confidence, "accepted": v.accepted, "rule": v.rule, "action": v.action,
            "reason": v.reason, "policy_version": self.version, "registry_version": self.registry["registry_version"],
            "connected": p["connected"], "duration_seconds": p["duration_seconds"],
            "report_ordinal": ordinal, "report_label": label,
            "ptp_date": d.ptp_date, "ptp_amount_type": d.ptp_amount_type, "callback_at": d.callback_at,
            "hardship_type": d.hardship_type, "language_used": d.language_used, "needs_human": d.needs_human,
            "call_summary": d.call_summary, "rule_path": d.rule_path, "errors": d.errors,
            "meaningful_reply": meaningful, "reward": reward, "effects": effects,
            "payment_verified": False,  # a voice call never verifies a payment
        }
        env = crm_envelope(event_type=EVENT_TYPE, borrower_id=bid, occurred_at=at, dedupe_key=f"k17:call:{call_id}",
                           payload=payload, provenance=self.provenance, source=SOURCE, loan_id=loan_id,
                           campaign_id=touch["campaign_id"] if touch else None,
                           touch_id=touch["instruction_id"] if touch else None)
        return {"status": "PROCESSED", "call_id": call_id, "attempt_id": p["attempt_id"], "shape": p["shape"],
                "call_status": p["status"], "completion_status": p["completion_status"], "terminal": p["terminal"],
                "next_action_status": p["next_action_status"], "variant_id": p["variant_id"],
                "connected": p["connected"], "failure_reason": p["failure_reason"], "metadata": p["metadata"],
                "borrower_id": bid, "disposition": d.disposition,
                "effective_disposition": v.effective, "confidence": d.confidence, "rule": v.rule, "action": v.action,
                "accepted": v.accepted, "reason": v.reason, "policy_version": self.version, "effects": effects,
                "meaningful_reply": meaningful, "reward": reward, "report_label": label, "envelope": env}

    # ----- actions -----
    def _apply(self, v: Verdict, d: Disposition, at: datetime, bid: str, ob: Optional[str], band: Optional[str],
               touch: Optional[dict], call_id: str) -> dict:
        lg, a = self.ledger, v.action
        summary = self._summary(d, v)
        if a in ("NONE", "RECORD_INTENT", "DOWNGRADE_NEEDS_TIME_NO_DATE"):
            return {}
        if a == "REVIEW":
            return self._review(bid, ob, at, v.effective if v.effective != UNCLEAR else f"UNCLEAR_{d.disposition}",
                                summary[1])
        if a == "RECORD_STOP_ALL_CHANNELS":
            return {"stop_canceled": lg.record_stop(bid, at, VOICE_CHANNEL)}
        if a == "SUPERVISOR_HOLD_AND_REVIEW":
            return {"handoff": self.handoff.handoff(bid, ob, at, f"POSSIBLE_{v.effective}", summary)}
        if a == "HARDSHIP_HOLD_AND_HANDOFF":
            k13 = lg.apply_exception(bid, ExceptionEvent("HARDSHIP", band or "UNKNOWN"), at, ob, summary)
            return {"k13": k13, "handoff": self.handoff.handoff(bid, ob, at, "HARDSHIP", summary, k13["ticket_id"])}
        if a == "K13_HOLD_AND_HANDOFF":
            k13 = lg.apply_exception(bid, ExceptionEvent(v.effective, band or "UNKNOWN"), at, ob, summary)
            return {"k13": k13, "handoff": self.handoff.handoff(bid, ob, at, v.effective, summary, k13["ticket_id"])}
        if a == "K13_CALLBACK_REQUEST":
            return {"k13": lg.apply_exception(bid, ExceptionEvent("CALLBACK_REQUEST", band or "UNKNOWN"), at, ob, summary)}
        if a == "STOP_VOICE_MARK_UNVERIFIED":
            return self._stop_voice(bid, ob, at, call_id, v.effective)
        if a == "PTP_HOLD":
            return self._ptp(bid, ob, at, band, d, v, call_id)
        if a == "PAYMENT_CLAIM_PENDING":
            return self._payment_claim(bid, ob, at, call_id)
        if a == "SCHEDULE_CALLBACK":
            return self._callback(bid, ob, at, d, touch, summary, call_id)
        if a == "RECORD_LANGUAGE":
            lang = normalize_language(d.language_used, self.registry)
            if lang not in self.registry["supported_languages"]:
                return self._review(bid, ob, at, "LANGUAGE_BARRIER", summary[1])
            with lg.tx() as c:
                c.execute("INSERT INTO voice_preferences (borrower_id, observed_language, source, updated_at)"
                          " VALUES (?,?,?,?) ON CONFLICT(borrower_id) DO UPDATE SET observed_language=excluded.observed_language,"
                          " source=excluded.source, updated_at=excluded.updated_at",
                          (bid, lang, f"call:{call_id}", lg._iso(at)))
            return {"observed_language": lang}
        raise ValueError(f"thresholds.json names an action the ingester does not implement: {a}")

    def _stop_voice(self, bid: str, ob: Optional[str], at: datetime, call_id: str, kind: str) -> dict:
        """The number on file reached someone else: withdraw voice consent for it (per channel, K03
        consents), cancel queued voice touches, flag the contact UNVERIFIED, ticket to re-verify.
        Nothing about the loan is ever said to a third party (the bot gets no loan details anyway)."""
        lg = self.ledger
        chans = tuple(self.thresholds["wrong_person"]["voice_channels"])
        q = ",".join("?" * len(chans))
        with lg.tx() as c:
            c.execute(f"UPDATE consents SET consented=0, updated_at=? WHERE borrower_id=? AND channel IN ({q})",
                      (lg._iso(at), bid, *chans))
            record_event(c, f"EV-VOICE-UNVERIFIED-{call_id}", bid, "consent_changed", lg._iso(at),
                         channel=VOICE_CHANNEL, details=f"voice consent withdrawn: {kind}, contact unverified")
            n = lg._cancel_queued(c, f"borrower_id=? AND channel IN ({q})", (bid, *chans), "CONTACT_UNVERIFIED")
            c.execute("INSERT INTO voice_contact_flags VALUES (?,?,?,?,?) ON CONFLICT(borrower_id) DO UPDATE SET"
                      " status=excluded.status, reason=excluded.reason, call_id=excluded.call_id,"
                      " flagged_at=excluded.flagged_at", (bid, "UNVERIFIED", kind, call_id, lg._iso(at)))
            tid = lg._ticket(c, self.thresholds["review_queue"], bid, ob, "CONTACT_VERIFICATION",
                             (f"Voice call reached {kind}; phone on file unverified", "Re-verify the contact number"), at)
            lg._audit(c, ACTOR, "VOICE_CONTACT_UNVERIFIED", bid, at=at, reason=kind, channels=list(chans),
                      canceled=n, ticket=tid)
        return {"voice_stopped_channels": list(chans), "contact": "UNVERIFIED", "canceled": n, "ticket_id": tid}

    def _ptp(self, bid: str, ob: Optional[str], at: datetime, band: Optional[str], d: Disposition, v: Verdict,
             call_id: str) -> dict:
        lg = self.ledger
        broken = lg._state(lg._conn(), bid)["broken_ptp"]
        offset = v.detail["date_offset_days"]
        try:
            k13 = lg.apply_exception(bid, ExceptionEvent("PTP_OFFERED", band or "UNKNOWN", date_offset_days=offset,
                                                         broken_ptp_count=broken), at, ob, self._summary(d, v))
        except ValueError:  # FSM-F6: no unpaid EMI to promise against -> a person reads the call, no hold
            return {"promise_date": d.ptp_date, "ptp": "NOT_CAPTURED_NO_UNPAID_EMI",
                    **self._review(bid, ob, at, "PTP_WITHOUT_EMI", self._summary(d, v)[1])}
        ob = k13.get("obligation_id") or ob
        out = {"k13": k13, "promise_date": d.ptp_date}
        if k13["hold"] == "PTP_HOLD":
            due = date.fromisoformat(d.ptp_date) + timedelta(days=int(self.thresholds["ptp"]["follow_up_due_days_after_promise"]))
            with lg.tx() as c:
                c.execute("INSERT OR IGNORE INTO voice_followups (call_id, borrower_id, obligation_id, use_case, promise_date,"
                          " due_on) VALUES (?,?,?,?,?,?)", (call_id, bid, ob, self.thresholds["ptp"]["follow_up_use_case"],
                                                            d.ptp_date, due.isoformat()))
            out.update(hold_through=d.ptp_date, follow_up_due_on=due.isoformat(),
                       follow_up_use_case=self.thresholds["ptp"]["follow_up_use_case"])
        return out

    def _payment_claim(self, bid: str, ob: Optional[str], at: datetime, call_id: str) -> dict:
        """Pause reminders for pause_hours with ledger stop_for_day rows; NEVER record a payment."""
        lg = self.ledger
        until = at + timedelta(hours=float(self.thresholds["payment_claim"]["pause_hours"]))
        days, d = [], at.date()
        while d <= until.date():
            days.append(d.isoformat())
            d += timedelta(days=1)
        with lg.tx() as c:
            for day in days:
                c.execute("INSERT OR IGNORE INTO stop_for_day VALUES (?,?,?)", (bid, day, "PAYMENT_CLAIM_PENDING"))
            n = lg._cancel_queued(c, "borrower_id=? AND day BETWEEN ? AND ?", (bid, days[0], days[-1]),
                                  "PAYMENT_CLAIM_PENDING")
            c.execute("INSERT OR IGNORE INTO voice_payment_claims (call_id, borrower_id, obligation_id, claimed_at,"
                      " pause_until) VALUES (?,?,?,?,?)", (call_id, bid, ob, lg._iso(at), lg._iso(until)))
            lg._audit(c, ACTOR, "VOICE_PAYMENT_CLAIM", bid, at=at, reason="PAYMENT_CLAIM_PENDING",
                      obligation_id=ob, pause_until=lg._iso(until), canceled=n)
        return {"payment_claim": "PENDING_VERIFICATION", "pause_until": lg._iso(until), "paused_days": days,
                "canceled": n, "marked_paid": False}

    def _callback(self, bid: str, ob: Optional[str], at: datetime, d: Disposition, touch: Optional[dict],
                  summary: tuple, call_id: str) -> dict:
        """Through the Supervisor (approve-and-reserve: caps, quiet days, holds, stop-for-day all apply)."""
        cb = self._loc(datetime.fromisoformat(d.callback_at))
        if touch is None or not ob:
            return {"callback": "WAIT", "callback_reason": "NO_CAMPAIGN_CONTEXT",
                    **self._review(bid, ob, at, "CALLBACK_UNSCHEDULED", summary[1])}
        spec = self.registry["use_cases"]["CALLBACK"]
        ins = Instruction(bid, ob, touch["campaign_id"], VOICE_CHANNEL, spec.get("gate_template_id"), cb,
                          int(SYNTHETIC_COSTS.get(VOICE_CHANNEL, 0)), purpose=None)
        r = self.supervisor.decide(ins)
        out = {"callback_at": cb.isoformat(timespec="minutes")}
        if r.action == "SEND" and r.reserved:
            return {**out, "callback": "SCHEDULED", "callback_instruction_id": r.instruction_id}
        out.update(callback="WAIT", callback_reason=r.reason, callback_rule=r.rule)
        if r.rule in RETRY_ON_THE_DAY_RULES:
            # only the data-freshness rule failed (K01: DPD older than 24 h at the callback time). Keep the
            # request so the Supervisor re-plans it on that day, when the spine has been refreshed; the full
            # gate runs again then.
            with self.ledger.tx() as c:
                c.execute("INSERT OR IGNORE INTO voice_followups (call_id, borrower_id, obligation_id, use_case,"
                          " callback_at, due_on) VALUES (?,?,?,?,?,?)",
                          (call_id, bid, ob, "CALLBACK", out["callback_at"], cb.date().isoformat()))
            out["callback_queued_for"] = cb.date().isoformat()
        return out

    # ----- clocks / reads -----
    def resolve_payment_claims(self, now: datetime) -> list[dict]:
        """Resolve pending "already paid" claims. Never marks anything paid.

          VERIFIED  the ledger (D7/LMS, Ledger.record_payment_verified) shows the claimed EMI fully paid — the
                    only way to VERIFIED;
          REJECTED  not paid, and there is fraud evidence for the borrower from pause_hours before the claim up to
                    now (Ledger.fraud_evidence: a CM-03 FRAUD_SUSPECT, or a D7 payment refused because its bank
                    reference belongs to another account). The pause ends early: this claim's remaining
                    PAYMENT_CLAIM_PENDING stop-for-day days are lifted (FSM-F4);
          EXPIRED   not paid, no evidence, pause_until reached: reminders resume on their own."""
        lg, now = self.ledger, self._loc(now)
        window = timedelta(hours=float(self.thresholds["payment_claim"]["pause_hours"]))
        out = []
        with lg.tx() as c:
            rows = c.execute("SELECT * FROM voice_payment_claims WHERE status='PENDING_VERIFICATION'").fetchall()
        for r in rows:
            with lg.tx() as c:
                o = lg._obligation(c, r["obligation_id"]) if r["obligation_id"] else None
            paid = o is not None and o["amount_paid_paise"] >= o["amount_due_paise"]
            evidence = [] if paid else lg.fraud_evidence(r["borrower_id"], lg._dt(r["claimed_at"]) - window, now)
            status = ("VERIFIED" if paid else "REJECTED" if evidence
                      else "EXPIRED" if lg._iso(now) >= r["pause_until"] else None)
            if not status:
                continue
            item = {"call_id": r["call_id"], "borrower_id": r["borrower_id"], "status": status}
            with lg.tx() as c:
                reason = ",".join(sorted({e["reason"] for e in evidence})) if evidence else None
                c.execute("UPDATE voice_payment_claims SET status=?, resolved_at=?, reason=? WHERE call_id=?"
                          " AND status='PENDING_VERIFICATION'", (status, lg._iso(now), reason, r["call_id"]))
                if status == "REJECTED":
                    item["lifted_days"] = self._end_claim_pause(c, r, now)
                    item["evidence"] = evidence
                    lg._audit(c, ACTOR, "VOICE_PAYMENT_CLAIM_REJECTED", r["borrower_id"], at=now, reason=reason,
                              call_id=r["call_id"], obligation_id=r["obligation_id"], lifted_days=item["lifted_days"],
                              evidence_audit_ids=[e["audit_id"] for e in evidence])
            out.append(item)
        return out

    def _end_claim_pause(self, c, claim, now: datetime) -> list[str]:
        """Lift the PAYMENT_CLAIM_PENDING stop-for-day rows a rejected claim placed, from today on, except days
        another still-pending claim of the same borrower covers. Other stop-for-day triggers (a reply, a pickup)
        are never touched. Canceled touches stay canceled; the Supervisor replans."""
        lg = self.ledger
        today = now.date().isoformat()
        others = c.execute("SELECT claimed_at, pause_until FROM voice_payment_claims WHERE borrower_id=? AND call_id<>?"
                           " AND status='PENDING_VERIFICATION'", (claim["borrower_id"], claim["call_id"])).fetchall()
        covered = set()
        for o in others:
            d, end = lg._dt(o["claimed_at"]).date(), lg._dt(o["pause_until"]).date()
            while d <= end:
                covered.add(d.isoformat())
                d += timedelta(days=1)
        days = [r["day"] for r in c.execute(
            "SELECT day FROM stop_for_day WHERE borrower_id=? AND trigger='PAYMENT_CLAIM_PENDING' AND day>=? AND day<=?",
            (claim["borrower_id"], today, lg._dt(claim["pause_until"]).date().isoformat())) if r["day"] not in covered]
        for day in days:
            c.execute("DELETE FROM stop_for_day WHERE borrower_id=? AND day=? AND trigger='PAYMENT_CLAIM_PENDING'",
                      (claim["borrower_id"], day))
        return days

    def due_followups(self, day: date) -> list[dict]:
        return [dict(r) for r in self.ledger._conn().execute(
            "SELECT * FROM voice_followups WHERE status='PENDING' AND due_on<=? ORDER BY due_on", (day.isoformat(),))]

    def call(self, call_id: str) -> Optional[dict]:
        r = self.ledger._conn().execute("SELECT * FROM voice_calls WHERE call_id=?", (call_id,)).fetchone()
        if r is None:
            return None
        out = dict(r)
        out["detail"] = json.loads(out["detail"] or "{}")
        out["metadata"] = json.loads(out["metadata"]) if out.get("metadata") else {}
        out["attempt_detail"] = json.loads(out["attempt_detail"]) if out.get("attempt_detail") else {}
        return out


__all__ = ["VoiceIngester", "Verdict", "decide", "is_meaningful", "normalize_payload", "validate_payload",
           "PayloadError", "LedgerHandoff",
           "HitlHandoff", "default_handoff", "DISPOSITIONS", "EVENT_TYPE"]

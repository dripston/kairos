"""K27 <-> Sarvam voice campaigns (DRY RUN ONLY: builds payloads, never calls Sarvam).

    cohort_upload(engine, day, use_case, ...)      -> upload_cohort body for today's voice-step borrowers
    campaign_config(journey_id, step, ...)         -> configure_campaign body (dry_run=True) with a K01-safe retry plan
    gate_response(engine, borrower_id, at)         -> CN AICC "auto churn" gate reply {ac_next, ac_until, ac_step}
    webhook_to_events(payload, borrower_of=...)    -> normalized ChurnEvents from a Sarvam call-attempt payload

What CN runs today (read from the live workspace, list/get only — docs/sarvam-campaigns-notes.md):
  * campaigns carry Sarvam-native retries OFF (retry_config.max_retries = 0) and a webhook_config
    {url, metadata{batch_id, outbound_campaign_id}};
  * redial ("auto churn") is a committed WORKFLOW: start -> HTTP gate (event before_call) -> switch on
    ac_next == "dial" -> telephony node -> HTTP report (event call_ended, with status, duration,
    attempt_id, interaction_id, end_datetime, failure_reason, recording_url, final_agent_variables)
    -> switch: ac_until (ISO UTC) -> wait until -> gate again; else end. The server decides every redial.
Kairos can play the gate: gate_response() answers with K27 + K01 logic, so every redial still passes
the lifecycle (paused / stopped / exhausted) and the journey's voice layer.

No PII leaves Kairos here: phone numbers are NEVER put in the payload; a `phone_ref` token is emitted and
ops resolves it inside the lender's system (requires_phone_resolution = True). Call variables carry the
first name only, never amounts, loan numbers or phones (K17 registry rule).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

from .state import ChurnEvent

IST = timezone(timedelta(hours=5, minutes=30))
# K17/K18 registry (origin/k17-k18/voice-engine backend/kairos_backend/voice/registry.json)
AGENTS = {"PRE_DUE_REMINDER": "Kairos---Pr-0282af37-ef9d", "EMI_REMINDER_EARLY": "Kairos---EM-15f2766f-4eba",
          "CALLBACK": "Kairos---Ca-f080ffa8-2d1d", "MID_BUCKET_RESOLUTION": "Kairos---Mi-edcd3099-61a2",
          "PTP_FOLLOW_UP": "Kairos---PT-07a93e21-1a9b", "HARDSHIP_TRIAGE": "Kairos---Ha-1f02cd05-dfa0"}
USE_CASE_BY_BAND = {"PRE_DUE": "PRE_DUE_REMINDER", "B1": "EMI_REMINDER_EARLY", "B2": "MID_BUCKET_RESOLUTION",
                    "B3": "MID_BUCKET_RESOLUTION", "B4": "MID_BUCKET_RESOLUTION"}
CALL_VARIABLES = ("user_name", "lender_name", "emi_due_date", "touch_id", "preferred_language", "call_date",
                  "max_ptp_date")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEBHOOK_PLACEHOLDER = "https://<kairos-host>/churn/sarvam/webhook"


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0][:30]


def voice_members(engine, day: date) -> list[tuple[str, str]]:
    """(borrower_id, voice channel) whose journey step today allows a voice channel (L3) and are ACTIVE."""
    out = []
    for bid in engine.borrower_ids():
        a = engine.allowed_channels(bid, day)
        for ch in a["channels"]:
            if ch in ("voicebot", "ivr") and any(ch in s.get("allowed", []) for s in a["steps"]):
                out.append((bid, ch))
                break
    return out


def cohort_upload(engine, day: date, use_case: Optional[str] = None, lender_name: str = "Kairos Demo Finance",
                  names: Optional[Callable[[str], str]] = None, campaign_id: str = "<DRY-RUN-CAMPAIGN-ID>") -> dict:
    """upload_cohort body (sarvam-voice-agents MCP) for today's voicebot step members. DRY RUN."""
    rows = []
    for bid, ch in voice_members(engine, day):
        if ch != "voicebot":
            continue
        st = engine.state(bid)
        feats = st.features(day)
        uc = use_case or ("PTP_FOLLOW_UP" if st.journey_id == "BROKEN_PTP" else USE_CASE_BY_BAND.get(feats["band"]))
        if uc is None:
            continue
        lang = st.static.get("language", "en")
        rows.append({"phone_ref": f"PHONE_REF:{bid}", "touch_id": f"K27-{bid}-{day.isoformat()}",
                     "user_name": _first_name(names(bid)) if names else "Customer", "lender_name": lender_name,
                     "emi_due_date": "", "preferred_language": {"hi": "Hindi"}.get(lang, "English"),
                     "call_date": day.isoformat(),
                     "max_ptp_date": (day + timedelta(days=7 if feats["band"] in ("PRE_DUE", "B1") else 3)).isoformat(),
                     "agent_id": AGENTS[uc], "use_case": uc, "cohort": st.cohort, "journey": st.journey_id})
    buf = io.StringIO()
    cols = ["phone_ref", "touch_id", *[c for c in CALL_VARIABLES if c != "touch_id"]]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    transformation = {"phone_number": {"column_name": "phone_ref", "country_code": "IN"},
                      "user_identifier": {"column_name": "touch_id"},
                      "app_variables": {v: {"column_name": v, "required": v in ("user_name", "touch_id")}
                                        for v in CALL_VARIABLES}}
    by_agent = {}
    for r in rows:
        by_agent.setdefault(r["agent_id"], 0)
        by_agent[r["agent_id"]] += 1
    return {"dry_run": True, "provenance": engine.provenance, "requires_phone_resolution": True,
            "tool": "upload_cohort",
            "body": {"parent": "campaign", "parent_id": campaign_id, "name": f"K27 voice {day.isoformat()}",
                     "content": buf.getvalue(), "transformation": transformation},
            "rows": len(rows), "by_agent": by_agent,
            "note": "One campaign per agent: split rows by agent_id. Phone column is a reference token; resolve it "
                    "inside the lender system. Variables must exist in the committed agent config or they reach the "
                    "call as nothing (upload_cohort contract)."}


def retry_plan(band: str, policy=None, attempts_today: int = 0, min_gap_minutes: int = 120,
               per_channel_day_cap: Optional[int] = None) -> dict:
    """Sarvam retry_config that cannot breach K01: every redial is a voice contact, so
    1 + max_retries <= per-channel-per-day cap - attempts already made today, and every interval >= the K01
    same-channel gap (120 min) and >= Sarvam's 5-minute minimum."""
    if policy is not None:
        per_channel_day_cap = per_channel_day_cap or policy.channel_day_cap(band, "voicebot") \
            or (policy.all_channel_day_cap(band)[0] if band == "PRE_DUE" else None)
        min_gap_minutes = max(min_gap_minutes, policy.min_gap_minutes())
    cap = per_channel_day_cap if per_channel_day_cap is not None else {"PRE_DUE": 1, "B1": 3}.get(band, 3)
    retries = max(0, min(2, cap - 1 - attempts_today))
    gap = max(5, int(min_gap_minutes))
    return {"max_retries": retries, "retry_interval_minutes": [gap] * retries,
            "retry_on": {"busy": {"enabled": retries > 0}, "no_answer": {"enabled": retries > 0},
                         "failed": {"enabled": False},
                         "short_duration": {"enabled": retries > 0, "threshold_seconds": 10} if retries else None,
                         "provider_error": {"enabled": True, "max_retries": 1, "retry_interval_minutes": gap},
                         "internal_error": {"enabled": True, "max_retries": 1, "retry_interval_minutes": gap}},
            "k01_basis": f"band {band}: per-channel/day cap {cap}, {attempts_today} used today, gap >= {gap} min"}


def campaign_config(journey_id: str, day: date, use_case: str, band: str, policy=None,
                    window: tuple = ("09:00", "18:45"), app_version: Optional[int] = None) -> dict:
    """configure_campaign body with dry_run=True (never create without dry run from here)."""
    quiet = ["Sunday"]
    start = datetime.combine(day, datetime.strptime(window[0], "%H:%M").time()).replace(tzinfo=IST)
    end = datetime.combine(day, datetime.strptime(window[1], "%H:%M").time()).replace(tzinfo=IST)
    return {"dry_run": True, "tool": "configure_campaign",
            "body": {"dry_run": True, "name": f"K27 {journey_id} {use_case} {day.isoformat()}",
                     "start_timestamp": start.isoformat(), "end_timestamp": end.isoformat(),
                     "allowed_schedule": {"timezone": "Asia/Kolkata", "allowed_start_time": window[0],
                                          "allowed_end_time": window[1],
                                          "allowed_days": [d for d in DAYS if d not in quiet]},
                     "app_config": {"app_type": "agent", "app_id": AGENTS[use_case],
                                    "app_version": app_version or "<COMMITTED_VERSION>", "attempts_per_second": 1.0,
                                    "connection_configs": [{"connection_id": "<ASK_OPS>", "phone_numbers": ["<ASK_OPS>"],
                                                            "weight": 1}],
                                    "retry_config": retry_plan(band, policy),
                                    "webhook_config": {"url": WEBHOOK_PLACEHOLDER,
                                                       "metadata": {"source": "kairos.k27", "journey": journey_id,
                                                                    "use_case": use_case, "day": day.isoformat()}}}},
            "note": "K01 contact window is 08:00-19:00 outbound, Sundays/holidays quiet; Sarvam schedule is set inside "
                    "it. Retries count as voice contacts against K01 caps (retry_plan). connection/number are ops' "
                    "choice (telephony list_connections -> get_connection)."}


def gate_response(engine, borrower_id: str, at: datetime, step: str = "") -> dict:
    """Answer CN's AICC auto-churn workflow gate (before_call / call_ended) for a borrower.
    ac_next='dial' only when the borrower is ACTIVE and today's journey step allows voice in the current layer;
    otherwise wait until the next contact-day morning (ac_until, ISO UTC) or end (both empty)."""
    st = engine.state(borrower_id)
    a = engine.allowed_channels(borrower_id, at.date())
    voice_ok = st.lifecycle == "ACTIVE" and any(c in ("voicebot", "ivr") for c in a["channels"])
    if voice_ok:
        return {"ac_next": "dial", "ac_until": "", "ac_step": step or f"{a['journey']}:{a['day']}",
                "reason": a["reason"]}
    if st.lifecycle in ("STOPPED_OPT_OUT", "PAID_EXIT", "EXHAUSTED_CAMPAIGN", "HARDSHIP_HUMAN"):
        return {"ac_next": "", "ac_until": "", "ac_step": step, "reason": f"END:{st.lifecycle}"}
    nxt = datetime.combine(at.date() + timedelta(days=1), datetime.strptime("09:30", "%H:%M").time()).replace(tzinfo=IST)
    until = nxt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ac_next": "", "ac_until": until, "ac_step": step, "reason": f"WAIT:{st.lifecycle}:{a['reason']}"}


# ---------- webhook -> normalized events ----------
_STATUS = {"completed": "COMPLETED", "answered": "ANSWERED", "connected": "CONNECTED", "success": "COMPLETED",
           "ended": "COMPLETED", "in_progress": "CONNECTED", "no_answer": "NO_ANSWER", "no-answer": "NO_ANSWER",
           "unanswered": "NO_ANSWER", "busy": "BUSY", "failed": "FAILED", "not_connected": "NOT_CONNECTED",
           "not-connected": "NOT_CONNECTED", "rejected": "DECLINED", "declined": "DECLINED", "canceled": "CANCELED",
           "cancelled": "CANCELED"}


def _vars(v: Any) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v) if v.strip() else {}
        except ValueError:
            return {}
    if isinstance(v, dict):
        return {k: (x.get("value") if isinstance(x, dict) and "value" in x else x) for k, x in v.items()}
    if isinstance(v, list):
        return {x["name"]: x.get("value") for x in v if isinstance(x, dict) and "name" in x}
    return {}


def parse_webhook(payload: Mapping[str, Any]) -> dict:
    """Normalize the fields CN's live workflow forwards (call_ended body) and the campaign call-event webhook."""
    p = dict(payload or {})
    meta = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
    variables = {}
    for k in ("final_agent_variables", "agent_variables", "post_call_variables", "variables"):
        variables.update(_vars(p.get(k)))
    attempt = p.get("attempt_id") or p.get("call_result_id") or p.get("interaction_id") or p.get("call_id")
    raw_status = str(p.get("status") or p.get("connectivity_status") or p.get("call_status") or "").strip().lower()
    dur = p.get("duration") if p.get("duration") not in (None, "") else p.get("duration_seconds")
    try:
        dur = float(dur) if dur not in (None, "") else 0.0
    except (TypeError, ValueError):
        dur = 0.0
    ended = p.get("end_datetime") or p.get("ended_at") or p.get("end_time")
    return {"attempt_id": str(attempt) if attempt else None, "interaction_id": p.get("interaction_id"),
            "status": _STATUS.get(raw_status, raw_status.upper() or "UNKNOWN"), "raw_status": raw_status,
            "duration_s": dur, "ended_at": ended, "failure_reason": p.get("failure_reason") or None,
            "variables": variables, "metadata": meta, "event": p.get("event"), "step": p.get("step"),
            "touch_id": variables.get("touch_id") or meta.get("touch_id")}


def webhook_to_events(payload: Mapping[str, Any], borrower_of: Callable[[dict], Optional[str]],
                      channel: str = "voicebot", provenance: str = "LIVE") -> list[ChurnEvent]:
    """One Sarvam call attempt -> ONE call event (idempotent id sarvam:<attempt_id>). The disposition rides on
    the call event; the engine applies K17 thresholds (STOP / hardship / PTP / paid claim) — no separate intent
    is invented. `before_call` gate events are not outcomes and map to nothing."""
    w = parse_webhook(payload)
    if w["event"] == "before_call" or not w["attempt_id"]:
        return []
    bid = borrower_of(w)
    if not bid:
        return []
    v = w["variables"]
    at = w["ended_at"] or datetime.now(timezone.utc).isoformat()
    attrs = {"duration_s": w["duration_s"], "disposition": (v.get("disposition") or "").upper() or None,
             "confidence": v.get("disposition_confidence"), "ptp_date": v.get("ptp_date") or None,
             "callback_at": v.get("callback_at") or None, "hardship_type": v.get("hardship_type"),
             "failure_reason": w["failure_reason"], "interaction_id": w["interaction_id"],
             "dtmf": v.get("dtmf") or v.get("dtmf_input")}
    try:
        attrs["confidence"] = float(attrs["confidence"]) if attrs["confidence"] not in (None, "") else 0.0
    except (TypeError, ValueError):
        attrs["confidence"] = 0.0
    return [ChurnEvent(f"sarvam:{w['attempt_id']}", bid, "call", ChurnEvent.from_dict(
        {"event_id": "x", "borrower_id": bid, "kind": "call", "occurred_at": at}).occurred_at, channel,
        w["status"], w["touch_id"], attrs, provenance, "sarvam.webhook")]


# Differences vs origin/k17-k18/voice-engine voice/ingest.py normalize_payload (to fix there):
VOICE_ENGINE_DIFFS = (
    "variables: CN's live payload carries `final_agent_variables` (a JSON STRING in the workflow report body); "
    "voice-engine reads agent_variables/app_variables/variables/... but not final_agent_variables and never json-decodes a string",
    "id: the attempt id is `attempt_id` (plus `interaction_id`, `call_result_id`); voice-engine looks for call_id / "
    "interaction_id / call.id / id — idempotency must key on attempt_id (one webhook per attempt, retries included)",
    "status: the field is `status` (workflow: status | connectivity_status); voice-engine also accepts call_status but "
    "not connectivity_status",
    "duration: `duration` (string in the workflow body); voice-engine accepts duration/duration_seconds as numbers — OK, "
    "but must tolerate a string",
    "end time: `end_datetime`; voice-engine reads ended_at / end_time only",
    "failure_reason and recording_url exist; voice-engine ignores failure_reason (useful to separate FAILED from NO_ANSWER)",
    "metadata: campaign webhook_config.metadata (batch_id, outbound_campaign_id) is echoed; voice-engine ignores it — "
    "use it to carry touch_id / journey instead of relying on agent variables",
    "every attempt (connected or not) is delivered; voice-engine assumes post-call only and treats unknown status as None",
    "before_call gate events exist in the workflow protocol and must not be ingested as outcomes",
)

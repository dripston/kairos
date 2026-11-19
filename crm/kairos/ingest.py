"""The one shared event-ingestion path.

Every input (seeded sends, provider callbacks, borrower messages, adapter
outputs, ledger webhooks and Kairos's own follow-on actions) goes through
`ingest_event`. It:

1. validates the envelope and resolves borrower / loan / campaign / touch IDs,
2. claims the idempotency key (`dedupe_key`); a repeat is logged as a
   duplicate and stops here, so it writes no timeline entry and has no side effects,
3. runs the handler for the event type, which updates its own table, writes
   exactly one timeline entry and may emit derived events,
4. ingests derived events through this same path, inside the same transaction.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from kairos.adapters import Adapters, default_adapters
from kairos.db import transaction
from kairos.labels import channel_name, rupees

PROVENANCES = ("LIVE", "REPLAY", "SIMULATED")
_ID_FIELDS = ("borrower_id", "loan_id", "campaign_id", "touch_id")


class IngestError(ValueError):
    """The envelope is malformed or references IDs that don't connect."""


@dataclass
class IngestResult:
    status: str                       # "accepted" | "duplicate"
    event_id: str
    event_type: str
    dedupe_key: str
    original_event_id: Optional[str] = None
    derived: List["IngestResult"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "event_id": self.event_id, "event_type": self.event_type,
            "dedupe_key": self.dedupe_key, "original_event_id": self.original_event_id,
            "derived": [d.to_dict() for d in self.derived],
        }

    def flatten(self) -> List["IngestResult"]:
        out = [self]
        for d in self.derived:
            out.extend(d.flatten())
        return out


@dataclass
class Timeline:
    category: str   # DISPATCH | PROVIDER_DELIVERY | CALL_OUTCOME | BORROWER_MESSAGE | BORROWER_INTENT |
                    # AGENT_OUTPUTS | SUPERVISOR | EXECUTION | PAYMENT_CLAIM | VERIFIED_PAYMENT | CONSENT
    title: str
    detail: str = ""


HandlerOutput = Tuple[Timeline, List[Dict[str, Any]]]


# ---------------------------------------------------------------- public API

def ingest_event(conn: sqlite3.Connection, envelope: Dict[str, Any],
                 adapters: Optional[Adapters] = None) -> IngestResult:
    """Ingest one event (and anything it triggers) atomically."""
    adapters = adapters or default_adapters()
    with transaction(conn):
        return _ingest(conn, dict(envelope), adapters)


def utc_iso(value: str) -> str:
    """Normalize an ISO timestamp with offset to UTC, second precision."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise IngestError(f"occurred_at must be ISO-8601 with an offset, got {value!r}") from exc
    if dt.tzinfo is None:
        raise IngestError(f"occurred_at needs a timezone offset, got {value!r}")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- core

def _ingest(conn: sqlite3.Connection, env: Dict[str, Any], adapters: Adapters) -> IngestResult:
    env = _validate_and_resolve(conn, env)
    received_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    cur = conn.execute(
        """INSERT INTO events (event_id, dedupe_key, event_type, source, provenance, fixture_of,
                               occurred_at, received_at, borrower_id, loan_id, campaign_id, touch_id,
                               causation_event_id, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT DO NOTHING""",
        (env["event_id"], env["dedupe_key"], env["event_type"], env["source"], env["provenance"],
         env.get("fixture_of"), env["occurred_at"], received_at, env["borrower_id"], env.get("loan_id"),
         env.get("campaign_id"), env.get("touch_id"), env.get("causation_event_id"),
         json.dumps(env["payload"], sort_keys=True)),
    )
    if cur.rowcount == 0:
        original = conn.execute(
            "SELECT event_id FROM events WHERE dedupe_key = ? OR event_id = ? ORDER BY seq LIMIT 1",
            (env["dedupe_key"], env["event_id"]),
        ).fetchone()
        conn.execute(
            """INSERT INTO duplicate_events (dedupe_key, attempted_event_id, original_event_id,
                                             event_type, borrower_id, received_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (env["dedupe_key"], env["event_id"], original["event_id"], env["event_type"],
             env["borrower_id"], received_at),
        )
        return IngestResult("duplicate", env["event_id"], env["event_type"], env["dedupe_key"],
                            original_event_id=original["event_id"])

    timeline, derived_envs = HANDLERS[env["event_type"]](conn, env, adapters)
    conn.execute(
        """INSERT INTO timeline_entries (event_id, borrower_id, occurred_at, category, title, detail,
                                         loan_id, campaign_id, touch_id, provenance, fixture_of)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (env["event_id"], env["borrower_id"], env["occurred_at"], timeline.category, timeline.title,
         timeline.detail, env.get("loan_id"), env.get("campaign_id"), env.get("touch_id"),
         env["provenance"], env.get("fixture_of")),
    )
    result = IngestResult("accepted", env["event_id"], env["event_type"], env["dedupe_key"])
    for derived in derived_envs:
        result.derived.append(_ingest(conn, derived, adapters))
    return result


def _validate_and_resolve(conn: sqlite3.Connection, env: Dict[str, Any]) -> Dict[str, Any]:
    # touch.planned creates its touch, so the touch can't be looked up yet.
    planned = env.get("event_type") == "touch.planned"
    event_type = env.get("event_type")
    if event_type not in HANDLERS:
        raise IngestError(f"Unknown event_type {event_type!r}")
    if env.get("provenance") not in PROVENANCES:
        raise IngestError(f"provenance must be one of {PROVENANCES}, got {env.get('provenance')!r}")
    for required in ("source", "dedupe_key", "occurred_at"):
        if not env.get(required):
            raise IngestError(f"{event_type}: missing {required}")
    if not isinstance(env.get("payload"), dict):
        raise IngestError(f"{event_type}: payload must be an object")
    env["occurred_at"] = utc_iso(env["occurred_at"])
    env.setdefault("event_id", "evt-" + hashlib.sha1(env["dedupe_key"].encode()).hexdigest()[:16])

    # A reply carries the provider ID of the message it answers. Map it to our touch.
    reply_to = env.pop("reply_to_provider_message_id", None)
    if reply_to and not env.get("touch_id"):
        row = conn.execute("SELECT touch_id FROM touches WHERE provider_message_id = ?", (reply_to,)).fetchone()
        if row:
            env["touch_id"] = row["touch_id"]

    if env.get("touch_id") and not planned:
        touch = conn.execute("SELECT * FROM touches WHERE touch_id = ?", (env["touch_id"],)).fetchone()
        if touch is None:
            raise IngestError(f"Unknown touch_id {env['touch_id']!r}")
        for key in ("borrower_id", "campaign_id", "loan_id"):
            if touch[key] is None:
                continue
            if env.get(key) and env[key] != touch[key]:
                raise IngestError(f"{key} {env[key]!r} conflicts with touch {touch['touch_id']} ({touch[key]!r})")
            env[key] = touch[key]

    if not env.get("borrower_id"):
        raise IngestError(f"{event_type}: cannot resolve borrower_id (no borrower_id, touch_id or known reply context)")
    if conn.execute("SELECT 1 FROM borrowers WHERE borrower_id = ?", (env["borrower_id"],)).fetchone() is None:
        raise IngestError(f"Unknown borrower_id {env['borrower_id']!r}")
    if env.get("loan_id"):
        loan = conn.execute("SELECT borrower_id FROM loans WHERE loan_id = ?", (env["loan_id"],)).fetchone()
        if loan is None or loan["borrower_id"] != env["borrower_id"]:
            raise IngestError(f"loan_id {env['loan_id']!r} does not belong to borrower {env['borrower_id']!r}")
    if env.get("campaign_id") and conn.execute(
            "SELECT 1 FROM campaigns WHERE campaign_id = ?", (env["campaign_id"],)).fetchone() is None:
        raise IngestError(f"Unknown campaign_id {env['campaign_id']!r}")
    return env


def _derive(parent: Dict[str, Any], event_type: str, source: str, dedupe_key: str,
            payload: Dict[str, Any], *, fixture_of: Optional[str] = None, **ids: Any) -> Dict[str, Any]:
    """Build a follow-on event that keeps the parent's IDs and provenance.

    Output from a fixture adapter is always SIMULATED, even if the input was LIVE.
    Only events a fixture actually produced carry `fixture_of`. Kairos's own
    follow-on actions (claim opened, reminder canceled) inherit provenance only.
    """
    env = {key: parent.get(key) for key in _ID_FIELDS}
    env.update({k: v for k, v in ids.items() if k in _ID_FIELDS})
    env.update({
        "event_type": event_type,
        "source": source,
        "provenance": "SIMULATED" if fixture_of else parent["provenance"],
        "fixture_of": fixture_of,
        "occurred_at": parent["occurred_at"],
        "dedupe_key": dedupe_key,
        "causation_event_id": parent["event_id"],
        "payload": payload,
    })
    return env


def _loan_label(conn: sqlite3.Connection, loan_id: Optional[str]) -> str:
    if not loan_id:
        return "all loans"
    row = conn.execute("SELECT label FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
    return row["label"] if row else loan_id


def _iso_local(value: str) -> str:
    return datetime.fromisoformat(value).isoformat()


# ---------------------------------------------------------------- handlers

def _on_touch_sent(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    if not env.get("touch_id"):
        raise IngestError("touch.sent needs touch_id")
    conn.execute(
        "UPDATE touches SET status = 'SENT', provider_message_id = ?, updated_at = ? WHERE touch_id = ?",
        (p["provider_message_id"], env["occurred_at"], env["touch_id"]),
    )
    return Timeline("DISPATCH", f"{channel_name(p['channel'])} sent",
                    f"Template {p.get('template_id', 'n/a')} · provider ID {p['provider_message_id']}"), []


_DELIVERY_RANK = {"SENT": 1, "DELIVERED": 2, "READ": 3}


def _on_provider_delivery(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    if not env.get("touch_id"):
        raise IngestError("provider.delivery needs touch_id to join the callback to its send")
    status = p["status"]
    current = conn.execute("SELECT * FROM provider_deliveries WHERE touch_id = ?", (env["touch_id"],)).fetchone()
    channel = channel_name(p["channel"])

    if current and status != "FAILED" and _DELIVERY_RANK.get(status, 0) < _DELIVERY_RANK.get(current["status"], 0):
        # Late, older callback (e.g. SENT after DELIVERED): keep the newer state.
        return Timeline("PROVIDER_DELIVERY", f"{channel} {status.lower()} callback arrived late",
                        f"Kept {current['status']}; older callbacks never rewind delivery state."), []

    read_status = "READ" if status == "READ" else (current["read_status"] if current else "UNKNOWN")
    conn.execute(
        """INSERT INTO provider_deliveries (touch_id, provider, provider_message_id, status, read_status,
                                            status_at, last_event_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(touch_id) DO UPDATE SET status = excluded.status, read_status = excluded.read_status,
               status_at = excluded.status_at, last_event_id = excluded.last_event_id""",
        (env["touch_id"], p["provider"], p["provider_message_id"], status, read_status,
         env["occurred_at"], env["event_id"]),
    )
    detail = "Provider delivery receipt only. It is not a reply."
    if p["channel"] == "SMS":
        detail += " Read status UNKNOWN (SMS has no read receipts)."
    return Timeline("PROVIDER_DELIVERY", f"{channel} {status.lower()}", detail), []


def _on_consent_changed(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    if p["channel"] != "VOICE":
        raise IngestError("Only voice consent is modeled in this slice")
    conn.execute("UPDATE borrowers SET consent_voice = ?, consent_version = ? WHERE borrower_id = ?",
                 (int(bool(p["granted"])), p["consent_version"], env["borrower_id"]))
    verb = "granted" if p["granted"] else "withdrawn"
    return Timeline("CONSENT", f"Voice consent {verb}", f"Consent version {p['consent_version']}."), []


def _on_touch_planned(conn, env, adapters) -> HandlerOutput:
    """A new outbound touch (e.g. an IVR call or a voicebot callback) enters the plan."""
    p = env["payload"]
    if conn.execute("SELECT 1 FROM touches WHERE touch_id = ?", (p["touch_id"],)).fetchone():
        raise IngestError(f"touch {p['touch_id']} already exists")
    if not env.get("campaign_id"):
        raise IngestError("touch.planned needs campaign_id")
    conn.execute(
        """INSERT INTO touches (touch_id, borrower_id, loan_id, campaign_id, channel, purpose, template_id,
                                scheduled_for, status, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""",
        (p["touch_id"], env["borrower_id"], env.get("loan_id"), env["campaign_id"], p["channel"], p["purpose"],
         p.get("template_id"), utc_iso(p["scheduled_for"]), env["occurred_at"]),
    )
    env["touch_id"] = p["touch_id"]  # shown on the timeline entry
    return Timeline("DISPATCH", f"{channel_name(p['channel'])} {p['purpose'].lower()} planned",
                    f"Flow {p.get('template_id')} · {_loan_label(conn, env.get('loan_id'))}"), []


def _on_touch_blocked(conn, env, adapters) -> HandlerOutput:
    """The executor refused to send: consent, hold or cap. Nothing reaches the provider."""
    touch = conn.execute("SELECT * FROM touches WHERE touch_id = ?", (env["touch_id"],)).fetchone()
    reason = env["payload"]["reason"]
    conn.execute("UPDATE touches SET status = 'BLOCKED', status_reason = ?, updated_at = ? WHERE touch_id = ?",
                 (reason, env["occurred_at"], env["touch_id"]))
    return Timeline("EXECUTION", f"Blocked send: {channel_name(touch['channel'])} {touch['purpose'].lower()}",
                    f"{reason} No call was placed."), []


def _on_call_outcome(conn, env, adapters) -> HandlerOutput:
    """Sarvam IVR / voicebot disposition.

    A structured intent (a valid DTMF choice, or what the voicebot heard) becomes a separate
    borrower.intent event. No-answer, invalid input or an unclear call yields none (E10).
    """
    p = env["payload"]
    conn.execute(
        """INSERT INTO call_outcomes (event_id, borrower_id, touch_id, provider, flow, call_id, disposition,
                                      dtmf, attempts, exit_reason, duration_s, transcript_json, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (env["event_id"], env["borrower_id"], env.get("touch_id"), p["provider"], p["flow"], p["call_id"],
         p["disposition"], p.get("dtmf"), p.get("attempts"), p.get("exit_reason"), p.get("duration_s"),
         json.dumps(p.get("transcript") or [], ensure_ascii=False), env["occurred_at"]),
    )
    label = "Sarvam IVR" if p["flow"] == "IVR" else "Sarvam voicebot"
    bits = [f"call {p['call_id']}"]
    if p.get("duration_s") is not None:
        bits.append(f"{p['duration_s']} s")
    if p.get("dtmf"):
        bits.append(f"DTMF {p['dtmf']}")
    if p.get("attempts"):
        bits.append(f"{p['attempts']} prompt attempt(s)")
    if p.get("exit_reason"):
        bits.append(f"exit {p['exit_reason']}")
    derived = []
    vi = p.get("intent")
    if vi and p["disposition"] in ("ANSWERED", "COMPLETED"):
        classified_by = "ivr.dtmf" if p["flow"] == "IVR" else "voicebot.nlu"
        derived.append(_derive(
            env, "borrower.intent", classified_by, f"intent:{env['event_id']}",
            {"intent": vi["intent"], "confidence": vi.get("confidence"), "classified_by": classified_by,
             "agreed_date": vi.get("agreed_date"), "reason": vi.get("reason"), "source_event_id": env["event_id"]},
            fixture_of=env.get("fixture_of"), loan_id=vi.get("loan_id") or env.get("loan_id"),
        ))
        tail = "Structured intent emitted separately."
    else:
        tail = "Disposition only. No borrower intent is inferred from a call outcome."
    return Timeline("CALL_OUTCOME", f"{label}: {p['disposition'].lower().replace('_', ' ')}",
                    f"{' · '.join(bits)}. {tail}"), derived


def _on_borrower_message(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    conn.execute(
        """INSERT INTO borrower_messages (event_id, borrower_id, touch_id, channel, provider_message_id, text, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (env["event_id"], env["borrower_id"], env.get("touch_id"), p["channel"], p["provider_message_id"],
         p["text"], env["occurred_at"]),
    )
    result = adapters.intent.classify({**p, "borrower_id": env["borrower_id"], "touch_id": env.get("touch_id")})
    intent_env = _derive(
        env, "borrower.intent", adapters.intent.name, f"intent:{env['event_id']}",
        {"intent": result.intent, "confidence": result.confidence, "classified_by": result.classified_by,
         "agreed_date": result.agreed_date, "claimed_amount": result.claimed_amount,
         "claimed_reference": result.claimed_reference, "source_event_id": env["event_id"]},
        fixture_of=adapters.intent.name if adapters.intent.is_fixture else None,
        loan_id=result.loan_id or env.get("loan_id"),
    )
    return Timeline("BORROWER_MESSAGE", f"Borrower replied on {channel_name(p['channel'])}", f"“{p['text']}”"), [intent_env]


INTENT_LABEL = {
    "NEEDS_TIME": "Needs more time", "CLAIMS_PAID": "Says it is already paid", "CALLBACK": "Wants a callback",
    "HARDSHIP": "Reports hardship", "STOP": "Opted out", "OTHER": "Unclear",
}


def _on_borrower_intent(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    intent = p["intent"]
    if intent not in INTENT_LABEL:
        raise IngestError(f"Unknown intent {intent!r}")
    if intent == "CLAIMS_PAID" and not env.get("loan_id"):
        raise IngestError("CLAIMS_PAID intent must name the loan it refers to")
    conn.execute(
        """INSERT INTO borrower_intents (event_id, borrower_id, loan_id, campaign_id, touch_id, source_event_id,
                                         intent, confidence, agreed_date, classified_by, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (env["event_id"], env["borrower_id"], env.get("loan_id"), env.get("campaign_id"), env.get("touch_id"),
         p["source_event_id"], intent, p.get("confidence"), p.get("agreed_date"), p["classified_by"],
         env["occurred_at"]),
    )
    title = f"Borrower intent: {INTENT_LABEL[intent]}"
    detail = f"Confidence {p['confidence']:.0%}" if p.get("confidence") is not None else "Confidence n/a"
    if p.get("reason"):
        detail += f". {p['reason']}"
    if intent == "CLAIMS_PAID":
        title += f" ({_loan_label(conn, env['loan_id'])})"
        claim_env = _derive(
            env, "payment.claim_opened", "kairos.cm03", f"claim:{env['event_id']}",
            {"amount": p.get("claimed_amount"), "claimed_reference": p.get("claimed_reference"),
             "intent_event_id": env["event_id"]},
        )
        return Timeline("BORROWER_INTENT", title, detail + ". Routed to payment verification (CM-03)."), [claim_env]

    # Every other intent goes to the supervisor, which reads the four agent outputs first.
    agents_env = _derive(
        env, "agent.outputs", adapters.agents.name, f"agents:{env['event_id']}",
        {"trigger_event_id": env["event_id"], "intent": intent, "agreed_date": p.get("agreed_date")},
        fixture_of=adapters.agents.name if adapters.agents.is_fixture else None,
    )
    return Timeline("BORROWER_INTENT", title, detail + "."), [agents_env]


def _on_agent_outputs(conn, env, adapters) -> HandlerOutput:
    """Record the four recommendations (K05, K09, K21, K22), then ask the supervisor."""
    from kairos.adapters import AgentOutput
    from kairos.views import load_state, summarize  # read model; imported lazily to avoid a cycle

    p = env["payload"]
    lines = summarize(load_state(conn, env["borrower_id"]))
    outputs = [AgentOutput(agent="summarizer", task_id="K05", owner="Yuval", is_fixture=False,
                           headline=lines[1], output={"channel_evidence": lines[0], "borrower_intent": lines[1]})]
    outputs += adapters.agents.recommend({"borrower_id": env["borrower_id"], "intent": p["intent"],
                                          "agreed_date": p.get("agreed_date")})
    run_id = "run-" + hashlib.sha1(env["event_id"].encode()).hexdigest()[:10]
    for o in outputs:
        conn.execute(
            """INSERT INTO agent_outputs (event_id, agent, task_id, owner, run_id, is_fixture, headline, output_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (env["event_id"], o.agent, o.task_id, o.owner, run_id, int(o.is_fixture), o.headline,
             json.dumps(o.output, sort_keys=True, ensure_ascii=False)),
        )
    decision = adapters.supervisor.decide({
        "trigger": "intent", "borrower_id": env["borrower_id"], "campaign_id": env.get("campaign_id"),
        "intent": p["intent"], "agreed_date": p.get("agreed_date"), "agent_outputs": outputs,
        "occurred_at": _iso_local(env["occurred_at"]),
    })
    detail = " | ".join(f"{o.task_id}{' (FIXTURE)' if o.is_fixture else ''}: {o.headline}" for o in outputs)
    return (Timeline("AGENT_OUTPUTS", f"Four agent recommendations ({run_id})", detail),
            [_decision_env(env, adapters, decision, p["trigger_event_id"], inputs_event_id=env["event_id"])])


def _decision_env(parent, adapters, decision, trigger_event_id, inputs_event_id=None) -> Dict[str, Any]:
    return _derive(
        parent, "supervisor.decision", adapters.supervisor.name, f"decision:{parent['event_id']}",
        {"decision": decision.decision, "reason": decision.reason, "decided_by": decision.decided_by,
         "policy_version": decision.policy_version,
         "expires_at": utc_iso(decision.expires_at) if decision.expires_at else None,
         "scope_loan_id": decision.scope_loan_id, "trigger_event_id": trigger_event_id,
         "inputs_event_id": inputs_event_id},
        fixture_of=adapters.supervisor.name if adapters.supervisor.is_fixture else None,
        loan_id=decision.scope_loan_id or parent.get("loan_id"),
    )


def _on_supervisor_decision(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    if p["decision"] not in ("SEND", "WAIT", "CANCEL", "HANDOFF"):
        raise IngestError(f"Unknown supervisor decision {p['decision']!r}")
    conn.execute(
        """INSERT INTO supervisor_decisions (event_id, borrower_id, campaign_id, trigger_event_id, inputs_event_id,
                                             decision, scope_loan_id, reason, policy_version, expires_at,
                                             decided_by, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (env["event_id"], env["borrower_id"], env.get("campaign_id"), p["trigger_event_id"], p.get("inputs_event_id"),
         p["decision"], p.get("scope_loan_id"), p["reason"], p["policy_version"], p.get("expires_at"),
         p["decided_by"], env["occurred_at"]),
    )
    derived = []
    if p["decision"] == "CANCEL":
        # Executor: cancel queued work in scope (one loan, or all of the borrower's pending touches).
        sql = "SELECT touch_id FROM touches WHERE borrower_id = ? AND status = 'PENDING'"
        args: List[Any] = [env["borrower_id"]]
        if p.get("scope_loan_id"):
            sql += " AND loan_id = ?"
            args.append(p["scope_loan_id"])
        for row in conn.execute(sql + " ORDER BY scheduled_for, touch_id", args).fetchall():
            derived.append(_derive(
                env, "touch.canceled", "kairos.executor", f"cancel:{row['touch_id']}:{env['event_id']}",
                {"reason": p["reason"], "decision_event_id": env["event_id"]}, touch_id=row["touch_id"],
            ))
    scope = f" · scope {_loan_label(conn, p['scope_loan_id'])}" if p.get("scope_loan_id") else ""
    detail = f"{p['reason']} Policy {p['policy_version']}"
    if p.get("expires_at"):
        detail += f", expires {_iso_local(p['expires_at'])[:16].replace('T', ' ')} UTC"
    return Timeline("SUPERVISOR", f"Supervisor decision: {p['decision']}{scope}", detail + "."), derived


def _on_payment_claim_opened(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    claim_id = "CLM-" + hashlib.sha1(env["event_id"].encode()).hexdigest()[:8].upper()
    conn.execute(
        """INSERT INTO payment_claims (claim_id, event_id, borrower_id, loan_id, intent_event_id, amount,
                                       claimed_reference, status, opened_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'VERIFICATION_PENDING', ?)""",
        (claim_id, env["event_id"], env["borrower_id"], env["loan_id"], p.get("intent_event_id"), p.get("amount"),
         p.get("claimed_reference"), env["occurred_at"]),
    )
    label = _loan_label(conn, env["loan_id"])
    ref = f" (ref {p['claimed_reference']})" if p.get("claimed_reference") else ""
    return Timeline(
        "PAYMENT_CLAIM", f"Payment claim for {label}: verification pending",
        f"{claim_id} · borrower says {rupees(p.get('amount'))} paid{ref}. This is a claim, not a verified "
        f"payment. {label} stays open until D7/LMS confirm it.",
    ), []


def _on_payment_verified(conn, env, adapters) -> HandlerOutput:
    p = env["payload"]
    if not env.get("loan_id"):
        raise IngestError("payment.verified needs loan_id")
    loan = conn.execute("SELECT * FROM loans WHERE loan_id = ?", (env["loan_id"],)).fetchone()
    claim = conn.execute(
        """SELECT claim_id FROM payment_claims WHERE loan_id = ? AND status = 'VERIFICATION_PENDING'
           ORDER BY opened_at LIMIT 1""", (env["loan_id"],),
    ).fetchone()
    conn.execute(
        """INSERT INTO verified_payments (event_id, borrower_id, loan_id, amount, ledger_ref, settled_at,
                                          lms_allocated, matched_claim_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (env["event_id"], env["borrower_id"], env["loan_id"], p["amount"], p["ledger_ref"],
         utc_iso(p["settled_at"]), int(p["lms_allocated"]), claim["claim_id"] if claim else None),
    )
    if claim:
        conn.execute(
            "UPDATE payment_claims SET status = 'VERIFIED', resolved_at = ?, resolved_by_event_id = ? WHERE claim_id = ?",
            (env["occurred_at"], env["event_id"], claim["claim_id"]),
        )

    label = loan["label"]
    remaining = max(loan["overdue_amount"] - p["amount"], 0) if p["lms_allocated"] else loan["overdue_amount"]
    cleared = bool(p["lms_allocated"]) and remaining == 0
    conn.execute(
        "UPDATE loans SET overdue_amount = ?, status = ?, status_reason = ?, updated_at = ? WHERE loan_id = ?",
        (remaining, "CLEARED" if cleared else "OPEN",
         f"Ledger {p['ledger_ref']} verified" if cleared else loan["status_reason"], env["occurred_at"], env["loan_id"]),
    )

    derived = []
    if cleared:
        decision = adapters.supervisor.decide({
            "trigger": "payment_verified", "borrower_id": env["borrower_id"], "loan_id": env["loan_id"],
            "loan_label": label, "occurred_at": _iso_local(env["occurred_at"]),
        })
        derived.append(_decision_env(env, adapters, decision, env["event_id"]))

    others = conn.execute(
        "SELECT label, status FROM loans WHERE borrower_id = ? AND loan_id != ? ORDER BY loan_id",
        (env["borrower_id"], env["loan_id"]),
    ).fetchall()
    other_text = " ".join(f"{o['label']} unaffected ({o['status'].lower()})." for o in others)
    outcome = (f"{label} overdue EMI cleared." if cleared
               else f"{label} still open, {rupees(remaining)} overdue (partial or unallocated).")
    claim_text = f" Matched claim {claim['claim_id']}." if claim else ""
    return Timeline(
        "VERIFIED_PAYMENT", f"Ledger verified {rupees(p['amount'])} for {label}",
        f"D7 settlement {p['ledger_ref']}, LMS allocated: {'yes' if p['lms_allocated'] else 'no'}.{claim_text} "
        f"{outcome} {other_text}".strip(),
    ), derived


def _on_touch_canceled(conn, env, adapters) -> HandlerOutput:
    touch = conn.execute("SELECT * FROM touches WHERE touch_id = ?", (env["touch_id"],)).fetchone()
    if touch["status"] == "PENDING":
        conn.execute(
            "UPDATE touches SET status = 'CANCELED', status_reason = ?, updated_at = ? WHERE touch_id = ?",
            (f"Supervisor CANCEL {env['payload']['decision_event_id']}", env["occurred_at"], env["touch_id"]),
        )
        title = f"Executed: {channel_name(touch['channel'])} {touch['purpose'].lower()} canceled"
    else:
        title = f"Executor: {touch['touch_id']} already {touch['status'].lower()}, nothing to cancel"
    return Timeline("EXECUTION", f"{title} ({_loan_label(conn, touch['loan_id'])})",
                    f"{touch['touch_id']} was scheduled for {_iso_local(touch['scheduled_for'])[:16].replace('T', ' ')} UTC."), []


HANDLERS: Dict[str, Callable[..., HandlerOutput]] = {
    "consent.changed": _on_consent_changed,
    "touch.planned": _on_touch_planned,
    "touch.sent": _on_touch_sent,
    "touch.blocked": _on_touch_blocked,
    "provider.delivery": _on_provider_delivery,
    "call.outcome": _on_call_outcome,
    "borrower.message": _on_borrower_message,
    "borrower.intent": _on_borrower_intent,
    "agent.outputs": _on_agent_outputs,
    "supervisor.decision": _on_supervisor_decision,
    "payment.claim_opened": _on_payment_claim_opened,
    "payment.verified": _on_payment_verified,
    "touch.canceled": _on_touch_canceled,
}

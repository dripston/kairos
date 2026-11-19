"""Read model for the borrower detail page (K05 / K14).

Everything here is derived from the tables the ingestion path writes. Nothing
on the page is stored separately.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from kairos.labels import channel_name

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def fmt_ist(value: Optional[str], with_day: bool = False) -> str:
    if not value:
        return ""
    dt = datetime.fromisoformat(value).astimezone(IST)
    return dt.strftime("%a %d %b, %H:%M IST" if with_day else "%d %b %H:%M")


def _rows(conn: sqlite3.Connection, sql: str, *args: Any) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def load_state(conn: sqlite3.Connection, borrower_id: str) -> Optional[Dict[str, Any]]:
    borrower = conn.execute("SELECT * FROM borrowers WHERE borrower_id = ?", (borrower_id,)).fetchone()
    if borrower is None:
        return None
    q = lambda sql: _rows(conn, sql, borrower_id)  # noqa: E731
    loans = q("SELECT * FROM loans WHERE borrower_id = ? ORDER BY label")
    touches = q("SELECT * FROM touches WHERE borrower_id = ? ORDER BY scheduled_for, touch_id")
    deliveries = {d["touch_id"]: d for d in q(
        "SELECT d.* FROM provider_deliveries d JOIN touches t USING (touch_id) WHERE t.borrower_id = ?")}
    messages = q("SELECT * FROM borrower_messages WHERE borrower_id = ? ORDER BY received_at, rowid")
    calls = q("SELECT * FROM call_outcomes WHERE borrower_id = ? ORDER BY occurred_at, rowid")
    for t in touches:
        t["delivery"] = deliveries.get(t["touch_id"])
        t["channel_name"] = channel_name(t["channel"])
        t["replies"] = [m for m in messages if m["touch_id"] == t["touch_id"]]
        t["calls"] = [c for c in calls if c["touch_id"] == t["touch_id"]]
    last_event = conn.execute("SELECT MAX(occurred_at) AS t FROM events WHERE borrower_id = ?",
                              (borrower_id,)).fetchone()["t"]
    return {
        "borrower": dict(borrower),
        "loans": loans,
        "touches": touches,
        "messages": messages,
        "calls": calls,
        "claims": q("SELECT * FROM payment_claims WHERE borrower_id = ? ORDER BY opened_at"),
        "payments": q("SELECT * FROM verified_payments WHERE borrower_id = ? ORDER BY settled_at"),
        "intents": q("SELECT * FROM borrower_intents WHERE borrower_id = ? ORDER BY occurred_at, rowid"),
        "decisions": q("""SELECT d.*, e.fixture_of, e.seq FROM supervisor_decisions d JOIN events e USING (event_id)
                          WHERE d.borrower_id = ? ORDER BY d.occurred_at, e.seq"""),
        "sim_now": last_event or (loans[0]["updated_at"] if loans else None),
    }


def borrower_view(conn: sqlite3.Connection, borrower_id: str) -> Optional[Dict[str, Any]]:
    state = load_state(conn, borrower_id)
    if state is None:
        return None
    for loan in state["loans"]:
        loan["touches"] = [t for t in state["touches"] if t["loan_id"] == loan["loan_id"]]
        loan["claims"] = [c for c in state["claims"] if c["loan_id"] == loan["loan_id"]]
        loan["payments"] = [p for p in state["payments"] if p["loan_id"] == loan["loan_id"]]

    agent_rows: Dict[str, List[Dict[str, Any]]] = {}
    for row in _rows(conn, """SELECT a.* FROM agent_outputs a JOIN events e USING (event_id)
                              WHERE e.borrower_id = ? ORDER BY a.task_id""", borrower_id):
        row["output"] = json.loads(row.pop("output_json"))
        agent_rows.setdefault(row["event_id"], []).append(row)
    timeline = _rows(conn, "SELECT * FROM timeline_entries WHERE borrower_id = ? ORDER BY occurred_at, entry_id",
                     borrower_id)
    transcripts = {c["event_id"]: json.loads(c["transcript_json"] or "[]") for c in state["calls"]}
    for entry in timeline:
        entry["when"] = fmt_ist(entry["occurred_at"])
        entry["agents"] = agent_rows.get(entry["event_id"], [])
        entry["transcript"] = transcripts.get(entry["event_id"], [])

    open_loans = [l for l in state["loans"] if l["status"] == "OPEN"]
    return {
        **state,
        "summary": summarize(state),
        "next_action": next_action(state),
        "timeline": timeline,
        "duplicates": _rows(conn, "SELECT * FROM duplicate_events WHERE borrower_id = ? ORDER BY id", borrower_id),
        "sim_now_display": fmt_ist(state["sim_now"], with_day=True),
        "position": {"open": len(open_loans), "total": len(state["loans"]),
                     "overdue": sum(l["overdue_amount"] for l in open_loans)},
    }


def _ids(*ids: Optional[str]) -> str:
    present = [i for i in ids if i]
    return f" [{', '.join(present)}]" if present else ""


def summarize(state: Dict[str, Any]) -> List[str]:
    """K05: one line of channel evidence, one line of borrower intent, each with source IDs."""
    # Line 1: outbound touches that actually happened, most recent first.
    evidence = []
    for t in sorted((t for t in state["touches"] if t["status"] == "SENT"),
                    key=lambda t: t["updated_at"], reverse=True)[:2]:
        parts = [f"{t['channel_name']} {t['template_id']} sent {fmt_ist(t['updated_at'])}"]
        d, last_id = t["delivery"], None
        voice = t["channel"] in ("IVR", "VOICE")
        if d:
            parts.append(d["status"].lower() + (", read UNKNOWN" if d["read_status"] == "UNKNOWN" else ", read"))
            last_id = d["last_event_id"]
        elif not voice:
            parts.append("no delivery receipt yet")
        for c in t["calls"]:
            parts.append(f"call {c['disposition'].lower().replace('_', ' ')} ({c['exit_reason']})")
            last_id = c["event_id"]
        if voice:
            if not t["calls"]:
                parts.append("no call outcome yet")
        elif t["replies"]:
            n = len(t["replies"])
            parts.append(f"{n} repl{'y' if n == 1 else 'ies'}")
            last_id = t["replies"][-1]["event_id"]
        else:
            parts.append("no reply")
        evidence.append(", ".join(parts) + _ids(t["touch_id"], last_id))
    line1 = "Channel: " + ("; ".join(evidence) if evidence else "no outbound touch yet") + "."

    # Line 2: what the borrower said, newest first, and what it led to.
    labels = {l["loan_id"]: f"{l['label']} ({l['loan_id']})" for l in state["loans"]}
    decisions = {d["trigger_event_id"]: d for d in state["decisions"]}
    claims = {c["intent_event_id"]: c for c in state["claims"]}
    said = []
    for i in reversed(state["intents"][-2:]):
        if i["intent"] == "CLAIMS_PAID":
            c = claims.get(i["event_id"])
            if c and c["status"] == "VERIFIED":
                said.append(f"says {labels.get(i['loan_id'])} is paid → ledger verified"
                            + _ids(i["event_id"], c["claim_id"], c["resolved_by_event_id"]))
            else:
                said.append(f"says {labels.get(i['loan_id'])} is paid → verification pending"
                            + _ids(i["event_id"], c["claim_id"] if c else None))
        else:
            d = decisions.get(i["event_id"])
            tail = ""
            if d:
                tail = f" → supervisor {d['decision']}" + (f" until {fmt_ist(d['expires_at'])}" if d["expires_at"] else "")
            said.append(f"{i['intent'].lower().replace('_', ' ')} ({fmt_ist(i['occurred_at'])}){tail}"
                        + _ids(i["event_id"], d["event_id"] if d else None))
    line2 = "Intent: " + ("; ".join(said) if said else "no borrower reply yet") + "."
    return [line1, line2]


def next_action(state: Dict[str, Any]) -> Dict[str, Any]:
    """Next action + reason, from supervisor holds, open claims and pending touches."""
    sim_now = state["sim_now"]
    loans = {l["loan_id"]: l for l in state["loans"]}
    open_ids = {lid for lid, l in loans.items() if l["status"] == "OPEN"}
    if not open_ids:
        return {"action": "NONE", "headline": "No outreach", "then": None, "basis": "Kairos state",
                "reason": "Every overdue obligation is cleared and verified by the ledger."}

    holds = [d for d in state["decisions"] if d["decision"] == "WAIT" and d["expires_at"] and d["expires_at"] > sim_now]
    hold = holds[-1] if holds else None
    intent_decisions = [d for d in state["decisions"] if d["inputs_event_id"]]
    if intent_decisions and intent_decisions[-1]["decision"] == "HANDOFF":
        d = intent_decisions[-1]
        return {"action": "HANDOFF", "headline": "HANDOFF to a supervisor case", "then": None,
                "reason": f"{d['reason']} Ordinary outreach stays paused until the case is resolved.",
                "basis": f"supervisor decision {d['event_id']} ({d['decided_by']}"
                         f"{', FIXTURE' if d['fixture_of'] else ''}, {d['policy_version']})"}
    claim_loans = {c["loan_id"] for c in state["claims"] if c["status"] == "VERIFICATION_PENDING"}
    pending = [t for t in state["touches"] if t["status"] == "PENDING" and (t["loan_id"] is None or t["loan_id"] in open_ids)]
    sendable = [t for t in pending if t["loan_id"] not in claim_loans]

    reasons, basis = [], ["Kairos state"]
    if hold:
        reasons.append(f"Supervisor WAIT until {fmt_ist(hold['expires_at'])}: {hold['reason']}")
        fx = ", FIXTURE" if hold["fixture_of"] else ""
        basis.insert(0, f"supervisor decision {hold['event_id']} ({hold['decided_by']}{fx}, {hold['policy_version']})")
    for lid in sorted(claim_loans):
        reasons.append(f"{loans[lid]['label']} has a payment claim awaiting ledger verification, so its reminder is held.")
    for d in state["decisions"]:
        if d["decision"] == "CANCEL" and d["scope_loan_id"]:
            reasons.append(f"{loans[d['scope_loan_id']]['label']} EMI is paid and verified; supervisor CANCEL "
                           f"removed its reminder.")

    then = None
    if sendable:
        first_time = sendable[0]["scheduled_for"]
        batch = [t for t in sendable if t["scheduled_for"] == first_time]
        what = " and ".join(f"{loans[t['loan_id']]['label'] if t['loan_id'] else 'account'} "
                            f"{t['purpose'].lower()} ({t['touch_id']})" for t in batch)
        then = f"{batch[0]['channel_name']} {what} at {fmt_ist(first_time)}"
        if hold and first_time < hold["expires_at"]:
            then += " (deferred until the hold ends)"

    if hold or (claim_loans and not sendable):
        headline = f"WAIT until {fmt_ist(hold['expires_at'])}" if hold else "WAIT for ledger verification"
        return {"action": "WAIT", "headline": headline, "reason": " ".join(reasons),
                "then": f"Then: {then}" if then else None, "basis": " + ".join(basis)}
    if then:
        reasons.insert(0, "Scheduled campaign reminder; no active hold.")
        return {"action": "SEND", "headline": then, "reason": " ".join(reasons), "then": None, "basis": " + ".join(basis)}
    return {"action": "WAIT", "headline": "WAIT, nothing scheduled", "then": None, "basis": " + ".join(basis),
            "reason": " ".join(reasons) or "No eligible touch is scheduled."}

"""Apply provider callbacks to the ledger — once, in the right order, without inventing anything.

apply_callback(ledger, event) takes one normalized event (see mock.py for the shape; real
adapters produce the same dict) and returns a CRM-style envelope:

    event_type     provider.delivery | borrower.message | call.outcome
    dedupe_key     stable key; the same callback delivered twice maps to the same key
    provenance     copied from the event (SIMULATED / DEMO / LIVE)
    applied        did this callback change anything?
    duplicate      True when the dedupe_key was already processed
    ignored_reason DUPLICATE | STALE | NO_CHANGE | TOUCH_CLOSED | ALREADY_SENT | SMS_READ_NOT_TRACKED |
                   UNKNOWN_INSTRUCTION
    counts_as_reply, read_status, reachability, outcome, intent, stop, canceled, touch_status, ...

Rules (K01 reply_attribution, edge cases E02/E04/E06/E10):
  * Delivery / read / click are NEVER replies. A click only updates reachability (E06).
  * SMS read status is always UNKNOWN — never inferred from delivery (E04).
  * Ledger statuses only move forward (ledger.RANK). A late SENT after DELIVERED, or any
    duplicate, changes nothing (E04). A FAILED receipt after the touch was SENT is kept as
    SENT by the ledger (it still counts toward caps); the envelope still reports UNREACHABLE.
  * Inbound text: STOP keyword -> ledger.record_stop (suppresses ALL channels, cancels queued
    work, E02). Any other text -> ledger.record_response (reply -> stop for the day).
  * Calls produce ONE normalized outcome per instruction (E10): a duplicate completion is
    ignored. Pickup -> ledger.record_call (stop for the day above the K01 threshold).
    A valid IVR digit or a voicebot structured intent is a captured response (reply); an
    invalid digit, no input, no-answer, busy or decline is NOT, and no intent is invented.
    IVR digit 9 / voicebot intent STOP -> record_stop.

Idempotency: each dedupe_key is claimed in a small table (channel_events) in the ledger's
SQLite file before anything is applied; a second delivery of the same callback (even from
another worker) finds the claim and returns the stored envelope marked duplicate. If applying
fails the claim is released, so a provider retry can apply it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Iterable, List, Optional

from ..ledger import RANK, Ledger
from .mock import IVR_MENU

DELIVERY_STATUSES = ("ACCEPTED", "SENT", "DELIVERED", "READ", "CLICKED", "FAILED", "UNKNOWN")
CALL_STATUSES = ("ANSWERED", "COMPLETED", "DECLINED", "NO_ANSWER", "BUSY")
CALL_CHANNELS = ("ivr", "voicebot")

# Exact-match opt-out keywords (industry-standard set) + "STOP <anything>" ("stop calling me").
STOP_KEYWORDS = {"STOP", "STOPALL", "STOP ALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "OPT OUT",
                 "OPT-OUT", "रोकें", "बंद"}

# what each delivery status asks the ledger to record
_LEDGER_STATUS = {"ACCEPTED": "ACCEPTED", "SENT": "SENT", "DELIVERED": "DELIVERED", "READ": "READ",
                  "CLICKED": "DELIVERED", "FAILED": "FAILED", "UNKNOWN": "UNKNOWN"}
_TARGET_RANK = {"ACCEPTED": "SENT", "UNKNOWN": "UNCERTAIN", "READ": "DELIVERED"}

_SCHEMA = """CREATE TABLE IF NOT EXISTS channel_events (
  dedupe_key TEXT PRIMARY KEY, instruction_id TEXT, channel TEXT, event_type TEXT, status TEXT,
  received_at TEXT, envelope TEXT)"""


def is_stop_text(text: Optional[str]) -> bool:
    if not text:
        return False
    t = " ".join(text.strip().strip(".!").upper().split())
    return t in STOP_KEYWORDS or t.startswith("STOP ")


def event_type_of(event: dict) -> str:
    status, channel = event["status"].upper(), event["channel"]
    if status == "REPLY":
        return "borrower.message"
    if status in CALL_STATUSES or (channel in CALL_CHANNELS and status == "FAILED"):
        return "call.outcome"
    if status in DELIVERY_STATUSES:
        return "provider.delivery"
    raise ValueError(f"unknown callback status {status!r}")


def dedupe_key(event: dict) -> str:
    """Delivery: one per (instruction, status). Call: one outcome per instruction (E10).
    Message: the provider's event id, else a hash of text + time."""
    ch, iid, status = event["channel"], event["instruction_id"], event["status"].upper()
    et = event_type_of(event)
    if et == "provider.delivery":
        return f"{ch}:{iid}:delivery:{status}"
    if et == "call.outcome":
        return f"{ch}:{iid}:call"
    ident = event.get("event_id") or hashlib.sha1(f"{event.get('text')}|{event.get('at')}".encode()).hexdigest()[:16]
    return f"{ch}:{iid}:message:{ident}"


def _ensure_table(ledger: Ledger) -> None:
    if getattr(ledger, "_channel_events_ready", False):
        return
    with ledger.tx() as c:
        c.execute(_SCHEMA)
    ledger._channel_events_ready = True


def _occurred_at(ledger: Ledger, touch: dict, event: dict) -> datetime:
    occ = event.get("occurred_at")
    if isinstance(occ, datetime):
        return ledger.policy.local(occ)
    if isinstance(occ, str):
        return ledger.policy.local(datetime.fromisoformat(occ))
    base = ledger.policy.local(datetime.fromisoformat(touch["scheduled_at"]))
    return base + timedelta(seconds=float(event.get("at") or 0))


def apply_callback(ledger: Ledger, event: dict) -> dict:
    """Apply one normalized callback to the ledger. Idempotent. Returns the envelope."""
    _ensure_table(ledger)
    status = event["status"].upper()
    channel, iid = event["channel"], event["instruction_id"]
    et = event_type_of(event)
    key = dedupe_key(event)
    env = {"event_type": et, "dedupe_key": key, "provenance": event.get("provenance", "UNKNOWN"),
           "channel": channel, "instruction_id": iid, "provider_ref": event.get("provider_ref"),
           "status": status, "borrower_id": None, "occurred_at": None, "applied": False, "duplicate": False,
           "ignored_reason": None, "counts_as_reply": False, "read_status": None, "reachability": None,
           "outcome": None, "intent": None, "dtmf": event.get("dtmf"), "duration_s": event.get("duration_s"),
           "text": event.get("text") if et == "borrower.message" else None, "stop": False, "canceled": 0,
           "touch_status": None}
    touch = ledger.touch(iid)
    if touch is None:
        env["ignored_reason"] = "UNKNOWN_INSTRUCTION"
        return env
    env["borrower_id"] = touch["borrower_id"]
    occurred = _occurred_at(ledger, touch, event)
    env["occurred_at"] = occurred.isoformat(timespec="seconds")

    with ledger.tx() as c:
        claimed = c.execute("INSERT OR IGNORE INTO channel_events (dedupe_key, instruction_id, channel, event_type,"
                            " status, received_at) VALUES (?,?,?,?,?,?)",
                            (key, iid, channel, et, status, ledger.policy.now().isoformat(timespec="seconds"))).rowcount
        prior = None if claimed else c.execute("SELECT envelope FROM channel_events WHERE dedupe_key=?", (key,)).fetchone()
    if not claimed:
        stored = json.loads(prior["envelope"]) if prior and prior["envelope"] else env
        return {**stored, "provenance": env["provenance"], "status": status, "applied": False, "duplicate": True,
                "ignored_reason": "DUPLICATE", "canceled": 0, "touch_status": ledger.touch(iid)["status"]}
    try:
        if et == "provider.delivery":
            _delivery(ledger, touch, event, status, env)
        elif et == "borrower.message":
            _message(ledger, touch, event, occurred, env)
        else:
            _call(ledger, touch, event, status, occurred, env)
    except BaseException:
        with ledger.tx() as c:
            c.execute("DELETE FROM channel_events WHERE dedupe_key=?", (key,))
        raise
    env["touch_status"] = ledger.touch(iid)["status"]
    with ledger.tx() as c:
        c.execute("UPDATE channel_events SET envelope=? WHERE dedupe_key=?", (json.dumps(env, default=str), key))
    return env


def apply_all(ledger: Ledger, events: Iterable[dict]) -> List[dict]:
    """Feed a callback stream (e.g. MockChannelProvider.callbacks(iid)) in arrival order."""
    return [apply_callback(ledger, e) for e in events]


# ---------- handlers ----------
def _record_status(ledger: Ledger, touch: dict, ledger_status: str, provider_ref: Optional[str],
                   reason: Optional[str] = None) -> tuple:
    before = ledger.touch(touch["instruction_id"])["status"]
    after = ledger.record_provider_status(touch["instruction_id"], ledger_status, provider_ref, reason=reason)
    return before, after


def _read_seen(ledger: Ledger, iid: str) -> bool:
    return ledger._conn().execute("SELECT 1 FROM channel_events WHERE instruction_id=? AND status='READ'"
                                  " AND envelope IS NOT NULL", (iid,)).fetchone() is not None


def _delivery(ledger: Ledger, touch: dict, event: dict, status: str, env: dict) -> None:
    ch, iid = touch["channel"], touch["instruction_id"]
    if ch == "sms":
        env["read_status"] = "UNKNOWN"  # SMS has no read receipts; never inferred (K01, E04)
        if status == "READ":
            env["ignored_reason"] = "SMS_READ_NOT_TRACKED"
            return
    elif ch == "whatsapp":
        env["read_status"] = "READ" if status in ("READ", "CLICKED") or _read_seen(ledger, iid) else "UNKNOWN"
    if status in ("DELIVERED", "READ", "CLICKED"):
        env["reachability"] = "REACHABLE"
    elif status == "FAILED":
        env["reachability"] = "UNREACHABLE"
    before, after = _record_status(ledger, touch, _LEDGER_STATUS[status], event.get("provider_ref"),
                                   reason=event.get("error") or ("PROVIDER_" + status if status == "FAILED" else None))
    if after != before:
        env["applied"] = True
    elif status in ("READ", "CLICKED") and after not in ("CANCELED", "FAILED"):
        env["applied"] = True  # new engagement information (read_status / reachability), not a reply
    elif before in ("CANCELED", "FAILED"):
        env["ignored_reason"] = "TOUCH_CLOSED"
    elif status == "FAILED":
        env["ignored_reason"] = "ALREADY_SENT"
    else:
        target = _TARGET_RANK.get(_LEDGER_STATUS[status], _LEDGER_STATUS[status])
        env["ignored_reason"] = "STALE" if RANK.get(target, -1) <= RANK.get(before, -1) else "NO_CHANGE"


def _message(ledger: Ledger, touch: dict, event: dict, occurred: datetime, env: dict) -> None:
    bid, ch = touch["borrower_id"], touch["channel"]
    _record_status(ledger, touch, "DELIVERED", event.get("provider_ref"))  # a reply proves it arrived
    env["reachability"] = "REACHABLE"
    if ch == "sms":
        env["read_status"] = "UNKNOWN"
    text = event.get("text") or ""
    if is_stop_text(text):
        env.update(stop=True, intent="STOP", applied=True, canceled=ledger.record_stop(bid, occurred, channel=ch))
        return
    env.update(counts_as_reply=True, applied=True,
               canceled=ledger.record_response(bid, occurred, channel=ch, kind="reply", details=text[:160]))


def _call(ledger: Ledger, touch: dict, event: dict, status: str, occurred: datetime, env: dict) -> None:
    bid, ch = touch["borrower_id"], touch["channel"]
    if status == "FAILED":
        before, after = _record_status(ledger, touch, "FAILED", event.get("provider_ref"),
                                       reason=event.get("error") or "CALL_FAILED")
        env.update(outcome="FAILED", reachability="UNREACHABLE", applied=after != before)
        if after == before:
            env["ignored_reason"] = "ALREADY_SENT"
        return
    if status in ("NO_ANSWER", "BUSY"):
        env.update(outcome=status, applied=True)  # one normalized outcome; nothing to change in the ledger
        return
    duration = float(event.get("duration_s") or 0)
    _record_status(ledger, touch, "DELIVERED", event.get("provider_ref"))
    env["reachability"] = "REACHABLE"
    env["applied"] = True
    canceled = ledger.record_call(bid, occurred, duration, channel=ch)
    intent = None
    if status == "DECLINED":
        env["outcome"] = "DECLINED"
    elif ch == "ivr":
        digit = event.get("dtmf")
        if digit in IVR_MENU:
            intent = IVR_MENU[digit]
            env["outcome"] = "ANSWERED_RESPONSE"
        elif digit:
            env["outcome"] = "ANSWERED_INVALID_INPUT"  # no intent is invented for an unknown key
        else:
            env["outcome"] = "ANSWERED_NO_INPUT"
    else:
        raw = event.get("intent")
        name = (raw.get("name") if isinstance(raw, dict) else raw) or None
        if name:
            intent = str(name).upper()
            env["intent_detail"] = raw if isinstance(raw, dict) else {"name": intent}
            env["outcome"] = "ANSWERED_RESPONSE"
        else:
            env["outcome"] = "ANSWERED_NO_INPUT"
    env["intent"] = intent
    if intent == "STOP":
        env["stop"] = True
        canceled += ledger.record_stop(bid, occurred, channel=ch)
    elif intent:
        env["counts_as_reply"] = True
        canceled += ledger.record_response(bid, occurred, channel=ch, kind=f"{ch}:{intent}")
    env["canceled"] = canceled

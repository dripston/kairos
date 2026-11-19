"""Synthetic K05/K14 journey, replayed through the shared ingestion path.

Each step starts from a raw provider/ledger payload, normalizes it with the
(FIXTURE) adapter Rehan will own, and hands the envelope to `ingest_event`,
the same function the HTTP webhook endpoints call.

    python3 -m kairos.replay            # reset, replay all 5 steps, print checks + timeline
    python3 -m kairos.replay --step     # run only the next step
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any, Callable, Dict, List, Optional

from kairos.adapters.fixtures import FixtureLedgerAdapter, FixtureSmsDlrAdapter, FixtureWhatsAppInboundAdapter
from kairos.db import connect, get_meta, set_meta, transaction
from kairos.ingest import ingest_event
from kairos.seed import BORROWER_ID, LOAN_A, LOAN_B, TOUCH_REMIND_A, TOUCH_REMIND_B, TOUCH_SMS, seed

SMS = FixtureSmsDlrAdapter()
WHATSAPP = FixtureWhatsAppInboundAdapter()
LEDGER = FixtureLedgerAdapter()

_LEDGER_WEBHOOK = {
    "txn_ref": "D7-UTR-6620981173", "loan_account": LOAN_A, "borrower_ref": BORROWER_ID,
    "amount": 4985, "status": "SETTLED", "lms_allocated": True,
    "settled_at": "2026-11-20T08:30:00+05:30",
}


def _one(conn: sqlite3.Connection, sql: str, *args: Any) -> Optional[sqlite3.Row]:
    return conn.execute(sql, args).fetchone()


def _check_delivered(conn, results) -> List[str]:
    d = _one(conn, "SELECT status, read_status FROM provider_deliveries WHERE touch_id = ?", TOUCH_SMS)
    assert d and d["status"] == "DELIVERED", "SMS delivery not recorded"
    assert d["read_status"] == "UNKNOWN", "SMS read status must stay UNKNOWN"
    assert _one(conn, "SELECT COUNT(*) n FROM borrower_intents")["n"] == 0, "delivery must not create an intent"
    return ["provider_deliveries: DELIVERED, read UNKNOWN", "no borrower intent created"]


def _check_wait(conn, results) -> List[str]:
    i = _one(conn, "SELECT intent FROM borrower_intents ORDER BY occurred_at DESC, rowid DESC LIMIT 1")
    d = _one(conn, "SELECT * FROM supervisor_decisions ORDER BY occurred_at DESC, rowid DESC LIMIT 1")
    assert i and i["intent"] == "NEEDS_TIME", "intent NEEDS_TIME missing"
    assert d and d["decision"] == "WAIT" and d["expires_at"], "supervisor WAIT missing"
    agents = _one(conn, "SELECT COUNT(*) n FROM agent_outputs WHERE event_id = ?", d["inputs_event_id"])["n"]
    assert agents == 4, f"supervisor read {agents} agent outputs, expected 4"
    return ["borrower_intents: NEEDS_TIME", "agent_outputs: K05, K09, K21, K22 recorded",
            f"supervisor_decisions: WAIT, policy {d['policy_version']}, expires {d['expires_at']}"]


def _check_claim(conn, results) -> List[str]:
    c = _one(conn, "SELECT status FROM payment_claims WHERE loan_id = ?", LOAN_A)
    loan = _one(conn, "SELECT status FROM loans WHERE loan_id = ?", LOAN_A)
    assert c and c["status"] == "VERIFICATION_PENDING", "claim not pending"
    assert loan["status"] == "OPEN", "a claim must not clear the loan"
    assert _one(conn, "SELECT COUNT(*) n FROM verified_payments")["n"] == 0, "claim must not create a verified payment"
    return ["payment_claims: Loan A VERIFICATION_PENDING", "Loan A still OPEN, no verified payment"]


def _check_verified(conn, results) -> List[str]:
    a = _one(conn, "SELECT status FROM loans WHERE loan_id = ?", LOAN_A)
    b = _one(conn, "SELECT status FROM loans WHERE loan_id = ?", LOAN_B)
    ra = _one(conn, "SELECT status FROM touches WHERE touch_id = ?", TOUCH_REMIND_A)
    rb = _one(conn, "SELECT status FROM touches WHERE touch_id = ?", TOUCH_REMIND_B)
    c = _one(conn, "SELECT status FROM payment_claims WHERE loan_id = ?", LOAN_A)
    assert a["status"] == "CLEARED" and c["status"] == "VERIFIED", "Loan A not cleared/verified"
    assert ra["status"] == "CANCELED", "Loan A reminder not canceled"
    cancel = _one(conn, "SELECT scope_loan_id FROM supervisor_decisions WHERE decision = 'CANCEL'")
    assert cancel and cancel["scope_loan_id"] == LOAN_A, "supervisor CANCEL scoped to Loan A missing"
    assert b["status"] == "OPEN" and rb["status"] == "PENDING", "Loan B must stay open with its reminder"
    return ["Loan A EMI CLEARED, claim VERIFIED", f"supervisor CANCEL scoped to {LOAN_A} → {TOUCH_REMIND_A} CANCELED",
            f"Loan B OPEN, {TOUCH_REMIND_B} still PENDING"]


def _check_duplicate(conn, results, before: int, after: int) -> List[str]:
    assert results[0]["status"] == "duplicate", "duplicate was accepted"
    assert after == before, f"timeline grew {before} -> {after}"
    assert _one(conn, "SELECT COUNT(*) n FROM verified_payments")["n"] == 1, "second verified payment written"
    return [f"ingest returned duplicate of {results[0]['original_event_id']}",
            f"timeline entries {before} -> {after} (no new entry)", "verified_payments still 1"]


STEPS: List[Dict[str, Any]] = [
    {"key": "sms_delivered", "title": "SMS delivered",
     "adapter": SMS, "raw": {"provider": "sms-sandbox", "message_id": "sms-msg-7781", "status": "DELIVRD",
                             "status_at": "2026-11-18T10:02:11+05:30", "client_ref": TOUCH_SMS},
     "check": _check_delivered},
    {"key": "needs_time", "title": "WhatsApp reply asks for more time → intent, 4 agent outputs, supervisor WAIT",
     "adapter": WHATSAPP, "raw": {"wamid": "wamid.IN-5521", "context_wamid": "wamid.OUT-T0002",
                                  "timestamp": "2026-11-19T09:14:40+05:30",
                                  "text": "Sir thoda time chahiye, abhi paise nahi hain."},
     "check": _check_wait},
    {"key": "claims_paid", "title": "Borrower claims payment for Loan A → verification pending",
     "adapter": WHATSAPP, "raw": {"wamid": "wamid.IN-5534", "context_wamid": "wamid.OUT-T0002",
                                  "timestamp": "2026-11-19T15:40:05+05:30",
                                  "text": "Loan PL100328 ki EMI kal UPI se pay kar di thi. Ref 4417 0921 8833."},
     "check": _check_claim},
    {"key": "ledger_verified", "title": "Ledger verifies Loan A → supervisor CANCEL its reminder, Loan B stays open",
     "adapter": LEDGER, "raw": {**_LEDGER_WEBHOOK, "delivery_attempt_id": "whk-1"},
     "check": _check_verified},
    {"key": "duplicate", "title": "Duplicate ledger webhook (retry) → no new timeline entry",
     "adapter": LEDGER, "raw": {**_LEDGER_WEBHOOK, "delivery_attempt_id": "whk-2"},
     "check": _check_duplicate},
]


def _timeline_count(conn: sqlite3.Connection) -> int:
    return _one(conn, "SELECT COUNT(*) n FROM timeline_entries WHERE borrower_id = ?", BORROWER_ID)["n"]


def run_step(conn: sqlite3.Connection, index: int) -> Dict[str, Any]:
    step = STEPS[index]
    envelope = step["adapter"].normalize(step["raw"])
    before = _timeline_count(conn)
    result = ingest_event(conn, envelope)
    after = _timeline_count(conn)
    results = [r.to_dict() for r in result.flatten()]
    try:
        check: Callable = step["check"]
        notes = (check(conn, results, before, after) if step["key"] == "duplicate" else check(conn, results))
        passed, error = True, None
    except AssertionError as exc:
        notes, passed, error = [], False, str(exc)
    outcome = {
        "n": index + 1, "key": step["key"], "title": step["title"], "adapter": step["adapter"].name,
        "fixture": step["adapter"].is_fixture, "events": [{k: r[k] for k in ("status", "event_type", "event_id",
                                                                               "original_event_id")} for r in results],
        "timeline_before": before, "timeline_after": after, "passed": passed, "checks": notes, "error": error,
    }
    with transaction(conn):
        set_meta(conn, "replay.cursor", str(index + 1))
        history = json.loads(get_meta(conn, "replay.results", "[]"))
        history.append(outcome)
        set_meta(conn, "replay.results", json.dumps(history))
    return outcome


def run_next(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    cursor = int(get_meta(conn, "replay.cursor", "0"))
    return run_step(conn, cursor) if cursor < len(STEPS) else None


def run_all(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    seed(conn)
    return [run_step(conn, i) for i in range(len(STEPS))]


def replay_state(conn: sqlite3.Connection) -> Dict[str, Any]:
    return {"cursor": int(get_meta(conn, "replay.cursor", "0")), "total": len(STEPS),
            "steps": [{"n": i + 1, "title": s["title"]} for i, s in enumerate(STEPS)],
            "results": json.loads(get_meta(conn, "replay.results", "[]"))}


def main(argv: Optional[List[str]] = None) -> int:
    from kairos.views import borrower_view

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="SQLite path (default: var/kairos.db or $KAIROS_DB)")
    parser.add_argument("--step", action="store_true", help="run only the next step")
    args = parser.parse_args(argv)
    conn = connect(args.db)
    if args.step:
        if _one(conn, "SELECT 1 FROM borrowers WHERE borrower_id = ?", BORROWER_ID) is None:
            seed(conn)
        outcome = run_next(conn)
        outcomes = [outcome] if outcome else []
        if not outcome:
            print("Journey already complete. Run without --step to reset and replay.")
    else:
        outcomes = run_all(conn)

    for o in outcomes:
        mark = "PASS" if o["passed"] else "FAIL"
        print(f"[{mark}] {o['n']}. {o['title']}   (timeline {o['timeline_before']} -> {o['timeline_after']})")
        for e in o["events"]:
            dup = f" (original {e['original_event_id']})" if e["original_event_id"] else ""
            print(f"       {e['status']:<9} {e['event_type']:<22} {e['event_id']}{dup}")
        for c in o["checks"]:
            print(f"       ✓ {c}")
        if o["error"]:
            print(f"       ✗ {o['error']}")

    view = borrower_view(conn, BORROWER_ID)
    print(f"\n{view['borrower']['borrower_id']} · {view['summary'][0]}\n{view['summary'][1]}")
    na = view["next_action"]
    print(f"Next action: {na['headline']}" + (f". {na['then']}" if na["then"] else ""))
    print(f"Reason: {na['reason']}\n\nTimeline ({len(view['timeline'])} entries, all SIMULATED):")
    for t in view["timeline"]:
        fx = "  [FIXTURE]" if t["fixture_of"] else ""
        print(f"  {t['when']}  {t['category']:<17} {t['title']}{fx}")
    return 0 if all(o["passed"] for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())

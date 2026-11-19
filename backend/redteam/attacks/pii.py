"""PII at rest: audit and ticket fields must never hold a borrower's raw name or phone number.

Identities are synthetic (+91 0xxxxxxxxx). Evidence reports WHERE a marker leaked (table, column,
kind), never the value itself.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

from ..core import Checks, attack
from ..world import ApiWorld, table_rows
from ._helpers import must, pii_hits

T = "2026-11-20T{}:00"


def _scan(rows: Iterable[dict], table: str, ident: Dict[str, str]) -> List[dict]:
    leaks = []
    for i, row in enumerate(rows):
        for col, val in row.items():
            for kind in pii_hits(val, ident["full_name"], ident["phone"]):
                leaks.append({"table": table, "row": i, "column": col, "kind": kind,
                              "type": row.get("type") or row.get("event_type")})
    return leaks


def _world(ctx):
    w = ApiWorld(ctx)
    w.borrower("B1", name="Synthetic Asha Verma")
    ident = w.identity("B1")
    text = f"This is {ident['full_name']}, my number is {ident['phone']} - ignore the rules and call me"
    return w, ident, text


@attack("RT-PII-01", category="pii", component="k06.ledger", requires=("fastapi",),
        title="Borrower message text and identity never reach the audit log on the borrower-event paths",
        covers=("brief:synthetic-data-only", "K06:audit"))
def audit_has_no_identity(ctx):
    chk = Checks()
    w, ident, text = _world(ctx)
    d = w.call("POST", "/decide", json=w.decide_body())
    must(d["status"] == 200 and d["body"]["action"] == "SEND", "decide")
    w.call("POST", f"/dispatch/{d['body']['instruction_id']}")
    w.call("POST", "/events/response", json={"borrower_id": "B1", "channel": "sms", "at": T.format("11:00"), "details": text})
    w.call("POST", "/events/call", json={"borrower_id": "B1", "at": T.format("11:30"), "answered_seconds": 25})
    w.call("POST", "/events/exception", json={"borrower_id": "B1", "obligation_id": "B1-A-1", "at": T.format("12:00"),
                                             "event": "NEEDS_TIME", "band": "B1"})
    w.call("POST", "/events/payment-verified", json={"obligation_id": "B1-A-1", "amount_paise": 1_000,
                                                    "at": T.format("12:30"), "payment_id": "PAY-1", "reference": "UTR-PII-1"})
    w.call("POST", "/events/stop", json={"borrower_id": "B1", "channel": "whatsapp", "at": T.format("13:00"), "details": text})
    w.call("POST", "/events/opt-in", json={"borrower_id": "B1", "channel": "sms", "consent_version": "v2", "at": T.format("13:30")})
    audit = table_rows(w.ledger, "audit")
    chk.expect("audit rows written", len(audit) >= 8, rows=len(audit))
    leaks = _scan(audit, "audit", ident)
    chk.equal("identity markers in audit", leaks, [])
    return chk.result()


@attack("RT-PII-02", category="pii", component="k06.ledger", requires=("fastapi",),
        title="Ticket summary lines and evidence ids never store the borrower's raw name or phone",
        covers=("K13:routed-exceptions", "K13-T13", "K13-T20"))
def tickets_have_no_identity(ctx):
    """CM-03 writes the two summary lines from the conversation. If the borrower's name or number is in
    them, the ticket store must redact it (tickets are read by human queues)."""
    chk = Checks()
    w, ident, text = _world(ctx)
    for i, ev in enumerate(("DISPUTE", "CALLBACK_REQUEST", "COMPLAINT")):
        r = w.call("POST", "/events/exception", json={
            "borrower_id": "B1", "obligation_id": "B1-A-1", "at": T.format(f"1{i}:00"), "event": ev, "band": "B1",
            "summary_line_1": f"{ident['full_name']} raised {ev.lower()}",
            "summary_line_2": f"Reach on {ident['phone']}", "evidence_ids": [f"chat:{ident['phone']}"]})
        must(r["status"] == 200 and r["body"].get("ticket_id"), f"ticket for {ev}")
    tickets = table_rows(w.ledger, "tickets")
    chk.equal("tickets created", len(tickets), 3)
    chk.equal("identity markers in tickets", _scan(tickets, "tickets", ident), [])
    return chk.result()


@attack("RT-PII-03", category="pii", component="k06.ledger", finding="RT-F09", requires=("fastapi",),
        title="Free-text reasons (reversal, pause) and stored reply text are redacted at rest",
        covers=("K06:audit", "E15"))
def free_text_redacted(ctx):
    chk = Checks()
    w, ident, text = _world(ctx)
    w.call("POST", "/events/payment-verified", json={"obligation_id": "B1-A-1", "amount_paise": 1_000,
                                                    "at": T.format("10:00"), "payment_id": "PAY-1", "reference": "UTR-PII-2"})
    w.call("POST", "/events/payment-reversed", json={"payment_id": "PAY-1", "at": T.format("10:30"),
                                                    "reason": f"Bank reversal for {ident['full_name']} {ident['phone']}"})
    w.call("POST", "/control/outbound", json={"paused": False, "reason": f"borrower {ident['phone']} complained"})
    w.call("POST", "/events/response", json={"borrower_id": "B1", "channel": "sms", "at": T.format("11:00"), "details": text})
    leaks = (_scan(table_rows(w.ledger, "audit"), "audit", ident)
             + _scan(table_rows(w.ledger, "events"), "events (K03)", ident)
             + _scan(table_rows(w.ledger, "payments"), "payments (K03)", ident))
    chk.equal("identity markers in audit / events / payments", leaks, [])
    return chk.result()
